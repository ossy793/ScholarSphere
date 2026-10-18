import os
import subprocess
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select, update

from app.core.clock import utcnow
from app.core.db import SessionLocal
from app.models import (
    AuditLog,
    EmailOutbox,
    LedgerEntry,
    Payment,
    ReconciliationRun,
    RepWallet,
    WebhookEvent,
    WorkerJob,
)
from app.services import ledger, payments, reconciliation, settlements
from app.worker.jobs import JOBS_BY_NAME, Job
from app.worker.runner import is_due, run_job
from tests.test_payments import (  # noqa: F401  (fixtures)
    FakePaystack,
    _pay,
    _rep_id,
    charge_data,
    clock,
    fake,
    send_webhook,
    setup,
)

BACKEND = Path(__file__).resolve().parents[1]
LAGOS = ZoneInfo("Africa/Lagos")


class SettlingPaystack(FakePaystack):
    """Adds Paystack's settlement and transaction lists."""

    def __init__(self) -> None:
        super().__init__()
        self.settlements: list[dict] = []
        self.settlement_txs: dict[int, list[dict]] = {}
        self.day_transactions: list[dict] = []

    async def list_settlements(self, since):
        return self.settlements

    async def settlement_transactions(self, settlement_id):
        return self.settlement_txs.get(settlement_id, [])

    async def list_transactions(self, start, end):
        return self.day_transactions


@pytest.fixture
def ps(monkeypatch, fake) -> SettlingPaystack:
    from app.services import paystack
    s = SettlingPaystack()
    monkeypatch.setattr(paystack, "get_client", lambda: s)
    return s


async def _paid(setup: dict) -> dict:
    payment = await _pay(setup)
    await send_webhook(setup["student"], {"event": "charge.success", "data": charge_data(payment)})
    return payment


async def _wallet() -> RepWallet:
    async with SessionLocal() as db:
        return await db.scalar(select(RepWallet))


# --- The timetable --------------------------------------------------------------------------


def _state(**kw) -> WorkerJob:
    return WorkerJob(name="x", **kw)


def test_interval_jobs_run_when_their_interval_has_passed() -> None:
    job = Job("x", lambda: None, every=timedelta(minutes=5))
    now = utcnow()
    assert is_due(job, None, now)
    assert not is_due(job, _state(last_started_at=now - timedelta(minutes=4)), now)
    assert is_due(job, _state(last_started_at=now - timedelta(minutes=5)), now)


def test_daily_job_runs_once_after_its_lagos_time_and_catches_up_if_missed() -> None:
    job = Job("x", lambda: None, daily_at=time(2, 0))
    at = lambda h, m, day=17: datetime(2026, 10, day, h, m, tzinfo=LAGOS)  # noqa: E731
    # 02:30 Lagos, last success yesterday 02:00 -> due
    assert is_due(job, _state(last_success_at=at(2, 0, 16)), at(2, 30))
    # 02:30 Lagos, already ran at 02:00 today -> not due
    assert not is_due(job, _state(last_success_at=at(2, 0)), at(2, 30))
    # 01:00 Lagos, ran yesterday 02:05 -> not due yet (today's slot hasn't come)
    assert not is_due(job, _state(last_success_at=at(2, 5, 16)), at(1, 0))
    # Worker was off for two days: catches up straight away
    assert is_due(job, _state(last_success_at=at(2, 0, 14)), at(1, 0))


# --- The phase 7 "done when" ------------------------------------------------------------------


def test_worker_runs_as_a_separate_process() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "app.worker", "--once", "--job", "email_outbox", "--job", "webhook_retry"],
        cwd=BACKEND, env={**os.environ, "PYTHONPATH": str(BACKEND)},
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "Job email_outbox" in result.stderr


async def test_worker_records_its_runs() -> None:
    await run_job(JOBS_BY_NAME["email_outbox"])
    async with SessionLocal() as db:
        state = await db.scalar(select(WorkerJob).where(WorkerJob.name == "email_outbox"))
    assert state.last_status == "ok" and state.runs == 1 and state.last_success_at is not None


