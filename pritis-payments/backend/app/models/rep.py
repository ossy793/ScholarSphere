import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin


class RepApplication(IdMixin, TimestampMixin, Base):
    """A request to become the verified rep of a class. One pending at a time per user."""

    __tablename__ = "rep_applications"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="status"),
        Index("ix_rep_applications_status_created_at", "status", "created_at"),
        Index(
            "uq_rep_applications_one_pending_per_user", "user_id",
            unique=True, postgresql_where=text("status = 'pending'"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classes.id", ondelete="RESTRICT")
    )
    # Storage key of the uploaded school ID / student email proof.
    proof_file: Mapped[str] = mapped_column(String(300))
    proof_content_type: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), server_default="pending")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[str | None] = mapped_column(Text)


class RepBankAccount(IdMixin, TimestampMixin, Base):
    """Where a rep's payouts go. One per rep; payouts use it only once staff verified it."""

    __tablename__ = "rep_bank_accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), unique=True
    )
    bank_code: Mapped[str] = mapped_column(String(20))
    bank_name: Mapped[str] = mapped_column(String(100))
    # Encrypted with DATA_ENCRYPTION_KEY; screens show only the last four digits.
    account_number: Mapped[str] = mapped_column(String(255))
    # As Paystack resolved it, never as typed by the user.
    account_name: Mapped[str] = mapped_column(String(200))
    paystack_recipient_code: Mapped[str | None] = mapped_column(String(50))
    verified_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
