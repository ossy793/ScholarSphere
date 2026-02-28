"""
Run this once to add the new columns to your existing database.
Usage (from the backend/ directory):
    python migrate.py
"""
from app.database import engine
from sqlalchemy import text

MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_premium BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active  BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin   BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS premium_activated_at TIMESTAMP",
    """
    CREATE TABLE IF NOT EXISTS promo_codes (
        id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        code_hash  VARCHAR(64) NOT NULL UNIQUE,
        is_used    BOOLEAN NOT NULL DEFAULT FALSE,
        expires_at TIMESTAMP NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_promo_codes_user_id ON promo_codes (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_promo_codes_is_used  ON promo_codes (is_used)",
    "CREATE INDEX IF NOT EXISTS ix_users_is_active ON users (is_active)",
    "CREATE INDEX IF NOT EXISTS ix_users_is_admin  ON users (is_admin)",
    # Brainstorm file storage — stores raw PDF/DOCX bytes so history shows the real viewer
    "ALTER TABLE brainstorm_documents ADD COLUMN IF NOT EXISTS file_content BYTEA",
    # User profile fields
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS department    VARCHAR(255)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS university    VARCHAR(255)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS level         VARCHAR(50)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMP",
    # Notifications
    """
    CREATE TABLE IF NOT EXISTS notifications (
        id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        title          VARCHAR(255) NOT NULL,
        message        TEXT NOT NULL,
        target_user_id UUID REFERENCES users(id) ON DELETE CASCADE,
        created_by_id  UUID REFERENCES users(id) ON DELETE SET NULL,
        created_at     TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_notifications_target_user_id ON notifications (target_user_id)",
    """
    CREATE TABLE IF NOT EXISTS notification_reads (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        notification_id UUID NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
        user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        read_at         TIMESTAMP NOT NULL DEFAULT NOW(),
        UNIQUE(notification_id, user_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_notification_reads_user_id         ON notification_reads (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_notification_reads_notification_id ON notification_reads (notification_id)",
]

def run():
    with engine.connect() as conn:
        for sql in MIGRATIONS:
            print(f"Running: {sql.strip()[:60]}...")
            conn.execute(text(sql))
        conn.commit()
    print("\nMigration complete! All columns and tables are up to date.")
    print("\nTo make yourself an admin, run this SQL in psql or pgAdmin:")
    print("  UPDATE users SET is_admin = TRUE WHERE email = 'your@email.com';")

if __name__ == "__main__":
    run()
