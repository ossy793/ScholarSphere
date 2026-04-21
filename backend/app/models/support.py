"""
Customer Support models:
  SupportAgent       — staff agents who handle conversations
  SupportConversation — a WhatsApp / web session with one user
  SupportMessage     — individual message inside a conversation
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, DateTime, ForeignKey, Text, Boolean, Integer,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from ..database import Base


class SupportAgent(Base):
    __tablename__ = "support_agents"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name       = Column(String(100), nullable=False)
    email      = Column(String(200), unique=True, nullable=True)
    # Optional link to a Pritis user account (admin user)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status     = Column(String(20), default="offline")   # online | offline | busy
    created_at = Column(DateTime, default=datetime.utcnow)

    conversations = relationship(
        "SupportConversation",
        back_populates="agent",
        foreign_keys="SupportConversation.assigned_agent_id",
    )


class SupportConversation(Base):
    __tablename__ = "support_conversations"

    id                 = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id            = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    user_phone         = Column(String(50),  nullable=False, index=True)
    user_name          = Column(String(100), nullable=True)
    channel            = Column(String(20),  default="whatsapp")   # whatsapp | web
    status             = Column(String(20),  default="ai")         # ai | human | waiting | closed
    assigned_agent_id  = Column(
        UUID(as_uuid=True),
        ForeignKey("support_agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    unread_count       = Column(Integer, default=0)
    created_at         = Column(DateTime, default=datetime.utcnow)
    updated_at         = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_message_at    = Column(DateTime, default=datetime.utcnow)
    last_message_text  = Column(String(200), nullable=True)

    agent    = relationship("SupportAgent",  back_populates="conversations",
                            foreign_keys=[assigned_agent_id])
    messages = relationship("SupportMessage", back_populates="conversation",
                            cascade="all, delete-orphan",
                            order_by="SupportMessage.created_at")


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("support_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sender_type  = Column(String(20),  nullable=False)   # user | ai | agent
    sender_name  = Column(String(100), nullable=True)    # agent name or "Pritis AI"
    content      = Column(Text,        nullable=False)
    created_at   = Column(DateTime,    default=datetime.utcnow)

    conversation = relationship("SupportConversation", back_populates="messages")
