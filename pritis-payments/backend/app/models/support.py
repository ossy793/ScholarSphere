import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin


class SupportTicket(IdMixin, TimestampMixin, Base):
    """A student or rep contacting support (owner's request, 2026-10-19). Staff answer on
    the admin site; each answer is emailed to the address the user had when they wrote.

    open (waiting for staff) -> answered (staff replied) -> closed; a new message reopens it.
    """

    __tablename__ = "support_tickets"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'answered', 'closed')", name="status"),
        Index("ix_support_tickets_status_updated_at", "status", "updated_at"),
    )

    ticket_no: Mapped[int] = mapped_column(
        BigInteger, server_default=text("nextval('support_ticket_no_seq')"), unique=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    email: Mapped[str] = mapped_column(String(320))  # where replies go
    subject: Mapped[str] = mapped_column(String(150))
    status: Mapped[str] = mapped_column(String(20), server_default="open")
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SupportMessage(IdMixin, TimestampMixin, Base):
    """One message in a ticket, from the user or from a staff member."""

    __tablename__ = "support_messages"
    __table_args__ = (
        CheckConstraint("sender IN ('user', 'staff')", name="sender"),
        CheckConstraint("(sender = 'staff') = (admin_id IS NOT NULL)", name="staff_has_admin"),
    )

    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("support_tickets.id", ondelete="CASCADE"), index=True
    )
    sender: Mapped[str] = mapped_column(String(10))
    admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT")
    )
    body: Mapped[str] = mapped_column(Text)


class Announcement(IdMixin, TimestampMixin, Base):
    """A message from staff emailed to many users at once: everyone, students, verified
    class reps, or people picked by hand. Kept as a record of what was sent to whom."""

    __tablename__ = "announcements"
    __table_args__ = (
        CheckConstraint("audience IN ('all', 'students', 'reps', 'selected')", name="audience"),
    )

    subject: Mapped[str] = mapped_column(String(150))
    body: Mapped[str] = mapped_column(Text)
    audience: Mapped[str] = mapped_column(String(20))
    sent_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT"))
    recipient_count: Mapped[int] = mapped_column(Integer)
    # For audience 'selected': who was picked (user ids), as chosen at the time.
    selected_user_ids: Mapped[list[Any] | None] = mapped_column(JSONB)
