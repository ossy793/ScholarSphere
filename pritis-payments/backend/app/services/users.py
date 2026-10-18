import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import Class, ClassMembership, University, User
from app.schemas.user import MembershipOut, UniversityOut, UserOut


def profile_complete(user: User) -> bool:
    return bool(user.phone and user.university_id and user.level)


async def get_active_university(db: AsyncSession, university_id: uuid.UUID) -> University:
    university = await db.get(University, university_id)
    if university is None or not university.is_active:
        raise AppError(
            "validation_error", "Some fields are invalid", 422,
            [{"field": "body.university_id", "message": "Choose your university from the list",
              "type": "university_invalid"}],
        )
    return university


async def build_user_out(db: AsyncSession, user: User) -> UserOut:
    from app.services import reps  # reps uses this module too

    status = await reps.rep_status(db, user.id)
    rows = await db.execute(
        select(Class.id, Class.name)
        .join(ClassMembership, ClassMembership.class_id == Class.id)
        .where(ClassMembership.user_id == user.id)
        .order_by(ClassMembership.joined_at)
    )
    university = await db.get(University, user.university_id) if user.university_id else None
    return UserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        phone=user.phone,
        university=(
            UniversityOut(id=university.id, name=university.name, short_name=university.short_name)
            if university else None
        ),
        level=user.level,
        matric_no=user.matric_no,
        profile_complete=profile_complete(user),
        email_verified=user.email_verified_at is not None,
        has_password=user.password_hash is not None,
        is_rep=status == "approved",
        rep_status=status,
        memberships=[MembershipOut(class_id=cid, class_name=name) for cid, name in rows],
    )
