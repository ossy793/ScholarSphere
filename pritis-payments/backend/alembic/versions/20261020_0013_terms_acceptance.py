"""Record acceptance of the Terms and the Rep Agreement

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-20
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("users", sa.Column("terms_accepted_at", TS, nullable=True))
    op.add_column("users", sa.Column("terms_version", sa.String(10), nullable=True))
    op.add_column("rep_applications", sa.Column("agreement_accepted_at", TS, nullable=True))
    op.add_column("rep_applications", sa.Column("agreement_version", sa.String(10), nullable=True))


def downgrade() -> None:
    op.drop_column("rep_applications", "agreement_version")
    op.drop_column("rep_applications", "agreement_accepted_at")
    op.drop_column("users", "terms_version")
    op.drop_column("users", "terms_accepted_at")
