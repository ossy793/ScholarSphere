import hashlib
import hmac
import json
import re
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text, update

from app.core.clock import utcnow
from app.core.db import SessionLocal
from app.models import ClassDue, EmailOutbox, LedgerEntry, Payment, RepWallet, User, WebhookEvent
from app.services import ledger, paystack, totp
from app.services.email import EmailSender, send_pending
from app.services.fees import rep_payout, student_total
from tests.admin_helpers import FakeClock
from tests.people import due_terms, rep_client, staff_client, user_client

SECRET = "sk_test_not_real"  # set in conftest


class FakePaystack(paystack.PaystackClient):
    """Initialize and verify without the network. Tests decide what Paystack reports."""

    def __init__(self) -> None:
        super().__init__(SECRET)
        self.initialized: list[dict] = []
        self.transactions: dict[str, dict] = {}  # reference -> what verify returns

    async def initialize_transaction(self, **kwargs) -> str:
        self.initialized.append(kwargs)
        return f"https://checkout.paystack.com/{kwargs['reference']}"

    async def verify_transaction(self, reference: str):
        return self.transactions.get(reference, {"status": "abandoned", "reference": reference})


def charge_data(payment: dict, *, fees: int = 17_838, amount: int | None = None, **extra) -> dict:
    return {
        # Paystack's transaction id: unique per payment (the webhook dedupes on it).
        "id": int(payment["reference"][4:12], 16), "status": "success", "reference": payment["reference"],
        "amount": amount if amount is not None else payment["total_kobo"], "currency": "NGN",
        "fees": fees, "channel": "card", "paid_at": utcnow().isoformat(), "gateway_response": "Approved",
        **extra,
    }


def signed(payload: dict, secret: str = SECRET) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
    return body, {"x-paystack-signature": sig, "content-type": "application/json"}


async def send_webhook(client: AsyncClient, payload: dict, secret: str = SECRET):
    body, headers = signed(payload, secret)
    return await client.post("/api/webhooks/paystack", content=body, headers=headers)


@pytest.fixture
def fake(monkeypatch) -> FakePaystack:
    f = FakePaystack()
    monkeypatch.setattr(paystack, "get_client", lambda: f)
    return f


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    c = FakeClock()
    monkeypatch.setattr(totp, "now", c.now)
    return c


@pytest.fixture
async def setup(clock: FakeClock) -> AsyncIterator[dict]:
    """A rep with a live ₦5,000 due (3% platform fee) and a student who opened its link."""
    rep = await rep_client("rep@example.com", "CSC 200 Level")
    student = await user_client("student@example.com")
    accountant = await staff_client(clock, "accountant")
    try:
        due = (await rep.post("/api/user/rep/dues", json=due_terms())).json()
        await accountant.post(f"/api/admin/dues/{due['id']}/approve", json={})
        await rep.post(f"/api/user/rep/dues/{due['id']}/live")
        slug = due["share_url"].split("s=")[1]
        assert (await student.post(f"/api/user/dues/by-link/{slug}")).status_code == 200
        yield {"rep": rep, "student": student, "accountant": accountant, "due_id": due["id"]}
    finally:
        for c in (rep, student, accountant):
            await c.__aexit__(None, None, None)


async def _pay(setup: dict, **body) -> dict:
    resp = await setup["student"].post("/api/user/payments", json={"due_id": setup["due_id"], **body})
    assert resp.status_code == 201, resp.text
    async with SessionLocal() as db:
        p = await db.get(Payment, resp.json()["payment_id"])
    return {"id": str(p.id), "reference": p.reference, "total_kobo": p.total_kobo, **resp.json()}


async def _rep_id() -> str:
    async with SessionLocal() as db:
        return str(await db.scalar(select(User.id).where(User.email == "rep@example.com")))


# --- Fees -------------------------------------------------------------------------------


