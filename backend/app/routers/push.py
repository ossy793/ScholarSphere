"""
Push Notification Router
------------------------
GET  /push/vapid-public-key         — public VAPID key for frontend subscription
POST /push/subscribe                — save a push subscription (authenticated)
DELETE /push/unsubscribe            — remove a push subscription (authenticated)
POST /push/cron/daily-reminder      — cron-triggered: "haven't practiced today"
POST /push/cron/streak-reminder     — cron-triggered: streak / engagement reminder
"""

from datetime import date
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models.push_subscription import PushSubscription
from ..models.user import User
from ..utils.security import get_current_user

router = APIRouter(prefix="/push", tags=["push"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class SubscribeRequest(BaseModel):
    endpoint: str
    keys: dict   # {"p256dh": "...", "auth": "..."}


# ── VAPID public key ──────────────────────────────────────────────────────────

@router.get("/vapid-public-key")
def get_vapid_public_key():
    """Return the VAPID public key so the browser can subscribe."""
    return {"public_key": settings.VAPID_PUBLIC_KEY}


# ── Subscribe ─────────────────────────────────────────────────────────────────

@router.post("/subscribe", status_code=status.HTTP_201_CREATED)
def subscribe(
    payload: SubscribeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing = db.query(PushSubscription).filter(
        PushSubscription.endpoint == payload.endpoint
    ).first()

    if existing:
        existing.user_id = current_user.id
        db.commit()
        return {"message": "Subscription updated."}

    sub = PushSubscription(
        user_id=current_user.id,
        endpoint=payload.endpoint,
        p256dh=payload.keys.get("p256dh", ""),
        auth=payload.keys.get("auth", ""),
    )
    db.add(sub)
    db.commit()
    return {"message": "Subscribed to push notifications."}


# ── Unsubscribe ───────────────────────────────────────────────────────────────

@router.delete("/unsubscribe")
def unsubscribe(
    payload: SubscribeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db.query(PushSubscription).filter(
        PushSubscription.endpoint == payload.endpoint,
        PushSubscription.user_id == current_user.id,
    ).delete()
    db.commit()
    return {"message": "Unsubscribed from push notifications."}


# ── Cron endpoints (protected by secret header) ───────────────────────────────

def _verify_cron(x_cron_secret: str = Header(None)):
    if not settings.CRON_SECRET or x_cron_secret != settings.CRON_SECRET:
        raise HTTPException(status_code=403, detail="Invalid cron secret")


@router.post("/cron/daily-reminder")
def cron_daily_reminder(
    db: Session = Depends(get_db),
    _: None = Depends(_verify_cron),
):
    """
    Send a morning study reminder to all subscribers.
    Call from a server cron at 7:00 AM UTC (8 AM Nigeria time):
      curl -X POST https://pritis.name.ng/api/push/cron/daily-reminder \\
           -H "X-Cron-Secret: <your_secret>"
    """
    from ..services.push_service import send_push_broadcast
    send_push_broadcast(
        db,
        title="Time to study! 📚",
        body="Good morning! Open Pritis and practice a quiz today. Consistency is key!",
        url="/dashboard.html",
    )
    return {"message": "Daily reminder sent."}


@router.post("/cron/streak-reminder")
def cron_streak_reminder(
    db: Session = Depends(get_db),
    _: None = Depends(_verify_cron),
):
    """
    Send a streak reminder to subscribers who haven't been active today.
    Call from a server cron at 17:00 UTC (6 PM Nigeria time):
      curl -X POST https://pritis.name.ng/api/push/cron/streak-reminder \\
           -H "X-Cron-Secret: <your_secret>"
    """
    from ..models.attempt import Attempt
    from ..models.brainstorm import BrainstormSession
    from ..services.push_service import send_push_to_user

    today = date.today()
    subs = db.query(PushSubscription).all()
    user_ids = list({sub.user_id for sub in subs})

    count = 0
    for user_id in user_ids:
        has_attempt = db.query(Attempt).filter(
            Attempt.user_id == user_id,
            func.date(Attempt.started_at) == today,
        ).first()
        has_brainstorm = db.query(BrainstormSession).filter(
            BrainstormSession.user_id == user_id,
            func.date(BrainstormSession.created_at) == today,
        ).first()
        if not has_attempt and not has_brainstorm:
            send_push_to_user(
                db, user_id,
                title="Don't break your streak! 🔥",
                body="You haven't practiced today. Open Pritis and keep your streak alive!",
                url="/dashboard.html",
            )
            count += 1

    return {"message": f"Streak reminder sent to {count} inactive users."}
