"""
Auth Service
------------
Handles all authentication business logic:
- user registration
- user login (credential verification + token issuance)
- fetching the current authenticated user profile
- forgot password (email code + reset)
"""

import random
import string
from datetime import datetime, timedelta
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..models.user import User
from ..models.password_reset import PasswordResetCode
from ..schemas.auth import RegisterRequest, LoginRequest, TokenResponse
from ..utils.security import hash_password, verify_password, create_access_token
from ..services.email_service import send_password_reset_email


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

    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        department=payload.department,
        university=payload.university,
        level=payload.level,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(str(user.id))
    return TokenResponse(access_token=token)


def login_user(payload: LoginRequest, db: Session) -> TokenResponse:
    """
    Verify credentials and return a JWT token.
    Raises 401 on invalid email or wrong password.
    """
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been deactivated. Please contact support.",
        )

    user.last_login_at = datetime.utcnow()
    db.commit()

    token = create_access_token(str(user.id))
    return TokenResponse(access_token=token)


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
