"""/api/admin/users and /api/admin/payments: search for support (design section 2:
support and super admin; the accountant read-only). Nothing here changes anything."""

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import ACCOUNTANT, SUPER_ADMIN, SUPPORT, require_roles
from app.core.errors import AppError
from app.models import AdminUser, Class, ClassDue, ClassMembership, Payment, Refund, RepApplication, University, User
from app.services import reps

router = APIRouter(tags=["admin search"])
search_staff = require_roles(SUPPORT, ACCOUNTANT, SUPER_ADMIN)


class UserRow(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str
    phone: str | None
    matric_no: str | None
    university: str | None
    level: str | None
    email_verified: bool
    status: str
    created_at: datetime
    is_rep: bool = False  # a verified rep


class PaymentRow(BaseModel):
    id: uuid.UUID
    reference: str
    receipt_no: str | None
    status: str
    student_name: str
    student_email: str
    class_name: str
    due_title: str
    total_kobo: int
    due_kobo: int
    service_charge_kobo: int
    paystack_fee_kobo: int | None
    channel: str | None
    created_at: datetime
    paid_at: datetime | None
    settled_at: datetime | None
    failure_reason: str | None
    refund_status: str | None  # the latest refund's status, if any


class UserDetail(UserRow):
    rep_status: str
    rep_class: str | None
    classes: list[str]
    payments: list[PaymentRow]


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


async def _user_row(db: AsyncSession, u: User, is_rep: bool | None = None) -> UserRow:
    university = await db.get(University, u.university_id) if u.university_id else None
    if is_rep is None:
        is_rep = await reps.is_verified_rep(db, u.id)
    return UserRow(
        id=u.id, full_name=u.full_name, email=u.email, phone=u.phone, matric_no=u.matric_no,
        university=university.name if university else None, level=u.level,
        email_verified=u.email_verified_at is not None, status=u.status, created_at=u.created_at,
        is_rep=is_rep,
    )


def _payments_query():
    latest_refund = (
        select(Refund.status).where(Refund.payment_id == Payment.id)
        .order_by(Refund.created_at.desc()).limit(1).correlate(Payment).scalar_subquery()
    )
    return (
        select(Payment, User, Class.name, ClassDue.title, latest_refund)
        .join(User, User.id == Payment.user_id)
        .join(ClassDue, ClassDue.id == Payment.due_id).join(Class, Class.id == ClassDue.class_id)
    )


def _payment_row(p: Payment, u: User, class_name: str, title: str, refund_status: str | None) -> PaymentRow:
    return PaymentRow(
        id=p.id, reference=p.reference, receipt_no=p.receipt_no, status=p.status,
        student_name=u.full_name, student_email=u.email, class_name=class_name, due_title=title,
        total_kobo=p.total_kobo, due_kobo=p.base_kobo, service_charge_kobo=p.service_charge_kobo,
        paystack_fee_kobo=p.paystack_fee_actual_kobo, channel=p.channel, created_at=p.created_at,
        paid_at=p.paid_at, settled_at=p.settled_at, failure_reason=p.failure_reason,
        refund_status=refund_status,
    )


@router.get("/users", response_model=list[UserRow])
async def search_users(
    q: str | None = Query(None, min_length=2, max_length=100, description="name, email, phone or matric number"),
    kind: Literal["all", "students", "reps"] = "all",
    offset: int = Query(0, ge=0, le=100_000),
    limit: int = Query(50, ge=1, le=200),
    _: AdminUser = Depends(search_staff), db: AsyncSession = Depends(get_session),
) -> list[UserRow]:
    """Everyone, newest first, or those matching q (by name). Filter to students or reps."""
    is_rep = exists().where(RepApplication.user_id == User.id, RepApplication.status == "approved")
    query = select(User, is_rep.label("is_rep"))
    if q:
        pattern = _like(q.strip())
        # Phones are stored as +234803…; staff type 0803… or 803… or 234803….
        digits = "".join(ch for ch in q if ch.isdigit())
        if digits.startswith("234"):
            digits = digits[3:]
        elif digits.startswith("0"):
            digits = digits[1:]
        conditions = [User.full_name.ilike(pattern), User.email.ilike(pattern), User.matric_no.ilike(pattern)]
        if len(digits) >= 4:
            conditions.append(User.phone.like(f"%{digits}%"))
        query = query.where(or_(*conditions)).order_by(User.full_name, User.id)
    else:
        query = query.order_by(User.created_at.desc(), User.id)
    if kind == "reps":
        query = query.where(is_rep)
    elif kind == "students":
        query = query.where(~is_rep)
    rows = (await db.execute(query.offset(offset).limit(limit))).all()
    return [await _user_row(db, u, rep) for u, rep in rows]


@router.get("/users/{user_id}", response_model=UserDetail)
async def user_detail(
    user_id: uuid.UUID, _: AdminUser = Depends(search_staff), db: AsyncSession = Depends(get_session)
) -> UserDetail:
    user = await db.get(User, user_id)
    if user is None:
        raise AppError("not_found", "User not found", 404)
    classes = (await db.scalars(
        select(Class.name).join(ClassMembership, ClassMembership.class_id == Class.id)
        .where(ClassMembership.user_id == user.id).order_by(Class.name)
    )).all()
    rep_class = await reps.verified_rep_class(db, user.id)
    payment_rows = (await db.execute(
        _payments_query().where(Payment.user_id == user.id).order_by(Payment.created_at.desc()).limit(100)
    )).all()
    return UserDetail(
        **(await _user_row(db, user)).model_dump(),
        rep_status=await reps.rep_status(db, user.id), rep_class=rep_class.name if rep_class else None,
        classes=list(classes), payments=[_payment_row(*r) for r in payment_rows],
    )


@router.get("/payments", response_model=list[PaymentRow])
async def search_payments(
    q: str | None = Query(None, max_length=100, description="reference, receipt number, student email or name"),
    status: Literal["pending", "success", "failed", "abandoned", "refunded"] | None = None,
    _: AdminUser = Depends(search_staff), db: AsyncSession = Depends(get_session),
) -> list[PaymentRow]:
    query = _payments_query()
    if q and q.strip():
        pattern = _like(q.strip())
        query = query.where(or_(Payment.reference.ilike(pattern), Payment.receipt_no.ilike(pattern),
                                User.email.ilike(pattern), User.full_name.ilike(pattern)))
    if status:
        query = query.where(Payment.status == status)
    rows = (await db.execute(query.order_by(Payment.created_at.desc()).limit(100))).all()
    return [_payment_row(*r) for r in rows]
