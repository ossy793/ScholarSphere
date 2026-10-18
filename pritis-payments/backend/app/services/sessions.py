"""Refresh-token sessions for both sites, stored hashed in auth_sessions."""

import uuid
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.security import hash_token, new_token
from app.models import AuthSession


async def start_session(
    db: AsyncSession,
    subject_type: str,
    subject_id: uuid.UUID,
    user_agent: str | None,
    lifetime: timedelta,
) -> tuple[str, AuthSession]:
    """Returns the raw refresh token (for the cookie) and the stored session."""
    token = new_token()
    session = AuthSession(
        subject_type=subject_type,
        subject_id=subject_id,
        token_hash=hash_token(token),
        expires_at=utcnow() + lifetime,
        user_agent=(user_agent or "")[:512] or None,
    )
    db.add(session)
    await db.flush()
    return token, session


async def rotate_session(
    db: AsyncSession,
    subject_type: str,
    token: str,
    user_agent: str | None,
    lifetime: timedelta,
    keep_expiry: bool = False,
) -> tuple[str, AuthSession] | None:
    """Swaps a refresh token for a new one. Returns None if the token is no good.

    Presenting an already-rotated token means it was probably stolen, so every
    session of that account is ended. The caller must commit either way.
    keep_expiry=True (staff) keeps the original end time instead of extending it.
    """
    now = utcnow()
    session = await db.scalar(
        select(AuthSession)
        .where(AuthSession.token_hash == hash_token(token), AuthSession.subject_type == subject_type)
        .with_for_update()
    )
    if session is None:
        return None
    if session.revoked_at is not None:
        await revoke_all(db, subject_type, session.subject_id)
        return None
    if session.expires_at <= now:
        return None
    session.revoked_at = now
    token, new_session = await start_session(
        db, subject_type, session.subject_id, user_agent, lifetime
    )
    if keep_expiry:
        new_session.expires_at = session.expires_at
    return token, new_session


async def revoke_session(db: AsyncSession, subject_type: str, token: str) -> None:
    await db.execute(
        update(AuthSession)
        .where(
            AuthSession.token_hash == hash_token(token),
            AuthSession.subject_type == subject_type,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=utcnow())
    )


async def revoke_all(db: AsyncSession, subject_type: str, subject_id: uuid.UUID) -> None:
    await db.execute(
        update(AuthSession)
        .where(
            AuthSession.subject_type == subject_type,
            AuthSession.subject_id == subject_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=utcnow())
    )
