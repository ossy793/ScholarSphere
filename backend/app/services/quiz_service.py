"""
Quiz Service
------------
Encapsulates all business logic for:
- Course CRUD
- Quiz CRUD + share-token generation
- Question CRUD
- Attempt creation & submission (scoring)

Routers remain thin: they only parse HTTP input, call a service function,
and return the result. All DB interactions and domain rules live here.
"""

import secrets
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.course import Course
from ..models.quiz import Quiz, SourceType
from ..models.question import Question, QuestionType
from ..models.attempt import Attempt, AttemptAnswer
from ..models.user import User
from ..schemas.course import CourseCreate, CourseUpdate, CourseResponse
from ..schemas.quiz import QuizCreate, QuizUpdate, QuizResponse, ShareTokenResponse
from ..schemas.question import QuestionCreate, QuestionUpdate, QuestionResponse
from ..schemas.attempt import AttemptCreate, AttemptSubmit, AttemptResponse


# ──────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────

def _quiz_to_response(quiz: Quiz, db: Session, question_count: int = None) -> QuizResponse:
    if question_count is None:
        question_count = db.query(func.count(Question.id)).filter(Question.quiz_id == quiz.id).scalar() or 0
    resp = QuizResponse.model_validate(quiz)
    resp.question_count = question_count
    return resp


def _course_to_response(course: Course, db: Session, quiz_count: int = None) -> CourseResponse:
    if quiz_count is None:
        quiz_count = db.query(func.count(Quiz.id)).filter(Quiz.course_id == course.id).scalar() or 0
    resp = CourseResponse.model_validate(course)
    resp.quiz_count = quiz_count
    return resp


def _assert_course_owner(course_id: UUID, user_id, db: Session) -> Course:
    course = db.query(Course).filter(
        Course.id == course_id, Course.user_id == user_id
    ).first()
    if not course:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    return course


def _assert_quiz_owner(quiz_id: UUID, user_id, db: Session) -> Quiz:
    quiz = db.query(Quiz).filter(
        Quiz.id == quiz_id, Quiz.user_id == user_id
    ).first()
    if not quiz:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quiz not found")
    return quiz


# ──────────────────────────────────────────────
#  Course operations
# ──────────────────────────────────────────────

def list_courses(user_id, db: Session) -> List[CourseResponse]:
    courses = (
        db.query(Course)
        .filter(Course.user_id == user_id)
        .order_by(Course.created_at.desc())
        .all()
    )
    if not courses:
        return []
    # Batch count quizzes per course in one query
    course_ids = [c.id for c in courses]
    counts = dict(
        db.query(Quiz.course_id, func.count(Quiz.id))
        .filter(Quiz.course_id.in_(course_ids))
        .group_by(Quiz.course_id)
        .all()
    )
    return [_course_to_response(c, db, quiz_count=counts.get(c.id, 0)) for c in courses]


def create_course(payload: CourseCreate, user_id, db: Session) -> CourseResponse:
    course = Course(
        user_id=user_id,
        name=payload.name,
        description=payload.description,
    )
    db.add(course)
    db.commit()
    db.refresh(course)
    return _course_to_response(course, db)


def get_course(course_id: UUID, user_id, db: Session) -> CourseResponse:
    course = _assert_course_owner(course_id, user_id, db)
    return _course_to_response(course, db)


def update_course(course_id: UUID, payload: CourseUpdate, user_id, db: Session) -> CourseResponse:
    course = _assert_course_owner(course_id, user_id, db)
    if payload.name is not None:
        course.name = payload.name
    if payload.description is not None:
        course.description = payload.description
    db.commit()
    db.refresh(course)
    return _course_to_response(course, db)


def delete_course(course_id: UUID, user_id, db: Session) -> None:
    course = _assert_course_owner(course_id, user_id, db)
    db.delete(course)
    db.commit()


# ──────────────────────────────────────────────
#  Quiz operations
# ──────────────────────────────────────────────

def list_quizzes(user_id, db: Session, course_id: Optional[UUID] = None) -> List[QuizResponse]:
    q = db.query(Quiz).filter(Quiz.user_id == user_id)
    if course_id:
        q = q.filter(Quiz.course_id == course_id)
    quizzes = q.order_by(Quiz.created_at.desc()).all()
    if not quizzes:
        return []
    # Batch count questions per quiz in one query
    quiz_ids = [quiz.id for quiz in quizzes]
    counts = dict(
        db.query(Question.quiz_id, func.count(Question.id))
        .filter(Question.quiz_id.in_(quiz_ids))
        .group_by(Question.quiz_id)
        .all()
    )
    return [_quiz_to_response(quiz, db, question_count=counts.get(quiz.id, 0)) for quiz in quizzes]


