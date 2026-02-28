from pydantic import BaseModel
from uuid import UUID
from typing import Optional, List
from ..models.question import QuestionType


class QuestionCreate(BaseModel):
    question_text: str
    question_type: QuestionType = QuestionType.mcq
    options: Optional[List[str]] = None
    correct_answer: str
    explanation: Optional[str] = None
    order_index: int = 0


class QuestionUpdate(BaseModel):
    question_text: Optional[str] = None
    question_type: Optional[QuestionType] = None
    options: Optional[List[str]] = None
    correct_answer: Optional[str] = None
    explanation: Optional[str] = None
    order_index: Optional[int] = None


class QuestionResponse(BaseModel):
    id: UUID
    quiz_id: UUID
    question_text: str
    question_type: QuestionType
    options: Optional[List[str]]
    correct_answer: str
    explanation: Optional[str]
    order_index: int

    class Config:
        from_attributes = True
