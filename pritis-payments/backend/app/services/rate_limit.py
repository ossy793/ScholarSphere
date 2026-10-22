"""Fixed-window rate limits kept in PostgreSQL, so all API workers share the counts.

Limits are generous on purpose: a whole class on campus Wi-Fi or one mobile
carrier can share a single IP address.
"""

import time
from datetime import UTC, datetime

from fastapi import Depends, Request
from sqlalchemy import text

from app.core.db import SessionLocal
from app.core.errors import AppError

# action -> (max requests, window in seconds)
# The per-address limits on the user site are generous on purpose: a whole class often
# shares one public address (campus Wi-Fi, mobile networks' shared addresses), and might
# all sign in during one lecture. Password guessing is stopped per account instead
# (temporary lockout after repeated failures, settings.login_max_failures).
LIMITS: dict[str, tuple[int, int]] = {
    "signup": (150, 600),
    "login": (300, 300),
    "verify_email": (300, 600),
    "resend_code": (3, 600),
    "forgot_password": (100, 600),
    "forgot_password_email": (3, 600),
    "reset_password": (150, 600),
    "refresh": (1200, 300),
    "admin_login": (20, 300),
    "admin_totp": (5, 300),  # per staff account
    "admin_invite": (30, 600),
    # Per user: account lookups reveal account holders' names, so keep them scarce.
    "resolve_account": (15, 3600),
    "rep_apply": (5, 3600),
    "email_receipt": (5, 3600),  # per user: "email me the receipt again"
    "support_message": (10, 3600),  # per user: messages to support
    "payout_code": (5, 600),  # per rep: payout confirmation codes emailed
}

_HIT = text(
    """
    INSERT INTO rate_limits (key, window_start, count) VALUES (:key, :window_start, 1)
    ON CONFLICT (key) DO UPDATE SET
        count = CASE WHEN rate_limits.window_start = EXCLUDED.window_start
                     THEN rate_limits.count + 1 ELSE 1 END,
        window_start = EXCLUDED.window_start,
        updated_at = now()
    RETURNING count
    """
)


async def enforce(action: str, subject: str) -> None:
    """Counts one hit for (action, subject); raises 429 once the window's limit is passed."""
    limit, window = LIMITS[action]
    now = int(time.time())
    window_start = datetime.fromtimestamp(now - now % window, UTC)
    # Own session and commit: the hit must count even if the request then fails.
    async with SessionLocal() as db:
        count = await db.scalar(
            _HIT, {"key": f"{action}:{subject}"[:200], "window_start": window_start}
        )
        await db.commit()
    if count > limit:
        raise AppError(
            "rate_limited", "Too many attempts. Please wait a few minutes and try again.", 429
        )


def client_ip(request: Request) -> str:
    # Behind Nginx, uvicorn's --proxy-headers sets request.client from X-Forwarded-For.
    return request.client.host if request.client else "unknown"


def limit_by_ip(action: str):
    async def dependency(request: Request) -> None:
        await enforce(action, client_ip(request))

    return Depends(dependency)
