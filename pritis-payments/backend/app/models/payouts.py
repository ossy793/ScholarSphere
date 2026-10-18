import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin


class PayoutRequest(IdMixin, TimestampMixin, Base):
    """A rep's request to be paid their Available balance (design section 5).

    requested -> rejected
              -> processing (approved; Paystack transfer started) -> paid | failed | reversed
    """

    __tablename__ = "payout_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('requested', 'rejected', 'processing', 'paid', 'failed', 'reversed')", name="status"
        ),
        CheckConstraint("amount_kobo > 0 AND net_kobo > 0", name="amounts_positive"),
        CheckConstraint("platform_fee_kobo >= 0 AND transfer_fee_kobo >= 0", name="fees_non_negative"),
        CheckConstraint("net_kobo = amount_kobo - platform_fee_kobo - transfer_fee_kobo", name="net_is_amount_less_fees"),
        Index("ix_payout_requests_status", "status"),
        # One open request (requested or processing) per rep.
        Index(
            "uq_payout_requests_one_open_per_rep", "rep_user_id",
            unique=True, postgresql_where=text("status IN ('requested', 'processing')"),
        ),
    )

    rep_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    amount_kobo: Mapped[int] = mapped_column(BigInteger)  # taken from Available
    platform_fee_kobo: Mapped[int] = mapped_column(BigInteger)
    transfer_fee_kobo: Mapped[int] = mapped_column(BigInteger)  # Paystack's fee, as estimated
    net_kobo: Mapped[int] = mapped_column(BigInteger)  # what reaches the rep's bank
    status: Mapped[str] = mapped_column(String(20), server_default="requested")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[str | None] = mapped_column(Text)
    transfer_reference: Mapped[str] = mapped_column(String(64), unique=True)
    paystack_transfer_code: Mapped[str | None] = mapped_column(String(64))
    # The verified payout destination used, kept as it was at the time.
    recipient_code: Mapped[str | None] = mapped_column(String(50))
    bank_label: Mapped[str | None] = mapped_column(String(300))
    transfer_fee_actual_kobo: Mapped[int | None] = mapped_column(BigInteger)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BankAccountChange(IdMixin, TimestampMixin, Base):
    """A verified rep asking to send payouts to a different account. Payouts pause until
    staff approve it (owner's decision, 2026-10-17)."""

    __tablename__ = "bank_account_changes"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="status"),
        Index(
            "uq_bank_account_changes_one_pending_per_user", "user_id",
            unique=True, postgresql_where=text("status = 'pending'"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    bank_code: Mapped[str] = mapped_column(String(20))
    bank_name: Mapped[str] = mapped_column(String(100))
    account_number: Mapped[str] = mapped_column(String(255))  # encrypted
    account_name: Mapped[str] = mapped_column(String(200))  # as Paystack resolved it
    status: Mapped[str] = mapped_column(String(20), server_default="pending")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[str | None] = mapped_column(Text)
