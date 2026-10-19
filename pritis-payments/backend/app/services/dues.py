"""The due lifecycle (design section 4):

    draft --submit--> pending --approve--> approved --enable--> live (rep can pause)
                         |                    |
                       reject              rep edits --> edit pending (old terms stay live)
                         v                                   | admin approves -> new terms live
                      rejected --rep revises--> draft        | admin rejects  -> old terms stay

Students only ever see and pay approved terms. After the deadline a due is closed; an
extension is an edit of the deadline, approved like any other.
"""

import secrets
import string
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import Request
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.deps import SUPER_ADMIN, RepContext
from app.core.errors import AppError, field_error
from app.models import AdminUser, Class, ClassDue, ClassMembership, DueRevision, Payment, User
from app.schemas.dues import DueTerms, Phase, RepDueOut, RevisionOut, StudentDueOut
from app.services import app_settings, audit, collected, fees
from app.services.email import queue_email

SLUG_ALPHABET = string.ascii_uppercase + string.digits


# --- Terms <-> storage --------------------------------------------------------------


def snapshot(terms: DueTerms) -> dict[str, Any]:
    return terms.model_dump(mode="json")


def terms_from_snapshot(data: dict[str, Any]) -> DueTerms:
    return DueTerms.model_validate(data)


def live_terms(due: ClassDue) -> DueTerms:
    return DueTerms(
        title=due.title, description=due.description, amount_kobo=due.amount_kobo,
        deadline=due.deadline, require_matric=due.require_matric,
    )


def _apply(due: ClassDue, terms: DueTerms) -> None:
    due.title = terms.title
    due.description = terms.description
    due.amount_kobo = terms.amount_kobo
    due.deadline = terms.deadline
    due.require_matric = terms.require_matric


def _check_deadline(terms: DueTerms) -> None:
    if terms.deadline <= utcnow():
        raise field_error("deadline", "The deadline must be in the future", "deadline_past")


# --- Reading ------------------------------------------------------------------------


def is_closed(due: ClassDue, now: datetime | None = None) -> bool:
    return (now or utcnow()) > due.deadline


def ensure_payable(due: ClassDue, now: datetime | None = None) -> None:
    """The server-side check every payment goes through (design section 5): only an
    approved, live due before its deadline can be paid."""
    if due.status != "approved" or not due.is_live:
        raise AppError("due_not_open", "This due isn't open for payment", 409)
    if is_closed(due, now):
        raise AppError("due_closed", "This due is closed. The deadline has passed.", 409)


async def open_revision(db: AsyncSession, due_id: uuid.UUID, lock: bool = False) -> DueRevision | None:
    query = select(DueRevision).where(
        DueRevision.due_id == due_id, DueRevision.status.in_(("draft", "pending"))
    )
    return await db.scalar(query.with_for_update() if lock else query)


async def latest_revision(db: AsyncSession, due_id: uuid.UUID) -> DueRevision:
    return await db.scalar(
        select(DueRevision).where(DueRevision.due_id == due_id)
        .order_by(DueRevision.version.desc()).limit(1)
    )


def phase(due: ClassDue, open_rev: DueRevision | None) -> Phase:
    if due.status != "approved":
        return due.status  # draft, pending, rejected
    if open_rev is not None and open_rev.status == "pending":
        return "edit_pending"
    return "live" if due.is_live else "approved"


def share_url(due: ClassDue) -> str:
    return f"{get_settings().app_base_url}/due.html?s={due.share_slug}"


def revision_out(rev: DueRevision) -> RevisionOut:
    return RevisionOut(
        version=rev.version, status=rev.status, terms=terms_from_snapshot(rev.snapshot),
        submitted_at=rev.submitted_at, reviewed_at=rev.reviewed_at, reject_reason=rev.reject_reason,
    )


