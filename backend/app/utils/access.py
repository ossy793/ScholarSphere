"""
Feature access control and usage tracking.

Tier matrix:
  free  → hard limits on core features; no gemini; no youtube/visual/friends
  basic → unlimited core; gemini; 10/month youtube + visual; friends ok
  pro   → no restrictions
  admin → always treated as pro (no restrictions)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from fastapi import HTTPException

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# ── Plan limit definitions ────────────────────────────────────────────────────
# None       → feature completely blocked for this plan
# {}         → allowed, unlimited
# {limit: N} → allowed up to N uses (lifetime for free, rolling for basic)
# reset_days → how often the counter resets (None = never)

PLAN_LIMITS: dict[str, dict] = {
    "free": {
        "input_questions":    {"limit": 2,  "reset_days": None},
        "ai_generate":        {"limit": 3,  "reset_days": None, "max_q": 20},
        "study_zone":         {"limit": 3,  "reset_days": None, "session_mins": 30},
        "youtube_resources":  None,
        "visual_explanation": None,
        "ask_friends":        None,
        "ai_recommendations": None,
        "challenge":          {},
        "study_strategy":     {},
        "model_groq":         {},
        "model_openai":       {},
        "model_gemini":       None,
    },
    "basic": {
        "input_questions":    {},
        "ai_generate":        {"max_q": 70},
        "study_zone":         {},
        "youtube_resources":  {"limit": 10, "reset_days": 30},
        "visual_explanation": {"limit": 10, "reset_days": 30},
        "ask_friends":        {},
        "ai_recommendations": {},
        "challenge":          {},
        "study_strategy":     {},
        "model_groq":         {},
        "model_openai":       {},
        "model_gemini":       {},
    },
    "pro": {
        "input_questions":    {},
        "ai_generate":        {},
        "study_zone":         {},
        "youtube_resources":  {},
        "visual_explanation": {},
        "ask_friends":        {},
        "ai_recommendations": {},
        "challenge":          {},
        "study_strategy":     {},
        "model_groq":         {},
        "model_openai":       {},
        "model_gemini":       {},
    },
}

# Which plan first unlocks a feature (used in upgrade messages)
_FEATURE_MIN_PLAN: dict[str, str] = {
    "youtube_resources":  "basic",
    "visual_explanation": "basic",
    "ask_friends":        "basic",
    "ai_recommendations": "basic",
    "model_gemini":       "basic",
}

PLAN_ORDER = ["free", "basic", "pro"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def effective_plan(user) -> str:
    """Return the user's current effective plan, accounting for expiry and admin."""
    if user.is_admin:
        return "pro"
    plan = getattr(user, "subscription_plan", "free") or "free"
    if plan in ("basic", "pro"):
        expiry = getattr(user, "subscription_expiry", None)
        if expiry and datetime.utcnow() > expiry:
            return "free"
    return plan


def _get_or_create_usage(db: "Session", user_id, feature_name: str):
    from ..models.feature_usage import FeatureUsage
    row = db.query(FeatureUsage).filter(
        FeatureUsage.user_id      == user_id,
        FeatureUsage.feature_name == feature_name,
    ).first()
    if not row:
        row = FeatureUsage(
            user_id      = user_id,
            feature_name = feature_name,
            usage_count  = 0,
            period_start = datetime.utcnow(),
            last_used    = datetime.utcnow(),
        )
        db.add(row)
        db.flush()
    return row


def _maybe_reset(row, reset_days: int | None, db: "Session") -> None:
    """Reset usage counter if the rolling window has elapsed."""
    if reset_days is None:
        return
    cutoff = datetime.utcnow() - timedelta(days=reset_days)
    if row.period_start < cutoff:
        row.usage_count  = 0
        row.period_start = datetime.utcnow()
        db.flush()


# ── Public API ────────────────────────────────────────────────────────────────

def check_access(
    db:        "Session",
    user,
    feature:   str,
    increment: bool = False,
) -> dict:
    """
    Verify the user can access *feature*.

    Returns the limit dict (may be empty {}) if allowed.
    Raises HTTP 402 if blocked or over quota.

    Pass increment=True to record one usage unit atomically with the check.
    """
    plan    = effective_plan(user)
    limits  = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"]).get(feature)

    if limits is None:
        required = _FEATURE_MIN_PLAN.get(feature, "basic")
        raise HTTPException(
            status_code=402,
            detail={
                "code":          "UPGRADE_REQUIRED",
                "feature":       feature,
                "required_plan": required,
                "current_plan":  plan,
                "message": (
                    f"This feature is not available on your current plan. "
                    f"Upgrade to {required.title()} to unlock it."
                ),
            },
        )

    hard_limit = limits.get("limit")
    if hard_limit is not None:
        row = _get_or_create_usage(db, user.id, feature)
        _maybe_reset(row, limits.get("reset_days"), db)
        if row.usage_count >= hard_limit:
            next_plan = PLAN_ORDER[min(PLAN_ORDER.index(plan) + 1, len(PLAN_ORDER) - 1)]
            raise HTTPException(
                status_code=402,
                detail={
                    "code":          "UPGRADE_REQUIRED",
                    "feature":       feature,
                    "required_plan": next_plan,
                    "current_plan":  plan,
                    "current_usage": row.usage_count,
                    "limit":         hard_limit,
                    "message": (
                        f"You've used {row.usage_count}/{hard_limit} of your "
                        f"{feature.replace('_', ' ')} allowance. "
                        f"Upgrade to {next_plan.title()} for more."
                    ),
                },
            )
        if increment:
            row.usage_count += 1
            row.last_used    = datetime.utcnow()
            db.commit()
    elif increment:
        # No hard limit, but still track usage
        row = _get_or_create_usage(db, user.id, feature)
        row.usage_count += 1
        row.last_used    = datetime.utcnow()
        db.commit()

    return limits


def get_usage_status(db: "Session", user) -> dict:
    """Return a summary of the user's current usage across all tracked features."""
    from ..models.feature_usage import FeatureUsage
    plan = effective_plan(user)
    rows = {
        r.feature_name: r
        for r in db.query(FeatureUsage).filter(FeatureUsage.user_id == user.id).all()
    }
    result = {}
    for feature, limits in PLAN_LIMITS.get(plan, PLAN_LIMITS["free"]).items():
        if limits is None:
            result[feature] = {"allowed": False}
            continue
        hard_limit = limits.get("limit") if limits else None
        if hard_limit is not None:
            row = rows.get(feature)
            used = row.usage_count if row else 0
            result[feature] = {
                "allowed": True,
                "used":    used,
                "limit":   hard_limit,
                "remaining": max(0, hard_limit - used),
            }
        else:
            result[feature] = {"allowed": True}
    return result


def enforce_model_access(user, model_id: str) -> None:
    """
    Raise 402 if the user's plan does not include the requested AI model.
    model_id: "groq" | "openai" | "gemini"
    """
    plan = effective_plan(user)
    feature = f"model_{model_id}"
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"]).get(feature)
    if limits is None:
        raise HTTPException(
            status_code=402,
            detail={
                "code":          "UPGRADE_REQUIRED",
                "feature":       feature,
                "required_plan": "basic",
                "current_plan":  plan,
                "message":       f"The {model_id.title()} model is not available on your current plan. Upgrade to Basic or higher.",
            },
        )


def clamp_question_count(user, requested: int) -> int:
    """Return the effective max questions allowed for the user's plan."""
    plan   = effective_plan(user)
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"]).get("ai_generate")
    if limits is None:
        return requested
    max_q = limits.get("max_q")
    if max_q is None:
        return requested
    return min(requested, max_q)
