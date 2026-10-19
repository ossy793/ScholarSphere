"""/api/admin/revenue: Pritis's earnings in the Paystack balance and withdrawing them to
Pritis's bank. The accountant can look; only the super admin saves the account or withdraws."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import ACCOUNTANT, SUPER_ADMIN, require_roles
from app.core.errors import AppError
from app.models import AdminUser, RevenueWithdrawal
from app.schemas.rep import AccountNumber, BankCode
from app.services import app_settings, paystack, revenue
from app.services.payouts import transfer_fee

router = APIRouter(prefix="/revenue", tags=["admin revenue"])
money_staff = require_roles(ACCOUNTANT, SUPER_ADMIN)
super_admin_only = require_roles(SUPER_ADMIN)


def get_paystack() -> paystack.PaystackClient:
    return paystack.get_client()


class AccountIn(BaseModel):
    bank_code: BankCode
    account_number: AccountNumber


class AccountOut(BaseModel):
    label: str
    set_by: str
    set_at: datetime


class WithdrawIn(BaseModel):
    amount_kobo: int = Field(gt=0, le=1_000_000_000_00)
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None


class WithdrawalOut(BaseModel):
    id: uuid.UUID
    status: Literal["processing", "paid", "failed"]
    amount_kobo: int
    transfer_fee_kobo: int
    transfer_fee_actual_kobo: int | None
    bank_label: str
    note: str | None
    requested_by_email: str
    created_at: datetime
    paid_at: datetime | None
    failure_reason: str | None


class RevenueOut(BaseModel):
    settlement_mode: Literal["balance", "bank"]
    paystack_balance_kobo: int | None  # None if Paystack couldn't be reached
    owed_to_reps_kobo: int
    revenue_kobo: int
    processing_kobo: int
    withdrawn_kobo: int
    withdrawable_kobo: int  # Paystack's transfer fee included
    account: AccountOut | None
    withdrawals: list[WithdrawalOut]


class FeeOut(BaseModel):
    amount_kobo: int
    transfer_fee_kobo: int


def _withdrawal_out(w: RevenueWithdrawal, email: str) -> WithdrawalOut:
    return WithdrawalOut(
        id=w.id, status=w.status, amount_kobo=w.amount_kobo, transfer_fee_kobo=w.transfer_fee_kobo,
        transfer_fee_actual_kobo=w.transfer_fee_actual_kobo, bank_label=w.bank_label, note=w.note,
        requested_by_email=email, created_at=w.created_at, paid_at=w.paid_at, failure_reason=w.failure_reason,
    )


async def _overview(db: AsyncSession, ps: paystack.PaystackClient | None) -> RevenueOut:
    balance = None
    if ps is not None:
        try:
            balance = await ps.get_balance()
        except AppError:
            balance = None
    pos = await revenue.position(db, balance)
    account = await revenue.get_account(db)
    rows = (await db.execute(
        select(RevenueWithdrawal, AdminUser.email).join(AdminUser, AdminUser.id == RevenueWithdrawal.requested_by)
        .order_by(RevenueWithdrawal.created_at.desc()).limit(50)
    )).all()
    return RevenueOut(
        settlement_mode=(await app_settings.get_fee_settings(db)).settlement_mode,
        paystack_balance_kobo=pos.paystack_balance_kobo, owed_to_reps_kobo=pos.owed_to_reps_kobo,
        revenue_kobo=pos.revenue_kobo, processing_kobo=pos.processing_kobo, withdrawn_kobo=pos.withdrawn_kobo,
        withdrawable_kobo=pos.withdrawable_kobo,
        account=AccountOut(label=revenue.account_label(account), set_by=account["set_by"],
                           set_at=datetime.fromisoformat(account["set_at"])) if account else None,
        withdrawals=[_withdrawal_out(w, email) for w, email in rows],
    )


def _optional_paystack() -> paystack.PaystackClient | None:
    try:
        return paystack.get_client()
    except AppError:
        return None


@router.get("", response_model=RevenueOut)
async def overview(
    _: AdminUser = Depends(money_staff), db: AsyncSession = Depends(get_session),
    ps: paystack.PaystackClient | None = Depends(_optional_paystack),
) -> RevenueOut:
    return await _overview(db, ps)


@router.get("/fee", response_model=FeeOut)
async def fee(
    amount_kobo: int = Query(gt=0, le=1_000_000_000_00),
    _: AdminUser = Depends(money_staff), db: AsyncSession = Depends(get_session),
) -> FeeOut:
    return FeeOut(amount_kobo=amount_kobo,
                  transfer_fee_kobo=transfer_fee(amount_kobo, await app_settings.get_fee_settings(db)))


@router.get("/banks")
async def banks(
    _: AdminUser = Depends(super_admin_only), ps: paystack.PaystackClient = Depends(get_paystack)
) -> list[dict[str, str]]:
    return [{"code": b.code, "name": b.name} for b in await ps.list_banks()]


@router.put("/account", response_model=RevenueOut)
async def set_account(
    body: AccountIn, request: Request,
    actor: AdminUser = Depends(super_admin_only), db: AsyncSession = Depends(get_session),
    ps: paystack.PaystackClient = Depends(get_paystack),
) -> RevenueOut:
    """Looked up with Paystack; every super admin is emailed about the change."""
    await revenue.set_account(db, body.bank_code, body.account_number, actor, request, ps)
    await db.commit()
    return await _overview(db, ps)


@router.post("/withdrawals", response_model=RevenueOut, status_code=201)
async def withdraw(
    body: WithdrawIn, request: Request,
    actor: AdminUser = Depends(super_admin_only), db: AsyncSession = Depends(get_session),
    ps: paystack.PaystackClient = Depends(get_paystack),
) -> RevenueOut:
    await revenue.withdraw(db, body.amount_kobo, body.note, actor, request, ps)
    return await _overview(db, ps)
