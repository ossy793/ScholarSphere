"""Withdrawing Pritis's revenue from the Paystack balance (owner's decision, 2026-10-19: the
funds stay in the Paystack balance instead of settling to the bank every day).

The money in the Paystack balance is partly the reps' (everything they're owed: Pending,
Available, in a payout, or set aside for a refund) and partly Pritis's revenue. Only the
revenue can be withdrawn, and only to Pritis's own bank account saved in the app by the
super admin. The amount allowed is the smaller of:
    what the ledger says is Pritis's (revenue - fees Pritis bore - earlier withdrawals), and
    the Paystack balance - everything owed to reps,
less any withdrawal still being sent, and Paystack's transfer fee comes out of it too.

Ledger, when Paystack confirms the transfer:
    debit pritis_bank A, debit withdrawal_fee_expense F    credit paystack_clearing A + F
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.clock import utcnow
from app.core.db import SessionLocal
from app.core.errors import AppError
from app.models import AdminUser, AppSetting, RevenueWithdrawal
from app.services import app_settings, audit, ledger, reports
from app.services.email import queue_email
from app.services.paystack import PaystackClient, TransferRefused
from app.services.payouts import naira, transfer_fee

log = logging.getLogger("app.revenue")

ACCOUNT_KEY = "revenue_account"
REFERENCE_PREFIX = "dpp-revenue-"
OWED_KINDS = ("rep_pending", "rep_available", "payout_hold", "refund_hold")
CHECK_AFTER_MINUTES = 10


# --- Pritis's bank account -----------------------------------------------------------------------


async def _account_row(db: AsyncSession, lock: bool = False) -> AppSetting | None:
    query = select(AppSetting).where(AppSetting.key == ACCOUNT_KEY)
    return await db.scalar(query.with_for_update() if lock else query)


async def get_account(db: AsyncSession) -> dict[str, Any] | None:
    row = await _account_row(db)
    return row.value if row else None


def account_label(account: dict[str, Any]) -> str:
    return f"{account['bank_name']} · {account['account_name']} · ••••{account['last4']}"


async def set_account(
    db: AsyncSession, bank_code: str, account_number: str, actor: AdminUser, request: Request | None,
    paystack: PaystackClient,
) -> dict[str, Any]:
    """Saves the account revenue goes to, looked up and registered with Paystack. Every
    super admin is emailed: changing where money goes is a classic fraud route."""
    if await db.scalar(select(RevenueWithdrawal.id).where(RevenueWithdrawal.status == "processing")):
        raise AppError("withdrawal_in_progress", "Wait until the withdrawal being sent has finished.", 409)
    bank_name = await paystack.bank_name(bank_code)
    account_name = await paystack.resolve_account(account_number, bank_code)
    recipient = await paystack.create_transfer_recipient(
        account_name=account_name, account_number=account_number, bank_code=bank_code)
    before = await get_account(db)
    value = {
        "bank_code": bank_code, "bank_name": bank_name, "account_name": account_name,
        "last4": account_number[-4:], "account_number": crypto.encrypt(account_number),
        "recipient_code": recipient, "set_by": actor.email, "set_at": utcnow().isoformat(),
    }
    row = await _account_row(db, lock=True)
    if row is None:
        db.add(AppSetting(key=ACCOUNT_KEY, value=value, updated_by=actor.id))
    else:
        row.value, row.updated_by = value, actor.id
    await audit.record(db, actor=actor, action="revenue.account_set", entity="app_settings",
                       before={"account": account_label(before)} if before else None,
                       after={"account": account_label(value)}, request=request)
    admins = (await db.scalars(select(AdminUser.email).where(
        AdminUser.status == "active", AdminUser.role == "super_admin"))).all()
    for email in admins:
        await queue_email(db, email, "revenue_account_changed", {
            "account": account_label(value), "previous": account_label(before) if before else "none",
            "changed_by": actor.full_name or actor.email,
        })
    return value


# --- How much can be withdrawn -------------------------------------------------------------------


@dataclass(frozen=True)
class Position:
    owed_to_reps_kobo: int
    revenue_kobo: int  # Pritis's share by the ledger, after fees it bore and earlier withdrawals
    processing_kobo: int  # withdrawals being sent, fees included
    withdrawn_kobo: int
    paystack_balance_kobo: int | None
    withdrawable_kobo: int  # the most that can leave now, Paystack's fee included


async def position(db: AsyncSession, paystack_balance: int | None) -> Position:
    # Database sums come back as Decimal; money here is whole kobo.
    owed = int(sum([await reports.account_total(db, kind=k) for k in OWED_KINDS]))
    clearing = int(await ledger.balance(db, "paystack_clearing"))
    processing = int(await db.scalar(
        select(func.coalesce(func.sum(RevenueWithdrawal.amount_kobo + RevenueWithdrawal.transfer_fee_kobo), 0))
        .where(RevenueWithdrawal.status == "processing")
    ))
    revenue = clearing - owed  # what in Paystack is Pritis's, by the ledger
    limit = revenue - processing
    if paystack_balance is not None:
        limit = min(limit, paystack_balance - owed - processing)
    return Position(
        owed_to_reps_kobo=owed, revenue_kobo=revenue, processing_kobo=processing,
        withdrawn_kobo=int(await ledger.balance(db, "pritis_bank")), paystack_balance_kobo=paystack_balance,
        withdrawable_kobo=max(limit, 0),
    )


# --- Withdrawing ---------------------------------------------------------------------------------


async def withdraw(
    db: AsyncSession, amount_kobo: int, note: str | None, actor: AdminUser, request: Request | None,
    paystack: PaystackClient,
) -> RevenueWithdrawal:
    account = await get_account(db)
    if not account:
        raise AppError("no_revenue_account", "Save Pritis's bank account first.", 409)
    # One at a time, decided under a lock so two clicks can't both pass the checks.
    await _account_row(db, lock=True)
    if await db.scalar(select(RevenueWithdrawal.id).where(RevenueWithdrawal.status == "processing")):
        raise AppError("withdrawal_in_progress", "A withdrawal is still being sent. Try again once it's done.", 409)
    fee = transfer_fee(amount_kobo, await app_settings.get_fee_settings(db))
    pos = await position(db, await paystack.get_balance())
    if amount_kobo + fee > pos.withdrawable_kobo:
        raise AppError(
            "too_much",
            f"You can withdraw up to {naira(max(pos.withdrawable_kobo - fee, 0))} now (Paystack's {naira(fee)} "
            f"transfer fee comes out too). The rest of the balance belongs to reps.",
            409,
        )
    w = RevenueWithdrawal(
        amount_kobo=amount_kobo, transfer_fee_kobo=fee, status="processing",
        reference=f"{REFERENCE_PREFIX}{uuid.uuid4().hex}", recipient_code=account["recipient_code"],
        bank_label=account_label(account), requested_by=actor.id, note=(note or None),
    )
    db.add(w)
    await db.flush()
    await audit.record(db, actor=actor, action="revenue.withdraw", entity="revenue_withdrawal", entity_id=w.id,
                       after={"amount_kobo": amount_kobo, "fee_kobo": fee, "to": w.bank_label,
                              "paystack_balance_kobo": pos.paystack_balance_kobo}, request=request)
    await db.commit()  # Processing before Paystack is asked, so it can never be sent twice

    try:
        data = await paystack.initiate_transfer(
            amount_kobo=amount_kobo, recipient_code=w.recipient_code, reference=w.reference,
            reason="Pritis DPP revenue")
    except TransferRefused as exc:
        w = await db.get(RevenueWithdrawal, w.id, with_for_update=True, populate_existing=True)
        w.status, w.failure_reason = "failed", f"Paystack refused the transfer: {exc}"
        await db.commit()
        return w
    except AppError:
        log.warning("Revenue withdrawal %s unclear; left processing for the status check", w.reference)
        return w
    w = await db.get(RevenueWithdrawal, w.id, with_for_update=True, populate_existing=True)
    await apply_transfer_result(db, w, {**data, "reference": w.reference})
    await db.commit()
    return w


async def apply_transfer_result(db: AsyncSession, w: RevenueWithdrawal, data: dict[str, Any]) -> bool:
    """Paystack's transfer status for a withdrawal the caller has locked. Idempotent."""
    if w.status != "processing":
        return False
    if data.get("transfer_code"):
        w.paystack_transfer_code = data["transfer_code"]
    status = str(data.get("status", "")).lower()
    if status == "success":
        actual = data.get("fee_charged", data.get("fees"))
        fee = int(actual) if actual is not None else w.transfer_fee_kobo
        w.transfer_fee_actual_kobo = fee if actual is not None else None
        await ledger.post(db, [
            ledger.Line("pritis_bank", "debit", w.amount_kobo),
            ledger.Line("withdrawal_fee_expense", "debit", fee),
            ledger.Line("paystack_clearing", "credit", w.amount_kobo + fee),
        ], ref_type="revenue_withdrawal", ref_id=w.id, memo="Revenue withdrawn to Pritis's bank")
        w.status, w.paid_at = "paid", utcnow()
        return True
    if status in ("failed", "reversed", "abandoned", "rejected"):
        w.status = "failed"
        w.failure_reason = str(data.get("reason") or data.get("message") or f"Transfer {status}")[:1000]
        return True
    if status == "otp":
        w.failure_reason = ("Paystack is asking for an OTP to finalise this transfer. Turn off OTP for API "
                            "transfers in Paystack settings, or finalise it on the Paystack dashboard.")
    return False


