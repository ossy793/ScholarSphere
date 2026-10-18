import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin


class AdminUser(IdMixin, TimestampMixin, Base):
    """A staff account on the admin site (admin.dpp.pritis.name.ng)."""

    __tablename__ = "admin_users"
    __table_args__ = (
        CheckConstraint("role IN ('super_admin', 'accountant', 'support')", name="role"),
        CheckConstraint("status IN ('invited', 'active', 'disabled')", name="status"),
    )

    email: Mapped[str] = mapped_column(String(320), unique=True)
    full_name: Mapped[str] = mapped_column(String(200), server_default="")
    # Empty until the invited staff member sets a password.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    # Authenticator-app secret, encrypted with DATA_ENCRYPTION_KEY.
    totp_secret: Mapped[str | None] = mapped_column(String(255))
    # The last 30-second step a code was accepted for, so a code can't be used twice.
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), server_default="invited")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_login_count: Mapped[int] = mapped_column(Integer, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminInvite(IdMixin, TimestampMixin, Base):
    """A one-time link for a staff member to set a password and authenticator. Stored hashed."""

    __tablename__ = "admin_invites"

    admin_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Empty for the first super admin, who is created from the command line.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT")
    )


class AuthSession(IdMixin, TimestampMixin, Base):
    """A refresh token for either site; subject_type says which account table."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint("subject_type IN ('user', 'admin')", name="subject_type"),
        Index("ix_auth_sessions_subject", "subject_type", "subject_id"),
    )

    subject_type: Mapped[str] = mapped_column(String(10))
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(512))


class AuditLog(IdMixin, TimestampMixin, Base):
    """Who did what. Rows are only ever inserted."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint("actor_type IN ('user', 'admin', 'system')", name="actor_type"),
        Index("ix_audit_logs_entity", "entity", "entity_id"),
        Index("ix_audit_logs_created_at", "created_at"),
    )

    actor_type: Mapped[str] = mapped_column(String(10))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(100))
    entity: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(String(45))
