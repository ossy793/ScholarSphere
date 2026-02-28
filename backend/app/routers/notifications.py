"""
Notifications Router
--------------------
GET  /notifications              — user's notifications (broadcast + targeted)
GET  /notifications/unread-count — number of unread notifications
PATCH /notifications/{id}/read   — mark one as read
PATCH /notifications/read-all    — mark all as read
"""

from uuid import UUID
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.notification import Notification, NotificationRead
from ..models.user import User
from ..utils.security import get_current_user

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _get_user_notifications(user_id, db: Session):
    """Return all notifications visible to this user (broadcast + targeted)."""
    return (
        db.query(Notification)
        .filter(
            or_(
                Notification.target_user_id == None,   # noqa: E711  broadcast
                Notification.target_user_id == user_id,
            )
        )
        .order_by(Notification.created_at.desc())
        .all()
    )


def _is_read(notif: Notification, user_id, db: Session) -> bool:
    return (
        db.query(NotificationRead)
        .filter(
            NotificationRead.notification_id == notif.id,
            NotificationRead.user_id == user_id,
        )
        .first()
    ) is not None


@router.get("")
def list_notifications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    notifs = _get_user_notifications(current_user.id, db)
    result = []
    for n in notifs:
        result.append({
            "id":         str(n.id),
            "title":      n.title,
            "message":    n.message,
            "is_read":    _is_read(n, current_user.id, db),
            "created_at": n.created_at.isoformat(),
            "is_broadcast": n.target_user_id is None,
        })
    return result


@router.get("/unread-count")
def unread_count(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    notifs = _get_user_notifications(current_user.id, db)
    count = sum(1 for n in notifs if not _is_read(n, current_user.id, db))
    return {"unread": count}


@router.patch("/read-all", status_code=status.HTTP_200_OK)
def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    notifs = _get_user_notifications(current_user.id, db)
    for n in notifs:
        if not _is_read(n, current_user.id, db):
            db.add(NotificationRead(notification_id=n.id, user_id=current_user.id))
    db.commit()
    return {"message": "All notifications marked as read."}


@router.patch("/{notification_id}/read", status_code=status.HTTP_200_OK)
def mark_read(
    notification_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    n = db.query(Notification).filter(Notification.id == notification_id).first()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    if n.target_user_id and n.target_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your notification")
    if not _is_read(n, current_user.id, db):
        db.add(NotificationRead(notification_id=n.id, user_id=current_user.id))
        db.commit()
    return {"message": "Marked as read."}
