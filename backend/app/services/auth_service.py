"""
Auth Service
------------
Handles all authentication business logic:
- user registration
- user login (credential verification + token issuance)
- fetching the current authenticated user profile
- forgot password (email code + reset)
"""

import re
import random
import secrets
import string
from datetime import datetime, timedelta
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

import httpx
import pyotp

from ..models.user import User
from ..models.password_reset import PasswordResetCode
from ..schemas.auth import RegisterRequest, LoginRequest, TokenResponse
from ..utils.security import hash_password, verify_password, create_access_token, create_pre_auth_token, decode_pre_auth_token
from ..services.email_service import send_password_reset_email
from ..config import settings


def register_user(payload: RegisterRequest, db: Session) -> TokenResponse:
    """
    Create a new user account.
    Raises 400 if the email is already taken.
    Returns a JWT token on success.
    """
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    # Username uniqueness check
    username = payload.username.strip().lower() if payload.username else None
    if username:
        if db.query(User).filter(User.username == username).first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="That username is already taken. Please choose another.",
            )

    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        username=username,
        department=payload.department,
        university=payload.university,
        level=payload.level,
        # Map registration fields to profile fields so profile_completed can be set
        school=payload.university,
        course=payload.department,
    )

    # Mark profile complete if all required fields are present
    _REQUIRED = ("full_name", "username", "school", "level", "course")
    user.profile_completed = all(
        getattr(user, f) and str(getattr(user, f)).strip() for f in _REQUIRED
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(str(user.id))
    return TokenResponse(access_token=token)


def login_user(payload: LoginRequest, db: Session) -> dict:
    """
    Verify credentials and return a JWT token.
    Raises 401 on invalid email or wrong password.
    """
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not user.password_hash or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been deactivated. Please contact support.",
        )

    if getattr(user, "totp_enabled", False) and user.totp_secret:
        pre_token = create_pre_auth_token(str(user.id))
        return {"requires_2fa": True, "pre_auth_token": pre_token}

    user.last_login_at = datetime.utcnow()
    db.commit()

    token = create_access_token(str(user.id))
    return {"access_token": token, "token_type": "bearer"}


def request_password_reset(email: str, db: Session) -> dict:
    """Generate a 6-digit code, store it, and email it to the user."""
    user = db.query(User).filter(User.email == email).first()
    # Always respond the same to avoid email enumeration
    if not user:
        return {"message": "If that email is registered, a reset code has been sent."}

    # Invalidate any existing unused codes for this email
    db.query(PasswordResetCode).filter(
        PasswordResetCode.email == email,
        PasswordResetCode.used == False,
    ).delete(synchronize_session=False)

    code = "".join(random.choices(string.digits, k=6))
    reset = PasswordResetCode(
        email=email,
        code=code,
        expires_at=datetime.utcnow() + timedelta(minutes=15),
    )
    db.add(reset)
    db.commit()

    sent = send_password_reset_email(email, user.full_name, code)
    if not sent:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to send reset email. Please try again later.",
        )
    return {"message": "If that email is registered, a reset code has been sent."}


def verify_reset_code(email: str, code: str, db: Session) -> dict:
    """Verify the code without consuming it (used for step 2 of the UI flow)."""
    reset = db.query(PasswordResetCode).filter(
        PasswordResetCode.email == email,
        PasswordResetCode.code == code,
        PasswordResetCode.used == False,
        PasswordResetCode.expires_at > datetime.utcnow(),
    ).first()
    if not reset:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired code.",
        )
    return {"message": "Code verified."}


def reset_password(email: str, code: str, new_password: str, db: Session) -> dict:
    """Verify code, hash and save the new password, mark code as used."""
    reset = db.query(PasswordResetCode).filter(
        PasswordResetCode.email == email,
        PasswordResetCode.code == code,
        PasswordResetCode.used == False,
        PasswordResetCode.expires_at > datetime.utcnow(),
    ).first()
    if not reset:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired code.",
        )

    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    user.password_hash = hash_password(new_password)
    reset.used = True
    db.commit()
    return {"message": "Password reset successful. You can now sign in."}


