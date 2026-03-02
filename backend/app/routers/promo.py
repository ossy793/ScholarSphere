"""
Promo Code Router
-----------------
Implements the freemium upgrade flow:
  POST /promo/request  — validate Gmail, generate & email a one-time code
  POST /promo/verify   — verify code, activate premium, return JWT + WhatsApp link
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


def _generate_code() -> str:
    """Generate a random 8-character alphanumeric code."""
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))


def _hash_code(raw_code: str) -> str:
    """HMAC-SHA256 the code keyed with SECRET_KEY. Constant-time safe."""
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        raw_code.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ── Request ───────────────────────────────────────────────────────────────────

@router.post("/request", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("3/hour")
def request_promo(
    request: Request,
    payload: PromoRequestSchema,
    db: Session = Depends(get_db),
):
    """
    Validate the Gmail, find-or-create the user, generate a one-time promo code,
    invalidate any previous unused codes, and send the code via email.
    """
    email = str(payload.email).lower().strip()

    if not email.endswith("@gmail.com"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only Gmail addresses (@gmail.com) are supported.",
        )

    # Find or create user
    user = db.query(User).filter(User.email == email).first()
    if not user:
        placeholder_pw = hash_password(secrets.token_hex(32))
        user = User(
            email=email,
            password_hash=placeholder_pw,
            full_name=email.split("@")[0],
        )
        db.add(user)
        db.flush()   # populate user.id

    if user.is_premium:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This account already has premium access.",
        )

    # Invalidate all previous unused codes for this user
    db.query(PromoCode).filter(
        PromoCode.user_id == user.id,
        PromoCode.is_used == False,          # noqa: E712
    ).delete(synchronize_session=False)

    # Generate, hash, and store the new code
    raw_code  = _generate_code()
    code_hash = _hash_code(raw_code)

    promo = PromoCode(
        user_id    = user.id,
        code_hash  = code_hash,
        expires_at = datetime.utcnow() + timedelta(minutes=_CODE_EXPIRY_MINUTES),
    )
    db.add(promo)
    db.commit()

    # Send email
    try:
        send_promo_email(email, raw_code)
    except Exception as exc:
        logger.error("SMTP failed for %s: %s", email, exc)
        if settings.SMTP_USER:
            # SMTP is configured but failed — surface the error to the client
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to send the activation email. Please try again in a moment.",
            )
        else:
            # No SMTP configured (dev mode) — log code to terminal
            logger.warning("⚠️  PROMO CODE for %s (email not sent, no SMTP configured): %s", email, raw_code)

    return {"message": f"A code has been sent to {email}. It expires in {_CODE_EXPIRY_MINUTES} minutes."}


# ── Verify ────────────────────────────────────────────────────────────────────

@router.post("/verify")
@limiter.limit("5/minute")
def verify_promo(
    request: Request,
    payload: PromoVerifySchema,
    db: Session = Depends(get_db),
):
    """
    Verify the code, activate premium on the user, and return a fresh JWT
    plus the WhatsApp group link.
    """
    email     = str(payload.email).lower().strip()
    raw_code  = payload.code.strip().upper().replace("-", "")   # tolerate dashes/lowercase
    code_hash = _hash_code(raw_code)

    # Use a generic error to prevent oracle attacks
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

    promo = db.query(PromoCode).filter(
        PromoCode.user_id   == user.id,
        PromoCode.code_hash == code_hash,
        PromoCode.is_used   == False,           # noqa: E712
        PromoCode.expires_at > datetime.utcnow(),
    ).first()

    if not promo:
        raise _BAD

    # Activate premium
    user.is_premium           = True
    user.premium_activated_at = datetime.utcnow()
    promo.is_used             = True
    db.commit()

    # Issue a fresh JWT so the frontend can update localStorage immediately
    token = create_access_token(str(user.id))

    return {
        "message":       "Premium activated! Welcome to Pistis Premium.",
        "access_token":  token,
        "whatsapp_link": settings.WHATSAPP_GROUP_LINK,
    }
