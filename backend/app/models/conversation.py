import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from ..database import Base


class Conversation(Base):
    """One-to-one chat thread between two users. user1_id < user2_id enforced in app logic."""
    __tablename__ = "conversations"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user1_id   = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    user2_id   = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("user1_id", "user2_id", name="uq_conversation_pair"),
    )

    messages = relationship(
        "DirectMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="DirectMessage.created_at",
    )
    user1 = relationship("User", foreign_keys=[user1_id])
    user2 = relationship("User", foreign_keys=[user2_id])


class DirectMessage(Base):
    __tablename__ = "direct_messages"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_id       = Column(UUID(as_uuid=True), ForeignKey("users.id",         ondelete="CASCADE"), nullable=False, index=True)
    content         = Column(Text, nullable=False)
    is_read         = Column(Boolean, default=False, nullable=False)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)

    conversation = relationship("Conversation", back_populates="messages")
    sender       = relationship("User", foreign_keys=[sender_id])
