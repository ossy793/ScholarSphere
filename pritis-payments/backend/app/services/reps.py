"""Rep applications: submit, review (approve / reject), and who counts as a rep."""

import secrets
import string
import uuid
from typing import Literal

from fastapi import Request, UploadFile
from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.errors import AppError, field_error
from app.models import AdminUser, Class, ClassMembership, RepApplication, RepBankAccount, User
from app.schemas.rep import BankAccountOut, ClassOut, RepApplicationForm
from app.services import audit
from app.services.email import queue_email
from app.services.paystack import PaystackClient
from app.services.storage import Storage
from app.services.users import get_active_university

RepStatus = Literal["none", "pending", "approved", "rejected"]

MAX_PROOF_BYTES = 5 * 1024 * 1024
INVITE_ALPHABET = string.ascii_uppercase + string.digits


# --- Who is a rep ---------------------------------------------------------------


async def latest_application(db: AsyncSession, user_id: uuid.UUID) -> RepApplication | None:
    return await db.scalar(
        select(RepApplication)
        .where(RepApplication.user_id == user_id)
        .order_by(RepApplication.created_at.desc(), RepApplication.id.desc())
        .limit(1)
    )


async def is_verified_rep(db: AsyncSession, user_id: uuid.UUID) -> bool:
    return bool(await db.scalar(select(exists().where(
        RepApplication.user_id == user_id, RepApplication.status == "approved"
    ))))


async def rep_status(db: AsyncSession, user_id: uuid.UUID) -> RepStatus:
    if await is_verified_rep(db, user_id):
        return "approved"
    latest = await latest_application(db, user_id)
    return latest.status if latest else "none"  # type: ignore[return-value]


async def verified_rep_class(db: AsyncSession, user_id: uuid.UUID) -> Class | None:
    """The class this user is the verified rep of, if any."""
    if not await is_verified_rep(db, user_id):
        return None
    return await db.scalar(select(Class).where(Class.rep_user_id == user_id))


# --- Display helpers ------------------------------------------------------------


def class_out(klass: Class) -> ClassOut:
    return ClassOut(
        id=klass.id, name=klass.name, school=klass.school, department=klass.department,
        level=klass.level, session=klass.session,
    )


def bank_account_out(account: RepBankAccount | None) -> BankAccountOut | None:
    if account is None:
        return None
    return BankAccountOut(
        bank_name=account.bank_name,
        account_name=account.account_name,
        last4=crypto.decrypt(account.account_number)[-4:],
        verified=account.verified_at is not None,
    )


async def member_count(db: AsyncSession, class_id: uuid.UUID) -> int:
    return await db.scalar(
        select(func.count()).select_from(ClassMembership).where(ClassMembership.class_id == class_id)
    ) or 0


# --- Proof file -----------------------------------------------------------------


