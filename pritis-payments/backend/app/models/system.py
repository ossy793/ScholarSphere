import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin


class AppSetting(IdMixin, TimestampMixin, Base):
    """Fee rules and limits, one row per key."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), unique=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT")
    )


class RateLimit(IdMixin, TimestampMixin, Base):
    """Fixed-window request counters, shared by every API worker."""

    __tablename__ = "rate_limits"

    key: Mapped[str] = mapped_column(String(200), unique=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    count: Mapped[int] = mapped_column(Integer)


class EmailOutbox(IdMixin, TimestampMixin, Base):
    """Emails waiting for the worker to send them."""

    __tablename__ = "email_outbox"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'sending', 'sent', 'failed')", name="status"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        Index("ix_email_outbox_status_send_after", "status", "send_after"),
    )

    to_email: Mapped[str] = mapped_column(String(320))
    template: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    status: Mapped[str] = mapped_column(String(20), server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    send_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
