from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import AppError

router = APIRouter(tags=["health"])


@router.get("/api/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise AppError("database_unavailable", "Database is not reachable", 503) from exc
    return {"status": "ok", "database": "ok"}
