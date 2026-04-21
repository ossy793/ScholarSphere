import re
from typing import Optional
from pydantic import BaseModel, EmailStr, field_validator
from uuid import UUID
from datetime import datetime


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    username:   Optional[str] = None
    department: Optional[str] = None
    university: Optional[str] = None
    level:      Optional[str] = None

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Full name cannot be empty")
        if len(v) > 100:
            raise ValueError("Full name must be 100 characters or fewer")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if len(v) > 128:
            raise ValueError("Password must be 128 characters or fewer")
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"[a-z]", v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r"[0-9!@#$%^&*()\-_=+\[\]{};:'\",.<>?/\\|`~]", v):
            raise ValueError("Password must contain at least one number or special character")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class VerifyResetCodeRequest(BaseModel):
    email: EmailStr
    code: str


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if len(v) > 128:
            raise ValueError("Password must be 128 characters or fewer")
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"[a-z]", v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r"[0-9!@#$%^&*()\-_=+\[\]{};:'\",.<>?/\\|`~]", v):
            raise ValueError("Password must contain at least one number or special character")
        return v


class GoogleAuthRequest(BaseModel):
    credential: str


class TwoFAVerifyRequest(BaseModel):
    pre_auth_token: str
    totp_code: str


class TwoFAEnableRequest(BaseModel):
    totp_code: str
    secret: str


class TwoFADisableRequest(BaseModel):
    totp_code: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: UUID
    email: str
    full_name: str
    username:            Optional[str] = None
    school:              Optional[str] = None
    level:               Optional[str] = None
    course:              Optional[str] = None
    profile_picture_url: Optional[str] = None
    profile_completed:   bool = False
    subscription_plan:   str  = "free"
    subscription_expiry: Optional[datetime] = None
    is_active:   bool
    is_admin:    bool
    totp_enabled: bool = False
    created_at: datetime

    class Config:
        from_attributes = True
