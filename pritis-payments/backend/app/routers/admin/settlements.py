"""/api/admin/settlements: the accountant's view of settlement and the background checks.
Accountant and super admin."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import ACCOUNTANT, SUPER_ADMIN, require_roles
from app.models import AdminUser, ReconciliationRun, User, WorkerJob
from app.services import settlements
from app.worker.jobs import JOBS

router = APIRouter(prefix="/settlements", tags=["admin settlements"])
money_staff = require_roles(ACCOUNTANT, SUPER_ADMIN)


class UnsettledOut(BaseModel):
    payment_id: uuid.UUID
    reference: str
    paid_at: datetime
    class_name: str
    rep_name: str
    due_kobo: int  # the rep's share, which moves to Available
    total_kobo: int  # what the student paid
    paystack_fee_kobo: int | None
    channel: str | None


class UnsettledPage(BaseModel):
    items: list[UnsettledOut]
    count: int
    total_due_kobo: int
    total_paid_kobo: int


class MarkSettledIn(BaseModel):
    payment_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    bank_reference: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=100)]


class MarkSettledOut(BaseModel):
    settled: int


class JobOut(BaseModel):
    name: str
    description: str
    schedule: str
    last_status: str | None
    last_started_at: datetime | None
    last_success_at: datetime | None
    last_result: str | None
    last_error: str | None


class ReconciliationOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    status: str
    period_start: datetime
    period_end: datetime
    summary: dict[str, Any]
    items: list[dict[str, Any]]


class StatusOut(BaseModel):
    jobs: list[JobOut]
    latest_reconciliation: ReconciliationOut | None


@router.get("/unsettled", response_model=UnsettledPage)
async def unsettled(_: AdminUser = Depends(money_staff), db: AsyncSession = Depends(get_session)) -> UnsettledPage:
    rows = await settlements.unsettled_payments(db)
    reps = {u.id: u.full_name for u in (await db.scalars(
        select(User).where(User.id.in_({klass.rep_user_id for _, klass, _ in rows}))
    )).all()} if rows else {}
    items = [
        UnsettledOut(
            payment_id=p.id, reference=p.reference, paid_at=p.paid_at, class_name=klass.name,
            rep_name=reps.get(klass.rep_user_id, ""), due_kobo=p.base_kobo, total_kobo=p.total_kobo,
            paystack_fee_kobo=p.paystack_fee_actual_kobo, channel=p.channel,
        )
        for p, klass, _ in rows
    ]
    return UnsettledPage(
        items=items, count=len(items),
        total_due_kobo=sum(i.due_kobo for i in items), total_paid_kobo=sum(i.total_kobo for i in items),
    )


@router.post("/mark", response_model=MarkSettledOut)
async def mark(
    body: MarkSettledIn, request: Request,
    actor: AdminUser = Depends(money_staff), db: AsyncSession = Depends(get_session),
) -> MarkSettledOut:
    """Fallback when Paystack's settlement data isn't available: after checking the bank
    statement, move these payments' rep shares from Pending to Available."""
    count = await settlements.mark_settled(db, body.payment_ids, body.bank_reference, actor, request)
    await db.commit()
    return MarkSettledOut(settled=count)


@router.get("/status", response_model=StatusOut)
async def status(_: AdminUser = Depends(money_staff), db: AsyncSession = Depends(get_session)) -> StatusOut:
    states = {s.name: s for s in (await db.scalars(select(WorkerJob))).all()}
    jobs = []
    for job in JOBS:
        s = states.get(job.name)
        jobs.append(JobOut(
            name=job.name, description=job.description,
            schedule=f"every {int(job.every.total_seconds())} s" if job.every else f"daily {job.daily_at:%H:%M} Lagos",
            last_status=s.last_status if s else None, last_started_at=s.last_started_at if s else None,
            last_success_at=s.last_success_at if s else None, last_result=s.last_result if s else None,
            last_error=s.last_error if s else None,
        ))
    latest = await db.scalar(select(ReconciliationRun).order_by(ReconciliationRun.created_at.desc()).limit(1))
    return StatusOut(
        jobs=jobs,
        latest_reconciliation=ReconciliationOut.model_validate(latest, from_attributes=True) if latest else None,
    )
