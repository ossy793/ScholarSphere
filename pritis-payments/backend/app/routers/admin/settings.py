"""/api/admin/settings: fee rules and limits. Super admin only (design section 2)."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import SUPER_ADMIN, require_roles
from app.models import AdminUser
from app.schemas.settings import FeeSettings
from app.services import app_settings

router = APIRouter(prefix="/settings", tags=["admin settings"])
super_admin_only = require_roles(SUPER_ADMIN)


@router.get("", response_model=FeeSettings)
async def get_settings(
    _: AdminUser = Depends(super_admin_only), db: AsyncSession = Depends(get_session)
) -> FeeSettings:
    return await app_settings.get_fee_settings(db)


@router.put("", response_model=FeeSettings)
async def update_settings(
    body: FeeSettings,
    request: Request,
    actor: AdminUser = Depends(super_admin_only),
    db: AsyncSession = Depends(get_session),
) -> FeeSettings:
    saved = await app_settings.update_fee_settings(db, body, actor, request)
    await db.commit()
    return saved
