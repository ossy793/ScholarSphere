from pydantic import BaseModel
from uuid import UUID
from datetime import datetime
from typing import Optional, List
from ..models.attempt import AttemptMode


class AnswerSubmit(BaseModel):
    question_id: UUID
    user_answer: Optional[str] = None


class AttemptCreate(BaseModel):
    quiz_id: UUID
    mode: AttemptMode = AttemptMode.exam


class AttemptSubmit(BaseModel):
    answers: List[AnswerSubmit]
    time_taken_seconds: Optional[int] = None


class AttemptAnswerResponse(BaseModel):
    question_id: UUID
    user_answer: Optional[str]
    is_correct: bool

    class Config:
        from_attributes = True


class AttemptResponse(BaseModel):
    id: UUID
    quiz_id: UUID
    user_id: UUID
    mode: AttemptMode
    score: Optional[float]
    total_questions: int
    correct_answers: int
    time_taken_seconds: Optional[int]
    started_at: datetime
    completed_at: Optional[datetime]
    answers: Optional[List[AttemptAnswerResponse]] = None

    class Config:
        from_attributes = True
