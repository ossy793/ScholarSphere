from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy import func, select, update

from app.core.clock import utcnow
from app.core.db import SessionLocal
from app.models import AuthSession, EmailOutbox, User
from tests.conftest import PASSWORD, latest_code, make_client, signup

NEW_PASSWORD = "Brand-new pass 2"


async def _reset_emails() -> int:
    async with SessionLocal() as db:
        return await db.scalar(
            select(func.count()).select_from(EmailOutbox).where(EmailOutbox.template == "reset_password")
        )


async def test_forgot_password_answers_the_same_for_unknown_email(client: AsyncClient) -> None:
    resp = await client.post("/api/user/auth/forgot-password", json={"email": "nobody@example.com"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert await _reset_emails() == 0


async def test_full_reset_flow(client: AsyncClient) -> None:
    await signup(client)  # this browser stays logged in on the old password

    async with make_client() as other:
        other.headers["X-CSRF-Token"] = (await other.get("/api/user/auth/csrf")).json()["csrf_token"]
        resp = await other.post("/api/user/auth/forgot-password", json={"email": "ADA@example.com"})
        assert resp.json() == {"ok": True}
        code = await latest_code("ada@example.com", "reset_password")

        resp = await other.post(
            "/api/user/auth/reset-password",
            json={"email": "ada@example.com", "code": code, "new_password": NEW_PASSWORD},
        )
        assert resp.status_code == 200
        assert resp.json()["email_verified"] is True  # the code proved inbox ownership
        assert (await other.get("/api/user/me")).status_code == 200  # logged in

        # Old password no longer works, new one does.
        bad = await other.post("/api/user/auth/login", json={"email": "ada@example.com", "password": PASSWORD})
        assert bad.status_code == 401
        good = await other.post(
            "/api/user/auth/login", json={"email": "ada@example.com", "password": NEW_PASSWORD}
        )
        assert good.status_code == 200

        # The code can't be used twice.
        again = await other.post(
            "/api/user/auth/reset-password",
            json={"email": "ada@example.com", "code": code, "new_password": "Yet-another 3"},
        )
        assert again.json()["error"]["code"] == "code_expired"

    # The other browser was signed out.
    assert (await client.get("/api/user/me")).status_code == 401


async def test_reset_signs_out_every_old_session(client: AsyncClient) -> None:
    await signup(client)
    await client.post("/api/user/auth/forgot-password", json={"email": "ada@example.com"})
    code = await latest_code("ada@example.com", "reset_password")
    await client.post(
        "/api/user/auth/reset-password",
        json={"email": "ada@example.com", "code": code, "new_password": NEW_PASSWORD},
    )
    async with SessionLocal() as db:
        live = await db.scalar(
            select(func.count()).select_from(AuthSession).where(AuthSession.revoked_at.is_(None))
        )
    assert live == 1  # only the session the reset just created


async def test_wrong_code_and_unknown_email_fail_alike(client: AsyncClient) -> None:
    await signup(client)
    await client.post("/api/user/auth/forgot-password", json={"email": "ada@example.com"})
    code = await latest_code("ada@example.com", "reset_password")
    wrong = "000000" if code != "000000" else "111111"

    resp = await client.post(
        "/api/user/auth/reset-password",
        json={"email": "ada@example.com", "code": wrong, "new_password": NEW_PASSWORD},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "code_invalid"

    resp = await client.post(
        "/api/user/auth/reset-password",
        json={"email": "nobody@example.com", "code": wrong, "new_password": NEW_PASSWORD},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "code_expired"


async def test_expired_reset_code(client: AsyncClient) -> None:
    from app.models import UserToken

    await signup(client)
    await client.post("/api/user/auth/forgot-password", json={"email": "ada@example.com"})
    code = await latest_code("ada@example.com", "reset_password")
    async with SessionLocal() as db:
        await db.execute(
            update(UserToken)
            .where(UserToken.purpose == "reset_password")
            .values(expires_at=utcnow() - timedelta(seconds=1))
        )
        await db.commit()
    resp = await client.post(
        "/api/user/auth/reset-password",
        json={"email": "ada@example.com", "code": code, "new_password": NEW_PASSWORD},
    )
    assert resp.json()["error"]["code"] == "code_expired"


async def test_reset_unlocks_a_locked_account(client: AsyncClient) -> None:
    await signup(client)
    async with SessionLocal() as db:
        await db.execute(update(User).values(locked_until=utcnow() + timedelta(minutes=10)))
        await db.commit()

    await client.post("/api/user/auth/forgot-password", json={"email": "ada@example.com"})
    code = await latest_code("ada@example.com", "reset_password")
    resp = await client.post(
        "/api/user/auth/reset-password",
        json={"email": "ada@example.com", "code": code, "new_password": NEW_PASSWORD},
    )
    assert resp.status_code == 200
    login = await client.post(
        "/api/user/auth/login", json={"email": "ada@example.com", "password": NEW_PASSWORD}
    )
    assert login.status_code == 200


async def test_google_only_user_can_set_a_password(client: AsyncClient) -> None:
    async with SessionLocal() as db:
        db.add(User(email="g@example.com", full_name="G User", google_sub="g-1", status="active",
                    email_verified_at=utcnow()))
        await db.commit()

    await client.post("/api/user/auth/forgot-password", json={"email": "g@example.com"})
    code = await latest_code("g@example.com", "reset_password")
    resp = await client.post(
        "/api/user/auth/reset-password",
        json={"email": "g@example.com", "code": code, "new_password": NEW_PASSWORD},
    )
    assert resp.json()["has_password"] is True


async def test_forgot_password_is_limited_per_email(client: AsyncClient, monkeypatch) -> None:
    from app.services import rate_limit

    monkeypatch.setitem(rate_limit.LIMITS, "forgot_password_email", (3, 10**9))
    await signup(client)
    for _ in range(3):
        resp = await client.post("/api/user/auth/forgot-password", json={"email": "ada@example.com"})
        assert resp.status_code == 200
    resp = await client.post("/api/user/auth/forgot-password", json={"email": "ada@example.com"})
    assert resp.status_code == 429
    assert await _reset_emails() == 3


async def test_reset_refuses_a_weak_new_password(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/user/auth/reset-password",
        json={"email": "ada@example.com", "code": "123456", "new_password": "alllowercase"},
    )
    assert resp.status_code == 422
    detail = resp.json()["error"]["details"][0]
    assert detail["field"] == "body.new_password"
    assert detail["message"] == (
        "Password must include an uppercase letter, a number and a symbol such as ! @ # $ or -"
    )


async def test_reset_password_validation(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/user/auth/reset-password",
        json={"email": "ada@example.com", "code": "12ab", "new_password": "short"},
    )
    assert resp.status_code == 422
    fields = {d["field"] for d in resp.json()["error"]["details"]}
    assert fields == {"body.code", "body.new_password"}
