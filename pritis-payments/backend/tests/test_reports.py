import csv
import io

import pytest
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models import LedgerEntry, Payment, PayoutRequest, RepWallet
from app.services import ledger
from tests.people import request_payout, staff_client, user_client
from tests.test_payments import charge_data, send_webhook
from tests.test_payouts import (  # noqa: F401  (fixtures)
    PayoutPaystack,
    _payout_row,
    clock,
    fake,
    funded,
    ps,
    setup,
    transfer_event,
)


async def _student_pays(funded: dict, email: str, channel: str, fee: int) -> dict:
    """Another student opens the rep's link and pays."""
    c = await user_client(email)
    try:
        due = (await funded["rep"].get(f"/api/user/rep/dues/{funded['due_id']}")).json()
        slug = due["share_url"].split("s=")[1]
        await c.post(f"/api/user/dues/by-link/{slug}")
        resp = await c.post("/api/user/payments", json={"due_id": funded["due_id"]})
        async with SessionLocal() as db:
            p = await db.get(Payment, resp.json()["payment_id"])
        payment = {"id": str(p.id), "reference": p.reference, "total_kobo": p.total_kobo}
        await send_webhook(c, {"event": "charge.success", "data": charge_data(payment, fees=fee, channel=channel)})
        return payment
    finally:
        await c.__aexit__(None, None, None)


@pytest.fixture
async def seeded(funded: dict, ps: PayoutPaystack) -> dict:
    """A small but realistic month: three payments (card, bank transfer, USSD) with
    different Paystack fees, one settled; a ₦3,000 payout paid, and a ₦1,000 one waiting."""
    await _student_pays(funded, "s2@example.com", "bank_transfer", 7_838)
    await _student_pays(funded, "s3@example.com", "ussd", 20_000)

    rep = funded["rep"]
    paid = (await request_payout(rep, 300_000)).json()
    await funded["accountant"].post(f"/api/admin/payouts/{paid['id']}/approve")
    row = await _payout_row(paid["id"])
    await send_webhook(funded["student"], transfer_event("transfer.success", row))
    waiting = (await request_payout(rep, 100_000)).json()
    return {**funded, "paid_payout": paid, "waiting_payout": waiting}


async def _truth() -> dict:
    """The same figures straight from the tables, to compare against."""
    async with SessionLocal() as db:
        payments = (await db.scalars(select(Payment).where(Payment.status == "success"))).all()
        wallets = (await db.scalars(select(RepWallet))).all()
        paid = (await db.scalars(select(PayoutRequest).where(PayoutRequest.status == "paid"))).all()
        clearing = await ledger.balance(db, "paystack_clearing")
        platform = await ledger.balance(db, "platform_revenue")
        payout_fees = await ledger.balance(db, "payout_fee_revenue")
    return {
        "count": len(payments),
        "volume": sum(p.total_kobo for p in payments),
        "fees": sum(p.paystack_fee_actual_kobo for p in payments),
        "service_revenue": sum(p.service_charge_kobo - p.paystack_fee_actual_kobo for p in payments),
        "owed": sum(w.pending_kobo + w.available_kobo + w.held_kobo for w in wallets),
        "paid_out": sum(p.net_kobo for p in paid),
        "payout_platform_fees": sum(p.platform_fee_kobo for p in paid),
        "clearing": clearing, "platform": platform, "payout_fees": payout_fees,
    }


# --- The phase 9 "done when": every figure matches the ledger and payments ---------------------


