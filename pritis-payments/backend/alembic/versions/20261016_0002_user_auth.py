"""Phase 2 user auth: login lockout columns, user_tokens, rate_limits

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-16
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
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
    op.add_column(
        "users", sa.Column("failed_login_count", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column("users", sa.Column("locked_until", TS, nullable=True))

    op.create_table(
        "user_tokens",
        _id(),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("used_at", TS, nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_user_tokens"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_user_tokens_user_id_users", ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "purpose IN ('verify_email', 'reset_password')", name="ck_user_tokens_purpose"
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_user_tokens_attempts_non_negative"),
    )
    op.create_index("ix_user_tokens_user_id", "user_tokens", ["user_id"])

    op.create_table(
        "rate_limits",
        _id(),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("window_start", TS, nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_rate_limits"),
        sa.UniqueConstraint("key", name="uq_rate_limits_key"),
    )


def downgrade() -> None:
    op.drop_table("rate_limits")
    op.drop_table("user_tokens")
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_count")
