import csv
import io
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from app.core import crypto
from app.core.db import SessionLocal
from app.core.errors import AppError
from app.models import EmailOutbox, LedgerEntry, PayoutRequest, RepBankAccount, RepWallet
from app.services import ledger, payouts
from app.services.paystack import Bank, TransferRefused
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


class PayoutPaystack(FakePaystack):
    """Balance, transfers, recipients and bank lookups, without the network."""

    def __init__(self) -> None:
        super().__init__()
        self.balance = 10_000_000  # ₦100,000
        self.transfers: list[dict] = []
        self.transfer_status = "pending"
        self.refuse_transfer = False
        self.unclear_transfer = False
        self.verified_transfers: dict[str, dict | None] = {}
        self.recipients: list[dict] = []
        self.accounts = {("0987654321", "044"): "ADA NGOZI OBI"}

    async def get_balance(self) -> int:
        return self.balance

    async def initiate_transfer(self, *, amount_kobo, recipient_code, reference, reason):
        if self.refuse_transfer:
            raise TransferRefused("Recipient account is invalid")
        if self.unclear_transfer:
            raise AppError("paystack_unavailable", "timeout", 502)
        self.transfers.append({"amount": amount_kobo, "recipient": recipient_code, "reference": reference})
        return {"status": self.transfer_status, "transfer_code": f"TRF_{len(self.transfers)}", "reference": reference}

    async def verify_transfer(self, reference):
        return self.verified_transfers.get(reference)

    async def list_banks(self):
        return [Bank("044", "Access Bank"), Bank("058", "Guaranty Trust Bank")]

    async def resolve_account(self, account_number, bank_code):
        try:
            return self.accounts[(account_number, bank_code)]
        except KeyError:
            raise AppError("account_not_found", "We couldn't find that account.", 400) from None

    async def create_transfer_recipient(self, *, account_name, account_number, bank_code):
        self.recipients.append({"name": account_name, "number": account_number, "bank": bank_code})
        return f"RCP_new{len(self.recipients)}"


@pytest.fixture
def ps(monkeypatch, fake) -> PayoutPaystack:
    from app.services import paystack
    p = PayoutPaystack()
    monkeypatch.setattr(paystack, "get_client", lambda: p)
    return p


@pytest.fixture
async def funded(setup: dict, ps: PayoutPaystack) -> dict:
    """The rep has ₦5,000 Available: a student paid and the accountant marked it settled."""
    payment = await _pay(setup)
    await send_webhook(setup["student"], {"event": "charge.success", "data": charge_data(payment)})
    resp = await setup["accountant"].post("/api/admin/settlements/mark",
                                          json={"payment_ids": [payment["id"]], "bank_reference": "test stmt"})
    assert resp.json() == {"settled": 1}
    return {**setup, "payment": payment}


async def _wallet() -> RepWallet:
    async with SessionLocal() as db:
        return await db.scalar(select(RepWallet))


async def _balance(code: str) -> int:
    async with SessionLocal() as db:
        return await ledger.balance(db, code)


async def _balanced() -> bool:
    async with SessionLocal() as db:
        return await ledger.unbalanced_transactions(db) == []


async def _request(funded: dict, amount: int = 500_000) -> dict:
    resp = await funded["rep"].post("/api/user/rep/payouts", json={"amount_kobo": amount})
    assert resp.status_code == 201, resp.text
    return resp.json()


def transfer_event(event: str, payout_row: PayoutRequest, **extra) -> dict:
    return {"event": event, "data": {"id": 9000 + len(event), "reference": payout_row.transfer_reference,
                                     "transfer_code": payout_row.paystack_transfer_code, **extra}}


async def _payout_row(payout_id: str) -> PayoutRequest:
    async with SessionLocal() as db:
        return await db.get(PayoutRequest, payout_id)


# --- Fees -------------------------------------------------------------------------------------


async def test_payout_quote_matches_the_design_example(funded: dict) -> None:
    # ₦5,000: 1% platform fee (₦50) + ₦10 Paystack transfer fee -> ₦4,940 sent.
    q = (await funded["rep"].get("/api/user/rep/payouts/quote?amount_kobo=500000")).json()
    assert q == {"amount_kobo": 500_000, "service_charge_kobo": 6_000, "net_kobo": 494_000}
    # ₦20,000 falls in the ₦25 band.
    q = (await funded["rep"].get("/api/user/rep/payouts/quote?amount_kobo=2000000")).json()
    assert q["service_charge_kobo"] == 20_000 + 2_500


