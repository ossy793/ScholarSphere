from pydantic import BaseModel
from uuid import UUID
from datetime import datetime
from typing import Optional
from ..models.quiz import SourceType


class QuizCreate(BaseModel):
    course_id: UUID
    title: str
    description: Optional[str] = None
    source_type: SourceType = SourceType.manual


class QuizUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    course_id: Optional[UUID] = None


class QuizResponse(BaseModel):
    id: UUID
    course_id: UUID
    user_id: UUID
    title: str
    description: Optional[str]
    source_type: SourceType
    share_token: Optional[str]
    created_at: datetime
    question_count: Optional[int] = 0

    class Config:
        from_attributes = True


class ShareTokenResponse(BaseModel):
    share_token: str
    share_url: str
