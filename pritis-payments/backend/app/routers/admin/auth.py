"""/api/admin/auth/*: password then authenticator-code login, sessions, invitations."""

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.cookies import (
    ADMIN_COOKIES,
    ADMIN_MFA_COOKIE,
    CSRF_COOKIE,
    clear_session_cookies,
    set_csrf_cookie,
    set_session_cookies,
)
from app.core.db import get_session
from app.core.deps import get_current_admin
from app.core.errors import AppError, error_response
from app.core.security import (
    MFA_TOKEN_MINUTES,
    create_access_token,
    create_mfa_token,
    decode_access_token,
    hash_password,
    new_token,
    password_needs_rehash,
    verify_password,
)
from app.models import AdminUser
from app.schemas.admin import (
    AcceptInviteIn,
    AdminLoginIn,
    AdminLoginOut,
    AdminOut,
    InviteSetupOut,
    InviteTokenIn,
    TotpIn,
)
from app.schemas.auth import CsrfOut, OkOut
from app.services import audit, rate_limit, sessions, staff, totp
from app.services.rate_limit import limit_by_ip

router = APIRouter(prefix="/auth", tags=["admin auth"])

MFA_COOKIE_PATH = "/api/admin/auth"


def admin_out(admin: AdminUser) -> AdminOut:
    return AdminOut.model_validate(admin, from_attributes=True)


def _session_hours() -> timedelta:
    return timedelta(hours=get_settings().admin_session_hours)


def _session_ended() -> JSONResponse:
    response = error_response(401, "session_expired", "Your session has ended. Please log in again.")
    clear_session_cookies(response, ADMIN_COOKIES)
    return response


@router.get("/csrf", response_model=CsrfOut)
async def csrf(request: Request, response: Response) -> CsrfOut:
    token = request.cookies.get(CSRF_COOKIE) or new_token()
    set_csrf_cookie(response, token)
    return CsrfOut(csrf_token=token)


@router.post("/login", response_model=AdminLoginOut, dependencies=[limit_by_ip("admin_login")])
async def login(
    body: AdminLoginIn, response: Response, db: AsyncSession = Depends(get_session)
) -> AdminLoginOut:
    """Step 1: email and password. On success the browser gets a 5-minute cookie for step 2."""
    settings = get_settings()
    now = utcnow()
    admin = await db.scalar(
        select(AdminUser).where(AdminUser.email == body.email.lower()).with_for_update()
    )
    if admin is not None and admin.locked_until is not None and admin.locked_until > now:
        raise AppError("account_locked", "Too many failed attempts. Try again later.", 429)

    if not verify_password(admin.password_hash if admin else None, body.password):
        if admin is not None:
            admin.failed_login_count += 1
            if admin.failed_login_count >= settings.login_max_failures:
                admin.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
                admin.failed_login_count = 0
            await db.commit()
        raise AppError("invalid_credentials", "Email or password is incorrect", 401)

    if admin.status != "active" or admin.totp_secret is None:
        raise AppError("account_disabled", "This staff account is not active.", 403)

    admin.failed_login_count = 0
    admin.locked_until = None
    if password_needs_rehash(admin.password_hash):
        admin.password_hash = hash_password(body.password)
    await db.commit()

    response.set_cookie(
        ADMIN_MFA_COOKIE, create_mfa_token(admin.id), max_age=MFA_TOKEN_MINUTES * 60,
        path=MFA_COOKIE_PATH, httponly=True, secure=settings.cookie_secure, samesite="lax",
    )
    return AdminLoginOut()


