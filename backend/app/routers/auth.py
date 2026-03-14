from fastapi import APIRouter, Depends, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.user import User
from ..schemas.auth import (
    RegisterRequest, LoginRequest, TokenResponse, UserResponse,
    ForgotPasswordRequest, VerifyResetCodeRequest, ResetPasswordRequest,
)
from ..services import auth_service
from ..utils.security import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])
limiter = Limiter(key_func=get_remote_address)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def register(request: Request, payload: RegisterRequest, db: Session = Depends(get_db)):
    return auth_service.register_user(payload, db)


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
def login(request: Request, payload: LoginRequest, db: Session = Depends(get_db)):
    return auth_service.login_user(payload, db)


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/forgot-password")
@limiter.limit("5/minute")
def forgot_password(request: Request, payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    return auth_service.request_password_reset(payload.email, db)


@router.post("/verify-reset-code")
@limiter.limit("10/minute")
def verify_reset_code(request: Request, payload: VerifyResetCodeRequest, db: Session = Depends(get_db)):
    return auth_service.verify_reset_code(payload.email, payload.code, db)


@router.post("/reset-password")
@limiter.limit("5/minute")
def reset_password(request: Request, payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    return auth_service.reset_password(payload.email, payload.code, payload.new_password, db)
