"""Phase 4: rep applications and payout bank accounts

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-17
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
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
        "rep_applications",
        _id(),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("class_id", UUID, nullable=False),
        sa.Column("proof_file", sa.String(300), nullable=False),
        sa.Column("proof_content_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("reviewed_by", UUID, nullable=True),
        sa.Column("reviewed_at", TS, nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_rep_applications"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_rep_applications_user_id_users", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["class_id"], ["classes.id"],
            name="fk_rep_applications_class_id_classes", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"], ["admin_users.id"],
            name="fk_rep_applications_reviewed_by_admin_users", ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')", name="ck_rep_applications_status"
        ),
    )
    op.create_index("ix_rep_applications_user_id", "rep_applications", ["user_id"])
    op.create_index(
        "ix_rep_applications_status_created_at", "rep_applications", ["status", "created_at"]
    )
    op.create_index(
        "uq_rep_applications_one_pending_per_user", "rep_applications", ["user_id"],
        unique=True, postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "rep_bank_accounts",
        _id(),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("bank_code", sa.String(20), nullable=False),
        sa.Column("bank_name", sa.String(100), nullable=False),
        sa.Column("account_number", sa.String(255), nullable=False),
        sa.Column("account_name", sa.String(200), nullable=False),
        sa.Column("paystack_recipient_code", sa.String(50), nullable=True),
        sa.Column("verified_by", UUID, nullable=True),
        sa.Column("verified_at", TS, nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_rep_bank_accounts"),
        sa.UniqueConstraint("user_id", name="uq_rep_bank_accounts_user_id"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_rep_bank_accounts_user_id_users", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["verified_by"], ["admin_users.id"],
            name="fk_rep_bank_accounts_verified_by_admin_users", ondelete="RESTRICT",
        ),
    )


def downgrade() -> None:
    op.drop_table("rep_bank_accounts")
    op.drop_table("rep_applications")
