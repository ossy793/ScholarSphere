"""/api/admin/refunds: support, the accountant or the super admin starts a refund; the
accountant or super admin approves (which sends it through Paystack) or rejects it
(design section 2)."""

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import ACCOUNTANT, SUPER_ADMIN, SUPPORT, require_roles
from app.models import AdminUser, Class, ClassDue, DueRevision, Payment, Refund, RepWallet, User
from app.schemas.payouts import ReasonIn
from app.schemas.refunds import AdminRefundOut, AdminRefundPage, RefundApproveIn, RefundIn, RefundStatus
from app.services import paystack, refunds

router = APIRouter(tags=["admin refunds"])
refund_starters = require_roles(SUPPORT, ACCOUNTANT, SUPER_ADMIN)
refund_approvers = require_roles(ACCOUNTANT, SUPER_ADMIN)


def get_paystack() -> paystack.PaystackClient:
    return paystack.get_client()


async def refund_out(db: AsyncSession, r: Refund) -> AdminRefundOut:
    payment = await db.get(Payment, r.payment_id)
    student = await db.get(User, payment.user_id)
    rep = await db.get(User, r.rep_user_id)
    due = await db.get(ClassDue, payment.due_id)
    klass = await db.get(Class, due.class_id)
    revision = await db.get(DueRevision, payment.revision_id)
    wallet = await db.scalar(select(RepWallet).where(RepWallet.user_id == rep.id))
    requester = await db.get(AdminUser, r.requested_by)
    reviewer = await db.get(AdminUser, r.reviewed_by) if r.reviewed_by else None
    fee = payment.paystack_fee_actual_kobo or 0
    return AdminRefundOut(
        id=r.id, status=r.status, payment_id=payment.id, payment_reference=payment.reference,
        receipt_no=payment.receipt_no, student_name=student.full_name, student_email=student.email,
        rep_name=rep.full_name, class_name=klass.name, due_title=revision.snapshot["title"],
        paid_at=payment.paid_at, settled=payment.settled_at is not None,
        total_kobo=payment.total_kobo, paystack_fee_kobo=fee,
        full_refund_kobo=payment.total_kobo, student_mistake_refund_kobo=payment.total_kobo - fee,
        amount_kobo=r.amount_kobo, fee_borne_kobo=r.fee_borne_kobo, mistake_by=r.mistake_by, reason=r.reason,
        rep_available_kobo=wallet.available_kobo if wallet else 0, reserved_kobo=r.reserved_kobo,
        requested_by_email=requester.email, reviewed_by_email=reviewer.email if reviewer else None,
        created_at=r.created_at, reviewed_at=r.reviewed_at, processed_at=r.processed_at,
        paystack_status=r.paystack_status, problem=r.reject_reason or r.failure_reason,
    )


@router.get("/refunds", response_model=AdminRefundPage)
async def list_refunds(
    status: RefundStatus = "requested",
    _: AdminUser = Depends(refund_starters), db: AsyncSession = Depends(get_session),
) -> AdminRefundPage:
    total = await db.scalar(select(func.count()).select_from(Refund).where(Refund.status == status))
    order = Refund.created_at.asc() if status == "requested" else Refund.created_at.desc()
    rows = (await db.scalars(select(Refund).where(Refund.status == status).order_by(order).limit(100))).all()
    return AdminRefundPage(items=[await refund_out(db, r) for r in rows], total=total or 0)


@router.post("/refunds", response_model=AdminRefundOut, status_code=201)
async def request_refund(
    body: RefundIn, request: Request,
    actor: AdminUser = Depends(refund_starters), db: AsyncSession = Depends(get_session),
) -> AdminRefundOut:
    refund = await refunds.request_refund(db, body.payment_id, body.reason, actor, request)
    await db.commit()
    return await refund_out(db, refund)


@router.post("/refunds/{refund_id}/approve", response_model=AdminRefundOut)
async def approve_refund(
    refund_id: uuid.UUID, body: RefundApproveIn, request: Request,
    actor: AdminUser = Depends(refund_approvers), db: AsyncSession = Depends(get_session),
    ps: paystack.PaystackClient = Depends(get_paystack),
) -> AdminRefundOut:
    """Sends the refund through Paystack. It's Processing until Paystack confirms it."""
    refund = await refunds.approve(db, refund_id, body.mistake_by, actor, request, ps)
    return await refund_out(db, refund)


@router.post("/refunds/{refund_id}/reject", response_model=AdminRefundOut)
async def reject_refund(
    refund_id: uuid.UUID, body: ReasonIn, request: Request,
    actor: AdminUser = Depends(refund_approvers), db: AsyncSession = Depends(get_session),
) -> AdminRefundOut:
    refund = await refunds.reject(db, refund_id, actor, body.reason, request)
    await db.commit()
    return await refund_out(db, refund)
