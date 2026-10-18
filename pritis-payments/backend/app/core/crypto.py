"""Encryption at rest for small secrets (staff authenticator secrets, rep bank account numbers)."""

from cryptography.fernet import Fernet

from app.core.config import get_settings


def _fernet() -> Fernet:
    return Fernet(get_settings().data_encryption_key.encode())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