# --- The phase 8 "done when" ---------------------------------------------------------------------


async def test_payout_requested_approved_processing_paid(funded: dict, ps: PayoutPaystack) -> None:
    rep_id = await _rep_id()
    clearing_before = await _balance("paystack_clearing")

    # Requested: Available -> hold at once.
    payout = await _request(funded)
    assert payout["status"] == "requested" and payout["net_kobo"] == 494_000
    w = await _wallet()
    assert (w.pending_kobo, w.available_kobo, w.held_kobo) == (0, 0, 500_000)
    assert await _balance(f"payout_hold:{rep_id}") == 500_000
    assert await _balanced()
    wallet = (await funded["rep"].get("/api/user/rep/wallet")).json()
    assert wallet["can_request"] is False and wallet["payouts"][0]["status"] == "requested"

    # The approval screen has what the accountant needs.
    accountant = funded["accountant"]
    item = (await accountant.get("/api/admin/payouts")).json()["items"][0]
    assert item["net_kobo"] == 494_000 and item["rep_held_kobo"] == 500_000
    assert item["bank_label"] == "GTBank · ADA OBI · ••••6789"
    assert (await accountant.get("/api/admin/paystack/balance")).json() == {"balance_kobo": 10_000_000}

    # Approved -> Processing: the transfer is started for the net amount.
    resp = await accountant.post(f"/api/admin/payouts/{payout['id']}/approve")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "processing"
    assert ps.transfers == [{"amount": 494_000, "recipient": "RCP_x",
                             "reference": resp.json()["transfer_reference"]}]
    assert (await _wallet()).held_kobo == 500_000  # still held until Paystack confirms

    # Paid: Paystack's webhook confirms.
    row = await _payout_row(payout["id"])
    assert (await send_webhook(funded["student"], transfer_event("transfer.success", row))).status_code == 200
    row = await _payout_row(payout["id"])
    assert row.status == "paid" and row.paid_at is not None
    w = await _wallet()
    assert (w.available_kobo, w.held_kobo) == (0, 0)
    assert await _balance(f"payout_hold:{rep_id}") == 0
    assert await _balance("paystack_clearing") == clearing_before - (494_000 + 1_000)
    assert await _balance("payout_fee_revenue") == 5_000
    assert await _balanced()

    # A repeated webhook changes nothing.
    await send_webhook(funded["student"], transfer_event("transfer.success", row))
    async with SessionLocal() as db:
        paid_txns = await db.scalar(select(func.count(func.distinct(LedgerEntry.txn_id))).where(LedgerEntry.ref_type == "payout_paid"))
        kinds = [e.payload["kind"] for e in (await db.scalars(
            select(EmailOutbox).where(EmailOutbox.template == "payout_update").order_by(EmailOutbox.created_at)
        )).all()]
    assert paid_txns == 1 and kinds == ["approved", "paid"]
    assert (await funded["rep"].get("/api/user/rep/wallet")).json()["payouts"][0]["status"] == "paid"


async def test_rejection_releases_the_hold(funded: dict) -> None:
    payout = await _request(funded)
    resp = await funded["accountant"].post(f"/api/admin/payouts/{payout['id']}/reject",
                                           json={"reason": "Please confirm the account with support first."})
    assert resp.json()["status"] == "rejected"
    w = await _wallet()
    assert (w.available_kobo, w.held_kobo) == (500_000, 0)
    assert await _balanced()
    # The rep can request again.
    assert (await _request(funded))["status"] == "requested"


@pytest.mark.parametrize("event", ["transfer.failed", "transfer.reversed"])
async def test_failed_or_reversed_transfer_releases_the_hold(funded: dict, ps: PayoutPaystack, event: str) -> None:
    payout = await _request(funded)
    await funded["accountant"].post(f"/api/admin/payouts/{payout['id']}/approve")
    row = await _payout_row(payout["id"])
    await send_webhook(funded["student"], transfer_event(event, row, reason="Account closed"))
    row = await _payout_row(payout["id"])
    assert row.status == ("reversed" if event == "transfer.reversed" else "failed")
    w = await _wallet()
    assert (w.available_kobo, w.held_kobo) == (500_000, 0)
    assert await _balanced()


async def test_paystack_refusing_the_transfer_releases_the_hold(funded: dict, ps: PayoutPaystack) -> None:
    ps.refuse_transfer = True
    payout = await _request(funded)
    resp = await funded["accountant"].post(f"/api/admin/payouts/{payout['id']}/approve")
    assert resp.json()["status"] == "failed"
    assert "Recipient account is invalid" in resp.json()["reason"]
    assert (await _wallet()).available_kobo == 500_000


