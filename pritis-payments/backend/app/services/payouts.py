"""Payouts (design sections 5 and 8): a rep's Available balance, sent to their verified
bank account through Paystack Transfers after the accountant or super admin approves.

Ledger, as in the design:
    request  P : debit rep available P            credit payout hold P
    rejected / failed / reversed: the reverse of the request
    paid       : debit payout hold P               credit Paystack clearing N + T,
                                                   credit payout fee revenue X
    actual transfer fee differs from T: the difference between payout fee revenue and clearing
where N = P - X - T is what reaches the rep's bank.

A transfer is never sent twice: each payout has one unique transfer reference, and an
unclear answer from Paystack is resolved by checking that reference, never by retrying.
"""

import logging
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.deps import RepContext
from app.core.errors import AppError
from app.models import AdminUser, BankAccountChange, PayoutRequest, RepBankAccount, User
from app.schemas.settings import FeeSettings
from app.services import app_settings, audit, codes, fees, ledger
from app.services.email import queue_email
from app.services.paystack import PaystackClient, TransferRefused

log = logging.getLogger("app.payouts")

CHECK_AFTER_MINUTES = 10
OPEN = ("requested", "processing")


def naira(kobo: int) -> str:
    return f"₦{kobo // 100:,}" + (f".{kobo % 100:02d}" if kobo % 100 else "")


def transfer_fee(amount_kobo: int, settings: FeeSettings) -> int:
    """Paystack's transfer fee for this amount, from the bands in settings."""
    for band in settings.transfer_fee_bands:
        if band.up_to_kobo is None or amount_kobo <= band.up_to_kobo:
            return band.fee_kobo
    return settings.transfer_fee_bands[-1].fee_kobo


async def quote(db: AsyncSession, amount_kobo: int) -> fees.PayoutQuote:
    settings = await app_settings.get_fee_settings(db)
    return fees.rep_payout(amount_kobo, Decimal(settings.payout_fee_percent), transfer_fee(amount_kobo, settings))


async def blocked_reason(db: AsyncSession, user_id: uuid.UUID) -> str | None:
    """Why this rep can't request a payout right now, if they can't."""
    account = await db.scalar(select(RepBankAccount).where(RepBankAccount.user_id == user_id))
    if account is None or not account.paystack_recipient_code or account.verified_at is None:
        return "Your payout account isn't verified yet."
    if await db.scalar(select(BankAccountChange.id).where(
        BankAccountChange.user_id == user_id, BankAccountChange.status == "pending"
    )):
        return "Payouts are paused while your new bank account is being checked."
    if await db.scalar(select(PayoutRequest.id).where(
        PayoutRequest.rep_user_id == user_id, PayoutRequest.status.in_(OPEN)
    )):
        return "You already have a payout in progress. You can request another once it's done."
    return None


async def _notify_rep(db: AsyncSession, payout: PayoutRequest, kind: str) -> None:
    rep = await db.get(User, payout.rep_user_id)
    await queue_email(db, rep.email, "payout_update", {
        "full_name": rep.full_name, "kind": kind, "amount": naira(payout.amount_kobo),
        "net": naira(payout.net_kobo), "bank": payout.bank_label or "",
        "reason": payout.reject_reason or payout.failure_reason or "",
        "link": f"{get_settings().app_base_url}/rep.html",
    })


async def _staff_emails(db: AsyncSession) -> list[str]:
    return list((await db.scalars(select(AdminUser.email).where(
        AdminUser.status == "active", AdminUser.role.in_(("accountant", "super_admin"))
    ))).all())


# --- Requesting -------------------------------------------------------------------------