def test_fee_engine_matches_the_design_example() -> None:
    # Design section 8: a ₦5,000 due -> ₦225 service charge (₦75 Paystack, ₦150 platform).
    q = student_total(500_000, __import__("decimal").Decimal("1.5"), __import__("decimal").Decimal("3"))
    assert (q.paystack_part_kobo, q.platform_part_kobo) == (7_500, 15_000)
    assert q.service_charge_kobo == 22_500 and q.total_kobo == 522_500
    # ₦5,000 payout: ₦50 platform fee + ₦10 transfer fee -> ₦4,940 sent.
    p = rep_payout(500_000, __import__("decimal").Decimal("1"), 1_000)
    assert p.net_kobo == 494_000


def test_fees_round_half_up_to_whole_kobo() -> None:
    from decimal import Decimal
    q = student_total(333, Decimal("1.5"), Decimal("3"))  # 4.995 -> 5, 9.99 -> 10
    assert (q.paystack_part_kobo, q.platform_part_kobo) == (5, 10)


async def test_students_see_only_the_total(setup: dict, fake) -> None:
    # Owner's decision: no due / service charge breakdown is sent to students at all.
    q = (await setup["student"].get(f"/api/user/dues/{setup['due_id']}/quote")).json()
    assert q == {"total_kobo": 522_500, "require_matric": False}
    due = (await setup["student"].get(f"/api/user/dues/{setup['due_id']}")).json()
    assert due["total_kobo"] == 522_500
    assert not {"amount_kobo", "service_charge_kobo", "due_kobo"} & due.keys()


# --- The phase 6 "done when" --------------------------------------------------------------


async def test_payment_paid_by_webhook_ledger_balances_and_receipt(setup: dict, fake: FakePaystack) -> None:
    student = setup["student"]
    payment = await _pay(setup)
    assert payment["authorization_url"] == f"https://checkout.paystack.com/{payment['reference']}"
    init = fake.initialized[0]
    assert init["amount_kobo"] == 522_500 and init["email"] == "student@example.com"
    assert init["callback_url"].endswith(f"/payment-callback.html?payment={payment['id']}")
    assert (await student.get(f"/api/user/payments/{payment['id']}")).json()["status"] == "pending"

    # Paystack's signed webhook arrives.
    resp = await send_webhook(student, {"event": "charge.success", "data": charge_data(payment)})
    assert resp.status_code == 200

    shown = (await student.get(f"/api/user/payments/{payment['id']}")).json()
    assert shown["status"] == "success"
    assert shown["total_kobo"] == 522_500 and "service_charge_kobo" not in shown
    assert re.fullmatch(r"R\d{4}-\d{6}", shown["receipt_no"])
    assert shown["channel"] == "card"
    due = (await student.get(f"/api/user/dues/{setup['due_id']}")).json()
    assert due["status"] == "paid" and due["payment_id"] == payment["id"]

    # Ledger: balanced, and the money lands where the design says.
    rep_id = await _rep_id()
    async with SessionLocal() as db:
        assert await ledger.unbalanced_transactions(db) == []
        assert await ledger.balance(db, "paystack_clearing") == 522_500 - 17_838
        assert await ledger.balance(db, f"rep_pending:{rep_id}") == 500_000
        assert await ledger.balance(db, "platform_revenue") == 22_500 - 17_838
        wallet = await db.scalar(select(RepWallet))
        stored = await db.scalar(select(Payment))
        receipt_email = await db.scalar(select(EmailOutbox).where(EmailOutbox.template == "payment_receipt"))
    assert (wallet.pending_kobo, wallet.available_kobo) == (500_000, 0)
    assert stored.paystack_fee_actual_kobo == 17_838
    assert receipt_email.payload["_attachments"] == [{"kind": "receipt", "payment_id": payment["id"]}]

    # Receipts are emailed, never downloaded from the site (owner's decision).
    assert (await student.get(f"/api/user/payments/{payment['id']}/receipt.pdf")).status_code == 404
    resp = await student.post(f"/api/user/payments/{payment['id']}/email-receipt")
    assert resp.json() == {"email": "student@example.com"}
    async with SessionLocal() as db:
        count = await db.scalar(select(func.count()).select_from(EmailOutbox).where(EmailOutbox.template == "payment_receipt"))
    assert count == 2  # the automatic one, plus the resend


