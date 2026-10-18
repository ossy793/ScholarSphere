"""Dues: what students see (/api/user/dues) and what reps manage (/api/user/rep/dues)."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import RepContext, get_current_rep, get_current_user
from app.core.errors import AppError
from app.models import ClassDue, User
from app.schemas.dues import DueCreateIn, DueTerms, RepDueOut, StudentDueOut
from app.services import dues, payments

router = APIRouter(tags=["dues"])


# --- Students -------------------------------------------------------------------------


@router.post("/dues/by-link/{slug}", response_model=StudentDueOut)
async def open_shared_link(
    slug: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
) -> StudentDueOut:
    """Opening a rep's link: joins the class, then shows the due."""
    due, klass = await dues.join_by_link(db, user, slug[:16])
    await db.commit()
    if not due.is_live:
        # Approved but paused: the student has joined, and sees it once the rep switches it on.
        raise AppError("due_paused", f"You've joined {klass.name}. This due isn't open right now; check back later.", 409)
    return await dues.student_due(db, user, due.id)


@router.get("/dues", response_model=list[StudentDueOut])
async def my_dues(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
) -> list[StudentDueOut]:
    # A payment finished on Paystack whose confirming page never ran shows as Paid here.
    await payments.recheck_pending_for_user(db, user.id)
    await db.commit()
    return await dues.visible_dues(db, user)


@router.get("/dues/{due_id}", response_model=StudentDueOut)
async def one_due(
    due_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
) -> StudentDueOut:
    await payments.recheck_pending_for_user(db, user.id, due_id)
    await db.commit()
    return await dues.student_due(db, user, due_id)


# --- Reps: every route goes through get_current_rep and get_own_due -----------------------


@router.get("/rep/dues", response_model=list[RepDueOut])
async def rep_dues(
    rep: RepContext = Depends(get_current_rep), db: AsyncSession = Depends(get_session)
) -> list[RepDueOut]:
    rows = await db.scalars(
        select(ClassDue).where(ClassDue.class_id == rep.klass.id).order_by(ClassDue.created_at.desc())
    )
    return [await dues.rep_due_out(db, d) for d in rows]


@router.post("/rep/dues", response_model=RepDueOut, status_code=201)
async def create(
    body: DueCreateIn, rep: RepContext = Depends(get_current_rep), db: AsyncSession = Depends(get_session)
) -> RepDueOut:
    terms = DueTerms.model_validate(body.model_dump(exclude={"submit"}))
    due = await dues.create_due(db, rep, terms, body.submit)
    await db.commit()
    return await dues.rep_due_out(db, due)


@router.get("/rep/dues/{due_id}", response_model=RepDueOut)
async def get_due(
    due_id: uuid.UUID, rep: RepContext = Depends(get_current_rep), db: AsyncSession = Depends(get_session)
) -> RepDueOut:
    return await dues.rep_due_out(db, await dues.get_own_due(db, rep, due_id))


@router.put("/rep/dues/{due_id}", response_model=RepDueOut)
async def edit(
    due_id: uuid.UUID, body: DueTerms,
    rep: RepContext = Depends(get_current_rep), db: AsyncSession = Depends(get_session),
) -> RepDueOut:
    """Before approval: edits the draft. After: creates a revision for approval."""
    due = await dues.update_due(db, rep, due_id, body)
    await db.commit()
    return await dues.rep_due_out(db, due)


@router.post("/rep/dues/{due_id}/submit", response_model=RepDueOut)
async def submit(
    due_id: uuid.UUID, rep: RepContext = Depends(get_current_rep), db: AsyncSession = Depends(get_session)
) -> RepDueOut:
    due = await dues.submit_due(db, rep, due_id)
    await db.commit()
    return await dues.rep_due_out(db, due)


@router.post("/rep/dues/{due_id}/live", response_model=RepDueOut)
async def go_live(
    due_id: uuid.UUID, rep: RepContext = Depends(get_current_rep), db: AsyncSession = Depends(get_session)
) -> RepDueOut:
    due = await dues.set_live(db, rep, due_id, True)
    await db.commit()
    return await dues.rep_due_out(db, due)


@router.post("/rep/dues/{due_id}/pause", response_model=RepDueOut)
async def pause(
    due_id: uuid.UUID, rep: RepContext = Depends(get_current_rep), db: AsyncSession = Depends(get_session)
) -> RepDueOut:
    due = await dues.set_live(db, rep, due_id, False)
    await db.commit()
    return await dues.rep_due_out(db, due)
