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
    ALLOWED_ORIGINS: list[str] = [
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "http://localhost:5501",
        "http://127.0.0.1:5501",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

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
