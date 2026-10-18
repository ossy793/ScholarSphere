"""The ledger (design section 5): every money movement as balanced, append-only entries.

Accounts:
    paystack_clearing        asset      money Paystack holds for Pritis
    platform_revenue         revenue    service charges, after Paystack's actual fee
    payout_fee_revenue       revenue    payout fees (phase 8)
    rep_pending:<user>       liability  owed to a rep, not yet settled by Paystack
    rep_available:<user>     liability  owed to a rep, can be paid out
    payout_hold:<user>       liability  requested payouts on hold (phase 8)
    refund_hold:<user>       liability  the rep's share of refunds being sent (phase 10)
    refund_fee_expense       expense    Paystack fees Pritis absorbs on refunds (phase 10)
"""

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LedgerAccount, LedgerEntry, RepWallet

AccountType = Literal["asset", "liability", "revenue", "expense"]

SYSTEM_ACCOUNTS: dict[str, tuple[str, AccountType]] = {
    "paystack_clearing": ("Paystack clearing", "asset"),
    "platform_revenue": ("Platform revenue (service charges)", "revenue"),
    "payout_fee_revenue": ("Payout fee revenue", "revenue"),
    "refund_fee_expense": ("Paystack fees on refunds (borne by Pritis)", "expense"),
}
REP_ACCOUNTS: dict[str, tuple[str, AccountType]] = {
    "rep_pending": ("Rep pending balance", "liability"),
    "rep_available": ("Rep available balance", "liability"),
    "payout_hold": ("Payout hold", "liability"),
    "refund_hold": ("Refund hold", "liability"),
}


class LedgerImbalance(Exception):
    """Debits and credits of one transaction don't match. Nothing is written."""


@dataclass(frozen=True)
class Line:
    account_code: str
    direction: Literal["debit", "credit"]
    amount_kobo: int


def rep_account(kind: str, user_id: uuid.UUID) -> str:
    return f"{kind}:{user_id}"


async def _account_id(db: AsyncSession, code: str) -> uuid.UUID:
    """Gets the account, creating system and rep accounts on first use."""
    if code in SYSTEM_ACCOUNTS:
        name, type_ = SYSTEM_ACCOUNTS[code]
        owner = None
    else:
        kind, _, user = code.partition(":")
        if kind not in REP_ACCOUNTS or not user:
            raise ValueError(f"Unknown ledger account {code!r}")
        name, type_ = REP_ACCOUNTS[kind]
        owner = uuid.UUID(user)
    await db.execute(
        insert(LedgerAccount)
        .values(code=code, name=name, type=type_, owner_user_id=owner)
        .on_conflict_do_nothing(index_elements=["code"])
    )
    return await db.scalar(select(LedgerAccount.id).where(LedgerAccount.code == code))


async def post(
    db: AsyncSession, lines: list[Line], *, ref_type: str, ref_id: uuid.UUID, memo: str | None = None
) -> uuid.UUID:
    """Writes one balanced transaction in the caller's database transaction. Zero-amount
    lines are dropped; negative amounts flip direction."""
    cleaned: list[Line] = []
    for line in lines:
        if line.amount_kobo == 0:
            continue
        if line.amount_kobo < 0:
            flipped = "credit" if line.direction == "debit" else "debit"
            line = Line(line.account_code, flipped, -line.amount_kobo)
        cleaned.append(line)
    debits = sum(l.amount_kobo for l in cleaned if l.direction == "debit")
    credits = sum(l.amount_kobo for l in cleaned if l.direction == "credit")
    if debits != credits or debits == 0:
        raise LedgerImbalance(f"debits {debits} != credits {credits} for {ref_type} {ref_id}")

    txn_id = uuid.uuid4()
    for line in cleaned:
        db.add(LedgerEntry(
            txn_id=txn_id, account_id=await _account_id(db, line.account_code),
            direction=line.direction, amount_kobo=line.amount_kobo,
            ref_type=ref_type, ref_id=ref_id, memo=memo,
        ))
    await db.flush()
    return txn_id


async def locked_wallet(db: AsyncSession, user_id: uuid.UUID) -> RepWallet:
    """The rep's cached balances, created if missing, locked for this transaction."""
    await db.execute(
        insert(RepWallet).values(user_id=user_id).on_conflict_do_nothing(index_elements=["user_id"])
    )
    return await db.scalar(select(RepWallet).where(RepWallet.user_id == user_id).with_for_update())


async def balance(db: AsyncSession, code: str) -> int:
    """An account's balance from the ledger, positive in its normal direction
    (debit for assets and expenses, credit for liabilities and revenue)."""
    account = await db.scalar(select(LedgerAccount).where(LedgerAccount.code == code))
    if account is None:
        return 0
    rows = await db.execute(
        select(LedgerEntry.direction, func.coalesce(func.sum(LedgerEntry.amount_kobo), 0))
        .where(LedgerEntry.account_id == account.id).group_by(LedgerEntry.direction)
    )
    totals = {direction: amount for direction, amount in rows}
    debit, credit = totals.get("debit", 0), totals.get("credit", 0)
    return debit - credit if account.type in ("asset", "expense") else credit - debit


async def unbalanced_transactions(db: AsyncSession) -> list[uuid.UUID]:
    """Every txn_id whose debits don't equal its credits. Should always be empty."""
    rows = await db.execute(text(
        """
        SELECT txn_id FROM ledger_entries
        GROUP BY txn_id
        HAVING SUM(CASE WHEN direction = 'debit' THEN amount_kobo ELSE -amount_kobo END) <> 0
        """
    ))
    return [r[0] for r in rows]
