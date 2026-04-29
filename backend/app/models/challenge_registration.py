import uuid
from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from ..database import Base


class ChallengeRegistration(Base):
    __tablename__ = "challenge_registrations"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    full_name  = Column(String(200), nullable=False)
    email      = Column(String(254), nullable=False)
    university = Column(String(300), nullable=False)
    rating     = Column(Integer,     nullable=False)  # 1–10
    status     = Column(String(20),  nullable=False, default="pending")
    created_at = Column(DateTime,    nullable=False, default=datetime.utcnow)