async def test_unclear_answer_is_resolved_by_reference_never_by_resending(funded: dict, ps: PayoutPaystack) -> None:
    ps.unclear_transfer = True
    payout = await _request(funded)
    resp = await funded["accountant"].post(f"/api/admin/payouts/{payout['id']}/approve")
    assert resp.json()["status"] == "processing"

    row = await _payout_row(payout["id"])
    async with SessionLocal() as db:
        await db.execute(update(PayoutRequest).values(reviewed_at=row.reviewed_at - timedelta(minutes=15)))
        await db.commit()

    # Paystack did get it after all: the check finds it paid.
    ps.verified_transfers[row.transfer_reference] = {"status": "success", "reference": row.transfer_reference,
                                                     "transfer_code": "TRF_late"}
    counts = await payouts.check_processing(ps)
    assert counts == {"checked": 1, "settled": 1}
    assert (await _payout_row(payout["id"])).status == "paid"
    assert ps.transfers == []  # never sent a second time


async def test_transfer_paystack_never_received_is_failed_and_released(funded: dict, ps: PayoutPaystack) -> None:
    ps.unclear_transfer = True
    payout = await _request(funded)
    await funded["accountant"].post(f"/api/admin/payouts/{payout['id']}/approve")
    row = await _payout_row(payout["id"])
    async with SessionLocal() as db:
        await db.execute(update(PayoutRequest).values(reviewed_at=row.reviewed_at - timedelta(minutes=15)))
        await db.commit()
    await payouts.check_processing(ps)  # verify returns None: Paystack has no such transfer
    assert (await _payout_row(payout["id"])).status == "failed"
    assert (await _wallet()).available_kobo == 500_000


async def test_actual_transfer_fee_different_from_the_estimate(funded: dict, ps: PayoutPaystack) -> None:
    payout = await _request(funded)
    await funded["accountant"].post(f"/api/admin/payouts/{payout['id']}/approve")
    row = await _payout_row(payout["id"])
    await send_webhook(funded["student"], transfer_event("transfer.success", row, fee_charged=2_500))
    assert await _balanced()
    assert await _balance("payout_fee_revenue") == 5_000 - 1_500  # Paystack charged ₦15 more


# --- Rules -----------------------------------------------------------------------------------


async def test_balance_too_low_blocks_approval(funded: dict, ps: PayoutPaystack) -> None:
    ps.balance = 100_000
    payout = await _request(funded)
    resp = await funded["accountant"].post(f"/api/admin/payouts/{payout['id']}/approve")
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "insufficient_paystack_balance"
    assert (await _payout_row(payout["id"])).status == "requested" and ps.transfers == []


async def test_cannot_approve_twice(funded: dict, ps: PayoutPaystack) -> None:
    payout = await _request(funded)
    await funded["accountant"].post(f"/api/admin/payouts/{payout['id']}/approve")
    again = await funded["accountant"].post(f"/api/admin/payouts/{payout['id']}/approve")
    assert again.json()["error"]["code"] == "already_decided" and len(ps.transfers) == 1


async def test_request_limits(funded: dict) -> None:
    resp = await funded["rep"].post("/api/user/rep/payouts", json={"amount_kobo": 600_000})
    assert resp.json()["error"]["code"] == "insufficient_available"
    resp = await funded["rep"].post("/api/user/rep/payouts", json={"amount_kobo": 500})
    assert resp.json()["error"]["code"] == "payout_too_small"
    await _request(funded, 200_000)
    resp = await funded["rep"].post("/api/user/rep/payouts", json={"amount_kobo": 100_000})
    assert resp.json()["error"]["code"] == "payout_blocked"  # one open request at a time


async def test_support_and_students_cannot_touch_payouts(funded: dict, clock) -> None:
    from tests.people import staff_client
    payout = await _request(funded)
    support = await staff_client(clock, "support")
    try:
        assert (await support.get("/api/admin/payouts")).status_code == 403
        assert (await support.post(f"/api/admin/payouts/{payout['id']}/approve")).status_code == 403
    finally:
        await support.__aexit__(None, None, None)
    assert (await funded["student"].post("/api/user/rep/payouts", json={"amount_kobo": 100})).status_code == 403


# --- Changing the payout bank account ---------------------------------------------------------------