async def test_summary_matches_the_ledger_and_payments(seeded: dict) -> None:
    truth = await _truth()
    s = (await seeded["accountant"].get("/api/admin/reports/summary")).json()

    assert s["payments_count"] == truth["count"] == 3
    assert s["gross_volume_kobo"] == truth["volume"] == 3 * 522_500
    assert s["paystack_fees_kobo"] == truth["fees"] == 17_838 + 7_838 + 20_000
    assert s["service_charge_revenue_kobo"] == truth["service_revenue"] == truth["platform"]
    assert s["payout_fee_revenue_kobo"] == truth["payout_fees"] == truth["payout_platform_fees"] == 3_000
    assert s["revenue_kobo"] == truth["service_revenue"] + 3_000
    assert s["owed_to_reps_kobo"] == truth["owed"]
    assert (s["owed_pending_kobo"], s["owed_available_kobo"], s["owed_held_kobo"]) == (1_000_000, 100_000, 100_000)
    assert s["payouts_waiting_count"] == 1 and s["payouts_waiting_kobo"] == 100_000
    assert s["paid_out_kobo"] == truth["paid_out"] == 300_000 - 3_000 - 1_000
    assert s["payments_today_count"] == 3

    # The books balance: what Paystack holds for us = what we owe reps + our revenue.
    assert truth["clearing"] == s["owed_to_reps_kobo"] + s["revenue_kobo"]


async def test_graphs_match_the_ledger_and_payments(seeded: dict) -> None:
    truth = await _truth()
    t = (await seeded["accountant"].get("/api/admin/reports/trends")).json()

    today = t["payments_per_day"][-1]
    assert (today["count"], today["value_kobo"]) == (3, truth["volume"])
    assert sum(d["count"] for d in t["payments_per_day"]) == 3
    assert {c["channel"]: c["count"] for c in t["channel_mix"]} == {"card": 1, "bank_transfer": 1, "ussd": 1}
    assert t["collections_by_class"] == [{"class_name": "CSC 200 Level", "collected_kobo": 1_500_000, "payments": 3}]
    assert {p["status"]: p["count"] for p in t["payouts_by_status"]} == {"paid": 1, "requested": 1}
    last = t["owed_vs_paid_out"][-1]
    assert last["owed_kobo"] == truth["owed"] and last["paid_out_kobo"] == truth["paid_out"]

    rev = (await seeded["accountant"].get("/api/admin/reports/revenue")).json()
    assert sum(m["total_kobo"] for m in rev["by_month"]) == truth["platform"] + truth["payout_fees"]
    assert rev["period"]["gross_volume_kobo"] == truth["volume"]
    assert rev["period"]["dues_kobo"] == 1_500_000


async def test_ledger_explorer(seeded: dict) -> None:
    accountant = seeded["accountant"]
    accounts = {a["code"]: a for a in (await accountant.get("/api/admin/ledger/accounts")).json()}
    truth = await _truth()
    assert accounts["paystack_clearing"]["balance_kobo"] == truth["clearing"]
    assert accounts["payout_fee_revenue"]["balance_kobo"] == 3_000

    page = (await accountant.get("/api/admin/ledger?ref_type=payout_paid")).json()
    assert page["total"] == 3 and {e["account_code"].split(":")[0] for e in page["items"]} == {
        "payout_hold", "paystack_clearing", "payout_fee_revenue"}
    txn = page["items"][0]["txn_id"]
    whole = (await accountant.get(f"/api/admin/ledger?txn_id={txn}")).json()
    debit = sum(e["amount_kobo"] for e in whole["items"] if e["direction"] == "debit")
    credit = sum(e["amount_kobo"] for e in whole["items"] if e["direction"] == "credit")
    assert debit == credit == 300_000

    reps = (await accountant.get("/api/admin/ledger?account=rep_pending:")).json()
    assert reps["total"] >= 3


