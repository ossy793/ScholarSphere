"""
Payment router — Paystack integration with tiered subscription plans.

Tiers:  basic | pro/max
Cycles: weekly (7 days) | monthly (30 days)

Pricing (kobo) — discounted launch prices:
  basic  weekly  = 50 000  (₦500  · was ₦1 000 · 50% OFF)
  basic  monthly = 180 000 (₦1 800 · was ₦3 000 · 40% OFF)
  max    weekly  = 100 000 (₦1 000 · was ₦1 500 · 33% OFF)
  max    monthly = 300 000 (₦3 000 · was ₦4 000 · 25% OFF)

Endpoints:
  GET  /payment/config                 — return public key + plan/price matrix
  POST /payment/verify                 — verify a Paystack reference (card/instant)
  GET  /payment/status/{ref}           — poll payment status (bank transfer)
  POST /payment/webhook                — Paystack webhook
  GET  /payment/usage                  — current user's feature usage summary
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db, SessionLocal
from ..models.user import User
from ..models.payment_transaction import PaymentTransaction
from ..utils.security import create_access_token, get_current_user
from ..utils.access import get_usage_status, effective_plan

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/payment", tags=["payment"])

# ── Plan / pricing matrix ─────────────────────────────────────────────────────
# kobo = final (discounted) amount charged via Paystack
# original_kobo = original price before discount (for display only)
# discount_pct  = percentage discount (for display only)

_PLANS = {
    "basic": {
        "weekly":  {"kobo":  50_000, "original_kobo": 100_000, "discount_pct": 50, "days": 7,  "label": "₦500 / week"},
        "monthly": {"kobo": 180_000, "original_kobo": 300_000, "discount_pct": 40, "days": 30, "label": "₦1,800 / month"},
    },
    "max": {
        "weekly":  {"kobo": 100_000, "original_kobo": 150_000, "discount_pct": 33, "days": 7,  "label": "₦1,000 / week"},
        "monthly": {"kobo": 300_000, "original_kobo": 400_000, "discount_pct": 25, "days": 30, "label": "₦3,000 / month"},
    },
}
# Legacy alias so old webhook/poll calls with plan="pro" still work
_PLANS["pro"] = _PLANS["max"]


# ── Schemas ───────────────────────────────────────────────────────────────────

class VerifyRequest(BaseModel):
    reference: str
    plan:      str   # "basic" | "max"
    cycle:     str   # "weekly" | "monthly"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _plan_config(plan: str, cycle: str) -> dict:
    if plan not in _PLANS or cycle not in _PLANS[plan]:
        raise HTTPException(400, f"Invalid plan '{plan}' or cycle '{cycle}'.")
    return _PLANS[plan][cycle]


async def _paystack_verify(reference: str, expected_kobo: int) -> dict:
    """Call Paystack API and return the transaction dict, or raise HTTPException."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(
                f"https://api.paystack.co/transaction/verify/{reference}",
                headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"},
            )
    except Exception as exc:
        logger.error("Paystack network error: %s", exc)
        raise HTTPException(502, "Could not reach payment gateway. Please try again.")

    if res.status_code != 200:
        raise HTTPException(400, "Payment verification failed. Please contact support.")

    data = res.json()
    tx   = data.get("data", {})

    if not data.get("status") or tx.get("status") != "success":
        raise HTTPException(400, "Payment not yet confirmed. Please wait a moment and try again.")

    paid_kobo = tx.get("amount", 0)
    if paid_kobo < expected_kobo:
        raise HTTPException(400,
            f"Payment amount too low (paid ₦{paid_kobo // 100}, "
            f"required ₦{expected_kobo // 100})."
        )

    return tx


def _grant_subscription(user: User, plan: str, cycle: str, db: Session) -> str:
    """Activate a subscription plan for the user and return a fresh JWT."""
    cfg  = _plan_config(plan, cycle)
    now  = datetime.utcnow()

    # If already on same or higher plan with time remaining, extend instead of reset
    current_expiry = user.subscription_expiry
    if (
        user.subscription_plan == plan
        and current_expiry
        and current_expiry > now
    ):
        base = current_expiry
    else:
        base = now

    user.subscription_plan   = plan
    user.subscription_start  = now
    user.subscription_expiry = base + timedelta(days=cfg["days"])
    db.commit()
    db.refresh(user)
    return create_access_token(str(user.id))


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/config")
def payment_config():
    """Return Paystack public key and the full plan/price matrix."""
    return {
        "public_key": settings.PAYSTACK_PUBLIC_KEY,
        "plans": {
            plan: {
                cycle: {
                    "kobo":          cfg["kobo"],
                    "original_kobo": cfg["original_kobo"],
                    "discount_pct":  cfg["discount_pct"],
                    "label":         cfg["label"],
                }
                for cycle, cfg in cycles.items()
            }
            for plan, cycles in _PLANS.items()
            if plan != "pro"   # exclude legacy alias from client-facing list
        },
    }