async def test_paystack_settlement_moves_money_to_available(setup: dict, ps: SettlingPaystack) -> None:
    payment = await _paid(setup)
    assert (await _wallet()).pending_kobo == 500_000
    body = (await setup["rep"].get("/api/user/rep/wallet")).json()
    assert {k: body[k] for k in ("pending_kobo", "available_kobo", "held_kobo")} == {
        "pending_kobo": 500_000, "available_kobo": 0, "held_kobo": 0}
    assert body["can_request"] is False

    ps.settlements = [{"id": 77, "status": "success", "settlement_date": utcnow().isoformat()},
                      {"id": 78, "status": "processing"}]
    ps.settlement_txs = {77: [{"reference": payment["reference"]}, {"reference": "OTHER-PRODUCT-1"}]}
    assert await settlements.check_paystack_settlements(ps) == 1

    wallet = await _wallet()
    assert (wallet.pending_kobo, wallet.available_kobo) == (0, 500_000)
    assert (await setup["rep"].get("/api/user/rep/wallet")).json()["available_kobo"] == 500_000
    rep_id = await _rep_id()
    async with SessionLocal() as db:
        assert await ledger.unbalanced_transactions(db) == []
        assert await ledger.balance(db, f"rep_pending:{rep_id}") == 0
        assert await ledger.balance(db, f"rep_available:{rep_id}") == 500_000
        stored = await db.get(Payment, payment["id"])
    assert stored.settled_at is not None and stored.settlement_reference == "paystack:77"

    # Running again changes nothing.
    assert await settlements.check_paystack_settlements(ps) == 0
    async with SessionLocal() as db:
        txns = await db.scalar(select(func.count(func.distinct(LedgerEntry.txn_id))).where(LedgerEntry.ref_type == "settlement"))
    assert txns == 1


async def test_nightly_check_is_clean_on_good_data(setup: dict, ps: SettlingPaystack) -> None:
    payment = await _paid(setup)
    start, end = reconciliation.previous_lagos_day()
    async with SessionLocal() as db:
        await db.execute(update(Payment).values(paid_at=start + timedelta(hours=10)))
        await db.commit()
    ps.day_transactions = [{"reference": payment["reference"], "amount": payment["total_kobo"]},
                           {"reference": "SOMEONE-ELSES-PAYMENT", "amount": 1}]

    run = await reconciliation.run(ps)
    assert run.status == "clean", run.items
    assert run.summary["unbalanced_transactions"] == 0
    assert run.summary["paystack_transactions"] == 1 and run.summary["our_payments"] == 1
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(EmailOutbox).where(
            EmailOutbox.template == "reconciliation_alert")) == 0


# --- Mismatches ------------------------------------------------------------------------------


async def test_nightly_check_fixes_a_wrong_wallet_and_reports_problems(setup: dict, ps: SettlingPaystack) -> None:
    await _paid(setup)
    async with SessionLocal() as db:
        await db.execute(update(RepWallet).values(pending_kobo=123))  # cache drifted
        await db.commit()
    ps.day_transactions = [{"reference": "DPP-NOT-OURS-AT-ALL", "amount": 5000}]

    run = await reconciliation.run(ps)
    kinds = {i["kind"] for i in run.items}
    assert run.status == "mismatch"
    assert {"wallet_mismatch", "paid_at_paystack_not_recorded"} <= kinds
    assert (await _wallet()).pending_kobo == 500_000  # corrected from the ledger
    async with SessionLocal() as db:
        alert = await db.scalar(select(EmailOutbox).where(EmailOutbox.template == "reconciliation_alert"))
    assert alert.to_email == "accountant@pritis.name.ng"


async def test_paystack_down_during_the_check_is_reported(setup: dict, ps: SettlingPaystack, monkeypatch) -> None:
    from app.core.errors import AppError

    async def down(start, end):
        raise AppError("paystack_unavailable", "Couldn't read /transaction from Paystack.", 502)

    monkeypatch.setattr(ps, "list_transactions", down)
    run = await reconciliation.run(ps)
    assert run.status == "error"
    assert run.items[0]["kind"] == "paystack_unreachable"


# --- Manual settlement by the accountant -------------------------------------------------------


