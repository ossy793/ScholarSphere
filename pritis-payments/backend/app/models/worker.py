from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin


class WorkerJob(IdMixin, TimestampMixin, Base):
    """When each background job last ran. Kept in the database so a restart neither
    skips a nightly job nor runs one twice."""

    __tablename__ = "worker_jobs"
    __table_args__ = (
        CheckConstraint("last_status IN ('running', 'ok', 'error')", name="last_status"),
    )

    name: Mapped[str] = mapped_column(String(60), unique=True)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(10))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_result: Mapped[str | None] = mapped_column(String(300))
    runs: Mapped[int] = mapped_column(Integer, server_default="0")


class ReconciliationRun(IdMixin, TimestampMixin, Base):
    """One nightly check: the ledger, reps' cached balances, and Paystack against our payments."""

    __tablename__ = "reconciliation_runs"
    __table_args__ = (
        CheckConstraint("status IN ('clean', 'mismatch', 'error')", name="status"),
        Index("ix_reconciliation_runs_created_at", "created_at"),
    )

    status: Mapped[str] = mapped_column(String(10))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Counts per check, e.g. {"payments_checked": 12, "unbalanced_transactions": 0, ...}
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Each problem found: {"kind": ..., "detail": ..., "ref": ...}
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
