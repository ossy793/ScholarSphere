"""6-digit one-time codes sent by email: email verification, password reset, and confirming
a payout request (the code is tied to the amount, so it can't confirm a different one)."""

import hmac
from datetime import timedelta
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import hash_otp, new_otp
from app.models import User, UserToken
from app.services.email import queue_email

Purpose = Literal["verify_email", "reset_password", "confirm_payout"]


def _hash(user: User, code: str, context: str) -> str:
    return hash_otp(user.id, f"{code}|{context}" if context else code)


async def send_code(
    db: AsyncSession, user: User, purpose: Purpose, *, context: str = "", extra: dict | None = None
) -> None:
    """Issues a fresh code (cancelling earlier ones for the same purpose) and queues the email.
    The email template has the same name as the purpose. With a context (e.g. an amount),
    the code only works together with that same context."""
    settings = get_settings()
    now = utcnow()
    await db.execute(
        update(UserToken)
        .where(UserToken.user_id == user.id, UserToken.purpose == purpose, UserToken.used_at.is_(None))
        .values(used_at=now)
    )
    code = new_otp()
    db.add(
        UserToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=_hash(user, code, context),
            expires_at=now + timedelta(minutes=settings.email_code_minutes),
        )
    )
    await queue_email(
        db, user.email, purpose,
        {"full_name": user.full_name, "code": code, "minutes": settings.email_code_minutes, **(extra or {})},
    )


async def use_code(db: AsyncSession, user: User | None, purpose: Purpose, code: str, *, context: str = "") -> None:
    """Consumes the code or raises AppError. The caller commits in both cases, so a
    wrong guess is counted.

    For password reset the caller doesn't know whether the account exists, so
    pass user=None for an unknown email: it fails the same way as a wrong code.
    """
    settings = get_settings()
    now = utcnow()
    generic = purpose == "reset_password"  # don't reveal whether the email has an account
    expired = AppError(
        "code_expired",
        "This code is not correct or has expired. Request a new one."
        if generic else "This code has expired. Request a new one.",
        400,
    )
    token = None
    if user is not None:
        token = await db.scalar(
            select(UserToken)
            .where(UserToken.user_id == user.id, UserToken.purpose == purpose, UserToken.used_at.is_(None))
            .order_by(UserToken.created_at.desc())
            .limit(1)
            .with_for_update()
        )
    if token is None or token.expires_at <= now:
        raise expired
    if token.attempts >= settings.email_code_max_attempts:
        raise AppError("code_expired", "Too many wrong attempts. Request a new code.", 400)
    if not hmac.compare_digest(token.token_hash, _hash(user, code, context)):
        token.attempts += 1
        left = settings.email_code_max_attempts - token.attempts
        if left <= 0:
            raise AppError("code_expired", "Too many wrong attempts. Request a new code.", 400)
        raise AppError(
            "code_invalid",
            f"That code is not correct. {left} {'try' if left == 1 else 'tries'} left.",
            400,
        )
    token.used_at = now
