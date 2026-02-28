from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env relative to this file's location (backend/.env),
# so it works regardless of the working directory uvicorn is launched from.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
    )

    DATABASE_URL: str
    SECRET_KEY: str
    GROQ_API_KEY: str
    ACCESS_TOKEN_EXPIRE_DAYS: int = 30

    # CORS — allowed frontend origins
    # Add your deployed frontend URL here or via EXTRA_ORIGINS env var
    EXTRA_ORIGINS: str = ""   # comma-separated list set in Render env vars

    @property
    def ALLOWED_ORIGINS(self) -> list[str]:
        base = [
            "http://localhost:5500",
            "http://127.0.0.1:5500",
            "http://localhost:5501",
            "http://127.0.0.1:5501",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
        if self.EXTRA_ORIGINS:
            extra = [o.strip() for o in self.EXTRA_ORIGINS.split(",") if o.strip()]
            base.extend(extra)
        return base

    # Email (SMTP) — used for promo code delivery
    SMTP_HOST:     str = "smtp.gmail.com"
    SMTP_PORT:     int = 587
    SMTP_USER:     str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM:     str = "OssyQuiz <noreply@ossyquiz.com>"

    # Premium promo
    WHATSAPP_GROUP_LINK: str = ""


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