async def test_exports_open_correctly(seeded: dict) -> None:
    accountant = seeded["accountant"]
    resp = await accountant.get("/api/admin/reports/export.csv")
    assert resp.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(resp.text.lstrip("﻿"))))
    assert len(rows) == 3
    assert {r["Channel"] for r in rows} == {"card", "bank_transfer", "ussd"}
    assert all(r["Total paid (NGN)"] == "5,225.00" for r in rows)
    revenue = sum(int(r["Platform revenue (NGN)"].replace(",", "").replace(".", "")) for r in rows)
    assert revenue == (await _truth())["service_revenue"]

    pdf = await accountant.get("/api/admin/reports/statement.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert "statement-" in pdf.headers["content-disposition"]


async def test_date_range_rules(seeded: dict) -> None:
    accountant = seeded["accountant"]
    assert (await accountant.get("/api/admin/reports/trends?from=2026-10-10&to=2026-10-01")).status_code == 422
    assert (await accountant.get("/api/admin/reports/trends?from=2020-01-01&to=2026-10-01")).status_code == 422
    empty = (await accountant.get("/api/admin/reports/trends?from=2025-01-01&to=2025-01-31")).json()
    assert sum(d["count"] for d in empty["payments_per_day"]) == 0 and len(empty["payments_per_day"]) == 31


# --- Search for support ---------------------------------------------------------------------------


async def test_support_searches_users_and_payments(seeded: dict, clock) -> None:
    support = await staff_client(clock, "support")
    try:
        by_email = (await support.get("/api/admin/users?q=s2@example")).json()
        assert [u["email"] for u in by_email] == ["s2@example.com"]
        assert len((await support.get("/api/admin/users?q=0803 123")).json()) >= 3  # phone digits
        detail = (await support.get(f"/api/admin/users/{by_email[0]['id']}")).json()
        assert detail["classes"] == ["CSC 200 Level"] and detail["payments"][0]["channel"] == "bank_transfer"

        rep = (await support.get("/api/admin/users?q=rep@example.com")).json()[0]
        rep_detail = (await support.get(f"/api/admin/users/{rep['id']}")).json()
        assert rep_detail["rep_status"] == "approved" and rep_detail["rep_class"] == "CSC 200 Level"

        ref = seeded["payment"]["reference"]
        found = (await support.get(f"/api/admin/payments?q={ref}")).json()
        assert len(found) == 1 and found[0]["status"] == "success"
        assert len((await support.get("/api/admin/payments?status=success")).json()) == 3

        # Support doesn't see the money dashboards.
        assert (await support.get("/api/admin/reports/summary")).status_code == 403
        assert (await support.get("/api/admin/ledger")).status_code == 403
        assert (await support.get("/api/admin/reports/export.csv")).status_code == 403
        overview = (await support.get("/api/admin/reports/overview")).json()
        assert overview["payouts_waiting"] == 1 and overview["reps"] == 1
    finally:
        await support.__aexit__(None, None, None)


async def test_accountant_can_search_read_only(seeded: dict) -> None:
    assert (await seeded["accountant"].get("/api/admin/users?q=Ada")).status_code == 200
    assert (await seeded["accountant"].get("/api/admin/payments?q=DPP")).status_code == 200


async def test_search_needs_two_characters(seeded: dict) -> None:
    assert (await seeded["accountant"].get("/api/admin/users?q=a")).status_code == 422


async def test_ledger_totals_never_drift(seeded: dict) -> None:
    async with SessionLocal() as db:
        assert await ledger.unbalanced_transactions(db) == []
        entries = await db.scalar(select(func.count()).select_from(LedgerEntry))
    assert entries > 0


async def test_users_list_without_searching(seeded: dict, clock) -> None:
    support = await staff_client(clock, "support")
    everyone = (await support.get("/api/admin/users")).json()
    assert len(everyone) >= 3
    assert [u["created_at"] for u in everyone] == sorted((u["created_at"] for u in everyone), reverse=True)
    reps = (await support.get("/api/admin/users?kind=reps")).json()
    assert reps and all(u["is_rep"] for u in reps)
    students = (await support.get("/api/admin/users?kind=students")).json()
    assert students and not any(u["is_rep"] for u in students)
    page2 = (await support.get("/api/admin/users?limit=1&offset=1")).json()
    assert page2[0]["id"] == everyone[1]["id"]
    await support.__aexit__(None, None, None)
