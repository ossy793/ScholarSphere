"""
Auth Service
------------
Handles all authentication business logic:
- user registration
- user login (credential verification + token issuance)
- fetching the current authenticated user profile
"""

from datetime import datetime
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..models.user import User
from ..schemas.auth import RegisterRequest, LoginRequest, TokenResponse
from ..utils.security import hash_password, verify_password, create_access_token


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
