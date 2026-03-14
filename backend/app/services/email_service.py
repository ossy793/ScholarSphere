"""
Email Service — Mailjet
Sends transactional emails (e.g. password reset codes).
"""
import logging
from mailjet_rest import Client
from ..config import settings

logger = logging.getLogger(__name__)


def send_password_reset_email(to_email: str, to_name: str, code: str) -> bool:
    """Send a 6-digit password reset code via Mailjet. Returns True on success."""
    if not settings.MAILJET_API_KEY or not settings.MAILJET_API_SECRET:
        logger.error("Mailjet credentials not configured")
        return False

    mailjet = Client(
        auth=(settings.MAILJET_API_KEY, settings.MAILJET_API_SECRET),
        version="v3.1",
    )

    html_body = f"""
    <div style="font-family:Inter,Arial,sans-serif;max-width:480px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;border:1px solid #D6E4F7">
      <div style="background:linear-gradient(135deg,#0077FF,#0044CC);padding:32px 24px;text-align:center">
        <div style="display:inline-block;background:#fff;border-radius:10px;width:44px;height:44px;line-height:44px;text-align:center;font-weight:900;font-size:24px;color:#0077FF;margin-bottom:10px">P</div>
        <h1 style="color:#fff;margin:0;font-size:22px;font-weight:700">Pritis</h1>
      </div>
      <div style="padding:32px 24px">
        <h2 style="margin:0 0 8px;color:#0A1628;font-size:18px">Reset Your Password</h2>
        <p style="color:#4A6080;margin:0 0 24px;font-size:14px">Use the code below to reset your Pritis password. It expires in <strong>15 minutes</strong>.</p>
        <div style="background:#F0F6FF;border-radius:10px;padding:24px;text-align:center;margin-bottom:24px">
          <span style="font-size:36px;font-weight:900;letter-spacing:10px;color:#0077FF;font-family:monospace">{code}</span>
        </div>
        <p style="color:#4A6080;font-size:13px;margin:0">If you didn't request this, you can safely ignore this email. Your account is secure.</p>
      </div>
      <div style="background:#F0F6FF;padding:16px 24px;text-align:center">
        <p style="color:#4A6080;font-size:12px;margin:0">© 2025 Pritis · AI-Powered Study Platform</p>
      </div>
    </div>
    """

    data = {
        "Messages": [
            {
                "From": {
                    "Email": settings.MAILJET_SENDER_EMAIL,
                    "Name": "Pritis",
                },
                "To": [{"Email": to_email, "Name": to_name}],
                "Subject": f"{code} is your Pritis password reset code",
                "HTMLPart": html_body,
                "TextPart": f"Your Pritis password reset code is: {code}\n\nIt expires in 15 minutes. If you did not request this, ignore this email.",
            }
        ]
    }

    try:
        result = mailjet.send.create(data=data)
        if result.status_code == 200:
            return True
        logger.error("Mailjet send failed: %s %s", result.status_code, result.json())
        return False
    except Exception as exc:
        logger.error("Mailjet exception: %s", exc)
        return False
