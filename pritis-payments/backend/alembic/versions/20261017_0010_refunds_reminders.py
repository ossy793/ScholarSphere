"""Phase 10: refunds, payment reminders

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-17
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
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
    op.drop_constraint("ck_payments_status", "payments", type_="check")
    op.create_check_constraint(
        "ck_payments_status", "payments", "status IN ('pending', 'success', 'failed', 'abandoned', 'refunded')"
    )
    op.add_column("payments", sa.Column("refunded_at", TS, nullable=True))
    op.add_column(
        "rep_wallets", sa.Column("refund_held_kobo", sa.BigInteger(), server_default="0", nullable=False)
    )

    op.create_table(
        "refunds",
        *_common(),
        sa.Column("payment_id", UUID, nullable=False),
        sa.Column("rep_user_id", UUID, nullable=False),
        sa.Column("amount_kobo", sa.BigInteger(), nullable=False),
        sa.Column("fee_borne_kobo", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("mistake_by", sa.String(10), nullable=True),
        sa.Column("status", sa.String(20), server_default="requested", nullable=False),
        sa.Column("requested_by", UUID, nullable=False),
        sa.Column("reviewed_by", UUID, nullable=True),
        sa.Column("reviewed_at", TS, nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("reserved_kobo", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("reserved_from", sa.String(10), nullable=True),
        sa.Column("reference", sa.String(64), nullable=False),
        sa.Column("paystack_refund_id", sa.String(40), nullable=True),
        sa.Column("paystack_status", sa.String(30), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("last_checked_at", TS, nullable=True),
        sa.Column("processed_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_refunds"),
        sa.UniqueConstraint("reference", name="uq_refunds_reference"),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], name="fk_refunds_payment_id_payments",
                                ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["rep_user_id"], ["users.id"], name="fk_refunds_rep_user_id_users",
                                ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by"], ["admin_users.id"], name="fk_refunds_requested_by_admin_users",
                                ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["admin_users.id"], name="fk_refunds_reviewed_by_admin_users",
                                ondelete="RESTRICT"),
        sa.CheckConstraint(
            "status IN ('requested', 'rejected', 'processing', 'processed', 'failed')", name="ck_refunds_status"
        ),
        sa.CheckConstraint(
            "mistake_by IS NULL OR mistake_by IN ('pritis', 'rep', 'student')", name="ck_refunds_mistake_by"
        ),
        sa.CheckConstraint(
            "reserved_from IS NULL OR reserved_from IN ('pending', 'available')", name="ck_refunds_reserved_from"
        ),
        sa.CheckConstraint(
            "amount_kobo > 0 AND fee_borne_kobo >= 0 AND reserved_kobo >= 0", name="ck_refunds_amounts"
        ),
    )
    op.create_index("ix_refunds_payment_id", "refunds", ["payment_id"])
    op.create_index("ix_refunds_status", "refunds", ["status"])
    op.create_index(
        "uq_refunds_one_open_per_payment", "refunds", ["payment_id"],
        unique=True, postgresql_where=sa.text("status IN ('requested', 'processing')"),
    )

    op.create_table(
        "payment_reminders",
        *_common(),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("due_id", UUID, nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_payment_reminders"),
        sa.UniqueConstraint("user_id", "due_id", "kind", name="uq_payment_reminders_user_id_due_id_kind"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_payment_reminders_user_id_users",
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["due_id"], ["class_dues.id"], name="fk_payment_reminders_due_id_class_dues",
                                ondelete="CASCADE"),
        sa.CheckConstraint("kind IN ('three_days', 'deadline_day')", name="ck_payment_reminders_kind"),
    )
    op.create_index("ix_payment_reminders_due_id", "payment_reminders", ["due_id"])


def downgrade() -> None:
    op.drop_table("payment_reminders")
    op.drop_table("refunds")
    op.drop_column("rep_wallets", "refund_held_kobo")
    op.drop_column("payments", "refunded_at")
    op.drop_constraint("ck_payments_status", "payments", type_="check")
    op.create_check_constraint(
        "ck_payments_status", "payments", "status IN ('pending', 'success', 'failed', 'abandoned')"
    )
