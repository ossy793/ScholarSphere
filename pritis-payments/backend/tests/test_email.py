import json

import httpx
import pytest
from sqlalchemy import select, update

from app.core.clock import utcnow
from app.core.db import SessionLocal
from app.models import EmailOutbox
from app.services.email import (
    BrevoEmailSender,
    EmailSender,
    EmailSendError,
    queue_email,
    render,
    send_pending,
)


class RecordingSender(EmailSender):
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, str, str, str]] = []
        self.fail = fail

    async def send(self, to: str, subject: str, html: str, text: str, attachments=()) -> None:
        if self.fail:
            raise ConnectionError("provider down")
        self.sent.append((to, subject, html, text))


async def _queue(payload: dict | None = None) -> None:
    async with SessionLocal() as db:
        await queue_email(
            db, "ada@example.com", "verify_email",
            payload or {"full_name": "Ada", "code": "123456", "minutes": 10},
        )
        await db.commit()


async def _row() -> EmailOutbox:
    async with SessionLocal() as db:
        return await db.scalar(select(EmailOutbox))


def _brevo(handler) -> BrevoEmailSender:
    return BrevoEmailSender(
        "test-key", "Pritis DPP <no-reply@pritis.name.ng>",
        transport=httpx.MockTransport(handler),
    )


async def test_brevo_sender_posts_the_email() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"messageId": "<abc@smtp-relay.mailin.fr>"})

    await _brevo(handler).send("ada@example.com", "Hello", "<p>Hi</p>", "Hi")
    request = seen[0]
    assert str(request.url) == "https://api.brevo.com/v3/smtp/email"
    assert request.headers["api-key"] == "test-key"
    body = json.loads(request.content)
    assert body == {
        "sender": {"name": "Pritis DPP", "email": "no-reply@pritis.name.ng"},
        "to": [{"email": "ada@example.com"}],
        "subject": "Hello",
        "htmlContent": "<p>Hi</p>",
        "textContent": "Hi",
    }


async def test_brevo_refusal_is_an_error_the_outbox_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": "invalid_parameter", "message": "sender not valid"})

    with pytest.raises(EmailSendError, match="400.*sender not valid"):
        await _brevo(handler).send("ada@example.com", "Hello", "<p>Hi</p>", "Hi")

    # Through the outbox, the failure is retried later instead of being lost.
    await _queue()
    assert await send_pending(_brevo(handler)) == 0
    row = await _row()
    assert row.status == "pending" and row.attempts == 1


async def test_brevo_unreachable_is_an_error_too() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    with pytest.raises(EmailSendError, match="Could not reach Brevo"):
        await _brevo(handler).send("ada@example.com", "Hello", "<p>Hi</p>", "Hi")


def test_render_escapes_html_but_not_text() -> None:
    subject, html, text = render(
        "verify_email", {"full_name": "<b>Ada</b>", "code": "123456", "minutes": 10}
    )
    assert "verification code" in subject
    assert "123456" in html and "123456" in text
    assert "&lt;b&gt;Ada&lt;/b&gt;" in html
    assert "<b>Ada</b>" in text


async def test_outbox_sends_and_marks_sent() -> None:
    await _queue()
    sender = RecordingSender()
    assert await send_pending(sender) == 1
    assert sender.sent[0][0] == "ada@example.com"
    row = await _row()
    assert row.status == "sent"
    assert row.attempts == 1
    assert await send_pending(sender) == 0  # not sent twice


async def test_outbox_retries_with_backoff_then_gives_up() -> None:
    await _queue()
    sender = RecordingSender(fail=True)
    for attempt in range(1, 6):
        assert await send_pending(sender) == 0
        row = await _row()
        assert row.attempts == attempt
        if attempt < 5:
            assert row.status == "pending"
            assert row.send_after > utcnow()
            # Pretend the backoff has passed.
            async with SessionLocal() as db:
                await db.execute(update(EmailOutbox).values(send_after=utcnow()))
                await db.commit()
    assert (await _row()).status == "failed"
