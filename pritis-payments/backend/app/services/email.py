"""Email: queue in email_outbox inside the caller's transaction; send later from the worker.

Sending sits behind EmailSender, picked by EMAIL_BACKEND: "console" prints emails to the
log (development), "brevo" sends them through Brevo. Another provider is one more class.
"""

import base64
import logging
import uuid
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from email.utils import parseaddr
from pathlib import Path
from typing import Any

import httpx
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import EmailOutbox

log = logging.getLogger("app.email")

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates" / "email"
SUBJECTS = {
    "verify_email": "Your Pritis Class Dues verification code",
    "reset_password": "Your Pritis Class Dues password reset code",
    "staff_invite": "You're invited to the Pritis Class Dues admin site",
    "rep_application_received": "We've received your class rep application",
    "rep_application_approved": "You're now a verified class rep",
    "rep_application_rejected": "Your class rep application needs changes",
    "due_submitted": "A due is waiting for approval",
    "due_decided": "Your due has been reviewed",
    "payment_receipt": "Your payment receipt",
    "reconciliation_alert": "Pritis nightly check found a problem",
    "payout_requested": "A rep has requested a payout",
    "payout_update": "Update on your payout",
    "bank_change_requested": "A rep wants to change their payout account",
    "bank_change_decided": "Your payout account change",
    "refund_update": "Update on a refund",
    "refund_staff": "Pritis Class Dues refund",
    "payment_reminder": "Reminder: your class due closes soon",
}
MAX_ATTEMPTS = 5

_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html"]),
    undefined=StrictUndefined,
)


@dataclass(frozen=True)
class Attachment:
    filename: str
    content: bytes
    content_type: str


class EmailSender(ABC):
    @abstractmethod
    async def send(
        self, to: str, subject: str, html: str, text: str, attachments: Sequence[Attachment] = ()
    ) -> None: ...


class ConsoleEmailSender(EmailSender):
    """Development sender: writes the email to the log instead of sending it."""

    async def send(
        self, to: str, subject: str, html: str, text: str, attachments: Sequence[Attachment] = ()
    ) -> None:
        files = ", ".join(f"{a.filename} ({len(a.content)} bytes)" for a in attachments)
        log.info("EMAIL to=%s subject=%r%s\n%s", to, subject, f" attachments: {files}" if files else "", text)


class EmailSendError(Exception):
    """The provider refused or failed to take the email. The outbox retries with backoff."""


class BrevoEmailSender(EmailSender):
    """Sends through Brevo's transactional email API (https://api.brevo.com/v3/smtp/email).

    The sender address must be a verified sender or on a domain authenticated in Brevo.
    """

    API_URL = "https://api.brevo.com/v3/smtp/email"

    def __init__(
        self, api_key: str, from_address: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        name, email = parseaddr(from_address)
        if not email:
            raise RuntimeError("EMAIL_FROM must be an address, e.g. 'Pritis <no-reply@pritis.name.ng>'")
        self.api_key = api_key
        self.sender = {"name": name or "Pritis Class Dues", "email": email}
        self.transport = transport  # tests pass a mock transport

    async def send(
        self, to: str, subject: str, html: str, text: str, attachments: Sequence[Attachment] = ()
    ) -> None:
        body: dict[str, Any] = {
            "sender": self.sender,
            "to": [{"email": to}],
            "subject": subject,
            "htmlContent": html,
            "textContent": text,
        }
        if attachments:
            body["attachment"] = [
                {"name": a.filename, "content": base64.b64encode(a.content).decode()} for a in attachments
            ]
        headers = {"api-key": self.api_key, "accept": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=20, transport=self.transport) as client:
                resp = await client.post(self.API_URL, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise EmailSendError(f"Could not reach Brevo: {exc!r}") from exc
        if resp.status_code >= 300:
            # Brevo's error body says what's wrong (unverified sender, blocked IP, bad key…).
            raise EmailSendError(f"Brevo answered {resp.status_code}: {resp.text[:300]}")
        log.info("Sent %r to %s via Brevo (%s)", subject, to, resp.json().get("messageId"))


def get_email_sender() -> EmailSender:
    settings = get_settings()
    if settings.email_backend == "console":
        return ConsoleEmailSender()
    if settings.email_backend == "brevo":
        if not settings.brevo_api_key:
            raise RuntimeError("EMAIL_BACKEND=brevo needs BREVO_API_KEY in .env")
        return BrevoEmailSender(settings.brevo_api_key, settings.email_from)
    raise RuntimeError(f"Unknown EMAIL_BACKEND {settings.email_backend!r}")


def render(template: str, payload: dict[str, Any]) -> tuple[str, str, str]:
    """Returns (subject, html, text)."""
    html = _env.get_template(f"{template}.html").render(**payload)
    text = _env.get_template(f"{template}.txt").render(**payload)
    return SUBJECTS[template], html, text


async def queue_email(
    db: AsyncSession, to: str, template: str, payload: dict[str, Any],
    attachments: Sequence[dict[str, str]] = (),
) -> None:
    """attachments are built when the email is sent, e.g. {"kind": "receipt", "payment_id": ...},
    so files never sit in the queue."""
    if template not in SUBJECTS:
        raise ValueError(f"Unknown email template {template!r}")
    if attachments:
        payload = {**payload, "_attachments": list(attachments)}
    db.add(EmailOutbox(to_email=to, template=template, payload=payload))


async def _build_attachments(db: AsyncSession, specs: list[dict[str, str]]) -> list[Attachment]:
    from app.services import receipts  # receipts uses this module's data, not the reverse

    built = []
    for spec in specs:
        if spec["kind"] == "receipt":
            filename, pdf = await receipts.receipt_pdf(db, uuid.UUID(spec["payment_id"]))
            built.append(Attachment(filename, pdf, "application/pdf"))
        else:
            raise ValueError(f"Unknown attachment kind {spec['kind']!r}")
    return built


async def send_pending(sender: EmailSender | None = None, batch: int = 20) -> int:
    """Sends due emails from the outbox; returns how many were sent.

    Failures retry with backoff and are marked failed after MAX_ATTEMPTS.
    SKIP LOCKED lets more than one worker run without sending twice.
    """
    sender = sender or get_email_sender()
    sent = 0
    async with SessionLocal() as db:
        rows = (
            await db.scalars(
                select(EmailOutbox)
                .where(EmailOutbox.status == "pending", EmailOutbox.send_after <= utcnow())
                .order_by(EmailOutbox.send_after)
                .limit(batch)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for row in rows:
            row.attempts += 1
            try:
                payload = {k: v for k, v in row.payload.items() if k != "_attachments"}
                subject, html, text = render(row.template, payload)
                files = await _build_attachments(db, row.payload.get("_attachments", []))
                await sender.send(row.to_email, subject, html, text, files)
            except Exception:
                log.exception("Email %s to %s failed (attempt %d)", row.id, row.to_email, row.attempts)
                if row.attempts >= MAX_ATTEMPTS:
                    row.status = "failed"
                else:
                    row.send_after = utcnow() + timedelta(seconds=30 * 2**row.attempts)
            else:
                row.status = "sent"
                sent += 1
        await db.commit()
    return sent
