import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.clock import utcnow
from app.core.config import get_settings

_hasher = PasswordHasher()  # argon2id
# Verified against when the account doesn't exist, so a wrong email takes as long as a wrong password.
_DUMMY_HASH = _hasher.hash("timing-equaliser")

JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        _hasher.verify(password_hash or _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return False
    return password_hash is not None


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def new_token() -> str:
    """A random, URL-safe secret for refresh tokens and CSRF."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Tokens are stored only as their SHA-256."""
    return hashlib.sha256(token.encode()).hexdigest()


def new_otp() -> str:
    """A 6-digit one-time code for email verification."""
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(user_id: uuid.UUID, code: str) -> str:
    # Keyed and bound to the user, so a leaked table can't be brute-forced offline.
    key = get_settings().jwt_secret.encode()
    return hmac.new(key, f"{user_id}:{code}".encode(), hashlib.sha256).hexdigest()


def create_access_token(subject_type: str, subject_id: uuid.UUID, session_id: uuid.UUID) -> str:
    settings = get_settings()
    now = utcnow()
    payload = {
        "sub": str(subject_id),
        "typ": subject_type,
        "sid": str(session_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


MFA_TOKEN_MINUTES = 5


def create_mfa_token(admin_id: uuid.UUID) -> str:
    """Proof that a staff member passed the password step, valid for a few minutes."""
    now = utcnow()
    payload = {
        "sub": str(admin_id),
        "typ": "admin_mfa",
        "sid": "-",
        "iat": now,
        "exp": now + timedelta(minutes=MFA_TOKEN_MINUTES),
    }
    return jwt.encode(payload, get_settings().jwt_secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str, subject_type: str) -> dict[str, Any] | None:
    try:
        data = jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "sub", "sid", "typ"]},
        )
    except jwt.PyJWTError:
        return None
    return data if data["typ"] == subject_type else None
