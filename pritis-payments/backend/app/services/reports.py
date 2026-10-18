"""The numbers behind the dashboards (design section 10). Every figure is computed from the
payments and ledger tables when asked, never from stored totals, so it can't drift from
the money. Days and months are Lagos time."""

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.models import (
    AdminUser,
    BankAccountChange,
    Class,
    ClassDue,
    DueRevision,
    LedgerAccount,
    LedgerEntry,
    Payment,
    PayoutRequest,
    ReconciliationRun,
    Refund,
    RepApplication,
    User,
)

LAGOS = ZoneInfo("Africa/Lagos")
REVENUE_ACCOUNTS = ("platform_revenue", "payout_fee_revenue")
# Money that came in: a refunded payment still came in; its refund is reported separately.
RECEIVED = Payment.status.in_(("success", "refunded"))
OWED_KINDS = ("rep_pending", "rep_available", "payout_hold")  # refund holds are on their way to students


def period(start: date, end: date) -> tuple[datetime, datetime]:
    """Lagos dates (inclusive) -> UTC datetimes [start of first day, start of the day after last)."""
    return (datetime.combine(start, time.min, tzinfo=LAGOS),
            datetime.combine(end + timedelta(days=1), time.min, tzinfo=LAGOS))


# Written into the SQL as a constant: as a bound parameter, the same expression in SELECT
# and GROUP BY would be two different parameters and PostgreSQL would refuse to group.
LAGOS_SQL = literal_column("'Africa/Lagos'")


def lagos_day(column):
    return func.date(func.timezone(LAGOS_SQL, column))


def _signed(account_type: str):
    """Entry amount signed in the account's normal direction."""
    debit_normal = account_type in ("asset", "expense")
    return case(
        (LedgerEntry.direction == ("debit" if debit_normal else "credit"), LedgerEntry.amount_kobo),
        else_=-LedgerEntry.amount_kobo,
    )


async def account_total(db: AsyncSession, codes: tuple[str, ...] | None = None, *, kind: str | None = None,
                        start: datetime | None = None, end: datetime | None = None) -> int:
    """Net balance (normal direction) of the given account codes, or every rep account of a
    kind ('rep_pending', 'rep_available', 'payout_hold'), optionally within a period."""
    filters = []
    if codes:
        filters.append(LedgerAccount.code.in_(codes))
    if kind:
        filters.append(LedgerAccount.code.like(f"{kind}:%"))
    if start:
        filters.append(LedgerEntry.created_at >= start)
    if end:
        filters.append(LedgerEntry.created_at < end)
    rows = await db.execute(
        select(LedgerAccount.type, func.coalesce(func.sum(
            case((LedgerEntry.direction == "debit", LedgerEntry.amount_kobo), else_=-LedgerEntry.amount_kobo)
        ), 0))
        .join(LedgerEntry, LedgerEntry.account_id == LedgerAccount.id)
        .where(*filters).group_by(LedgerAccount.type)
    )
    total = 0
    for account_type, debit_minus_credit in rows:
        total += debit_minus_credit if account_type in ("asset", "expense") else -debit_minus_credit
    return int(total)


# --- Summary cards -------------------------------------------------------------------------


async def summary(db: AsyncSession) -> dict[str, Any]:
    now = utcnow()
    today_start, today_end = period(now.astimezone(LAGOS).date(), now.astimezone(LAGOS).date())
    success = RECEIVED
    volume, count, fees = (await db.execute(
        select(func.coalesce(func.sum(Payment.total_kobo), 0), func.count(),
               func.coalesce(func.sum(Payment.paystack_fee_actual_kobo), 0)).where(success)
    )).one()
    today_count, today_value = (await db.execute(
        select(func.count(), func.coalesce(func.sum(Payment.total_kobo), 0))
        .where(success, Payment.paid_at >= today_start, Payment.paid_at < today_end)
    )).one()
    service_revenue = await account_total(db, ("platform_revenue",))
    payout_revenue = await account_total(db, ("payout_fee_revenue",))
    pending = await account_total(db, kind="rep_pending")
    available = await account_total(db, kind="rep_available")
    held = await account_total(db, kind="payout_hold")
    refund_held = await account_total(db, kind="refund_hold")
    refund_fees = await account_total(db, ("refund_fee_expense",))
    refunded_count, refunded = (await db.execute(
        select(func.count(), func.coalesce(func.sum(Refund.amount_kobo), 0)).where(Refund.status == "processed")
    )).one()
    waiting_count, waiting_amount = (await db.execute(
        select(func.count(), func.coalesce(func.sum(PayoutRequest.amount_kobo), 0))
        .where(PayoutRequest.status == "requested")
    )).one()
    paid_out = await db.scalar(
        select(func.coalesce(func.sum(PayoutRequest.net_kobo), 0)).where(PayoutRequest.status == "paid")
    )
    latest = await db.scalar(select(ReconciliationRun).order_by(ReconciliationRun.created_at.desc()).limit(1))
    return {
        "revenue_kobo": service_revenue + payout_revenue,
        "service_charge_revenue_kobo": service_revenue,
        "payout_fee_revenue_kobo": payout_revenue,
        "gross_volume_kobo": int(volume),
        "payments_count": int(count),
        "paystack_fees_kobo": int(fees),
        "owed_to_reps_kobo": pending + available + held,
        "owed_pending_kobo": pending,
        "owed_available_kobo": available,
        "owed_held_kobo": held,
        "payouts_waiting_count": int(waiting_count),
        "payouts_waiting_kobo": int(waiting_amount),
        "paid_out_kobo": int(paid_out),
        "refunds_in_progress_kobo": refund_held,
        "refunded_count": int(refunded_count),
        "refunded_kobo": int(refunded),
        "refund_fee_expense_kobo": refund_fees,
        "payments_today_count": int(today_count),
        "payments_today_kobo": int(today_value),
        "reconciliation": {"status": latest.status, "at": latest.created_at.isoformat(), "items": len(latest.items)}
        if latest else None,
    }