async def _check_request(db: AsyncSession, rep: RepContext, amount_kobo: int):
    """Everything that must be true for this payout request. Returns (locked wallet, quote)."""
    if reason := await blocked_reason(db, rep.user.id):
        raise AppError("payout_blocked", reason, 409)
    wallet = await ledger.locked_wallet(db, rep.user.id)
    if amount_kobo > wallet.available_kobo:
        raise AppError("insufficient_available",
                       f"You can request up to {naira(max(wallet.available_kobo, 0))} (your Available balance).", 409)
    q = await quote(db, amount_kobo)
    if q.net_kobo <= 0:
        raise AppError("payout_too_small", "That amount is too small to cover the payout service charge.", 409)
    return wallet, q


async def send_confirmation_code(db: AsyncSession, rep: RepContext, amount_kobo: int) -> str:
    """Step 1 of a payout request (owner's request, 2026-10-22): an emailed code that only
    confirms this amount, so a stolen login alone can't move a rep's money. Returns the
    address it went to."""
    _, q = await _check_request(db, rep, amount_kobo)
    account = await db.scalar(select(RepBankAccount).where(RepBankAccount.user_id == rep.user.id))
    last4 = crypto.decrypt(account.account_number)[-4:]
    await codes.send_code(db, rep.user, "confirm_payout", context=str(amount_kobo), extra={
        "amount": naira(amount_kobo), "net": naira(q.net_kobo),
        "bank": f"{account.bank_name} · {account.account_name} · ••••{last4}",
    })
    return rep.user.email


async def request_payout(db: AsyncSession, rep: RepContext, amount_kobo: int, code: str) -> PayoutRequest:
    """Step 2: the emailed code for this amount, then the request itself."""
    await codes.use_code(db, rep.user, "confirm_payout", code, context=str(amount_kobo))
    wallet, q = await _check_request(db, rep, amount_kobo)

    account = await db.scalar(select(RepBankAccount).where(RepBankAccount.user_id == rep.user.id))
    last4 = crypto.decrypt(account.account_number)[-4:]
    payout = PayoutRequest(
        rep_user_id=rep.user.id, amount_kobo=amount_kobo, platform_fee_kobo=q.platform_fee_kobo,
        transfer_fee_kobo=q.transfer_fee_kobo, net_kobo=q.net_kobo, status="requested",
        transfer_reference=f"dpp-payout-{uuid.uuid4().hex}",
        recipient_code=account.paystack_recipient_code,
        bank_label=f"{account.bank_name} · {account.account_name} · ••••{last4}",
    )
    db.add(payout)
    await db.flush()
    # The amount moves from Available to the hold at once.
    await ledger.post(db, [
        ledger.Line(ledger.rep_account("rep_available", rep.user.id), "debit", amount_kobo),
        ledger.Line(ledger.rep_account("payout_hold", rep.user.id), "credit", amount_kobo),
    ], ref_type="payout_request", ref_id=payout.id, memo="Payout requested")
    wallet.available_kobo -= amount_kobo
    wallet.held_kobo += amount_kobo
    wallet.version += 1

    for email in await _staff_emails(db):
        await queue_email(db, email, "payout_requested", {
            "rep_name": rep.user.full_name, "class_name": rep.klass.name,
            "amount": naira(amount_kobo), "net": naira(q.net_kobo),
            "link": f"{get_settings().admin_base_url}/payouts.html",
        })
    return payout


# --- Releasing and paying ---------------------------------------------------------------


async def _release(db: AsyncSession, payout: PayoutRequest, status: str, reason: str) -> None:
    """Rejected, failed or reversed: the hold goes back to Available."""
    await ledger.post(db, [
        ledger.Line(ledger.rep_account("payout_hold", payout.rep_user_id), "debit", payout.amount_kobo),
        ledger.Line(ledger.rep_account("rep_available", payout.rep_user_id), "credit", payout.amount_kobo),
    ], ref_type="payout_release", ref_id=payout.id, memo=f"Payout {status}")
    wallet = await ledger.locked_wallet(db, payout.rep_user_id)
    wallet.held_kobo -= payout.amount_kobo
    wallet.available_kobo += payout.amount_kobo
    wallet.version += 1
    payout.status = status
    if status != "rejected":
        payout.failure_reason = reason[:1000]


