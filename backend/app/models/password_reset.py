import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from ..database import Base


class PasswordResetCode(Base):
    __tablename__ = "password_reset_codes"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email      = Column(String(255), nullable=False, index=True)
    code       = Column(String(6), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used       = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
