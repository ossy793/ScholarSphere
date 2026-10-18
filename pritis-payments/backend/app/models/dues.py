import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin


class ClassDue(IdMixin, TimestampMixin, Base):
    """A due and its live terms.

    Once approved, the term columns change only when an admin approves a revision
    (live_revision_id moves with them). Before the first approval they hold the draft.
    """

    __tablename__ = "class_dues"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'pending', 'approved', 'rejected')", name="status"),
        CheckConstraint("amount_kobo > 0", name="amount_positive"),
        CheckConstraint(
            "platform_fee_percent >= 0 AND platform_fee_percent <= 20", name="fee_percent_range"
        ),
    )

    class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classes.id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, server_default="")
    amount_kobo: Mapped[int] = mapped_column(BigInteger)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    require_matric: Mapped[bool] = mapped_column(Boolean, server_default=false())
    platform_fee_percent: Mapped[Decimal] = mapped_column(Numeric(4, 2), server_default="3")
    # draft -> pending -> approved | rejected (first approval). Later edits are revisions.
    status: Mapped[str] = mapped_column(String(20), server_default="draft")
    # Set by the rep once approved ("enable" / "pause").
    is_live: Mapped[bool] = mapped_column(Boolean, server_default=false())
    share_slug: Mapped[str] = mapped_column(String(16), unique=True)
    live_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("due_revisions.id", ondelete="RESTRICT", use_alter=True),
    )


class DueRevision(IdMixin, TimestampMixin, Base):
    """Every version of a due's terms. At most one open (draft or pending) per due."""

    __tablename__ = "due_revisions"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'pending', 'approved', 'rejected')", name="status"),
        UniqueConstraint("due_id", "version", name="uq_due_revisions_due_id_version"),
        Index("ix_due_revisions_status_created_at", "status", "created_at"),
        Index(
            "uq_due_revisions_one_open_per_due", "due_id",
            unique=True, postgresql_where=text("status IN ('draft', 'pending')"),
        ),
    )

    due_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("class_dues.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    # {"title", "description", "amount_kobo", "deadline" (ISO, UTC), "require_matric"}
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), server_default="draft")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[str | None] = mapped_column(Text)
    # The platform fee the approver set for these terms.
    platform_fee_percent: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
