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
from sqlalchemy import Numeric
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.user import User
from ..models.quiz import Quiz
from ..models.question import Question
from ..models.attempt import Attempt
from ..models.brainstorm import BrainstormSession, BrainstormMessage
from ..models.notification import Notification
from ..models.leaderboard import LeaderboardReset
from ..utils.security import get_current_admin

_VALID_METRICS = {"brainstorm_time", "brainstorm_messages", "quizzes_generated", "avg_score"}


class SendNotificationRequest(BaseModel):
    title:          str
    message:        str
    target_user_id: Optional[str] = None  # null/omit = broadcast to all

router = APIRouter(prefix="/admin", tags=["admin"])


def _fmt_duration(seconds: float | None) -> str:
    """Format seconds into a human-readable string like '2h 15m' or '< 1 min'."""
    if not seconds or seconds < 60:
        return "< 1 min"
    minutes = int(seconds // 60)
    hours   = minutes // 60
    mins    = minutes % 60
    if hours:
        return f"{hours}h {mins}m" if mins else f"{hours}h"
    return f"{mins}m"


def _bs_duration_subquery(db: Session):
    """
    Returns a subquery with columns (session_id, user_id, duration_seconds).
    Duration = seconds from session created_at to the last message created_at.
    Sessions with no messages get duration = 0.
    """
    last_msg = (
        db.query(
            BrainstormMessage.session_id,
            func.max(BrainstormMessage.created_at).label("last_msg_at"),
        )
        .group_by(BrainstormMessage.session_id)
        .subquery()
    )
    return (
        db.query(
            BrainstormSession.id.label("session_id"),
            BrainstormSession.user_id,
            func.coalesce(
                func.extract(
                    "epoch",
                    last_msg.c.last_msg_at - BrainstormSession.created_at,
                ),
                0,
            ).label("duration_seconds"),
        )
        .outerjoin(last_msg, BrainstormSession.id == last_msg.c.session_id)
        .subquery()
    )


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

    # Brainstorm duration stats
    dur_sub = _bs_duration_subquery(db)
    bs_agg  = db.query(
        func.count(dur_sub.c.session_id).label("total_sessions"),
        func.coalesce(func.sum(dur_sub.c.duration_seconds), 0).label("total_seconds"),
        func.coalesce(func.avg(dur_sub.c.duration_seconds), 0).label("avg_seconds"),
    ).first()

    return {
        "total_users":              total_users,
        "active_users":             active_users,
        "inactive_users":           total_users - active_users,
        "premium_users":            premium_users,
        "free_users":               total_users - premium_users,
        "new_users_today":          new_today,
        "total_quizzes":            total_quizzes,
        "total_questions":          total_questions,
        "total_attempts":           total_attempts,
        "total_brainstorm_sessions": int(bs_agg.total_sessions or 0),
        "total_brainstorm_time":    _fmt_duration(float(bs_agg.total_seconds or 0)),
        "avg_brainstorm_duration":  _fmt_duration(float(bs_agg.avg_seconds or 0)),
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
        round(sum(a.score for a in attempts if a.score is not None) / len([a for a in attempts if a.score is not None]), 1) if any(a.score is not None for a in attempts) else None
    )

    # Brainstorm stats — sessions, messages, and total time spent
    dur_sub = _bs_duration_subquery(db)
    bs_agg = db.query(
        func.count(dur_sub.c.session_id).label("sessions"),
        func.coalesce(func.sum(dur_sub.c.duration_seconds), 0).label("total_seconds"),
    ).filter(dur_sub.c.user_id == user_id).first()

    bs_sessions       = int(bs_agg.sessions or 0)
    bs_total_seconds  = float(bs_agg.total_seconds or 0)

    bs_messages = (
        db.query(func.count(BrainstormMessage.id))
        .join(BrainstormSession, BrainstormMessage.session_id == BrainstormSession.id)
        .filter(BrainstormSession.user_id == user_id)
        .scalar() or 0
    )

    # Last 5 brainstorm sessions with individual durations
    last_msg = (
        db.query(
            BrainstormMessage.session_id,
            func.max(BrainstormMessage.created_at).label("last_msg_at"),
            func.count(BrainstormMessage.id).label("msg_count"),
        )
        .group_by(BrainstormMessage.session_id)
        .subquery()
    )
    recent_sessions = (
        db.query(
            BrainstormSession,
            func.coalesce(
                func.extract("epoch", last_msg.c.last_msg_at - BrainstormSession.created_at), 0
            ).label("duration_seconds"),
            func.coalesce(last_msg.c.msg_count, 0).label("msg_count"),
        )
        .outerjoin(last_msg, BrainstormSession.id == last_msg.c.session_id)
        .filter(BrainstormSession.user_id == user_id)
        .order_by(BrainstormSession.updated_at.desc())
        .limit(5)
        .all()
    )

    session_list = [
        {
            "title":            s.BrainstormSession.title,
            "created_at":       s.BrainstormSession.created_at.isoformat(),
            "duration":         _fmt_duration(float(s.duration_seconds or 0)),
            "duration_seconds": int(s.duration_seconds or 0),
            "messages":         int(s.msg_count or 0),
        }
        for s in recent_sessions
    ]

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
        "brainstorm_total_time": _fmt_duration(bs_total_seconds),
        "brainstorm_total_seconds": int(bs_total_seconds),
        "brainstorm_session_list":  session_list,
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


# ── Leaderboards ──────────────────────────────────────────────────────────────

def _get_reset_at(db: Session, metric: str) -> datetime:
    """Return the reset timestamp for a metric, or datetime.min if never reset."""
    row = db.query(LeaderboardReset).filter(LeaderboardReset.metric == metric).first()
    return row.reset_at if row else datetime.min


@router.get("/leaderboard")
def get_leaderboards(
    limit: int = Query(20, ge=1, le=100),
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Return top-N user rankings for all four leaderboard metrics."""
    resets = {
        m: _get_reset_at(db, m) for m in _VALID_METRICS
    }
    reset_iso = {m: (v.isoformat() if v != datetime.min else None) for m, v in resets.items()}

    # ── 1. Brainstorm time ────────────────────────────────────────────────────
    last_msg_sq = (
        db.query(
            BrainstormMessage.session_id,
            func.max(BrainstormMessage.created_at).label("last_msg_at"),
        )
        .group_by(BrainstormMessage.session_id)
        .subquery()
    )
    dur_sq = (
        db.query(
            BrainstormSession.user_id,
            func.coalesce(
                func.extract("epoch", last_msg_sq.c.last_msg_at - BrainstormSession.created_at),
                0,
            ).label("dur"),
        )
        .outerjoin(last_msg_sq, BrainstormSession.id == last_msg_sq.c.session_id)
        .filter(BrainstormSession.created_at >= resets["brainstorm_time"])
        .subquery()
    )
    bt_rows = (
        db.query(
            User.id,
            User.full_name,
            User.email,
            func.coalesce(func.sum(dur_sq.c.dur), 0).label("total_sec"),
        )
        .join(dur_sq, User.id == dur_sq.c.user_id)
        .filter(User.is_admin == False)  # noqa: E712
        .group_by(User.id, User.full_name, User.email)
        .order_by(func.sum(dur_sq.c.dur).desc())
        .limit(limit)
        .all()
    )
    brainstorm_time = [
        {
            "rank": i + 1,
            "user_id": str(r.id),
            "full_name": r.full_name,
            "email": r.email,
            "value": int(r.total_sec),
            "display": _fmt_duration(float(r.total_sec)),
        }
        for i, r in enumerate(bt_rows)
    ]

    # ── 2. Brainstorm messages ────────────────────────────────────────────────
    bm_rows = (
        db.query(
            User.id,
            User.full_name,
            User.email,
            func.count(BrainstormMessage.id).label("msg_count"),
        )
        .join(BrainstormSession, BrainstormSession.user_id == User.id)
        .join(BrainstormMessage, BrainstormMessage.session_id == BrainstormSession.id)
        .filter(
            User.is_admin == False,  # noqa: E712
            BrainstormSession.created_at >= resets["brainstorm_messages"],
        )
        .group_by(User.id, User.full_name, User.email)
        .order_by(func.count(BrainstormMessage.id).desc())
        .limit(limit)
        .all()
    )
    brainstorm_messages = [
        {
            "rank": i + 1,
            "user_id": str(r.id),
            "full_name": r.full_name,
            "email": r.email,
            "value": int(r.msg_count),
            "display": f"{r.msg_count:,}",
        }
        for i, r in enumerate(bm_rows)
    ]

    # ── 3. Quizzes generated ──────────────────────────────────────────────────
    qg_rows = (
        db.query(
            User.id,
            User.full_name,
            User.email,
            func.count(Quiz.id).label("quiz_count"),
        )
        .join(Quiz, Quiz.user_id == User.id)
        .filter(
            User.is_admin == False,  # noqa: E712
            Quiz.created_at >= resets["quizzes_generated"],
        )
        .group_by(User.id, User.full_name, User.email)
        .order_by(func.count(Quiz.id).desc())
        .limit(limit)
        .all()
    )
    quizzes_generated = [
        {
            "rank": i + 1,
            "user_id": str(r.id),
            "full_name": r.full_name,
            "email": r.email,
            "value": int(r.quiz_count),
            "display": str(r.quiz_count),
        }
        for i, r in enumerate(qg_rows)
    ]

    # ── 4. Average score ──────────────────────────────────────────────────────
    as_rows = (
        db.query(
            User.id,
            User.full_name,
            User.email,
            func.round(func.avg(Attempt.score).cast(Numeric), 1).label("avg_sc"),
            func.count(Attempt.id).label("attempts"),
        )
        .join(Attempt, Attempt.user_id == User.id)
        .filter(
            User.is_admin == False,  # noqa: E712
            Attempt.score.isnot(None),
            Attempt.started_at >= resets["avg_score"],
        )
        .group_by(User.id, User.full_name, User.email)
        .having(func.count(Attempt.id) >= 1)
        .order_by(func.avg(Attempt.score).desc())
        .limit(limit)
        .all()
    )
    avg_score = [
        {
            "rank": i + 1,
            "user_id": str(r.id),
            "full_name": r.full_name,
            "email": r.email,
            "value": float(r.avg_sc),
            "display": f"{r.avg_sc}%",
            "attempts": int(r.attempts),
        }
        for i, r in enumerate(as_rows)
    ]

    return {
        "brainstorm_time":     brainstorm_time,
        "brainstorm_messages": brainstorm_messages,
        "quizzes_generated":   quizzes_generated,
        "avg_score":           avg_score,
        "reset_timestamps":    reset_iso,
    }


@router.post("/leaderboard/{metric}/reset", status_code=status.HTTP_200_OK)
def reset_leaderboard(
    metric: str,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Reset (restart) a leaderboard by recording the current timestamp."""
    if metric not in _VALID_METRICS:
        raise HTTPException(status_code=400, detail=f"Invalid metric '{metric}'")
    now = datetime.utcnow()
    existing = db.query(LeaderboardReset).filter(LeaderboardReset.metric == metric).first()
    if existing:
        existing.reset_at = now
    else:
        db.add(LeaderboardReset(metric=metric, reset_at=now))
    db.commit()
    return {"message": f"Leaderboard '{metric}' has been reset.", "reset_at": now.isoformat()}


# ── Universities ───────────────────────────────────────────────────────────────

@router.get("/universities")
def get_universities(
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Return users grouped by university with level breakdown."""
    rows = (
        db.query(User.university, User.level, func.count(User.id).label("cnt"))
        .filter(User.is_admin == False)  # noqa: E712
        .group_by(User.university, User.level)
        .all()
    )

    uni_map: dict = {}
    for uni, level, cnt in rows:
        uni_key   = (uni or "").strip() or "Unspecified"
        level_key = (level or "").strip() or "Unspecified"
        if uni_key not in uni_map:
            uni_map[uni_key] = {"university": uni_key, "total": 0, "levels": {}}
        uni_map[uni_key]["total"] += cnt
        uni_map[uni_key]["levels"][level_key] = uni_map[uni_key]["levels"].get(level_key, 0) + cnt

    return sorted(uni_map.values(), key=lambda x: -x["total"])


# ── Notifications ─────────────────────────────────────────────────────────────

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
