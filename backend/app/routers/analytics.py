import hashlib
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import List, Dict, Any
from uuid import UUID

from ..database import get_db
from ..models.user import User
from ..models.quiz import Quiz
from ..models.attempt import Attempt
from ..models.course import Course
from ..models.brainstorm import BrainstormSession
from ..services import analytics_service
from ..services.ai_service import generate_recommendations
from ..utils.security import get_current_user

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview")
def get_overview(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    return analytics_service.get_overview(current_user.id, db)


@router.get("/trend")
def get_score_trend(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    return analytics_service.get_score_trend(current_user.id, db)


@router.get("/courses/{course_id}")
def get_course_analytics(
    course_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    return analytics_service.get_course_analytics(course_id, current_user.id, db)


@router.get("/courses-summary")
def get_courses_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    return analytics_service.get_courses_summary(current_user.id, db)


@router.get("/brainstorm-streak")
def get_brainstorm_streak(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    streak = analytics_service.get_brainstorm_streak(current_user.id, db)
    return {"streak": streak}


@router.get("/update-token")
def get_update_token(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, str]:
    """
    Returns a lightweight token representing the user's latest activity state.
    The frontend polls this endpoint and shows a 'new update' alert when the
    token changes compared to what it last saw (stored in sessionStorage).
    """
    # Collect the latest updated_at / created_at timestamps across all user data
    timestamps = []

    latest_quiz = db.query(func.max(Quiz.updated_at)).filter(Quiz.user_id == current_user.id).scalar()
    if latest_quiz:
        timestamps.append(str(latest_quiz))

    latest_attempt = db.query(func.max(Attempt.completed_at)).filter(Attempt.user_id == current_user.id).scalar()
    if latest_attempt:
        timestamps.append(str(latest_attempt))

    latest_course = db.query(func.max(Course.updated_at)).filter(Course.user_id == current_user.id).scalar()
    if latest_course:
        timestamps.append(str(latest_course))

    latest_session = db.query(func.max(BrainstormSession.updated_at)).filter(
        BrainstormSession.user_id == current_user.id
    ).scalar()
    if latest_session:
        timestamps.append(str(latest_session))

    raw = "|".join(timestamps) or "empty"
    token = hashlib.md5(raw.encode()).hexdigest()[:12]
    return {"token": token}


@router.get("/recommendations")
def get_recommendations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Generate AI-powered study recommendations from the user's quiz performance."""
    perf_data = analytics_service.get_performance_for_recommendations(current_user.id, db)
    if not perf_data:
        return []
    try:
        return generate_recommendations(perf_data)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
