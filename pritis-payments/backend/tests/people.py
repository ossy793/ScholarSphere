"""Test helpers that set up people: verified students, verified reps and logged-in staff."""

from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy import select

from app.core import crypto
from app.core.clock import utcnow
from app.core.db import SessionLocal
from app.models import Class, RepApplication, RepBankAccount, User
from tests.admin_helpers import FakeClock, admin_csrf, admin_login, make_staff
from tests.conftest import latest_code, make_client, signup


def future(days: int = 7) -> str:
    return (utcnow() + timedelta(days=days)).isoformat()


def due_terms(**overrides) -> dict:
    return {
        "title": "Handout 2026", "description": "Course handouts for first semester",
        "amount_kobo": 500_000, "deadline": future(), "require_matric": False, **overrides,
    }


async def user_client(email: str) -> AsyncClient:
    """A logged-in student with a verified email and complete profile. Close it yourself."""
    c = make_client()
    await c.__aenter__()
    c.headers["X-CSRF-Token"] = (await c.get("/api/user/auth/csrf")).json()["csrf_token"]
    await signup(c, email=email)
    code = await latest_code(email)
    assert (await c.post("/api/user/auth/verify-email", json={"code": code})).status_code == 200
    return c


async def rep_client(email: str, class_name: str) -> AsyncClient:
    """A verified rep, set up directly (phase 4 tests cover the application itself)."""
    c = await user_client(email)
    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == email))
        klass = Class(name=class_name, school="University of Lagos", department="Computer Science",
                      level="200", session="2025/2026", rep_user_id=user.id,
                      invite_code=("".join(ch for ch in class_name.upper() if ch.isalnum()) + "XXXXXXXX")[:8])
        db.add(klass)
        await db.flush()
        db.add(RepApplication(user_id=user.id, class_id=klass.id, proof_file="x",
                              proof_content_type="image/png", status="approved"))
        db.add(RepBankAccount(user_id=user.id, bank_code="058", bank_name="GTBank",
                              account_number=crypto.encrypt("0123456789"), account_name="ADA OBI",
                              paystack_recipient_code="RCP_x", verified_at=utcnow()))
        await db.commit()
    return c


async def staff_client(clock: FakeClock, role: str) -> AsyncClient:
    email = f"{role}@pritis.name.ng"
    secret = await make_staff(email, role)
    c = make_client()
    await c.__aenter__()
    await admin_csrf(c)
    await admin_login(c, clock, email, secret)
    return c


async def request_payout(rep: AsyncClient, amount_kobo: int, email: str = "rep@example.com"):
    """A payout request the way the site does it: emailed code first, then the request."""
    sent = await rep.post("/api/user/rep/payouts/code", json={"amount_kobo": amount_kobo})
    if sent.status_code != 200:
        return sent
    code = await latest_code(email, "confirm_payout")
    return await rep.post("/api/user/rep/payouts", json={"amount_kobo": amount_kobo, "code": code})