async def test_bank_change_pauses_payouts_until_approved(funded: dict, ps: PayoutPaystack) -> None:
    rep = funded["rep"]
    resp = await rep.post("/api/user/rep/bank-account/change", json={"bank_code": "044", "account_number": "0987654321"})
    assert resp.status_code == 201
    assert resp.json()["status"] == "pending" and resp.json()["account_name"] == "ADA NGOZI OBI"

    wallet = (await rep.get("/api/user/rep/wallet")).json()
    assert wallet["can_request"] is False and "paused" in wallet["blocked_reason"]
    assert (await rep.post("/api/user/rep/payouts", json={"amount_kobo": 100_000})).json()["error"]["code"] == "payout_blocked"

    accountant = funded["accountant"]
    change = (await accountant.get("/api/admin/bank-changes")).json()[0]
    assert change["current_account"] == "GTBank · ADA OBI · ••••6789"
    approved = await accountant.post(f"/api/admin/bank-changes/{change['id']}/approve")
    assert approved.json()["status"] == "approved"
    assert ps.recipients == [{"name": "ADA NGOZI OBI", "number": "0987654321", "bank": "044"}]

    async with SessionLocal() as db:
        account = await db.scalar(select(RepBankAccount))
    assert crypto.decrypt(account.account_number) == "0987654321"
    assert account.paystack_recipient_code == "RCP_new1" and account.bank_name == "Access Bank"

    payout = await _request(funded, 100_000)
    assert payout["bank_label"] == "Access Bank · ADA NGOZI OBI · ••••4321"


async def test_bank_change_rules(funded: dict, ps: PayoutPaystack) -> None:
    rep = funded["rep"]
    same = await rep.post("/api/user/rep/bank-account/change", json={"bank_code": "058", "account_number": "0123456789"})
    assert same.json()["error"]["code"] == "same_account"

    await _request(funded)
    blocked = await rep.post("/api/user/rep/bank-account/change", json={"bank_code": "044", "account_number": "0987654321"})
    assert blocked.json()["error"]["code"] == "payout_open"


async def test_rejected_bank_change_keeps_the_old_account(funded: dict, ps: PayoutPaystack) -> None:
    await funded["rep"].post("/api/user/rep/bank-account/change", json={"bank_code": "044", "account_number": "0987654321"})
    change = (await funded["accountant"].get("/api/admin/bank-changes")).json()[0]
    resp = await funded["accountant"].post(f"/api/admin/bank-changes/{change['id']}/reject",
                                           json={"reason": "The name doesn't match your ID."})
    assert resp.json()["status"] == "rejected"
    async with SessionLocal() as db:
        account = await db.scalar(select(RepBankAccount))
    assert crypto.decrypt(account.account_number) == "0123456789"
    assert (await funded["rep"].get("/api/user/rep/wallet")).json()["can_request"] is True


# --- The rep's view of each due ----------------------------------------------------------------------


async def test_rep_sees_who_paid_with_downloads(funded: dict) -> None:
    rep = funded["rep"]
    due_id = funded["due_id"]
    due = next(d for d in (await rep.get("/api/user/rep/dues")).json() if d["id"] == due_id)
    assert (due["students"], due["paid_count"], due["collected_kobo"], due["outstanding_kobo"]) == (1, 1, 500_000, 0)

    payments = (await rep.get(f"/api/user/rep/dues/{due_id}/payments")).json()
    assert payments["paid"][0]["name"] == "Ada Obi" and payments["unpaid"] == []

    csv_resp = await rep.get(f"/api/user/rep/dues/{due_id}/paid-list.csv")
    assert csv_resp.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(csv_resp.text.lstrip("﻿"))))
    assert rows[1][:2] == ["No.", "Name"] and rows[2][1] == "Ada Obi" and rows[2][2] == "5,000.00"

    pdf = await rep.get(f"/api/user/rep/dues/{due_id}/paid-list.pdf")
    assert pdf.content.startswith(b"%PDF") and "paid-list-handout-2026.pdf" in pdf.headers["content-disposition"]


async def test_another_rep_cannot_see_the_paid_list(funded: dict) -> None:
    from tests.people import rep_client
    other = await rep_client("other@example.com", "MTH 300 Level")
    try:
        for path in ("payments", "paid-list.csv", "paid-list.pdf"):
            assert (await other.get(f"/api/user/rep/dues/{funded['due_id']}/{path}")).status_code == 404
    finally:
        await other.__aexit__(None, None, None)
