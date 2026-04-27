import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID

from ..database import Base


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id     = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    email       = Column(String(255), nullable=False)
    full_name   = Column(String(255), nullable=True)
    amount_kobo = Column(Integer, nullable=False)
    plan        = Column(String(20), nullable=False)
    cycle       = Column(String(20), nullable=False)
    reference   = Column(String(100), nullable=False, unique=True, index=True)
    paid_at     = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at  = Column(DateTime, nullable=False, default=datetime.utcnow)
