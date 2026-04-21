"""
Challenge models — real-time quiz competitions.
"""
import random
import string
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from ..database import Base


def _gen_code() -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=6))


class Challenge(Base):
    __tablename__ = "challenges"

    id                   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    host_id              = Column(UUID(as_uuid=True), ForeignKey("users.id",   ondelete="CASCADE"), nullable=False)
    quiz_id              = Column(UUID(as_uuid=True), ForeignKey("quizzes.id", ondelete="CASCADE"), nullable=False)
    code                 = Column(String(10),  unique=True, nullable=False, default=_gen_code)
    duration_seconds     = Column(Integer,  default=30)          # per-question seconds
    timer_mode           = Column(String(20), default="per_question")  # per_question | full_quiz
    max_participants     = Column(Integer,  nullable=True)
    status               = Column(String(20), default="waiting") # waiting | active | completed
    current_question_idx = Column(Integer,  default=0)
    started_at           = Column(DateTime, nullable=True)
    ended_at             = Column(DateTime, nullable=True)
    created_at           = Column(DateTime, default=datetime.utcnow)

    host         = relationship("User",                foreign_keys=[host_id])
    quiz         = relationship("Quiz",                foreign_keys=[quiz_id])
    participants = relationship("ChallengeParticipant", back_populates="challenge",
                                cascade="all, delete-orphan")


class ChallengeParticipant(Base):
    __tablename__ = "challenge_participants"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    challenge_id = Column(UUID(as_uuid=True), ForeignKey("challenges.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    user_id      = Column(UUID(as_uuid=True), ForeignKey("users.id",      ondelete="CASCADE"),
                          nullable=False)
    score        = Column(Integer,  default=0)
    finished     = Column(Boolean,  default=False)
    joined_at    = Column(DateTime, default=datetime.utcnow)

    challenge = relationship("Challenge",          back_populates="participants")
    user      = relationship("User",               foreign_keys=[user_id])
    responses = relationship("ChallengeResponse",  back_populates="participant",
                             cascade="all, delete-orphan")


class ChallengeResponse(Base):
    __tablename__ = "challenge_responses"

    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    participant_id   = Column(UUID(as_uuid=True), ForeignKey("challenge_participants.id", ondelete="CASCADE"),
                              nullable=False, index=True)
    challenge_id     = Column(UUID(as_uuid=True), ForeignKey("challenges.id", ondelete="CASCADE"),
                              nullable=False, index=True)
    question_id      = Column(UUID(as_uuid=True), ForeignKey("questions.id",  ondelete="SET NULL"),
                              nullable=True)
    question_idx     = Column(Integer,  nullable=False)
    answer           = Column(Text,     nullable=False)
    is_correct       = Column(Boolean,  default=False)
    points_earned    = Column(Integer,  default=0)
    response_time_ms = Column(Float,    default=0.0)
    answered_at      = Column(DateTime, default=datetime.utcnow)

    participant = relationship("ChallengeParticipant", back_populates="responses")
