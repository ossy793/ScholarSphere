from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select, update

from app.core.db import SessionLocal
from app.core.errors import AppError
from app.models import ClassDue, EmailOutbox, LedgerEntry, Payment, PaymentReminder, Refund
from app.services import refunds, reminders
from app.services.paystack import RefundRefused
from tests.people import staff_client
from tests.test_payments import FakePaystack, _pay, _rep_id, charge_data, clock, fake, send_webhook, setup  # noqa: F401
from tests.test_payouts import PayoutPaystack, _balance, _balanced, _request, _wallet, funded  # noqa: F401

TOTAL, DUE, CHARGE, FEE = 522_500, 500_000, 22_500, 17_838  # the ₦5,000 due paid in the fixtures


class RefundPaystack(PayoutPaystack):
    def __init__(self) -> None:
        super().__init__()
        self.refunds: list[dict] = []
        self.refund_status = "pending"
        self.refuse_refund = False
        self.unclear_refund = False
        self.fetched: dict[str, dict | None] = {}
        self.listed: list[dict] = []

    async def create_refund(self, *, transaction_reference, amount_kobo, merchant_note):
        if self.refuse_refund:
            raise RefundRefused("Transaction has been fully reversed")
        if self.unclear_refund:
            raise AppError("paystack_unavailable", "timeout", 502)
        self.refunds.append({"transaction": transaction_reference, "amount": amount_kobo, "note": merchant_note})
        return {"id": 700 + len(self.refunds), "status": self.refund_status, "amount": amount_kobo}

    async def fetch_refund(self, refund_id):
        return self.fetched.get(refund_id)

    async def list_refunds(self, transaction_id):
        return self.listed


@pytest.fixture
def ps(monkeypatch, fake) -> RefundPaystack:  # noqa: F811  (replaces the payouts fixture)
    from app.services import paystack
    p = RefundPaystack()
    monkeypatch.setattr(paystack, "get_client", lambda: p)
    return p


@pytest.fixture
async def paid(setup: dict, ps: RefundPaystack, clock) -> dict:
    """A paid, not yet settled ₦5,000 due, and a support agent."""
    payment = await _pay(setup)
    await send_webhook(setup["student"], {"event": "charge.success", "data": charge_data(payment)})
    support = await staff_client(clock, "support")
    try:
        yield {**setup, "payment": payment, "support": support}
    finally:
        await support.__aexit__(None, None, None)


async def _ask(ctx: dict, who: str = "support") -> dict:
    resp = await ctx[who].post("/api/admin/refunds", json={"payment_id": ctx["payment"]["id"],
                                                           "reason": "Student paid for the wrong class"})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _approve(ctx: dict, refund_id: str, mistake_by: str = "rep"):
    return await ctx["accountant"].post(f"/api/admin/refunds/{refund_id}/approve", json={"mistake_by": mistake_by})


def refund_event(event: str, payment: dict, **extra) -> dict:
    return {"event": event, "data": {"status": event.removeprefix("refund."), "transaction_reference": payment["reference"],
                                     "refund_reference": f"RR{len(event)}", "amount": TOTAL, "currency": "NGN", **extra}}


async def _payment(payment_id: str) -> Payment:
    async with SessionLocal() as db:
        return await db.get(Payment, payment_id)


async def _settle(ctx: dict, payment_id: str) -> dict:
    return (await ctx["accountant"].post("/api/admin/settlements/mark",
                                         json={"payment_ids": [payment_id], "bank_reference": "stmt"})).json()


# --- The phase 10 "done when": a refund end to end ---------------------------------------------


