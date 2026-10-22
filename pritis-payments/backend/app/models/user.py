import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    true,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin


LEVELS = ("100", "200", "300", "400", "500")


class University(IdMixin, TimestampMixin, Base):
    """Nigerian universities offered in the signup dropdown. Seeded by migration 0003."""

    __tablename__ = "universities"

    name: Mapped[str] = mapped_column(String(200), unique=True)
    short_name: Mapped[str | None] = mapped_column(String(30))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())


class User(IdMixin, TimestampMixin, Base):
    """A student or class rep on the user site (dpp.pritis.name.ng)."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="status"),
        CheckConstraint(f"level IN ({', '.join(repr(l) for l in LEVELS)})", name="level"),
    )

    email: Mapped[str] = mapped_column(String(320), unique=True)
    # Empty for Google-only accounts.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(32))
    matric_no: Mapped[str | None] = mapped_column(String(64))
    # Phone (stored as +234...), university and level are required at signup.
    # Google sign-ups fill them in on the "complete your profile" page.
    university_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universities.id", ondelete="RESTRICT")
    )
    level: Mapped[str | None] = mapped_column(String(3))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When they ticked "I agree" to the Terms, and which version (app/core/legal.py).
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terms_version: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), server_default="active")
    # Login lockout after repeated wrong passwords.
    failed_login_count: Mapped[int] = mapped_column(Integer, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserToken(IdMixin, TimestampMixin, Base):
    """One-time codes sent to a user (email verification; password reset later). Stored hashed."""

    __tablename__ = "user_tokens"
    __table_args__ = (
        CheckConstraint("purpose IN ('verify_email', 'reset_password', 'confirm_payout')", name="purpose"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    purpose: Mapped[str] = mapped_column(String(20))
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")


class Class(IdMixin, TimestampMixin, Base):
    """A class owned by exactly one rep."""

    __tablename__ = "classes"

    name: Mapped[str] = mapped_column(String(200))
    school: Mapped[str] = mapped_column(String(200))
    department: Mapped[str] = mapped_column(String(200))
    level: Mapped[str] = mapped_column(String(32))
    session: Mapped[str] = mapped_column(String(32))
    # Unique: one rep per class, one class per rep.
    rep_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), unique=True
    )
    invite_code: Mapped[str] = mapped_column(String(32), unique=True)


class ClassMembership(IdMixin, TimestampMixin, Base):
    __tablename__ = "class_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "class_id", name="uq_class_memberships_user_id_class_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classes.id", ondelete="RESTRICT"), index=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
