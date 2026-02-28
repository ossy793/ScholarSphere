"""
Admin Router
------------
All endpoints require a valid JWT belonging to a user with is_admin=True.

GET  /admin/stats                  — platform-wide statistics
GET  /admin/users                  — paginated + searchable user list
PATCH /admin/users/{id}/activate   — re-activate a deactivated user
PATCH /admin/users/{id}/deactivate — soft-disable a user (blocks login + API)
DELETE /admin/users/{id}           — permanently delete a user and all their data
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.user import User
from ..models.quiz import Quiz
from ..models.question import Question
from ..models.attempt import Attempt
from ..models.brainstorm import BrainstormSession, BrainstormMessage
from ..models.notification import Notification
from ..utils.security import get_current_admin


class SendNotificationRequest(BaseModel):
    title:          str
    message:        str
    target_user_id: Optional[str] = None  # null/omit = broadcast to all

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Stats ─────────────────────────────────────────────────────────────────────

@router.get("/stats")
def admin_stats(
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    today = datetime.utcnow().date()

    total_users   = db.query(func.count(User.id)).scalar() or 0
    active_users  = db.query(func.count(User.id)).filter(User.is_active == True).scalar() or 0    # noqa: E712
    premium_users = db.query(func.count(User.id)).filter(User.is_premium == True).scalar() or 0   # noqa: E712
    new_today     = (
        db.query(func.count(User.id))
        .filter(func.date(User.created_at) == today)
        .scalar() or 0
    )
    total_quizzes   = db.query(func.count(Quiz.id)).scalar() or 0
    total_questions = db.query(func.count(Question.id)).scalar() or 0
    total_attempts  = db.query(func.count(Attempt.id)).scalar() or 0

    return {
        "total_users":      total_users,
        "active_users":     active_users,
        "inactive_users":   total_users - active_users,
        "premium_users":    premium_users,
        "free_users":       total_users - premium_users,
        "new_users_today":  new_today,
        "total_quizzes":    total_quizzes,
        "total_questions":  total_questions,
        "total_attempts":   total_attempts,
    }


# ── User list ─────────────────────────────────────────────────────────────────

@router.get("/users")
def admin_list_users(
    search: Optional[str] = Query(None),
    filter: Optional[str] = Query("all"),   # all | active | inactive | premium | free
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    # Subqueries for per-user quiz and attempt counts (avoids N+1)
    quiz_sub = (
        db.query(Quiz.user_id, func.count(Quiz.id).label("quiz_count"))
        .group_by(Quiz.user_id)
        .subquery()
    )
    attempt_sub = (
        db.query(Attempt.user_id, func.count(Attempt.id).label("attempt_count"))
        .group_by(Attempt.user_id)
        .subquery()
    )

    q = (
        db.query(
            User,
            func.coalesce(quiz_sub.c.quiz_count, 0).label("quiz_count"),
            func.coalesce(attempt_sub.c.attempt_count, 0).label("attempt_count"),
        )
        .outerjoin(quiz_sub,    User.id == quiz_sub.c.user_id)
        .outerjoin(attempt_sub, User.id == attempt_sub.c.user_id)
    )

    # Search
    if search and search.strip():
        term = f"%{search.strip()}%"
        q = q.filter(or_(User.full_name.ilike(term), User.email.ilike(term)))

    # Filter
    if filter == "active":
        q = q.filter(User.is_active == True)        # noqa: E712
    elif filter == "inactive":
        q = q.filter(User.is_active == False)       # noqa: E712
    elif filter == "premium":
        q = q.filter(User.is_premium == True)       # noqa: E712
    elif filter == "free":
        q = q.filter(User.is_premium == False)      # noqa: E712

    total = q.count()
    rows  = q.order_by(User.created_at.desc()).offset((page - 1) * limit).limit(limit).all()

    users = []
    for row in rows:
        u: User = row[0]
        users.append({
            "id":             str(u.id),
            "full_name":      u.full_name,
            "email":          u.email,
            "is_premium":     u.is_premium,
            "is_active":      u.is_active,
            "is_admin":       u.is_admin,
            "created_at":     u.created_at.isoformat(),
            "total_quizzes":  int(row[1]),
            "total_attempts": int(row[2]),
        })

    return {
        "users": users,
        "total": total,
        "page":  page,
        "pages": max(1, -(-total // limit)),  # ceiling division
    }


# ── Activate / Deactivate ─────────────────────────────────────────────────────

@router.patch("/users/{user_id}/activate", status_code=status.HTTP_200_OK)
def activate_user(
    user_id: UUID,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_admin:
        raise HTTPException(status_code=400, detail="Cannot modify another admin account")
    user.is_active = True
    db.commit()
    return {"message": f"{user.full_name} has been activated."}


@router.patch("/users/{user_id}/deactivate", status_code=status.HTTP_200_OK)
def deactivate_user(
    user_id: UUID,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account")
    if user.is_admin:
        raise HTTPException(status_code=400, detail="Cannot deactivate another admin account")
    user.is_active = False
    db.commit()
    return {"message": f"{user.full_name} has been deactivated."}


# ── Delete ────────────────────────────────────────────────────────────────────

@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: UUID,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    if user.is_admin:
        raise HTTPException(status_code=400, detail="Cannot delete another admin account")
    db.delete(user)
    db.commit()


# ── User detail ───────────────────────────────────────────────────────────────

@router.get("/users/{user_id}/detail")
def user_detail(
    user_id: UUID,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Return full profile + activity stats for one user."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Quiz stats
    total_quizzes = db.query(func.count(Quiz.id)).filter(Quiz.user_id == user_id).scalar() or 0

    # Attempt / score stats
    attempts = db.query(Attempt).filter(Attempt.user_id == user_id).all()
    total_attempts = len(attempts)
    avg_score = (
        round(sum(a.score for a in attempts) / total_attempts, 1) if total_attempts else None
    )

    # Brainstorm stats
    bs_sessions  = db.query(func.count(BrainstormSession.id)).filter(BrainstormSession.user_id == user_id).scalar() or 0
    bs_messages  = (
        db.query(func.count(BrainstormMessage.id))
        .join(BrainstormSession, BrainstormMessage.session_id == BrainstormSession.id)
        .filter(BrainstormSession.user_id == user_id)
        .scalar() or 0
    )

    return {
        "id":                   str(user.id),
        "full_name":            user.full_name,
        "email":                user.email,
        "department":           user.department,
        "university":           user.university,
        "level":                user.level,
        "is_premium":           user.is_premium,
        "is_active":            user.is_active,
        "is_admin":             user.is_admin,
        "created_at":           user.created_at.isoformat(),
        "last_login_at":        user.last_login_at.isoformat() if user.last_login_at else None,
        "premium_activated_at": user.premium_activated_at.isoformat() if user.premium_activated_at else None,
        "total_quizzes":        total_quizzes,
        "total_attempts":       total_attempts,
        "average_score":        avg_score,
        "brainstorm_sessions":  bs_sessions,
        "brainstorm_messages":  bs_messages,
    }


# ── Notifications ─────────────────────────────────────────────────────────────

@router.post("/notifications", status_code=status.HTTP_201_CREATED)
def send_notification(
    payload: SendNotificationRequest,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Send a notification to one user or broadcast to all."""
    target_id = None
    if payload.target_user_id:
        target = db.query(User).filter(User.id == payload.target_user_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Target user not found")
        target_id = target.id

    notif = Notification(
        title=payload.title,
        message=payload.message,
        target_user_id=target_id,
        created_by_id=admin.id,
    )
    db.add(notif)
    db.commit()
    db.refresh(notif)
    scope = f"user {target.full_name}" if target_id else "all users"
    return {"message": f"Notification sent to {scope}.", "id": str(notif.id)}


@router.get("/notifications")
def list_sent_notifications(
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """List all notifications sent by admins (most recent first)."""
    notifs = db.query(Notification).order_by(Notification.created_at.desc()).limit(100).all()
    return [
        {
            "id":             str(n.id),
            "title":          n.title,
            "message":        n.message,
            "target_user_id": str(n.target_user_id) if n.target_user_id else None,
            "is_broadcast":   n.target_user_id is None,
            "created_at":     n.created_at.isoformat(),
        }
        for n in notifs
    ]
