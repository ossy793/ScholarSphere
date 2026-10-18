"""/api/admin/payouts, /api/admin/paystack/balance and /api/admin/bank-changes.
Accountant and super admin (design section 2; bank changes by owner's decision)."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.db import get_session
from app.core.deps import ACCOUNTANT, SUPER_ADMIN, require_roles
from app.models import AdminUser, BankAccountChange, Class, PayoutRequest, RepBankAccount, RepWallet, User
from app.routers.user.payouts import change_out, payout_out
from app.schemas.payouts import AdminBankChangeOut, AdminPayoutOut, AdminPayoutPage, BalanceOut, ReasonIn
from app.services import bank_changes, payouts, paystack

router = APIRouter(tags=["admin payouts"])
money_staff = require_roles(ACCOUNTANT, SUPER_ADMIN)


def get_paystack() -> paystack.PaystackClient:
    return paystack.get_client()


async def _payout_out(db: AsyncSession, p: PayoutRequest) -> AdminPayoutOut:
    rep = await db.get(User, p.rep_user_id)
    klass = await db.scalar(select(Class).where(Class.rep_user_id == rep.id))
    wallet = await db.scalar(select(RepWallet).where(RepWallet.user_id == rep.id))
    reviewer = await db.get(AdminUser, p.reviewed_by) if p.reviewed_by else None
    history = (await db.scalars(
        select(PayoutRequest).where(PayoutRequest.rep_user_id == rep.id, PayoutRequest.id != p.id)
        .order_by(PayoutRequest.created_at.desc()).limit(5)
    )).all()
    pending_change = await bank_changes.pending_change(db, rep.id)
    return AdminPayoutOut(
        id=p.id, status=p.status, rep_name=rep.full_name, rep_email=rep.email,
        class_name=klass.name if klass else "", amount_kobo=p.amount_kobo,
        platform_fee_kobo=p.platform_fee_kobo, transfer_fee_kobo=p.transfer_fee_kobo,
        transfer_fee_actual_kobo=p.transfer_fee_actual_kobo, net_kobo=p.net_kobo,
        bank_label=p.bank_label, transfer_reference=p.transfer_reference,
        created_at=p.created_at, reviewed_at=p.reviewed_at,
        reviewed_by_email=reviewer.email if reviewer else None, paid_at=p.paid_at,
        reason=p.reject_reason or p.failure_reason,
        rep_pending_kobo=wallet.pending_kobo if wallet else 0,
        rep_available_kobo=wallet.available_kobo if wallet else 0,
        rep_held_kobo=wallet.held_kobo if wallet else 0,
        history=[payout_out(h) for h in history],
        bank_change_pending=pending_change is not None,
    )


@router.get("/payouts", response_model=AdminPayoutPage)
async def list_payouts(
    status: Literal["requested", "processing", "paid", "rejected", "failed", "reversed"] = "requested",
    _: AdminUser = Depends(money_staff), db: AsyncSession = Depends(get_session),
) -> AdminPayoutPage:
    filters = [PayoutRequest.status.in_(("failed", "reversed"))] if status in ("failed", "reversed") \
        else [PayoutRequest.status == status]
    total = await db.scalar(select(func.count()).select_from(PayoutRequest).where(*filters))
    order = PayoutRequest.created_at.asc() if status == "requested" else PayoutRequest.created_at.desc()
    rows = (await db.scalars(select(PayoutRequest).where(*filters).order_by(order).limit(100))).all()
    return AdminPayoutPage(items=[await _payout_out(db, p) for p in rows], total=total or 0)


@router.get("/paystack/balance", response_model=BalanceOut)
async def paystack_balance(
    _: AdminUser = Depends(money_staff), ps: paystack.PaystackClient = Depends(get_paystack)
) -> BalanceOut:
    return BalanceOut(balance_kobo=await ps.get_balance())


@router.post("/payouts/{payout_id}/approve", response_model=AdminPayoutOut)
async def approve_payout(
    payout_id: uuid.UUID, request: Request,
    actor: AdminUser = Depends(money_staff), db: AsyncSession = Depends(get_session),
    ps: paystack.PaystackClient = Depends(get_paystack),
) -> AdminPayoutOut:
    """Starts the Paystack transfer. The payout is Processing until Paystack confirms it."""
    payout = await payouts.approve(db, payout_id, actor, request, ps)
    return await _payout_out(db, payout)


@router.post("/payouts/{payout_id}/reject", response_model=AdminPayoutOut)
async def reject_payout(
    payout_id: uuid.UUID, body: ReasonIn, request: Request,
    actor: AdminUser = Depends(money_staff), db: AsyncSession = Depends(get_session),
) -> AdminPayoutOut:
    payout = await payouts.reject(db, payout_id, actor, body.reason, request)
    await db.commit()
    return await _payout_out(db, payout)


async def _change_out(db: AsyncSession, c: BankAccountChange) -> AdminBankChangeOut:
    rep = await db.get(User, c.user_id)
    klass = await db.scalar(select(Class).where(Class.rep_user_id == rep.id))
    current = await db.scalar(select(RepBankAccount).where(RepBankAccount.user_id == rep.id))
    reviewer = await db.get(AdminUser, c.reviewed_by) if c.reviewed_by else None
    return AdminBankChangeOut(
        **change_out(c).model_dump(), rep_name=rep.full_name, rep_email=rep.email,
        class_name=klass.name if klass else "",
        current_account=(f"{current.bank_name} · {current.account_name} · ••••{crypto.decrypt(current.account_number)[-4:]}"
                         if current else None),
        reviewed_by_email=reviewer.email if reviewer else None,
    )


@router.get("/bank-changes", response_model=list[AdminBankChangeOut])
async def list_changes(
    status: Literal["pending", "approved", "rejected"] = "pending",
    _: AdminUser = Depends(money_staff), db: AsyncSession = Depends(get_session),
) -> list[AdminBankChangeOut]:
    rows = (await db.scalars(
        select(BankAccountChange).where(BankAccountChange.status == status)
        .order_by(BankAccountChange.created_at).limit(100)
    )).all()
    return [await _change_out(db, c) for c in rows]


@router.post("/bank-changes/{change_id}/approve", response_model=AdminBankChangeOut)
async def approve_change(
    change_id: uuid.UUID, request: Request,
    actor: AdminUser = Depends(money_staff), db: AsyncSession = Depends(get_session),
    ps: paystack.PaystackClient = Depends(get_paystack),
) -> AdminBankChangeOut:
    change = await bank_changes.approve(db, change_id, actor, request, ps)
    await db.commit()
    return await _change_out(db, change)


@router.post("/bank-changes/{change_id}/reject", response_model=AdminBankChangeOut)
async def reject_change(
    change_id: uuid.UUID, body: ReasonIn, request: Request,
    actor: AdminUser = Depends(money_staff), db: AsyncSession = Depends(get_session),
) -> AdminBankChangeOut:
    change = await bank_changes.reject(db, change_id, actor, body.reason, request)
    await db.commit()
    return await _change_out(db, change)
