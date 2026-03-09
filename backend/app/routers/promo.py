"""
Promo Router
-----------------
Implements the freemium upgrade flow:
  GET  /promo/status   — public; returns available promotional slots
  POST /promo/request  — validate Gmail, generate & email a one-time code
  POST /promo/verify   — verify code, activate 5-week promo premium, return JWT
"""

import hmac
import hashlib
import logging
import secrets
import string
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.user import User
from ..models.promo import PromoCode
from ..schemas.promo import PromoRequestSchema, PromoVerifySchema
from ..utils.email import send_promo_email
from ..utils.security import hash_password, create_access_token
from ..config import settings

router  = APIRouter(prefix="/promo", tags=["promo"])
limiter = Limiter(key_func=get_remote_address)

_CODE_EXPIRY_MINUTES = 15
_CODE_ALPHABET       = string.ascii_uppercase + string.digits
_PROMO_MAX_SLOTS     = 200
_PROMO_DURATION_DAYS = 35           # 5 weeks


def _generate_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))


def _hash_code(raw_code: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        raw_code.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _count_promo_activations(db: Session) -> int:
    """Count all premium users (claimed spots)."""
    return (
        db.query(func.count(User.id))
        .filter(User.is_premium == True)  # noqa: E712
        .scalar() or 0
    )


# ── Public status ─────────────────────────────────────────────────────────────

@router.get("/status")
def promo_status(db: Session = Depends(get_db)):
    """Return current promotional slot availability (used by landing page)."""
    used  = _count_promo_activations(db)
    slots = max(0, _PROMO_MAX_SLOTS - used)
    return {
        "slots_total":     _PROMO_MAX_SLOTS,
        "slots_used":      used,
        "slots_remaining": slots,
        "promo_active":    slots > 0,
        "duration_weeks":  5,
    }


# ── Request ───────────────────────────────────────────────────────────────────

@router.post("/request", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("3/hour")
def request_promo(
    request: Request,
    payload: PromoRequestSchema,
    db: Session = Depends(get_db),
):
    email = str(payload.email).lower().strip()

    if not email.endswith("@gmail.com"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only Gmail addresses (@gmail.com) are supported.",
        )

    # Check slot availability first
    if _count_promo_activations(db) >= _PROMO_MAX_SLOTS:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="The promotional offer has ended — all 200 slots have been claimed.",
        )

    user = db.query(User).filter(User.email == email).first()
    if not user:
        placeholder_pw = hash_password(secrets.token_hex(32))
        user = User(
            email=email,
            password_hash=placeholder_pw,
            full_name=email.split("@")[0],
        )
        db.add(user)
        db.flush()

    if user.is_premium:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This account already has premium access.",
        )

    db.query(PromoCode).filter(
        PromoCode.user_id == user.id,
        PromoCode.is_used == False,          # noqa: E712
    ).delete(synchronize_session=False)

    raw_code  = _generate_code()
    code_hash = _hash_code(raw_code)

    promo = PromoCode(
        user_id    = user.id,
        code_hash  = code_hash,
        expires_at = datetime.utcnow() + timedelta(minutes=_CODE_EXPIRY_MINUTES),
    )
    db.add(promo)
    db.commit()

    try:
        send_promo_email(email, raw_code)
    except Exception as exc:
        logger.error("SMTP failed for %s: %s", email, exc)
        if settings.SMTP_USER:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to send the activation email. Please try again in a moment.",
            )
        else:
            logger.warning("PROMO CODE for %s (no SMTP): %s", email, raw_code)

    return {"message": f"A code has been sent to {email}. It expires in {_CODE_EXPIRY_MINUTES} minutes."}


# ── Verify ────────────────────────────────────────────────────────────────────

@router.post("/verify")
@limiter.limit("5/minute")
def verify_promo(
    request: Request,
    payload: PromoVerifySchema,
    db: Session = Depends(get_db),
):
    email     = str(payload.email).lower().strip()
    raw_code  = payload.code.strip().upper().replace("-", "")
    code_hash = _hash_code(raw_code)

    _BAD = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Invalid email or code. Codes expire after 15 minutes.",
    )

    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise _BAD

    if user.is_premium:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This account already has premium access.",
        )

    # Final slot check (race-condition guard)
    if _count_promo_activations(db) >= _PROMO_MAX_SLOTS:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Sorry — all 200 promotional slots were just claimed. The offer has ended.",
        )

    promo = db.query(PromoCode).filter(
        PromoCode.user_id   == user.id,
        PromoCode.code_hash == code_hash,
        PromoCode.is_used   == False,           # noqa: E712
        PromoCode.expires_at > datetime.utcnow(),
    ).first()

    if not promo:
        raise _BAD

    now = datetime.utcnow()
    user.is_premium           = True
    user.premium_activated_at = now
    user.promo_expires_at     = now + timedelta(days=_PROMO_DURATION_DAYS)
    promo.is_used             = True
    db.commit()

    token = create_access_token(str(user.id))

    return {
        "message":       "Premium activated! Your free Pro access is valid for 5 weeks.",
        "access_token":  token,
        "whatsapp_link": settings.WHATSAPP_GROUP_LINK,
        "expires_at":    user.promo_expires_at.isoformat(),
    }
