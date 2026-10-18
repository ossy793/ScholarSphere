"""Refunds (design sections 5 and 8) through Paystack's Refund API.

Support, the accountant or the super admin requests one; the accountant or super admin
approves it and records whose mistake it was:
    Pritis's or the rep's -> the student gets the whole payment back; Pritis absorbs the
                             Paystack fee Paystack keeps (refund_fee_expense)
    the student's         -> the student gets the payment less that fee

Ledger, for a payment of due D + service charge S with Paystack fee F, refunding R:
    approve  : debit rep pending or available D        credit refund hold D
               (pending if the payment hasn't settled yet, else available, which can go
               negative: future settlements cover it before any new payout)
    processed: debit refund hold D, debit platform revenue S - F,
               debit refund fee expense R - D - (S - F)  credit Paystack clearing R
    failed   : the reverse of approve

A refund is sent once: approval commits Processing before Paystack is asked, and an unclear
answer is resolved by asking Paystack (the status check), never by asking for it again.
"""

import logging
import uuid
from datetime import timedelta
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.errors import AppError
from app.models import AdminUser, Class, ClassDue, DueRevision, Payment, Refund, User
from app.services import audit, ledger
from app.services.email import queue_email
from app.services.paystack import PaystackClient, RefundRefused
from app.services.payouts import naira

log = logging.getLogger("app.refunds")

OPEN = ("requested", "processing")
CHECK_AFTER_MINUTES = 10
GIVE_UP_AFTER_MINUTES = 60  # an approved refund Paystack has never heard of is failed and released


def refund_amount(payment: Payment, mistake_by: str) -> tuple[int, int]:
    """(what the student gets back, the Paystack fee Pritis absorbs)."""
    fee = payment.paystack_fee_actual_kobo or 0
    if mistake_by == "student":
        return payment.total_kobo - fee, 0
    return payment.total_kobo, fee


async def _rep_id(db: AsyncSession, payment: Payment) -> uuid.UUID:
    due = await db.get(ClassDue, payment.due_id)
    return (await db.get(Class, due.class_id)).rep_user_id


async def open_refund(db: AsyncSession, payment_id: uuid.UUID) -> Refund | None:
    return await db.scalar(select(Refund).where(Refund.payment_id == payment_id, Refund.status.in_(OPEN)))


async def processing_refund_exists(db: AsyncSession, payment_id: uuid.UUID) -> bool:
    """A payment with a refund being sent mustn't settle meanwhile (its share is reserved
    from Pending)."""
    return await db.scalar(
        select(Refund.id).where(Refund.payment_id == payment_id, Refund.status == "processing")
    ) is not None


# --- Emails -----------------------------------------------------------------------------------


async def _notify(db: AsyncSession, refund: Refund, kind: str) -> None:
    """Student and rep hear about each step (design section 11)."""
    payment = await db.get(Payment, refund.payment_id)
    student = await db.get(User, payment.user_id)
    rep = await db.get(User, refund.rep_user_id)
    title = (await db.get(DueRevision, payment.revision_id)).snapshot["title"]
    base = {"kind": kind, "title": title, "student_name": student.full_name,
            "amount": naira(refund.amount_kobo), "reason": refund.reject_reason or refund.failure_reason or ""}
    await queue_email(db, student.email, "refund_update", {
        **base, "full_name": student.full_name, "audience": "student",
        "link": f"{get_settings().app_base_url}/dashboard.html",
    })
    await queue_email(db, rep.email, "refund_update", {
        **base, "full_name": rep.full_name, "audience": "rep", "share": naira(refund.reserved_kobo or payment.base_kobo),
        "link": f"{get_settings().app_base_url}/rep.html",
    })


async def _staff_emails(db: AsyncSession) -> list[str]:
    return list((await db.scalars(select(AdminUser.email).where(
        AdminUser.status == "active", AdminUser.role.in_(("accountant", "super_admin"))
    ))).all())


