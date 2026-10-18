import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin


class Refund(IdMixin, TimestampMixin, Base):
    """Money going back to a student through Paystack's Refund API (design section 5).

    requested -> rejected
              -> processing (approved; the rep's share is reserved; Paystack asked)
                     -> processed (ledger reversed, payment Refunded) | failed (reservation released)
    """

    __tablename__ = "refunds"
    __table_args__ = (
        CheckConstraint(
            "status IN ('requested', 'rejected', 'processing', 'processed', 'failed')", name="status"
        ),
        CheckConstraint("mistake_by IS NULL OR mistake_by IN ('pritis', 'rep', 'student')", name="mistake_by"),
        CheckConstraint("reserved_from IS NULL OR reserved_from IN ('pending', 'available')", name="reserved_from"),
        CheckConstraint("amount_kobo > 0 AND fee_borne_kobo >= 0 AND reserved_kobo >= 0", name="amounts"),
        Index("ix_refunds_status", "status"),
        # One open refund (requested or processing) per payment.
        Index(
            "uq_refunds_one_open_per_payment", "payment_id",
            unique=True, postgresql_where=text("status IN ('requested', 'processing')"),
        ),
    )

    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id", ondelete="RESTRICT"), index=True
    )
    rep_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    # What goes back to the student: the full payment, or less Paystack's fee when the
    # student made the mistake. Set again at approval.
    amount_kobo: Mapped[int] = mapped_column(BigInteger)
    # Paystack's fee that Paystack keeps and Pritis absorbs (0 when the student bears it).
    fee_borne_kobo: Mapped[int] = mapped_column(BigInteger, server_default="0")
    reason: Mapped[str] = mapped_column(Text)
    # Whose mistake it was, recorded by the approver; decides who bears Paystack's fee.
    mistake_by: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), server_default="requested")
    requested_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT")
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[str | None] = mapped_column(Text)
    # The rep's share held back at approval, and which balance it came from.
    reserved_kobo: Mapped[int] = mapped_column(BigInteger, server_default="0")
    reserved_from: Mapped[str | None] = mapped_column(String(10))
    # Our own unique reference (sent as the merchant note), so one refund is never sent twice.
    reference: Mapped[str] = mapped_column(String(64), unique=True)
    paystack_refund_id: Mapped[str | None] = mapped_column(String(40))
    paystack_status: Mapped[str | None] = mapped_column(String(30))  # pending, processing... as Paystack says
    failure_reason: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentReminder(IdMixin, TimestampMixin, Base):
    """One row per reminder sent, so the daily job never sends the same one twice."""

    __tablename__ = "payment_reminders"
    __table_args__ = (
        CheckConstraint("kind IN ('three_days', 'deadline_day')", name="kind"),
        UniqueConstraint("user_id", "due_id", "kind", name="uq_payment_reminders_user_id_due_id_kind"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    due_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("class_dues.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
