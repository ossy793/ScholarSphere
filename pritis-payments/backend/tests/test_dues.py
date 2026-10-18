from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core import crypto
from app.core.clock import utcnow
from app.core.db import SessionLocal
from app.core.errors import AppError
from app.models import (
    AuditLog,
    Class,
    ClassDue,
    ClassMembership,
    EmailOutbox,
    RepApplication,
    RepBankAccount,
    User,
)
from app.services import dues as dues_service
from app.services import totp
from tests.admin_helpers import FakeClock, admin_csrf, admin_login, make_staff
from tests.conftest import latest_code, make_client, signup


def future(days: int = 7) -> str:
    return (utcnow() + timedelta(days=days)).isoformat()


def terms(**overrides) -> dict:
    return {
        "title": "Handout 2026", "description": "Course handouts for first semester",
        "amount_kobo": 500_000, "deadline": future(), "require_matric": True, **overrides,
    }


async def _user_client(email: str) -> AsyncClient:
    c = make_client()
    await c.__aenter__()
    c.headers["X-CSRF-Token"] = (await c.get("/api/user/auth/csrf")).json()["csrf_token"]
    await signup(c, email=email)
    code = await latest_code(email)
    assert (await c.post("/api/user/auth/verify-email", json={"code": code})).status_code == 200
    return c


async def _make_rep(email: str, class_name: str) -> AsyncClient:
    """A verified rep, set up directly (phase 4 covers the application itself)."""
    c = await _user_client(email)
    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == email))
        klass = Class(name=class_name, school="University of Lagos", department="Computer Science",
                      level="200", session="2025/2026", rep_user_id=user.id,
                      invite_code=class_name[:8].upper().replace(" ", "X").ljust(8, "X"))
        db.add(klass)
        await db.flush()
        db.add(RepApplication(user_id=user.id, class_id=klass.id, proof_file="x", proof_content_type="image/png",
                              status="approved"))
        db.add(RepBankAccount(user_id=user.id, bank_code="058", bank_name="GTBank",
                              account_number=crypto.encrypt("0123456789"), account_name="ADA OBI",
                              paystack_recipient_code="RCP_x", verified_at=utcnow()))
        await db.commit()
    return c


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(totp, "now", fake.now)
    return fake


async def _staff(clock: FakeClock, role: str) -> AsyncClient:
    email = f"{role}@pritis.name.ng"
    secret = await make_staff(email, role)
    c = make_client()
    await c.__aenter__()
    await admin_csrf(c)
    await admin_login(c, clock, email, secret)
    return c


@pytest.fixture
async def rep() -> AsyncIterator[AsyncClient]:
    c = await _make_rep("rep@example.com", "CSC 200 Level")
    yield c
    await c.__aexit__(None, None, None)


@pytest.fixture
async def student() -> AsyncIterator[AsyncClient]:
    c = await _user_client("student@example.com")
    yield c
    await c.__aexit__(None, None, None)


@pytest.fixture
async def accountant(clock: FakeClock) -> AsyncIterator[AsyncClient]:
    c = await _staff(clock, "accountant")
    yield c
    await c.__aexit__(None, None, None)


@pytest.fixture
async def boss(clock: FakeClock) -> AsyncIterator[AsyncClient]:
    c = await _staff(clock, "super_admin")
    yield c
    await c.__aexit__(None, None, None)


def slug_of(due: dict) -> str:
    return due["share_url"].split("s=")[1]


# --- The whole lifecycle (design section 4 diagram) ------------------------------------------


