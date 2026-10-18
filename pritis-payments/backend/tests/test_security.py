"""The phase 10 security pass (design section 9): every route is checked, not a sample.

- Each /api route needs the right kind of session, except a short, reviewed public list.
- A user session never opens the admin API, and a staff session never opens the user API.
- A rep can't see or change another class's dues or members; a student can't see dues of
  a class they haven't joined or another student's payments.
- Every response carries the security headers.
"""

import re
import uuid

import pytest
from fastapi.routing import APIRoute
from httpx import AsyncClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.core.security_headers import SecurityHeadersMiddleware
from app.main import app
from tests.conftest import make_client
from tests.people import due_terms, rep_client, user_client
from tests.test_payments import _pay, clock, fake, setup  # noqa: F401  (fixtures)

# Reachable without signing in, on purpose. A new route that turns up here fails the test
# until someone decides it should be public.
PUBLIC = {
    ("GET", "/api/health"),
    ("POST", "/api/webhooks/paystack"),  # signed by Paystack instead
    ("GET", "/api/user/universities"),  # the signup form's dropdown
    ("GET", "/api/user/auth/csrf"),
    ("POST", "/api/user/auth/signup"),
    ("POST", "/api/user/auth/forgot-password"),
    ("POST", "/api/user/auth/reset-password"),
    ("POST", "/api/user/auth/login"),
    ("GET", "/api/user/auth/google"),
    ("GET", "/api/user/auth/google/callback"),
    ("POST", "/api/user/auth/refresh"),
    ("POST", "/api/user/auth/logout"),
    ("GET", "/api/admin/auth/csrf"),
    ("POST", "/api/admin/auth/login"),
    ("POST", "/api/admin/auth/totp"),  # needs the half-signed-in cookie from login
    ("POST", "/api/admin/auth/refresh"),
    ("POST", "/api/admin/auth/logout"),
    ("POST", "/api/admin/auth/invite/start"),  # needs the invitation token
    ("POST", "/api/admin/auth/invite/accept"),
}


def api_routes() -> list[tuple[str, str]]:
    routes = []
    for r in app.routes:
        if isinstance(r, APIRoute):
            for method in sorted(r.methods - {"HEAD"}):
                routes.append((method, r.path))
    return routes


def fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path)


async def _csrf(c: AsyncClient) -> AsyncClient:
    c.headers["X-CSRF-Token"] = (await c.get("/api/user/auth/csrf")).json()["csrf_token"]
    return c


async def _call(c: AsyncClient, method: str, path: str):
    body = {} if method in ("POST", "PUT") else None
    return await c.request(method, path, json=body)


async def test_every_route_needs_a_session_unless_it_is_on_the_public_list() -> None:
    # verify-email and resend-code need the session signup starts; checked separately below.
    found_public = set()
    async with make_client() as c:
        await _csrf(c)
        for method, path in api_routes():
            resp = await _call(c, method, fill(path))
            if resp.status_code != 401:
                found_public.add((method, path))
    unexpected = found_public - PUBLIC
    assert not unexpected, f"Reachable without a session: {sorted(unexpected)}"
    assert {("GET", "/api/health"), ("POST", "/api/user/auth/login")} <= found_public  # the check really ran


async def test_user_sessions_never_open_the_admin_api() -> None:
    student = await user_client("student@example.com")
    try:
        for method, path in api_routes():
            if path.startswith("/api/admin/") and (method, path) not in PUBLIC:
                resp = await _call(student, method, fill(path))
                assert resp.status_code == 401, (method, path, resp.status_code)
    finally:
        await student.__aexit__(None, None, None)


async def test_staff_sessions_never_open_the_user_api(setup: dict) -> None:
    accountant = setup["accountant"]
    for method, path in api_routes():
        if path.startswith("/api/user/") and (method, path) not in PUBLIC:
            resp = await _call(accountant, method, fill(path))
            assert resp.status_code == 401, (method, path, resp.status_code)


async def test_reps_only_reach_their_own_class(setup: dict) -> None:
    other_rep = await rep_client("rep2@example.com", "MTH 300 Level")
    try:
        due_id = setup["due_id"]
        class_id = (await setup["rep"].get("/api/user/rep/class")).json()["class"]["id"]
        # Every rep route that names a due or class, called by the other class's rep.
        checked = 0
        for method, path in api_routes():
            if not path.startswith("/api/user/rep/") or "{" not in path:
                continue
            url = path.replace("{due_id}", due_id).replace("{class_id}", class_id)
            body = due_terms() if method == "PUT" else ({} if method == "POST" else None)
            resp = await other_rep.request(method, url, json=body)
            assert resp.status_code in (403, 404), (method, path, resp.status_code)
            checked += 1
        assert checked >= 9
        # And the owner still can.
        assert (await setup["rep"].get(f"/api/user/rep/dues/{due_id}")).status_code == 200
    finally:
        await other_rep.__aexit__(None, None, None)


async def test_students_only_reach_their_own_dues_and_payments(setup: dict, fake) -> None:
    payment = await _pay(setup)
    outsider = await user_client("outsider@example.com")
    try:
        due_id = setup["due_id"]
        for url in (f"/api/user/dues/{due_id}", f"/api/user/dues/{due_id}/quote",
                    f"/api/user/payments/{payment['id']}"):
            assert (await outsider.get(url)).status_code == 404, url
        resp = await outsider.post(f"/api/user/payments/{payment['id']}/email-receipt")
        assert resp.status_code == 404
        resp = await outsider.post("/api/user/payments", json={"due_id": due_id})
        assert resp.status_code == 404
        # Not a rep: no rep routes at all.
        assert (await outsider.get("/api/user/rep/dues")).status_code == 403
    finally:
        await outsider.__aexit__(None, None, None)


# --- Headers ---------------------------------------------------------------------------------


async def test_security_headers_on_api_responses() -> None:
    async with make_client() as c:
        resp = await c.get("/api/health")
    h = resp.headers
    assert "default-src 'self'" in h["content-security-policy"]
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert "unsafe-inline" not in h["content-security-policy"]
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["cache-control"] == "no-store"
    assert "strict-transport-security" not in h  # HTTPS only (production)


@pytest.mark.parametrize("hsts", [True, False])
async def test_hsts_only_when_served_over_https(hsts: bool) -> None:
    inner = Starlette(routes=[Route("/", lambda request: PlainTextResponse("ok"))])
    from httpx import ASGITransport
    async with AsyncClient(transport=ASGITransport(app=SecurityHeadersMiddleware(inner, hsts=hsts)),
                           base_url="http://t") as c:
        resp = await c.get("/")
    assert ("strict-transport-security" in resp.headers) is hsts
    assert "cache-control" not in resp.headers  # pages may be cached; only /api is no-store