async def google_sign_in(credential: str, db: Session) -> dict:
    """Verify Google ID token, create/find user, return JWT."""
    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": credential},
        )
    if res.status_code != 200:
        raise HTTPException(400, "Invalid Google credential.")

    info = res.json()
    if settings.GOOGLE_CLIENT_ID and info.get("aud") != settings.GOOGLE_CLIENT_ID:
        raise HTTPException(400, "Google token audience mismatch.")

    google_id = info.get("sub", "")
    email     = info.get("email", "").lower().strip()
    name      = info.get("name") or email.split("@")[0]

    if not email or not google_id:
        raise HTTPException(400, "Could not retrieve account info from Google.")

    user = db.query(User).filter(User.google_id == google_id).first()
    if not user:
        user = db.query(User).filter(User.email == email).first()
        if user:
            user.google_id = google_id
            db.commit()

    if not user:
        base = re.sub(r"[^a-z0-9]", "", name.lower())[:20] or "user"
        uname, suffix = base, 0
        while db.query(User).filter(User.username == uname).first():
            suffix += 1
            uname = f"{base}{suffix}"

        user = User(
            email=email,
            password_hash=hash_password(secrets.token_urlsafe(32)),
            full_name=name,
            username=uname,
            google_id=google_id,
            profile_picture_url=info.get("picture"),
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    if not user.is_active:
        raise HTTPException(403, "Your account has been deactivated.")

    user.last_login_at = datetime.utcnow()
    db.commit()
    return {"access_token": create_access_token(str(user.id)), "token_type": "bearer"}


def setup_2fa(user: User) -> dict:
    """Generate TOTP secret and provisioning URI for QR code."""
    secret = pyotp.random_base32()
    uri    = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name="Pritis")
    return {"secret": secret, "uri": uri}


def enable_2fa(user: User, totp_code: str, secret: str, db: Session) -> dict:
    """Verify first TOTP code and persist the secret to enable 2FA."""
    if not pyotp.TOTP(secret).verify(totp_code, valid_window=1):
        raise HTTPException(400, "Invalid authenticator code. Please try again.")
    user.totp_secret  = secret
    user.totp_enabled = True
    db.commit()
    return {"message": "Two-factor authentication enabled."}


def disable_2fa(user: User, totp_code: str, db: Session) -> dict:
    """Verify TOTP code and disable 2FA."""
    if not getattr(user, "totp_enabled", False) or not user.totp_secret:
        raise HTTPException(400, "2FA is not currently enabled.")
    if not pyotp.TOTP(user.totp_secret).verify(totp_code, valid_window=1):
        raise HTTPException(400, "Invalid authenticator code. Please try again.")
    user.totp_enabled = False
    user.totp_secret  = None
    db.commit()
    return {"message": "Two-factor authentication disabled."}


def verify_2fa_login(pre_auth_token: str, totp_code: str, db: Session) -> dict:
    """Complete login after 2FA: verify TOTP code and return JWT."""
    user_id = decode_pre_auth_token(pre_auth_token)
    if not user_id:
        raise HTTPException(401, "Session expired. Please log in again.")

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not getattr(user, "totp_enabled", False) or not user.totp_secret:
        raise HTTPException(401, "Authentication error.")

    if not pyotp.TOTP(user.totp_secret).verify(totp_code, valid_window=1):
        raise HTTPException(400, "Invalid authenticator code. Please try again.")

    user.last_login_at = datetime.utcnow()
    db.commit()
    return {"access_token": create_access_token(str(user.id)), "token_type": "bearer"}


def get_user_by_id(user_id: str, db: Session) -> User:
    """
    Fetch a user by primary key.
    Raises 404 if not found.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return user
