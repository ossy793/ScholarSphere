import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, Field, StringConstraints

from app.schemas.settings import Percent

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=120)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]

# Where a due is in its life, as the rep sees it (design section 4 diagram).
Phase = Literal["draft", "pending", "rejected", "approved", "live", "edit_pending"]


class DueTerms(BaseModel):
    """What a rep proposes and an admin approves. Money in kobo."""

    title: Title
    description: Description = ""
    amount_kobo: int = Field(ge=10_000, le=1_000_000_000)  # ₦100 to ₦10,000,000
    # Must include a time zone, e.g. 2026-11-30T23:59:00+01:00 (Lagos).
    deadline: AwareDatetime
    require_matric: bool = False


class DueCreateIn(DueTerms):
    submit: bool = True  # False keeps it as a draft


class RevisionOut(BaseModel):
    version: int
    status: Literal["draft", "pending", "approved", "rejected"]
    terms: DueTerms
    submitted_at: datetime | None
    reviewed_at: datetime | None
    reject_reason: str | None


class RepDueOut(BaseModel):
    id: uuid.UUID
    phase: Phase
    is_live: bool
    closed: bool
    share_url: str
    # The terms students see (None until the first approval).
    live_terms: DueTerms | None
    # The draft or pending change, if any.
    open_revision: RevisionOut | None
    # The most recent decision, so a rejected edit's reason is visible.
    latest_revision: RevisionOut
    created_at: datetime
    # Collection so far (design section 10: students, paid, collected, outstanding).
    students: int = 0
    paid_count: int = 0
    collected_kobo: int = 0
    outstanding_kobo: int = 0
    # What each student pays for the live terms (due + service charge), for the rep to announce.
    student_total_kobo: int | None = None


class RepQuoteOut(BaseModel):
    """What students would pay for an amount, shown while the rep types it."""

    amount_kobo: int  # what the rep asked for (the due)
    service_charge_kobo: int  # Paystack's processing charge and Pritis's fee, added on top
    total_kobo: int  # what each student pays


class StudentDueOut(BaseModel):
    id: uuid.UUID
    class_name: str
    title: str
    description: str
    # What the student pays, service charge included. Students see only this total.
    total_kobo: int
    deadline: datetime
    require_matric: bool
    status: Literal["unpaid", "paid", "closed"]
    # The successful payment, for the receipt link (when paid).
    payment_id: uuid.UUID | None = None


class ApproveDueIn(BaseModel):
    # Leave out to use the due's current fee (or the default from settings for a new due).
    platform_fee_percent: Percent | None = None


class RejectDueIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=500)]


class AdminDueRevisionOut(BaseModel):
    due_id: uuid.UUID
    revision_version: int
    kind: Literal["new", "edit"]
    status: Literal["pending", "approved", "rejected"]
    class_name: str
    school: str
    rep_name: str
    rep_email: str
    proposed: DueTerms
    current: DueTerms | None  # live terms, for an edit
    current_fee_percent: Decimal | None
    suggested_fee_percent: Decimal
    needs_super_admin: bool
    submitted_at: datetime | None
    reviewed_at: datetime | None
    reviewed_by_email: str | None
    reject_reason: str | None
    approved_fee_percent: Decimal | None


class AdminDueRevisionPage(BaseModel):
    items: list[AdminDueRevisionOut]
    total: int
    page: int
    page_size: int
    max_due_amount_kobo: int
