"""/api/admin/admin-users: staff accounts. Super admin only."""

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import SUPER_ADMIN, require_roles
from app.core.errors import AppError
from app.models import AdminUser
from app.routers.admin.auth import admin_out
from app.schemas.admin import AdminOut, StaffCreateIn, StaffInviteOut
from app.services import audit, sessions, staff

router = APIRouter(prefix="/admin-users", tags=["admin staff"])
super_admin_only = require_roles(SUPER_ADMIN)


async def _get_staff(db: AsyncSession, staff_id: uuid.UUID) -> AdminUser:
    admin = await db.get(AdminUser, staff_id, with_for_update=True)
    if admin is None:
        raise AppError("not_found", "Staff account not found", 404)
    return admin


@router.get("", response_model=list[AdminOut])
async def list_staff(
    _: AdminUser = Depends(super_admin_only), db: AsyncSession = Depends(get_session)
) -> list[AdminOut]:
    rows = await db.scalars(select(AdminUser).order_by(AdminUser.created_at))
    return [admin_out(a) for a in rows]


@router.post("", response_model=StaffInviteOut, status_code=201)
async def invite_staff(
    body: StaffCreateIn,
    request: Request,
    actor: AdminUser = Depends(super_admin_only),
    db: AsyncSession = Depends(get_session),
) -> StaffInviteOut:
    admin, link = await staff.create_staff(
        db, email=body.email, full_name=body.full_name, role=body.role, invited_by=actor
    )
    await audit.record(
        db, actor=actor, action="admin.invite", entity="admin_user", entity_id=admin.id,
        after={"email": admin.email, "full_name": admin.full_name, "role": admin.role},
        request=request,
    )
    await db.commit()
    return StaffInviteOut(admin=admin_out(admin), invite_url=link)


@router.post("/{staff_id}/resend-invite", response_model=StaffInviteOut)
async def resend_invite(
    staff_id: uuid.UUID,
    request: Request,
    actor: AdminUser = Depends(super_admin_only),
    db: AsyncSession = Depends(get_session),
) -> StaffInviteOut:
    admin = await _get_staff(db, staff_id)
    if admin.status != "invited":
        raise AppError("not_invited", "This person has already set up their account", 409)
    link = await staff.issue_invite(db, admin, actor)
    await audit.record(
        db, actor=actor, action="admin.invite_resent", entity="admin_user", entity_id=admin.id,
        request=request,
    )
    await db.commit()
    return StaffInviteOut(admin=admin_out(admin), invite_url=link)


@router.post("/{staff_id}/reset-authenticator", response_model=StaffInviteOut)
async def reset_authenticator(
    staff_id: uuid.UUID,
    request: Request,
    actor: AdminUser = Depends(super_admin_only),
    db: AsyncSession = Depends(get_session),
) -> StaffInviteOut:
    """Lost or replaced phone. Not for your own account: someone holding a stolen
    password could otherwise use it to take the account over (use the command line)."""
    if staff_id == actor.id:
        raise AppError(
            "cannot_reset_self",
            "You can't reset your own authenticator here. Ask another super admin, "
            "or run the reset command on the server.",
            409,
        )
    admin = await _get_staff(db, staff_id)
    before = admin.status
    link = await staff.reset_authenticator(db, admin, actor)
    await audit.record(
        db, actor=actor, action="admin.reset_authenticator", entity="admin_user",
        entity_id=admin.id, before={"status": before}, after={"status": admin.status},
        request=request,
    )
    await db.commit()
    return StaffInviteOut(admin=admin_out(admin), invite_url=link)


@router.post("/{staff_id}/disable", response_model=AdminOut)
async def disable_staff(
    staff_id: uuid.UUID,
    request: Request,
    actor: AdminUser = Depends(super_admin_only),
    db: AsyncSession = Depends(get_session),
) -> AdminOut:
    if staff_id == actor.id:
        raise AppError("cannot_disable_self", "You can't disable your own account", 409)
    admin = await _get_staff(db, staff_id)
    if admin.status != "disabled":
        before = admin.status
        admin.status = "disabled"
        await sessions.revoke_all(db, "admin", admin.id)  # signed out everywhere at once
        await audit.record(
            db, actor=actor, action="admin.disable", entity="admin_user", entity_id=admin.id,
            before={"status": before}, after={"status": "disabled"}, request=request,
        )
        await db.commit()
    return admin_out(admin)


@router.post("/{staff_id}/enable", response_model=AdminOut)
async def enable_staff(
    staff_id: uuid.UUID,
    request: Request,
    actor: AdminUser = Depends(super_admin_only),
    db: AsyncSession = Depends(get_session),
) -> AdminOut:
    admin = await _get_staff(db, staff_id)
    if admin.status == "disabled":
        # Someone who never finished their invitation goes back to waiting for it.
        admin.status = "active" if admin.password_hash else "invited"
        await audit.record(
            db, actor=actor, action="admin.enable", entity="admin_user", entity_id=admin.id,
            before={"status": "disabled"}, after={"status": admin.status}, request=request,
        )
        await db.commit()
    return admin_out(admin)
