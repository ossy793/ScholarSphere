import re
import uuid
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, EmailStr, Field, StringConstraints

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]
Code = Annotated[str, StringConstraints(pattern=r"^\d{6}$")]
Level = Literal["100", "200", "300", "400", "500"]

# Must match frontend/shared/password-rules.js.
PASSWORD_RULES = (
    (r"[A-Z]", "an uppercase letter"),
    (r"[a-z]", "a lowercase letter"),
    (r"\d", "a number"),
    (r"[^A-Za-z0-9\s]", "a symbol such as ! @ # $ or -"),
)


def check_password_strength(value: str) -> str:
    missing = [label for pattern, label in PASSWORD_RULES if not re.search(pattern, value)]
    if missing:
        listed = missing[0] if len(missing) == 1 else f"{', '.join(missing[:-1])} and {missing[-1]}"
        raise ValueError(f"Password must include {listed}")
    return value


# For new passwords only (signup, reset). Login accepts whatever was set before.
Password = Annotated[str, Field(min_length=8, max_length=128), AfterValidator(check_password_strength)]


def normalize_ng_phone(value: str) -> str:
    """Accepts 0803..., 803..., 234803... or +234803... and returns +234803...

    Nigerian mobile numbers are 10 digits after the country code and start with
    70, 71, 80, 81, 90 or 91.
    """
    digits = re.sub(r"[\s\-().]", "", value)
    for prefix in ("+234", "234", "0"):
        if digits.startswith(prefix):
            digits = digits[len(prefix):]
            break
    if not re.fullmatch(r"[789][01]\d{8}", digits):
        raise ValueError("Enter a valid Nigerian mobile number, for example 0803 123 4567")
    return f"+234{digits}"


NgPhone = Annotated[str, StringConstraints(max_length=20), AfterValidator(normalize_ng_phone)]


class ProfileFields(BaseModel):
    phone: NgPhone
    university_id: uuid.UUID
    level: Level


class SignupIn(ProfileFields):
    full_name: Name
    email: EmailStr
    password: Password
    accept_terms: bool = False  # must be true: the "I agree to the Terms" box


class ProfileIn(ProfileFields):
    full_name: Name | None = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class VerifyEmailIn(BaseModel):
    code: Code


class ForgotPasswordIn(BaseModel):
    email: EmailStr


class ResetPasswordIn(BaseModel):
    email: EmailStr
    code: Code
    new_password: Password


class CsrfOut(BaseModel):
    csrf_token: str


class OkOut(BaseModel):
    ok: bool = True