async def _tell_approver(db: AsyncSession, refund: Refund) -> None:
    """A failed refund: the approver is told (design section 8)."""
    approver = await db.get(AdminUser, refund.reviewed_by) if refund.reviewed_by else None
    payment = await db.get(Payment, refund.payment_id)
    for email in [approver.email] if approver else await _staff_emails(db):
        await queue_email(db, email, "refund_staff", {
            "kind": "failed", "reference": payment.reference, "amount": naira(refund.amount_kobo),
            "reason": refund.failure_reason or "", "requested_by": "",
            "link": f"{get_settings().admin_base_url}/refunds.html",
        })


# --- Requesting -------------------------------------------------------------------------------


async def request_refund(
    db: AsyncSession, payment_id: uuid.UUID, reason: str, actor: AdminUser, request: Request | None
) -> Refund:
    payment = await db.get(Payment, payment_id, with_for_update=True)
    if payment is None:
        raise AppError("not_found", "Payment not found", 404)
    if payment.status != "success":
        raise AppError("not_refundable", "Only successful payments that haven't been refunded can be refunded.", 409)
    if await open_refund(db, payment.id):
        raise AppError("refund_open", "This payment already has a refund waiting or in progress.", 409)
    refund = Refund(
        payment_id=payment.id, rep_user_id=await _rep_id(db, payment),
        amount_kobo=payment.total_kobo, reason=reason, status="requested", requested_by=actor.id,
        reference=f"dpp-refund-{uuid.uuid4().hex}",
    )
    db.add(refund)
    await db.flush()
    await audit.record(db, actor=actor, action="refund.request", entity="refund", entity_id=refund.id,
                       after={"payment": payment.reference, "total_kobo": payment.total_kobo, "reason": reason},
                       request=request)
    for email in await _staff_emails(db):
        await queue_email(db, email, "refund_staff", {
            "kind": "requested", "reference": payment.reference, "amount": naira(payment.total_kobo),
            "reason": reason, "requested_by": actor.full_name or actor.email,
            "link": f"{get_settings().admin_base_url}/refunds.html",
        })
    await _notify(db, refund, "requested")
    return refund


# --- Reserving and releasing the rep's share ------------------------------------------------------


async def _reserve(db: AsyncSession, refund: Refund, payment: Payment) -> None:
    source = "available" if payment.settled_at is not None else "pending"
    amount = payment.base_kobo
    await ledger.post(db, [
        ledger.Line(ledger.rep_account(f"rep_{source}", refund.rep_user_id), "debit", amount),
        ledger.Line(ledger.rep_account("refund_hold", refund.rep_user_id), "credit", amount),
    ], ref_type="refund_reserve", ref_id=refund.id, memo=f"Refund reserved for {payment.reference}")
    wallet = await ledger.locked_wallet(db, refund.rep_user_id)
    setattr(wallet, f"{source}_kobo", getattr(wallet, f"{source}_kobo") - amount)
    wallet.refund_held_kobo += amount
    wallet.version += 1
    refund.reserved_kobo = amount
    refund.reserved_from = source


async def _release(db: AsyncSession, refund: Refund, reason: str) -> None:
    """Paystack refused or failed the refund: the rep's share goes back where it came from."""
    if refund.reserved_kobo:
        source = refund.reserved_from
        await ledger.post(db, [
            ledger.Line(ledger.rep_account("refund_hold", refund.rep_user_id), "debit", refund.reserved_kobo),
            ledger.Line(ledger.rep_account(f"rep_{source}", refund.rep_user_id), "credit", refund.reserved_kobo),
        ], ref_type="refund_release", ref_id=refund.id, memo="Refund failed; reservation released")
        wallet = await ledger.locked_wallet(db, refund.rep_user_id)
        setattr(wallet, f"{source}_kobo", getattr(wallet, f"{source}_kobo") + refund.reserved_kobo)
        wallet.refund_held_kobo -= refund.reserved_kobo
        wallet.version += 1
    refund.status = "failed"
    refund.failure_reason = reason[:1000]


