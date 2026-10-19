"""/api/user/support: a student or rep writes to Pritis support and reads the answers.
Works before the email is verified too: that's often when people need help."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import get_current_user
from app.models import AdminUser, SupportMessage, SupportTicket, User
from app.schemas.support import MessageIn, MessageOut, TicketIn, TicketOut
from app.services import rate_limit, support

router = APIRouter(tags=["support"])


async def ticket_out(db: AsyncSession, ticket: SupportTicket, *, staff_view: bool = False) -> TicketOut:
    rows = (await db.execute(
        select(SupportMessage, AdminUser.full_name)
        .outerjoin(AdminUser, AdminUser.id == SupportMessage.admin_id)
        .where(SupportMessage.ticket_id == ticket.id).order_by(SupportMessage.created_at)
    )).all()
    return TicketOut(
        id=ticket.id, ticket_no=ticket.ticket_no, subject=ticket.subject, status=ticket.status,
        email=ticket.email, created_at=ticket.created_at, last_message_at=ticket.last_message_at,
        messages=[MessageOut(sender=m.sender, staff_name=name if staff_view else None, body=m.body,
                             created_at=m.created_at) for m, name in rows],
    )


@router.get("/support", response_model=list[TicketOut])
async def my_tickets(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)) -> list[TicketOut]:
    tickets = (await db.scalars(
        select(SupportTicket).where(SupportTicket.user_id == user.id)
        .order_by(SupportTicket.last_message_at.desc()).limit(50)
    )).all()
    return [await ticket_out(db, t) for t in tickets]


@router.post("/support", response_model=TicketOut, status_code=201)
async def contact_support(
    body: TicketIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
) -> TicketOut:
    await rate_limit.enforce("support_message", str(user.id))
    ticket = await support.open_ticket(db, user, body.subject, body.message)
    await db.commit()
    return await ticket_out(db, ticket)


@router.post("/support/{ticket_id}/messages", response_model=TicketOut)
async def add_message(
    ticket_id: uuid.UUID, body: MessageIn,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
) -> TicketOut:
    await rate_limit.enforce("support_message", str(user.id))
    ticket = await support.add_user_message(db, user, ticket_id, body.message)
    await db.commit()
    return await ticket_out(db, ticket)