async def apply_transfer_event(db: AsyncSession, event_type: str, data: dict[str, Any]) -> str:
    w = await db.scalar(
        select(RevenueWithdrawal).where(RevenueWithdrawal.reference == data.get("reference")).with_for_update()
    )
    if w is None:
        return "ignored"
    status = {"transfer.success": "success", "transfer.failed": "failed", "transfer.reversed": "reversed"}[event_type]
    await apply_transfer_result(db, w, {**data, "status": status})
    return "processed"


async def check_processing(paystack: PaystackClient) -> dict[str, int]:
    """With the payout check every 10 minutes: withdrawals still Processing, in case a
    webhook was missed. One Paystack never received is marked failed (nothing left)."""
    counts = {"checked": 0, "finished": 0}
    async with SessionLocal() as db:
        stale = (await db.scalars(
            select(RevenueWithdrawal).where(
                RevenueWithdrawal.status == "processing",
                RevenueWithdrawal.created_at < utcnow() - timedelta(minutes=CHECK_AFTER_MINUTES),
            ).limit(20).with_for_update(skip_locked=True)
        )).all()
        for w in stale:
            counts["checked"] += 1
            w.last_checked_at = utcnow()
            try:
                data = await paystack.verify_transfer(w.reference)
            except AppError:
                continue
            if data is None:
                w.status, w.failure_reason = "failed", "The transfer never reached Paystack. Nothing left the balance."
                counts["finished"] += 1
            elif await apply_transfer_result(db, w, data):
                counts["finished"] += 1
        await db.commit()
    return counts
