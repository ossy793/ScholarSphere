import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime
from sqlalchemy.dialects.postgresql import UUID
from ..database import Base


class LeaderboardReset(Base):
    """
    Stores the last reset timestamp per leaderboard metric.
    Only data created AFTER reset_at is included in that leaderboard.
    One row per metric (enforced by unique constraint on metric).

    Valid metrics:
      brainstorm_time      — total time spent in Brainstorm
      brainstorm_messages  — total AI messages sent
      quizzes_generated    — quizzes created
      avg_score            — average quiz score
    """
    __tablename__ = "leaderboard_resets"

    id       = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    metric   = Column(String(50), nullable=False, unique=True)
    reset_at = Column(DateTime, nullable=False, default=datetime.utcnow)
