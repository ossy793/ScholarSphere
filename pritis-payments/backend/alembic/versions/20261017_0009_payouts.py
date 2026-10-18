"""Phase 8: payout requests and bank account changes

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-17
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def _common() -> list[sa.Column]:
    return [
        sa.Column("id", UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "payout_requests",
        *_common(),
        sa.Column("rep_user_id", UUID, nullable=False),
        sa.Column("amount_kobo", sa.BigInteger(), nullable=False),
        sa.Column("platform_fee_kobo", sa.BigInteger(), nullable=False),
        sa.Column("transfer_fee_kobo", sa.BigInteger(), nullable=False),
        sa.Column("net_kobo", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(20), server_default="requested", nullable=False),
        sa.Column("reviewed_by", UUID, nullable=True),
        sa.Column("reviewed_at", TS, nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("transfer_reference", sa.String(64), nullable=False),
        sa.Column("paystack_transfer_code", sa.String(64), nullable=True),
        sa.Column("recipient_code", sa.String(50), nullable=True),
        sa.Column("bank_label", sa.String(300), nullable=True),
        sa.Column("transfer_fee_actual_kobo", sa.BigInteger(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("last_checked_at", TS, nullable=True),
        sa.Column("paid_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_payout_requests"),
        sa.UniqueConstraint("transfer_reference", name="uq_payout_requests_transfer_reference"),
        sa.ForeignKeyConstraint(
            ["rep_user_id"], ["users.id"], name="fk_payout_requests_rep_user_id_users", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"], ["admin_users.id"], name="fk_payout_requests_reviewed_by_admin_users", ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "status IN ('requested', 'rejected', 'processing', 'paid', 'failed', 'reversed')",
            name="ck_payout_requests_status",
        ),
        sa.CheckConstraint("amount_kobo > 0 AND net_kobo > 0", name="ck_payout_requests_amounts_positive"),
        sa.CheckConstraint(
            "platform_fee_kobo >= 0 AND transfer_fee_kobo >= 0", name="ck_payout_requests_fees_non_negative"
        ),
        sa.CheckConstraint(
            "net_kobo = amount_kobo - platform_fee_kobo - transfer_fee_kobo",
            name="ck_payout_requests_net_is_amount_less_fees",
        ),
    )
    op.create_index("ix_payout_requests_status", "payout_requests", ["status"])
    op.create_index(
        "uq_payout_requests_one_open_per_rep", "payout_requests", ["rep_user_id"],
        unique=True, postgresql_where=sa.text("status IN ('requested', 'processing')"),
    )

    op.create_table(
        "bank_account_changes",
        *_common(),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("bank_code", sa.String(20), nullable=False),
        sa.Column("bank_name", sa.String(100), nullable=False),
        sa.Column("account_number", sa.String(255), nullable=False),
        sa.Column("account_name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("reviewed_by", UUID, nullable=True),
        sa.Column("reviewed_at", TS, nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_bank_account_changes"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_bank_account_changes_user_id_users", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"], ["admin_users.id"], name="fk_bank_account_changes_reviewed_by_admin_users",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')", name="ck_bank_account_changes_status"
        ),
    )
    op.create_index("ix_bank_account_changes_user_id", "bank_account_changes", ["user_id"])
    op.create_index(
        "uq_bank_account_changes_one_pending_per_user", "bank_account_changes", ["user_id"],
        unique=True, postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_table("bank_account_changes")
    op.drop_table("payout_requests")
