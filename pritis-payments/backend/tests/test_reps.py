from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.errors import AppError
from app.models import AuditLog, Class, ClassMembership, EmailOutbox, RepApplication, RepBankAccount, User
from app.services import paystack, totp
from app.services.paystack import Bank, PaystackClient
from tests.admin_helpers import FakeClock, admin_csrf, admin_login, make_staff
from tests.conftest import latest_code, make_client, signup, university_id

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
PDF = b"%PDF-1.4\n" + b"0" * 200


class FakePaystack(PaystackClient):
    """Paystack's bank list, account lookup and transfer recipients, without the network."""

    def __init__(self) -> None:
        super().__init__("sk_test_fake")
        self.accounts = {("0123456789", "058"): "ADA NGOZI OBI", ("0987654321", "044"): "BAYO ADE"}
        self.recipients: list[dict] = []
        self.fail_recipient = False

    async def list_banks(self) -> list[Bank]:
        return [Bank("044", "Access Bank"), Bank("058", "Guaranty Trust Bank")]

    async def resolve_account(self, account_number: str, bank_code: str) -> str:
        try:
            return self.accounts[(account_number, bank_code)]
        except KeyError:
            raise AppError("account_not_found", "We couldn't find that account.", 400) from None

    async def create_transfer_recipient(self, *, account_name, account_number, bank_code) -> str:
        if self.fail_recipient:
            raise AppError("paystack_recipient_failed", "Paystack didn't accept the payout account", 502)
        self.recipients.append({"name": account_name, "number": account_number, "bank": bank_code})
        return f"RCP_test{len(self.recipients)}"


@pytest.fixture
def fake_paystack(monkeypatch) -> FakePaystack:
    fake = FakePaystack()
    monkeypatch.setattr(paystack, "get_client", lambda: fake)
    return fake


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(totp, "now", fake.now)
    return fake


async def _verified(client: AsyncClient, email: str = "ada@example.com") -> None:
    await signup(client, email=email)
    code = await latest_code(email)
    assert (await client.post("/api/user/auth/verify-email", json={"code": code})).status_code == 200


async def _apply(client: AsyncClient, proof=("id.png", PNG, "image/png"), **overrides):
    data = {
        "full_name": "Ada Ngozi Obi", "matric_no": "190404001", "phone": "0803 123 4567",
        "university_id": await university_id(), "department": "Computer Science",
        "level": "200", "class_name": "CSC 200 Level", "session": "2025/2026",
        "bank_code": "058", "account_number": "0123456789", "accept_agreement": "on",
        **overrides,
    }
    return await client.post("/api/user/rep/applications", data=data, files={"proof": proof})


@pytest.fixture
async def student() -> AsyncIterator[AsyncClient]:
    async with make_client() as c:
        c.headers["X-CSRF-Token"] = (await c.get("/api/user/auth/csrf")).json()["csrf_token"]
        await _verified(c)
        yield c


async def _staff(clock: FakeClock, role: str) -> AsyncClient:
    email = f"{role}@pritis.name.ng"
    secret = await make_staff(email, role)
    c = make_client()
    await c.__aenter__()
    await admin_csrf(c)
    await admin_login(c, clock, email, secret)
    return c


@pytest.fixture
async def support(clock: FakeClock) -> AsyncIterator[AsyncClient]:
    c = await _staff(clock, "support")
    yield c
    await c.__aexit__(None, None, None)


async def _pending_id(admin: AsyncClient) -> str:
    items = (await admin.get("/api/admin/rep-applications")).json()["items"]
    return items[0]["id"]


# --- Bank lookup ------------------------------------------------------------------


