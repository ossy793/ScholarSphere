from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID

from ..database import get_db
from ..models.user import User
from ..schemas.question import QuestionCreate, QuestionUpdate, QuestionResponse
from ..services import quiz_service
from ..utils.security import get_current_user

router = APIRouter(tags=["questions"])


@router.get("/quizzes/{quiz_id}/questions", response_model=List[QuestionResponse])
def list_questions(
    quiz_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return quiz_service.list_questions(quiz_id, current_user.id, db)


@router.post("/quizzes/{quiz_id}/questions", response_model=QuestionResponse, status_code=status.HTTP_201_CREATED)
def add_question(
    quiz_id: UUID,
    payload: QuestionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return quiz_service.add_question(quiz_id, payload, current_user.id, db)


@router.put("/questions/{question_id}", response_model=QuestionResponse)
def update_question(
    question_id: UUID,
    payload: QuestionUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return quiz_service.update_question(question_id, payload, current_user.id, db)


@router.delete("/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_question(
    question_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    quiz_service.delete_question(question_id, current_user.id, db)
