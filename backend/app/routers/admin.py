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

from datetime import datetime, date, timedelta
from io import BytesIO
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
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
from ..models.payment_transaction import PaymentTransaction
from ..models.feature_usage import FeatureUsage
from ..utils.security import get_current_admin

_FEATURE_LABELS = {
    "input_questions":    {"label": "Quiz Uploads",         "icon": "✏️"},
    "ai_generate":        {"label": "AI Generates",         "icon": "🤖"},
    "study_zone":         {"label": "Study Zone Sessions",  "icon": "📖"},
    "youtube_resources":  {"label": "YouTube Resources",    "icon": "▶️"},
    "visual_explanation": {"label": "Visual Explanations",  "icon": "🎨"},
    "ask_friends":        {"label": "Ask Friends",          "icon": "💬"},
    "ai_recommendations": {"label": "AI Recommendations",   "icon": "🎯"},
    "challenge":          {"label": "Challenges",           "icon": "⚔️"},
    "study_strategy":     {"label": "Study Strategy",       "icon": "📋"},
    "model_groq":         {"label": "Groq (AI) Calls",      "icon": "⚡"},
    "model_openai":       {"label": "ChatGPT Calls",        "icon": "🧠"},
    "model_claude":       {"label": "Claude Calls",         "icon": "✨"},
}

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
    free_users    = db.query(func.count(User.id)).filter(User.subscription_plan == "free").scalar() or 0
    basic_users   = db.query(func.count(User.id)).filter(User.subscription_plan == "basic").scalar() or 0
    pro_users     = db.query(func.count(User.id)).filter(User.subscription_plan.in_(["pro", "max"])).scalar() or 0
    paid_users    = basic_users + pro_users
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

    # Push subscribers
    from ..services.push_service import get_subscriber_count
    push_subscribers = get_subscriber_count(db)

    return {
        "total_users":              total_users,
        "active_users":             active_users,
        "inactive_users":           total_users - active_users,
        "free_users":               free_users,
        "basic_users":              basic_users,
        "pro_users":                pro_users,
        "paid_users":               paid_users,
        "premium_users":            paid_users,
        "new_users_today":          new_today,
        "total_quizzes":            total_quizzes,
        "total_questions":          total_questions,
        "total_attempts":           total_attempts,
        "total_brainstorm_sessions": int(bs_agg.total_sessions or 0),
        "total_brainstorm_time":    _fmt_duration(float(bs_agg.total_seconds or 0)),
        "avg_brainstorm_duration":  _fmt_duration(float(bs_agg.avg_seconds or 0)),
        "push_subscribers":         push_subscribers,
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
        q = q.filter(User.subscription_plan.in_(["basic", "pro"]))
    elif filter == "free":
        q = q.filter(User.subscription_plan == "free")

    total = q.count()
    rows  = q.order_by(User.created_at.desc()).offset((page - 1) * limit).limit(limit).all()

    users = []
    for row in rows:
        u: User = row[0]
        users.append({
            "id":             str(u.id),
            "full_name":      u.full_name,
            "email":          u.email,
            "subscription_plan":   u.subscription_plan or "free",
            "subscription_expiry": u.subscription_expiry.isoformat() if u.subscription_expiry else None,
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

    # Feature usage breakdown
    fu_rows = (
        db.query(FeatureUsage)
        .filter(FeatureUsage.user_id == user_id)
        .all()
    )
    feature_usage = []
    for row in fu_rows:
        meta = _FEATURE_LABELS.get(row.feature_name, {"label": row.feature_name, "icon": "🔧"})
        feature_usage.append({
            "feature":    row.feature_name,
            "label":      meta["label"],
            "icon":       meta["icon"],
            "count":      row.usage_count,
            "last_used":  row.last_used.isoformat() if row.last_used else None,
        })
    # Sort: highest usage first
    feature_usage.sort(key=lambda x: -x["count"])

    # Payment history for this user
    payments = (
        db.query(PaymentTransaction)
        .filter(PaymentTransaction.user_id == user_id)
        .order_by(PaymentTransaction.paid_at.desc())
        .limit(10)
        .all()
    )
    payment_list = [
        {
            "plan":       p.plan,
            "cycle":      p.cycle,
            "amount_kobo": p.amount_kobo,
            "paid_at":    p.paid_at.isoformat(),
        }
        for p in payments
    ]

    return {
        "id":                   str(user.id),
        "full_name":            user.full_name,
        "email":                user.email,
        "department":           user.course,
        "university":           user.school,
        "level":                user.level,
        "subscription_plan":   user.subscription_plan or "free",
        "subscription_expiry": user.subscription_expiry.isoformat() if user.subscription_expiry else None,
        "is_active":            user.is_active,
        "is_admin":             user.is_admin,
        "created_at":           user.created_at.isoformat(),
        "last_login_at":        user.last_login_at.isoformat() if user.last_login_at else None,
        "subscription_start":   user.subscription_start.isoformat() if user.subscription_start else None,
        "total_quizzes":        total_quizzes,
        "total_attempts":       total_attempts,
        "average_score":        avg_score,
        "brainstorm_sessions":  bs_sessions,
        "brainstorm_messages":  bs_messages,
        "brainstorm_total_time": _fmt_duration(bs_total_seconds),
        "brainstorm_total_seconds": int(bs_total_seconds),
        "brainstorm_session_list":  session_list,
        "feature_usage":            feature_usage,
        "payment_history":          payment_list,
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

    # Also deliver as a device push notification
    try:
        from ..services.push_service import send_push_to_user, send_push_broadcast
        if target_id:
            send_push_to_user(db, target_id, payload.title, payload.message)
        else:
            send_push_broadcast(db, payload.title, payload.message)
    except Exception:
        pass  # Push failure must not block in-app notification delivery

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
        db.query(User.school, User.level, func.count(User.id).label("cnt"))
        .filter(User.is_admin == False)  # noqa: E712
        .group_by(User.school, User.level)
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


# ── PDF Export ────────────────────────────────────────────────────────────────

@router.get("/export-users/pdf")
def export_users_pdf(
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Export all non-admin users as a formatted PDF."""
    users = (
        db.query(User)
        .filter(User.is_admin == False)  # noqa: E712
        .order_by(User.created_at.desc())
        .all()
    )

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "pdf_title",
        parent=styles["Heading1"],
        fontSize=18,
        alignment=TA_CENTER,
        spaceAfter=4,
        textColor=colors.HexColor("#0077FF"),
    )
    subtitle_style = ParagraphStyle(
        "pdf_subtitle",
        parent=styles["Normal"],
        fontSize=9,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#888888"),
        spaceAfter=16,
    )

    generated_at = datetime.utcnow().strftime("%d %b %Y, %H:%M UTC")
    elements = [
        Paragraph("Pritis User List", title_style),
        Paragraph(f"Generated: {generated_at}  •  {len(users)} users", subtitle_style),
        Spacer(1, 0.3 * cm),
    ]

    # A4 landscape usable width ≈ 25.7 cm with 2 cm margins each side
    col_widths = [0.8 * cm, 5 * cm, 7.5 * cm, 6 * cm, 2 * cm, 2 * cm, 2.4 * cm]
    header = ["#", "Full Name", "Email", "School / University", "Plan", "Status", "Joined"]
    rows = [header]
    for i, u in enumerate(users, 1):
        plan         = (u.subscription_plan or "free").capitalize()
        status_label = "Active" if u.is_active else "Inactive"
        school       = (u.school or u.university or "—")[:60]
        joined       = u.created_at.strftime("%d %b %Y") if u.created_at else "—"
        rows.append([str(i), u.full_name or "—", u.email or "—", school, plan, status_label, joined])

    table = Table(rows, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        # Header
        ("BACKGROUND",    (0, 0), (-1, 0), colors.HexColor("#0077FF")),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 9),
        ("TOPPADDING",    (0, 0), (-1, 0), 7),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
        ("LINEBELOW",     (0, 0), (-1, 0), 1.5, colors.HexColor("#0055CC")),
        # Data rows
        ("FONTNAME",      (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE",      (0, 1), (-1, -1), 8),
        ("TOPPADDING",    (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, colors.HexColor("#F0F7FF")]),
        # Grid
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#DDDDDD")),
        # Alignment
        ("ALIGN",         (0, 0), (0, -1), "CENTER"),
        ("ALIGN",         (4, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(table)

    doc.build(elements)
    buffer.seek(0)

    filename = f"pritis-users-{datetime.utcnow().strftime('%Y-%m-%d')}.pdf"
    return Response(
        content=buffer.read(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── Finance ───────────────────────────────────────────────────────────────────

@router.get("/finance/payments")
def finance_payments(
    page:  int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    admin: User    = Depends(get_current_admin),
    db:    Session = Depends(get_db),
):
    """Return paginated payment transactions + summary stats."""
    total_revenue = db.query(
        func.coalesce(func.sum(PaymentTransaction.amount_kobo), 0)
    ).scalar() or 0

    total_txns = db.query(func.count(PaymentTransaction.id)).scalar() or 0

    paying_users = db.query(func.count(func.distinct(PaymentTransaction.user_id))).scalar() or 0

    txns_q = db.query(PaymentTransaction).order_by(PaymentTransaction.paid_at.desc())
    total  = txns_q.count()
    rows   = txns_q.offset((page - 1) * limit).limit(limit).all()

    return {
        "summary": {
            "total_revenue_kobo": int(total_revenue),
            "total_transactions": int(total_txns),
            "paying_users":       int(paying_users),
        },
        "transactions": [
            {
                "id":           str(t.id),
                "email":        t.email,
                "full_name":    t.full_name or "—",
                "amount_kobo":  t.amount_kobo,
                "plan":         t.plan,
                "cycle":        t.cycle,
                "reference":    t.reference,
                "paid_at":      t.paid_at.isoformat(),
            }
            for t in rows
        ],
        "total": total,
        "page":  page,
        "pages": max(1, -(-total // limit)),
    }


@router.get("/finance/export/pdf")
def finance_export_pdf(
    admin: User    = Depends(get_current_admin),
    db:    Session = Depends(get_db),
):
    """Export payment transactions as a PDF report."""
    txns = db.query(PaymentTransaction).order_by(PaymentTransaction.paid_at.desc()).all()

    total_kobo = sum(t.amount_kobo for t in txns)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "pdf_title",
        parent=styles["Heading1"],
        fontSize=18,
        alignment=TA_CENTER,
        spaceAfter=4,
        textColor=colors.HexColor("#0077FF"),
    )
    subtitle_style = ParagraphStyle(
        "pdf_subtitle",
        parent=styles["Normal"],
        fontSize=9,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#888888"),
        spaceAfter=16,
    )

    generated_at = datetime.utcnow().strftime("%d %b %Y, %H:%M UTC")
    total_naira  = f"₦{total_kobo // 100:,}"
    elements = [
        Paragraph("Pritis Payment Report", title_style),
        Paragraph(
            f"Generated: {generated_at}  •  {len(txns)} transactions  •  Total Revenue: {total_naira}",
            subtitle_style,
        ),
        Spacer(1, 0.3 * cm),
    ]

    col_widths = [0.8 * cm, 5.5 * cm, 6.5 * cm, 2.2 * cm, 2.2 * cm, 2.2 * cm, 6 * cm, 3 * cm]
    header = ["#", "Full Name", "Email", "Plan", "Cycle", "Amount", "Reference", "Date"]
    rows = [header]
    for i, t in enumerate(txns, 1):
        rows.append([
            str(i),
            (t.full_name or "—")[:40],
            (t.email or "—")[:50],
            t.plan.capitalize(),
            t.cycle.capitalize(),
            f"₦{t.amount_kobo // 100:,}",
            t.reference[:40],
            t.paid_at.strftime("%d %b %Y") if t.paid_at else "—",
        ])

    table = Table(rows, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), colors.HexColor("#0077FF")),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 9),
        ("TOPPADDING",    (0, 0), (-1, 0), 7),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
        ("LINEBELOW",     (0, 0), (-1, 0), 1.5, colors.HexColor("#0055CC")),
        ("FONTNAME",      (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE",      (0, 1), (-1, -1), 8),
        ("TOPPADDING",    (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, colors.HexColor("#F0F7FF")]),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#DDDDDD")),
        ("ALIGN",         (0, 0), (0, -1), "CENTER"),
        ("ALIGN",         (5, 0), (5, -1), "RIGHT"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(table)

    doc.build(elements)
    buffer.seek(0)

    filename = f"pritis-payments-{datetime.utcnow().strftime('%Y-%m-%d')}.pdf"
    return Response(
        content=buffer.read(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── Analytics Trends ──────────────────────────────────────────────────────────

@router.get("/analytics/trends")
def analytics_trends(
    days: int  = Query(30, ge=7, le=90),
    admin: User    = Depends(get_current_admin),
    db:    Session = Depends(get_db),
):
    """Return time-series data for charts: new users, revenue, plan distribution."""
    today      = date.today()
    start_date = today - timedelta(days=days - 1)

    # ── Daily new users ───────────────────────────────────────────────────────
    user_rows = (
        db.query(
            func.date(User.created_at).label("day"),
            func.count(User.id).label("cnt"),
        )
        .filter(
            func.date(User.created_at) >= start_date,
            User.is_admin == False,   # noqa: E712
        )
        .group_by(func.date(User.created_at))
        .all()
    )
    user_by_day = {str(r.day): int(r.cnt) for r in user_rows}

    # ── Daily revenue ─────────────────────────────────────────────────────────
    rev_rows = (
        db.query(
            func.date(PaymentTransaction.paid_at).label("day"),
            func.sum(PaymentTransaction.amount_kobo).label("kobo"),
        )
        .filter(func.date(PaymentTransaction.paid_at) >= start_date)
        .group_by(func.date(PaymentTransaction.paid_at))
        .all()
    )
    rev_by_day = {str(r.day): int(r.kobo) for r in rev_rows}

    # ── Fill all days in range ────────────────────────────────────────────────
    labels        = []
    daily_users   = []
    daily_revenue = []
    for i in range(days):
        d = start_date + timedelta(days=i)
        ds = str(d)
        labels.append(d.strftime("%d %b"))
        daily_users.append(user_by_day.get(ds, 0))
        daily_revenue.append(round(rev_by_day.get(ds, 0) / 100, 2))  # naira

    # ── Plan distribution ─────────────────────────────────────────────────────
    plan_rows = (
        db.query(User.subscription_plan, func.count(User.id).label("cnt"))
        .filter(User.is_admin == False)   # noqa: E712
        .group_by(User.subscription_plan)
        .all()
    )
    plan_dist = {(r.subscription_plan or "free"): int(r.cnt) for r in plan_rows}

    # ── Cumulative growth ─────────────────────────────────────────────────────
    total_before = (
        db.query(func.count(User.id))
        .filter(
            func.date(User.created_at) < start_date,
            User.is_admin == False,   # noqa: E712
        )
        .scalar() or 0
    )
    cumulative = []
    running = total_before
    for cnt in daily_users:
        running += cnt
        cumulative.append(running)

    return {
        "labels":        labels,
        "daily_users":   daily_users,
        "daily_revenue": daily_revenue,
        "cumulative":    cumulative,
        "plan_dist":     plan_dist,
    }


# ── Bulk Email ────────────────────────────────────────────────────────────────

@router.get("/users/emails")
def get_all_emails(
    admin: User    = Depends(get_current_admin),
    db:    Session = Depends(get_db),
):
    """Return a comma-separated string of all active non-admin user emails."""
    rows = (
        db.query(User.email)
        .filter(User.is_admin == False, User.is_active == True)   # noqa: E712
        .order_by(User.email)
        .all()
    )
    emails = [r.email for r in rows if r.email]
    return {"emails": ", ".join(emails), "count": len(emails)}
