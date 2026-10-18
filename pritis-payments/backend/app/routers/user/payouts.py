"""The rep's money: per-due collection (with the paid list as PDF/CSV), wallet, payout
requests and bank account changes. Every route goes through get_current_rep (own class
only) and dues through get_own_due."""

import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.db import get_session
from app.core.deps import RepContext, get_current_rep
from app.models import BankAccountChange, Class, PayoutRequest, RepWallet
from app.schemas.payouts import (
    BankChangeIn,
    BankChangeOut,
    DueCollectionOut,
    PayoutIn,
    PayoutOut,
    PayoutQuoteOut,
    StudentRowOut,
    WalletOut,
)
from app.services import bank_changes, collected, dues, payouts, paystack

router = APIRouter(prefix="/rep", tags=["rep money"])


def get_paystack() -> paystack.PaystackClient:
    return paystack.get_client()


def payout_out(p: PayoutRequest) -> PayoutOut:
    return PayoutOut(
        id=p.id, status=p.status, amount_kobo=p.amount_kobo,
        service_charge_kobo=p.platform_fee_kobo + p.transfer_fee_kobo, net_kobo=p.net_kobo,
        bank_label=p.bank_label, created_at=p.created_at, reviewed_at=p.reviewed_at, paid_at=p.paid_at,
        reason=p.reject_reason or p.failure_reason,
    )


def change_out(c: BankAccountChange) -> BankChangeOut:
    return BankChangeOut(
        id=c.id, status=c.status, bank_name=c.bank_name, account_name=c.account_name,
        last4=crypto.decrypt(c.account_number)[-4:], created_at=c.created_at,
        reviewed_at=c.reviewed_at, reject_reason=c.reject_reason,
    )


# --- Per due ----------------------------------------------------------------------------


def _row(s: collected.Student) -> StudentRowOut:
    return StudentRowOut(user_id=s.user_id, name=s.name, matric_no=s.matric_no, amount_kobo=s.amount_kobo,
                         paid_at=s.paid_at, receipt_no=s.receipt_no)


@router.get("/dues/{due_id}/payments", response_model=DueCollectionOut)
async def due_payments(
    due_id: uuid.UUID, rep: RepContext = Depends(get_current_rep), db: AsyncSession = Depends(get_session)
) -> DueCollectionOut:
    """Who paid, who hasn't, and the totals. The rep's page refreshes this every 30 seconds."""
    due = await dues.get_own_due(db, rep, due_id)
    c = await collected.due_collection(db, due)
    return DueCollectionOut(
        students=c.students, paid_count=c.paid_count, collected_kobo=c.collected_kobo,
        outstanding_kobo=c.outstanding_kobo, paid=[_row(s) for s in c.paid], unpaid=[_row(s) for s in c.unpaid],
    )


def _filename(title: str, ext: str) -> str:
    safe = "".join(ch if ch.isalnum() else "-" for ch in title).strip("-").lower()[:50] or "due"
    return f"paid-list-{safe}.{ext}"


@router.get("/dues/{due_id}/paid-list.csv")
async def paid_list_csv(
    due_id: uuid.UUID, rep: RepContext = Depends(get_current_rep), db: AsyncSession = Depends(get_session)
) -> Response:
    due = await dues.get_own_due(db, rep, due_id)
    c = await collected.due_collection(db, due)
    body = collected.paid_list_csv(due, rep.klass, c)
    return Response(
        content="﻿" + body,  # BOM so Excel opens it as UTF-8
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{_filename(due.title, "csv")}"',
                 "Cache-Control": "private, no-store"},
    )


@router.get("/dues/{due_id}/paid-list.pdf")
async def paid_list_pdf(
    due_id: uuid.UUID, rep: RepContext = Depends(get_current_rep), db: AsyncSession = Depends(get_session)
) -> Response:
    due = await dues.get_own_due(db, rep, due_id)
    c = await collected.due_collection(db, due)
    return Response(
        content=collected.paid_list_pdf(due, rep.klass, c), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{_filename(due.title, "pdf")}"',
                 "Cache-Control": "private, no-store"},
    )


# --- Wallet and payouts ---------------------------------------------------------------------


@router.get("/wallet", response_model=WalletOut)
async def wallet(rep: RepContext = Depends(get_current_rep), db: AsyncSession = Depends(get_session)) -> WalletOut:
    w = await db.scalar(select(RepWallet).where(RepWallet.user_id == rep.user.id))
    history = (await db.scalars(
        select(PayoutRequest).where(PayoutRequest.rep_user_id == rep.user.id)
        .order_by(PayoutRequest.created_at.desc()).limit(50)
    )).all()
    reason = await payouts.blocked_reason(db, rep.user.id)
    available = w.available_kobo if w else 0
    if reason is None and available < 0:
        reason = (f"Refunds took your Available balance below zero ({payouts.naira(-available)} short). "
                  "Your next settled payments cover that first, then you can request a payout.")
    elif reason is None and available == 0:
        reason = "Nothing to pay out yet: money becomes Available once Paystack settles it."
    return WalletOut(
        pending_kobo=w.pending_kobo if w else 0, available_kobo=available, held_kobo=w.held_kobo if w else 0,
        refund_held_kobo=w.refund_held_kobo if w else 0,
        can_request=reason is None, blocked_reason=reason, payouts=[payout_out(p) for p in history],
    )


@router.get("/payouts/quote", response_model=PayoutQuoteOut)
async def payout_quote(
    amount_kobo: int = Query(gt=0, le=1_000_000_000_00),
    _: RepContext = Depends(get_current_rep), db: AsyncSession = Depends(get_session),
) -> PayoutQuoteOut:
    q = await payouts.quote(db, amount_kobo)
    return PayoutQuoteOut(amount_kobo=q.payout_kobo, service_charge_kobo=q.service_charge_kobo, net_kobo=q.net_kobo)


@router.post("/payouts", response_model=PayoutOut, status_code=201)
async def request_payout(
    body: PayoutIn, rep: RepContext = Depends(get_current_rep), db: AsyncSession = Depends(get_session)
) -> PayoutOut:
    payout = await payouts.request_payout(db, rep, body.amount_kobo)
    await db.commit()
    return payout_out(payout)


# --- Changing the payout account ---------------------------------------------------------------


@router.get("/bank-account/change", response_model=BankChangeOut | None)
async def latest_change(
    rep: RepContext = Depends(get_current_rep), db: AsyncSession = Depends(get_session)
) -> BankChangeOut | None:
    change = await db.scalar(
        select(BankAccountChange).where(BankAccountChange.user_id == rep.user.id)
        .order_by(BankAccountChange.created_at.desc()).limit(1)
    )
    return change_out(change) if change else None


@router.post("/bank-account/change", response_model=BankChangeOut, status_code=201)
async def request_change(
    body: BankChangeIn,
    rep: RepContext = Depends(get_current_rep),
    db: AsyncSession = Depends(get_session),
    ps: paystack.PaystackClient = Depends(get_paystack),
) -> BankChangeOut:
    """New payout account: looked up with Paystack now, used once staff approve it.
    Payouts pause until then."""
    change = await bank_changes.request_change(db, rep, body.bank_code, body.account_number, ps)
    await db.commit()
    return change_out(change)
