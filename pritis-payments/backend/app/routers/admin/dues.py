"""/api/admin/dues: the approval queue for dues and their revisions.
The super admin and the accountant (design section 2)."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import ACCOUNTANT, SUPER_ADMIN, require_roles
from app.models import AdminUser, Class, ClassDue, DueRevision, User
from app.schemas.dues import (
    AdminDueRevisionOut,
    AdminDueRevisionPage,
    ApproveDueIn,
    RejectDueIn,
)
from app.services import app_settings, dues

router = APIRouter(prefix="/dues", tags=["admin dues"])
approvers = require_roles(SUPER_ADMIN, ACCOUNTANT)


async def _out(db: AsyncSession, rev: DueRevision, max_kobo: int, default_fee) -> AdminDueRevisionOut:
    due = await db.get(ClassDue, rev.due_id)
    klass = await db.get(Class, due.class_id)
    rep = await db.get(User, klass.rep_user_id)
    reviewer = await db.get(AdminUser, rev.reviewed_by) if rev.reviewed_by else None
    proposed = dues.terms_from_snapshot(rev.snapshot)
    # An edit is a revision made after an earlier version was already approved.
    is_edit = bool(await db.scalar(
        select(func.count()).select_from(DueRevision)
        .where(DueRevision.due_id == due.id, DueRevision.status == "approved",
               DueRevision.version < rev.version)
    ))
    show_current = rev.status == "pending" and is_edit
    return AdminDueRevisionOut(
        due_id=due.id, revision_version=rev.version, kind="edit" if is_edit else "new",
        status=rev.status, class_name=klass.name, school=klass.school,
        rep_name=rep.full_name, rep_email=rep.email,
        proposed=proposed,
        current=dues.live_terms(due) if show_current else None,
        current_fee_percent=due.platform_fee_percent if due.live_revision_id else None,
        suggested_fee_percent=due.platform_fee_percent if due.live_revision_id else default_fee,
        needs_super_admin=proposed.amount_kobo > max_kobo,
        submitted_at=rev.submitted_at, reviewed_at=rev.reviewed_at,
        reviewed_by_email=reviewer.email if reviewer else None,
        reject_reason=rev.reject_reason, approved_fee_percent=rev.platform_fee_percent,
    )


@router.get("", response_model=AdminDueRevisionPage)
async def queue(
    status: Literal["pending", "approved", "rejected"] = "pending",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _: AdminUser = Depends(approvers),
    db: AsyncSession = Depends(get_session),
) -> AdminDueRevisionPage:
    """Dues and revisions by review status. Pending: oldest first."""
    fees = await app_settings.get_fee_settings(db)
    total = await db.scalar(
        select(func.count()).select_from(DueRevision).where(DueRevision.status == status)
    )
    order = DueRevision.submitted_at.asc() if status == "pending" else DueRevision.reviewed_at.desc()
    rows = await db.scalars(
        select(DueRevision).where(DueRevision.status == status)
        .order_by(order, DueRevision.id).offset((page - 1) * page_size).limit(page_size)
    )
    items = [await _out(db, r, fees.max_due_amount_kobo, fees.platform_fee_percent) for r in rows]
    return AdminDueRevisionPage(
        items=items, total=total or 0, page=page, page_size=page_size,
        max_due_amount_kobo=fees.max_due_amount_kobo,
    )


@router.post("/{due_id}/approve", response_model=AdminDueRevisionOut)
async def approve(
    due_id: uuid.UUID, body: ApproveDueIn, request: Request,
    actor: AdminUser = Depends(approvers), db: AsyncSession = Depends(get_session),
) -> AdminDueRevisionOut:
    """Approves what's waiting on this due (a new due or an edit), optionally with a
    different platform fee. Above the maximum amount only the super admin may approve."""
    due = await dues.approve(db, due_id, actor, body.platform_fee_percent, request)
    await db.commit()
    rev = await db.get(DueRevision, due.live_revision_id)
    fees = await app_settings.get_fee_settings(db)
    return await _out(db, rev, fees.max_due_amount_kobo, fees.platform_fee_percent)


@router.post("/{due_id}/reject", response_model=AdminDueRevisionOut)
async def reject(
    due_id: uuid.UUID, body: RejectDueIn, request: Request,
    actor: AdminUser = Depends(approvers), db: AsyncSession = Depends(get_session),
) -> AdminDueRevisionOut:
    await dues.reject(db, due_id, actor, body.reason, request)
    await db.commit()
    rev = await dues.latest_revision(db, due_id)
    fees = await app_settings.get_fee_settings(db)
    return await _out(db, rev, fees.max_due_amount_kobo, fees.platform_fee_percent)
