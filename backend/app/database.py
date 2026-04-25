from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import declarative_base, sessionmaker
from .config import settings

# NullPool: required for Supabase (Transaction mode, port 6543) — very low
# connection limit means each request must open/close its own connection.
engine = create_engine(
    settings.DATABASE_URL,
    poolclass=NullPool,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables():
    """Create all tables and apply incremental column migrations."""
    Base.metadata.create_all(bind=engine)
    _migrate_profile_fields()
    _migrate_conversation_tables()
    _migrate_study_tables()
    _migrate_auth_fields()
    _migrate_subscription_fields()
    _migrate_feature_usage_table()
    _migrate_support_user_id()


def _migrate_profile_fields():
    """Safely add new profile columns to the users table if they don't exist."""
    new_cols = [
        ("username",            "VARCHAR(50)"),
        ("school",              "VARCHAR(255)"),
        ("course",              "VARCHAR(255)"),
        ("profile_picture_url", "TEXT"),
        ("profile_completed",   "BOOLEAN NOT NULL DEFAULT FALSE"),
    ]
    with engine.connect() as conn:
        for col, col_type in new_cols:
            try:
                conn.execute(text(
                    f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {col_type}"
                ))
                conn.commit()
            except Exception:
                conn.rollback()


def _migrate_conversation_tables():
    """Create conversations + direct_messages tables (idempotent via CREATE TABLE IF NOT EXISTS)."""
    ddl = [
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user1_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            user2_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_conversation_pair UNIQUE (user1_id, user2_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_conversations_user1 ON conversations(user1_id)",
        "CREATE INDEX IF NOT EXISTS ix_conversations_user2 ON conversations(user2_id)",
        """
        CREATE TABLE IF NOT EXISTS direct_messages (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            sender_id       UUID NOT NULL REFERENCES users(id)         ON DELETE CASCADE,
            content         TEXT NOT NULL,
            is_read         BOOLEAN NOT NULL DEFAULT FALSE,
            created_at      TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_direct_messages_conv ON direct_messages(conversation_id)",
        "CREATE INDEX IF NOT EXISTS ix_direct_messages_sender ON direct_messages(sender_id)",
    ]
    with engine.connect() as conn:
        for stmt in ddl:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                conn.rollback()


def _migrate_study_tables():
    """Create notes and study_schedules tables (idempotent via CREATE TABLE IF NOT EXISTS)."""
    ddl = [
        """
        CREATE TABLE IF NOT EXISTS notes (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            folder     VARCHAR(100),
            title      VARCHAR(300) NOT NULL DEFAULT 'Untitled',
            content    TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_notes_user_id ON notes(user_id)",
        "CREATE INDEX IF NOT EXISTS ix_notes_folder  ON notes(folder)",
        """
        CREATE TABLE IF NOT EXISTS study_schedules (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            course           VARCHAR(255) NOT NULL,
            description      TEXT,
            scheduled_time   TIMESTAMP NOT NULL,
            duration_minutes INTEGER,
            notified_30      BOOLEAN NOT NULL DEFAULT FALSE,
            notified_15      BOOLEAN NOT NULL DEFAULT FALSE,
            notified_5       BOOLEAN NOT NULL DEFAULT FALSE,
            created_at       TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_study_schedules_user_id       ON study_schedules(user_id)",
        "CREATE INDEX IF NOT EXISTS ix_study_schedules_scheduled_time ON study_schedules(scheduled_time)",
    ]
    with engine.connect() as conn:
        for stmt in ddl:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                conn.rollback()


def _migrate_auth_fields():
    """Add Google OAuth and TOTP 2FA columns to users table."""
    new_cols = [
        ("google_id",    "VARCHAR(100)"),
        ("totp_secret",  "VARCHAR(64)"),
        ("totp_enabled", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ]
    with engine.connect() as conn:
        for col, col_type in new_cols:
            try:
                conn.execute(text(
                    f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {col_type}"
                ))
                conn.commit()
            except Exception:
                conn.rollback()
        try:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_google_id "
                "ON users(google_id) WHERE google_id IS NOT NULL"
            ))
            conn.commit()
        except Exception:
            conn.rollback()


def _migrate_subscription_fields():
    """
    Replace the old binary is_premium model with tiered subscription fields.
    - Adds subscription_plan / subscription_expiry / subscription_start columns
    - Migrates any existing is_premium=TRUE users → subscription_plan='free'
      (per spec: all promo users are downgraded; only admins get unrestricted access)
    - Drops old promo / premium columns once data is migrated
    """
    new_cols = [
        ("subscription_plan",   "VARCHAR(20) NOT NULL DEFAULT 'free'"),
        ("subscription_expiry", "TIMESTAMP"),
        ("subscription_start",  "TIMESTAMP"),
    ]
    with engine.connect() as conn:
        for col, col_type in new_cols:
            try:
                conn.execute(text(
                    f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {col_type}"
                ))
                conn.commit()
            except Exception:
                conn.rollback()

        # Downgrade all previously-premium users to free (promo removal)
        try:
            conn.execute(text(
                "UPDATE users SET subscription_plan = 'free' "
                "WHERE is_premium = TRUE"
            ))
            conn.commit()
        except Exception:
            conn.rollback()

        # Drop legacy columns (safe — data already migrated above)
        for col in ("is_premium", "premium_activated_at", "promo_expires_at"):
            try:
                conn.execute(text(f"ALTER TABLE users DROP COLUMN IF EXISTS {col}"))
                conn.commit()
            except Exception:
                conn.rollback()


def _migrate_feature_usage_table():
    """Create the feature_usage table for per-user quota tracking."""
    ddl = [
        """
        CREATE TABLE IF NOT EXISTS feature_usage (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            feature_name VARCHAR(64) NOT NULL,
            usage_count  INT NOT NULL DEFAULT 0,
            period_start TIMESTAMP NOT NULL DEFAULT NOW(),
            last_used    TIMESTAMP NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_feature_usage_user_feature UNIQUE (user_id, feature_name)
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_feature_usage_user_id ON feature_usage(user_id)",
    ]
    with engine.connect() as conn:
        for stmt in ddl:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                conn.rollback()


def _migrate_support_user_id():
    """Add user_id FK to support_conversations for authenticated web users."""
    with engine.connect() as conn:
        try:
            conn.execute(text(
                "ALTER TABLE support_conversations "
                "ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE SET NULL"
            ))
            conn.commit()
        except Exception:
            conn.rollback()
        try:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_support_conversations_user_id "
                "ON support_conversations(user_id)"
            ))
            conn.commit()
        except Exception:
            conn.rollback()
