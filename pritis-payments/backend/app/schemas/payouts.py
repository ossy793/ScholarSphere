import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.schemas.rep import AccountNumber, BankCode

PayoutStatus = Literal["requested", "rejected", "processing", "paid", "failed", "reversed"]


class PayoutQuoteOut(BaseModel):
    """What the rep sees before confirming: the payout, one service charge line, what they get."""

    amount_kobo: int
    service_charge_kobo: int
    net_kobo: int


class PayoutCodeIn(BaseModel):
    amount_kobo: int = Field(gt=0, le=1_000_000_000_00)


class PayoutIn(BaseModel):
    amount_kobo: int = Field(gt=0, le=1_000_000_000_00)
    code: Annotated[str, StringConstraints(pattern=r"^\d{6}$")]  # emailed for this amount


class CodeSentOut(BaseModel):
    email: str


class PayoutOut(BaseModel):
    id: uuid.UUID
    status: PayoutStatus
    amount_kobo: int
    service_charge_kobo: int
    net_kobo: int
    bank_label: str | None
    created_at: datetime
    reviewed_at: datetime | None
    paid_at: datetime | None
    reason: str | None  # why it was rejected or failed


class WalletOut(BaseModel):
    pending_kobo: int  # paid by students, not yet settled by Paystack
    available_kobo: int  # settled; can be paid out
    held_kobo: int  # in a payout that's waiting or being sent
    refund_held_kobo: int  # reserved for refunds Paystack is sending
    can_request: bool
    blocked_reason: str | None
    payouts: list[PayoutOut]


class BankChangeIn(BaseModel):
    bank_code: BankCode
    account_number: AccountNumber


class BankChangeOut(BaseModel):
    id: uuid.UUID
    status: Literal["pending", "approved", "rejected"]
    bank_name: str
    account_name: str
    last4: str
    created_at: datetime
    reviewed_at: datetime | None
    reject_reason: str | None


class ReasonIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=500)]


class AdminPayoutOut(BaseModel):
    id: uuid.UUID
    status: PayoutStatus
    rep_name: str
    rep_email: str
    class_name: str
    amount_kobo: int
    platform_fee_kobo: int
    transfer_fee_kobo: int
    transfer_fee_actual_kobo: int | None
    net_kobo: int
    bank_label: str | None
    transfer_reference: str
    created_at: datetime
    reviewed_at: datetime | None
    reviewed_by_email: str | None
    paid_at: datetime | None
    reason: str | None
    # For the approval screen: the rep's balances now, and their earlier payouts.
    rep_pending_kobo: int
    rep_available_kobo: int
    rep_held_kobo: int
    history: list[PayoutOut]
    bank_change_pending: bool


class AdminPayoutPage(BaseModel):
    items: list[AdminPayoutOut]
    total: int


class AdminBankChangeOut(BankChangeOut):
    rep_name: str
    rep_email: str
    class_name: str
    current_account: str | None
    reviewed_by_email: str | None


class BalanceOut(BaseModel):
    balance_kobo: int


class StudentRowOut(BaseModel):
    user_id: uuid.UUID
    name: str
    matric_no: str | None
    amount_kobo: int | None
    paid_at: datetime | None
    receipt_no: str | None


class DueCollectionOut(BaseModel):
    students: int
    paid_count: int
    collected_kobo: int
    outstanding_kobo: int
    paid: list[StudentRowOut]
    unpaid: list[StudentRowOut]
