"""6-digit one-time codes sent by email: email verification and password reset."""

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

Purpose = Literal["verify_email", "reset_password"]


async def send_code(db: AsyncSession, user: User, purpose: Purpose) -> None:
    """Issues a fresh code (cancelling earlier ones for the same purpose) and queues the email.
    The email template has the same name as the purpose."""
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
            token_hash=hash_otp(user.id, code),
            expires_at=now + timedelta(minutes=settings.email_code_minutes),
        )
    )
    await queue_email(
        db, user.email, purpose,
        {"full_name": user.full_name, "code": code, "minutes": settings.email_code_minutes},
    )


async def use_code(db: AsyncSession, user: User | None, purpose: Purpose, code: str) -> None:
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
    if not hmac.compare_digest(token.token_hash, hash_otp(user.id, code)):
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