def create_quiz(payload: QuizCreate, user_id, db: Session) -> QuizResponse:
    _assert_course_owner(payload.course_id, user_id, db)
    quiz = Quiz(
        course_id=payload.course_id,
        user_id=user_id,
        title=payload.title,
        description=payload.description,
        source_type=payload.source_type,
    )
    db.add(quiz)
    db.commit()
    db.refresh(quiz)
    return _quiz_to_response(quiz, db)


def get_quiz(quiz_id: UUID, user_id, db: Session) -> QuizResponse:
    quiz = _assert_quiz_owner(quiz_id, user_id, db)
    return _quiz_to_response(quiz, db)


def get_shared_quiz(share_token: str, db: Session) -> dict:
    """Return a public-safe quiz payload for shared links (no correct answers exposed)."""
    quiz = db.query(Quiz).filter(Quiz.share_token == share_token).first()
    if not quiz:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared quiz not found")
    questions = (
        db.query(Question)
        .filter(Question.quiz_id == quiz.id)
        .order_by(Question.order_index)
        .all()
    )
    return {
        "id": str(quiz.id),
        "title": quiz.title,
        "description": quiz.description,
        "questions": [
            {
                "id": str(q.id),
                "question_text": q.question_text,
                "question_type": q.question_type,
                "options": q.options,
                "correct_answer": q.correct_answer,
                "explanation": q.explanation,
                "order_index": q.order_index,
            }
            for q in questions
        ],
    }


def update_quiz(quiz_id: UUID, payload: QuizUpdate, user_id, db: Session) -> QuizResponse:
    quiz = _assert_quiz_owner(quiz_id, user_id, db)
    if payload.title is not None:
        quiz.title = payload.title
    if payload.description is not None:
        quiz.description = payload.description
    if payload.course_id is not None:
        _assert_course_owner(payload.course_id, user_id, db)
        quiz.course_id = payload.course_id
    db.commit()
    db.refresh(quiz)
    return _quiz_to_response(quiz, db)


def delete_quiz(quiz_id: UUID, user_id, db: Session) -> None:
    quiz = _assert_quiz_owner(quiz_id, user_id, db)
    db.delete(quiz)
    db.commit()


def generate_share_token(quiz_id: UUID, user_id, db: Session) -> ShareTokenResponse:
    quiz = _assert_quiz_owner(quiz_id, user_id, db)
    if not quiz.share_token:
        quiz.share_token = secrets.token_urlsafe(32)
        db.commit()
        db.refresh(quiz)
    return ShareTokenResponse(
        share_token=quiz.share_token,
        share_url=f"/quiz-practice.html?share={quiz.share_token}",
    )


# ──────────────────────────────────────────────
#  Question operations
# ──────────────────────────────────────────────

def list_questions(quiz_id: UUID, user_id, db: Session) -> List[Question]:
    _assert_quiz_owner(quiz_id, user_id, db)
    return (
        db.query(Question)
        .filter(Question.quiz_id == quiz_id)
        .order_by(Question.order_index)
        .all()
    )


def add_question(quiz_id: UUID, payload: QuestionCreate, user_id, db: Session) -> Question:
    _assert_quiz_owner(quiz_id, user_id, db)
    question = Question(
        quiz_id=quiz_id,
        question_text=payload.question_text,
        question_type=payload.question_type,
        options=payload.options,
        correct_answer=payload.correct_answer,
        explanation=payload.explanation,
        order_index=payload.order_index,
    )
    db.add(question)
    db.commit()
    db.refresh(question)
    return question


def update_question(question_id: UUID, payload: QuestionUpdate, user_id, db: Session) -> Question:
    question = (
        db.query(Question)
        .join(Quiz, Quiz.id == Question.quiz_id)
        .filter(Question.id == question_id, Quiz.user_id == user_id)
        .first()
    )
    if not question:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")
    for field, val in payload.model_dump(exclude_unset=True).items():
        setattr(question, field, val)
    db.commit()
    db.refresh(question)
    return question


def delete_question(question_id: UUID, user_id, db: Session) -> None:
    question = (
        db.query(Question)
        .join(Quiz, Quiz.id == Question.quiz_id)
        .filter(Question.id == question_id, Quiz.user_id == user_id)
        .first()
    )
    if not question:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")
    db.delete(question)
    db.commit()


# ──────────────────────────────────────────────
#  Attempt operations
# ──────────────────────────────────────────────

