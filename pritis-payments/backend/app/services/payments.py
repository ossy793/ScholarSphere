"""Taking a payment (design section 8).

The browser never decides anything: a payment counts only when Paystack's signed webhook
or a server-side Verify Transaction says it succeeded, for exactly the amount and currency
we asked for. Applying that result is idempotent, so the webhook, the verify fallback and
a repeated webhook can all arrive in any order.
"""

import logging
import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.errors import AppError, field_error
from app.models import Class, ClassDue, ClassMembership, DueRevision, Payment, User, WebhookEvent
from app.services import app_settings, dues, fees, ledger, payouts, refunds
from app.services import paystack as paystack_service
from app.services.email import queue_email
from app.services.paystack import PaystackClient

log = logging.getLogger("app.payments")

REUSE_PENDING_MINUTES = 30  # pressing Pay again reuses the same open checkout
VERIFY_EVERY_SECONDS = 5  # how often a polling page may make us ask Paystack
MATRIC = re.compile(r"^[A-Za-z0-9/\-. ]{3,30}$")


class PaymentMismatch(Exception):
    """Paystack reported a different reference, amount or currency. Never credited."""


# --- Quotes -------------------------------------------------------------------------


async def quote(db: AsyncSession, due: ClassDue) -> fees.StudentQuote:
    settings = await app_settings.get_fee_settings(db)
    return fees.student_total(
        due.amount_kobo, Decimal(settings.paystack_fee_percent), Decimal(due.platform_fee_percent)
    )


async def member_due(db: AsyncSession, user: User, due_id: uuid.UUID, lock: bool = False) -> ClassDue:
    """A due of one of the student's classes, else 404."""
    query = (
        select(ClassDue)
        .join(ClassMembership, ClassMembership.class_id == ClassDue.class_id)
        .where(ClassDue.id == due_id, ClassMembership.user_id == user.id)
    )
    due = await db.scalar(query.with_for_update(of=ClassDue) if lock else query)
    if due is None:
        raise AppError("not_found", "Due not found", 404)
    return due


async def successful_payment(db: AsyncSession, user_id: uuid.UUID, due_id: uuid.UUID) -> Payment | None:
    # A refunded payment has status 'refunded', so the student can pay again.
    return await db.scalar(
        select(Payment).where(Payment.user_id == user_id, Payment.due_id == due_id, Payment.status == "success")
        .order_by(Payment.paid_at).limit(1)
    )


# --- Starting a payment -----------------------------------------------------------------


async def start_payment(
    db: AsyncSession, user: User, due_id: uuid.UUID, matric_no: str | None, paystack: PaystackClient
) -> Payment:
    """Creates a Pending payment from a quote the server computes itself, then asks
    Paystack for a checkout. Pressing Pay twice reuses the open checkout."""
    due = await member_due(db, user, due_id, lock=True)
    dues.ensure_payable(due)
    if await successful_payment(db, user.id, due.id):
        raise AppError("already_paid", "You've already paid this due", 409)

    matric = (matric_no or "").strip() or None
    if due.require_matric:
        if not matric:
            raise field_error("matric_no", "Your rep asks for your matric number", "missing")
        if not MATRIC.match(matric):
            raise field_error("matric_no", "Enter a valid matric number", "invalid")

    q = await quote(db, due)
    if matric and not user.matric_no:
        user.matric_no = matric  # so the rep's student list shows it too

    open_payment = await db.scalar(
        select(Payment).where(
            Payment.user_id == user.id, Payment.due_id == due.id, Payment.status == "pending",
            Payment.revision_id == due.live_revision_id, Payment.total_kobo == q.total_kobo,
            Payment.authorization_url.is_not(None),
            Payment.created_at > utcnow() - timedelta(minutes=REUSE_PENDING_MINUTES),
        ).order_by(Payment.created_at.desc()).limit(1)
    )
    if open_payment is not None:
        open_payment.matric_no = matric or open_payment.matric_no
        return open_payment

    payment = Payment(
        reference=f"DPP-{uuid.uuid4().hex[:20].upper()}",
        user_id=user.id, due_id=due.id, revision_id=due.live_revision_id,
        base_kobo=q.due_kobo, service_charge_kobo=q.service_charge_kobo,
        paystack_part_kobo=q.paystack_part_kobo, platform_part_kobo=q.platform_part_kobo,
        total_kobo=q.total_kobo, status="pending", matric_no=matric,
    )
    db.add(payment)
    await db.flush()

    klass = await db.get(Class, due.class_id)
    try:
        payment.authorization_url = await paystack.initialize_transaction(
            email=user.email, amount_kobo=q.total_kobo, reference=payment.reference,
            callback_url=f"{get_settings().app_base_url}/payment-callback.html?payment={payment.id}",
            metadata={
                "payment_id": str(payment.id), "due_id": str(due.id), "user_id": str(user.id),
                "custom_fields": [
                    {"display_name": "Due", "variable_name": "due", "value": due.title},
                    {"display_name": "Class", "variable_name": "class", "value": klass.name},
                ],
            },
        )
    except AppError as exc:
        payment.status = "failed"
        payment.failure_reason = exc.message
        raise
    return payment


