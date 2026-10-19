from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """All configuration comes from environment variables (or backend/.env)."""

    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    environment: Literal["local", "test", "staging", "production"] = "local"
    database_url: str
    test_database_url: str | None = None
    log_level: str = "INFO"

    # Public origin of the user site; used in emailed links and the Google redirect.
    app_base_url: str = "http://localhost:8100"

    # Public origin of the admin site; used in staff invitation links.
    admin_base_url: str = "http://admin.localhost:8100"

    jwt_secret: str
    # Fernet key for data encrypted at rest (staff authenticator secrets, rep bank accounts).
    data_encryption_key: str
    access_token_minutes: int = 15
    user_refresh_days: int = 7
    admin_session_hours: int = 8
    staff_invite_hours: int = 72

    email_code_minutes: int = 10
    email_code_max_attempts: int = 5

    login_max_failures: int = 5
    login_lockout_minutes: int = 15

    google_client_id: str | None = None
    google_client_secret: str | None = None

    # Paystack secret key: sk_test_... until launch, then sk_live_...
    paystack_secret_key: str | None = None

    # Uploaded files (rep proof of studentship). "local" keeps them under storage_local_dir;
    # "r2" uses a Cloudflare R2 bucket.
    storage_backend: Literal["local", "r2"] = "local"
    storage_local_dir: str = str(BACKEND_DIR / "var" / "uploads")
    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket: str | None = None
    r2_backup_bucket: str | None = None  # nightly database backups; falls back to r2_bucket

    email_backend: Literal["console", "brevo"] = "console"
    email_from: str = "Pritis DPP <no-reply@pritis.name.ng>"
    brevo_api_key: str | None = None

    @property
    def cookie_secure(self) -> bool:
        return self.environment in ("staging", "production")

    @property
    def sqlalchemy_url(self) -> str:
        if self.environment == "test":
            if not self.test_database_url:
                raise RuntimeError("TEST_DATABASE_URL must be set to run tests")
            if self.test_database_url == self.database_url:
                raise RuntimeError("TEST_DATABASE_URL must differ from DATABASE_URL; tests wipe it")
            return self.test_database_url
        return self.database_url

    @property
    def docs_enabled(self) -> bool:
        return self.environment != "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
