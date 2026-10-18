"""/api/admin/reports/*, /api/admin/ledger, /api/admin/reconciliation: the accountant's
dashboard, graphs, exports and ledger explorer (design sections 7 and 10).
Accountant and super admin; the overview counts are for every staff role."""

import uuid
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.db import get_session
from app.core.deps import ACCOUNTANT, SUPER_ADMIN, SUPPORT, require_roles
from app.core.errors import AppError
from app.models import AdminUser, LedgerAccount, LedgerEntry, ReconciliationRun
from app.services import reports, statements

router = APIRouter(tags=["admin reports"])
money_staff = require_roles(ACCOUNTANT, SUPER_ADMIN)
any_staff = require_roles(SUPER_ADMIN, ACCOUNTANT, SUPPORT)
MAX_DAYS = 400


def date_range(
    from_: date | None = Query(None, alias="from"), to: date | None = Query(None),
) -> tuple[date, date]:
    """Lagos dates, inclusive. Defaults to the last 30 days."""
    today = utcnow().astimezone(reports.LAGOS).date()
    end = to or today
    start = from_ or end - timedelta(days=29)
    if start > end:
        raise AppError("bad_range", "The start date must be on or before the end date", 422)
    if (end - start).days > MAX_DAYS:
        raise AppError("bad_range", f"Choose at most {MAX_DAYS} days", 422)
    return start, end


@router.get("/reports/overview")
async def overview(_: AdminUser = Depends(any_staff), db: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Approvals waiting and counts: shown on every staff member's overview."""
    return await reports.overview_counts(db)


@router.get("/reports/summary")
async def summary(_: AdminUser = Depends(money_staff), db: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    return await reports.summary(db)


@router.get("/reports/trends")
async def trends(
    period: tuple[date, date] = Depends(date_range),
    _: AdminUser = Depends(money_staff), db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await reports.trends(db, *period)


@router.get("/reports/revenue")
async def revenue(
    period: tuple[date, date] = Depends(date_range),
    _: AdminUser = Depends(money_staff), db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return {
        "by_month": await reports.revenue_by_month(db, *period),
        "period": await reports.period_figures(db, *period),
    }


@router.get("/reports/export.csv")
async def export_csv(
    period: tuple[date, date] = Depends(date_range),
    _: AdminUser = Depends(money_staff), db: AsyncSession = Depends(get_session),
) -> Response:
    start, end = period
    return Response(
        content="﻿" + await statements.payments_csv(db, start, end),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="payments-{start}-to-{end}.csv"',
                 "Cache-Control": "private, no-store"},
    )


@router.get("/reports/statement.pdf")
async def statement_pdf(
    period: tuple[date, date] = Depends(date_range),
    _: AdminUser = Depends(money_staff), db: AsyncSession = Depends(get_session),
) -> Response:
    start, end = period
    return Response(
        content=await statements.statement_pdf(db, start, end), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="statement-{start}-to-{end}.pdf"',
                 "Cache-Control": "private, no-store"},
    )


# --- Ledger explorer -----------------------------------------------------------------------------


class AccountOut(BaseModel):
    code: str
    name: str
    type: str
    balance_kobo: int
    entries: int


@router.get("/ledger/accounts", response_model=list[AccountOut])
async def ledger_accounts(_: AdminUser = Depends(money_staff), db: AsyncSession = Depends(get_session)) -> list[AccountOut]:
    rows = await db.execute(
        select(LedgerAccount.code, LedgerAccount.name, LedgerAccount.type, func.count(LedgerEntry.id),
               func.coalesce(func.sum(case((LedgerEntry.direction == "debit", LedgerEntry.amount_kobo),
                                           else_=-LedgerEntry.amount_kobo)), 0))
        .outerjoin(LedgerEntry, LedgerEntry.account_id == LedgerAccount.id)
        .group_by(LedgerAccount.id).order_by(LedgerAccount.type, LedgerAccount.code)
    )
    return [
        AccountOut(code=code, name=name, type=t, entries=n,
                   balance_kobo=int(net if t in ("asset", "expense") else -net))
        for code, name, t, n, net in rows
    ]


class EntryOut(BaseModel):
    id: uuid.UUID
    created_at: Any
    txn_id: uuid.UUID
    account_code: str
    account_name: str
    direction: str
    amount_kobo: int
    ref_type: str
    ref_id: uuid.UUID
    memo: str | None


class EntryPage(BaseModel):
    items: list[EntryOut]
    total: int
    page: int
    page_size: int


@router.get("/ledger", response_model=EntryPage)
async def ledger_entries(
    account: str | None = Query(None, max_length=80, description="exact code, or a prefix like rep_pending:"),
    ref_type: str | None = Query(None, max_length=40),
    txn_id: uuid.UUID | None = None,
    period: tuple[date, date] = Depends(date_range),
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    _: AdminUser = Depends(money_staff), db: AsyncSession = Depends(get_session),
) -> EntryPage:
    lo, hi = reports.period(*period)
    filters = [LedgerEntry.created_at >= lo, LedgerEntry.created_at < hi]
    if account:
        filters.append(LedgerAccount.code.like(f"{account}%") if account.endswith(":") else LedgerAccount.code == account)
    if ref_type:
        filters.append(LedgerEntry.ref_type == ref_type)
    if txn_id:
        filters = [LedgerEntry.txn_id == txn_id]  # a whole transaction, whenever it was
    base = select(LedgerEntry, LedgerAccount).join(LedgerAccount, LedgerAccount.id == LedgerEntry.account_id).where(*filters)
    total = await db.scalar(select(func.count()).select_from(base.subquery()))
    rows = await db.execute(
        base.order_by(LedgerEntry.created_at.desc(), LedgerEntry.txn_id, LedgerEntry.direction.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )
    return EntryPage(
        items=[EntryOut(id=e.id, created_at=e.created_at, txn_id=e.txn_id, account_code=a.code, account_name=a.name,
                        direction=e.direction, amount_kobo=e.amount_kobo, ref_type=e.ref_type, ref_id=e.ref_id,
                        memo=e.memo) for e, a in rows],
        total=total or 0, page=page, page_size=page_size,
    )


@router.get("/reconciliation")
async def reconciliation_runs(
    limit: int = Query(30, ge=1, le=200),
    _: AdminUser = Depends(money_staff), db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    runs = (await db.scalars(select(ReconciliationRun).order_by(ReconciliationRun.created_at.desc()).limit(limit))).all()
    return [{"id": str(r.id), "created_at": r.created_at.isoformat(), "status": r.status,
             "period_start": r.period_start.isoformat(), "summary": r.summary, "items": r.items} for r in runs]