async def test_repeated_webhook_changes_nothing(setup: dict, fake: FakePaystack) -> None:
    payment = await _pay(setup)
    payload = {"event": "charge.success", "data": charge_data(payment)}
    for _ in range(3):
        assert (await send_webhook(setup["student"], payload)).status_code == 200

    async with SessionLocal() as db:
        events = await db.scalar(select(func.count()).select_from(WebhookEvent))
        txns = await db.scalar(select(func.count(func.distinct(LedgerEntry.txn_id))))
        wallet = await db.scalar(select(RepWallet))
        emails = await db.scalar(select(func.count()).select_from(EmailOutbox).where(EmailOutbox.template == "payment_receipt"))
    assert (events, txns, wallet.pending_kobo, emails) == (1, 1, 500_000, 1)


async def test_verify_fallback_when_the_webhook_is_late(setup: dict, fake: FakePaystack) -> None:
    payment = await _pay(setup)
    fake.transactions[payment["reference"]] = charge_data(payment)

    # The callback page polls; with no webhook yet, the server asks Paystack itself.
    shown = (await setup["student"].get(f"/api/user/payments/{payment['id']}")).json()
    assert shown["status"] == "success"

    # The webhook arriving afterwards doesn't credit twice.
    await send_webhook(setup["student"], {"event": "charge.success", "data": charge_data(payment)})
    async with SessionLocal() as db:
        wallet = await db.scalar(select(RepWallet))
        assert await db.scalar(select(func.count(func.distinct(LedgerEntry.txn_id)))) == 1
    assert wallet.pending_kobo == 500_000


async def test_paid_but_closed_paystack_early_shows_paid_on_the_dashboard(setup: dict, fake: FakePaystack) -> None:
    # The student paid (e.g. by bank transfer) but closed Paystack's page before being sent
    # back, so the confirming page never ran, and no webhook reached us.
    payment = await _pay(setup)
    fake.transactions[payment["reference"]] = charge_data(payment, channel="bank_transfer")

    dues = (await setup["student"].get("/api/user/dues")).json()
    assert dues[0]["status"] == "paid" and dues[0]["payment_id"] == payment["id"]
    history = (await setup["student"].get("/api/user/payments")).json()
    assert [p["receipt_no"] is not None for p in history] == [True]
    async with SessionLocal() as db:
        assert (await db.scalar(select(RepWallet))).pending_kobo == 500_000


async def test_dashboard_still_loads_if_paystack_is_down(setup: dict, fake: FakePaystack, monkeypatch) -> None:
    await _pay(setup)

    async def down(reference):
        from app.core.errors import AppError
        raise AppError("paystack_unavailable", "down", 502)

    monkeypatch.setattr(fake, "verify_transaction", down)
    dues = (await setup["student"].get("/api/user/dues")).json()
    assert dues[0]["status"] == "unpaid"


async def test_polling_asks_paystack_at_most_every_few_seconds(setup: dict, fake: FakePaystack, monkeypatch) -> None:
    payment = await _pay(setup)
    calls = []
    original = fake.verify_transaction

    async def counting(reference):
        calls.append(reference)
        return await original(reference)

    monkeypatch.setattr(fake, "verify_transaction", counting)
    for _ in range(4):
        await setup["student"].get(f"/api/user/payments/{payment['id']}")
    assert len(calls) == 1


# --- Never trust anything but a correct, signed answer ----------------------------------------


async def test_bad_signature_is_rejected_and_not_stored(setup: dict, fake: FakePaystack) -> None:
    payment = await _pay(setup)
    resp = await send_webhook(setup["student"], {"event": "charge.success", "data": charge_data(payment)},
                              secret="sk_test_wrong")
    assert resp.status_code == 401
    unsigned = await setup["student"].post("/api/webhooks/paystack", json={"event": "charge.success"})
    assert unsigned.status_code == 401
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(WebhookEvent)) == 0
        assert (await db.scalar(select(Payment))).status == "pending"


