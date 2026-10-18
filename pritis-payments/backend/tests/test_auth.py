from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.clock import utcnow
from app.core.db import SessionLocal
from app.models import AuthSession, User, UserToken
from app.services import rate_limit
from tests.conftest import PASSWORD, latest_code, make_client, signup, signup_body


async def _user(email: str = "ada@example.com") -> User:
    async with SessionLocal() as db:
        return await db.scalar(select(User).where(User.email == email))


# --- signup -----------------------------------------------------------------


async def test_signup_logs_in_and_queues_code(client: AsyncClient) -> None:
    resp = await signup(client, email="Ada@Example.com")
    body = resp.json()
    assert body["email"] == "ada@example.com"
    assert body["email_verified"] is False
    assert body["is_rep"] is False
    assert client.cookies.get("dpp_access")

    me = await client.get("/api/user/me")
    assert me.status_code == 200
    assert await latest_code("ada@example.com")

    user = await _user()
    assert user.password_hash.startswith("$argon2id$")


async def test_signup_duplicate_email(client: AsyncClient) -> None:
    await signup(client)
    resp = await client.post(
        "/api/user/auth/signup", json=await signup_body(email="ADA@example.com")
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "email_taken"


async def test_signup_validation(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/user/auth/signup",
        json={"full_name": "A", "email": "not-an-email", "password": "short",
              "phone": "12345", "university_id": "not-a-uuid", "level": "600"},
    )
    assert resp.status_code == 422
    details = {d["field"]: d["message"] for d in resp.json()["error"]["details"]}
    assert set(details) == {
        "body.full_name", "body.email", "body.password",
        "body.phone", "body.university_id", "body.level",
    }
    assert details["body.phone"].startswith("Enter a valid Nigerian mobile number")


# --- email verification code -----------------------------------------------


async def test_verify_with_code(client: AsyncClient) -> None:
    await signup(client)
    code = await latest_code("ada@example.com")
    resp = await client.post("/api/user/auth/verify-email", json={"code": code})
    assert resp.status_code == 200
    assert resp.json()["email_verified"] is True
    assert (await _user()).email_verified_at is not None


async def test_wrong_code_counts_down_then_locks_the_code(client: AsyncClient) -> None:
    await signup(client)
    code = await latest_code("ada@example.com")
    wrong = "000000" if code != "000000" else "111111"

    for left in (4, 3, 2, 1):
        resp = await client.post("/api/user/auth/verify-email", json={"code": wrong})
        assert resp.json()["error"]["code"] == "code_invalid"
        assert f"{left} tr" in resp.json()["error"]["message"]
    resp = await client.post("/api/user/auth/verify-email", json={"code": wrong})
    assert resp.json()["error"]["code"] == "code_expired"

    # Even the right code no longer works; a new one is needed.
    resp = await client.post("/api/user/auth/verify-email", json={"code": code})
    assert resp.json()["error"]["code"] == "code_expired"


async def test_expired_code(client: AsyncClient) -> None:
    await signup(client)
    code = await latest_code("ada@example.com")
    async with SessionLocal() as db:
        await db.execute(update(UserToken).values(expires_at=utcnow() - timedelta(seconds=1)))
        await db.commit()
    resp = await client.post("/api/user/auth/verify-email", json={"code": code})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "code_expired"


async def test_resend_replaces_old_code(client: AsyncClient) -> None:
    await signup(client)
    old = await latest_code("ada@example.com")
    resp = await client.post("/api/user/auth/resend-code")
    assert resp.status_code == 200
    new = await latest_code("ada@example.com")

    if old != new:
        resp = await client.post("/api/user/auth/verify-email", json={"code": old})
        assert resp.status_code == 400
    resp = await client.post("/api/user/auth/verify-email", json={"code": new})
    assert resp.json()["email_verified"] is True

    resp = await client.post("/api/user/auth/resend-code")
    assert resp.json()["error"]["code"] == "already_verified"


async def test_resend_is_rate_limited(client: AsyncClient, monkeypatch) -> None:
    monkeypatch.setitem(rate_limit.LIMITS, "resend_code", (3, 10**9))
    await signup(client)
    for _ in range(3):
        assert (await client.post("/api/user/auth/resend-code")).status_code == 200
    resp = await client.post("/api/user/auth/resend-code")
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "rate_limited"


async def test_verify_requires_login(client: AsyncClient) -> None:
    resp = await client.post("/api/user/auth/verify-email", json={"code": "123456"})
    assert resp.status_code == 401


# --- login, lockout, rate limit ---------------------------------------------


async def test_login_and_me(client: AsyncClient) -> None:
    await signup(client)
    async with make_client() as fresh:
        csrf = (await fresh.get("/api/user/auth/csrf")).json()["csrf_token"]
        fresh.headers["X-CSRF-Token"] = csrf
        assert (await fresh.get("/api/user/me")).status_code == 401

        resp = await fresh.post(
            "/api/user/auth/login", json={"email": "ADA@example.com", "password": PASSWORD}
        )
        assert resp.status_code == 200
        assert resp.json()["full_name"] == "Ada Obi"
        assert (await fresh.get("/api/user/me")).status_code == 200


@pytest.mark.parametrize(
    ("email", "password"),
    [("ada@example.com", "wrong password"), ("nobody@example.com", PASSWORD)],
)
async def test_login_failure_is_generic(client: AsyncClient, email: str, password: str) -> None:
    await signup(client)
    resp = await client.post("/api/user/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 401
    assert resp.json()["error"] == {
        "code": "invalid_credentials", "message": "Email or password is incorrect", "details": [],
    }


async def test_lockout_after_repeated_failures(client: AsyncClient) -> None:
    await signup(client)
    login = {"email": "ada@example.com", "password": "wrong password"}
    for _ in range(5):
        resp = await client.post("/api/user/auth/login", json=login)
        assert resp.status_code == 401

    # Locked: even the right password is refused.
    resp = await client.post(
        "/api/user/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
    )
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "account_locked"

    # When the lock runs out, the right password works and the counter resets.
    async with SessionLocal() as db:
        await db.execute(update(User).values(locked_until=utcnow() - timedelta(seconds=1)))
        await db.commit()
    resp = await client.post(
        "/api/user/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
    )
    assert resp.status_code == 200
    user = await _user()
    assert user.failed_login_count == 0
    assert user.locked_until is None


async def test_login_rate_limit_per_ip(client: AsyncClient, monkeypatch) -> None:
    # A huge window, so the four hits can't straddle a window boundary and reset the count.
    monkeypatch.setitem(rate_limit.LIMITS, "login", (3, 10**9))
    login = {"email": "nobody@example.com", "password": "x"}
    for _ in range(3):
        assert (await client.post("/api/user/auth/login", json=login)).status_code == 401
    resp = await client.post("/api/user/auth/login", json=login)
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "rate_limited"


# --- CSRF -------------------------------------------------------------------


async def test_csrf_required_on_state_changing_requests(raw_client: AsyncClient) -> None:
    payload = await signup_body()

    resp = await raw_client.post("/api/user/auth/signup", json=payload)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "csrf_failed"

    await raw_client.get("/api/user/auth/csrf")
    resp = await raw_client.post(
        "/api/user/auth/signup", json=payload, headers={"X-CSRF-Token": "not-the-cookie"}
    )
    assert resp.status_code == 403

    token = raw_client.cookies.get("csrf_token")
    resp = await raw_client.post(
        "/api/user/auth/signup", json=payload, headers={"X-CSRF-Token": token}
    )
    assert resp.status_code == 201


async def test_get_requests_do_not_need_csrf(raw_client: AsyncClient) -> None:
    assert (await raw_client.get("/api/health")).status_code == 200


# --- refresh and logout -----------------------------------------------------


async def _post_with_refresh(token: str) -> int:
    """Calls refresh from a separate 'browser' holding only the given refresh token."""
    async with make_client() as other:
        resp = await other.post(
            "/api/user/auth/refresh",
            headers={"Cookie": f"dpp_refresh={token}; csrf_token=t", "X-CSRF-Token": "t"},
        )
        return resp.status_code


async def test_refresh_rotates_the_token(client: AsyncClient) -> None:
    await signup(client)
    old_refresh = client.cookies.get("dpp_refresh")

    resp = await client.post("/api/user/auth/refresh")
    assert resp.status_code == 200
    new_refresh = client.cookies.get("dpp_refresh")
    assert new_refresh and new_refresh != old_refresh
    assert (await client.get("/api/user/me")).status_code == 200


async def test_reusing_an_old_refresh_token_ends_every_session(client: AsyncClient) -> None:
    await signup(client)
    old_refresh = client.cookies.get("dpp_refresh")
    assert (await client.post("/api/user/auth/refresh")).status_code == 200

    # Someone replays the old token: treated as theft.
    assert await _post_with_refresh(old_refresh) == 401

    async with SessionLocal() as db:
        live = await db.scalar(select(AuthSession).where(AuthSession.revoked_at.is_(None)))
    assert live is None
    assert (await client.get("/api/user/me")).status_code == 401


async def test_expired_refresh_token(client: AsyncClient) -> None:
    await signup(client)
    token = client.cookies.get("dpp_refresh")
    async with SessionLocal() as db:
        await db.execute(update(AuthSession).values(expires_at=utcnow() - timedelta(seconds=1)))
        await db.commit()
    assert await _post_with_refresh(token) == 401


async def test_logout_ends_the_session(client: AsyncClient) -> None:
    await signup(client)
    refresh_token = client.cookies.get("dpp_refresh")

    resp = await client.post("/api/user/auth/logout")
    assert resp.status_code == 200
    assert not client.cookies.get("dpp_access")
    assert (await client.get("/api/user/me")).status_code == 401
    assert await _post_with_refresh(refresh_token) == 401


async def test_tampered_access_token_is_rejected(raw_client: AsyncClient) -> None:
    resp = await raw_client.get("/api/user/me", headers={"Cookie": "dpp_access=not.a.jwt"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "not_authenticated"