async def test_accountant_marks_payments_settled(setup: dict, ps: SettlingPaystack) -> None:
    payment = await _paid(setup)
    accountant = setup["accountant"]
    page = (await accountant.get("/api/admin/settlements/unsettled")).json()
    assert page["count"] == 1 and page["total_due_kobo"] == 500_000
    assert page["items"][0]["reference"] == payment["reference"]

    resp = await accountant.post("/api/admin/settlements/mark",
                                 json={"payment_ids": [payment["id"]], "bank_reference": "GTB stmt 17 Oct"})
    assert resp.json() == {"settled": 1}
    assert (await _wallet()).available_kobo == 500_000
    assert (await accountant.get("/api/admin/settlements/unsettled")).json()["count"] == 0

    # Marking again does nothing.
    again = await accountant.post("/api/admin/settlements/mark",
                                  json={"payment_ids": [payment["id"]], "bank_reference": "GTB stmt 17 Oct"})
    assert again.json() == {"settled": 0}
    async with SessionLocal() as db:
        log = await db.scalar(select(AuditLog).where(AuditLog.action == "settlement.mark"))
        stored = await db.get(Payment, payment["id"])
    assert log.after["bank_reference"] == "GTB stmt 17 Oct"
    assert stored.settlement_reference == "manual:GTB stmt 17 Oct"


async def test_support_cannot_settle(setup: dict, ps: SettlingPaystack, clock) -> None:
    from tests.people import staff_client
    support = await staff_client(clock, "support")
    try:
        assert (await support.get("/api/admin/settlements/unsettled")).status_code == 403
        assert (await support.get("/api/admin/settlements/status")).status_code == 403
    finally:
        await support.__aexit__(None, None, None)


async def test_status_shows_jobs_and_latest_check(setup: dict, ps: SettlingPaystack) -> None:
    await run_job(JOBS_BY_NAME["email_outbox"])
    await reconciliation.run(ps)
    status = (await setup["accountant"].get("/api/admin/settlements/status")).json()
    jobs = {j["name"]: j for j in status["jobs"]}
    assert jobs["email_outbox"]["last_status"] == "ok"
    assert jobs["reconciliation"]["schedule"] == "daily 02:00 Lagos"
    assert status["latest_reconciliation"]["status"] == "clean"


# --- Payments the student never came back from -----------------------------------------------------


async def test_stale_pending_payments_are_checked_then_abandoned(setup: dict, ps: SettlingPaystack) -> None:
    paid_later = await _pay(setup)
    async with SessionLocal() as db:
        await db.execute(update(Payment).values(created_at=utcnow() - timedelta(minutes=15)))
        await db.commit()
    ps.transactions[paid_later["reference"]] = charge_data(paid_later)
    counts = await payments.check_stale_pending(ps)
    assert counts["paid"] == 1
    assert (await _wallet()).pending_kobo == 500_000

    # A different attempt that was never completed, over a day old.
    async with SessionLocal() as db:
        old = await db.get(Payment, paid_later["id"])
        db.add(Payment(
            reference="DPP-OLDABANDONED00000001", user_id=old.user_id, due_id=old.due_id,
            revision_id=old.revision_id, base_kobo=old.base_kobo, service_charge_kobo=old.service_charge_kobo,
            paystack_part_kobo=old.paystack_part_kobo, platform_part_kobo=old.platform_part_kobo,
            total_kobo=old.total_kobo, status="pending", authorization_url="https://x",
            created_at=utcnow() - timedelta(hours=25),
        ))
        await db.commit()
    counts = await payments.check_stale_pending(ps)
    assert counts["abandoned"] == 1
    async with SessionLocal() as db:
        abandoned = await db.scalar(select(Payment).where(Payment.reference == "DPP-OLDABANDONED00000001"))
    assert abandoned.status == "abandoned"


async def test_unprocessed_webhooks_are_retried(setup: dict, ps: SettlingPaystack) -> None:
    payment = await _pay(setup)
    async with SessionLocal() as db:
        db.add(WebhookEvent(
            event_type="charge.success", paystack_event_key="charge.success:retry-me",
            payload={"event": "charge.success", "data": charge_data(payment)},
            signature_ok=True, status="received", created_at=utcnow() - timedelta(minutes=2),
        ))
        await db.commit()
    assert await payments.retry_webhooks() == 1
    async with SessionLocal() as db:
        event = await db.scalar(select(WebhookEvent))
        stored = await db.get(Payment, payment["id"])
    assert event.status == "processed" and stored.status == "success"
