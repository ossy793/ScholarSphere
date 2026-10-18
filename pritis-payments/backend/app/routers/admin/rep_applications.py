"""/api/admin/rep-applications: verifying class reps.

Support, the accountant and the super admin. (The design document lists only support and
the super admin; the owner added the accountant on 2026-10-17.)
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import ACCOUNTANT, SUPER_ADMIN, SUPPORT, require_roles
from app.core.errors import AppError
from app.models import AdminUser, Class, RepApplication, RepBankAccount, User
from app.schemas.rep import (
    AdminRepApplicationOut,
    AdminRepApplicationPage,
    ApplicantOut,
    RejectIn,
)
from app.services import paystack, reps, storage

router = APIRouter(prefix="/rep-applications", tags=["admin reps"])
reviewers = require_roles(SUPPORT, ACCOUNTANT, SUPER_ADMIN)


def get_paystack() -> paystack.PaystackClient:
    return paystack.get_client()


async def _out(db: AsyncSession, application: RepApplication) -> AdminRepApplicationOut:
    user = await db.get(User, application.user_id)
    klass = await db.get(Class, application.class_id)
    account = await db.scalar(select(RepBankAccount).where(RepBankAccount.user_id == user.id))
    reviewer = await db.get(AdminUser, application.reviewed_by) if application.reviewed_by else None
    return AdminRepApplicationOut(
        id=application.id, status=application.status, created_at=application.created_at,
        applicant=ApplicantOut(
            id=user.id, full_name=user.full_name, email=user.email, phone=user.phone,
            matric_no=user.matric_no, email_verified=user.email_verified_at is not None,
        ),
        klass=reps.class_out(klass),
        bank_account=reps.bank_account_out(account),
        proof_url=f"/api/admin/rep-applications/{application.id}/proof",
        proof_content_type=application.proof_content_type,
        reviewed_by_email=reviewer.email if reviewer else None,
        reviewed_at=application.reviewed_at,
        reject_reason=application.reject_reason,
    )


@router.get("", response_model=AdminRepApplicationPage)
async def list_applications(
    status: Literal["pending", "approved", "rejected"] = "pending",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _: AdminUser = Depends(reviewers),
    db: AsyncSession = Depends(get_session),
) -> AdminRepApplicationPage:
    total = await db.scalar(
        select(func.count()).select_from(RepApplication).where(RepApplication.status == status)
    )
    # Oldest first while waiting (first come, first served); newest first once decided.
    order = RepApplication.created_at.asc() if status == "pending" else RepApplication.reviewed_at.desc()
    rows = await db.scalars(
        select(RepApplication).where(RepApplication.status == status)
        .order_by(order, RepApplication.id).offset((page - 1) * page_size).limit(page_size)
    )
    return AdminRepApplicationPage(
        items=[await _out(db, a) for a in rows], total=total or 0, page=page, page_size=page_size
    )


@router.get("/{application_id}/proof")
async def proof(
    application_id: uuid.UUID,
    _: AdminUser = Depends(reviewers),
    db: AsyncSession = Depends(get_session),
) -> Response:
    application = await db.get(RepApplication, application_id)
    if application is None:
        raise AppError("not_found", "Application not found", 404)
    data = await storage.get_storage().get(application.proof_file)
    headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
    if application.proof_content_type == "application/pdf":
        # Downloaded rather than shown: an uploaded PDF must never run inside the admin site.
        headers["Content-Disposition"] = f'attachment; filename="proof-{application.id}.pdf"'
    else:
        headers["Content-Disposition"] = "inline"
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self'"
    return Response(content=data, media_type=application.proof_content_type, headers=headers)


@router.post("/{application_id}/approve", response_model=AdminRepApplicationOut)
async def approve(
    application_id: uuid.UUID,
    request: Request,
    actor: AdminUser = Depends(reviewers),
    db: AsyncSession = Depends(get_session),
    ps: paystack.PaystackClient = Depends(get_paystack),
) -> AdminRepApplicationOut:
    application = await reps.approve(db, application_id, actor, request, ps)
    await db.commit()
    return await _out(db, application)


@router.post("/{application_id}/reject", response_model=AdminRepApplicationOut)
async def reject(
    application_id: uuid.UUID,
    body: RejectIn,
    request: Request,
    actor: AdminUser = Depends(reviewers),
    db: AsyncSession = Depends(get_session),
) -> AdminRepApplicationOut:
    application = await reps.reject(db, application_id, body.reason, actor, request)
    await db.commit()
    return await _out(db, application)