def _detect_type(head: bytes) -> tuple[str, str] | None:
    """Content type and extension from the file's first bytes; the browser's claim is ignored."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if head.startswith(b"%PDF-"):
        return "application/pdf", ".pdf"
    return None


async def read_proof(upload: UploadFile) -> tuple[bytes, str, str]:
    data = await upload.read(MAX_PROOF_BYTES + 1)
    if not data:
        raise field_error("proof", "Upload a photo of your school ID or student email", "missing")
    if len(data) > MAX_PROOF_BYTES:
        raise field_error("proof", "The file must be 5 MB or smaller", "too_large")
    detected = _detect_type(data[:16])
    if detected is None:
        raise field_error("proof", "Upload a JPG, PNG or WebP photo, or a PDF", "file_type")
    content_type, ext = detected
    return data, content_type, ext


# --- Submitting -----------------------------------------------------------------


async def _new_invite_code(db: AsyncSession) -> str:
    while True:
        code = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(8))
        if not await db.scalar(select(exists().where(Class.invite_code == code))):
            return code


async def submit_application(
    db: AsyncSession,
    user: User,
    form: RepApplicationForm,
    proof: UploadFile,
    paystack: PaystackClient,
    storage: Storage,
) -> RepApplication:
    status = await rep_status(db, user.id)
    if status == "approved":
        raise AppError("already_rep", "You're already a verified rep", 409)
    if status == "pending":
        raise AppError("application_pending", "Your application is already being reviewed", 409)

    university = await get_active_university(db, form.university_id)
    data, content_type, ext = await read_proof(proof)
    # Never trust a name typed in the browser: ask Paystack again.
    bank_name = await paystack.bank_name(form.bank_code)
    account_name = await paystack.resolve_account(form.account_number, form.bank_code)

    key = f"rep-proofs/{user.id}/{uuid.uuid4().hex}{ext}"
    await storage.put(key, data, content_type)

    # The application registers the class; a rep reapplying after a rejection keeps it.
    klass = await db.scalar(select(Class).where(Class.rep_user_id == user.id).with_for_update())
    if klass is None:
        klass = Class(rep_user_id=user.id, invite_code=await _new_invite_code(db))
        db.add(klass)
    klass.name = form.class_name
    klass.school = university.name
    klass.department = form.department
    klass.level = form.level
    klass.session = form.session

    account = await db.scalar(
        select(RepBankAccount).where(RepBankAccount.user_id == user.id).with_for_update()
    )
    if account is None:
        account = RepBankAccount(user_id=user.id)
        db.add(account)
    account.bank_code = form.bank_code
    account.bank_name = bank_name
    account.account_number = crypto.encrypt(form.account_number)
    account.account_name = account_name
    # A changed account must be verified again.
    account.paystack_recipient_code = None
    account.verified_by = None
    account.verified_at = None

    user.full_name = form.full_name
    user.phone = form.phone
    user.matric_no = form.matric_no
    await db.flush()

    application = RepApplication(
        user_id=user.id, class_id=klass.id, proof_file=key, proof_content_type=content_type,
        status="pending",
    )
    db.add(application)
    try:
        await db.flush()
    except IntegrityError:  # two submissions at once
        raise AppError("application_pending", "Your application is already being reviewed", 409) from None

    await queue_email(db, user.email, "rep_application_received",
                      {"full_name": user.full_name, "class_name": klass.name})
    return application


# --- Reviewing ------------------------------------------------------------------


async def _load_for_review(db: AsyncSession, application_id: uuid.UUID):
    application = await db.get(RepApplication, application_id, with_for_update=True)
    if application is None:
        raise AppError("not_found", "Application not found", 404)
    if application.status != "pending":
        raise AppError("already_reviewed", "This application has already been decided", 409)
    user = await db.get(User, application.user_id)
    klass = await db.get(Class, application.class_id)
    account = await db.scalar(
        select(RepBankAccount).where(RepBankAccount.user_id == application.user_id).with_for_update()
    )
    return application, user, klass, account


async def approve(
    db: AsyncSession, application_id: uuid.UUID, actor: AdminUser, request: Request,
    paystack: PaystackClient,
) -> RepApplication:
    application, user, klass, account = await _load_for_review(db, application_id)
    if account is None:
        raise AppError("no_bank_account", "This applicant has no payout account", 409)

    # The payout recipient is created only once a reviewer has verified the rep.
    recipient = await paystack.create_transfer_recipient(
        account_name=account.account_name,
        account_number=crypto.decrypt(account.account_number),
        bank_code=account.bank_code,
    )
    now = utcnow()
    account.paystack_recipient_code = recipient
    account.verified_by = actor.id
    account.verified_at = now
    application.status = "approved"
    application.reviewed_by = actor.id
    application.reviewed_at = now

    await queue_email(db, user.email, "rep_application_approved", {
        "full_name": user.full_name, "class_name": klass.name,
        "link": f"{get_settings().app_base_url}/login.html",
        "bank_name": account.bank_name, "account_name": account.account_name,
        "last4": crypto.decrypt(account.account_number)[-4:],
    })
    await audit.record(
        db, actor=actor, action="rep_application.approve", entity="rep_application",
        entity_id=application.id,
        after={"user": user.email, "class": klass.name, "recipient_code": recipient},
        request=request,
    )
    return application


async def reject(
    db: AsyncSession, application_id: uuid.UUID, reason: str, actor: AdminUser, request: Request
) -> RepApplication:
    application, user, klass, _ = await _load_for_review(db, application_id)
    application.status = "rejected"
    application.reviewed_by = actor.id
    application.reviewed_at = utcnow()
    application.reject_reason = reason

    await queue_email(db, user.email, "rep_application_rejected", {
        "full_name": user.full_name, "class_name": klass.name, "reason": reason,
        "link": f"{get_settings().app_base_url}/rep-apply.html",
    })
    await audit.record(
        db, actor=actor, action="rep_application.reject", entity="rep_application",
        entity_id=application.id, after={"user": user.email, "class": klass.name, "reason": reason},
        request=request,
    )
    return application
