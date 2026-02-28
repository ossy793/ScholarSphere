from pydantic import BaseModel, EmailStr


class PromoRequestSchema(BaseModel):
    email: EmailStr


class PromoVerifySchema(BaseModel):
    email: EmailStr
    code: str
