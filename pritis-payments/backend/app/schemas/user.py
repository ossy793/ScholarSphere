import uuid
from typing import Literal

from pydantic import BaseModel


class MembershipOut(BaseModel):
    class_id: uuid.UUID
    class_name: str


class UniversityOut(BaseModel):
    id: uuid.UUID
    name: str
    short_name: str | None


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    phone: str | None
    university: UniversityOut | None
    level: str | None
    matric_no: str | None = None
    # False until phone, university and level are all set (Google sign-ups start without them).
    profile_complete: bool
    email_verified: bool
    has_password: bool
    # True once staff approved a rep application.
    is_rep: bool
    rep_status: Literal["none", "pending", "approved", "rejected"]
    memberships: list[MembershipOut]
