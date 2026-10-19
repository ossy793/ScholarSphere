import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin


class RevenueWithdrawal(IdMixin, TimestampMixin, Base):
    """Pritis taking its earnings out of the Paystack balance to its own bank account
    (owner's decision, 2026-10-19: funds stay in the Paystack balance).

    processing (Paystack transfer started) -> paid | failed
    Only revenue can leave this way: never money that belongs to reps.
    """

    __tablename__ = "revenue_withdrawals"
    __table_args__ = (
        CheckConstraint("status IN ('processing', 'paid', 'failed')", name="status"),
        CheckConstraint("amount_kobo > 0 AND transfer_fee_kobo >= 0", name="amounts"),
        Index("ix_revenue_withdrawals_status", "status"),
    )

    amount_kobo: Mapped[int] = mapped_column(BigInteger)  # what reaches Pritis's bank
    transfer_fee_kobo: Mapped[int] = mapped_column(BigInteger)  # Paystack's fee, as estimated
    transfer_fee_actual_kobo: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), server_default="processing")
    reference: Mapped[str] = mapped_column(String(64), unique=True)
    recipient_code: Mapped[str] = mapped_column(String(50))
    bank_label: Mapped[str] = mapped_column(String(300))
    requested_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT")
    )
    note: Mapped[str | None] = mapped_column(Text)
    paystack_transfer_code: Mapped[str | None] = mapped_column(String(64))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