async def _mark_paid(db: AsyncSession, payout: PayoutRequest, data: dict[str, Any]) -> None:
    n, t, x = payout.net_kobo, payout.transfer_fee_kobo, payout.platform_fee_kobo
    lines = [
        ledger.Line(ledger.rep_account("payout_hold", payout.rep_user_id), "debit", payout.amount_kobo),
        ledger.Line("paystack_clearing", "credit", n + t),
        ledger.Line("payout_fee_revenue", "credit", x),
    ]
    actual = data.get("fee_charged", data.get("fees"))
    if actual is not None:
        payout.transfer_fee_actual_kobo = int(actual)
        diff = int(actual) - t  # Paystack charged more (+) or less (-) than we estimated
        lines += [ledger.Line("payout_fee_revenue", "debit", diff), ledger.Line("paystack_clearing", "credit", diff)]
    await ledger.post(db, lines, ref_type="payout_paid", ref_id=payout.id, memo="Payout paid")
    wallet = await ledger.locked_wallet(db, payout.rep_user_id)
    wallet.held_kobo -= payout.amount_kobo
    wallet.version += 1
    payout.status = "paid"
    payout.paid_at = utcnow()
    payout.paystack_transfer_code = data.get("transfer_code") or payout.paystack_transfer_code


async def apply_transfer_result(db: AsyncSession, payout: PayoutRequest, data: dict[str, Any]) -> bool:
    """Applies Paystack's transfer status to a payout the caller has locked. Idempotent:
    a repeated webhook or check changes nothing. Returns True if the status changed."""
    if payout.status != "processing":
        return False
    if data.get("reference") and data["reference"] != payout.transfer_reference:
        raise AppError("reference_mismatch", "Transfer reference doesn't match this payout", 409)
    if data.get("transfer_code"):
        payout.paystack_transfer_code = data["transfer_code"]
    status = str(data.get("status", "")).lower()
    if status == "success":
        await _mark_paid(db, payout, data)
        await _notify_rep(db, payout, "paid")
        return True
    if status in ("failed", "reversed", "abandoned", "rejected"):
        final = "reversed" if status == "reversed" else "failed"
        await _release(db, payout, final, str(data.get("reason") or data.get("message") or f"Transfer {status}"))
        await _notify_rep(db, payout, final)
        return True
    if status == "otp":
        payout.failure_reason = ("Paystack is asking for an OTP to finalise this transfer. Turn off OTP for API "
                                 "transfers in Paystack settings, or finalise it on the Paystack dashboard.")
    return False


# --- Staff decisions ----------------------------------------------------------------------


async def _locked(db: AsyncSession, payout_id: uuid.UUID) -> PayoutRequest:
    payout = await db.get(PayoutRequest, payout_id, with_for_update=True)
    if payout is None:
        raise AppError("not_found", "Payout not found", 404)
    if payout.status != "requested":
        raise AppError("already_decided", "This payout has already been decided", 409)
    return payout


