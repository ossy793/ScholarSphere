"""The audit log writer. Every staff action calls record() inside its own transaction,
so the log row is saved together with the change it describes."""

import uuid
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdminUser, AuditLog
from app.services.rate_limit import client_ip


async def record(
    db: AsyncSession,
    *,
    actor: AdminUser | None,
    action: str,
    entity: str,
    entity_id: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    request: Request | None = None,
) -> None:
    """actor=None means the system (or the command line) did it."""
    db.add(
        AuditLog(
            actor_type="admin" if actor else "system",
            actor_id=actor.id if actor else None,
            action=action,
            entity=entity,
            entity_id=entity_id,
            before=jsonable_encoder(before) if before is not None else None,
            after=jsonable_encoder(after) if after is not None else None,
            ip=client_ip(request) if request else None,
        )
    )
