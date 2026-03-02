"""
Analytics Service
-----------------
All database query logic for performance analytics:
- overview stats (counts + average score)
- score trend (last 20 completed attempts)
- per-course stats (quiz-level breakdown + trend)
- courses summary (avg score per course, for bar chart)
"""

from collections import defaultdict
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.attempt import Attempt, AttemptAnswer
from ..models.course import Course
from ..models.quiz import Quiz
from ..models.question import Question


def get_overview(user_id, db: Session) -> Dict[str, Any]:
    """
    Return high-level statistics for the dashboard.
    Uses aggregated queries — no per-course loops.
    """
    total_courses  = db.query(func.count(Course.id)).filter(Course.user_id == user_id).scalar() or 0
    total_quizzes  = db.query(func.count(Quiz.id)).filter(Quiz.user_id == user_id).scalar() or 0
    total_attempts = db.query(func.count(Attempt.id)).filter(Attempt.user_id == user_id).scalar() or 0

    global_avg_raw = (
        db.query(func.avg(Attempt.score))
        .filter(Attempt.user_id == user_id, Attempt.score.isnot(None))
        .scalar()
    )
    average_score = round(float(global_avg_raw), 1) if global_avg_raw is not None else 0.0

    # Best course: single query with JOIN + GROUP BY
    best_row = (
        db.query(Course.name, func.avg(Attempt.score).label("avg_score"))
        .join(Quiz, Quiz.course_id == Course.id)
        .join(Attempt, Attempt.quiz_id == Quiz.id)
        .filter(Course.user_id == user_id, Attempt.score.isnot(None))
        .group_by(Course.id, Course.name)
        .order_by(func.avg(Attempt.score).desc())
        .first()
    )
    best_course = best_row.name if best_row else None

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
    Return a detailed breakdown for a single course.
    Fetches all attempts in one query, then groups in Python.
    """
    course = db.query(Course).filter(
        Course.id == course_id, Course.user_id == user_id
    ).first()
    if not course:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")

    quizzes = db.query(Quiz).filter(Quiz.course_id == course_id).all()
    all_quiz_ids = [q.id for q in quizzes]

    # Single query for all scored attempts across the course
    all_attempts = (
        db.query(Attempt)
        .filter(
            Attempt.quiz_id.in_(all_quiz_ids),
            Attempt.score.isnot(None),
        )
        .all()
    )

    attempts_by_quiz: Dict[str, list] = defaultdict(list)
    for a in all_attempts:
        attempts_by_quiz[str(a.quiz_id)].append(a)

    quiz_stats = []
    for quiz in quizzes:
        scored = attempts_by_quiz[str(quiz.id)]
        avg = round(sum(a.score for a in scored) / len(scored), 1) if scored else None
        quiz_stats.append({
            "quiz_id":       str(quiz.id),
            "quiz_title":    quiz.title,
            "attempt_count": len(scored),
            "average_score": avg,
        })

    # Score trend (ordered by date) built from the same data already fetched
    trend_attempts = sorted(
        [a for a in all_attempts if a.completed_at is not None],
        key=lambda a: a.completed_at,
    )
    score_trend = [
        {"date": a.completed_at.strftime("%Y-%m-%d"), "score": a.score}
        for a in trend_attempts
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
    Single query with LEFT JOIN — no per-course loops.
    """
    rows = (
        db.query(Course.name, func.avg(Attempt.score).label("avg_score"))
        .outerjoin(Quiz, Quiz.course_id == Course.id)
        .outerjoin(
            Attempt,
            (Attempt.quiz_id == Quiz.id) & Attempt.score.isnot(None),
        )
        .filter(Course.user_id == user_id)
        .group_by(Course.id, Course.name)
        .all()
    )
    return [
        {
            "course_name":   row.name,
            "average_score": round(float(row.avg_score), 1) if row.avg_score is not None else None,
        }
        for row in rows
    ]


def get_performance_for_recommendations(user_id, db: Session) -> List[Dict[str, Any]]:
    """
    Build a rich, course-grouped performance summary for AI recommendation generation.
    Uses the most recent completed attempt per quiz, including up to 5 wrong answers
    with question text so the AI can generate concept-specific suggestions.
    """
    # All completed, scored attempts — newest-first so we can dedupe by quiz
    attempts = (
        db.query(Attempt)
        .filter(
            Attempt.user_id == user_id,
            Attempt.score.isnot(None),
            Attempt.completed_at.isnot(None),
        )
        .order_by(Attempt.completed_at.desc())
        .all()
    )
    if not attempts:
        return []

    # Keep only the most recent attempt per quiz
    seen: set = set()
    latest_attempts = []
    for a in attempts:
        if a.quiz_id not in seen:
            seen.add(a.quiz_id)
            latest_attempts.append(a)

    # Batch-load quizzes and courses in two queries
    quiz_ids   = [a.quiz_id for a in latest_attempts]
    quizzes    = {q.id: q for q in db.query(Quiz).filter(Quiz.id.in_(quiz_ids)).all()}
    course_ids = list({q.course_id for q in quizzes.values()})
    courses    = {c.id: c for c in db.query(Course).filter(Course.id.in_(course_ids)).all()}

    # Batch-load wrong answers with question text in one JOIN query
    attempt_ids = [a.id for a in latest_attempts]
    wrong_rows = (
        db.query(
            AttemptAnswer.attempt_id,
            Question.question_text,
            AttemptAnswer.user_answer,
            Question.correct_answer,
        )
        .join(Question, Question.id == AttemptAnswer.question_id)
        .filter(
            AttemptAnswer.attempt_id.in_(attempt_ids),
            AttemptAnswer.is_correct == False,  # noqa: E712
        )
        .all()
    )

    # Group wrong answers by attempt_id (cap at 5 per attempt to keep tokens manageable)
    wrong_by_attempt: Dict[str, list] = defaultdict(list)
    for row in wrong_rows:
        aid = str(row.attempt_id)
        if len(wrong_by_attempt[aid]) < 5:
            wrong_by_attempt[aid].append({
                "question":       row.question_text,
                "user_answer":    row.user_answer or "(no answer)",
                "correct_answer": row.correct_answer,
            })

    # Organise by course name
    by_course: Dict[str, list] = defaultdict(list)
    for attempt in latest_attempts:
        quiz = quizzes.get(attempt.quiz_id)
        if not quiz:
            continue
        course = courses.get(quiz.course_id)
        course_name = course.name if course else "Unknown Course"
        by_course[course_name].append({
            "quiz_title":      quiz.title,
            "score":           round(attempt.score, 1),
            "correct":         attempt.correct_answers,
            "total":           attempt.total_questions,
            "wrong_questions": wrong_by_attempt.get(str(attempt.id), []),
        })

    return [
        {"course_name": name, "quizzes": quiz_list}
        for name, quiz_list in by_course.items()
    ]
