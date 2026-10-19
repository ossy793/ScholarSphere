import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import AuditLog, EmailOutbox, User
from app.services import rate_limit
from app.services.email import render
from tests.admin_helpers import FakeClock
from tests.people import rep_client, staff_client, user_client


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    from app.services import totp
    c = FakeClock()
    monkeypatch.setattr(totp, "now", c.now)
    return c


@pytest.fixture
async def people(clock):
    student = await user_client("student@example.com")
    rep = await rep_client("rep@example.com", "CSC 200 Level")
    support = await staff_client(clock, "support")
    accountant = await staff_client(clock, "accountant")
    try:
        yield {"student": student, "rep": rep, "support": support, "accountant": accountant}
    finally:
        for c in (student, rep, support, accountant):
            await c.__aexit__(None, None, None)


async def _mail(template: str) -> list[EmailOutbox]:
    async with SessionLocal() as db:
        return list((await db.scalars(
            select(EmailOutbox).where(EmailOutbox.template == template).order_by(EmailOutbox.created_at)
        )).all())


# --- Contact support ---------------------------------------------------------------------------


async def test_student_writes_staff_answer_reply_is_emailed(people: dict) -> None:
    resp = await people["student"].post("/api/user/support", json={
        "subject": "Payment not showing", "message": "I paid for Handout 2026 but it still says unpaid."})
    assert resp.status_code == 201, resp.text
    ticket = resp.json()
    assert ticket["status"] == "open" and ticket["ticket_no"] >= 1001
    assert ticket["email"] == "student@example.com"

    # Support staff are told a message is waiting.
    new = await _mail("support_new")
    assert {m.to_email for m in new} == {"support@pritis.name.ng"}
    subject, _, _ = render("support_new", new[0].payload)
    assert subject == f"Support message #{ticket['ticket_no']}: Payment not showing"

    # It's in the inbox, with who wrote it.
    inbox = (await people["support"].get("/api/admin/support")).json()
    assert inbox["total"] == 1 and inbox["items"][0]["user_name"] and inbox["items"][0]["is_rep"] is False

    # The answer goes to the student's email and the ticket shows as answered.
    resp = await people["support"].post(f"/api/admin/support/{ticket['id']}/reply", json={
        "message": "Thanks. It shows as paid now; Paystack confirmed it a few minutes later."})
    assert resp.status_code == 200 and resp.json()["status"] == "answered"
    replies = await _mail("support_reply")
    assert [m.to_email for m in replies] == ["student@example.com"]
    subject, html, _ = render("support_reply", replies[0].payload)
    assert subject == f"Re: Payment not showing [support #{ticket['ticket_no']}]"
    assert "It shows as paid now" in html

    # The student sees the thread (without staff names) and can answer, which reopens it.
    mine = (await people["student"].get("/api/user/support")).json()
    assert [m["sender"] for m in mine[0]["messages"]] == ["user", "staff"]
    assert mine[0]["messages"][1]["staff_name"] is None
    resp = await people["student"].post(f"/api/user/support/{ticket['id']}/messages",
                                        json={"message": "Thank you, all good now."})
    assert resp.json()["status"] == "open"

    # Reply and close in one step.
    resp = await people["support"].post(f"/api/admin/support/{ticket['id']}/reply",
                                        json={"message": "Glad it's sorted.", "close": True})
    assert resp.json()["status"] == "closed"
    async with SessionLocal() as db:
        assert await db.scalar(select(AuditLog.id).where(AuditLog.action == "support.reply"))


async def test_users_only_see_and_answer_their_own_messages(people: dict) -> None:
    ticket = (await people["student"].post("/api/user/support", json={
        "subject": "Wrong class", "message": "I joined the wrong class by mistake."})).json()
    assert (await people["rep"].get("/api/user/support")).json() == []
    resp = await people["rep"].post(f"/api/user/support/{ticket['id']}/messages", json={"message": "Not mine at all"})
    assert resp.status_code == 404


async def test_only_support_and_super_admin_use_the_inbox(people: dict) -> None:
    assert (await people["accountant"].get("/api/admin/support")).status_code == 403
    assert (await people["accountant"].post("/api/admin/announcements", json={
        "subject": "Hello", "message": "Hello everyone", "audience": "all"})).status_code == 403
    assert (await people["student"].get("/api/admin/support")).status_code == 401


async def test_messages_to_support_are_rate_limited(people: dict, monkeypatch) -> None:
    monkeypatch.setitem(rate_limit.LIMITS, "support_message", (2, 10**9))
    for _ in range(2):
        assert (await people["student"].post("/api/user/support", json={
            "subject": "Help", "message": "Please help me out"})).status_code == 201
    resp = await people["student"].post("/api/user/support", json={"subject": "Help", "message": "Please help me out"})
    assert resp.status_code == 429


# --- Messages to many users ---------------------------------------------------------------------


async def _ids(*emails: str) -> list[str]:
    async with SessionLocal() as db:
        return [str(i) for i in (await db.scalars(select(User.id).where(User.email.in_(emails)))).all()]


@pytest.mark.parametrize("audience, expected", [
    ("all", {"student@example.com", "rep@example.com"}),
    ("students", {"student@example.com"}),
    ("reps", {"rep@example.com"}),
])
async def test_message_to_a_group(people: dict, audience: str, expected: set[str]) -> None:
    count = (await people["support"].post("/api/admin/announcements/count", json={"audience": audience})).json()
    assert count == {"recipients": len(expected)}
    resp = await people["support"].post("/api/admin/announcements", json={
        "subject": "Dues portal maintenance", "message": "The site will be down 10pm to 11pm tonight.\nThanks!",
        "audience": audience})
    assert resp.status_code == 201, resp.text
    assert resp.json()["recipient_count"] == len(expected)
    sent = await _mail("announcement")
    assert {m.to_email for m in sent} == expected
    subject, html, _ = render("announcement", sent[0].payload)
    assert subject == "Dues portal maintenance" and "down 10pm to 11pm" in html
    history = (await people["support"].get("/api/admin/announcements")).json()
    assert history[0]["audience"] == audience


async def test_message_to_chosen_people(people: dict) -> None:
    ids = await _ids("rep@example.com")
    resp = await people["support"].post("/api/admin/announcements", json={
        "subject": "Your payout", "message": "Please check your bank details.", "audience": "selected",
        "user_ids": ids})
    assert resp.json()["recipient_count"] == 1
    assert [m.to_email for m in await _mail("announcement")] == ["rep@example.com"]
    # Choosing nobody is refused.
    resp = await people["support"].post("/api/admin/announcements", json={
        "subject": "Hi", "message": "Hello there", "audience": "selected", "user_ids": []})
    assert resp.status_code == 422


async def test_message_text_is_escaped_in_the_email(people: dict) -> None:
    await people["support"].post("/api/admin/announcements", json={
        "subject": "Note", "message": "<script>alert(1)</script> & more", "audience": "students"})
    _, html, _ = render("announcement", (await _mail("announcement"))[0].payload)
    assert "<script>" not in html and "&lt;script&gt;" in html