async def test_due_lifecycle_end_to_end(rep: AsyncClient, student: AsyncClient, accountant: AsyncClient) -> None:
    # Draft
    due = (await rep.post("/api/user/rep/dues", json={**terms(), "submit": False})).json()
    assert due["phase"] == "draft" and due["live_terms"] is None
    due_id = due["id"]
    assert (await accountant.get("/api/admin/dues")).json()["total"] == 0  # drafts aren't in the queue

    # Draft -> Pending
    due = (await rep.post(f"/api/user/rep/dues/{due_id}/submit")).json()
    assert due["phase"] == "pending"
    queue = (await accountant.get("/api/admin/dues")).json()
    assert queue["total"] == 1 and queue["items"][0]["kind"] == "new"
    assert queue["items"][0]["suggested_fee_percent"] == "3.00" or queue["items"][0]["suggested_fee_percent"] == "3"

    # Pending -> Rejected (reason shown to the rep)
    resp = await accountant.post(f"/api/admin/dues/{due_id}/reject", json={"reason": "Add the course code to the title."})
    assert resp.json()["status"] == "rejected"
    due = (await rep.get(f"/api/user/rep/dues/{due_id}")).json()
    assert due["phase"] == "rejected"
    assert due["latest_revision"]["reject_reason"] == "Add the course code to the title."

    # Rejected -> rep revises -> Draft -> Pending
    due = (await rep.put(f"/api/user/rep/dues/{due_id}", json=terms(title="CSC201 Handout 2026"))).json()
    assert due["phase"] == "draft"
    await rep.post(f"/api/user/rep/dues/{due_id}/submit")

    # Pending -> Approved, with an adjusted platform fee
    resp = await accountant.post(f"/api/admin/dues/{due_id}/approve", json={"platform_fee_percent": "2.5"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["approved_fee_percent"] in ("2.5", "2.50")
    due = (await rep.get(f"/api/user/rep/dues/{due_id}")).json()
    assert due["phase"] == "approved" and due["live_terms"]["title"] == "CSC201 Handout 2026"

    # Approved but not live: students can't see it, and the link only joins the class.
    slug = slug_of(due)
    resp = await student.post(f"/api/user/dues/by-link/{slug}")
    assert resp.json()["error"]["code"] == "due_paused"
    assert (await student.get("/api/user/dues")).json() == []

    # Approved -> Live
    due = (await rep.post(f"/api/user/rep/dues/{due_id}/live")).json()
    assert due["phase"] == "live"
    seen = (await student.post(f"/api/user/dues/by-link/{slug}")).json()
    # Students see only the total: ₦5,000 + 1.5% Paystack part + 2.5% platform fee = ₦5,200.
    assert seen["title"] == "CSC201 Handout 2026" and seen["total_kobo"] == 520_000
    assert "amount_kobo" not in seen
    assert seen["status"] == "unpaid" and seen["class_name"] == "CSC 200 Level"
    assert [d["id"] for d in (await student.get("/api/user/dues")).json()] == [due_id]

    # Live -> rep edits -> Edit pending: the OLD terms stay live
    due = (await rep.put(f"/api/user/rep/dues/{due_id}", json=terms(title="CSC201 Handout 2026", amount_kobo=600_000))).json()
    assert due["phase"] == "edit_pending"
    assert due["live_terms"]["amount_kobo"] == 500_000
    assert due["open_revision"]["terms"]["amount_kobo"] == 600_000
    assert (await student.get(f"/api/user/dues/{due_id}")).json()["total_kobo"] == 520_000
    item = (await accountant.get("/api/admin/dues")).json()["items"][0]
    assert item["kind"] == "edit"
    assert item["current"]["amount_kobo"] == 500_000 and item["proposed"]["amount_kobo"] == 600_000

    # No second change while one is waiting.
    resp = await rep.put(f"/api/user/rep/dues/{due_id}", json=terms(amount_kobo=700_000))
    assert resp.json()["error"]["code"] == "awaiting_approval"

    # Edit approved -> new terms live, fee kept
    await accountant.post(f"/api/admin/dues/{due_id}/approve", json={})
    assert (await student.get(f"/api/user/dues/{due_id}")).json()["total_kobo"] == 624_000  # ₦6,000 at 4%
    async with SessionLocal() as db:
        stored = await db.get(ClassDue, due_id)
    assert str(stored.platform_fee_percent) == "2.50"

    # Live -> pause hides it from students
    assert (await rep.post(f"/api/user/rep/dues/{due_id}/pause")).json()["phase"] == "approved"
    assert (await student.get("/api/user/dues")).json() == []


async def test_rejected_edit_keeps_the_old_terms(rep: AsyncClient, student: AsyncClient, accountant: AsyncClient) -> None:
    due_id = (await rep.post("/api/user/rep/dues", json=terms())).json()["id"]
    await accountant.post(f"/api/admin/dues/{due_id}/approve", json={})
    await rep.post(f"/api/user/rep/dues/{due_id}/live")

    await rep.put(f"/api/user/rep/dues/{due_id}", json=terms(amount_kobo=900_000))
    await accountant.post(f"/api/admin/dues/{due_id}/reject", json={"reason": "That's too much for handouts."})

    due = (await rep.get(f"/api/user/rep/dues/{due_id}")).json()
    assert due["phase"] == "live"
    assert due["live_terms"]["amount_kobo"] == 500_000
    assert due["latest_revision"]["status"] == "rejected"
    assert due["latest_revision"]["reject_reason"] == "That's too much for handouts."


async def test_editing_with_no_changes_is_refused(rep: AsyncClient, accountant: AsyncClient) -> None:
    body = terms()
    due_id = (await rep.post("/api/user/rep/dues", json=body)).json()["id"]
    await accountant.post(f"/api/admin/dues/{due_id}/approve", json={})
    resp = await rep.put(f"/api/user/rep/dues/{due_id}", json=body)
    assert resp.json()["error"]["code"] == "no_changes"


async def test_no_edits_while_first_approval_is_pending(rep: AsyncClient) -> None:
    due_id = (await rep.post("/api/user/rep/dues", json=terms())).json()["id"]
    resp = await rep.put(f"/api/user/rep/dues/{due_id}", json=terms(title="Something else"))
    assert resp.json()["error"]["code"] == "awaiting_approval"


# --- Deadlines: closed dues and extensions ------------------------------------------------


async def test_closed_due_cannot_be_paid(rep: AsyncClient, student: AsyncClient, accountant: AsyncClient) -> None:
    due = (await rep.post("/api/user/rep/dues", json=terms())).json()
    await accountant.post(f"/api/admin/dues/{due['id']}/approve", json={})
    await rep.post(f"/api/user/rep/dues/{due['id']}/live")
    await student.post(f"/api/user/dues/by-link/{slug_of(due)}")

    async with SessionLocal() as db:
        await db.execute(update(ClassDue).values(deadline=utcnow() - timedelta(minutes=1)))
        await db.commit()
        stored = await db.get(ClassDue, due["id"])

    # Students see "Due closed"…
    assert (await student.get(f"/api/user/dues/{due['id']}")).json()["status"] == "closed"
    assert (await rep.get(f"/api/user/rep/dues/{due['id']}")).json()["closed"] is True
    # …and the server refuses payment (the check every payment will go through).
    with pytest.raises(AppError) as exc:
        dues_service.ensure_payable(stored)
    assert exc.value.code == "due_closed"


async def test_payable_only_when_approved_and_live(rep: AsyncClient, accountant: AsyncClient) -> None:
    due = (await rep.post("/api/user/rep/dues", json=terms())).json()
    async with SessionLocal() as db:
        stored = await db.get(ClassDue, due["id"])
    with pytest.raises(AppError, match="isn't open"):
        dues_service.ensure_payable(stored)  # pending

    await accountant.post(f"/api/admin/dues/{due['id']}/approve", json={})
    await rep.post(f"/api/user/rep/dues/{due['id']}/live")
    async with SessionLocal() as db:
        stored = await db.get(ClassDue, due["id"])
    dues_service.ensure_payable(stored)  # no error


async def test_extension_is_a_revision(rep: AsyncClient, student: AsyncClient, accountant: AsyncClient) -> None:
    body = terms()
    due = (await rep.post("/api/user/rep/dues", json=body)).json()
    await accountant.post(f"/api/admin/dues/{due['id']}/approve", json={})
    await rep.post(f"/api/user/rep/dues/{due['id']}/live")
    await student.post(f"/api/user/dues/by-link/{slug_of(due)}")
    async with SessionLocal() as db:
        await db.execute(update(ClassDue).values(deadline=utcnow() - timedelta(minutes=1)))
        await db.commit()

    # Extending: an edit of the deadline, waiting for approval like any other.
    resp = await rep.put(f"/api/user/rep/dues/{due['id']}", json={**body, "deadline": future(14)})
    assert resp.json()["phase"] == "edit_pending"
    assert (await student.get(f"/api/user/dues/{due['id']}")).json()["status"] == "closed"

    await accountant.post(f"/api/admin/dues/{due['id']}/approve", json={})
    assert (await student.get(f"/api/user/dues/{due['id']}")).json()["status"] == "unpaid"


async def test_deadline_must_be_in_the_future(rep: AsyncClient) -> None:
    resp = await rep.post("/api/user/rep/dues", json=terms(deadline=(utcnow() - timedelta(hours=1)).isoformat()))
    assert resp.status_code == 422
    assert resp.json()["error"]["details"][0]["field"] == "body.deadline"


@pytest.mark.parametrize(
    "bad", [{"amount_kobo": 5_000}, {"title": "x"}, {"deadline": "2026-12-01T10:00:00"}, {"amount_kobo": 12.5}]
)
async def test_due_validation(rep: AsyncClient, bad: dict) -> None:
    assert (await rep.post("/api/user/rep/dues", json=terms(**bad))).status_code == 422


# --- Approval rules ----------------------------------------------------------------------


async def test_above_maximum_only_super_admin_approves(
    rep: AsyncClient, accountant: AsyncClient, boss: AsyncClient
) -> None:
    due_id = (await rep.post("/api/user/rep/dues", json=terms(amount_kobo=25_000_000))).json()["id"]  # ₦250,000
    item = (await accountant.get("/api/admin/dues")).json()["items"][0]
    assert item["needs_super_admin"] is True

    resp = await accountant.post(f"/api/admin/dues/{due_id}/approve", json={})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "super_admin_required"

    assert (await boss.post(f"/api/admin/dues/{due_id}/approve", json={})).status_code == 200


async def test_support_cannot_approve_dues(rep: AsyncClient, clock: FakeClock) -> None:
    due_id = (await rep.post("/api/user/rep/dues", json=terms())).json()["id"]
    support = await _staff(clock, "support")
    try:
        assert (await support.get("/api/admin/dues")).status_code == 403
        assert (await support.post(f"/api/admin/dues/{due_id}/approve", json={})).status_code == 403
    finally:
        await support.__aexit__(None, None, None)


async def test_emails_and_audit(rep: AsyncClient, accountant: AsyncClient) -> None:
    due_id = (await rep.post("/api/user/rep/dues", json=terms())).json()["id"]
    await accountant.post(f"/api/admin/dues/{due_id}/approve", json={"platform_fee_percent": "4"})
    async with SessionLocal() as db:
        submitted = (await db.scalars(select(EmailOutbox).where(EmailOutbox.template == "due_submitted"))).all()
        decided = await db.scalar(select(EmailOutbox).where(EmailOutbox.template == "due_decided"))
        log = await db.scalar(select(AuditLog).where(AuditLog.action == "due.approve"))
    assert [e.to_email for e in submitted] == ["accountant@pritis.name.ng"]
    assert decided.to_email == "rep@example.com" and decided.payload["approved"] is True
    assert log.after["platform_fee_percent"] == "4"


# --- Ownership and students -----------------------------------------------------------------


async def test_rep_cannot_touch_another_class_due(rep: AsyncClient, accountant: AsyncClient) -> None:
    other = await _make_rep("other@example.com", "MTH 300 Level")
    try:
        due_id = (await rep.post("/api/user/rep/dues", json=terms())).json()["id"]
        await accountant.post(f"/api/admin/dues/{due_id}/approve", json={})
        for method, path, body in [
            ("GET", f"/api/user/rep/dues/{due_id}", None),
            ("PUT", f"/api/user/rep/dues/{due_id}", terms(title="Hijacked")),
            ("POST", f"/api/user/rep/dues/{due_id}/live", None),
            ("POST", f"/api/user/rep/dues/{due_id}/pause", None),
            ("POST", f"/api/user/rep/dues/{due_id}/submit", None),
        ]:
            resp = await other.request(method, path, json=body)
            assert resp.status_code == 404, (method, path)
        assert (await other.get("/api/user/rep/dues")).json() == []
    finally:
        await other.__aexit__(None, None, None)


async def test_students_cannot_create_dues(student: AsyncClient) -> None:
    resp = await student.post("/api/user/rep/dues", json=terms())
    assert resp.status_code == 403


async def test_draft_link_does_not_work_and_non_members_see_nothing(
    rep: AsyncClient, student: AsyncClient, accountant: AsyncClient
) -> None:
    draft = (await rep.post("/api/user/rep/dues", json={**terms(), "submit": False})).json()
    assert (await student.post(f"/api/user/dues/by-link/{slug_of(draft)}")).status_code == 404

    live = (await rep.post("/api/user/rep/dues", json=terms(title="Lab coat"))).json()
    await accountant.post(f"/api/admin/dues/{live['id']}/approve", json={})
    await rep.post(f"/api/user/rep/dues/{live['id']}/live")
    # Not a member of the class yet: can't read it by id.
    assert (await student.get(f"/api/user/dues/{live['id']}")).status_code == 404
    # Opening the link joins the class (once, however often it's opened).
    await student.post(f"/api/user/dues/by-link/{slug_of(live)}")
    await student.post(f"/api/user/dues/by-link/{slug_of(live).lower()}")
    async with SessionLocal() as db:
        memberships = (await db.scalars(select(ClassMembership))).all()
    assert len(memberships) == 1
    assert (await student.get(f"/api/user/dues/{live['id']}")).status_code == 200
    me = (await student.get("/api/user/me")).json()
    assert [m["class_name"] for m in me["memberships"]] == ["CSC 200 Level"]