async def overview_counts(db: AsyncSession) -> dict[str, int]:
    """Approvals waiting and user counts, for the overview."""
    async def count(model, *where) -> int:
        return int(await db.scalar(select(func.count()).select_from(model).where(*where)) or 0)

    return {
        "rep_applications_waiting": await count(RepApplication, RepApplication.status == "pending"),
        "dues_waiting": await count(DueRevision, DueRevision.status == "pending"),
        "payouts_waiting": await count(PayoutRequest, PayoutRequest.status == "requested"),
        "bank_changes_waiting": await count(BankAccountChange, BankAccountChange.status == "pending"),
        "refunds_waiting": await count(Refund, Refund.status == "requested"),
        "students": await count(User, User.status == "active"),
        "reps": await count(RepApplication, RepApplication.status == "approved"),
        "classes": await count(Class),
        "live_dues": await count(ClassDue, ClassDue.status == "approved", ClassDue.is_live.is_(True)),
        "staff": await count(AdminUser, AdminUser.status == "active"),
    }


# --- Graphs ----------------------------------------------------------------------------------


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


async def trends(db: AsyncSession, start: date, end: date) -> dict[str, Any]:
    lo, hi = period(start, end)
    success = RECEIVED
    in_period = (Payment.paid_at >= lo, Payment.paid_at < hi)

    # Payments per day: count and value.
    per_day = {d: (c, v) for d, c, v in await db.execute(
        select(lagos_day(Payment.paid_at), func.count(), func.sum(Payment.total_kobo))
        .where(success, *in_period).group_by(lagos_day(Payment.paid_at))
    )}
    days = _days(start, end)
    payments_per_day = [
        {"day": d.isoformat(), "count": int(per_day.get(d, (0, 0))[0]), "value_kobo": int(per_day.get(d, (0, 0))[1] or 0)}
        for d in days
    ]

    # Collections by class (what students paid, due amounts only).
    by_class = [
        {"class_name": name, "collected_kobo": int(total), "payments": int(n)}
        for name, total, n in await db.execute(
            select(Class.name, func.sum(Payment.base_kobo), func.count())
            .join(ClassDue, ClassDue.id == Payment.due_id).join(Class, Class.id == ClassDue.class_id)
            .where(success, *in_period).group_by(Class.name).order_by(func.sum(Payment.base_kobo).desc())
        )
    ]

    # Payment channel mix.
    channels = [
        {"channel": ch or "unknown", "count": int(n), "value_kobo": int(v)}
        for ch, n, v in await db.execute(
            select(Payment.channel, func.count(), func.sum(Payment.total_kobo))
            .where(success, *in_period).group_by(Payment.channel).order_by(func.count().desc())
        )
    ]

    # Payout requests by status (requested in the period).
    payouts = [
        {"status": s, "count": int(n), "amount_kobo": int(a)}
        for s, n, a in await db.execute(
            select(PayoutRequest.status, func.count(), func.sum(PayoutRequest.amount_kobo))
            .where(PayoutRequest.created_at >= lo, PayoutRequest.created_at < hi)
            .group_by(PayoutRequest.status)
        )
    ]

    # Owed to reps vs paid out, over time: end-of-day balances of the rep accounts, and
    # payouts paid so far, from the ledger.
    owed_before = sum([await account_total(db, kind=k, end=lo) for k in OWED_KINDS])
    owed_change = {d: int(v) for d, v in await db.execute(
        select(lagos_day(LedgerEntry.created_at), func.sum(_signed("liability")))
        .join(LedgerAccount, LedgerAccount.id == LedgerEntry.account_id)
        .where(LedgerAccount.owner_user_id.is_not(None), ~LedgerAccount.code.like("refund_hold:%"),
               LedgerEntry.created_at >= lo, LedgerEntry.created_at < hi)
        .group_by(lagos_day(LedgerEntry.created_at))
    )}
    paid_before = int(await db.scalar(
        select(func.coalesce(func.sum(PayoutRequest.net_kobo), 0))
        .where(PayoutRequest.status == "paid", PayoutRequest.paid_at < lo)
    ))
    paid_change = {d: int(v) for d, v in await db.execute(
        select(lagos_day(PayoutRequest.paid_at), func.sum(PayoutRequest.net_kobo))
        .where(PayoutRequest.status == "paid", PayoutRequest.paid_at >= lo, PayoutRequest.paid_at < hi)
        .group_by(lagos_day(PayoutRequest.paid_at))
    )}
    owed_vs_paid, owed, paid = [], owed_before, paid_before
    for d in days:
        owed += owed_change.get(d, 0)
        paid += paid_change.get(d, 0)
        owed_vs_paid.append({"day": d.isoformat(), "owed_kobo": owed, "paid_out_kobo": paid})

    return {
        "payments_per_day": payments_per_day,
        "collections_by_class": by_class,
        "channel_mix": channels,
        "payouts_by_status": payouts,
        "owed_vs_paid_out": owed_vs_paid,
    }


