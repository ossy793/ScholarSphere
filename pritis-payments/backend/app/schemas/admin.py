import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, EmailStr, Field, StringConstraints

from app.schemas.auth import Code, Name, Password

Role = Literal["super_admin", "accountant", "support"]


class AdminOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: Role
    status: str
    last_login_at: datetime | None
    created_at: datetime


class AdminLoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class AdminLoginOut(BaseModel):
    next: Literal["totp"] = "totp"


class TotpIn(BaseModel):
    code: Code


class InviteTokenIn(BaseModel):
    token: Annotated[str, StringConstraints(min_length=20, max_length=200)]


class InviteSetupOut(BaseModel):
    email: str
    full_name: str
    role: Role
    secret: str
    otpauth_uri: str
    qr_svg: str


class AcceptInviteIn(InviteTokenIn):
    password: Password
    code: Code


class StaffCreateIn(BaseModel):
    email: EmailStr
    full_name: Name
    role: Role


class StaffInviteOut(BaseModel):
    admin: AdminOut
    # Shown to the super admin too, so the link can be shared directly while email is not set up.
    invite_url: str


class AuditLogOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    actor_type: str
    actor_email: str | None
    action: str
    entity: str
    entity_id: uuid.UUID | None
    before: dict | None
    after: dict | None
    ip: str | None


class AuditLogPage(BaseModel):
    items: list[AuditLogOut]
    total: int
    page: int
    page_size: int
