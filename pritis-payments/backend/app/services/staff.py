"""Staff accounts and their invitation links."""

from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import hash_token, new_token
from app.models import AdminInvite, AdminUser
from app.services import sessions
from app.services.email import queue_email

ROLE_LABELS = {"super_admin": "Super admin", "accountant": "Accountant", "support": "Customer support"}


async def create_staff(
    db: AsyncSession, *, email: str, full_name: str, role: str, invited_by: AdminUser | None
) -> tuple[AdminUser, str]:
    """Creates an invited staff account and returns it with its invitation link."""
    email = email.strip().lower()
    if await db.scalar(select(AdminUser.id).where(AdminUser.email == email)):
        raise AppError("email_taken", "A staff account with this email already exists", 409)
    admin = AdminUser(email=email, full_name=full_name.strip(), role=role, status="invited")
    db.add(admin)
    await db.flush()
    link = await issue_invite(db, admin, invited_by)
    return admin, link


async def reset_authenticator(
    db: AsyncSession, admin: AdminUser, reset_by: AdminUser | None
) -> str:
    """For a lost or replaced phone: clears the authenticator and the password, signs the
    person out everywhere, and sends a fresh set-up link. Returns the link."""
    if admin.status == "disabled":
        raise AppError("account_disabled", "Enable this account before resetting it", 409)
    admin.totp_secret = None
    admin.totp_last_step = None
    admin.password_hash = None
    admin.failed_login_count = 0
    admin.locked_until = None
    admin.status = "invited"
    await sessions.revoke_all(db, "admin", admin.id)
    return await issue_invite(db, admin, reset_by, reset=True)


async def issue_invite(
    db: AsyncSession, admin: AdminUser, invited_by: AdminUser | None, reset: bool = False
) -> str:
    """A new link (cancelling older ones) and the invitation email. Returns the link."""
    settings = get_settings()
    now = utcnow()
    await db.execute(
        update(AdminInvite)
        .where(AdminInvite.admin_user_id == admin.id, AdminInvite.used_at.is_(None))
        .values(used_at=now)
    )
    token = new_token()
    db.add(
        AdminInvite(
            admin_user_id=admin.id,
            token_hash=hash_token(token),
            expires_at=now + timedelta(hours=settings.staff_invite_hours),
            created_by=invited_by.id if invited_by else None,
        )
    )
    link = f"{settings.admin_base_url}/accept-invite.html?token={token}"
    await queue_email(
        db, admin.email, "staff_invite",
        {
            "full_name": admin.full_name,
            "role": ROLE_LABELS[admin.role],
            "link": link,
            "hours": settings.staff_invite_hours,
            "invited_by": invited_by.full_name if invited_by else "Pritis",
            "reset": reset,
        },
    )
    return link


async def find_invite(db: AsyncSession, token: str) -> tuple[AdminInvite, AdminUser]:
    invite = await db.scalar(
        select(AdminInvite).where(AdminInvite.token_hash == hash_token(token)).with_for_update()
    )
    if invite is None or invite.used_at is not None or invite.expires_at <= utcnow():
        raise AppError(
            "invite_invalid",
            "This invitation link is invalid or has expired. Ask the super admin for a new one.",
            400,
        )
    admin = await db.get(AdminUser, invite.admin_user_id, with_for_update=True)
    if admin is None or admin.status != "invited":
        raise AppError("invite_invalid", "This invitation has already been used.", 400)
    return invite, admin