async def revenue_by_month(db: AsyncSession, start: date, end: date) -> list[dict[str, Any]]:
    """Platform revenue per month from the ledger's revenue accounts."""
    lo, hi = period(start, end)
    month = func.date_trunc(literal_column("'month'"), func.timezone(LAGOS_SQL, LedgerEntry.created_at))
    rows = await db.execute(
        select(month, LedgerAccount.code, func.sum(_signed("revenue")))
        .join(LedgerAccount, LedgerAccount.id == LedgerEntry.account_id)
        .where(LedgerAccount.code.in_(REVENUE_ACCOUNTS), LedgerEntry.created_at >= lo, LedgerEntry.created_at < hi)
        .group_by(month, LedgerAccount.code).order_by(month)
    )
    months: dict[str, dict[str, Any]] = {}
    for m, code, amount in rows:
        key = m.strftime("%Y-%m")
        entry = months.setdefault(key, {"month": key, "service_charge_kobo": 0, "payout_fee_kobo": 0})
        entry["service_charge_kobo" if code == "platform_revenue" else "payout_fee_kobo"] += int(amount)
    for entry in months.values():
        entry["total_kobo"] = entry["service_charge_kobo"] + entry["payout_fee_kobo"]
    return list(months.values())


# --- Statements --------------------------------------------------------------------------------


async def period_figures(db: AsyncSession, start: date, end: date) -> dict[str, int]:
    lo, hi = period(start, end)
    success = (RECEIVED, Payment.paid_at >= lo, Payment.paid_at < hi)
    count, volume, dues_total, charges, fees = (await db.execute(
        select(func.count(), func.coalesce(func.sum(Payment.total_kobo), 0), func.coalesce(func.sum(Payment.base_kobo), 0),
               func.coalesce(func.sum(Payment.service_charge_kobo), 0),
               func.coalesce(func.sum(Payment.paystack_fee_actual_kobo), 0)).where(*success)
    )).one()
    paid_count, paid_out, payout_fees = (await db.execute(
        select(func.count(), func.coalesce(func.sum(PayoutRequest.net_kobo), 0),
               func.coalesce(func.sum(PayoutRequest.platform_fee_kobo), 0))
        .where(PayoutRequest.status == "paid", PayoutRequest.paid_at >= lo, PayoutRequest.paid_at < hi)
    )).one()
    refunds_count, refunded = (await db.execute(
        select(func.count(), func.coalesce(func.sum(Refund.amount_kobo), 0))
        .where(Refund.status == "processed", Refund.processed_at >= lo, Refund.processed_at < hi)
    )).one()
    return {
        "payments_count": int(count), "gross_volume_kobo": int(volume), "dues_kobo": int(dues_total),
        "service_charges_kobo": int(charges), "paystack_fees_kobo": int(fees),
        "service_charge_revenue_kobo": await account_total(db, ("platform_revenue",), start=lo, end=hi),
        "payout_fee_revenue_kobo": await account_total(db, ("payout_fee_revenue",), start=lo, end=hi),
        "payouts_paid_count": int(paid_count), "paid_out_kobo": int(paid_out), "payout_platform_fees_kobo": int(payout_fees),
        "refunds_count": int(refunds_count), "refunded_kobo": int(refunded),
        "refund_fee_expense_kobo": await account_total(db, ("refund_fee_expense",), start=lo, end=hi),
    }
