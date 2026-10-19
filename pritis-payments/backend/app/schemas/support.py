import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

Subject = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=150)]
Body = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=5000)]
TicketStatus = Literal["open", "answered", "closed"]
Audience = Literal["all", "students", "reps", "selected"]


class TicketIn(BaseModel):
    subject: Subject
    message: Body


class MessageIn(BaseModel):
    message: Body


class ReplyIn(BaseModel):
    message: Body
    close: bool = False  # answer and mark resolved in one step


class MessageOut(BaseModel):
    sender: Literal["user", "staff"]
    staff_name: str | None  # shown to staff only
    body: str
    created_at: datetime


class TicketOut(BaseModel):
    id: uuid.UUID
    ticket_no: int
    subject: str
    status: TicketStatus
    email: str
    created_at: datetime
    last_message_at: datetime
    messages: list[MessageOut]


class AdminTicketOut(TicketOut):
    user_id: uuid.UUID
    user_name: str
    is_rep: bool


class AdminTicketPage(BaseModel):
    items: list[AdminTicketOut]
    total: int


class AnnouncementIn(BaseModel):
    subject: Subject
    message: Body
    audience: Audience
    user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)


class AudienceIn(BaseModel):
    audience: Audience
    user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)


class AudienceCountOut(BaseModel):
    recipients: int


class AnnouncementOut(BaseModel):
    id: uuid.UUID
    subject: str
    body: str
    audience: Audience
    recipient_count: int
    sent_by_email: str
    created_at: datetime