async def rep_due_out(db: AsyncSession, due: ClassDue) -> RepDueOut:
    open_rev = await open_revision(db, due.id)
    latest = await latest_revision(db, due.id)
    students, paid_count, collected_kobo, outstanding = await collected.summary(db, due)
    return RepDueOut(
        students=students, paid_count=paid_count, collected_kobo=collected_kobo, outstanding_kobo=outstanding,
        id=due.id,
        phase=phase(due, open_rev),
        is_live=due.is_live,
        closed=due.status == "approved" and is_closed(due),
        share_url=share_url(due),
        live_terms=live_terms(due) if due.live_revision_id else None,
        open_revision=revision_out(open_rev) if open_rev else None,
        latest_revision=revision_out(latest),
        created_at=due.created_at,
    )


def student_due_out(
    due: ClassDue, class_name: str, paystack_percent: Decimal, paid: tuple[uuid.UUID, int] | None = None
) -> StudentDueOut:
    """Students see one amount: the total (service charge included). Paid once a successful
    payment exists, showing what was paid; otherwise Due closed after the deadline, else Unpaid."""
    if paid:
        status, payment_id, total = "paid", paid[0], paid[1]
    else:
        status = "closed" if is_closed(due) else "unpaid"
        payment_id = None
        total = fees.student_total(due.amount_kobo, paystack_percent, Decimal(due.platform_fee_percent)).total_kobo
    return StudentDueOut(
        id=due.id, class_name=class_name, title=due.title, description=due.description,
        total_kobo=total, deadline=due.deadline, require_matric=due.require_matric,
        status=status, payment_id=payment_id,
    )


async def _paid(db: AsyncSession, user_id: uuid.UUID, due_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[uuid.UUID, int]]:
    """due_id -> (payment id, total paid) for the student's successful payments."""
    if not due_ids:
        return {}
    rows = await db.execute(
        select(Payment.due_id, Payment.id, Payment.total_kobo).where(
            Payment.user_id == user_id, Payment.due_id.in_(due_ids), Payment.status == "success"
        )
    )
    return {due_id: (payment_id, total) for due_id, payment_id, total in rows}


async def _paystack_percent(db: AsyncSession) -> Decimal:
    return Decimal((await app_settings.get_fee_settings(db)).paystack_fee_percent)


async def get_own_due(db: AsyncSession, rep: RepContext, due_id: uuid.UUID, lock: bool = False) -> ClassDue:
    """Ownership check: another class's due answers 404, as if it didn't exist."""
    due = await db.get(ClassDue, due_id, with_for_update=lock)
    if due is None or due.class_id != rep.klass.id:
        raise AppError("not_found", "Due not found", 404)
    return due


# --- Rep actions --------------------------------------------------------------------


async def _new_slug(db: AsyncSession) -> str:
    while True:
        slug = "".join(secrets.choice(SLUG_ALPHABET) for _ in range(10))
        if not await db.scalar(select(exists().where(ClassDue.share_slug == slug))):
            return slug


async def _notify_staff(db: AsyncSession, title: str, rep: RepContext, kind: str) -> None:
    """Due or revision awaiting approval: tell the people who approve (super admin, accountant)."""
    approvers = (await db.scalars(
        select(AdminUser.email).where(
            AdminUser.status == "active", AdminUser.role.in_(("super_admin", "accountant"))
        )
    )).all()
    for email in approvers:
        await queue_email(db, email, "due_submitted", {
            "kind": kind, "title": title, "class_name": rep.klass.name,
            "rep_name": rep.user.full_name, "link": f"{get_settings().admin_base_url}/dues.html",
        })


async def create_due(db: AsyncSession, rep: RepContext, terms: DueTerms, submit: bool) -> ClassDue:
    _check_deadline(terms)
    now = utcnow()
    due = ClassDue(
        class_id=rep.klass.id, share_slug=await _new_slug(db),
        status="pending" if submit else "draft", is_live=False,
        platform_fee_percent=Decimal((await app_settings.get_fee_settings(db)).platform_fee_percent),
    )
    _apply(due, terms)
    db.add(due)
    await db.flush()
    db.add(DueRevision(
        due_id=due.id, version=1, snapshot=snapshot(terms),
        status="pending" if submit else "draft", submitted_at=now if submit else None,
    ))
    await db.flush()
    if submit:
        await _notify_staff(db, due.title, rep, "new")
    return due


