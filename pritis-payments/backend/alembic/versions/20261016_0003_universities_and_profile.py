"""Universities list; university, level on users

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-16
"""
import json
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)
DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "nigerian_universities.json"


def upgrade() -> None:
    universities = op.create_table(
        "universities",
        sa.Column("id", UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("short_name", sa.String(30), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_universities"),
        sa.UniqueConstraint("name", name="uq_universities_name"),
    )
    rows = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    op.bulk_insert(universities, [{"name": r["name"], "short_name": r["short"]} for r in rows])

    op.add_column("users", sa.Column("university_id", UUID, nullable=True))
    op.add_column("users", sa.Column("level", sa.String(3), nullable=True))
    op.create_foreign_key(
        "fk_users_university_id_universities", "users", "universities",
        ["university_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_users_level", "users", "level IN ('100', '200', '300', '400', '500')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_level", "users", type_="check")
    op.drop_constraint("fk_users_university_id_universities", "users", type_="foreignkey")
    op.drop_column("users", "level")
    op.drop_column("users", "university_id")
    op.drop_table("universities")
