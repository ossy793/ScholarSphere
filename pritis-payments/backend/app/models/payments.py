import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin


class Payment(IdMixin, TimestampMixin, Base):
    """One row per payment attempt. Amounts in kobo; the split is internal (students see
    only the due, one service charge line and the total)."""

    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'success', 'failed', 'abandoned', 'refunded')", name="status"),
        CheckConstraint("base_kobo > 0", name="base_positive"),
        CheckConstraint("paystack_part_kobo >= 0 AND platform_part_kobo >= 0", name="parts_non_negative"),
        CheckConstraint("service_charge_kobo = paystack_part_kobo + platform_part_kobo", name="charge_is_parts"),
        CheckConstraint("total_kobo = base_kobo + service_charge_kobo", name="total_is_base_plus_charge"),
        Index("ix_payments_due_id_status", "due_id", "status"),
        Index("ix_payments_user_id_due_id", "user_id", "due_id"),
    )

    reference: Mapped[str] = mapped_column(String(40), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    due_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("class_dues.id", ondelete="RESTRICT"))
    # The approved terms the student paid against.
    revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("due_revisions.id", ondelete="RESTRICT")
    )
    base_kobo: Mapped[int] = mapped_column(BigInteger)
    service_charge_kobo: Mapped[int] = mapped_column(BigInteger)
    paystack_part_kobo: Mapped[int] = mapped_column(BigInteger)
    platform_part_kobo: Mapped[int] = mapped_column(BigInteger)
    # Paystack's real fee, from charge.success.
    paystack_fee_actual_kobo: Mapped[int | None] = mapped_column(BigInteger)
    total_kobo: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), server_default="NGN")
    status: Mapped[str] = mapped_column(String(20), server_default="pending")
    channel: Mapped[str | None] = mapped_column(String(30))
    paystack_id: Mapped[int | None] = mapped_column(BigInteger)
    authorization_url: Mapped[str | None] = mapped_column(String(300))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When Paystack settled it to Pritis (phase 7); then the rep's money becomes Available.
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # "paystack:<settlement id>", or the bank statement reference the accountant entered.
    settlement_reference: Mapped[str | None] = mapped_column(String(120))
    # Last time the server asked Paystack about this payment (verify fallback).
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    matric_no: Mapped[str | None] = mapped_column(String(30))
    receipt_no: Mapped[str | None] = mapped_column(String(20), unique=True)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    # Set with status 'refunded' when Paystack confirms a refund (phase 10).
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookEvent(IdMixin, TimestampMixin, Base):
    """Raw Paystack events, stored before processing: once per event key (dedupe) and
    kept for replay."""

    __tablename__ = "webhook_events"
    __table_args__ = (
        CheckConstraint("status IN ('received', 'processed', 'ignored', 'failed')", name="status"),
        Index("ix_webhook_events_status_created_at", "status", "created_at"),
    )

    event_type: Mapped[str] = mapped_column(String(60))
    paystack_event_key: Mapped[str] = mapped_column(String(200), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    signature_ok: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(20), server_default="received")
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class LedgerAccount(IdMixin, TimestampMixin, Base):
    """Chart of accounts. Rep accounts are created on first use, one set per rep."""

    __tablename__ = "ledger_accounts"
    __table_args__ = (
        CheckConstraint("type IN ('asset', 'liability', 'revenue', 'expense')", name="type"),
    )

    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(20))
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )


class LedgerEntry(IdMixin, TimestampMixin, Base):
    """Append-only money movements. A database trigger refuses UPDATE and DELETE; mistakes
    are corrected by new, reversing entries. Each txn_id balances (debits = credits)."""

    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint("direction IN ('debit', 'credit')", name="direction"),
        CheckConstraint("amount_kobo > 0", name="amount_positive"),
        Index("ix_ledger_entries_account_id_created_at", "account_id", "created_at"),
        Index("ix_ledger_entries_ref", "ref_type", "ref_id"),
    )

    txn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ledger_accounts.id", ondelete="RESTRICT")
    )
    direction: Mapped[str] = mapped_column(String(6))
    amount_kobo: Mapped[int] = mapped_column(BigInteger)
    ref_type: Mapped[str] = mapped_column(String(40))
    ref_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    memo: Mapped[str | None] = mapped_column(String(200))


class RepWallet(IdMixin, TimestampMixin, Base):
    """Cached balances per rep, updated in the same transaction as the ledger under a row
    lock; the nightly check (phase 7) recomputes them from the ledger."""

    __tablename__ = "rep_wallets"
    __table_args__ = (UniqueConstraint("user_id", name="uq_rep_wallets_user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    pending_kobo: Mapped[int] = mapped_column(BigInteger, server_default="0")
    available_kobo: Mapped[int] = mapped_column(BigInteger, server_default="0")
    held_kobo: Mapped[int] = mapped_column(BigInteger, server_default="0")
    refund_held_kobo: Mapped[int] = mapped_column(BigInteger, server_default="0")  # refunds in progress
    version: Mapped[int] = mapped_column(BigInteger, server_default="0")
