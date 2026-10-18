"""Phase 1 foundation tables

users, classes, class_memberships, admin_users, auth_sessions,
audit_logs, app_settings, email_outbox

Revision ID: 0001
Revises:
Create Date: 2026-10-16
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def _id() -> sa.Column:
    return sa.Column("id", UUID, server_default=sa.text("gen_random_uuid()"), nullable=False)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "users",
        _id(),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("google_sub", sa.String(255), nullable=True),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("matric_no", sa.String(64), nullable=True),
        sa.Column("email_verified_at", TS, nullable=True),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.UniqueConstraint("google_sub", name="uq_users_google_sub"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_users_status"),
    )

    op.create_table(
        "admin_users",
        _id(),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("totp_secret", sa.String(64), nullable=True),
        sa.Column("status", sa.String(20), server_default="invited", nullable=False),
        sa.Column("last_login_at", TS, nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_admin_users"),
        sa.UniqueConstraint("email", name="uq_admin_users_email"),
        sa.CheckConstraint(
            "role IN ('super_admin', 'accountant', 'support')", name="ck_admin_users_role"
        ),
        sa.CheckConstraint(
            "status IN ('invited', 'active', 'disabled')", name="ck_admin_users_status"
        ),
    )

    op.create_table(
        "classes",
        _id(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("school", sa.String(200), nullable=False),
        sa.Column("department", sa.String(200), nullable=False),
        sa.Column("level", sa.String(32), nullable=False),
        sa.Column("session", sa.String(32), nullable=False),
        sa.Column("rep_user_id", UUID, nullable=False),
        sa.Column("invite_code", sa.String(32), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_classes"),
        sa.ForeignKeyConstraint(
            ["rep_user_id"], ["users.id"],
            name="fk_classes_rep_user_id_users", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("rep_user_id", name="uq_classes_rep_user_id"),
        sa.UniqueConstraint("invite_code", name="uq_classes_invite_code"),
    )

    op.create_table(
        "class_memberships",
        _id(),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("class_id", UUID, nullable=False),
        sa.Column("joined_at", TS, server_default=sa.func.now(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_class_memberships"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_class_memberships_user_id_users", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["class_id"], ["classes.id"],
            name="fk_class_memberships_class_id_classes", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "user_id", "class_id", name="uq_class_memberships_user_id_class_id"
        ),
    )
    op.create_index("ix_class_memberships_class_id", "class_memberships", ["class_id"])

    op.create_table(
        "auth_sessions",
        _id(),
        sa.Column("subject_type", sa.String(10), nullable=False),
        sa.Column("subject_id", UUID, nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("revoked_at", TS, nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_auth_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
        sa.CheckConstraint(
            "subject_type IN ('user', 'admin')", name="ck_auth_sessions_subject_type"
        ),
    )
    op.create_index(
        "ix_auth_sessions_subject", "auth_sessions", ["subject_type", "subject_id"]
    )

    op.create_table(
        "audit_logs",
        _id(),
        sa.Column("actor_type", sa.String(10), nullable=False),
        sa.Column("actor_id", UUID, nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity", sa.String(50), nullable=False),
        sa.Column("entity_id", UUID, nullable=True),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
        sa.CheckConstraint(
            "actor_type IN ('user', 'admin', 'system')", name="ck_audit_logs_actor_type"
        ),
    )
    op.create_index("ix_audit_logs_entity", "audit_logs", ["entity", "entity_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    op.create_table(
        "app_settings",
        _id(),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("updated_by", UUID, nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_app_settings"),
        sa.UniqueConstraint("key", name="uq_app_settings_key"),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["admin_users.id"],
            name="fk_app_settings_updated_by_admin_users", ondelete="RESTRICT",
        ),
    )

    op.create_table(
        "email_outbox",
        _id(),
        sa.Column("to_email", sa.String(320), nullable=False),
        sa.Column("template", sa.String(100), nullable=False),
        sa.Column("payload", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("send_after", TS, server_default=sa.func.now(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_email_outbox"),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed')", name="ck_email_outbox_status"
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_email_outbox_attempts_non_negative"),
    )
    op.create_index(
        "ix_email_outbox_status_send_after", "email_outbox", ["status", "send_after"]
    )


def downgrade() -> None:
    op.drop_table("email_outbox")
    op.drop_table("app_settings")
    op.drop_table("audit_logs")
    op.drop_table("auth_sessions")
    op.drop_table("class_memberships")
    op.drop_table("classes")
    op.drop_table("admin_users")
    op.drop_table("users")
