"""Route dependencies for the current user (user site) and staff member (admin site)."""

import uuid
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cookies import ADMIN_COOKIES, USER_COOKIES
from app.core.db import get_session
from app.core.errors import AppError
from app.core.security import decode_access_token
from app.models import AdminUser, AuthSession, Class, User
from app.services.users import profile_complete


async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_session)
) -> User:
    token = request.cookies.get(USER_COOKIES.access)
    data = decode_access_token(token, "user") if token else None
    if data is None:
        raise AppError("not_authenticated", "Please log in", 401)

    session = await db.get(AuthSession, uuid.UUID(data["sid"]))
    if session is None or session.revoked_at is not None or session.subject_type != "user":
        raise AppError("not_authenticated", "Please log in", 401)

    user = await db.get(User, uuid.UUID(data["sub"]))
    if user is None or user.status != "active":
        raise AppError("not_authenticated", "Please log in", 401)
    return user


async def get_current_admin(
    request: Request, db: AsyncSession = Depends(get_session)
) -> AdminUser:
    token = request.cookies.get(ADMIN_COOKIES.access)
    data = decode_access_token(token, "admin") if token else None
    if data is None:
        raise AppError("not_authenticated", "Please log in", 401)

    session = await db.get(AuthSession, uuid.UUID(data["sid"]))
    if session is None or session.revoked_at is not None or session.subject_type != "admin":
        raise AppError("not_authenticated", "Please log in", 401)

    admin = await db.get(AdminUser, uuid.UUID(data["sub"]))
    if admin is None or admin.status != "active":
        raise AppError("not_authenticated", "Please log in", 401)
    return admin


def require_roles(*roles: str):
    """Route dependency: only these staff roles may call the route (design section 2)."""

    async def dependency(admin: AdminUser = Depends(get_current_admin)) -> AdminUser:
        if admin.role not in roles:
            raise AppError("forbidden", "Your role does not allow this", 403)
        return admin

    return dependency


SUPER_ADMIN = "super_admin"
ACCOUNTANT = "accountant"
SUPPORT = "support"


async def get_verified_user(user: User = Depends(get_current_user)) -> User:
    """For paying dues and applying as a rep: needs a verified email and a complete profile."""
    if user.email_verified_at is None:
        raise AppError("email_not_verified", "Verify your email address first", 403)
    if not profile_complete(user):
        raise AppError(
            "profile_incomplete", "Add your phone number, university and level first", 403
        )
    return user


@dataclass(frozen=True)
class RepContext:
    user: User
    klass: Class  # the one class this rep owns; every rep route is scoped to it


async def get_current_rep(
    user: User = Depends(get_verified_user), db: AsyncSession = Depends(get_session)
) -> RepContext:
    """Every rep route depends on this: a verified rep, and only their own class."""
    from app.services.reps import verified_rep_class

    klass = await verified_rep_class(db, user.id)
    if klass is None:
        raise AppError("not_a_rep", "Only verified class reps can do this", 403)
    return RepContext(user=user, klass=klass)


def require_own_class(class_id: uuid.UUID, rep: RepContext) -> Class:
    """Ownership check for routes that take a class id. Someone else's class looks like
    it doesn't exist (404), so ids can't be probed."""
    if class_id != rep.klass.id:
        raise AppError("not_found", "Class not found", 404)
    return rep.klass
