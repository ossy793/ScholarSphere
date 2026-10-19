"""Revenue withdrawals from the Paystack balance

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "revenue_withdrawals",
        sa.Column("id", UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("amount_kobo", sa.BigInteger(), nullable=False),
        sa.Column("transfer_fee_kobo", sa.BigInteger(), nullable=False),
        sa.Column("transfer_fee_actual_kobo", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(20), server_default="processing", nullable=False),
        sa.Column("reference", sa.String(64), nullable=False),
        sa.Column("recipient_code", sa.String(50), nullable=False),
        sa.Column("bank_label", sa.String(300), nullable=False),
        sa.Column("requested_by", UUID, nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("paystack_transfer_code", sa.String(64), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("last_checked_at", TS, nullable=True),
        sa.Column("paid_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_revenue_withdrawals"),
        sa.UniqueConstraint("reference", name="uq_revenue_withdrawals_reference"),
        sa.ForeignKeyConstraint(["requested_by"], ["admin_users.id"],
                                name="fk_revenue_withdrawals_requested_by_admin_users", ondelete="RESTRICT"),
        sa.CheckConstraint("status IN ('processing', 'paid', 'failed')", name="ck_revenue_withdrawals_status"),
        sa.CheckConstraint("amount_kobo > 0 AND transfer_fee_kobo >= 0", name="ck_revenue_withdrawals_amounts"),
    )
    op.create_index("ix_revenue_withdrawals_status", "revenue_withdrawals", ["status"])


def downgrade() -> None:
    op.drop_table("revenue_withdrawals")
