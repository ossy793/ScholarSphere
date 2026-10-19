from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.core.db import SessionLocal
from app.models import EmailOutbox, Payment, RevenueWithdrawal
from app.services import settlements
from tests.people import staff_client
from tests.test_payments import _pay, charge_data, clock, fake, send_webhook, setup  # noqa: F401
from tests.test_payouts import PayoutPaystack, _balance, _balanced, _wallet, ps  # noqa: F401

# The ₦5,000 due paid in the fixtures: Paystack keeps ₦178.38, so ₦5,046.62 lands in the
# Paystack balance; ₦5,000 is the rep's and ₦46.62 is Pritis's revenue.
OWED, REVENUE = 500_000, 4_662


@pytest.fixture
async def paid(setup: dict, ps: PayoutPaystack, clock) -> dict:
    payment = await _pay(setup)
    await send_webhook(setup["student"], {"event": "charge.success", "data": charge_data(payment)})
    boss = await staff_client(clock, "super_admin")
    try:
        yield {**setup, "payment": payment, "boss": boss}
    finally:
        await boss.__aexit__(None, None, None)


async def _set_account(ctx: dict):
    return await ctx["boss"].put("/api/admin/revenue/account", json={"bank_code": "044", "account_number": "0987654321"})


# --- Balance mode: money becomes Available after the hold -------------------------------------


async def test_balance_mode_releases_after_the_hold(paid: dict) -> None:
    assert await settlements.release_after_hold() == 0  # just paid: still Pending
    async with SessionLocal() as db:
        await db.execute(update(Payment).values(paid_at=Payment.paid_at - timedelta(hours=25)))
        await db.commit()
    assert await settlements.release_after_hold() == 1
    w = await _wallet()
    assert (w.pending_kobo, w.available_kobo) == (0, OWED)
    assert await settlements.release_after_hold() == 0  # idempotent
    assert await settlements.run(None) == "balance mode: released 0"


# --- Withdrawing revenue -------------------------------------------------------------------------


async def test_withdraw_only_revenue_to_the_saved_account(paid: dict, ps: PayoutPaystack) -> None:
    boss = paid["boss"]
    overview = (await boss.get("/api/admin/revenue")).json()
    assert overview["settlement_mode"] == "balance"
    assert (overview["owed_to_reps_kobo"], overview["revenue_kobo"]) == (OWED, REVENUE)
    assert overview["withdrawable_kobo"] == REVENUE and overview["account"] is None

    # No account yet.
    resp = await boss.post("/api/admin/revenue/withdrawals", json={"amount_kobo": 1_000})
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "no_revenue_account"

    # Save Pritis's account: looked up with Paystack, every super admin is told.
    resp = await _set_account(paid)
    assert resp.status_code == 200, resp.text
    assert resp.json()["account"]["label"] == "Access Bank · ADA NGOZI OBI · ••••4321"
    async with SessionLocal() as db:
        assert await db.scalar(select(EmailOutbox.id).where(EmailOutbox.template == "revenue_account_changed"))

    # More than the revenue (Paystack's ₦10 fee included) is refused: the rest is the rep's.
    resp = await boss.post("/api/admin/revenue/withdrawals", json={"amount_kobo": REVENUE})
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "too_much"

    resp = await boss.post("/api/admin/revenue/withdrawals", json={"amount_kobo": REVENUE - 1_000, "note": "October"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["withdrawals"][0]["status"] == "processing"
    assert ps.transfers[-1]["amount"] == REVENUE - 1_000 and ps.transfers[-1]["recipient"] == "RCP_new1"

    # Paystack confirms.
    async with SessionLocal() as db:
        w = await db.scalar(select(RevenueWithdrawal))
    await send_webhook(paid["student"], {"event": "transfer.success", "data": {
        "id": 77001, "reference": w.reference, "transfer_code": "TRF_rev", "fee_charged": 1_000}})
    after = (await boss.get("/api/admin/revenue")).json()
    assert after["withdrawals"][0]["status"] == "paid"
    assert after["withdrawn_kobo"] == REVENUE - 1_000 and after["withdrawable_kobo"] == 0
    assert await _balance("paystack_clearing") == OWED  # only the rep's money is left
    assert await _balanced()
    # A repeated webhook changes nothing.
    await send_webhook(paid["student"], {"event": "transfer.success", "data": {
        "id": 77001, "reference": w.reference, "transfer_code": "TRF_rev", "fee_charged": 1_000}})
    assert await _balance("pritis_bank") == REVENUE - 1_000


async def test_the_real_paystack_balance_caps_withdrawals(paid: dict, ps: PayoutPaystack) -> None:
    await _set_account(paid)
    ps.balance = OWED + 2_000  # e.g. money not yet in the balance: only ₦20 above what reps are owed
    overview = (await paid["boss"].get("/api/admin/revenue")).json()
    assert overview["withdrawable_kobo"] == 2_000
    resp = await paid["boss"].post("/api/admin/revenue/withdrawals", json={"amount_kobo": 1_500})
    assert resp.status_code == 409  # ₦15 + ₦10 fee is more than ₦20


async def test_refused_transfer_fails_and_nothing_leaves(paid: dict, ps: PayoutPaystack) -> None:
    await _set_account(paid)
    ps.refuse_transfer = True
    resp = await paid["boss"].post("/api/admin/revenue/withdrawals", json={"amount_kobo": 2_000})
    assert resp.json()["withdrawals"][0]["status"] == "failed"
    assert await _balance("pritis_bank") == 0
    assert (await paid["boss"].get("/api/admin/revenue")).json()["withdrawable_kobo"] == REVENUE


async def test_only_the_super_admin_moves_money(paid: dict) -> None:
    accountant = paid["accountant"]
    assert (await accountant.get("/api/admin/revenue")).status_code == 200  # can look
    assert (await accountant.put("/api/admin/revenue/account", json={
        "bank_code": "044", "account_number": "0987654321"})).status_code == 403
    assert (await accountant.post("/api/admin/revenue/withdrawals", json={"amount_kobo": 1_000})).status_code == 403
    assert (await paid["student"].get("/api/admin/revenue")).status_code == 401
