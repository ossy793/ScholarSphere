import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class QuoteOut(BaseModel):
    """What the student sees before paying: only the total. (Owner's decision, 2026-10-17:
    no due / service charge breakdown for students. The split stays internal.)"""

    total_kobo: int
    require_matric: bool


class PayIn(BaseModel):
    due_id: uuid.UUID
    matric_no: str | None = Field(default=None, max_length=30)


class PayOut(BaseModel):
    payment_id: uuid.UUID
    authorization_url: str


class EmailedOut(BaseModel):
    email: str


class PaymentOut(BaseModel):
    id: uuid.UUID
    reference: str
    status: Literal["pending", "success", "failed", "abandoned", "refunded"]
    due_id: uuid.UUID
    due_title: str
    class_name: str
    total_kobo: int  # students see only the total
    channel: str | None
    paid_at: datetime | None
    receipt_no: str | None
    created_at: datetime
