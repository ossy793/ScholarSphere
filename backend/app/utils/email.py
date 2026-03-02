"""
Email Utility
-------------
Production:  Brevo API   (HTTP — no domain verification needed, just verify sender email)
             Set BREVO_API_KEY + BREVO_SENDER_EMAIL in Render environment variables.
Fallback:    Resend API  (requires verified domain at resend.com/domains)
Local dev:   SMTP via Gmail (set SMTP_USER + SMTP_PASSWORD in .env)
"""

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from ..config import settings


def _html_body(code: str) -> str:
    return f"""
<!DOCTYPE html>
<html>
<body style="font-family:Inter,Arial,sans-serif;background:#f3f4f6;padding:40px 0;margin:0">
  <div style="max-width:480px;margin:0 auto;background:#fff;border-radius:12px;
              box-shadow:0 4px 24px rgba(0,0,0,.08);overflow:hidden">
    <div style="background:linear-gradient(135deg,#1e1b4b,#4338ca);padding:32px;text-align:center">
      <div style="width:52px;height:52px;background:#fff;border-radius:12px;
                  display:inline-flex;align-items:center;justify-content:center;
                  font-size:26px;font-weight:900;color:#4338ca;margin-bottom:12px">P</div>
      <h1 style="color:#fff;margin:0;font-size:1.4rem;font-weight:800">Pistis Premium</h1>
    </div>
    <div style="padding:32px 36px">
      <h2 style="margin:0 0 8px;font-size:1.1rem;color:#1e1b4b">Your Activation Code</h2>
      <p style="color:#6b7280;margin:0 0 24px;font-size:.9rem">
        Use the code below to unlock premium access. It expires in <strong>15 minutes</strong>.
      </p>
      <div style="background:#f5f3ff;border:2px dashed #818cf8;border-radius:10px;
                  padding:20px;text-align:center;margin-bottom:24px">
        <span style="font-size:2rem;font-weight:900;letter-spacing:6px;
                     color:#4338ca;font-family:monospace">{code}</span>
      </div>
      <p style="color:#6b7280;font-size:.85rem;margin:0 0 4px">
        Enter this code on the Pistis upgrade page along with your email address.
      </p>
      <p style="color:#ef4444;font-size:.85rem;margin:0">
        Do not share this code with anyone.
      </p>
    </div>
    <div style="background:#f9fafb;padding:16px 36px;border-top:1px solid #e5e7eb;
                text-align:center;color:#9ca3af;font-size:.8rem">
      If you did not request this code, you can safely ignore this email.
    </div>
  </div>
</body>
</html>
"""


def _send_via_brevo(to_email: str, code: str) -> None:
    """Send via Brevo HTTP API — no domain needed, just a verified sender email."""
    import requests
    resp = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={
            "accept": "application/json",
            "api-key": settings.BREVO_API_KEY,
            "content-type": "application/json",
        },
        json={
            "sender": {"name": "Pistis", "email": settings.BREVO_SENDER_EMAIL},
            "to": [{"email": to_email}],
            "subject": "Your Pistis Premium Access Code",
            "htmlContent": _html_body(code),
        },
        timeout=10,
    )
    resp.raise_for_status()


def _send_via_resend(to_email: str, code: str) -> None:
    """Send via Resend HTTP API — works on Render (no SMTP port restrictions)."""
    import resend
    resend.api_key = settings.RESEND_API_KEY
    resend.Emails.send({
        "from":    settings.RESEND_FROM,
        "to":      [to_email],
        "subject": "Your Pistis Premium Access Code",
        "html":    _html_body(code),
    })


def _send_via_smtp(to_email: str, code: str) -> None:
    """Send via Gmail SMTP — local development fallback."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your Pistis Premium Access Code"
    msg["From"]    = settings.SMTP_FROM
    msg["To"]      = to_email
    msg.attach(MIMEText(_html_body(code), "html"))

    ctx = ssl.create_default_context()
    if settings.SMTP_PORT == 465:
        with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, context=ctx) as server:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)
    else:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.ehlo()
            server.starttls(context=ctx)
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)


def send_promo_email(to_email: str, code: str) -> None:
    """
    Send the promo activation code.
    Priority: Brevo (no domain needed) → Resend (domain needed) → SMTP (local dev).
    """
    if settings.BREVO_API_KEY and settings.BREVO_SENDER_EMAIL:
        _send_via_brevo(to_email, code)
    elif settings.RESEND_API_KEY:
        _send_via_resend(to_email, code)
    else:
        _send_via_smtp(to_email, code)
