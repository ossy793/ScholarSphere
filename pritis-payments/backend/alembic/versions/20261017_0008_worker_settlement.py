"""Phase 7: worker job state, reconciliation runs, settlement reference on payments

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-17
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
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
    op.add_column("payments", sa.Column("settlement_reference", sa.String(120), nullable=True))

    op.create_table(
        "worker_jobs",
        *_common(),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("last_started_at", TS, nullable=True),
        sa.Column("last_finished_at", TS, nullable=True),
        sa.Column("last_success_at", TS, nullable=True),
        sa.Column("last_status", sa.String(10), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_result", sa.String(300), nullable=True),
        sa.Column("runs", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_worker_jobs"),
        sa.UniqueConstraint("name", name="uq_worker_jobs_name"),
        sa.CheckConstraint("last_status IN ('running', 'ok', 'error')", name="ck_worker_jobs_last_status"),
    )

    op.create_table(
        "reconciliation_runs",
        *_common(),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("period_start", TS, nullable=False),
        sa.Column("period_end", TS, nullable=False),
        sa.Column("summary", postgresql.JSONB(), nullable=False),
        sa.Column("items", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_reconciliation_runs"),
        sa.CheckConstraint("status IN ('clean', 'mismatch', 'error')", name="ck_reconciliation_runs_status"),
    )
    op.create_index("ix_reconciliation_runs_created_at", "reconciliation_runs", ["created_at"])


def downgrade() -> None:
    op.drop_table("reconciliation_runs")
    op.drop_table("worker_jobs")
    op.drop_column("payments", "settlement_reference")
