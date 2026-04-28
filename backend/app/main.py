from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from sqlalchemy.orm import Session
from sqlalchemy import func
from .config import settings
from .database import create_tables, get_db
from .routers import auth, courses, quizzes, questions, generate, attempts, analytics, brainstorm, admin, notifications, push, users, friends, study, assistant, support, challenge, payment
from .models.user import User
from .models.question import Question
from .models.brainstorm import BrainstormSession, BrainstormMessage, BrainstormDocument  # noqa: F401 – registers tables
from .models.feature_usage import FeatureUsage  # noqa: F401 – registers table
from .models.notification import Notification, NotificationRead  # noqa: F401 – registers tables
from .models.leaderboard import LeaderboardReset  # noqa: F401 – registers table
from .models.push_subscription import PushSubscription  # noqa: F401 – registers table
from .models.password_reset import PasswordResetCode   # noqa: F401 – registers table
from .models.conversation import Conversation, DirectMessage  # noqa: F401 – registers tables
from .models.study import Note, StudySchedule  # noqa: F401 – registers tables
from .models.support import SupportAgent, SupportConversation, SupportMessage  # noqa: F401 – registers tables
from .models.challenge import Challenge, ChallengeParticipant, ChallengeResponse  # noqa: F401 – registers tables
from .models.payment_transaction import PaymentTransaction  # noqa: F401 – registers table
from .utils.scheduler import start_scheduler, stop_scheduler
from .database import engine
from sqlalchemy import text

# ── Rate limiter (shared across routers via app.state) ────────────────────────
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="Pritis API",
    description="AI-powered quiz generation and practice platform",
    version="1.0.0",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Cron-Secret"],
)

# ── Security headers ──────────────────────────────────────────────────────────
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), geolocation=()"
    return response


@app.on_event("startup")
def on_startup():
    create_tables()
    # Idempotent schema migrations for columns added after initial table creation
    with engine.connect() as conn:
        conn.execute(text(
            "ALTER TABLE challenges ADD COLUMN IF NOT EXISTS host_mode VARCHAR(20) DEFAULT 'participant'"
        ))
        conn.commit()
    # ALTER TYPE ADD VALUE must run outside a transaction in PostgreSQL
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(
            "ALTER TYPE questiontype ADD VALUE IF NOT EXISTS 'true_false'"
        ))
    start_scheduler()


@app.on_event("shutdown")
def on_shutdown():
    stop_scheduler()


app.include_router(auth.router, prefix="/api")
app.include_router(courses.router, prefix="/api")
app.include_router(quizzes.router, prefix="/api")
app.include_router(questions.router, prefix="/api")
app.include_router(generate.router, prefix="/api")
app.include_router(attempts.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")
app.include_router(brainstorm.router, prefix="/api")
app.include_router(admin.router,          prefix="/api")
app.include_router(notifications.router,  prefix="/api")
app.include_router(push.router,           prefix="/api")
app.include_router(users.router,          prefix="/api")
app.include_router(friends.router,        prefix="/api")
app.include_router(study.router,          prefix="/api")
app.include_router(assistant.router,      prefix="/api")
app.include_router(support.router,        prefix="/api")
app.include_router(challenge.router,      prefix="/api")
app.include_router(payment.router,        prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "Pritis"}


@app.get("/api/stats")
def public_stats(db: Session = Depends(get_db)):
    """Public endpoint — returns platform-wide totals for the landing page."""
    total_users     = db.query(func.count(User.id)).scalar() or 0
    total_questions = db.query(func.count(Question.id)).scalar() or 0
    return {
        "total_users":     total_users,
        "total_questions": total_questions,
    }
