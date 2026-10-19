"""Google sign-in through OpenID Connect (Authlib)."""

from typing import Any

from authlib.integrations.starlette_client import OAuth, StarletteOAuth2App
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.errors import AppError
from app.models import User

_oauth = OAuth()


def google_client() -> StarletteOAuth2App | None:
    """None until GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are set."""
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        return None
    client = _oauth.create_client("google")
    if client is None:
        _oauth.register(
            "google",
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
        client = _oauth.create_client("google")
    return client


async def find_or_create_google_user(db: AsyncSession, info: dict[str, Any]) -> User:
    """Matches by Google id, then links to an existing account with the same email.
    Google is for logging in only (owner's decision, 2026-10-19): accounts are created
    with the signup form, so an email with no account is refused. Only Google-verified
    emails are trusted."""
    sub = info.get("sub")
    email = (info.get("email") or "").strip().lower()
    if not sub or not email:
        raise AppError("google_failed", "Google did not return an email address", 400)

    user = await db.scalar(select(User).where(User.google_sub == sub))
    if user is None:
        if info.get("email_verified") is not True:
            raise AppError(
                "google_email_unverified",
                "Your Google email is not verified. Sign up with email and password instead.",
                400,
            )
        user = await db.scalar(select(User).where(User.email == email))
        if user is None:
            raise AppError(
                "google_no_account",
                "There's no account with this Google email yet. Create an account first, then you can log in with Google.",
                404,
            )
        if user.google_sub is not None:
            raise AppError(
                "google_account_conflict",
                "This email is already linked to a different Google account.",
                409,
            )
        user.google_sub = sub
        if user.email_verified_at is None:
            user.email_verified_at = utcnow()

    if user.status != "active":
        raise AppError("account_disabled", "This account has been disabled. Contact support.", 403)
    await db.flush()
    return user
