import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from ..database import Base


class FeatureUsage(Base):
    __tablename__ = "feature_usage"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id      = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    feature_name = Column(String(64), nullable=False)
    usage_count  = Column(Integer, nullable=False, default=0)
    period_start = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_used    = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        __import__("sqlalchemy").UniqueConstraint("user_id", "feature_name", name="uq_feature_usage_user_feature"),
    )
