"""Phase 6: payments, webhook events, ledger, rep wallets

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-17
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
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
        "payments",
        _id(),
        sa.Column("reference", sa.String(40), nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("due_id", UUID, nullable=False),
        sa.Column("revision_id", UUID, nullable=False),
        sa.Column("base_kobo", sa.BigInteger(), nullable=False),
        sa.Column("service_charge_kobo", sa.BigInteger(), nullable=False),
        sa.Column("paystack_part_kobo", sa.BigInteger(), nullable=False),
        sa.Column("platform_part_kobo", sa.BigInteger(), nullable=False),
        sa.Column("paystack_fee_actual_kobo", sa.BigInteger(), nullable=True),
        sa.Column("total_kobo", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), server_default="NGN", nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("channel", sa.String(30), nullable=True),
        sa.Column("paystack_id", sa.BigInteger(), nullable=True),
        sa.Column("authorization_url", sa.String(300), nullable=True),
        sa.Column("paid_at", TS, nullable=True),
        sa.Column("settled_at", TS, nullable=True),
        sa.Column("last_checked_at", TS, nullable=True),
        sa.Column("matric_no", sa.String(30), nullable=True),
        sa.Column("receipt_no", sa.String(20), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_payments"),
        sa.UniqueConstraint("reference", name="uq_payments_reference"),
        sa.UniqueConstraint("receipt_no", name="uq_payments_receipt_no"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_payments_user_id_users", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["due_id"], ["class_dues.id"], name="fk_payments_due_id_class_dues", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["revision_id"], ["due_revisions.id"], name="fk_payments_revision_id_due_revisions", ondelete="RESTRICT"
        ),
        sa.CheckConstraint("status IN ('pending', 'success', 'failed', 'abandoned')", name="ck_payments_status"),
        sa.CheckConstraint("base_kobo > 0", name="ck_payments_base_positive"),
        sa.CheckConstraint("paystack_part_kobo >= 0 AND platform_part_kobo >= 0", name="ck_payments_parts_non_negative"),
        sa.CheckConstraint(
            "service_charge_kobo = paystack_part_kobo + platform_part_kobo", name="ck_payments_charge_is_parts"
        ),
        sa.CheckConstraint(
            "total_kobo = base_kobo + service_charge_kobo", name="ck_payments_total_is_base_plus_charge"
        ),
    )
    op.create_index("ix_payments_due_id_status", "payments", ["due_id", "status"])
    op.create_index("ix_payments_user_id_due_id", "payments", ["user_id", "due_id"])

    op.create_table(
        "webhook_events",
        _id(),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("paystack_event_key", sa.String(200), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("signature_ok", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(20), server_default="received", nullable=False),
        sa.Column("processed_at", TS, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_webhook_events"),
        sa.UniqueConstraint("paystack_event_key", name="uq_webhook_events_paystack_event_key"),
        sa.CheckConstraint(
            "status IN ('received', 'processed', 'ignored', 'failed')", name="ck_webhook_events_status"
        ),
    )
    op.create_index("ix_webhook_events_status_created_at", "webhook_events", ["status", "created_at"])

    op.create_table(
        "ledger_accounts",
        _id(),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("owner_user_id", UUID, nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_ledger_accounts"),
        sa.UniqueConstraint("code", name="uq_ledger_accounts_code"),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name="fk_ledger_accounts_owner_user_id_users", ondelete="RESTRICT"
        ),
        sa.CheckConstraint("type IN ('asset', 'liability', 'revenue', 'expense')", name="ck_ledger_accounts_type"),
    )
    op.create_index("ix_ledger_accounts_owner_user_id", "ledger_accounts", ["owner_user_id"])

    op.create_table(
        "ledger_entries",
        _id(),
        sa.Column("txn_id", UUID, nullable=False),
        sa.Column("account_id", UUID, nullable=False),
        sa.Column("direction", sa.String(6), nullable=False),
        sa.Column("amount_kobo", sa.BigInteger(), nullable=False),
        sa.Column("ref_type", sa.String(40), nullable=False),
        sa.Column("ref_id", UUID, nullable=False),
        sa.Column("memo", sa.String(200), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_ledger_entries"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["ledger_accounts.id"], name="fk_ledger_entries_account_id_ledger_accounts",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("direction IN ('debit', 'credit')", name="ck_ledger_entries_direction"),
        sa.CheckConstraint("amount_kobo > 0", name="ck_ledger_entries_amount_positive"),
    )
    op.create_index("ix_ledger_entries_txn_id", "ledger_entries", ["txn_id"])
    op.create_index("ix_ledger_entries_account_id_created_at", "ledger_entries", ["account_id", "created_at"])
    op.create_index("ix_ledger_entries_ref", "ledger_entries", ["ref_type", "ref_id"])

    # Append-only: the database itself refuses to change or remove ledger rows.
    op.execute(
        """
        CREATE FUNCTION ledger_entries_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'ledger_entries is append-only: % is not allowed', TG_OP;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER ledger_entries_no_update_or_delete
        BEFORE UPDATE OR DELETE ON ledger_entries
        FOR EACH ROW EXECUTE FUNCTION ledger_entries_append_only()
        """
    )

    op.create_table(
        "rep_wallets",
        _id(),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("pending_kobo", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("available_kobo", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("held_kobo", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("version", sa.BigInteger(), server_default="0", nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_rep_wallets"),
        sa.UniqueConstraint("user_id", name="uq_rep_wallets_user_id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_rep_wallets_user_id_users", ondelete="RESTRICT"),
    )

    # Human-friendly receipt numbers: R2026-000001, ...
    op.execute("CREATE SEQUENCE receipt_no_seq START 1")


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS receipt_no_seq")
    op.drop_table("rep_wallets")
    op.execute("DROP TRIGGER IF EXISTS ledger_entries_no_update_or_delete ON ledger_entries")
    op.execute("DROP FUNCTION IF EXISTS ledger_entries_append_only()")
    op.drop_table("ledger_entries")
    op.drop_table("ledger_accounts")
    op.drop_table("webhook_events")
    op.drop_table("payments")
