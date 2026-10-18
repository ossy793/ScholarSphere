"""Phase 5: class dues and their revisions

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-17
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def _id() -> sa.Column:
    return sa.Column("id", UUID, server_default=sa.text("gen_random_uuid()"), nullable=False)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "class_dues",
        _id(),
        sa.Column("class_id", UUID, nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("amount_kobo", sa.BigInteger(), nullable=False),
        sa.Column("deadline", TS, nullable=False),
        sa.Column("require_matric", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("platform_fee_percent", sa.Numeric(4, 2), server_default="3", nullable=False),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column("is_live", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("share_slug", sa.String(16), nullable=False),
        sa.Column("live_revision_id", UUID, nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_class_dues"),
        sa.UniqueConstraint("share_slug", name="uq_class_dues_share_slug"),
        sa.ForeignKeyConstraint(
            ["class_id"], ["classes.id"],
            name="fk_class_dues_class_id_classes", ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'pending', 'approved', 'rejected')", name="ck_class_dues_status"
        ),
        sa.CheckConstraint("amount_kobo > 0", name="ck_class_dues_amount_positive"),
        sa.CheckConstraint(
            "platform_fee_percent >= 0 AND platform_fee_percent <= 20",
            name="ck_class_dues_fee_percent_range",
        ),
    )
    op.create_index("ix_class_dues_class_id", "class_dues", ["class_id"])

    op.create_table(
        "due_revisions",
        _id(),
        sa.Column("due_id", UUID, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column("submitted_at", TS, nullable=True),
        sa.Column("reviewed_by", UUID, nullable=True),
        sa.Column("reviewed_at", TS, nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("platform_fee_percent", sa.Numeric(4, 2), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_due_revisions"),
        sa.UniqueConstraint("due_id", "version", name="uq_due_revisions_due_id_version"),
        sa.ForeignKeyConstraint(
            ["due_id"], ["class_dues.id"],
            name="fk_due_revisions_due_id_class_dues", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"], ["admin_users.id"],
            name="fk_due_revisions_reviewed_by_admin_users", ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'pending', 'approved', 'rejected')", name="ck_due_revisions_status"
        ),
    )
    op.create_index("ix_due_revisions_due_id", "due_revisions", ["due_id"])
    op.create_index(
        "ix_due_revisions_status_created_at", "due_revisions", ["status", "created_at"]
    )
    op.create_index(
        "uq_due_revisions_one_open_per_due", "due_revisions", ["due_id"],
        unique=True, postgresql_where=sa.text("status IN ('draft', 'pending')"),
    )

    # Added after both tables exist: the two tables point at each other.
    op.create_foreign_key(
        "fk_class_dues_live_revision_id_due_revisions", "class_dues", "due_revisions",
        ["live_revision_id"], ["id"], ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_class_dues_live_revision_id_due_revisions", "class_dues", type_="foreignkey"
    )
    op.drop_table("due_revisions")
    op.drop_table("class_dues")