async def test_bank_list_and_account_lookup(student: AsyncClient, fake_paystack) -> None:
    banks = (await student.get("/api/user/banks")).json()
    assert {"code": "058", "name": "Guaranty Trust Bank"} in banks

    resp = await student.post(
        "/api/user/bank-accounts/resolve", json={"bank_code": "058", "account_number": "0123456789"}
    )
    assert resp.json() == {"account_name": "ADA NGOZI OBI", "bank_name": "Guaranty Trust Bank"}

    resp = await student.post(
        "/api/user/bank-accounts/resolve", json={"bank_code": "058", "account_number": "1111111111"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "account_not_found"


async def test_lookup_needs_a_verified_email(client: AsyncClient, fake_paystack) -> None:
    await signup(client)  # not verified
    resp = await client.post(
        "/api/user/bank-accounts/resolve", json={"bank_code": "058", "account_number": "0123456789"}
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "email_not_verified"


# --- Applying ---------------------------------------------------------------------


async def test_apply(student: AsyncClient, fake_paystack) -> None:
    resp = await _apply(student)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["class"]["name"] == "CSC 200 Level"
    assert body["class"]["school"] == "University of Lagos"
    assert body["bank_account"] == {
        "bank_name": "Guaranty Trust Bank", "account_name": "ADA NGOZI OBI",
        "last4": "6789", "verified": False,
    }

    me = (await student.get("/api/user/me")).json()
    assert me["rep_status"] == "pending" and me["is_rep"] is False
    assert me["full_name"] == "Ada Ngozi Obi"

    async with SessionLocal() as db:
        account = await db.scalar(select(RepBankAccount))
        user = await db.scalar(select(User))
        email = await db.scalar(select(EmailOutbox).where(EmailOutbox.template == "rep_application_received"))
    assert "0123456789" not in account.account_number  # encrypted at rest
    assert account.paystack_recipient_code is None  # only created on approval
    assert user.matric_no == "190404001"
    assert email is not None

    # Not a rep until approved.
    resp = await student.get("/api/user/rep/class")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "not_a_rep"

    # One application at a time.
    assert (await _apply(student)).json()["error"]["code"] == "application_pending"


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"proof": ("notes.txt", b"just some text", "text/plain")}, "body.proof"),
        ({"proof": ("fake.png", b"GIF89a" + b"0" * 50, "image/png")}, "body.proof"),  # lies about type
        ({"proof": ("big.png", PNG + b"0" * (5 * 1024 * 1024), "image/png")}, "body.proof"),
        ({"session": "2025/2027"}, "body.session"),
        ({"account_number": "12345"}, "body.account_number"),
        ({"bank_code": "999"}, "body.bank_code"),
        ({"phone": "12345"}, "body.phone"),
    ],
)
async def test_apply_validation(student: AsyncClient, fake_paystack, change: dict, field: str) -> None:
    resp = await _apply(student, **change)
    assert resp.status_code == 422, resp.text
    assert field in {d["field"] for d in resp.json()["error"]["details"]}


async def test_apply_with_an_account_paystack_cannot_find(student: AsyncClient, fake_paystack) -> None:
    resp = await _apply(student, account_number="1111111111")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "account_not_found"
    async with SessionLocal() as db:
        assert await db.scalar(select(RepApplication)) is None


async def test_unverified_user_cannot_apply(client: AsyncClient, fake_paystack) -> None:
    await signup(client)
    assert (await _apply(client)).status_code == 403


# --- Reviewing (the phase 4 "done when") -----------------------------------------------