async def test_wrong_amount_is_never_credited(setup: dict, fake: FakePaystack) -> None:
    payment = await _pay(setup)
    await send_webhook(setup["student"], {"event": "charge.success", "data": charge_data(payment, amount=100)})
    async with SessionLocal() as db:
        event = await db.scalar(select(WebhookEvent))
        stored = await db.scalar(select(Payment))
        entries = await db.scalar(select(func.count()).select_from(LedgerEntry))
    assert event.status == "failed" and "100" in event.error
    assert stored.status == "pending" and entries == 0


async def test_other_paystack_payments_are_ignored(setup: dict, fake: FakePaystack) -> None:
    # e.g. a pritis.name.ng payment on the same Paystack account
    data = {"id": 99, "status": "success", "reference": "SOMETHING-ELSE", "amount": 1000, "currency": "NGN"}
    assert (await send_webhook(setup["student"], {"event": "charge.success", "data": data})).status_code == 200
    async with SessionLocal() as db:
        assert (await db.scalar(select(WebhookEvent))).status == "ignored"


async def test_paystack_fee_above_the_service_charge_comes_from_the_platform(setup: dict, fake: FakePaystack) -> None:
    payment = await _pay(setup)
    await send_webhook(setup["student"], {"event": "charge.success", "data": charge_data(payment, fees=30_000)})
    rep_id = await _rep_id()
    async with SessionLocal() as db:
        assert await ledger.unbalanced_transactions(db) == []
        assert await ledger.balance(db, f"rep_pending:{rep_id}") == 500_000  # rep untouched
        assert await ledger.balance(db, "platform_revenue") == 22_500 - 30_000  # platform absorbs it


# --- Rules for paying --------------------------------------------------------------------


async def test_pressing_pay_twice_reuses_the_checkout(setup: dict, fake: FakePaystack) -> None:
    first = await _pay(setup)
    second = await _pay(setup)
    assert first["id"] == second["id"] and len(fake.initialized) == 1


async def test_cannot_pay_twice(setup: dict, fake: FakePaystack) -> None:
    payment = await _pay(setup)
    await send_webhook(setup["student"], {"event": "charge.success", "data": charge_data(payment)})
    resp = await setup["student"].post("/api/user/payments", json={"due_id": setup["due_id"]})
    assert resp.json()["error"]["code"] == "already_paid"


async def test_closed_due_cannot_be_paid(setup: dict, fake: FakePaystack) -> None:
    async with SessionLocal() as db:
        await db.execute(update(ClassDue).values(deadline=utcnow() - timedelta(minutes=1)))
        await db.commit()
    resp = await setup["student"].post("/api/user/payments", json={"due_id": setup["due_id"]})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "due_closed"
    assert fake.initialized == []


async def test_matric_number_when_the_rep_asks(setup: dict, fake: FakePaystack) -> None:
    async with SessionLocal() as db:
        await db.execute(update(ClassDue).values(require_matric=True))
        await db.commit()
    resp = await setup["student"].post("/api/user/payments", json={"due_id": setup["due_id"]})
    assert resp.status_code == 422
    assert resp.json()["error"]["details"][0]["field"] == "body.matric_no"

    payment = await _pay(setup, matric_no="190404001")
    async with SessionLocal() as db:
        assert (await db.get(Payment, payment["id"])).matric_no == "190404001"


async def test_unverified_student_cannot_pay(setup: dict, fake: FakePaystack) -> None:
    async with SessionLocal() as db:
        await db.execute(update(User).where(User.email == "student@example.com").values(email_verified_at=None))
        await db.commit()
    resp = await setup["student"].post("/api/user/payments", json={"due_id": setup["due_id"]})
    assert resp.json()["error"]["code"] == "email_not_verified"


async def test_non_members_and_other_students(setup: dict, fake: FakePaystack) -> None:
    payment = await _pay(setup)
    outsider = await user_client("outsider@example.com")
    try:
        assert (await outsider.get(f"/api/user/dues/{setup['due_id']}/quote")).status_code == 404
        assert (await outsider.post("/api/user/payments", json={"due_id": setup["due_id"]})).status_code == 404
        assert (await outsider.get(f"/api/user/payments/{payment['id']}")).status_code == 404
        assert (await outsider.post(f"/api/user/payments/{payment['id']}/email-receipt")).status_code == 404
    finally:
        await outsider.__aexit__(None, None, None)