# --- Applying Paystack's answer -----------------------------------------------------------


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


async def _next_receipt_no(db: AsyncSession) -> str:
    seq = await db.scalar(text("SELECT nextval('receipt_no_seq')"))
    return f"R{utcnow().year}-{seq:06d}"


async def apply_paystack_result(db: AsyncSession, payment: Payment, data: dict[str, Any]) -> bool:
    """Applies a charge.success / verify result to a payment the caller has locked.
    Returns True if this call marked it paid. Safe to call any number of times."""
    if payment.status == "success":
        return False  # already applied: a repeated webhook or verify changes nothing
    status = data.get("status")
    if status != "success":
        if status in ("failed", "reversed") and payment.status == "pending":
            payment.status = "failed"
            payment.failure_reason = str(data.get("gateway_response") or status)[:500]
        return False

    if (
        data.get("reference") != payment.reference
        or int(data.get("amount", -1)) != payment.total_kobo
        or str(data.get("currency", "")).upper() != payment.currency
    ):
        log.error(
            "Paystack result mismatch for %s: reference=%s amount=%s currency=%s (expected %s %s)",
            payment.reference, data.get("reference"), data.get("amount"), data.get("currency"),
            payment.total_kobo, payment.currency,
        )
        raise PaymentMismatch(f"Paystack reported {data.get('amount')} {data.get('currency')}")

    fee = int(data.get("fees") or 0)
    payment.status = "success"
    payment.paystack_fee_actual_kobo = fee
    payment.channel = (data.get("channel") or None) and str(data["channel"])[:30]
    payment.paystack_id = data.get("id")
    payment.paid_at = _parse_time(data.get("paid_at") or data.get("paidAt")) or utcnow()
    payment.receipt_no = await _next_receipt_no(db)

    due = await db.get(ClassDue, payment.due_id)
    klass = await db.get(Class, due.class_id)
    rep_id = klass.rep_user_id
    d, s = payment.base_kobo, payment.service_charge_kobo
    # Design section 5: debit Paystack clearing D+S-F; credit rep pending D; credit
    # platform revenue S-F (any shortfall comes out of the platform's share, never the rep's).
    await ledger.post(
        db,
        [
            ledger.Line("paystack_clearing", "debit", d + s - fee),
            ledger.Line(ledger.rep_account("rep_pending", rep_id), "credit", d),
            ledger.Line("platform_revenue", "credit", s - fee),
        ],
        ref_type="payment", ref_id=payment.id, memo=f"Payment {payment.reference}",
    )
    wallet = await ledger.locked_wallet(db, rep_id)
    wallet.pending_kobo += d
    wallet.version += 1

    await queue_receipt_email(db, payment)
    log.info("Payment %s succeeded (%s kobo, fee %s)", payment.reference, payment.total_kobo, fee)
    return True


async def queue_receipt_email(db: AsyncSession, payment: Payment) -> str:
    """Receipts are delivered by email only (owner's decision): the PDF is attached when
    the email is sent. Returns the address it goes to."""
    student = await db.get(User, payment.user_id)
    revision = await db.get(DueRevision, payment.revision_id)
    due = await db.get(ClassDue, payment.due_id)
    klass = await db.get(Class, due.class_id)
    total = payment.total_kobo
    await queue_email(
        db, student.email, "payment_receipt",
        {
            "full_name": student.full_name, "title": revision.snapshot["title"], "class_name": klass.name,
            "total": f"₦{total // 100:,}" + (f".{total % 100:02d}" if total % 100 else ""),
            "receipt_no": payment.receipt_no, "reference": payment.reference,
            "link": f"{get_settings().app_base_url}/dashboard.html",
        },
        attachments=[{"kind": "receipt", "payment_id": str(payment.id)}],
    )
    return student.email


async def verify_if_due(db: AsyncSession, payment: Payment, paystack: PaystackClient) -> None:
    """The verify fallback: if the webhook hasn't arrived, ask Paystack (at most every few
    seconds per payment, however often the page polls)."""
    now = utcnow()
    if payment.status != "pending":
        return
    if payment.last_checked_at and payment.last_checked_at > now - timedelta(seconds=VERIFY_EVERY_SECONDS):
        return
    payment.last_checked_at = now
    data = await paystack.verify_transaction(payment.reference)
    if data is not None:
        try:
            await apply_paystack_result(db, payment, data)
        except PaymentMismatch as exc:
            payment.status = "failed"
            payment.failure_reason = f"Mismatch, needs review: {exc}"


RECHECK_PENDING_HOURS = 24