async def test_refund_requested_approved_processed(paid: dict, ps: RefundPaystack) -> None:
    rep_id = await _rep_id()
    clearing_before = await _balance("paystack_clearing")
    assert await _balance("platform_revenue") == CHARGE - FEE

    # Support starts it; nothing moves yet.
    refund = await _ask(paid)
    assert refund["status"] == "requested"
    assert (refund["full_refund_kobo"], refund["student_mistake_refund_kobo"]) == (TOTAL, TOTAL - FEE)
    assert (await _wallet()).pending_kobo == DUE

    # The accountant approves: the rep's share is reserved (from Pending: not settled yet),
    # and Paystack is asked for the whole payment.
    resp = await _approve(paid, refund["id"], "rep")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "processing" and body["amount_kobo"] == TOTAL and body["fee_borne_kobo"] == FEE
    assert ps.refunds == [{"transaction": paid["payment"]["reference"], "amount": TOTAL, "note": ps.refunds[0]["note"]}]
    w = await _wallet()
    assert (w.pending_kobo, w.refund_held_kobo) == (0, DUE)
    assert await _balance(f"refund_hold:{rep_id}") == DUE
    assert (await paid["rep"].get("/api/user/rep/wallet")).json()["refund_held_kobo"] == DUE

    # It can't be settled while the refund is on its way.
    assert await _settle(paid, paid["payment"]["id"]) == {"settled": 0}

    # Paystack confirms.
    assert (await send_webhook(paid["student"], refund_event("refund.processed", paid["payment"]))).status_code == 200
    p = await _payment(paid["payment"]["id"])
    assert p.status == "refunded" and p.refunded_at is not None
    w = await _wallet()
    assert (w.pending_kobo, w.available_kobo, w.refund_held_kobo) == (0, 0, 0)
    assert await _balance("paystack_clearing") == clearing_before - TOTAL
    assert await _balance("platform_revenue") == 0
    assert await _balance("refund_fee_expense") == FEE  # Paystack keeps its fee; Pritis bears it
    assert await _balanced()

    # A repeated webhook changes nothing.
    await send_webhook(paid["student"], refund_event("refund.processed", paid["payment"]))
    async with SessionLocal() as db:
        n = await db.scalar(select(func.count(func.distinct(LedgerEntry.txn_id)))
                            .where(LedgerEntry.ref_type == "refund_processed"))
        kinds = [(e.to_email, e.payload["kind"]) for e in (await db.scalars(
            select(EmailOutbox).where(EmailOutbox.template == "refund_update").order_by(EmailOutbox.created_at)
        )).all()]
    assert n == 1
    assert [k for to, k in kinds if to == "student@example.com"] == ["requested", "approved", "processed"]
    assert [k for to, k in kinds if to == "rep@example.com"] == ["requested", "approved", "processed"]

    # The student sees it as refunded, the rep's list drops it, and the student can pay again.
    history = (await paid["student"].get("/api/user/payments")).json()
    assert history[0]["status"] == "refunded"
    dues = (await paid["rep"].get("/api/user/rep/dues")).json()
    assert dues[0]["paid_count"] == 0
    assert (await paid["student"].post("/api/user/payments", json={"due_id": paid["due_id"]})).status_code == 201


async def test_student_mistake_refund_keeps_paystacks_fee(paid: dict, ps: RefundPaystack) -> None:
    refund = await _ask(paid)
    body = (await _approve(paid, refund["id"], "student")).json()
    assert body["amount_kobo"] == TOTAL - FEE and body["fee_borne_kobo"] == 0
    assert ps.refunds[0]["amount"] == TOTAL - FEE
    await send_webhook(paid["student"], refund_event("refund.processed", paid["payment"], amount=TOTAL - FEE))
    assert await _balance("refund_fee_expense") == 0
    assert await _balance("platform_revenue") == 0
    assert await _balanced()


