"""Support inbox and messages from staff (owner's request, 2026-10-19).

- A student or rep writes to support from the site. Support staff are emailed that a new
  message is waiting; they answer on the admin site, and each answer is emailed to the
  user's email address (the one they had when they wrote).
- Support staff can email everyone, all students, all verified class reps, or people
  they pick. Each recipient gets their own email through the outbox; the message is kept.
"""

import uuid
from typing import Literal

from fastapi import Request
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.errors import AppError
from app.models import AdminUser, Announcement, RepApplication, SupportMessage, SupportTicket, User
from app.services import audit
from app.services.email import queue_email

Audience = Literal["all", "students", "reps", "selected"]
MAX_SELECTED = 500


def ticket_label(ticket: SupportTicket) -> str:
    return f"#{ticket.ticket_no}"


# --- Tickets ------------------------------------------------------------------------------------


async def open_ticket(db: AsyncSession, user: User, subject: str, body: str) -> SupportTicket:
    ticket = SupportTicket(user_id=user.id, email=user.email, subject=subject, status="open",
                           last_message_at=utcnow())
    db.add(ticket)
    await db.flush()
    db.add(SupportMessage(ticket_id=ticket.id, sender="user", body=body))
    await db.refresh(ticket)  # ticket_no comes from the database
    staff = (await db.scalars(select(AdminUser.email).where(
        AdminUser.status == "active", AdminUser.role.in_(("support", "super_admin"))
    ))).all()
    for email in staff:
        await queue_email(db, email, "support_new", {
            "ticket": ticket_label(ticket), "subject": subject, "name": user.full_name, "email": user.email,
            "body": body, "link": f"{get_settings().admin_base_url}/support.html?ticket={ticket.id}",
        })
    return ticket


async def add_user_message(db: AsyncSession, user: User, ticket_id: uuid.UUID, body: str) -> SupportTicket:
    """The user adds to their own ticket (reopens it if it was answered or closed)."""
    ticket = await db.get(SupportTicket, ticket_id, with_for_update=True)
    if ticket is None or ticket.user_id != user.id:
        raise AppError("not_found", "Message not found", 404)
    db.add(SupportMessage(ticket_id=ticket.id, sender="user", body=body))
    ticket.status = "open"
    ticket.closed_at = None
    ticket.last_message_at = utcnow()
    return ticket


async def staff_reply(
    db: AsyncSession, ticket_id: uuid.UUID, body: str, close: bool, actor: AdminUser, request: Request | None
) -> SupportTicket:
    ticket = await db.get(SupportTicket, ticket_id, with_for_update=True)
    if ticket is None:
        raise AppError("not_found", "Message not found", 404)
    db.add(SupportMessage(ticket_id=ticket.id, sender="staff", admin_id=actor.id, body=body))
    now = utcnow()
    ticket.last_message_at = now
    ticket.status = "closed" if close else "answered"
    ticket.closed_at = now if close else None
    user = await db.get(User, ticket.user_id)
    first = await db.scalar(
        select(SupportMessage.body).where(SupportMessage.ticket_id == ticket.id, SupportMessage.sender == "user")
        .order_by(SupportMessage.created_at).limit(1)
    )
    await queue_email(db, ticket.email, "support_reply", {
        "full_name": user.full_name, "ticket": ticket_label(ticket), "subject": ticket.subject,
        "reply": body, "original": first or "", "closed": close,
        "link": f"{get_settings().app_base_url}/support.html",
    })
    await audit.record(db, actor=actor, action="support.reply", entity="support_ticket", entity_id=ticket.id,
                       after={"ticket": ticket.ticket_no, "closed": close}, request=request)
    return ticket


async def set_status(db: AsyncSession, ticket_id: uuid.UUID, closed: bool) -> SupportTicket:
    ticket = await db.get(SupportTicket, ticket_id, with_for_update=True)
    if ticket is None:
        raise AppError("not_found", "Message not found", 404)
    if closed:
        ticket.status, ticket.closed_at = "closed", utcnow()
    else:
        ticket.status, ticket.closed_at = "open", None
    return ticket


# --- Messages to many users ------------------------------------------------------------------------


def _is_rep():
    return exists().where(RepApplication.user_id == User.id, RepApplication.status == "approved")


def audience_query(audience: Audience, user_ids: list[uuid.UUID] | None):
    """Active users with a verified email in the chosen group."""
    query = select(User).where(User.status == "active", User.email_verified_at.is_not(None))
    if audience == "reps":
        query = query.where(_is_rep())
    elif audience == "students":
        query = query.where(~_is_rep())
    elif audience == "selected":
        query = query.where(User.id.in_(user_ids or []))
    return query


async def count_audience(db: AsyncSession, audience: Audience, user_ids: list[uuid.UUID] | None) -> int:
    return int(await db.scalar(select(func.count()).select_from(audience_query(audience, user_ids).subquery())) or 0)


async def send_announcement(
    db: AsyncSession, subject: str, body: str, audience: Audience, user_ids: list[uuid.UUID] | None,
    actor: AdminUser, request: Request | None,
) -> Announcement:
    if audience == "selected":
        if not user_ids:
            raise AppError("no_recipients", "Choose at least one person to send to.", 422)
        if len(user_ids) > MAX_SELECTED:
            raise AppError("too_many_selected", f"Choose at most {MAX_SELECTED} people, or send to a whole group.", 422)
    users = (await db.scalars(audience_query(audience, user_ids))).all()
    if not users:
        raise AppError("no_recipients", "Nobody matches that choice (only active users with a verified email get messages).", 422)
    announcement = Announcement(
        subject=subject, body=body, audience=audience, sent_by=actor.id, recipient_count=len(users),
        selected_user_ids=[str(u) for u in user_ids] if audience == "selected" else None,
    )
    db.add(announcement)
    await db.flush()
    for user in users:
        await queue_email(db, user.email, "announcement", {
            "full_name": user.full_name, "subject": subject, "body": body,
            "link": f"{get_settings().app_base_url}/dashboard.html",
        })
    await audit.record(db, actor=actor, action="announcement.send", entity="announcement", entity_id=announcement.id,
                       after={"subject": subject, "audience": audience, "recipients": len(users)}, request=request)
    return announcement