async def recheck_pending_for_user(db: AsyncSession, user_id: uuid.UUID, due_id: uuid.UUID | None = None) -> None:
    """When a student opens their dashboard or a due, ask Paystack about their unfinished
    payments. Covers a student who paid but closed Paystack's page before being sent back
    (so the confirming page never ran) while no webhook reached us. Never fails the page."""
    try:
        paystack_client = paystack_service.get_client()
    except AppError:
        return  # Paystack not configured
    query = (
        select(Payment)
        .where(
            Payment.user_id == user_id, Payment.status == "pending",
            Payment.authorization_url.is_not(None),
            Payment.created_at > utcnow() - timedelta(hours=RECHECK_PENDING_HOURS),
        )
        .order_by(Payment.created_at.desc()).limit(3)
        .with_for_update(skip_locked=True)
    )
    if due_id is not None:
        query = query.where(Payment.due_id == due_id)
    for payment in (await db.scalars(query)).all():
        try:
            await verify_if_due(db, payment, paystack_client)
        except AppError:
            pass  # Paystack unreachable: the page still loads; the next visit retries


CHECK_AFTER_MINUTES = 10
ABANDON_AFTER_HOURS = 24


async def check_stale_pending(paystack: PaystackClient) -> dict[str, int]:
    """Worker job (every 5 minutes): asks Paystack about payments still Pending after 10
    minutes, in case the webhook and the student's return were both missed. After 24
    hours a payment Paystack hasn't completed is marked Abandoned."""
    now = utcnow()
    counts = {"checked": 0, "paid": 0, "abandoned": 0}
    async with SessionLocal() as db:
        stale = (await db.scalars(
            select(Payment).where(
                Payment.status == "pending",
                Payment.created_at < now - timedelta(minutes=CHECK_AFTER_MINUTES),
            ).order_by(Payment.created_at).limit(100).with_for_update(skip_locked=True)
        )).all()
        for payment in stale:
            counts["checked"] += 1
            payment.last_checked_at = None  # bypass the polling throttle
            try:
                await verify_if_due(db, payment, paystack)
            except AppError:
                continue  # Paystack unreachable; try again next run
            if payment.status == "success":
                counts["paid"] += 1
            elif payment.status == "pending" and payment.created_at < now - timedelta(hours=ABANDON_AFTER_HOURS):
                payment.status = "abandoned"
                payment.failure_reason = "Not completed on Paystack within 24 hours"
                counts["abandoned"] += 1
        await db.commit()
    return counts


async def retry_webhooks() -> int:
    """Worker job (every minute): processes stored events that weren't processed, e.g.
    because the server restarted right after storing them."""
    async with SessionLocal() as db:
        ids = (await db.scalars(
            select(WebhookEvent.id).where(
                WebhookEvent.status == "received",
                WebhookEvent.created_at < utcnow() - timedelta(seconds=30),
            ).order_by(WebhookEvent.created_at).limit(50)
        )).all()
    for event_id in ids:
        await process_event(event_id)
    return len(ids)


# --- Webhooks ---------------------------------------------------------------------------


def event_key(payload: dict[str, Any]) -> str:
    """Paystack sends no event id; the event name plus the transaction's (or transfer's, or
    refund's) id is unique. Refund events may carry only the transaction reference."""
    data = payload.get("data") or {}
    ident = (data.get("id") or data.get("refund_reference") or data.get("reference")
             or data.get("transfer_code") or data.get("transaction_reference"))
    return f"{payload.get('event')}:{ident}"[:200]


async def process_event(event_id: uuid.UUID) -> None:
    """Processes one stored event in its own transaction. On an unexpected error it stays
    'received' (with the error noted) so the retry job (phase 7) tries again."""
    async with SessionLocal() as db:
        event = await db.scalar(
            select(WebhookEvent).where(WebhookEvent.id == event_id, WebhookEvent.status == "received")
            .with_for_update(skip_locked=True)
        )
        if event is None:
            return
        try:
            event.status = await _dispatch(db, event)
            event.processed_at = utcnow()
            event.error = None
            await db.commit()
        except PaymentMismatch as exc:
            await db.rollback()
            await _mark(event_id, "failed", str(exc))
        except Exception as exc:
            await db.rollback()
            log.exception("Webhook event %s failed", event_id)
            await _mark(event_id, "received", repr(exc)[:500])


async def _mark(event_id: uuid.UUID, status: str, error: str) -> None:
    async with SessionLocal() as db:
        await db.execute(
            update(WebhookEvent).where(WebhookEvent.id == event_id).values(status=status, error=error)
        )
        await db.commit()


async def _dispatch(db: AsyncSession, event: WebhookEvent) -> str:
    if event.event_type == "charge.success":
        data = event.payload.get("data") or {}
        payment = await db.scalar(
            select(Payment).where(Payment.reference == data.get("reference")).with_for_update()
        )
        if payment is None:
            # Not ours: another product on the same Paystack account (e.g. pritis.name.ng).
            return "ignored"
        await apply_paystack_result(db, payment, data)
        return "processed"
    if event.event_type in ("transfer.success", "transfer.failed", "transfer.reversed"):
        return await payouts.apply_transfer_event(db, event.event_type, event.payload.get("data") or {})
    if event.event_type in ("refund.pending", "refund.processing", "refund.processed", "refund.failed"):
        return await refunds.apply_refund_event(db, event.event_type, event.payload.get("data") or {})
    return "ignored"
