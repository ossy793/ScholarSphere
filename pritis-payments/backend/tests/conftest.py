import os

# Must be set before any app module is imported: switches to TEST_DATABASE_URL and
# pins the secrets the tests rely on.
os.environ["ENVIRONMENT"] = "test"
os.environ["JWT_SECRET"] = "test-jwt-secret-not-for-production-0123456789"
os.environ["APP_BASE_URL"] = "http://test"
os.environ["GOOGLE_CLIENT_ID"] = "test-google-client"
os.environ["GOOGLE_CLIENT_SECRET"] = "test-google-secret"
os.environ["ADMIN_BASE_URL"] = "http://admin.test"
os.environ["DATA_ENCRYPTION_KEY"] = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
os.environ["PAYSTACK_SECRET_KEY"] = "sk_test_not_real"  # every Paystack call is faked in tests
os.environ["STORAGE_BACKEND"] = "local"
import tempfile  # noqa: E402

os.environ["STORAGE_LOCAL_DIR"] = tempfile.mkdtemp(prefix="pritis-test-uploads-")

import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.core.config import get_settings

BACKEND_DIR = Path(__file__).resolve().parents[1]
PASSWORD = "Correct-horse 1"
# Seeded by migrations and kept between tests.
REFERENCE_TABLES = {"universities"}


def alembic_config() -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["url"] = get_settings().sqlalchemy_url
    cfg.attributes["configure_logger"] = False
    return cfg


async def _empty_schema() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(get_settings().sqlalchemy_url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> None:
    """Rebuild the test database from an empty schema with the real migrations.
    (Not by downgrading: rows left by the last run can't always survive a downgrade.)"""
    import asyncio

    asyncio.run(_empty_schema())
    command.upgrade(alembic_config(), "head")


@pytest.fixture(autouse=True)
async def clean_tables(migrated_database: None) -> None:
    from app.core.db import engine
    from app.models import Base

    names = ", ".join(
        table.name for table in Base.metadata.sorted_tables if table.name not in REFERENCE_TABLES
    )
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


def make_client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def raw_client() -> AsyncIterator[AsyncClient]:
    """A client without the CSRF header."""
    async with make_client() as c:
        yield c


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """A browser-like client: has the CSRF cookie and sends it back in the header."""
    async with make_client() as c:
        resp = await c.get("/api/user/auth/csrf")
        c.headers["X-CSRF-Token"] = resp.json()["csrf_token"]
        yield c


async def university_id(name: str = "University of Lagos") -> str:
    from app.core.db import SessionLocal
    from app.models import University

    async with SessionLocal() as db:
        return str(await db.scalar(select(University.id).where(University.name == name)))


async def signup_body(email: str = "ada@example.com", password: str = PASSWORD) -> dict:
    return {
        "full_name": "Ada Obi",
        "email": email,
        "password": password,
        "phone": "0803 123 4567",
        "university_id": await university_id(),
        "level": "200",
        "accept_terms": True,
    }


async def signup(client: AsyncClient, email: str = "ada@example.com", password: str = PASSWORD):
    resp = await client.post("/api/user/auth/signup", json=await signup_body(email, password))
    assert resp.status_code == 201, resp.text
    return resp


async def latest_code(email: str, template: str = "verify_email") -> str:
    """Reads the code out of the most recent queued email of that kind."""
    from app.core.db import SessionLocal
    from app.models import EmailOutbox

    async with SessionLocal() as db:
        row = await db.scalar(
            select(EmailOutbox)
            .where(EmailOutbox.to_email == email, EmailOutbox.template == template)
            .order_by(EmailOutbox.created_at.desc(), EmailOutbox.id.desc())
            .limit(1)
        )
    assert row is not None
    assert re.fullmatch(r"\d{6}", row.payload["code"])
    return row.payload["code"]
