"""
Push notification service using pywebpush + VAPID.

Usage:
  send_push_to_user(db, user_id, title, body)   — one user
  send_push_broadcast(db, title, body)            — all subscribers
  get_subscriber_count(db)                        — count subscriptions
"""

import json
import logging

logger = logging.getLogger(__name__)


def _subscription_info(sub) -> dict:
    return {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}


def _send_one(subscription_info: dict, title: str, body: str, url: str) -> str:
    """Returns 'ok', 'expired', or 'error'."""
    try:
        from pywebpush import webpush, WebPushException
        from ..config import settings

        webpush(
            subscription_info=subscription_info,
            data=json.dumps({"title": title, "body": body, "url": url}),
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": f"mailto:{settings.VAPID_CLAIM_EMAIL}"},
        )
        return "ok"
    except Exception as ex:
        try:
            from pywebpush import WebPushException
            if isinstance(ex, WebPushException) and ex.response and ex.response.status_code in (404, 410):
                return "expired"
        except Exception:
            pass
        logger.warning("Push send error: %s", ex)
        return "error"


def _cleanup_expired(db, expired_ids: list):
    if not expired_ids:
        return
    from ..models.push_subscription import PushSubscription
    db.query(PushSubscription).filter(
        PushSubscription.id.in_(expired_ids)
    ).delete(synchronize_session=False)
    db.commit()


def send_push_to_user(db, user_id, title: str, body: str, url: str = "/dashboard.html"):
    """Send push notification to all subscriptions of a single user."""
    try:
        from ..models.push_subscription import PushSubscription
        subs = db.query(PushSubscription).filter(PushSubscription.user_id == user_id).all()
        expired = []
        for sub in subs:
            result = _send_one(_subscription_info(sub), title, body, url)
            if result == "expired":
                expired.append(sub.id)
        _cleanup_expired(db, expired)
    except Exception as ex:
        logger.warning("send_push_to_user failed: %s", ex)


def send_push_broadcast(db, title: str, body: str, url: str = "/dashboard.html"):
    """Send push notification to ALL subscribed users."""
    try:
        from ..models.push_subscription import PushSubscription
        subs = db.query(PushSubscription).all()
        expired = []
        for sub in subs:
            result = _send_one(_subscription_info(sub), title, body, url)
            if result == "expired":
                expired.append(sub.id)
        _cleanup_expired(db, expired)
        logger.info("Broadcast push sent to %d subscriptions", len(subs))
    except Exception as ex:
        logger.warning("send_push_broadcast failed: %s", ex)


def get_subscriber_count(db) -> int:
    try:
        from ..models.push_subscription import PushSubscription
        from sqlalchemy import func
        return db.query(func.count(PushSubscription.id)).scalar() or 0
    except Exception:
        return 0
