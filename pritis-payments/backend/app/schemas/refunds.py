import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints

RefundStatus = Literal["requested", "rejected", "processing", "processed", "failed"]
MistakeBy = Literal["pritis", "rep", "student"]


class RefundIn(BaseModel):
    payment_id: uuid.UUID
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=500)]


class RefundApproveIn(BaseModel):
    mistake_by: MistakeBy  # decides who bears Paystack's fee


class AdminRefundOut(BaseModel):
    id: uuid.UUID
    status: RefundStatus
    payment_id: uuid.UUID
    payment_reference: str
    receipt_no: str | None
    student_name: str
    student_email: str
    rep_name: str
    class_name: str
    due_title: str
    paid_at: datetime | None
    settled: bool  # whether the payment had reached the rep's Available balance
    total_kobo: int  # what the student paid
    paystack_fee_kobo: int  # what Paystack kept and won't return
    full_refund_kobo: int  # if Pritis or the rep made the mistake
    student_mistake_refund_kobo: int  # if the student did (less Paystack's fee)
    amount_kobo: int
    fee_borne_kobo: int
    mistake_by: MistakeBy | None
    reason: str
    rep_available_kobo: int
    reserved_kobo: int
    requested_by_email: str
    reviewed_by_email: str | None
    created_at: datetime
    reviewed_at: datetime | None
    processed_at: datetime | None
    paystack_status: str | None
    problem: str | None  # why it was rejected or failed


class AdminRefundPage(BaseModel):
    items: list[AdminRefundOut]
    total: int
