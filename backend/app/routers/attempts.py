from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID

from ..database import get_db
from ..models.user import User
from ..schemas.attempt import AttemptCreate, AttemptSubmit, AttemptResponse
from ..services import quiz_service
from ..utils.security import get_current_user

router = APIRouter(prefix="/attempts", tags=["attempts"])


@router.post("", response_model=AttemptResponse, status_code=status.HTTP_201_CREATED)
def start_attempt(
    payload: AttemptCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return quiz_service.start_attempt(payload, current_user.id, db)


@router.put("/{attempt_id}/submit", response_model=AttemptResponse)
def submit_attempt(
    attempt_id: UUID,
    payload: AttemptSubmit,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return quiz_service.submit_attempt(attempt_id, payload, current_user.id, db)


@router.get("", response_model=List[AttemptResponse])
def list_attempts(
    quiz_id: Optional[UUID] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return quiz_service.list_attempts(current_user.id, db, quiz_id=quiz_id)


@router.get("/{attempt_id}", response_model=AttemptResponse)
def get_attempt(
    attempt_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return quiz_service.get_attempt(attempt_id, current_user.id, db)