async def test_refund_after_payout_takes_the_rep_negative(funded: dict, ps: RefundPaystack, clock) -> None:
    # Settled and paid out, then refunded: the rep's Available balance goes below zero.
    payout = await _request(funded)
    await funded["accountant"].post(f"/api/admin/payouts/{payout['id']}/approve")
    from tests.test_payouts import _payout_row, transfer_event
    await send_webhook(funded["student"], transfer_event("transfer.success", await _payout_row(payout["id"])))
    refund = (await funded["accountant"].post("/api/admin/refunds", json={
        "payment_id": funded["payment"]["id"], "reason": "Duplicate class registration"})).json()
    await _approve(funded, refund["id"], "pritis")
    assert (await _wallet()).available_kobo == -DUE
    await send_webhook(funded["student"], refund_event("refund.processed", funded["payment"]))
    wallet = (await funded["rep"].get("/api/user/rep/wallet")).json()
    assert wallet["available_kobo"] == -DUE and wallet["can_request"] is False
    assert "below zero" in wallet["blocked_reason"]
    assert await _balanced()


async def test_paystack_refusing_releases_the_reservation(paid: dict, ps: RefundPaystack) -> None:
    ps.refuse_refund = True
    refund = await _ask(paid)
    body = (await _approve(paid, refund["id"])).json()
    assert body["status"] == "failed" and "fully reversed" in body["problem"]
    w = await _wallet()
    assert (w.pending_kobo, w.refund_held_kobo) == (DUE, 0)
    assert (await _payment(paid["payment"]["id"])).status == "success"
    assert await _balanced()
    # Released from Pending, so the payment settles normally again.
    assert await _settle(paid, paid["payment"]["id"]) == {"settled": 1}


async def test_failed_webhook_releases_and_tells_the_approver(paid: dict, ps: RefundPaystack) -> None:
    refund = await _ask(paid)
    await _approve(paid, refund["id"])
    await send_webhook(paid["student"], refund_event("refund.failed", paid["payment"]))
    async with SessionLocal() as db:
        row = await db.get(Refund, refund["id"])
        staff_mail = (await db.scalars(select(EmailOutbox).where(EmailOutbox.template == "refund_staff"))).all()
    assert row.status == "failed"
    assert (await _wallet()).pending_kobo == DUE
    assert [m.payload["kind"] for m in staff_mail if m.to_email == "accountant@pritis.name.ng"] == ["requested", "failed"]


async def test_unclear_answer_is_resolved_by_the_status_check(paid: dict, ps: RefundPaystack) -> None:
    ps.unclear_refund = True
    refund = await _ask(paid)
    assert (await _approve(paid, refund["id"])).json()["status"] == "processing"
    async with SessionLocal() as db:
        await db.execute(update(Refund).values(reviewed_at=Refund.reviewed_at - timedelta(minutes=15)))
        await db.commit()
    # Paystack did get it: the check finds it among the transaction's refunds.
    ps.listed = [{"id": 991, "amount": TOTAL, "status": "processed"}]
    assert await refunds.check_processing(ps) == {"checked": 1, "finished": 1}
    assert (await _payment(paid["payment"]["id"])).status == "refunded"
    assert ps.refunds == []  # never asked twice


async def test_refund_paystack_never_received_is_released_after_an_hour(paid: dict, ps: RefundPaystack) -> None:
    ps.unclear_refund = True
    refund = await _ask(paid)
    await _approve(paid, refund["id"])
    async with SessionLocal() as db:
        await db.execute(update(Refund).values(reviewed_at=Refund.reviewed_at - timedelta(minutes=15)))
        await db.commit()
    assert await refunds.check_processing(ps) == {"checked": 1, "finished": 0}  # too soon to give up
    async with SessionLocal() as db:
        await db.execute(update(Refund).values(reviewed_at=Refund.reviewed_at - timedelta(hours=1)))
        await db.commit()
    assert await refunds.check_processing(ps) == {"checked": 1, "finished": 1}
    assert (await _wallet()).pending_kobo == DUE


