import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from ..database import Base


class User(Base):
    __tablename__ = "users"

    id                   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email                = Column(String(255), unique=True, nullable=False, index=True)
    password_hash        = Column(String(255), nullable=False)
    full_name            = Column(String(255), nullable=False)
    department           = Column(String(255), nullable=True)
    university           = Column(String(255), nullable=True)
    level                = Column(String(50),  nullable=True)
    is_premium           = Column(Boolean, default=False, nullable=False)
    is_active            = Column(Boolean, default=True,  nullable=False, index=True)
    is_admin             = Column(Boolean, default=False, nullable=False, index=True)
    premium_activated_at = Column(DateTime, nullable=True)
    last_login_at        = Column(DateTime, nullable=True)
    created_at           = Column(DateTime, default=datetime.utcnow, nullable=False)

    courses             = relationship("Course",            back_populates="user", cascade="all, delete-orphan")
    quizzes             = relationship("Quiz",              back_populates="user", cascade="all, delete-orphan")
    attempts            = relationship("Attempt",           back_populates="user", cascade="all, delete-orphan")
    brainstorm_sessions = relationship("BrainstormSession", back_populates="user", cascade="all, delete-orphan")
    promo_codes         = relationship("PromoCode",         back_populates="user", cascade="all, delete-orphan")
    notifications       = relationship("Notification",      foreign_keys="Notification.target_user_id", back_populates="target_user", cascade="all, delete-orphan")
