"""Quotes, paying, payment status and emailing receipts (/api/user/...)."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import get_current_user, get_verified_user
from app.core.errors import AppError
from app.models import Class, ClassDue, DueRevision, Payment, User
from app.schemas.payments import EmailedOut, PaymentOut, PayIn, PayOut, QuoteOut
from app.services import payments, paystack, rate_limit

router = APIRouter(tags=["payments"])


def get_paystack() -> paystack.PaystackClient:
    return paystack.get_client()


async def _payment_out(db: AsyncSession, payment: Payment) -> PaymentOut:
    revision = await db.get(DueRevision, payment.revision_id)
    due = await db.get(ClassDue, payment.due_id)
    klass = await db.get(Class, due.class_id)
    return PaymentOut(
        id=payment.id, reference=payment.reference, status=payment.status, due_id=payment.due_id,
        due_title=revision.snapshot["title"], class_name=klass.name,
        total_kobo=payment.total_kobo, channel=payment.channel, paid_at=payment.paid_at,
        receipt_no=payment.receipt_no, created_at=payment.created_at,
    )


async def _own_payment(db: AsyncSession, user: User, payment_id: uuid.UUID, lock: bool = False) -> Payment:
    payment = await db.get(Payment, payment_id, with_for_update=lock)
    if payment is None or payment.user_id != user.id:
        raise AppError("not_found", "Payment not found", 404)
    return payment


@router.get("/dues/{due_id}/quote", response_model=QuoteOut)
async def quote(
    due_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
) -> QuoteOut:
    due = await payments.member_due(db, user, due_id)
    q = await payments.quote(db, due)
    return QuoteOut(total_kobo=q.total_kobo, require_matric=due.require_matric)


@router.post("/payments", response_model=PayOut, status_code=201)
async def pay(
    body: PayIn,
    user: User = Depends(get_verified_user),  # verified email and complete profile
    db: AsyncSession = Depends(get_session),
    ps: paystack.PaystackClient = Depends(get_paystack),
) -> PayOut:
    try:
        payment = await payments.start_payment(db, user, body.due_id, body.matric_no, ps)
    except AppError:
        await db.commit()  # keeps a failed attempt on record
        raise
    await db.commit()
    return PayOut(payment_id=payment.id, authorization_url=payment.authorization_url)


@router.get("/payments", response_model=list[PaymentOut])
async def history(
    include_unfinished: bool = Query(False),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> list[PaymentOut]:
    statuses = ("success", "refunded", "pending", "failed") if include_unfinished else ("success", "refunded")
    await payments.recheck_pending_for_user(db, user.id)
    await db.commit()
    rows = await db.scalars(
        select(Payment).where(Payment.user_id == user.id, Payment.status.in_(statuses))
        .order_by(Payment.created_at.desc()).limit(100)
    )
    return [await _payment_out(db, p) for p in rows]


@router.get("/payments/{payment_id}", response_model=PaymentOut)
async def status(
    payment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    ps: paystack.PaystackClient = Depends(get_paystack),
) -> PaymentOut:
    """Polled by the page Paystack sends the student back to. While still pending, the
    server asks Paystack itself (the verify fallback for a late or missing webhook)."""
    payment = await _own_payment(db, user, payment_id, lock=True)
    try:
        await payments.verify_if_due(db, payment, ps)
    except AppError:
        pass  # Paystack unreachable: just report the current status; the next poll retries
    await db.commit()
    return await _payment_out(db, payment)


@router.post("/payments/{payment_id}/email-receipt", response_model=EmailedOut)
async def email_receipt(
    payment_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
) -> EmailedOut:
    """Receipts are emailed, not downloaded from the site (owner's decision). This sends
    it again, e.g. if the first one went to spam."""
    payment = await _own_payment(db, user, payment_id)
    if payment.status != "success":
        raise AppError("no_receipt", "There's no receipt for this payment yet", 404)
    await rate_limit.enforce("email_receipt", str(user.id))
    sent_to = await payments.queue_receipt_email(db, payment)
    await db.commit()
    return EmailedOut(email=sent_to)
