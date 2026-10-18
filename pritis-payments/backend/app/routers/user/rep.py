"""Rep application, payout bank account, and the rep area (/api/user/rep/*)."""

import uuid

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import func, select
from starlette.datastructures import UploadFile as StarletteUploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.db import get_session
from app.core.errors import field_error
from app.core.deps import (
    RepContext,
    get_current_rep,
    get_current_user,
    get_verified_user,
    require_own_class,
)
from app.models import Class, ClassDue, ClassMembership, Payment, RepBankAccount, User
from app.schemas.rep import (
    BankAccountOut,
    BankOut,
    MemberOut,
    RepApplicationForm,
    RepApplicationOut,
    RepClassOut,
    ResolveAccountIn,
    ResolveAccountOut,
)
from app.services import paystack, rate_limit, reps, storage

router = APIRouter(tags=["rep"])


def get_paystack() -> paystack.PaystackClient:
    return paystack.get_client()


def get_storage() -> storage.Storage:
    return storage.get_storage()


@router.get("/banks", response_model=list[BankOut])
async def banks(
    _: User = Depends(get_current_user), ps: paystack.PaystackClient = Depends(get_paystack)
) -> list[BankOut]:
    return [BankOut(code=b.code, name=b.name) for b in await ps.list_banks()]


@router.post("/bank-accounts/resolve", response_model=ResolveAccountOut)
async def resolve_account(
    body: ResolveAccountIn,
    user: User = Depends(get_verified_user),
    ps: paystack.PaystackClient = Depends(get_paystack),
) -> ResolveAccountOut:
    """Shows the account name before the rep submits, so they can see it's right."""
    await rate_limit.enforce("resolve_account", str(user.id))
    bank_name = await ps.bank_name(body.bank_code)
    account_name = await ps.resolve_account(body.account_number, body.bank_code)
    return ResolveAccountOut(account_name=account_name, bank_name=bank_name)


async def _application_out(db: AsyncSession, user: User) -> RepApplicationOut | None:
    application = await reps.latest_application(db, user.id)
    if application is None:
        return None
    klass = await db.get(Class, application.class_id)
    account = await db.scalar(select(RepBankAccount).where(RepBankAccount.user_id == user.id))
    return RepApplicationOut(
        id=application.id, status=application.status, created_at=application.created_at,
        reviewed_at=application.reviewed_at, reject_reason=application.reject_reason,
        matric_no=user.matric_no, university_id=user.university_id,
        klass=reps.class_out(klass), bank_account=reps.bank_account_out(account),
    )


@router.get("/rep/application", response_model=RepApplicationOut | None)
async def my_application(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
) -> RepApplicationOut | None:
    return await _application_out(db, user)


async def application_form(request: Request) -> tuple[RepApplicationForm, UploadFile]:
    """Reads the multipart form: the fields go through RepApplicationForm, plus one file.
    (FastAPI can't yet combine a form model with a file upload, so this does it by hand.)"""
    form = await request.form(max_files=1, max_fields=30)
    fields = {k: v for k, v in form.items() if isinstance(v, str)}
    try:
        model = RepApplicationForm.model_validate(fields)
    except ValidationError as exc:
        raise RequestValidationError(
            [{**e, "loc": ("body", *e["loc"])} for e in exc.errors(include_url=False)]
        ) from None
    proof = form.get("proof")
    if not isinstance(proof, StarletteUploadFile):
        raise field_error("proof", "Upload a photo of your school ID or student email", "missing")
    return model, proof


@router.post("/rep/applications", response_model=RepApplicationOut, status_code=201)
async def apply(
    # Order matters: check who's asking before reading the upload.
    user: User = Depends(get_verified_user),
    submitted: tuple[RepApplicationForm, UploadFile] = Depends(application_form),
    db: AsyncSession = Depends(get_session),
    ps: paystack.PaystackClient = Depends(get_paystack),
    store: storage.Storage = Depends(get_storage),
) -> RepApplicationOut:
    form, proof = submitted
    await rate_limit.enforce("rep_apply", str(user.id))
    await reps.submit_application(db, user, form, proof, ps, store)
    await db.commit()
    return await _application_out(db, user)


# --- The rep area. Every route goes through get_current_rep (own class only). ---------


@router.get("/rep/class", response_model=RepClassOut)
async def my_class(
    rep: RepContext = Depends(get_current_rep), db: AsyncSession = Depends(get_session)
) -> RepClassOut:
    account = await db.scalar(select(RepBankAccount).where(RepBankAccount.user_id == rep.user.id))
    return RepClassOut(
        klass=reps.class_out(rep.klass), invite_code=rep.klass.invite_code,
        member_count=await reps.member_count(db, rep.klass.id),
        bank_account=reps.bank_account_out(account),
    )


@router.get("/rep/classes/{class_id}/members", response_model=list[MemberOut])
async def class_members(
    class_id: uuid.UUID,
    rep: RepContext = Depends(get_current_rep),
    db: AsyncSession = Depends(get_session),
) -> list[MemberOut]:
    klass = require_own_class(class_id, rep)
    # The profile's matric number, or else the latest one the student gave when paying
    # one of this class's dues.
    paid_matric = (
        select(Payment.matric_no)
        .join(ClassDue, ClassDue.id == Payment.due_id)
        .where(Payment.user_id == User.id, ClassDue.class_id == klass.id, Payment.matric_no.is_not(None))
        .order_by(Payment.created_at.desc()).limit(1)
        .scalar_subquery()
    )
    rows = await db.execute(
        select(User.id, User.full_name, func.coalesce(User.matric_no, paid_matric), ClassMembership.joined_at)
        .join(ClassMembership, ClassMembership.user_id == User.id)
        .where(ClassMembership.class_id == klass.id)
        .order_by(User.full_name)
    )
    return [MemberOut(user_id=i, full_name=n, matric_no=m, joined_at=j) for i, n, m, j in rows]


@router.get("/rep/bank-account", response_model=BankAccountOut | None)
async def my_bank_account(
    rep: RepContext = Depends(get_current_rep), db: AsyncSession = Depends(get_session)
) -> BankAccountOut | None:
    account = await db.scalar(select(RepBankAccount).where(RepBankAccount.user_id == rep.user.id))
    return reps.bank_account_out(account)
