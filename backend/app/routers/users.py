import re
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.user import User
from ..schemas.auth import UserResponse
from ..utils.security import get_current_user

router = APIRouter(prefix="/users", tags=["users"])

_REQUIRED_PROFILE_FIELDS = ("full_name", "username", "school", "level", "course")
_MAX_AVATAR_BYTES = 512 * 1024  # 512 KB base64 limit (~384 KB raw image)


def _profile_complete(user: User) -> bool:
    return all(
        getattr(user, f) and str(getattr(user, f)).strip()
        for f in _REQUIRED_PROFILE_FIELDS
    )


class ProfileUpdateRequest(BaseModel):
    full_name:           Optional[str] = None
    username:            Optional[str] = None
    school:              Optional[str] = None
    level:               Optional[str] = None
    course:              Optional[str] = None
    profile_picture_url: Optional[str] = None   # base64 data-URL or empty string to clear

    @field_validator("full_name")
    @classmethod
    def v_full_name(cls, v):
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("Full name cannot be empty.")
        if len(v) > 100:
            raise ValueError("Full name must be 100 characters or fewer.")
        return v

    @field_validator("username")
    @classmethod
    def v_username(cls, v):
        if v is None:
            return v
        v = v.strip().lower()
        if not v:
            raise ValueError("Username cannot be empty.")
        if len(v) < 3:
            raise ValueError("Username must be at least 3 characters.")
        if len(v) > 30:
            raise ValueError("Username must be 30 characters or fewer.")
        if not re.match(r'^[a-z0-9_]+$', v):
            raise ValueError("Username may only contain lowercase letters, numbers, and underscores.")
        return v

    @field_validator("school")
    @classmethod
    def v_school(cls, v):
        if v is None:
            return v
        v = v.strip()
        if len(v) > 200:
            raise ValueError("School name is too long.")
        return v

    @field_validator("level")
    @classmethod
    def v_level(cls, v):
        if v is None:
            return v
        return v.strip()

    @field_validator("course")
    @classmethod
    def v_course(cls, v):
        if v is None:
            return v
        v = v.strip()
        if len(v) > 150:
            raise ValueError("Course name must be 150 characters or fewer.")
        return v

    @field_validator("profile_picture_url")
    @classmethod
    def v_avatar(cls, v):
        if v is None or v == "":
            return v
        # Must be a data URL or http/https URL
        if not (v.startswith("data:image/") or v.startswith("http://") or v.startswith("https://")):
            raise ValueError("Invalid profile picture format.")
        if len(v.encode()) > _MAX_AVATAR_BYTES:
            raise ValueError("Profile picture is too large. Please use an image under 384 KB.")
        return v


@router.get("/me", response_model=UserResponse)
def get_my_profile(current_user: User = Depends(get_current_user)):
    """Return the current user's full profile."""
    return current_user


@router.patch("/me", response_model=UserResponse)
def update_my_profile(
    payload: ProfileUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update profile fields. Sets profile_completed when all required fields are present."""

    # Username uniqueness check
    if payload.username and payload.username != current_user.username:
        conflict = db.query(User).filter(
            User.username == payload.username,
            User.id != current_user.id,
        ).first()
        if conflict:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That username is already taken. Please choose another.",
            )

    updatable = ("full_name", "username", "school", "level", "course", "profile_picture_url")
    for field in updatable:
        val = getattr(payload, field)
        if val is not None:
            setattr(current_user, field, val if val != "" else None)

    # Recompute profile_completed
    current_user.profile_completed = _profile_complete(current_user)

    db.commit()
    db.refresh(current_user)
    return current_user
