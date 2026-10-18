"""Settlement (design sections 5 and 8): when Paystack has paid a payment out to Pritis's
bank, the rep's share moves from Pending to Available (debit rep pending, credit rep
available). Only Available money can be paid out.

Two ways in: the hourly check reads Paystack's settlements; if that data isn't available,
the accountant marks payments settled after checking the bank statement.
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.db import SessionLocal
from app.core.errors import AppError
from app.models import AdminUser, Class, ClassDue, Payment
from app.services import audit, ledger, refunds
from app.services.paystack import PaystackClient

log = logging.getLogger("app.settlements")

LOOKBACK_DAYS = 14  # how far back the hourly check reads Paystack's settlements


async def settle_payment(db: AsyncSession, payment: Payment, when: datetime, reference: str) -> bool:
    """Moves one payment's due amount from the rep's Pending to Available. The caller has
    locked the payment. Returns False if there was nothing to do (already settled)."""
    if payment.status != "success" or payment.settled_at is not None:
        return False
    if await refunds.processing_refund_exists(db, payment.id):
        return False  # its share is reserved from Pending for a refund; settles if that fails
    due = await db.get(ClassDue, payment.due_id)
    klass = await db.get(Class, due.class_id)
    rep_id = klass.rep_user_id
    amount = payment.base_kobo
    await ledger.post(
        db,
        [
            ledger.Line(ledger.rep_account("rep_pending", rep_id), "debit", amount),
            ledger.Line(ledger.rep_account("rep_available", rep_id), "credit", amount),
        ],
        ref_type="settlement", ref_id=payment.id, memo=f"Settled {payment.reference} ({reference})"[:200],
    )
    wallet = await ledger.locked_wallet(db, rep_id)
    wallet.pending_kobo -= amount
    wallet.available_kobo += amount
    wallet.version += 1
    payment.settled_at = when
    payment.settlement_reference = reference[:120]
    return True


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


async def check_paystack_settlements(paystack: PaystackClient) -> int:
    """The hourly job: settles every payment Paystack reports as paid out. Idempotent."""
    settled = 0
    for settlement in await paystack.list_settlements(utcnow() - timedelta(days=LOOKBACK_DAYS)):
        if str(settlement.get("status", "")).lower() != "success":
            continue
        when = _parse(settlement.get("settlement_date") or settlement.get("settled_at")) or utcnow()
        references = [
            t.get("reference") for t in await paystack.settlement_transactions(settlement["id"])
            if str(t.get("reference", "")).startswith("DPP-")  # ours only
        ]
        if not references:
            continue
        async with SessionLocal() as db:
            payments = (await db.scalars(
                select(Payment).where(Payment.reference.in_(references), Payment.settled_at.is_(None))
                .with_for_update(skip_locked=True)
            )).all()
            for payment in payments:
                if await settle_payment(db, payment, when, f"paystack:{settlement['id']}"):
                    settled += 1
            await db.commit()
    if settled:
        log.info("Paystack settlements: %d payment(s) moved to Available", settled)
    return settled


async def unsettled_payments(db: AsyncSession) -> list[tuple[Payment, Class, ClassDue]]:
    rows = await db.execute(
        select(Payment, Class, ClassDue)
        .join(ClassDue, ClassDue.id == Payment.due_id)
        .join(Class, Class.id == ClassDue.class_id)
        .where(Payment.status == "success", Payment.settled_at.is_(None))
        .order_by(Payment.paid_at)
    )
    return list(rows.all())


async def mark_settled(
    db: AsyncSession, payment_ids: list[uuid.UUID], bank_reference: str, actor: AdminUser, request: Request
) -> int:
    """The accountant's fallback, after checking the bank statement."""
    payments = (await db.scalars(
        select(Payment).where(Payment.id.in_(payment_ids)).with_for_update()
    )).all()
    if len(payments) != len(set(payment_ids)):
        raise AppError("not_found", "Some of those payments don't exist", 404)
    now = utcnow()
    done = []
    for payment in payments:
        if await settle_payment(db, payment, now, f"manual:{bank_reference}"):
            done.append(payment)
    if done:
        await audit.record(
            db, actor=actor, action="settlement.mark", entity="payment", entity_id=None,
            after={
                "bank_reference": bank_reference,
                "payments": [p.reference for p in done],
                "total_due_kobo": sum(p.base_kobo for p in done),
            },
            request=request,
        )
    return len(done)