async def update_due(db: AsyncSession, rep: RepContext, due_id: uuid.UUID, terms: DueTerms) -> ClassDue:
    """Before the first approval this edits the draft. After it, the change waits as a
    pending revision and the live terms stay as they are until an admin approves."""
    due = await get_own_due(db, rep, due_id, lock=True)
    open_rev = await open_revision(db, due.id, lock=True)
    if open_rev is not None and open_rev.status == "pending":
        raise AppError("awaiting_approval", "This due is waiting for approval. Wait for the decision before changing it.", 409)
    _check_deadline(terms)

    if due.status in ("draft", "rejected"):
        # Never approved: the draft is simply rewritten.
        if open_rev is None:  # rejected: start a fresh draft
            latest = await latest_revision(db, due.id)
            open_rev = DueRevision(due_id=due.id, version=latest.version + 1, status="draft", snapshot={})
            db.add(open_rev)
        open_rev.snapshot = snapshot(terms)
        _apply(due, terms)
        due.status = "draft"
        return due

    # Approved: a revision for review; class_dues is untouched (design section 6).
    if snapshot(terms) == snapshot(live_terms(due)):
        raise AppError("no_changes", "Nothing has changed", 409)
    latest = await latest_revision(db, due.id)
    db.add(DueRevision(
        due_id=due.id, version=latest.version + 1, snapshot=snapshot(terms),
        status="pending", submitted_at=utcnow(),
    ))
    await db.flush()
    await _notify_staff(db, terms.title, rep, "edit")
    return due


async def submit_due(db: AsyncSession, rep: RepContext, due_id: uuid.UUID) -> ClassDue:
    due = await get_own_due(db, rep, due_id, lock=True)
    open_rev = await open_revision(db, due.id, lock=True)
    if due.status != "draft" or open_rev is None or open_rev.status != "draft":
        raise AppError("not_a_draft", "Only a draft can be submitted", 409)
    _check_deadline(terms_from_snapshot(open_rev.snapshot))
    open_rev.status = "pending"
    open_rev.submitted_at = utcnow()
    due.status = "pending"
    await db.flush()
    await _notify_staff(db, due.title, rep, "new")
    return due


async def set_live(db: AsyncSession, rep: RepContext, due_id: uuid.UUID, live: bool) -> ClassDue:
    due = await get_own_due(db, rep, due_id, lock=True)
    if due.status != "approved":
        raise AppError("not_approved", "Only an approved due can be switched on or off", 409)
    due.is_live = live
    return due


# --- Admin decisions ----------------------------------------------------------------


async def _pending_for_review(db: AsyncSession, due_id: uuid.UUID) -> tuple[ClassDue, DueRevision]:
    due = await db.get(ClassDue, due_id, with_for_update=True)
    if due is None:
        raise AppError("not_found", "Due not found", 404)
    rev = await open_revision(db, due.id, lock=True)
    if rev is None or rev.status != "pending":
        raise AppError("nothing_to_review", "There's nothing waiting for approval on this due", 409)
    return due, rev


async def _rep_and_class(db: AsyncSession, due: ClassDue) -> tuple[User, Class]:
    klass = await db.get(Class, due.class_id)
    return await db.get(User, klass.rep_user_id), klass


async def approve(
    db: AsyncSession, due_id: uuid.UUID, actor: AdminUser, fee_percent: Decimal | None, request: Request
) -> ClassDue:
    due, rev = await _pending_for_review(db, due_id)
    proposed = terms_from_snapshot(rev.snapshot)
    fees = await app_settings.get_fee_settings(db)
    if proposed.amount_kobo > fees.max_due_amount_kobo and actor.role != SUPER_ADMIN:
        raise AppError(
            "super_admin_required",
            "This due is above the maximum amount in settings, so only the super admin can approve it.",
            403,
        )
    first = due.live_revision_id is None
    fee = fee_percent if fee_percent is not None else (
        Decimal(fees.platform_fee_percent) if first else due.platform_fee_percent
    )
    before = None if first else {**snapshot(live_terms(due)), "platform_fee_percent": str(due.platform_fee_percent)}

    now = utcnow()
    _apply(due, proposed)
    due.platform_fee_percent = fee
    due.live_revision_id = rev.id
    due.status = "approved"
    rev.status = "approved"
    rev.reviewed_by = actor.id
    rev.reviewed_at = now
    rev.platform_fee_percent = fee

    rep_user, klass = await _rep_and_class(db, due)
    await queue_email(db, rep_user.email, "due_decided", {
        "full_name": rep_user.full_name, "title": due.title, "class_name": klass.name,
        "approved": True, "kind": "new" if first else "edit", "reason": None,
        "link": f"{get_settings().app_base_url}/rep.html",
    })
    await audit.record(
        db, actor=actor, action="due.approve", entity="class_due", entity_id=due.id,
        before=before,
        after={**rev.snapshot, "platform_fee_percent": str(fee), "version": rev.version},
        request=request,
    )
    return due


