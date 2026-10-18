"""The nightly check (design section 11, 02:00 Lagos):

1. every ledger transaction balances;
2. every rep's cached wallet equals what the ledger says (fixed, and reported, if not);
3. every successful payment has its ledger entries, and every settled one its settlement;
4. yesterday's successful Paystack transactions match our successful payments.

The result is saved; on any mismatch the accountant and super admin are emailed.
"""

import logging
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.errors import AppError
from app.models import AdminUser, LedgerAccount, LedgerEntry, Payment, ReconciliationRun, RepWallet
from app.services import ledger
from app.services.email import queue_email
from app.services.paystack import PaystackClient

log = logging.getLogger("app.reconciliation")
LAGOS = ZoneInfo("Africa/Lagos")


def previous_lagos_day(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Yesterday, midnight to midnight in Lagos, as UTC datetimes."""
    today = (now or utcnow()).astimezone(LAGOS).date()
    end = datetime.combine(today, time.min, tzinfo=LAGOS)
    return end - timedelta(days=1), end


async def _check_wallets(db: AsyncSession, items: list[dict[str, Any]]) -> int:
    owners = set((await db.scalars(
        select(LedgerAccount.owner_user_id).where(LedgerAccount.owner_user_id.is_not(None)).distinct()
    )).all())
    owners |= set((await db.scalars(select(RepWallet.user_id))).all())
    for user_id in owners:
        expected = {
            "pending_kobo": await ledger.balance(db, ledger.rep_account("rep_pending", user_id)),
            "available_kobo": await ledger.balance(db, ledger.rep_account("rep_available", user_id)),
            "held_kobo": await ledger.balance(db, ledger.rep_account("payout_hold", user_id)),
            "refund_held_kobo": await ledger.balance(db, ledger.rep_account("refund_hold", user_id)),
        }
        wallet = await ledger.locked_wallet(db, user_id)
        cached = {k: getattr(wallet, k) for k in expected}
        if cached != expected:
            items.append({"kind": "wallet_mismatch", "ref": str(user_id),
                          "detail": f"cached {cached} but the ledger says {expected}; cache corrected"})
            for key, value in expected.items():
                setattr(wallet, key, value)
            wallet.version += 1
    return len(owners)


async def _check_payment_postings(db: AsyncSession, items: list[dict[str, Any]]) -> int:
    def posted(ref_type: str):
        return exists().where(LedgerEntry.ref_type == ref_type, LedgerEntry.ref_id == Payment.id)

    unposted = (await db.scalars(
        select(Payment.reference).where(Payment.status.in_(("success", "refunded")), ~posted("payment"))
    )).all()
    for reference in unposted:
        items.append({"kind": "payment_not_in_ledger", "ref": reference,
                      "detail": "Successful payment has no ledger entries"})
    unsettled_posting = (await db.scalars(
        select(Payment.reference).where(Payment.settled_at.is_not(None), ~posted("settlement"))
    )).all()
    for reference in unsettled_posting:
        items.append({"kind": "settlement_not_in_ledger", "ref": reference,
                      "detail": "Payment is marked settled but has no settlement entries"})
    return len(unposted) + len(unsettled_posting)


async def _compare_with_paystack(
    db: AsyncSession, paystack: PaystackClient, start: datetime, end: datetime, items: list[dict[str, Any]]
) -> dict[str, int]:
    theirs = {
        t["reference"]: t for t in await paystack.list_transactions(start, end)
        if str(t.get("reference", "")).startswith("DPP-")  # ignore other products on the account
    }
    ours = {
        p.reference: p for p in (await db.scalars(
            select(Payment).where(Payment.status.in_(("success", "refunded")),
                                  Payment.paid_at >= start, Payment.paid_at < end)
        )).all()
    }
    for reference, tx in theirs.items():
        payment = ours.get(reference)
        if payment is None:
            recorded = await db.scalar(select(Payment).where(Payment.reference == reference))
            items.append({"kind": "paid_at_paystack_not_recorded", "ref": reference,
                          "detail": f"Paystack has it as paid; ours is {recorded.status if recorded else 'missing'}"})
        elif int(tx.get("amount", -1)) != payment.total_kobo:
            items.append({"kind": "amount_mismatch", "ref": reference,
                          "detail": f"Paystack {tx.get('amount')} kobo, ours {payment.total_kobo} kobo"})
    for reference in ours.keys() - theirs.keys():
        if ours[reference].status == "refunded":
            continue  # Paystack may list a refunded transaction as reversed rather than success
        items.append({"kind": "recorded_not_at_paystack", "ref": reference,
                      "detail": "We have it as paid but Paystack's list for the day doesn't"})
    return {"paystack_transactions": len(theirs), "our_payments": len(ours)}


async def run(paystack: PaystackClient | None, now: datetime | None = None) -> ReconciliationRun:
    start, end = previous_lagos_day(now)
    items: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    status = "clean"
    async with SessionLocal() as db:
        unbalanced = await ledger.unbalanced_transactions(db)
        for txn_id in unbalanced:
            items.append({"kind": "ledger_unbalanced", "ref": str(txn_id), "detail": "Debits don't equal credits"})
        summary["unbalanced_transactions"] = len(unbalanced)
        summary["wallets_checked"] = await _check_wallets(db, items)
        summary["payments_missing_postings"] = await _check_payment_postings(db, items)
        if paystack is not None:
            try:
                summary.update(await _compare_with_paystack(db, paystack, start, end, items))
            except AppError as exc:
                status = "error"
                items.append({"kind": "paystack_unreachable", "ref": "", "detail": exc.message})
        else:
            summary["paystack"] = "not configured; comparison skipped"
        if items and status != "error":
            status = "mismatch"

        run_row = ReconciliationRun(status=status, period_start=start, period_end=end, summary=summary, items=items)
        db.add(run_row)
        if status != "clean":
            recipients = (await db.scalars(
                select(AdminUser.email).where(
                    AdminUser.status == "active", AdminUser.role.in_(("accountant", "super_admin"))
                )
            )).all()
            for email in recipients:
                await queue_email(db, email, "reconciliation_alert", {
                    "status": status, "count": len(items),
                    "day": start.astimezone(LAGOS).strftime("%d %b %Y"),
                    "items": [f"{i['kind']}: {i['ref']} {i['detail']}".strip() for i in items[:10]],
                    "link": f"{get_settings().admin_base_url}/settlements.html",
                })
        await db.commit()
        await db.refresh(run_row)
    log.info("Reconciliation for %s: %s (%d item(s))", start.date(), status, len(items))
    return run_row
