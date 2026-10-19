"""Support inbox and messages from staff

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def _common() -> list[sa.Column]:
    return [
        sa.Column("id", UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.execute("CREATE SEQUENCE support_ticket_no_seq START 1001")
    op.create_table(
        "support_tickets",
        *_common(),
        sa.Column("ticket_no", sa.BigInteger(), server_default=sa.text("nextval('support_ticket_no_seq')"),
                  nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("subject", sa.String(150), nullable=False),
        sa.Column("status", sa.String(20), server_default="open", nullable=False),
        sa.Column("last_message_at", TS, server_default=sa.text("now()"), nullable=False),
        sa.Column("closed_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_support_tickets"),
        sa.UniqueConstraint("ticket_no", name="uq_support_tickets_ticket_no"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_support_tickets_user_id_users",
                                ondelete="RESTRICT"),
        sa.CheckConstraint("status IN ('open', 'answered', 'closed')", name="ck_support_tickets_status"),
    )
    op.create_index("ix_support_tickets_user_id", "support_tickets", ["user_id"])
    op.create_index("ix_support_tickets_status_updated_at", "support_tickets", ["status", "updated_at"])

    op.create_table(
        "support_messages",
        *_common(),
        sa.Column("ticket_id", UUID, nullable=False),
        sa.Column("sender", sa.String(10), nullable=False),
        sa.Column("admin_id", UUID, nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_support_messages"),
        sa.ForeignKeyConstraint(["ticket_id"], ["support_tickets.id"],
                                name="fk_support_messages_ticket_id_support_tickets", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["admin_id"], ["admin_users.id"], name="fk_support_messages_admin_id_admin_users",
                                ondelete="RESTRICT"),
        sa.CheckConstraint("sender IN ('user', 'staff')", name="ck_support_messages_sender"),
        sa.CheckConstraint("(sender = 'staff') = (admin_id IS NOT NULL)", name="ck_support_messages_staff_has_admin"),
    )
    op.create_index("ix_support_messages_ticket_id", "support_messages", ["ticket_id"])

    op.create_table(
        "announcements",
        *_common(),
        sa.Column("subject", sa.String(150), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("audience", sa.String(20), nullable=False),
        sa.Column("sent_by", UUID, nullable=False),
        sa.Column("recipient_count", sa.Integer(), nullable=False),
        sa.Column("selected_user_ids", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_announcements"),
        sa.ForeignKeyConstraint(["sent_by"], ["admin_users.id"], name="fk_announcements_sent_by_admin_users",
                                ondelete="RESTRICT"),
        sa.CheckConstraint("audience IN ('all', 'students', 'reps', 'selected')", name="ck_announcements_audience"),
    )


def downgrade() -> None:
    op.drop_table("announcements")
    op.drop_table("support_messages")
    op.drop_table("support_tickets")
    op.execute("DROP SEQUENCE IF EXISTS support_ticket_no_seq")