@router.post("/totp", response_model=AdminOut)
async def verify_totp(
    body: TotpIn, request: Request, response: Response, db: AsyncSession = Depends(get_session)
) -> AdminOut:
    """Step 2: the 6-digit code from the authenticator app. Starts the 8-hour session."""
    token = request.cookies.get(ADMIN_MFA_COOKIE)
    data = decode_access_token(token, "admin_mfa") if token else None
    if data is None:
        raise AppError("mfa_expired", "Your sign-in took too long. Enter your password again.", 401)

    admin_id = uuid.UUID(data["sub"])
    await rate_limit.enforce("admin_totp", str(admin_id))
    admin = await db.get(AdminUser, admin_id, with_for_update=True)
    if admin is None or admin.status != "active" or admin.totp_secret is None:
        raise AppError("mfa_expired", "Your sign-in took too long. Enter your password again.", 401)

    step = totp.verify(admin.totp_secret, body.code, admin.totp_last_step)
    if step is None:
        raise AppError("code_invalid", "That code is not correct. Use the latest code in your app.", 400)

    admin.totp_last_step = step
    admin.last_login_at = utcnow()
    lifetime = _session_hours()
    refresh, session = await sessions.start_session(
        db, "admin", admin.id, request.headers.get("user-agent"), lifetime
    )
    access = create_access_token("admin", admin.id, session.id)
    set_session_cookies(response, ADMIN_COOKIES, access, refresh, int(lifetime.total_seconds()))
    response.delete_cookie(ADMIN_MFA_COOKIE, path=MFA_COOKIE_PATH)
    await audit.record(
        db, actor=admin, action="admin.login", entity="admin_user", entity_id=admin.id,
        request=request,
    )
    await db.commit()
    return admin_out(admin)


@router.post("/refresh", response_model=OkOut)
async def refresh(
    request: Request, response: Response, db: AsyncSession = Depends(get_session)
) -> OkOut | JSONResponse:
    token = request.cookies.get(ADMIN_COOKIES.refresh)
    if not token:
        return _session_ended()
    lifetime = _session_hours()
    rotated = await sessions.rotate_session(
        db, "admin", token, request.headers.get("user-agent"), lifetime, keep_expiry=True
    )
    if rotated is None:
        await db.commit()
        return _session_ended()
    new_refresh, session = rotated

    admin = await db.get(AdminUser, session.subject_id)
    if admin is None or admin.status != "active":
        await sessions.revoke_all(db, "admin", session.subject_id)
        await db.commit()
        return _session_ended()

    remaining = int((session.expires_at - utcnow()).total_seconds())
    access = create_access_token("admin", admin.id, session.id)
    set_session_cookies(response, ADMIN_COOKIES, access, new_refresh, max(remaining, 0))
    await db.commit()
    return OkOut()


@router.post("/logout", response_model=OkOut)
async def logout(request: Request, db: AsyncSession = Depends(get_session)) -> JSONResponse:
    token = request.cookies.get(ADMIN_COOKIES.refresh)
    if token:
        await sessions.revoke_session(db, "admin", token)
        await db.commit()
    response = JSONResponse({"ok": True})
    clear_session_cookies(response, ADMIN_COOKIES)
    return response


@router.post(
    "/invite/start", response_model=InviteSetupOut, dependencies=[limit_by_ip("admin_invite")]
)
async def invite_start(body: InviteTokenIn, db: AsyncSession = Depends(get_session)) -> InviteSetupOut:
    """Opening the invitation link: shows who it's for and the authenticator QR code.
    The secret is kept until the invite is accepted, so reloading the page shows the same QR."""
    _, admin = await staff.find_invite(db, body.token)
    if admin.totp_secret is None:
        admin.totp_secret = totp.new_secret()
    await db.commit()
    return InviteSetupOut(
        email=admin.email, full_name=admin.full_name, role=admin.role,
        **totp.setup_details(admin.totp_secret, admin.email),
    )


@router.post("/invite/accept", response_model=OkOut, dependencies=[limit_by_ip("admin_invite")])
async def invite_accept(
    body: AcceptInviteIn, request: Request, db: AsyncSession = Depends(get_session)
) -> OkOut:
    """Sets the password and confirms the authenticator works. Then the person logs in normally."""
    invite, admin = await staff.find_invite(db, body.token)
    if admin.totp_secret is None:
        raise AppError("invite_invalid", "Open the invitation link again to scan the QR code.", 400)
    step = totp.verify(admin.totp_secret, body.code, None)
    if step is None:
        raise AppError(
            "code_invalid", "That code is not correct. Use the Pritis DPP entry in your authenticator app (set up earlier, it may say Pritis Class Dues).", 400
        )
    now = utcnow()
    admin.password_hash = hash_password(body.password)
    admin.totp_last_step = step
    admin.status = "active"
    invite.used_at = now
    await audit.record(
        db, actor=admin, action="admin.invite_accepted", entity="admin_user", entity_id=admin.id,
        request=request,
    )
    await db.commit()
    return OkOut()


me_router = APIRouter(tags=["admin"])


@me_router.get("/me", response_model=AdminOut)
async def me(admin: AdminUser = Depends(get_current_admin)) -> AdminOut:
    return admin_out(admin)
