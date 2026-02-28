"""
Analytics Service
-----------------
All database query logic for performance analytics:
- overview stats (counts + average score)
- score trend (last 20 completed attempts)
- per-course stats (quiz-level breakdown + trend)
- courses summary (avg score per course, for bar chart)
"""

from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.attempt import Attempt
from ..models.course import Course
from ..models.quiz import Quiz


def _avg_score_for_quiz_ids(quiz_ids: List, db: Session) -> Optional[float]:
    """Return rounded average score across a set of quiz IDs, or None if no data."""
    if not quiz_ids:
        return None
    result = (
        db.query(func.avg(Attempt.score))
        .filter(Attempt.quiz_id.in_(quiz_ids), Attempt.score.isnot(None))
        .scalar()
    )
    return round(float(result), 1) if result is not None else None


def get_overview(user_id, db: Session) -> Dict[str, Any]:
    """
    Return high-level statistics for the dashboard:
    - total courses, quizzes, attempts
    - average score across all attempts
    - name of the best-performing course
    """
    total_courses  = db.query(Course).filter(Course.user_id == user_id).count()
    total_quizzes  = db.query(Quiz).filter(Quiz.user_id == user_id).count()
    total_attempts = db.query(Attempt).filter(Attempt.user_id == user_id).count()

    global_avg_raw = (
        db.query(func.avg(Attempt.score))
        .filter(Attempt.user_id == user_id, Attempt.score.isnot(None))
        .scalar()
    )
    average_score = round(float(global_avg_raw), 1) if global_avg_raw is not None else 0.0

    # Determine the best-performing course by average attempt score
    best_course: Optional[str] = None
    best_avg = -1.0
    courses = db.query(Course).filter(Course.user_id == user_id).all()
    for course in courses:
        quiz_ids = [
            row.id for row in db.query(Quiz.id).filter(Quiz.course_id == course.id).all()
        ]
        course_avg = _avg_score_for_quiz_ids(quiz_ids, db)
        if course_avg is not None and course_avg > best_avg:
            best_avg = course_avg
            best_course = course.name

    return {
        "total_courses":  total_courses,
        "total_quizzes":  total_quizzes,
        "total_attempts": total_attempts,
        "average_score":  average_score,
        "best_course":    best_course,
    }


def get_score_trend(user_id, db: Session) -> List[Dict[str, Any]]:
    """
    Return the last 20 completed attempts (oldest → newest) for a line chart.
    Each entry: { date, score, quiz_id }
    """
    attempts = (
        db.query(Attempt)
        .filter(
            Attempt.user_id == user_id,
            Attempt.score.isnot(None),
            Attempt.completed_at.isnot(None),
        )
        .order_by(Attempt.completed_at.asc())
        .limit(20)
        .all()
    )
    return [
        {
            "date":    a.completed_at.strftime("%Y-%m-%d"),
            "score":   a.score,
            "quiz_id": str(a.quiz_id),
        }
        for a in attempts
    ]


def get_course_analytics(course_id: UUID, user_id, db: Session) -> Dict[str, Any]:
    """
    Return a detailed breakdown for a single course:
    - per-quiz attempt count and average score
    - score trend for all quizzes in the course
    """
    course = db.query(Course).filter(
        Course.id == course_id, Course.user_id == user_id
    ).first()
    if not course:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")

    quizzes = db.query(Quiz).filter(Quiz.course_id == course_id).all()

    quiz_stats = []
    for quiz in quizzes:
        scored_attempts = (
            db.query(Attempt)
            .filter(Attempt.quiz_id == quiz.id, Attempt.score.isnot(None))
            .all()
        )
        avg = (
            round(sum(a.score for a in scored_attempts) / len(scored_attempts), 1)
            if scored_attempts else None
        )
        quiz_stats.append({
            "quiz_id":       str(quiz.id),
            "quiz_title":    quiz.title,
            "attempt_count": len(scored_attempts),
            "average_score": avg,
        })

    all_quiz_ids = [q.id for q in quizzes]
    course_attempts = (
        db.query(Attempt)
        .filter(
            Attempt.quiz_id.in_(all_quiz_ids),
            Attempt.score.isnot(None),
            Attempt.completed_at.isnot(None),
        )
        .order_by(Attempt.completed_at.asc())
        .all()
    )
    score_trend = [
        {"date": a.completed_at.strftime("%Y-%m-%d"), "score": a.score}
        for a in course_attempts
    ]

    return {
        "course_id":   str(course_id),
        "course_name": course.name,
        "quizzes":     quiz_stats,
        "score_trend": score_trend,
    }


def get_courses_summary(user_id, db: Session) -> List[Dict[str, Any]]:
    """
    Return average score per course for the bar chart.
    Courses with no scored attempts will have average_score = null.
    """
    courses = db.query(Course).filter(Course.user_id == user_id).all()
    result = []
    for course in courses:
        quiz_ids = [
            row.id for row in db.query(Quiz.id).filter(Quiz.course_id == course.id).all()
        ]
        avg = _avg_score_for_quiz_ids(quiz_ids, db)
        result.append({"course_name": course.name, "average_score": avg})
    return result
