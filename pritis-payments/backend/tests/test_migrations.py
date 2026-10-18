from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from app.core.db import engine
from app.models import Base
from tests.conftest import alembic_config

PHASE_1_TABLES = {
    "users", "classes", "class_memberships", "admin_users",
    "auth_sessions", "audit_logs", "app_settings", "email_outbox",
}


async def test_all_phase_1_tables_exist() -> None:
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    assert PHASE_1_TABLES <= tables


async def test_models_match_migrations() -> None:
    """Fails if a model changes without a matching Alembic migration."""
    async with engine.connect() as conn:
        diff = await conn.run_sync(
            lambda c: compare_metadata(
                MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata
            )
        )
    assert diff == []


def test_downgrade_and_upgrade_round_trip() -> None:
    cfg = alembic_config()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
