from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from starlette.responses import RedirectResponse

from app.core.db import SessionLocal
from app.models import User
from app.services import google
from tests.conftest import signup


class FakeGoogle:
    """Stands in for Authlib's Google client, so no network is needed."""

    def __init__(self, userinfo: dict[str, Any] | None = None, fail: bool = False) -> None:
        self.userinfo = userinfo
        self.fail = fail

    async def authorize_redirect(self, request, redirect_uri: str) -> RedirectResponse:
        return RedirectResponse(f"https://accounts.google.com/o/oauth2/auth?redirect_uri={redirect_uri}", 302)

    async def authorize_access_token(self, request) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("state mismatch")
        return {"userinfo": self.userinfo}


def _info(**overrides: Any) -> dict[str, Any]:
    info = {"sub": "google-123", "email": "Ada@Gmail.com", "email_verified": True, "name": "Ada Obi"}
    return {**info, **overrides}


@pytest.fixture
def fake_google(monkeypatch):
    def install(**kwargs: Any) -> FakeGoogle:
        fake = FakeGoogle(**kwargs)
        monkeypatch.setattr(google, "google_client", lambda: fake)
        return fake

    return install


async def _callback(client: AsyncClient):
    return await client.get("/api/user/auth/google/callback?code=x&state=y", follow_redirects=False)


async def test_google_start_redirects_to_google(client: AsyncClient, fake_google) -> None:
    fake_google(userinfo=_info())
    resp = await client.get("/api/user/auth/google?next=/dues/abc", follow_redirects=False)
    assert resp.status_code == 302
    assert "redirect_uri=http://test/api/user/auth/google/callback" in resp.headers["location"]


async def test_new_google_user_completes_profile_then_returns_to_next(
    client: AsyncClient, fake_google
) -> None:
    fake_google(userinfo=_info())
    await client.get("/api/user/auth/google?next=/dues/abc", follow_redirects=False)
    resp = await _callback(client)
    assert resp.status_code == 303
    # No phone, university or level yet, so the profile page comes first.
    assert resp.headers["location"] == "/complete-profile.html?next=%2Fdues%2Fabc"

    me = (await client.get("/api/user/me")).json()
    assert me["email"] == "ada@gmail.com"
    assert me["email_verified"] is True
    assert me["has_password"] is False
    assert me["profile_complete"] is False


async def test_returning_google_user_with_profile_goes_straight_to_next(
    client: AsyncClient, fake_google
) -> None:
    await signup(client, email="ada@gmail.com")  # has phone, university, level
    fake_google(userinfo=_info())
    await client.get("/api/user/auth/google?next=/dues/abc", follow_redirects=False)
    resp = await _callback(client)
    assert resp.headers["location"] == "/dues/abc"


async def test_google_links_to_existing_password_account(client: AsyncClient, fake_google) -> None:
    await signup(client, email="ada@gmail.com")
    fake_google(userinfo=_info())
    resp = await _callback(client)
    assert resp.status_code == 303

    async with SessionLocal() as db:
        users = (await db.scalars(select(User))).all()
    assert len(users) == 1
    assert users[0].google_sub == "google-123"
    assert users[0].email_verified_at is not None  # Google vouched for the email


async def test_unverified_google_email_is_refused(client: AsyncClient, fake_google) -> None:
    fake_google(userinfo=_info(email_verified=False))
    resp = await _callback(client)
    assert resp.headers["location"] == "/login.html?error=google_email_unverified"
    assert (await client.get("/api/user/me")).status_code == 401


async def test_google_failure_goes_back_to_login(client: AsyncClient, fake_google) -> None:
    fake_google(fail=True)
    resp = await _callback(client)
    assert resp.headers["location"] == "/login.html?error=google_failed"


@pytest.mark.parametrize("bad_next", ["//evil.example", "https://evil.example", "/\\evil.example"])
async def test_next_must_stay_on_site(client: AsyncClient, fake_google, bad_next: str) -> None:
    fake_google(userinfo=_info())
    await client.get("/api/user/auth/google", params={"next": bad_next}, follow_redirects=False)
    resp = await _callback(client)
    assert resp.headers["location"] == "/complete-profile.html?next=%2Fdashboard.html"


async def test_google_not_configured(client: AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr(google, "google_client", lambda: None)
    resp = await client.get("/api/user/auth/google", follow_redirects=False)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "google_not_configured"
