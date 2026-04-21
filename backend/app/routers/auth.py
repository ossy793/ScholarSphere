from fastapi import APIRouter, Depends, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models.user import User
from ..schemas.auth import (
    RegisterRequest, LoginRequest, TokenResponse, UserResponse,
    ForgotPasswordRequest, VerifyResetCodeRequest, ResetPasswordRequest,
    GoogleAuthRequest, TwoFAVerifyRequest, TwoFAEnableRequest, TwoFADisableRequest,
)
from ..services import auth_service
from ..utils.security import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])
limiter = Limiter(key_func=get_remote_address)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def register(request: Request, payload: RegisterRequest, db: Session = Depends(get_db)):
    return auth_service.register_user(payload, db)


@router.post("/login")
@limiter.limit("10/minute")
def login(request: Request, payload: LoginRequest, db: Session = Depends(get_db)):
    return auth_service.login_user(payload, db)


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.get("/config")
def auth_config():
    """Public endpoint — returns frontend-safe auth config (Google client ID)."""
    return {"google_client_id": settings.GOOGLE_CLIENT_ID or ""}


# ── Google Sign-In ────────────────────────────────────────────────────────────

@router.post("/google")
@limiter.limit("20/minute")
async def google_auth(request: Request, payload: GoogleAuthRequest, db: Session = Depends(get_db)):
    """Verify Google ID token and return a Pritis JWT."""
    return await auth_service.google_sign_in(payload.credential, db)


# ── Two-Factor Authentication (TOTP) ─────────────────────────────────────────

@router.post("/2fa/setup")
def two_fa_setup(current_user: User = Depends(get_current_user)):
    """Generate a TOTP secret + provisioning URI (call before enabling)."""
    return auth_service.setup_2fa(current_user)


@router.post("/2fa/enable")
def two_fa_enable(
    payload:      TwoFAEnableRequest,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Confirm first TOTP code and enable 2FA on the account."""
    return auth_service.enable_2fa(current_user, payload.totp_code, payload.secret, db)


@router.post("/2fa/disable")
def two_fa_disable(
    payload:      TwoFADisableRequest,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Verify TOTP code and disable 2FA."""
    return auth_service.disable_2fa(current_user, payload.totp_code, db)


@router.post("/2fa/verify")
@limiter.limit("10/minute")
def two_fa_verify(request: Request, payload: TwoFAVerifyRequest, db: Session = Depends(get_db)):
    """Complete login: verify TOTP code after password check."""
    return auth_service.verify_2fa_login(payload.pre_auth_token, payload.totp_code, db)


# ── Password Reset ────────────────────────────────────────────────────────────

@router.post("/forgot-password")
@limiter.limit("5/minute")
def forgot_password(request: Request, payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    return auth_service.request_password_reset(payload.email, db)


@router.post("/verify-reset-code")
@limiter.limit("10/minute")
def verify_reset_code(request: Request, payload: VerifyResetCodeRequest, db: Session = Depends(get_db)):
    return auth_service.verify_reset_code(payload.email, payload.code, db)


@router.post("/reset-password")
@limiter.limit("5/minute")
def reset_password(request: Request, payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    return auth_service.reset_password(payload.email, payload.code, payload.new_password, db)
