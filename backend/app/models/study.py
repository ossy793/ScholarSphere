import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Text, Boolean, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from ..database import Base


class Note(Base):
    __tablename__ = "notes"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    folder     = Column(String(100), nullable=True,  index=True)
    title      = Column(String(300), nullable=False, default="Untitled")
    content    = Column(Text,        nullable=True,  default="")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class StudySchedule(Base):
    __tablename__ = "study_schedules"

    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id          = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
                              nullable=False, index=True)
    course           = Column(String(255), nullable=False)
    description      = Column(Text,    nullable=True)
    scheduled_time   = Column(DateTime, nullable=False, index=True)
    duration_minutes = Column(Integer,  nullable=True)
    notified_30      = Column(Boolean, default=False, nullable=False)
    notified_15      = Column(Boolean, default=False, nullable=False)
    notified_5       = Column(Boolean, default=False, nullable=False)
    created_at       = Column(DateTime, default=datetime.utcnow, nullable=False)
