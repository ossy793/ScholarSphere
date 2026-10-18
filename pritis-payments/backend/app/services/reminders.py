"""Payment reminders (design section 11), queued daily at 08:00 Lagos time.

Unpaid students of a live due are emailed 3 days before its deadline and on the deadline
day. The design's weekly "overdue" reminder is left out: after the deadline the due is
closed and can't be paid. Each reminder is recorded, so a student gets each kind once per
due (a rerun of the job, or an extended deadline, never repeats one).
"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import Class, ClassDue, ClassMembership, Payment, PaymentReminder, User
from app.services import app_settings, fees
from app.services.email import queue_email
from app.services.payouts import naira

log = logging.getLogger("app.reminders")
LAGOS = ZoneInfo("Africa/Lagos")


def reminder_kind(deadline: datetime, now: datetime) -> str | None:
    """Which reminder (if any) today brings for this deadline, counting Lagos calendar days."""
    if deadline <= now:
        return None
    days = (deadline.astimezone(LAGOS).date() - now.astimezone(LAGOS).date()).days
    return {3: "three_days", 0: "deadline_day"}.get(days)


async def queue_reminders(now: datetime | None = None) -> int:
    now = now or utcnow()
    queued = 0
    async with SessionLocal() as db:
        paystack_percent = Decimal((await app_settings.get_fee_settings(db)).paystack_fee_percent)
        dues = (await db.scalars(
            select(ClassDue).where(
                ClassDue.status == "approved", ClassDue.is_live.is_(True),
                ClassDue.deadline > now, ClassDue.deadline < now + timedelta(days=4),
            )
        )).all()
        for due in dues:
            kind = reminder_kind(due.deadline, now)
            if kind is None:
                continue
            klass = await db.get(Class, due.class_id)
            total = fees.student_total(due.amount_kobo, paystack_percent, Decimal(due.platform_fee_percent)).total_kobo
            paid = exists().where(Payment.user_id == User.id, Payment.due_id == due.id, Payment.status == "success")
            students = (await db.scalars(
                select(User).join(ClassMembership, ClassMembership.user_id == User.id)
                .where(ClassMembership.class_id == due.class_id, User.status == "active",
                       User.email_verified_at.is_not(None), ~paid)
            )).all()
            for student in students:
                inserted = await db.scalar(
                    insert(PaymentReminder).values(user_id=student.id, due_id=due.id, kind=kind)
                    .on_conflict_do_nothing(constraint="uq_payment_reminders_user_id_due_id_kind")
                    .returning(PaymentReminder.id)
                )
                if inserted is None:
                    continue  # already reminded
                await queue_email(db, student.email, "payment_reminder", {
                    "full_name": student.full_name, "title": due.title, "class_name": klass.name,
                    "total": naira(total), "kind": kind,
                    "deadline": due.deadline.astimezone(LAGOS).strftime("%A %d %B %Y, %I:%M %p").replace(" 0", " "),
                    "link": f"{get_settings().app_base_url}/due.html?id={due.id}",
                })
                queued += 1
        await db.commit()
    if queued:
        log.info("Queued %d payment reminder(s)", queued)
    return queued
