from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.cli import create_super_admin
from app.core.clock import utcnow
from app.core.db import SessionLocal
from app.models import AdminInvite, AdminUser, AuthSession
from app.services import rate_limit, totp
from tests.admin_helpers import (
    STAFF_PASSWORD,
    FakeClock,
    accept_invite,
    admin_csrf,
    admin_login,
    token_from_link,
)
from tests.conftest import make_client, signup

BOSS = "boss@pritis.name.ng"


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(totp, "now", fake.now)
    return fake


@pytest.fixture
async def admin_client() -> AsyncIterator[AsyncClient]:
    async with make_client() as c:
        await admin_csrf(c)
        yield c


@pytest.fixture
async def boss(admin_client: AsyncClient, clock: FakeClock) -> str:
    """A logged-in super admin, created the way the first one is: from the command line."""
    link = await create_super_admin(BOSS, "Boss Person")
    secret = await accept_invite(admin_client, clock, link)
    await admin_login(admin_client, clock, BOSS, secret)
    return secret


async def _invite(admin_client: AsyncClient, email: str, role: str) -> str:
    resp = await admin_client.post(
        "/api/admin/admin-users", json={"email": email, "full_name": email.split("@")[0], "role": role}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["admin"]["status"] == "invited"
    return resp.json()["invite_url"]


async def _staff_client(clock: FakeClock, link: str, email: str) -> AsyncClient:
    c = make_client()
    await c.__aenter__()
    await admin_csrf(c)
    secret = await accept_invite(c, clock, link)
    await admin_login(c, clock, email, secret)
    return c


# --- The phase 3 "done when" ------------------------------------------------


async def test_super_admin_invites_staff_who_log_in_with_two_factor(
    admin_client: AsyncClient, boss: str, clock: FakeClock
) -> None:
    me = (await admin_client.get("/api/admin/me")).json()
    assert me["role"] == "super_admin" and me["email"] == BOSS

    accountant_link = await _invite(admin_client, "acct@pritis.name.ng", "accountant")
    support_link = await _invite(admin_client, "help@pritis.name.ng", "support")
    assert accountant_link.startswith("http://admin.test/accept-invite.html?token=")

    accountant = await _staff_client(clock, accountant_link, "acct@pritis.name.ng")
    support = await _staff_client(clock, support_link, "help@pritis.name.ng")
    try:
        assert (await accountant.get("/api/admin/me")).json()["role"] == "accountant"
        assert (await support.get("/api/admin/me")).json()["role"] == "support"

        # Role limits (design section 2): staff accounts, settings and the audit log are
        # super-admin only.
        for c in (accountant, support):
            assert (await c.get("/api/admin/admin-users")).status_code == 403
            assert (await c.post("/api/admin/admin-users", json={
                "email": "x@pritis.name.ng", "full_name": "X Y", "role": "support"})).status_code == 403
            assert (await c.get("/api/admin/settings")).status_code == 403
            assert (await c.put("/api/admin/settings", json={})).status_code in (403, 422)
            assert (await c.get("/api/admin/audit-logs")).status_code == 403

        staff = (await admin_client.get("/api/admin/admin-users")).json()
        assert {s["email"]: s["status"] for s in staff} == {
            BOSS: "active", "acct@pritis.name.ng": "active", "help@pritis.name.ng": "active",
        }
    finally:
        await accountant.__aexit__(None, None, None)
        await support.__aexit__(None, None, None)


async def test_settings_change_appears_in_audit_log(admin_client: AsyncClient, boss: str) -> None:
    current = (await admin_client.get("/api/admin/settings")).json()
    assert current["platform_fee_percent"] == "3"
    assert current["max_due_amount_kobo"] == 20_000_000

    changed = {**current, "payout_fee_percent": "1.25", "max_due_amount_kobo": 25_000_000}
    resp = await admin_client.put("/api/admin/settings", json=changed)
    assert resp.status_code == 200
    assert (await admin_client.get("/api/admin/settings")).json()["payout_fee_percent"] == "1.25"

    logs = (await admin_client.get("/api/admin/audit-logs?action=settings.update")).json()
    assert logs["total"] == 1
    entry = logs["items"][0]
    assert entry["actor_email"] == BOSS
    assert entry["before"] == {"payout_fee_percent": "1", "max_due_amount_kobo": 20_000_000}
    assert entry["after"] == {"payout_fee_percent": "1.25", "max_due_amount_kobo": 25_000_000}

    # Saving the same values again changes nothing and logs nothing.
    await admin_client.put("/api/admin/settings", json=changed)
    logs = (await admin_client.get("/api/admin/audit-logs?action=settings.update")).json()
    assert logs["total"] == 1


@pytest.mark.parametrize(
    ("change", "field_hint"),
    [
        ({"platform_fee_percent": "25"}, "platform_fee_percent"),
        ({"paystack_fee_percent": "1.555"}, "paystack_fee_percent"),
        ({"max_due_amount_kobo": 500}, "max_due_amount_kobo"),
        ({"transfer_fee_bands": [{"up_to_kobo": 500000, "fee_kobo": 1000}]}, "last transfer fee band"),
        ({"transfer_fee_bands": [
            {"up_to_kobo": 5000000, "fee_kobo": 2500},
            {"up_to_kobo": 500000, "fee_kobo": 1000},
            {"up_to_kobo": None, "fee_kobo": 5000}]}, "smallest to largest"),
    ],
)
async def test_settings_validation(
    admin_client: AsyncClient, boss: str, change: dict, field_hint: str
) -> None:
    current = (await admin_client.get("/api/admin/settings")).json()
    resp = await admin_client.put("/api/admin/settings", json={**current, **change})
    assert resp.status_code == 422
    assert field_hint in resp.text


# --- Invitations ------------------------------------------------------------


async def test_invite_link_works_once(admin_client: AsyncClient, clock: FakeClock) -> None:
    link = await create_super_admin(BOSS, "Boss Person")
    await accept_invite(admin_client, clock, link)
    resp = await admin_client.post("/api/admin/auth/invite/start", json={"token": token_from_link(link)})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invite_invalid"


async def test_expired_invite(admin_client: AsyncClient) -> None:
    link = await create_super_admin(BOSS, "Boss Person")
    async with SessionLocal() as db:
        await db.execute(update(AdminInvite).values(expires_at=utcnow() - timedelta(seconds=1)))
        await db.commit()
    resp = await admin_client.post("/api/admin/auth/invite/start", json={"token": token_from_link(link)})
    assert resp.json()["error"]["code"] == "invite_invalid"


async def test_reopening_the_invite_shows_the_same_qr(admin_client: AsyncClient) -> None:
    link = await create_super_admin(BOSS, "Boss Person")
    body = {"token": token_from_link(link)}
    first = (await admin_client.post("/api/admin/auth/invite/start", json=body)).json()
    second = (await admin_client.post("/api/admin/auth/invite/start", json=body)).json()
    assert first["secret"] == second["secret"]
    assert first["qr_svg"].startswith("data:image/svg+xml")
    assert first["otpauth_uri"].startswith("otpauth://totp/Pritis%20DPP:")

    # The secret is stored encrypted, never as the plain value.
    async with SessionLocal() as db:
        stored = await db.scalar(select(AdminUser.totp_secret))
    assert stored != first["secret"]


async def test_accept_needs_a_correct_code_and_strong_password(
    admin_client: AsyncClient, clock: FakeClock
) -> None:
    link = await create_super_admin(BOSS, "Boss Person")
    token = token_from_link(link)
    secret = (await admin_client.post("/api/admin/auth/invite/start", json={"token": token})).json()["secret"]

    resp = await admin_client.post("/api/admin/auth/invite/accept",
                                   json={"token": token, "password": STAFF_PASSWORD, "code": "000000"})
    assert resp.json()["error"]["code"] == "code_invalid"

    resp = await admin_client.post("/api/admin/auth/invite/accept",
                                   json={"token": token, "password": "weakpass", "code": clock.next_code(secret)})
    assert resp.status_code == 422


async def test_resend_invite_cancels_the_old_link(admin_client: AsyncClient, boss: str) -> None:
    old = await _invite(admin_client, "acct@pritis.name.ng", "accountant")
    staff_id = next(
        s["id"] for s in (await admin_client.get("/api/admin/admin-users")).json()
        if s["email"] == "acct@pritis.name.ng"
    )
    new = (await admin_client.post(f"/api/admin/admin-users/{staff_id}/resend-invite")).json()["invite_url"]
    assert new != old
    resp = await admin_client.post("/api/admin/auth/invite/start", json={"token": token_from_link(old)})
    assert resp.json()["error"]["code"] == "invite_invalid"
    resp = await admin_client.post("/api/admin/auth/invite/start", json={"token": token_from_link(new)})
    assert resp.status_code == 200


async def test_duplicate_staff_email(admin_client: AsyncClient, boss: str) -> None:
    resp = await admin_client.post(
        "/api/admin/admin-users", json={"email": BOSS.upper(), "full_name": "Again", "role": "support"}
    )
    assert resp.status_code == 409


# --- Login ------------------------------------------------------------------


async def test_wrong_and_replayed_codes_are_refused(admin_client: AsyncClient, clock: FakeClock) -> None:
    link = await create_super_admin(BOSS, "Boss Person")
    secret = await accept_invite(admin_client, clock, link)

    await admin_client.post("/api/admin/auth/login", json={"email": BOSS, "password": STAFF_PASSWORD})
    resp = await admin_client.post("/api/admin/auth/totp", json={"code": "000000"})
    assert resp.json()["error"]["code"] == "code_invalid"
    assert (await admin_client.get("/api/admin/me")).status_code == 401

    code = clock.next_code(secret)
    assert (await admin_client.post("/api/admin/auth/totp", json={"code": code})).status_code == 200

    # The same code can't be used again, even straight away on another device.
    async with make_client() as other:
        await admin_csrf(other)
        await other.post("/api/admin/auth/login", json={"email": BOSS, "password": STAFF_PASSWORD})
        resp = await other.post("/api/admin/auth/totp", json={"code": code})
        assert resp.json()["error"]["code"] == "code_invalid"


async def test_code_step_needs_the_password_step_first(admin_client: AsyncClient) -> None:
    resp = await admin_client.post("/api/admin/auth/totp", json={"code": "123456"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "mfa_expired"


async def test_password_lockout(admin_client: AsyncClient, clock: FakeClock) -> None:
    link = await create_super_admin(BOSS, "Boss Person")
    await accept_invite(admin_client, clock, link)
    for _ in range(5):
        resp = await admin_client.post("/api/admin/auth/login", json={"email": BOSS, "password": "Wrong-pass 1"})
        assert resp.status_code == 401
    resp = await admin_client.post("/api/admin/auth/login", json={"email": BOSS, "password": STAFF_PASSWORD})
    assert resp.json()["error"]["code"] == "account_locked"


async def test_code_guessing_is_rate_limited(
    admin_client: AsyncClient, clock: FakeClock, monkeypatch
) -> None:
    monkeypatch.setitem(rate_limit.LIMITS, "admin_totp", (5, 10**9))
    link = await create_super_admin(BOSS, "Boss Person")
    await accept_invite(admin_client, clock, link)
    await admin_client.post("/api/admin/auth/login", json={"email": BOSS, "password": STAFF_PASSWORD})
    for _ in range(5):
        await admin_client.post("/api/admin/auth/totp", json={"code": "000000"})
    resp = await admin_client.post("/api/admin/auth/totp", json={"code": "111111"})
    assert resp.status_code == 429


async def test_invited_staff_cannot_log_in_before_accepting(admin_client: AsyncClient) -> None:
    await create_super_admin(BOSS, "Boss Person")
    resp = await admin_client.post("/api/admin/auth/login", json={"email": BOSS, "password": STAFF_PASSWORD})
    assert resp.status_code == 401


async def test_admin_session_does_not_outlive_eight_hours(admin_client: AsyncClient, boss: str) -> None:
    async with SessionLocal() as db:
        first = await db.scalar(select(AuthSession).where(AuthSession.subject_type == "admin"))
    assert (await admin_client.post("/api/admin/auth/refresh")).status_code == 200
    async with SessionLocal() as db:
        live = await db.scalar(
            select(AuthSession).where(AuthSession.subject_type == "admin", AuthSession.revoked_at.is_(None))
        )
    assert live.id != first.id
    assert live.expires_at == first.expires_at  # rotated, but not extended


async def test_admin_logout(admin_client: AsyncClient, boss: str) -> None:
    assert (await admin_client.post("/api/admin/auth/logout")).status_code == 200
    assert (await admin_client.get("/api/admin/me")).status_code == 401


# --- Disabling staff ----------------------------------------------------------


async def test_disabling_staff_signs_them_out(
    admin_client: AsyncClient, boss: str, clock: FakeClock
) -> None:
    link = await _invite(admin_client, "acct@pritis.name.ng", "accountant")
    accountant = await _staff_client(clock, link, "acct@pritis.name.ng")
    try:
        staff_id = (await accountant.get("/api/admin/me")).json()["id"]
        resp = await admin_client.post(f"/api/admin/admin-users/{staff_id}/disable")
        assert resp.json()["status"] == "disabled"
        assert (await accountant.get("/api/admin/me")).status_code == 401

        resp = await admin_client.post(f"/api/admin/admin-users/{staff_id}/enable")
        assert resp.json()["status"] == "active"

        actions = [i["action"] for i in (await admin_client.get("/api/admin/audit-logs")).json()["items"]]
        assert "admin.disable" in actions and "admin.enable" in actions
    finally:
        await accountant.__aexit__(None, None, None)


async def test_super_admin_cannot_disable_themselves(admin_client: AsyncClient, boss: str) -> None:
    me = (await admin_client.get("/api/admin/me")).json()
    resp = await admin_client.post(f"/api/admin/admin-users/{me['id']}/disable")
    assert resp.status_code == 409


# --- Reset authenticator (lost phone) ------------------------------------------


async def test_reset_authenticator_for_a_lost_phone(
    admin_client: AsyncClient, boss: str, clock: FakeClock
) -> None:
    link = await _invite(admin_client, "acct@pritis.name.ng", "accountant")
    accountant = await _staff_client(clock, link, "acct@pritis.name.ng")
    try:
        staff_id = (await accountant.get("/api/admin/me")).json()["id"]
        resp = await admin_client.post(f"/api/admin/admin-users/{staff_id}/reset-authenticator")
        assert resp.status_code == 200
        assert resp.json()["admin"]["status"] == "invited"
        new_link = resp.json()["invite_url"]

        # Signed out at once, and the old password no longer works.
        assert (await accountant.get("/api/admin/me")).status_code == 401
        old_login = await accountant.post(
            "/api/admin/auth/login", json={"email": "acct@pritis.name.ng", "password": STAFF_PASSWORD}
        )
        assert old_login.status_code == 401
    finally:
        await accountant.__aexit__(None, None, None)

    # The new link sets up a new password and a new authenticator.
    async with make_client() as phone:
        await admin_csrf(phone)
        new_secret = await accept_invite(phone, clock, new_link, password="Brand-new 2")
        me = await admin_login(phone, clock, "acct@pritis.name.ng", new_secret, password="Brand-new 2")
        assert me["role"] == "accountant" and me["id"] == staff_id  # same account, history kept

    logs = (await admin_client.get("/api/admin/audit-logs?action=admin.reset_authenticator")).json()
    assert logs["total"] == 1
    assert logs["items"][0]["actor_email"] == BOSS


async def test_reset_email_says_it_is_a_reset(admin_client: AsyncClient, boss: str) -> None:
    from app.models import EmailOutbox
    from app.services.email import render

    link = await _invite(admin_client, "acct@pritis.name.ng", "accountant")
    staff_id = next(
        s["id"] for s in (await admin_client.get("/api/admin/admin-users")).json()
        if s["email"] == "acct@pritis.name.ng"
    )
    await admin_client.post(f"/api/admin/admin-users/{staff_id}/reset-authenticator")
    async with SessionLocal() as db:
        emails = (await db.scalars(
            select(EmailOutbox).where(EmailOutbox.to_email == "acct@pritis.name.ng")
            .order_by(EmailOutbox.created_at)
        )).all()
    _, first_html, first_text = render("staff_invite", emails[0].payload)
    _, reset_html, reset_text = render("staff_invite", emails[-1].payload)
    assert "has invited you" in first_text and "reset" not in first_text
    assert "has reset your" in reset_text and "Set up sign-in again" in reset_html
    assert link not in reset_text  # a new link, not the old one


async def test_reset_authenticator_rules(
    admin_client: AsyncClient, boss: str, clock: FakeClock
) -> None:
    me = (await admin_client.get("/api/admin/me")).json()
    resp = await admin_client.post(f"/api/admin/admin-users/{me['id']}/reset-authenticator")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "cannot_reset_self"

    link = await _invite(admin_client, "help@pritis.name.ng", "support")
    support = await _staff_client(clock, link, "help@pritis.name.ng")
    try:
        # Only the super admin can reset anyone.
        resp = await support.post(f"/api/admin/admin-users/{me['id']}/reset-authenticator")
        assert resp.status_code == 403
        staff_id = (await support.get("/api/admin/me")).json()["id"]
    finally:
        await support.__aexit__(None, None, None)

    # A disabled account must be enabled first.
    await admin_client.post(f"/api/admin/admin-users/{staff_id}/disable")
    resp = await admin_client.post(f"/api/admin/admin-users/{staff_id}/reset-authenticator")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "account_disabled"


async def test_command_line_reset_for_the_only_super_admin(
    admin_client: AsyncClient, boss: str, clock: FakeClock
) -> None:
    from app.cli import reset_authenticator

    link = await reset_authenticator(BOSS)
    assert (await admin_client.get("/api/admin/me")).status_code == 401  # signed out
    async with make_client() as phone:
        await admin_csrf(phone)
        secret = await accept_invite(phone, clock, link, password="Brand-new 2")
        me = await admin_login(phone, clock, BOSS, secret, password="Brand-new 2")
        assert me["role"] == "super_admin"


# --- The two sites stay apart --------------------------------------------------


async def test_user_and_admin_sessions_are_separate(
    client: AsyncClient, admin_client: AsyncClient, boss: str
) -> None:
    await signup(client)
    assert (await client.get("/api/admin/me")).status_code == 401  # student can't reach admin API
    assert (await admin_client.get("/api/user/me")).status_code == 401  # staff aren't users

    # Even an admin access token copied into the user cookie is refused (wrong token type).
    admin_token = admin_client.cookies.get("dpa_access")
    async with make_client() as c:
        resp = await c.get("/api/user/me", headers={"Cookie": f"dpp_access={admin_token}"})
        assert resp.status_code == 401


async def test_admin_api_requires_login(admin_client: AsyncClient) -> None:
    for path in ("/api/admin/me", "/api/admin/admin-users", "/api/admin/settings", "/api/admin/audit-logs"):
        assert (await admin_client.get(path)).status_code == 401