async def test_apply_review_approve_then_rep_dashboard(
    student: AsyncClient, support: AsyncClient, fake_paystack
) -> None:
    await _apply(student)

    page = (await support.get("/api/admin/rep-applications")).json()
    assert page["total"] == 1
    item = page["items"][0]
    assert item["applicant"]["email"] == "ada@example.com"
    assert item["bank_account"]["last4"] == "6789"  # never the full number

    proof = await support.get(item["proof_url"])
    assert proof.status_code == 200
    assert proof.content == PNG
    assert proof.headers["content-type"] == "image/png"
    assert "sandbox" in proof.headers["content-security-policy"]

    resp = await support.post(f"/api/admin/rep-applications/{item['id']}/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    assert resp.json()["reviewed_by_email"] == "support@pritis.name.ng"
    assert fake_paystack.recipients == [{"name": "ADA NGOZI OBI", "number": "0123456789", "bank": "058"}]

    # The user is now a rep and sees the rep dashboard.
    me = (await student.get("/api/user/me")).json()
    assert me["is_rep"] is True and me["rep_status"] == "approved"
    rep = (await student.get("/api/user/rep/class")).json()
    assert rep["class"]["name"] == "CSC 200 Level"
    assert len(rep["invite_code"]) == 8
    assert rep["bank_account"]["verified"] is True

    async with SessionLocal() as db:
        account = await db.scalar(select(RepBankAccount))
        approved_email = await db.scalar(
            select(EmailOutbox).where(EmailOutbox.template == "rep_application_approved"))
        log = await db.scalar(select(AuditLog).where(AuditLog.action == "rep_application.approve"))
    assert account.paystack_recipient_code == "RCP_test1"
    assert approved_email.payload["last4"] == "6789"
    assert log is not None

    # Can't be decided twice.
    again = await support.post(f"/api/admin/rep-applications/{item['id']}/approve")
    assert again.json()["error"]["code"] == "already_reviewed"


async def test_reject_then_apply_again(
    student: AsyncClient, support: AsyncClient, fake_paystack
) -> None:
    await _apply(student)
    app_id = await _pending_id(support)

    resp = await support.post(f"/api/admin/rep-applications/{app_id}/reject",
                              json={"reason": "The ID photo is too blurry to read."})
    assert resp.json()["status"] == "rejected"

    mine = (await student.get("/api/user/rep/application")).json()
    assert mine["status"] == "rejected"
    assert mine["reject_reason"] == "The ID photo is too blurry to read."
    assert (await student.get("/api/user/me")).json()["rep_status"] == "rejected"

    resp = await _apply(student, class_name="CSC 200L (2025/2026)")
    assert resp.status_code == 201
    assert resp.json()["status"] == "pending"
    async with SessionLocal() as db:
        classes = (await db.scalars(select(Class))).all()
    assert len(classes) == 1  # the same class, updated
    assert classes[0].name == "CSC 200L (2025/2026)"


async def test_reject_needs_a_reason(student: AsyncClient, support: AsyncClient, fake_paystack) -> None:
    await _apply(student)
    app_id = await _pending_id(support)
    resp = await support.post(f"/api/admin/rep-applications/{app_id}/reject", json={"reason": ""})
    assert resp.status_code == 422


async def test_failed_payout_recipient_keeps_application_pending(
    student: AsyncClient, support: AsyncClient, fake_paystack
) -> None:
    await _apply(student)
    app_id = await _pending_id(support)
    fake_paystack.fail_recipient = True
    resp = await support.post(f"/api/admin/rep-applications/{app_id}/approve")
    assert resp.status_code == 502
    assert (await student.get("/api/user/me")).json()["rep_status"] == "pending"


async def test_accountant_can_verify_reps_too(
    student: AsyncClient, clock: FakeClock, fake_paystack
) -> None:
    # Owner's decision (2026-10-17): the accountant reviews rep applications as well.
    await _apply(student)
    accountant = await _staff(clock, "accountant")
    try:
        page = (await accountant.get("/api/admin/rep-applications")).json()
        app_id = page["items"][0]["id"]
        assert (await accountant.get(f"/api/admin/rep-applications/{app_id}/proof")).status_code == 200
        resp = await accountant.post(f"/api/admin/rep-applications/{app_id}/approve")
        assert resp.json()["reviewed_by_email"] == "accountant@pritis.name.ng"
    finally:
        await accountant.__aexit__(None, None, None)


async def test_students_cannot_reach_the_review_api(student: AsyncClient, fake_paystack) -> None:
    await _apply(student)
    async with SessionLocal() as db:
        app_id = await db.scalar(select(RepApplication.id))
    assert (await student.get("/api/admin/rep-applications")).status_code == 401
    assert (await student.post(f"/api/admin/rep-applications/{app_id}/approve")).status_code == 401


async def test_pdf_proof_is_downloaded_not_shown(
    student: AsyncClient, support: AsyncClient, fake_paystack
) -> None:
    await _apply(student, proof=("id.pdf", PDF, "application/pdf"))
    item = (await support.get("/api/admin/rep-applications")).json()["items"][0]
    resp = await support.get(item["proof_url"])
    assert resp.content == PDF
    assert resp.headers["content-disposition"].startswith("attachment")


# --- Ownership: one rep can never see another class --------------------------------


async def test_one_rep_cannot_see_another_class(support: AsyncClient, fake_paystack) -> None:
    async with make_client() as rep_a, make_client() as rep_b, make_client() as plain:
        for c, email in ((rep_a, "a@example.com"), (rep_b, "b@example.com"), (plain, "c@example.com")):
            c.headers["X-CSRF-Token"] = (await c.get("/api/user/auth/csrf")).json()["csrf_token"]
            await _verified(c, email)
        await _apply(rep_a, class_name="Class A")
        await _apply(rep_b, class_name="Class B", bank_code="044", account_number="0987654321")
        for item in (await support.get("/api/admin/rep-applications")).json()["items"]:
            assert (await support.post(f"/api/admin/rep-applications/{item['id']}/approve")).status_code == 200

        class_a = (await rep_a.get("/api/user/rep/class")).json()["class"]
        class_b = (await rep_b.get("/api/user/rep/class")).json()["class"]
        assert class_a["name"] == "Class A" and class_b["name"] == "Class B"

        # A student joins class B.
        async with SessionLocal() as db:
            plain_id = await db.scalar(select(User.id).where(User.email == "c@example.com"))
            db.add(ClassMembership(user_id=plain_id, class_id=class_b["id"]))
            await db.commit()

        own = await rep_b.get(f"/api/user/rep/classes/{class_b['id']}/members")
        assert [m["full_name"] for m in own.json()] == ["Ada Obi"]

        # Rep A asking for class B's members gets "not found", as if it didn't exist.
        other = await rep_a.get(f"/api/user/rep/classes/{class_b['id']}/members")
        assert other.status_code == 404
        assert (await rep_a.get(f"/api/user/rep/classes/{class_a['id']}/members")).json() == []

        # A plain student can't use rep routes at all.
        assert (await plain.get(f"/api/user/rep/classes/{class_b['id']}/members")).status_code == 403
        assert (await plain.get("/api/user/rep/class")).status_code == 403


async def test_applying_needs_the_rep_agreement_and_records_it(student: AsyncClient, fake_paystack) -> None:
    from app.core.legal import REP_AGREEMENT_VERSION
    from app.models import RepApplication
    resp = await _apply(student, accept_agreement="")
    assert resp.status_code == 422
    assert resp.json()["error"]["details"][0]["field"].endswith("accept_agreement")
    assert (await _apply(student)).status_code == 201
    async with SessionLocal() as db:
        app_row = await db.scalar(select(RepApplication))
    assert app_row.agreement_accepted_at is not None and app_row.agreement_version == REP_AGREEMENT_VERSION
