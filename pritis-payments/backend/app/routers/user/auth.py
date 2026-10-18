"""/api/user/auth/*: signup, email code, login, Google, refresh, logout."""

import logging
from datetime import timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.cookies import (
    CSRF_COOKIE,
    USER_COOKIES,
    clear_session_cookies,
    set_csrf_cookie,
    set_session_cookies,
)
from app.core.db import get_session
from app.core.deps import get_current_user
from app.core.errors import AppError, error_response
from app.core.security import (
    create_access_token,
    hash_password,
    new_token,
    password_needs_rehash,
    verify_password,
)
from app.models import User
from app.schemas.auth import (
    CsrfOut,
    ForgotPasswordIn,
    LoginIn,
    OkOut,
    ResetPasswordIn,
    SignupIn,
    VerifyEmailIn,
)
from app.schemas.user import UserOut
from app.services import codes, google, rate_limit, sessions
from app.services.rate_limit import limit_by_ip
from app.services.users import build_user_out, get_active_university, profile_complete

log = logging.getLogger("app.auth")

router = APIRouter(prefix="/auth", tags=["user auth"])

DEFAULT_LANDING = "/dashboard.html"


def _refresh_lifetime() -> timedelta:
    return timedelta(days=get_settings().user_refresh_days)


def safe_next(value: str | None) -> str:
    """Only same-site paths, so a crafted link can't bounce users to another site."""
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return DEFAULT_LANDING


async def _log_in(db: AsyncSession, request: Request, response: Response, user: User) -> None:
    lifetime = _refresh_lifetime()
    refresh, session = await sessions.start_session(
        db, "user", user.id, request.headers.get("user-agent"), lifetime
    )
    access = create_access_token("user", user.id, session.id)
    set_session_cookies(response, USER_COOKIES, access, refresh, int(lifetime.total_seconds()))


def _session_ended() -> JSONResponse:
    response = error_response(401, "session_expired", "Your session has ended. Please log in again.")
    clear_session_cookies(response, USER_COOKIES)
    return response


@router.get("/csrf", response_model=CsrfOut)
async def csrf(request: Request, response: Response) -> CsrfOut:
    token = request.cookies.get(CSRF_COOKIE) or new_token()
    set_csrf_cookie(response, token)
    return CsrfOut(csrf_token=token)