def start_attempt(payload: AttemptCreate, user_id, db: Session) -> Attempt:
    """Create an attempt record when the user starts a quiz."""
    quiz = _assert_quiz_owner(payload.quiz_id, user_id, db)
    total = db.query(Question).filter(Question.quiz_id == quiz.id).count()
    attempt = Attempt(
        user_id=user_id,
        quiz_id=payload.quiz_id,
        mode=payload.mode,
        total_questions=total,
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt


def submit_attempt(attempt_id: UUID, payload: AttemptSubmit, user_id, db: Session) -> Attempt:
    """
    Grade a submitted attempt:
    - compare each answer against the stored correct_answer (case-insensitive)
    - persist AttemptAnswer rows
    - compute and store score as a percentage
    """
    attempt = db.query(Attempt).filter(
        Attempt.id == attempt_id, Attempt.user_id == user_id
    ).first()
    if not attempt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attempt not found")
    if attempt.completed_at:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Attempt already submitted")

    questions = {
        str(q.id): q
        for q in db.query(Question).filter(Question.quiz_id == attempt.quiz_id).all()
    }

    from .ai_service import grade_short_answers

    # Split answers into exact-match (MCQ/true_false) and semantic (short_answer)
    exact_answers = []
    semantic_answers = []  # list of (ans, q, batch_index)

    for ans in payload.answers:
        q = questions.get(str(ans.question_id))
        if not q:
            continue
        if q.question_type == "short_answer":
            semantic_answers.append((ans, q, len(semantic_answers)))
        else:
            exact_answers.append((ans, q))

    # Semantically grade all short_answer questions in one batch call
    semantic_results: list[bool] = []
    if semantic_answers:
        batch = [
            {
                "index": i,
                "question": q.question_text,
                "expected": q.correct_answer,
                "user_answer": ans.user_answer or "",
            }
            for i, (ans, q, _) in enumerate(semantic_answers)
        ]
        semantic_results = grade_short_answers(batch)

    correct_count = 0

    # Persist exact-match answers
    for ans, q in exact_answers:
        is_correct = (ans.user_answer or "").strip().lower() == q.correct_answer.strip().lower()
        if is_correct:
            correct_count += 1
        db.add(AttemptAnswer(
            attempt_id=attempt.id,
            question_id=ans.question_id,
            user_answer=ans.user_answer,
            is_correct=is_correct,
        ))

    # Persist semantically graded answers
    for idx, (ans, q, _) in enumerate(semantic_answers):
        is_correct = semantic_results[idx] if idx < len(semantic_results) else False
        if is_correct:
            correct_count += 1
        db.add(AttemptAnswer(
            attempt_id=attempt.id,
            question_id=ans.question_id,
            user_answer=ans.user_answer,
            is_correct=is_correct,
        ))

    total = len(questions)
    attempt.correct_answers = correct_count
    attempt.total_questions = total
    attempt.score = round((correct_count / total) * 100, 1) if total else 0.0
    attempt.time_taken_seconds = payload.time_taken_seconds
    attempt.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(attempt)

    # Eagerly trigger the answers relationship so the response serialiser can access them
    _ = attempt.answers
    return attempt


def list_attempts(user_id, db: Session, quiz_id: Optional[UUID] = None) -> List[Attempt]:
    q = db.query(Attempt).filter(Attempt.user_id == user_id)
    if quiz_id:
        q = q.filter(Attempt.quiz_id == quiz_id)
    return q.order_by(Attempt.started_at.desc()).all()


def get_attempt(attempt_id: UUID, user_id, db: Session) -> Attempt:
    attempt = db.query(Attempt).filter(
        Attempt.id == attempt_id, Attempt.user_id == user_id
    ).first()
    if not attempt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attempt not found")
    return attempt


# ──────────────────────────────────────────────
#  AI-generation helpers
# ──────────────────────────────────────────────

def save_generated_quiz(
    db: Session,
    user_id,
    course_id: UUID,
    title: str,
    source_type: SourceType,
    raw_questions: list,
) -> QuizResponse:
    """
    Persist an AI-generated quiz and its questions in a single transaction.
    raw_questions is the validated list returned by ai_service.generate_questions().
    """
    _assert_course_owner(course_id, user_id, db)

    quiz = Quiz(
        course_id=course_id,
        user_id=user_id,
        title=title,
        source_type=source_type,
    )
    db.add(quiz)
    db.flush()  # obtain quiz.id before committing

    for q in raw_questions:
        q_type_str = q.get("question_type", "mcq")
        try:
            q_type = QuestionType(q_type_str)
        except ValueError:
            q_type = QuestionType.mcq

        db.add(Question(
            quiz_id=quiz.id,
            question_text=q["question_text"],
            question_type=q_type,
            options=q.get("options"),
            correct_answer=q["correct_answer"],
            explanation=q.get("explanation"),
            order_index=q.get("order_index", 0),
        ))

    db.commit()
    db.refresh(quiz)
    return _quiz_to_response(quiz, db)
