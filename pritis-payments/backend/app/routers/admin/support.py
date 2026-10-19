"""/api/admin/support (the support inbox) and /api/admin/announcements (messages from staff
to many users). Support staff and the super admin."""

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import SUPER_ADMIN, SUPPORT, require_roles
from app.core.errors import AppError
from app.models import AdminUser, Announcement, SupportTicket, User
from app.routers.user.support import ticket_out
from app.schemas.support import (
    AdminTicketOut, AdminTicketPage, AnnouncementIn, AnnouncementOut, AudienceCountOut, AudienceIn, ReplyIn,
    TicketStatus,
)
from app.services import reps, support

router = APIRouter(tags=["admin support"])
support_staff = require_roles(SUPPORT, SUPER_ADMIN)


async def _admin_ticket(db: AsyncSession, ticket: SupportTicket) -> AdminTicketOut:
    user = await db.get(User, ticket.user_id)
    return AdminTicketOut(
        **(await ticket_out(db, ticket, staff_view=True)).model_dump(),
        user_id=user.id, user_name=user.full_name, is_rep=await reps.is_verified_rep(db, user.id),
    )


@router.get("/support", response_model=AdminTicketPage)
async def inbox(
    status: TicketStatus = "open",
    _: AdminUser = Depends(support_staff), db: AsyncSession = Depends(get_session),
) -> AdminTicketPage:
    total = await db.scalar(select(func.count()).select_from(SupportTicket).where(SupportTicket.status == status))
    order = SupportTicket.last_message_at.asc() if status == "open" else SupportTicket.last_message_at.desc()
    tickets = (await db.scalars(
        select(SupportTicket).where(SupportTicket.status == status).order_by(order).limit(100)
    )).all()
    return AdminTicketPage(items=[await _admin_ticket(db, t) for t in tickets], total=total or 0)


@router.get("/support/{ticket_id}", response_model=AdminTicketOut)
async def one_ticket(
    ticket_id: uuid.UUID, _: AdminUser = Depends(support_staff), db: AsyncSession = Depends(get_session)
) -> AdminTicketOut:
    ticket = await db.get(SupportTicket, ticket_id)
    if ticket is None:
        raise AppError("not_found", "Message not found", 404)
    return await _admin_ticket(db, ticket)


@router.post("/support/{ticket_id}/reply", response_model=AdminTicketOut)
async def reply(
    ticket_id: uuid.UUID, body: ReplyIn, request: Request,
    actor: AdminUser = Depends(support_staff), db: AsyncSession = Depends(get_session),
) -> AdminTicketOut:
    """Saves the answer and emails it to the user."""
    ticket = await support.staff_reply(db, ticket_id, body.message, body.close, actor, request)
    await db.commit()
    return await _admin_ticket(db, ticket)


@router.post("/support/{ticket_id}/close", response_model=AdminTicketOut)
async def close(
    ticket_id: uuid.UUID, _: AdminUser = Depends(support_staff), db: AsyncSession = Depends(get_session)
) -> AdminTicketOut:
    ticket = await support.set_status(db, ticket_id, closed=True)
    await db.commit()
    return await _admin_ticket(db, ticket)


@router.post("/support/{ticket_id}/reopen", response_model=AdminTicketOut)
async def reopen(
    ticket_id: uuid.UUID, _: AdminUser = Depends(support_staff), db: AsyncSession = Depends(get_session)
) -> AdminTicketOut:
    ticket = await support.set_status(db, ticket_id, closed=False)
    await db.commit()
    return await _admin_ticket(db, ticket)


# --- Messages to many users ---------------------------------------------------------------------


@router.post("/announcements/count", response_model=AudienceCountOut)
async def count(
    body: AudienceIn, _: AdminUser = Depends(support_staff), db: AsyncSession = Depends(get_session)
) -> AudienceCountOut:
    """How many people a message would reach, before sending."""
    return AudienceCountOut(recipients=await support.count_audience(db, body.audience, body.user_ids))


@router.post("/announcements", response_model=AnnouncementOut, status_code=201)
async def send(
    body: AnnouncementIn, request: Request,
    actor: AdminUser = Depends(support_staff), db: AsyncSession = Depends(get_session),
) -> AnnouncementOut:
    a = await support.send_announcement(db, body.subject, body.message, body.audience, body.user_ids, actor, request)
    await db.commit()
    return AnnouncementOut(id=a.id, subject=a.subject, body=a.body, audience=a.audience,
                           recipient_count=a.recipient_count, sent_by_email=actor.email, created_at=a.created_at)


@router.get("/announcements", response_model=list[AnnouncementOut])
async def history(_: AdminUser = Depends(support_staff), db: AsyncSession = Depends(get_session)) -> list[AnnouncementOut]:
    rows = (await db.execute(
        select(Announcement, AdminUser.email).join(AdminUser, AdminUser.id == Announcement.sent_by)
        .order_by(Announcement.created_at.desc()).limit(50)
    )).all()
    return [AnnouncementOut(id=a.id, subject=a.subject, body=a.body, audience=a.audience,
                            recipient_count=a.recipient_count, sent_by_email=email, created_at=a.created_at)
            for a, email in rows]