async def approve(
    db: AsyncSession, payout_id: uuid.UUID, actor: AdminUser, request: Request, paystack: PaystackClient
) -> PayoutRequest:
    """Checks the Paystack balance, marks the payout Processing (committed, so it can't be
    approved twice), then starts the transfer."""
    payout = await _locked(db, payout_id)
    if await db.scalar(select(BankAccountChange.id).where(
        BankAccountChange.user_id == payout.rep_user_id, BankAccountChange.status == "pending"
    )):
        raise AppError("payout_blocked", "This rep's bank account change is waiting for review.", 409)
    balance = await paystack.get_balance()
    needed = payout.net_kobo + payout.transfer_fee_kobo
    if balance < needed:
        raise AppError(
            "insufficient_paystack_balance",
            f"The Paystack balance is {naira(balance)}, but this transfer needs {naira(needed)} with its fee. Top it up first.",
            409,
        )
    payout.status = "processing"
    payout.reviewed_by = actor.id
    payout.reviewed_at = utcnow()
    await audit.record(db, actor=actor, action="payout.approve", entity="payout_request", entity_id=payout.id,
                       after={"amount_kobo": payout.amount_kobo, "net_kobo": payout.net_kobo,
                              "to": payout.bank_label, "paystack_balance_kobo": balance}, request=request)
    await db.commit()

    try:
        data = await paystack.initiate_transfer(
            amount_kobo=payout.net_kobo, recipient_code=payout.recipient_code,
            reference=payout.transfer_reference, reason="Pritis DPP payout",
        )
    except TransferRefused as exc:
        payout = await db.get(PayoutRequest, payout.id, with_for_update=True, populate_existing=True)
        await _release(db, payout, "failed", f"Paystack refused the transfer: {exc}")
        await _notify_rep(db, payout, "failed")
        await db.commit()
        return payout
    except AppError:
        # Unclear answer: leave it Processing. The status check asks Paystack about this
        # reference before anything else happens, so it's never sent twice.
        log.warning("Transfer %s unclear; left processing for the status check", payout.transfer_reference)
        return payout

    payout = await db.get(PayoutRequest, payout.id, with_for_update=True, populate_existing=True)
    await apply_transfer_result(db, payout, {**data, "reference": payout.transfer_reference})
    if payout.status == "processing":
        await _notify_rep(db, payout, "approved")
    await db.commit()
    return payout


async def reject(db: AsyncSession, payout_id: uuid.UUID, actor: AdminUser, reason: str, request: Request) -> PayoutRequest:
    payout = await _locked(db, payout_id)
    payout.reviewed_by = actor.id
    payout.reviewed_at = utcnow()
    payout.reject_reason = reason
    await _release(db, payout, "rejected", reason)
    await _notify_rep(db, payout, "rejected")
    await audit.record(db, actor=actor, action="payout.reject", entity="payout_request", entity_id=payout.id,
                       after={"amount_kobo": payout.amount_kobo, "reason": reason}, request=request)
    return payout


# --- Paystack telling us how it went -----------------------------------------------------------


async def apply_transfer_event(db: AsyncSession, event_type: str, data: dict[str, Any]) -> str:
    """transfer.success / transfer.failed / transfer.reversed webhooks."""
    payout = await db.scalar(
        select(PayoutRequest).where(PayoutRequest.transfer_reference == data.get("reference")).with_for_update()
    )
    if payout is None:
        return "ignored"  # not ours (e.g. another product on the same Paystack account)
    status = {"transfer.success": "success", "transfer.failed": "failed", "transfer.reversed": "reversed"}[event_type]
    await apply_transfer_result(db, payout, {**data, "status": status})
    return "processed"


async def check_processing(paystack: PaystackClient) -> dict[str, int]:
    """Worker job (every 10 minutes): asks Paystack about transfers still Processing, in
    case a webhook was missed. A payout Paystack never received is failed and released."""
    counts = {"checked": 0, "settled": 0}
    async with SessionLocal() as db:
        stale = (await db.scalars(
            select(PayoutRequest).where(
                PayoutRequest.status == "processing",
                PayoutRequest.reviewed_at < utcnow() - timedelta(minutes=CHECK_AFTER_MINUTES),
            ).limit(50).with_for_update(skip_locked=True)
        )).all()
        for payout in stale:
            counts["checked"] += 1
            payout.last_checked_at = utcnow()
            try:
                data = await paystack.verify_transfer(payout.transfer_reference)
            except AppError:
                continue
            if data is None:
                await _release(db, payout, "failed", "The transfer never reached Paystack. Request the payout again.")
                await _notify_rep(db, payout, "failed")
                counts["settled"] += 1
            elif await apply_transfer_result(db, payout, data):
                counts["settled"] += 1
        await db.commit()
    return counts
