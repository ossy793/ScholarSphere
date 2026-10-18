"""Command-line tasks.

    python -m app.cli create-super-admin --email you@pritis.name.ng --name "Your Name"
    python -m app.cli resend-invite --email you@pritis.name.ng
    python -m app.cli reset-authenticator --email you@pritis.name.ng
    python -m app.cli send-test-email --to you@example.com

create-super-admin makes the first super admin (there is no public signup on the admin
site) and prints the link where they set a password and an authenticator app.
reset-authenticator is for a lost phone: it clears the password and authenticator and
prints a new set-up link.
"""

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.errors import AppError
from app.models import AdminUser
from app.services import audit, staff
from app.services import email as email_service


async def create_super_admin(email: str, full_name: str) -> str:
    async with SessionLocal() as db:
        admin, link = await staff.create_staff(
            db, email=email, full_name=full_name, role="super_admin", invited_by=None
        )
        await audit.record(
            db, actor=None, action="admin.invite", entity="admin_user", entity_id=admin.id,
            after={"email": admin.email, "full_name": admin.full_name, "role": admin.role,
                   "via": "command line"},
        )
        await db.commit()
    return link


async def resend_super_admin_invite(email: str) -> str:
    """For when the first link expired before it was used."""
    async with SessionLocal() as db:
        admin = await db.scalar(select(AdminUser).where(AdminUser.email == email.strip().lower()))
        if admin is None or admin.status != "invited":
            raise AppError("not_invited", "No staff account waiting for an invitation with that email")
        link = await staff.issue_invite(db, admin, None)
        await db.commit()
    return link


async def reset_authenticator(email: str) -> str:
    """For a super admin who lost their phone and has no other super admin to reset them."""
    async with SessionLocal() as db:
        admin = await db.scalar(
            select(AdminUser).where(AdminUser.email == email.strip().lower()).with_for_update()
        )
        if admin is None:
            raise AppError("not_found", "No staff account with that email")
        before = admin.status
        link = await staff.reset_authenticator(db, admin, None)
        await audit.record(
            db, actor=None, action="admin.reset_authenticator", entity="admin_user",
            entity_id=admin.id, before={"status": before},
            after={"status": admin.status, "via": "command line"},
        )
        await db.commit()
    return link


async def send_test_email(to: str) -> str:
    """Sends one email straight away (not through the queue) so problems show at once."""
    sender = email_service.get_email_sender()
    subject, html, text = email_service.render(
        "verify_email", {"full_name": "there", "code": "123456", "minutes": 10}
    )
    await sender.send(to, f"[Test] {subject}", html, text)
    return type(sender).__name__


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create-super-admin", help="create the first super admin")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)

    resend = commands.add_parser("resend-invite", help="new invitation link for a staff member")
    resend.add_argument("--email", required=True)

    reset = commands.add_parser(
        "reset-authenticator", help="lost phone: clear password and authenticator, new link"
    )
    reset.add_argument("--email", required=True)

    test_email = commands.add_parser("send-test-email", help="check the email provider works")
    test_email.add_argument("--to", required=True)

    args = parser.parse_args()
    if args.command == "send-test-email":
        try:
            used = asyncio.run(send_test_email(args.to))
        except Exception as exc:  # show the provider's reason plainly
            print(f"Sending failed: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"Test email sent to {args.to} using {used}. Check the inbox (and spam folder).")
        return

    try:
        if args.command == "create-super-admin":
            link = asyncio.run(create_super_admin(args.email, args.name))
        elif args.command == "reset-authenticator":
            link = asyncio.run(reset_authenticator(args.email))
        else:
            link = asyncio.run(resend_super_admin_invite(args.email))
    except AppError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        sys.exit(1)

    print("Invitation created. Open this link to set the password and authenticator app")
    print("(it was also queued as an email):\n")
    print(f"    {link}\n")


if __name__ == "__main__":
    main()
