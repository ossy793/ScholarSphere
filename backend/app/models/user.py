import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Boolean, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from ..database import Base


class User(Base):
    __tablename__ = "users"

    id                   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email                = Column(String(255), unique=True, nullable=False, index=True)
    password_hash        = Column(String(255), nullable=False)
    full_name            = Column(String(255), nullable=False)
    username             = Column(String(50),  unique=True,  nullable=True,  index=True)
    department           = Column(String(255), nullable=True)   # legacy
    university           = Column(String(255), nullable=True)   # legacy
    school               = Column(String(255), nullable=True)
    level                = Column(String(50),  nullable=True)
    course               = Column(String(255), nullable=True)
    profile_picture_url  = Column(Text,        nullable=True)
    profile_completed    = Column(Boolean, default=False, nullable=False)
    google_id            = Column(String(100), unique=True, nullable=True, index=True)
    totp_secret          = Column(String(64), nullable=True)
    totp_enabled         = Column(Boolean, default=False, nullable=False)

    # ── Subscription ───────────────────────────────────────────────────────────
    # subscription_plan: "free" | "basic" | "pro"
    subscription_plan    = Column(String(20), nullable=False, default="free", server_default="free")
    subscription_expiry  = Column(DateTime, nullable=True)    # NULL = never expires (admin grant)
    subscription_start   = Column(DateTime, nullable=True)    # when current period started

    is_active            = Column(Boolean, default=True,  nullable=False, index=True)
    is_admin             = Column(Boolean, default=False, nullable=False, index=True)
    last_login_at        = Column(DateTime, nullable=True)
    created_at           = Column(DateTime, default=datetime.utcnow, nullable=False)

    courses             = relationship("Course",            back_populates="user", cascade="all, delete-orphan")
    quizzes             = relationship("Quiz",              back_populates="user", cascade="all, delete-orphan")
    attempts            = relationship("Attempt",           back_populates="user", cascade="all, delete-orphan")
    brainstorm_sessions = relationship("BrainstormSession", back_populates="user", cascade="all, delete-orphan")
    notifications       = relationship("Notification",      foreign_keys="Notification.target_user_id", back_populates="target_user", cascade="all, delete-orphan")
