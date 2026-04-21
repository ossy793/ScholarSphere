"""
Friend Message Email Notification
-----------------------------------
Sent when a user receives a direct message and is not currently online.
Uses the same email stack as the rest of the app (Mailjet → SMTP fallback).
"""

import asyncio

import httpx

from ..config import settings


def _html_body(sender_name: str, preview: str) -> str:
    escaped = preview.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!DOCTYPE html>
<html>
<body style="font-family:Inter,Arial,sans-serif;background:#f3f4f6;padding:40px 0;margin:0">
  <div style="max-width:480px;margin:0 auto;background:#fff;border-radius:12px;
              box-shadow:0 4px 24px rgba(0,0,0,.08);overflow:hidden">
    <div style="background:linear-gradient(135deg,#1e1b4b,#4338ca);padding:28px;text-align:center">
      <div style="width:48px;height:48px;background:#fff;border-radius:12px;
                  display:inline-flex;align-items:center;justify-content:center;
                  font-size:24px;font-weight:900;color:#4338ca;margin-bottom:10px">P</div>
      <h1 style="color:#fff;margin:0;font-size:1.3rem;font-weight:800">New Message on Pritis</h1>
    </div>
    <div style="padding:28px 32px">
      <p style="color:#374151;font-size:0.95rem;margin:0 0 16px">
        <strong>{sender_name}</strong> sent you a message:
      </p>
      <div style="background:#f5f3ff;border-left:4px solid #6366f1;border-radius:0 8px 8px 0;
                  padding:14px 18px;margin-bottom:20px;color:#374151;font-size:0.9rem;
                  line-height:1.6;font-style:italic">
        &ldquo;{escaped}&rdquo;
      </div>
      <a href="https://www.pritis.name.ng/frontend/brainstorm.html"
         style="display:inline-block;background:#4338ca;color:#fff;text-decoration:none;
                padding:11px 24px;border-radius:8px;font-weight:700;font-size:0.9rem">
        Open Pritis to Reply
      </a>
    </div>
    <div style="background:#f9fafb;padding:14px 32px;border-top:1px solid #e5e7eb;
                color:#9ca3af;font-size:0.78rem">
      You received this because someone sent you a message on Pritis.
    </div>
  </div>
</body>
</html>"""


def _send_mailjet(to_email: str, sender_name: str, preview: str) -> None:
    with httpx.Client(timeout=8) as client:
        resp = client.post(
            "https://api.mailjet.com/v3.1/send",
            auth=(settings.MAILJET_API_KEY, settings.MAILJET_API_SECRET),
            json={
                "Messages": [{
                    "From": {"Email": settings.MAILJET_SENDER_EMAIL, "Name": "Pritis"},
                    "To":   [{"Email": to_email}],
                    "Subject":  f"{sender_name} sent you a message on Pritis",
                    "HTMLPart": _html_body(sender_name, preview),
                }]
            },
        )
        resp.raise_for_status()


async def _send_mailjet_async(to_email: str, sender_name: str, preview: str) -> None:
    async with httpx.AsyncClient(timeout=8) as client:
        resp = await client.post(
            "https://api.mailjet.com/v3.1/send",
            auth=(settings.MAILJET_API_KEY, settings.MAILJET_API_SECRET),
            json={
                "Messages": [{
                    "From": {"Email": settings.MAILJET_SENDER_EMAIL, "Name": "Pritis"},
                    "To":   [{"Email": to_email}],
                    "Subject":  f"{sender_name} sent you a message on Pritis",
                    "HTMLPart": _html_body(sender_name, preview),
                }]
            },
        )
        resp.raise_for_status()


def _send_smtp(to_email: str, sender_name: str, preview: str) -> None:
    import smtplib, ssl
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{sender_name} sent you a message on Pritis"
    msg["From"]    = settings.SMTP_FROM
    msg["To"]      = to_email
    msg.attach(MIMEText(_html_body(sender_name, preview), "html"))
    ctx = ssl.create_default_context()
    if settings.SMTP_PORT == 465:
        with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, context=ctx) as s:
            s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            s.send_message(msg)
    else:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as s:
            s.ehlo(); s.starttls(context=ctx)
            s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            s.send_message(msg)


def send_friend_message_email(to_email: str, sender_name: str, message_preview: str) -> None:
    """Sync — safe to call from sync routes (FastAPI threadpool)."""
    if settings.MAILJET_API_KEY and settings.MAILJET_API_SECRET:
        _send_mailjet(to_email, sender_name, message_preview)
    elif settings.SMTP_USER and settings.SMTP_PASSWORD:
        _send_smtp(to_email, sender_name, message_preview)


async def send_friend_message_email_async(to_email: str, sender_name: str, message_preview: str) -> None:
    """Async — use from async routes or BackgroundTasks."""
    if settings.MAILJET_API_KEY and settings.MAILJET_API_SECRET:
        await _send_mailjet_async(to_email, sender_name, message_preview)
    elif settings.SMTP_USER and settings.SMTP_PASSWORD:
        await asyncio.to_thread(_send_smtp, to_email, sender_name, message_preview)
