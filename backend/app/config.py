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
    OPENAI_API_KEY: str = ""
    CLAUDE_API_KEY: str = ""
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
            "https://scholar-sphere-five.vercel.app",
            "https://pritis.name.ng",
            "https://www.pritis.name.ng",
        ]
        if self.EXTRA_ORIGINS:
            extra = [o.strip() for o in self.EXTRA_ORIGINS.split(",") if o.strip()]
            base.extend(extra)
        return base

    # Email — Mailjet API (production / Render) — free 6k/month, no domain needed
    MAILJET_API_KEY:      str = ""
    MAILJET_API_SECRET:   str = ""
    MAILJET_SENDER_EMAIL: str = ""   # Must be a verified sender in Mailjet dashboard

    # Email — Brevo API (production / Render) — no domain verification needed
    BREVO_API_KEY:      str = ""
    BREVO_SENDER_EMAIL: str = ""

    # Email — Resend API (production / Render) — requires verified domain
    RESEND_API_KEY: str = ""
    RESEND_FROM:    str = "Pritis <onboarding@resend.dev>"

    # Email — SMTP fallback (local development only)
    SMTP_HOST:     str = "smtp.gmail.com"
    SMTP_PORT:     int = 587
    SMTP_USER:     str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM:     str = "Pritis <noreply@ossyquiz.com>"

    # Premium promo
    WHATSAPP_GROUP_LINK: str = ""

    # Push notifications (VAPID)
    VAPID_PUBLIC_KEY:  str = ""
    VAPID_PRIVATE_KEY: str = ""
    VAPID_CLAIM_EMAIL: str = "admin@pritis.name.ng"

    # Cron job secret (protects scheduled notification endpoints)
    CRON_SECRET: str = ""

    # Paystack payment gateway
    PAYSTACK_SECRET_KEY: str = ""   # sk_test_... or sk_live_...
    PAYSTACK_PUBLIC_KEY: str = ""   # pk_test_... or pk_live_...
    PAYSTACK_AMOUNT_KOBO: int = 100000  # 100,000 kobo = ₦1,000

    # Google OAuth (Sign in with Google)
    GOOGLE_CLIENT_ID: str = ""

    # YouTube Data API v3 (for Study Zone learning recommendations)
    YOUTUBE_API_KEY: str = ""

    # ── WhatsApp / Customer Support ───────────────────────────────────────────
    # Twilio WhatsApp sandbox / production
    TWILIO_ACCOUNT_SID:    str = ""
    TWILIO_AUTH_TOKEN:     str = ""
    TWILIO_WHATSAPP_FROM:  str = "whatsapp:+14155238886"  # sandbox default

    # Meta Cloud API (preferred for production)
    META_WHATSAPP_TOKEN:       str = ""   # Permanent token from Meta Business
    META_PHONE_NUMBER_ID:      str = ""   # Phone Number ID from Meta
    META_WEBHOOK_VERIFY_TOKEN: str = "pritis_support_verify"

    # Support AI behaviour
    SUPPORT_AI_ENABLED: bool = True


@lru_cache()
def get_setting() -> Settings:
    return Settings()


settings = get_setting()