async def reject(db: AsyncSession, due_id: uuid.UUID, actor: AdminUser, reason: str, request: Request) -> ClassDue:
    due, rev = await _pending_for_review(db, due_id)
    first = due.live_revision_id is None
    rev.status = "rejected"
    rev.reviewed_by = actor.id
    rev.reviewed_at = utcnow()
    rev.reject_reason = reason
    if first:
        due.status = "rejected"
    # An edit's rejection leaves the approved terms live.

    rep_user, klass = await _rep_and_class(db, due)
    await queue_email(db, rep_user.email, "due_decided", {
        "full_name": rep_user.full_name, "title": rev.snapshot["title"], "class_name": klass.name,
        "approved": False, "kind": "new" if first else "edit", "reason": reason,
        "link": f"{get_settings().app_base_url}/rep.html",
    })
    await audit.record(
        db, actor=actor, action="due.reject", entity="class_due", entity_id=due.id,
        after={"title": rev.snapshot["title"], "version": rev.version, "reason": reason},
        request=request,
    )
    return due


# --- Students -----------------------------------------------------------------------


async def join_by_link(db: AsyncSession, user: User, slug: str) -> tuple[ClassDue, Class]:
    """Opening a shared due link adds the student to that class (design section 4)."""
    due = await db.scalar(select(ClassDue).where(ClassDue.share_slug == slug.strip().upper()))
    if due is None or due.status != "approved":
        raise AppError("not_found", "This dues link isn't valid. Ask your rep for the right link.", 404)
    klass = await db.get(Class, due.class_id)
    already = await db.scalar(select(exists().where(
        ClassMembership.user_id == user.id, ClassMembership.class_id == klass.id
    )))
    if not already:
        db.add(ClassMembership(user_id=user.id, class_id=klass.id))
        await db.flush()
    return due, klass


async def visible_dues(db: AsyncSession, user: User) -> list[StudentDueOut]:
    """Live dues of every class the student belongs to."""
    rows = await db.execute(
        select(ClassDue, Class.name)
        .join(Class, Class.id == ClassDue.class_id)
        .join(ClassMembership, ClassMembership.class_id == Class.id)
        .where(ClassMembership.user_id == user.id, ClassDue.status == "approved", ClassDue.is_live.is_(True))
        .order_by(ClassDue.deadline)
    )
    rows = rows.all()
    paid = await _paid(db, user.id, [due.id for due, _ in rows])
    percent = await _paystack_percent(db)
    return [student_due_out(due, name, percent, paid.get(due.id)) for due, name in rows]


async def student_due(db: AsyncSession, user: User, due_id: uuid.UUID) -> StudentDueOut:
    row = (await db.execute(
        select(ClassDue, Class.name)
        .join(Class, Class.id == ClassDue.class_id)
        .join(ClassMembership, ClassMembership.class_id == Class.id)
        .where(ClassMembership.user_id == user.id, ClassDue.id == due_id,
               ClassDue.status == "approved", ClassDue.is_live.is_(True))
    )).first()
    if row is None:
        raise AppError("not_found", "Due not found", 404)
    due, name = row
    paid = await _paid(db, user.id, [due.id])
    return student_due_out(due, name, await _paystack_percent(db), paid.get(due.id))


async def count_pending(db: AsyncSession) -> int:
    return await db.scalar(
        select(func.count()).select_from(DueRevision).where(DueRevision.status == "pending")
    ) or 0