@router.post("/signup", status_code=201, response_model=UserOut, dependencies=[limit_by_ip("signup")])
async def signup(
    body: SignupIn, request: Request, response: Response, db: AsyncSession = Depends(get_session)
) -> UserOut:
    email = body.email.lower()
    taken = AppError("email_taken", "An account with this email already exists. Log in instead.", 409)
    if await db.scalar(select(User.id).where(User.email == email)):
        raise taken
    await get_active_university(db, body.university_id)
    user = User(
        email=email,
        full_name=body.full_name,
        password_hash=hash_password(body.password),
        phone=body.phone,
        university_id=body.university_id,
        level=body.level,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        raise taken from None
    await codes.send_code(db, user, "verify_email")
    await _log_in(db, request, response, user)
    await db.commit()
    return await build_user_out(db, user)


@router.post("/verify-email", response_model=UserOut, dependencies=[limit_by_ip("verify_email")])
async def verify_email(
    body: VerifyEmailIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> UserOut:
    if user.email_verified_at is None:
        try:
            await codes.use_code(db, user, "verify_email", body.code)
        except AppError:
            await db.commit()  # keep the wrong-attempt count
            raise
        user.email_verified_at = utcnow()
        await db.commit()
    return await build_user_out(db, user)


@router.post("/resend-code", response_model=OkOut)
async def resend_code(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
) -> OkOut:
    if user.email_verified_at is not None:
        raise AppError("already_verified", "Your email is already verified", 409)
    await rate_limit.enforce("resend_code", str(user.id))
    await codes.send_code(db, user, "verify_email")
    await db.commit()
    return OkOut()


@router.post(
    "/forgot-password", response_model=OkOut, dependencies=[limit_by_ip("forgot_password")]
)
async def forgot_password(body: ForgotPasswordIn, db: AsyncSession = Depends(get_session)) -> OkOut:
    """Emails a reset code. Answers the same whether or not the email has an account."""
    email = body.email.lower()
    await rate_limit.enforce("forgot_password_email", email)
    user = await db.scalar(select(User).where(User.email == email))
    if user is not None and user.status == "active":
        await codes.send_code(db, user, "reset_password")
        await db.commit()
    return OkOut()


@router.post(
    "/reset-password", response_model=UserOut, dependencies=[limit_by_ip("reset_password")]
)
async def reset_password(
    body: ResetPasswordIn, request: Request, response: Response, db: AsyncSession = Depends(get_session)
) -> UserOut:
    """Sets a new password with the emailed code, signs out every other device and logs in."""
    user = await db.scalar(
        select(User).where(User.email == body.email.lower(), User.status == "active").with_for_update()
    )
    try:
        await codes.use_code(db, user, "reset_password", body.code)
    except AppError:
        await db.commit()  # keep the wrong-attempt count
        raise

    now = utcnow()
    user.password_hash = hash_password(body.new_password)
    user.failed_login_count = 0
    user.locked_until = None
    if user.email_verified_at is None:
        user.email_verified_at = now  # the code proved they own the inbox
    await sessions.revoke_all(db, "user", user.id)
    await _log_in(db, request, response, user)
    await db.commit()
    return await build_user_out(db, user)


@router.post("/login", response_model=UserOut, dependencies=[limit_by_ip("login")])
async def login(
    body: LoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_session)
) -> UserOut:
    settings = get_settings()
    now = utcnow()
    user = await db.scalar(
        select(User).where(User.email == body.email.lower()).with_for_update()
    )

    if user is not None and user.locked_until is not None and user.locked_until > now:
        minutes = max(1, int((user.locked_until - now).total_seconds() // 60) + 1)
        raise AppError(
            "account_locked",
            f"Too many failed attempts. Try again in {minutes} minute{'s' if minutes != 1 else ''}.",
            429,
        )

    if not verify_password(user.password_hash if user else None, body.password):
        if user is not None:
            user.failed_login_count += 1
            if user.failed_login_count >= settings.login_max_failures:
                user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
                user.failed_login_count = 0
            await db.commit()
        raise AppError("invalid_credentials", "Email or password is incorrect", 401)

    if user.status != "active":
        raise AppError("account_disabled", "This account has been disabled. Contact support.", 403)

    user.failed_login_count = 0
    user.locked_until = None
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    await _log_in(db, request, response, user)
    await db.commit()
    return await build_user_out(db, user)


@router.get("/google")
async def google_start(request: Request, next: str | None = None) -> Response:
    client = google.google_client()
    if client is None:
        raise AppError("google_not_configured", "Google sign-in is not set up yet", 503)
    request.session["next"] = safe_next(next)
    redirect_uri = f"{get_settings().app_base_url}/api/user/auth/google/callback"
    return await client.authorize_redirect(request, redirect_uri)


@router.get("/google/callback")
async def google_callback(request: Request, db: AsyncSession = Depends(get_session)) -> Response:
    next_url = safe_next(request.session.pop("next", None))
    client = google.google_client()
    if client is None:
        return RedirectResponse("/login.html?error=google_not_configured", 303)
    try:
        token = await client.authorize_access_token(request)
    except Exception:
        log.warning("Google sign-in failed", exc_info=True)
        return RedirectResponse("/login.html?error=google_failed", 303)
    try:
        user = await google.find_or_create_google_user(db, token.get("userinfo") or {})
    except AppError as exc:
        return RedirectResponse(f"/login.html?error={exc.code}", 303)

    if not profile_complete(user):
        # First Google sign-in: collect phone, university and level before anything else.
        next_url = f"/complete-profile.html?next={quote(next_url, safe='')}"
    response = RedirectResponse(next_url, 303)
    await _log_in(db, request, response, user)
    await db.commit()
    return response


@router.post("/refresh", response_model=OkOut, dependencies=[limit_by_ip("refresh")])
async def refresh(
    request: Request, response: Response, db: AsyncSession = Depends(get_session)
) -> OkOut | JSONResponse:
    token = request.cookies.get(USER_COOKIES.refresh)
    if not token:
        return _session_ended()
    lifetime = _refresh_lifetime()
    rotated = await sessions.rotate_session(
        db, "user", token, request.headers.get("user-agent"), lifetime
    )
    if rotated is None:
        await db.commit()
        return _session_ended()
    new_refresh, session = rotated

    user = await db.get(User, session.subject_id)
    if user is None or user.status != "active":
        await sessions.revoke_all(db, "user", session.subject_id)
        await db.commit()
        return _session_ended()

    access = create_access_token("user", user.id, session.id)
    set_session_cookies(response, USER_COOKIES, access, new_refresh, int(lifetime.total_seconds()))
    await db.commit()
    return OkOut()


@router.post("/logout", response_model=OkOut)
async def logout(request: Request, db: AsyncSession = Depends(get_session)) -> JSONResponse:
    token = request.cookies.get(USER_COOKIES.refresh)
    if token:
        await sessions.revoke_session(db, "user", token)
        await db.commit()
    response = JSONResponse({"ok": True})
    clear_session_cookies(response, USER_COOKIES)
    return response
