"""Helpers for admin-site tests: invitations, authenticator codes and two-step login."""

import time
from urllib.parse import parse_qs, urlparse

import pyotp
from httpx import AsyncClient

from app.core import crypto
from app.services import totp

STAFF_PASSWORD = "Staff-pass 1"


class FakeClock:
    """Replaces totp.now so each code can come from a fresh 30-second step."""

    def __init__(self) -> None:
        self.t = time.time()

    def now(self) -> float:
        return self.t

    def next_code(self, secret: str) -> str:
        self.t += totp.STEP_SECONDS
        return pyotp.TOTP(secret).at(self.t)


def token_from_link(link: str) -> str:
    return parse_qs(urlparse(link).query)["token"][0]


async def admin_csrf(client: AsyncClient) -> None:
    resp = await client.get("/api/admin/auth/csrf")
    client.headers["X-CSRF-Token"] = resp.json()["csrf_token"]


async def accept_invite(
    client: AsyncClient, clock: FakeClock, link: str, password: str = STAFF_PASSWORD
) -> str:
    """Opens the link, 'scans' the QR, sets the password. Returns the authenticator secret."""
    token = token_from_link(link)
    setup = await client.post("/api/admin/auth/invite/start", json={"token": token})
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    resp = await client.post(
        "/api/admin/auth/invite/accept",
        json={"token": token, "password": password, "code": clock.next_code(secret)},
    )
    assert resp.status_code == 200, resp.text
    return secret


async def make_staff(email: str, role: str) -> str:
    """An active staff account straight in the database (skipping the invite). Returns its
    authenticator secret; log in with admin_login(..., STAFF_PASSWORD)."""
    from app.core.db import SessionLocal
    from app.core.security import hash_password
    from app.models import AdminUser

    secret = pyotp.random_base32()
    async with SessionLocal() as db:
        db.add(AdminUser(
            email=email, full_name=email.split("@")[0], role=role, status="active",
            password_hash=hash_password(STAFF_PASSWORD), totp_secret=crypto.encrypt(secret),
        ))
        await db.commit()
    return secret


async def admin_login(
    client: AsyncClient, clock: FakeClock, email: str, secret: str, password: str = STAFF_PASSWORD
) -> dict:
    resp = await client.post("/api/admin/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"next": "totp"}
    resp = await client.post("/api/admin/auth/totp", json={"code": clock.next_code(secret)})
    assert resp.status_code == 200, resp.text
    return resp.json()
