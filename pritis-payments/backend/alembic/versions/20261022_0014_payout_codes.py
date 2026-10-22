"""Email codes to confirm payout requests

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-22
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_user_tokens_purpose", "user_tokens", type_="check")
    op.create_check_constraint(
        "ck_user_tokens_purpose", "user_tokens", "purpose IN ('verify_email', 'reset_password', 'confirm_payout')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM user_tokens WHERE purpose = 'confirm_payout'")
    op.drop_constraint("ck_user_tokens_purpose", "user_tokens", type_="check")
    op.create_check_constraint(
        "ck_user_tokens_purpose", "user_tokens", "purpose IN ('verify_email', 'reset_password')"
    )
