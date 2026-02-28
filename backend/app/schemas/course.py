from pydantic import BaseModel
from uuid import UUID
from datetime import datetime
from typing import Optional


class CourseCreate(BaseModel):
    name: str
    description: Optional[str] = None


class CourseUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class CourseResponse(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    description: Optional[str]
    created_at: datetime
    quiz_count: Optional[int] = 0

    class Config:
        from_attributes = True
