"""/api/admin/audit-logs: who did what. Super admin only."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import SUPER_ADMIN, require_roles
from app.models import AdminUser, AuditLog
from app.schemas.admin import AuditLogOut, AuditLogPage

router = APIRouter(prefix="/audit-logs", tags=["admin audit"])


@router.get("", response_model=AuditLogPage)
async def list_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    action: str | None = Query(None, max_length=100),
    _: AdminUser = Depends(require_roles(SUPER_ADMIN)),
    db: AsyncSession = Depends(get_session),
) -> AuditLogPage:
    filters = [AuditLog.action == action] if action else []
    total = await db.scalar(select(func.count()).select_from(AuditLog).where(*filters))
    rows = await db.execute(
        select(AuditLog, AdminUser.email)
        .outerjoin(
            AdminUser, (AuditLog.actor_type == "admin") & (AdminUser.id == AuditLog.actor_id)
        )
        .where(*filters)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [
        AuditLogOut(
            id=log.id, created_at=log.created_at, actor_type=log.actor_type, actor_email=email,
            action=log.action, entity=log.entity, entity_id=log.entity_id,
            before=log.before, after=log.after, ip=log.ip,
        )
        for log, email in rows
    ]
    return AuditLogPage(items=items, total=total or 0, page=page, page_size=page_size)
