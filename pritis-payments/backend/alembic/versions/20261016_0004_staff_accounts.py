"""Phase 3 staff accounts: name, 2FA replay guard, lockout, invitations

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-16
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column(
        "admin_users", sa.Column("full_name", sa.String(200), server_default="", nullable=False)
    )
    op.alter_column(
        "admin_users", "totp_secret",
        existing_type=sa.String(64), type_=sa.String(255), existing_nullable=True,
    )
    op.add_column("admin_users", sa.Column("totp_last_step", sa.BigInteger(), nullable=True))
    op.add_column(
        "admin_users",
        sa.Column("failed_login_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("admin_users", sa.Column("locked_until", TS, nullable=True))

    op.create_table(
        "admin_invites",
        sa.Column("id", UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("admin_user_id", UUID, nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("used_at", TS, nullable=True),
        sa.Column("created_by", UUID, nullable=True),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_admin_invites"),
        sa.UniqueConstraint("token_hash", name="uq_admin_invites_token_hash"),
        sa.ForeignKeyConstraint(
            ["admin_user_id"], ["admin_users.id"],
            name="fk_admin_invites_admin_user_id_admin_users", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["admin_users.id"],
            name="fk_admin_invites_created_by_admin_users", ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_admin_invites_admin_user_id", "admin_invites", ["admin_user_id"])


def downgrade() -> None:
    op.drop_table("admin_invites")
    op.drop_column("admin_users", "locked_until")
    op.drop_column("admin_users", "failed_login_count")
    op.drop_column("admin_users", "totp_last_step")
    op.alter_column(
        "admin_users", "totp_secret",
        existing_type=sa.String(255), type_=sa.String(64), existing_nullable=True,
    )
    op.drop_column("admin_users", "full_name")
