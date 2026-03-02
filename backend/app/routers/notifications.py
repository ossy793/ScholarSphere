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


def _get_read_ids(user_id, notif_ids: list, db: Session) -> set:
    """Return the set of notification IDs already read by this user — single query."""
    if not notif_ids:
        return set()
    rows = (
        db.query(NotificationRead.notification_id)
        .filter(
            NotificationRead.user_id == user_id,
            NotificationRead.notification_id.in_(notif_ids),
        )
        .all()
    )
    return {r.notification_id for r in rows}


@router.get("")
def list_notifications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    notifs = _get_user_notifications(current_user.id, db)
    read_ids = _get_read_ids(current_user.id, [n.id for n in notifs], db)
    return [
        {
            "id":           str(n.id),
            "title":        n.title,
            "message":      n.message,
            "is_read":      n.id in read_ids,
            "created_at":   n.created_at.isoformat(),
            "is_broadcast": n.target_user_id is None,
        }
        for n in notifs
    ]


@router.get("/unread-count")
def unread_count(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    notifs = _get_user_notifications(current_user.id, db)
    read_ids = _get_read_ids(current_user.id, [n.id for n in notifs], db)
    return {"unread": sum(1 for n in notifs if n.id not in read_ids)}


@router.patch("/read-all", status_code=status.HTTP_200_OK)
def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    notifs = _get_user_notifications(current_user.id, db)
    read_ids = _get_read_ids(current_user.id, [n.id for n in notifs], db)
    new_reads = [
        NotificationRead(notification_id=n.id, user_id=current_user.id)
        for n in notifs
        if n.id not in read_ids
    ]
    if new_reads:
        db.add_all(new_reads)
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
    already_read = _get_read_ids(current_user.id, [notification_id], db)
    if notification_id not in already_read:
        db.add(NotificationRead(notification_id=n.id, user_id=current_user.id))
        db.commit()
    return {"message": "Marked as read."}