async def _mark_processed(db: AsyncSession, refund: Refund) -> None:
    payment = await db.get(Payment, refund.payment_id, with_for_update=True)
    d, s, f = payment.base_kobo, payment.service_charge_kobo, payment.paystack_fee_actual_kobo or 0
    r = refund.amount_kobo
    await ledger.post(db, [
        ledger.Line(ledger.rep_account("refund_hold", refund.rep_user_id), "debit", refund.reserved_kobo),
        ledger.Line("platform_revenue", "debit", s - f),
        ledger.Line("refund_fee_expense", "debit", r - d - (s - f)),
        ledger.Line("paystack_clearing", "credit", r),
    ], ref_type="refund_processed", ref_id=refund.id, memo=f"Refund of {payment.reference}")
    wallet = await ledger.locked_wallet(db, refund.rep_user_id)
    wallet.refund_held_kobo -= refund.reserved_kobo
    wallet.version += 1
    now = utcnow()
    refund.status = "processed"
    refund.processed_at = now
    payment.status = "refunded"
    payment.refunded_at = now


async def apply_refund_result(db: AsyncSession, refund: Refund, data: dict[str, Any]) -> bool:
    """Applies Paystack's refund status to a refund the caller has locked. Idempotent:
    a repeated webhook or check changes nothing. Returns True if it finished."""
    if refund.status != "processing":
        return False
    if data.get("id") and not refund.paystack_refund_id:
        refund.paystack_refund_id = str(data["id"])[:40]
    status = str(data.get("status", "")).lower()
    refund.paystack_status = status[:30] or refund.paystack_status
    if status == "processed":
        await _mark_processed(db, refund)
        await _notify(db, refund, "processed")
        return True
    if status == "failed":
        await _release(db, refund, str(data.get("reason") or data.get("message") or "Paystack couldn't complete the refund"))
        await _tell_approver(db, refund)
        await _notify(db, refund, "failed")
        return True
    return False  # pending / processing / needs-attention: still on its way


# --- Staff decisions ----------------------------------------------------------------------------


async def _locked_requested(db: AsyncSession, refund_id: uuid.UUID) -> Refund:
    refund = await db.get(Refund, refund_id, with_for_update=True)
    if refund is None:
        raise AppError("not_found", "Refund not found", 404)
    if refund.status != "requested":
        raise AppError("already_decided", "This refund has already been decided", 409)
    return refund


async def approve(
    db: AsyncSession, refund_id: uuid.UUID, mistake_by: str, actor: AdminUser, request: Request | None,
    paystack: PaystackClient,
) -> Refund:
    """Reserves the rep's share, marks the refund Processing (committed, so it can't be
    approved twice), then asks Paystack to send it."""
    refund = await _locked_requested(db, refund_id)
    payment = await db.get(Payment, refund.payment_id, with_for_update=True)
    if payment.status != "success":
        raise AppError("not_refundable", "This payment can no longer be refunded.", 409)
    amount, fee_borne = refund_amount(payment, mistake_by)
    if amount <= 0:
        raise AppError("refund_too_small", "Nothing would be left to refund after Paystack's fee.", 409)
    balance = await paystack.get_balance()
    if balance < amount:
        raise AppError(
            "insufficient_paystack_balance",
            f"The Paystack balance is {naira(balance)}, but this refund needs {naira(amount)}. Top it up first.",
            409,
        )
    refund.amount_kobo = amount
    refund.fee_borne_kobo = fee_borne
    refund.mistake_by = mistake_by
    await _reserve(db, refund, payment)
    refund.status = "processing"
    refund.reviewed_by = actor.id
    refund.reviewed_at = utcnow()
    await audit.record(db, actor=actor, action="refund.approve", entity="refund", entity_id=refund.id,
                       after={"payment": payment.reference, "amount_kobo": amount, "mistake_by": mistake_by,
                              "fee_borne_kobo": fee_borne, "reserved_from": refund.reserved_from,
                              "paystack_balance_kobo": balance}, request=request)
    await db.commit()

    try:
        data = await paystack.create_refund(
            transaction_reference=payment.reference, amount_kobo=amount, merchant_note=refund.reference,
        )
    except RefundRefused as exc:
        refund = await db.get(Refund, refund.id, with_for_update=True, populate_existing=True)
        await _release(db, refund, f"Paystack refused the refund: {exc}")
        await _notify(db, refund, "failed")
        await db.commit()
        return refund
    except AppError:
        log.warning("Refund %s unclear; left processing for the status check", refund.reference)
        return refund

    refund = await db.get(Refund, refund.id, with_for_update=True, populate_existing=True)
    if not await apply_refund_result(db, refund, data):
        await _notify(db, refund, "approved")
    await db.commit()
    return refund