async def test_no_receipt_before_payment_succeeds(setup: dict, fake: FakePaystack) -> None:
    payment = await _pay(setup)
    resp = await setup["student"].post(f"/api/user/payments/{payment['id']}/email-receipt")
    assert resp.status_code == 404


async def test_resending_receipts_is_limited(setup: dict, fake: FakePaystack, monkeypatch) -> None:
    from app.services import rate_limit
    monkeypatch.setitem(rate_limit.LIMITS, "email_receipt", (2, 10**9))
    payment = await _pay(setup)
    await send_webhook(setup["student"], {"event": "charge.success", "data": charge_data(payment)})
    for _ in range(2):
        assert (await setup["student"].post(f"/api/user/payments/{payment['id']}/email-receipt")).status_code == 200
    resp = await setup["student"].post(f"/api/user/payments/{payment['id']}/email-receipt")
    assert resp.status_code == 429


async def test_rep_sees_the_matric_number_given_at_payment(setup: dict, fake: FakePaystack) -> None:
    async with SessionLocal() as db:
        await db.execute(update(ClassDue).values(require_matric=True))
        await db.commit()
    await _pay(setup, matric_no="ACC/2023/045")

    rep = setup["rep"]
    class_id = (await rep.get("/api/user/rep/class")).json()["class"]["id"]
    members = (await rep.get(f"/api/user/rep/classes/{class_id}/members")).json()
    assert [(m["full_name"], m["matric_no"]) for m in members] == [("Ada Obi", "ACC/2023/045")]
    # …and it's saved on the student's profile for next time.
    assert (await setup["student"].get("/api/user/me")).json()["matric_no"] == "ACC/2023/045"


# --- The ledger itself ------------------------------------------------------------------


async def test_ledger_entries_cannot_be_changed_or_deleted(setup: dict, fake: FakePaystack) -> None:
    payment = await _pay(setup)
    await send_webhook(setup["student"], {"event": "charge.success", "data": charge_data(payment)})
    for sql in ("UPDATE ledger_entries SET amount_kobo = 1", "DELETE FROM ledger_entries"):
        async with SessionLocal() as db:
            with pytest.raises(Exception, match="append-only"):
                await db.execute(text(sql))
                await db.commit()


async def test_unbalanced_postings_are_refused() -> None:
    import uuid
    async with SessionLocal() as db:
        with pytest.raises(ledger.LedgerImbalance):
            await ledger.post(db, [ledger.Line("paystack_clearing", "debit", 100),
                                   ledger.Line("platform_revenue", "credit", 99)],
                              ref_type="test", ref_id=uuid.uuid4())


async def test_receipt_email_goes_out_with_the_pdf(setup: dict, fake: FakePaystack) -> None:
    payment = await _pay(setup)
    await send_webhook(setup["student"], {"event": "charge.success", "data": charge_data(payment)})

    sent = []

    class Recorder(EmailSender):
        async def send(self, to, subject, html, text, attachments=()):
            sent.append((to, subject, list(attachments)))

    await send_pending(Recorder())
    receipt = next(s for s in sent if s[1] == "Your payment receipt")
    assert receipt[0] == "student@example.com"
    (attachment,) = receipt[2]
    assert attachment.filename.startswith("receipt-R") and attachment.content.startswith(b"%PDF")


async def test_reps_see_what_students_will_pay(setup: dict) -> None:
    # While creating a due: the student total for any amount (default 1.5% + 3%).
    q = (await setup["rep"].get("/api/user/rep/dues/quote?amount_kobo=500000")).json()
    assert q == {"amount_kobo": 500_000, "service_charge_kobo": 22_500, "total_kobo": 522_500}
    # And on each approved due, the total students actually pay.
    dues = (await setup["rep"].get("/api/user/rep/dues")).json()
    assert dues[0]["student_total_kobo"] == 522_500
    # Students can't use the rep quote.
    assert (await setup["student"].get("/api/user/rep/dues/quote?amount_kobo=500000")).status_code == 403
