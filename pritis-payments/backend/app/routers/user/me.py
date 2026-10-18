from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import get_current_user
from app.models import University, User
from app.schemas.auth import ProfileIn
from app.schemas.user import UniversityOut, UserOut
from app.services.users import build_user_out, get_active_university

router = APIRouter(tags=["user"])


@router.get("/me", response_model=UserOut)
async def me(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
) -> UserOut:
    return await build_user_out(db, user)


@router.put("/me/profile", response_model=UserOut)
async def update_profile(
    body: ProfileIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> UserOut:
    """Phone, university and level (and optionally name). Google sign-ups use this to finish signing up."""
    await get_active_university(db, body.university_id)
    user.phone = body.phone
    user.university_id = body.university_id
    user.level = body.level
    if body.full_name:
        user.full_name = body.full_name
    await db.commit()
    return await build_user_out(db, user)


@router.get("/universities", response_model=list[UniversityOut])
async def universities(
    response: Response, db: AsyncSession = Depends(get_session)
) -> list[UniversityOut]:
    """Public: the signup page needs it before anyone is logged in."""
    rows = await db.scalars(
        select(University).where(University.is_active.is_(True)).order_by(University.name)
    )
    response.headers["Cache-Control"] = "public, max-age=3600"
    return [UniversityOut(id=u.id, name=u.name, short_name=u.short_name) for u in rows]
