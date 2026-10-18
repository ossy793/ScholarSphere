"""A verified rep changing their payout bank account (owner's decision, 2026-10-17).

The new account is looked up with Paystack, then waits for the accountant or super admin.
Payouts pause meanwhile (no new requests, no approvals). On approval a new Paystack
transfer recipient is created and becomes the only payout destination.
"""

import uuid

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.deps import RepContext
from app.core.errors import AppError
from app.models import AdminUser, BankAccountChange, PayoutRequest, RepBankAccount, User
from app.services import audit
from app.services.email import queue_email
from app.services.paystack import PaystackClient


async def pending_change(db: AsyncSession, user_id: uuid.UUID) -> BankAccountChange | None:
    return await db.scalar(select(BankAccountChange).where(
        BankAccountChange.user_id == user_id, BankAccountChange.status == "pending"
    ))


async def request_change(
    db: AsyncSession, rep: RepContext, bank_code: str, account_number: str, paystack: PaystackClient
) -> BankAccountChange:
    if await pending_change(db, rep.user.id):
        raise AppError("change_pending", "You already have a bank account change waiting for review.", 409)
    if await db.scalar(select(PayoutRequest.id).where(
        PayoutRequest.rep_user_id == rep.user.id, PayoutRequest.status.in_(("requested", "processing"))
    )):
        raise AppError("payout_open", "Wait for your current payout to finish before changing your bank account.", 409)
    current = await db.scalar(select(RepBankAccount).where(RepBankAccount.user_id == rep.user.id))
    if current and current.bank_code == bank_code and crypto.decrypt(current.account_number) == account_number:
        raise AppError("same_account", "That's already your payout account.", 409)

    bank_name = await paystack.bank_name(bank_code)
    account_name = await paystack.resolve_account(account_number, bank_code)  # never trust the browser
    change = BankAccountChange(
        user_id=rep.user.id, bank_code=bank_code, bank_name=bank_name,
        account_number=crypto.encrypt(account_number), account_name=account_name, status="pending",
    )
    db.add(change)
    try:
        await db.flush()
    except IntegrityError:
        raise AppError("change_pending", "You already have a bank account change waiting for review.", 409) from None

    staff = (await db.scalars(select(AdminUser.email).where(
        AdminUser.status == "active", AdminUser.role.in_(("accountant", "super_admin"))
    ))).all()
    for email in staff:
        await queue_email(db, email, "bank_change_requested", {
            "rep_name": rep.user.full_name, "class_name": rep.klass.name,
            "new_account": f"{bank_name} · {account_name} · ••••{account_number[-4:]}",
            "link": f"{get_settings().admin_base_url}/payouts.html?tab=bank",
        })
    return change


async def _locked(db: AsyncSession, change_id: uuid.UUID) -> BankAccountChange:
    change = await db.get(BankAccountChange, change_id, with_for_update=True)
    if change is None:
        raise AppError("not_found", "Bank account change not found", 404)
    if change.status != "pending":
        raise AppError("already_decided", "This change has already been decided", 409)
    return change


async def _notify(db: AsyncSession, change: BankAccountChange, approved: bool) -> None:
    rep = await db.get(User, change.user_id)
    await queue_email(db, rep.email, "bank_change_decided", {
        "full_name": rep.full_name, "approved": approved,
        "account": f"{change.bank_name} · {change.account_name} · ••••{crypto.decrypt(change.account_number)[-4:]}",
        "reason": change.reject_reason or "", "link": f"{get_settings().app_base_url}/rep.html",
    })


async def approve(
    db: AsyncSession, change_id: uuid.UUID, actor: AdminUser, request: Request, paystack: PaystackClient
) -> BankAccountChange:
    change = await _locked(db, change_id)
    number = crypto.decrypt(change.account_number)
    recipient = await paystack.create_transfer_recipient(
        account_name=change.account_name, account_number=number, bank_code=change.bank_code
    )
    account = await db.scalar(
        select(RepBankAccount).where(RepBankAccount.user_id == change.user_id).with_for_update()
    )
    before = {"bank": account.bank_name, "account_name": account.account_name,
              "last4": crypto.decrypt(account.account_number)[-4:]} if account else None
    if account is None:
        account = RepBankAccount(user_id=change.user_id)
        db.add(account)
    now = utcnow()
    account.bank_code = change.bank_code
    account.bank_name = change.bank_name
    account.account_number = change.account_number
    account.account_name = change.account_name
    account.paystack_recipient_code = recipient
    account.verified_by = actor.id
    account.verified_at = now
    change.status = "approved"
    change.reviewed_by = actor.id
    change.reviewed_at = now
    await _notify(db, change, True)
    await audit.record(db, actor=actor, action="bank_change.approve", entity="bank_account_change",
                       entity_id=change.id, before=before,
                       after={"bank": change.bank_name, "account_name": change.account_name,
                              "last4": number[-4:], "recipient_code": recipient}, request=request)
    return change


async def reject(
    db: AsyncSession, change_id: uuid.UUID, actor: AdminUser, reason: str, request: Request
) -> BankAccountChange:
    change = await _locked(db, change_id)
    change.status = "rejected"
    change.reviewed_by = actor.id
    change.reviewed_at = utcnow()
    change.reject_reason = reason
    await _notify(db, change, False)
    await audit.record(db, actor=actor, action="bank_change.reject", entity="bank_account_change",
                       entity_id=change.id, after={"reason": reason}, request=request)
    return change