@router.post("/verify")
async def verify_payment(
    payload:      VerifyRequest,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Verify a Paystack reference and activate the chosen plan."""
    if not settings.PAYSTACK_SECRET_KEY:
        raise HTTPException(503, "Payment gateway not configured.")

    # Block payment if user already has an active subscription
    now = datetime.utcnow()
    if (
        not current_user.is_admin
        and current_user.subscription_expiry
        and current_user.subscription_expiry > now
    ):
        expiry_str = current_user.subscription_expiry.strftime("%d %b %Y")
        raise HTTPException(
            400,
            f"You already have an active subscription until {expiry_str}. "
            "You can subscribe again after it expires."
        )

    cfg = _plan_config(payload.plan, payload.cycle)
    tx  = await _paystack_verify(payload.reference, cfg["kobo"])

    tx_email   = (tx.get("customer") or {}).get("email", "").lower().strip()
    user_email = (current_user.email or "").lower().strip()
    if tx_email and tx_email != user_email:
        raise HTTPException(400, "Payment email does not match your account email.")

    token = _grant_subscription(current_user, payload.plan, payload.cycle, db)

    try:
        tx_log = PaymentTransaction(
            user_id=current_user.id,
            email=current_user.email or "",
            full_name=current_user.full_name or "",
            amount_kobo=cfg["kobo"],
            plan=payload.plan,
            cycle=payload.cycle,
            reference=payload.reference,
            paid_at=datetime.utcnow(),
        )
        db.add(tx_log)
        db.commit()
    except IntegrityError:
        db.rollback()
    except Exception:
        db.rollback()

    plan_label = payload.plan.title()
    return {
        "token":   token,
        "message": f"{plan_label} plan activated! Enjoy your new features.",
    }


@router.get("/status/{reference}")
async def payment_status(
    reference:    str,
    plan:         str     = "basic",
    cycle:        str     = "monthly",
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """
    Poll endpoint for bank transfers.
    Frontend calls this every few seconds while showing a 'waiting' screen.
    Returns {confirmed: true, token: ...} when payment clears.
    """
    if not settings.PAYSTACK_SECRET_KEY:
        raise HTTPException(503, "Payment gateway not configured.")

    cfg = _plan_config(plan, cycle)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(
                f"https://api.paystack.co/transaction/verify/{reference}",
                headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"},
            )
        data = res.json()
        tx   = data.get("data", {})

        if data.get("status") and tx.get("status") == "success":
            if tx.get("amount", 0) >= cfg["kobo"]:
                token = _grant_subscription(current_user, plan, cycle, db)
                return {"confirmed": True, "token": token}

    except Exception:
        pass

    return {"confirmed": False}


@router.post("/webhook")
async def paystack_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Paystack webhook — handles async bank transfer confirmations.
    Metadata must include plan + cycle (set by frontend when initiating payment).
    """
    body = await request.body()

    secret    = settings.PAYSTACK_SECRET_KEY.encode()
    signature = request.headers.get("x-paystack-signature", "")
    expected  = hmac.new(secret, body, hashlib.sha512).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(400, "Invalid webhook signature.")

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(400, "Invalid JSON payload.")

    event = payload.get("event")
    data  = payload.get("data", {})

    if event == "charge.success":
        status_str = data.get("status", "")
        email      = (data.get("customer") or {}).get("email", "").lower().strip()
        paid_kobo  = data.get("amount", 0)
        meta       = data.get("metadata") or {}
        plan       = meta.get("plan",  "basic")
        cycle      = meta.get("cycle", "monthly")

        if status_str == "success" and email:
            try:
                cfg = _plan_config(plan, cycle)
            except HTTPException:
                cfg = _PLANS["basic"]["monthly"]
                plan, cycle = "basic", "monthly"

            if paid_kobo >= cfg["kobo"]:
                db2 = SessionLocal()
                try:
                    user = db2.query(User).filter(User.email == email).first()
                    if user:
                        _grant_subscription(user, plan, cycle, db2)
                        logger.info("Webhook: %s plan (%s) granted to %s", plan, cycle, email)
                    ref = data.get("reference", "")
                    if ref:
                        try:
                            tx_log = PaymentTransaction(
                                user_id=user.id if user else None,
                                email=email,
                                full_name=user.full_name if user else "",
                                amount_kobo=paid_kobo,
                                plan=plan,
                                cycle=cycle,
                                reference=ref,
                                paid_at=datetime.utcnow(),
                            )
                            db2.add(tx_log)
                            db2.commit()
                        except IntegrityError:
                            db2.rollback()
                        except Exception:
                            db2.rollback()
                finally:
                    db2.close()

    return JSONResponse({"status": "ok"})


@router.get("/usage")
def get_feature_usage(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Return the current user's feature usage summary."""
    plan    = effective_plan(current_user)
    now     = datetime.utcnow()
    expiry  = current_user.subscription_expiry
    start   = current_user.subscription_start

    days_remaining = None
    days_used      = None
    if expiry and plan in ("basic", "max"):
        days_remaining = max(0, (expiry - now).days)
    if start and plan in ("basic", "max"):
        days_used = max(0, (now - start).days)

    return {
        "plan":           plan,
        "start":          start.isoformat()  if start  else None,
        "expiry":         expiry.isoformat() if expiry else None,
        "days_remaining": days_remaining,
        "days_used":      days_used,
        "is_admin":       current_user.is_admin,
        "usage":          get_usage_status(db, current_user),
    }
