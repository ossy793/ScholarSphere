import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.schemas.auth import Level, Name, NgPhone

Text200 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]
AccountNumber = Annotated[str, StringConstraints(pattern=r"^\d{10}$")]
BankCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]


class RepApplicationForm(BaseModel):
    """The rep application form (sent as multipart, with the proof file alongside)."""

    full_name: Name
    matric_no: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=30,
                               pattern=r"^[A-Za-z0-9/\-. ]+$")
    ]
    phone: NgPhone
    university_id: uuid.UUID
    department: Text200
    level: Level
    class_name: Text200
    session: Annotated[str, StringConstraints(pattern=r"^\d{4}/\d{4}$")]
    bank_code: BankCode
    account_number: AccountNumber

    @field_validator("session")
    @classmethod
    def _consecutive_years(cls, value: str) -> str:
        start, end = (int(y) for y in value.split("/"))
        if end != start + 1 or not 2000 <= start <= 2100:
            raise ValueError("Use an academic session like 2025/2026")
        return value


class ResolveAccountIn(BaseModel):
    bank_code: BankCode
    account_number: AccountNumber


class ResolveAccountOut(BaseModel):
    account_name: str
    bank_name: str


class BankOut(BaseModel):
    code: str
    name: str


class ClassOut(BaseModel):
    id: uuid.UUID
    name: str
    school: str
    department: str
    level: str
    session: str


class BankAccountOut(BaseModel):
    bank_name: str
    account_name: str
    last4: str
    verified: bool


class RepApplicationOut(BaseModel):
    """What the applicant sees about their own latest application."""

    id: uuid.UUID
    status: Literal["pending", "approved", "rejected"]
    created_at: datetime
    reviewed_at: datetime | None
    reject_reason: str | None
    matric_no: str | None
    university_id: uuid.UUID | None
    klass: ClassOut = Field(serialization_alias="class")
    bank_account: BankAccountOut | None

    model_config = {"populate_by_name": True}


class RejectIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=500)]


class ApplicantOut(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str
    phone: str | None
    matric_no: str | None
    email_verified: bool


class AdminRepApplicationOut(BaseModel):
    id: uuid.UUID
    status: Literal["pending", "approved", "rejected"]
    created_at: datetime
    applicant: ApplicantOut
    klass: ClassOut = Field(serialization_alias="class")
    bank_account: BankAccountOut | None
    proof_url: str
    proof_content_type: str
    reviewed_by_email: str | None
    reviewed_at: datetime | None
    reject_reason: str | None

    model_config = {"populate_by_name": True}


class AdminRepApplicationPage(BaseModel):
    items: list[AdminRepApplicationOut]
    total: int
    page: int
    page_size: int


class RepClassOut(BaseModel):
    """The rep's own class, for the rep dashboard."""

    klass: ClassOut = Field(serialization_alias="class")
    invite_code: str
    member_count: int
    bank_account: BankAccountOut | None

    model_config = {"populate_by_name": True}


class MemberOut(BaseModel):
    user_id: uuid.UUID
    full_name: str
    matric_no: str | None
    joined_at: datetime