async def test_who_may_do_what(paid: dict, ps: RefundPaystack) -> None:
    refund = await _ask(paid)
    # Support starts refunds but can't approve them.
    assert (await paid["support"].post(f"/api/admin/refunds/{refund['id']}/approve",
                                       json={"mistake_by": "rep"})).status_code == 403
    # Students and reps can't reach the admin API at all.
    for c in (paid["student"], paid["rep"]):
        assert (await c.get("/api/admin/refunds")).status_code == 401
    # One open refund per payment.
    again = await paid["support"].post("/api/admin/refunds", json={"payment_id": paid["payment"]["id"],
                                                                   "reason": "Asking a second time"})
    assert again.status_code == 409


async def test_rejection_and_balance_check(paid: dict, ps: RefundPaystack) -> None:
    refund = await _ask(paid)
    ps.balance = 100_000  # ₦1,000: not enough for ₦5,225
    resp = await _approve(paid, refund["id"])
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "insufficient_paystack_balance"
    assert (await _wallet()).refund_held_kobo == 0  # nothing reserved

    resp = await paid["accountant"].post(f"/api/admin/refunds/{refund['id']}/reject",
                                         json={"reason": "The student did pay for this class"})
    assert resp.json()["status"] == "rejected"
    assert (await _approve(paid, refund["id"])).status_code == 409  # already decided
    listed = (await paid["support"].get("/api/admin/refunds?status=rejected")).json()
    assert listed["total"] == 1 and listed["items"][0]["problem"] == "The student did pay for this class"


async def test_only_successful_payments_can_be_refunded(setup: dict, ps: RefundPaystack) -> None:
    payment = await _pay(setup)  # still pending
    resp = await setup["accountant"].post("/api/admin/refunds", json={"payment_id": payment["id"],
                                                                      "reason": "Never went through"})
    assert resp.status_code == 409


# --- Reminders -------------------------------------------------------------------------------------


async def test_reminders_three_days_before_and_on_the_day(setup: dict, fake) -> None:
    lagos = ZoneInfo("Africa/Lagos")
    async with SessionLocal() as db:
        due = await db.get(ClassDue, setup["due_id"])
        deadline_day = due.deadline.astimezone(lagos).date()
    deadline_day_8am = datetime.combine(deadline_day, time(8, 0), tzinfo=lagos)
    if deadline_day_8am >= due.deadline:
        pytest.skip("fixture deadline falls before 08:00 Lagos")

    # A day with no reminder.
    assert await reminders.queue_reminders(deadline_day_8am - timedelta(days=2)) == 0
    # 3 days before: the unpaid student gets one; running again sends nothing new.
    assert await reminders.queue_reminders(deadline_day_8am - timedelta(days=3)) == 1
    assert await reminders.queue_reminders(deadline_day_8am - timedelta(days=3)) == 0
    # Once paid, no deadline-day reminder.
    payment = await _pay(setup)
    await send_webhook(setup["student"], {"event": "charge.success", "data": charge_data(payment)})
    assert await reminders.queue_reminders(deadline_day_8am) == 0
    async with SessionLocal() as db:
        kinds = (await db.scalars(select(PaymentReminder.kind))).all()
        mail = (await db.scalars(select(EmailOutbox).where(EmailOutbox.template == "payment_reminder"))).all()
    assert kinds == ["three_days"]
    assert mail[0].to_email == "student@example.com" and mail[0].payload["total"] == "₦5,225"


def test_reminder_days_count_in_lagos_time() -> None:
    lagos = ZoneInfo("Africa/Lagos")
    deadline = datetime(2026, 11, 20, 23, 0, tzinfo=lagos)
    assert reminders.reminder_kind(deadline, datetime(2026, 11, 17, 8, 0, tzinfo=lagos)) == "three_days"
    assert reminders.reminder_kind(deadline, datetime(2026, 11, 20, 8, 0, tzinfo=lagos)) == "deadline_day"
    assert reminders.reminder_kind(deadline, datetime(2026, 11, 18, 8, 0, tzinfo=lagos)) is None
    assert reminders.reminder_kind(deadline, datetime(2026, 11, 21, 8, 0, tzinfo=lagos)) is None