async def reject(db: AsyncSession, refund_id: uuid.UUID, actor: AdminUser, reason: str, request: Request | None) -> Refund:
    refund = await _locked_requested(db, refund_id)
    refund.status = "rejected"
    refund.reviewed_by = actor.id
    refund.reviewed_at = utcnow()
    refund.reject_reason = reason
    await audit.record(db, actor=actor, action="refund.reject", entity="refund", entity_id=refund.id,
                       after={"reason": reason}, request=request)
    await _notify(db, refund, "rejected")
    return refund


# --- Paystack telling us how it went ----------------------------------------------------------------


async def apply_refund_event(db: AsyncSession, event_type: str, data: dict[str, Any]) -> str:
    """refund.pending / refund.processing / refund.processed / refund.failed webhooks.
    Paystack names the transaction, not our refund, so we find the refund being sent for it."""
    reference = data.get("transaction_reference") or (data.get("transaction") or {}).get("reference")
    payment = await db.scalar(select(Payment).where(Payment.reference == reference)) if reference else None
    if payment is None:
        return "ignored"  # not ours (e.g. another product on the same Paystack account)
    refund = await db.scalar(
        select(Refund).where(Refund.payment_id == payment.id, Refund.status == "processing").with_for_update()
    )
    if refund is None:
        return "ignored"  # already finished (a repeated event) or never approved here
    status = event_type.removeprefix("refund.")
    await apply_refund_result(db, refund, {**data, "status": status, "id": data.get("id") or refund.paystack_refund_id})
    return "processed"


async def check_processing(paystack: PaystackClient) -> dict[str, int]:
    """Worker job (with the transfer check, every 10 minutes): asks Paystack about refunds
    still Processing, in case a webhook was missed."""
    counts = {"checked": 0, "finished": 0}
    now = utcnow()
    async with SessionLocal() as db:
        stale = (await db.scalars(
            select(Refund).where(
                Refund.status == "processing", Refund.reviewed_at < now - timedelta(minutes=CHECK_AFTER_MINUTES),
            ).limit(50).with_for_update(skip_locked=True)
        )).all()
        for refund in stale:
            counts["checked"] += 1
            refund.last_checked_at = now
            payment = await db.get(Payment, refund.payment_id)
            try:
                if refund.paystack_refund_id:
                    data = await paystack.fetch_refund(refund.paystack_refund_id)
                else:
                    # The answer to our request was lost: find it among the transaction's refunds.
                    found = [r for r in await paystack.list_refunds(payment.paystack_id or payment.reference)
                             if int(r.get("amount") or 0) == refund.amount_kobo
                             and str(r.get("status", "")).lower() != "failed"]
                    data = found[0] if found else None
            except AppError:
                continue
            if data is None:
                if refund.reviewed_at < now - timedelta(minutes=GIVE_UP_AFTER_MINUTES):
                    await _release(db, refund, "The refund never reached Paystack. It can be requested again.")
                    await _tell_approver(db, refund)
                    await _notify(db, refund, "failed")
                    counts["finished"] += 1
            elif await apply_refund_result(db, refund, data):
                counts["finished"] += 1
        await db.commit()
    return counts
