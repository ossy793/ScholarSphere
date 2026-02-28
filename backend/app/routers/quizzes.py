from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID

from ..database import get_db
from ..models.user import User
from ..schemas.quiz import QuizCreate, QuizUpdate, QuizResponse, ShareTokenResponse
from ..services import quiz_service
from ..utils.security import get_current_user

router = APIRouter(prefix="/quizzes", tags=["quizzes"])


@router.get("", response_model=List[QuizResponse])
def list_quizzes(
    course_id: Optional[UUID] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return quiz_service.list_quizzes(current_user.id, db, course_id=course_id)


@router.post("", response_model=QuizResponse, status_code=status.HTTP_201_CREATED)
def create_quiz(
    payload: QuizCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return quiz_service.create_quiz(payload, current_user.id, db)


@router.get("/shared/{share_token}")
def get_shared_quiz(share_token: str, db: Session = Depends(get_db)):
    return quiz_service.get_shared_quiz(share_token, db)


@router.get("/{quiz_id}", response_model=QuizResponse)
def get_quiz(
    quiz_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return quiz_service.get_quiz(quiz_id, current_user.id, db)


@router.put("/{quiz_id}", response_model=QuizResponse)
def update_quiz(
    quiz_id: UUID,
    payload: QuizUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return quiz_service.update_quiz(quiz_id, payload, current_user.id, db)


@router.delete("/{quiz_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_quiz(
    quiz_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    quiz_service.delete_quiz(quiz_id, current_user.id, db)


@router.post("/{quiz_id}/share", response_model=ShareTokenResponse)
def generate_share_token(
    quiz_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return quiz_service.generate_share_token(quiz_id, current_user.id, db)
