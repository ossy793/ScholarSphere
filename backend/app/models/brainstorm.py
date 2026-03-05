import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, Integer, LargeBinary, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from ..database import Base


class BrainstormSession(Base):
    __tablename__ = "brainstorm_sessions"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id    = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title      = Column(String(255), nullable=False, default="New Session")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user     = relationship("User", back_populates="brainstorm_sessions")
    messages = relationship(
        "BrainstormMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="BrainstormMessage.created_at",
    )
    document = relationship(
        "BrainstormDocument",
        back_populates="session",
        uselist=False,
        cascade="all, delete-orphan",
    )


class BrainstormMessage(Base):
    __tablename__ = "brainstorm_messages"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("brainstorm_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role       = Column(String(20), nullable=False)   # "user" | "assistant"
    content    = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    session = relationship("BrainstormSession", back_populates="messages")


class BrainstormDocument(Base):
    __tablename__ = "brainstorm_documents"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id      = Column(
        UUID(as_uuid=True),
        ForeignKey("brainstorm_sessions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    filename        = Column(String(255), nullable=False)
    file_type       = Column(String(20),  nullable=False)   # "pdf"|"docx"|"txt"|"paste"
    extracted_text  = Column(Text, nullable=False)
    file_size_bytes = Column(Integer, nullable=True)
    file_content    = Column(LargeBinary, nullable=True)    # raw original file bytes
    viewer_pdf      = Column(LargeBinary, nullable=True)    # converted PDF for the viewer (Pipeline B)
    pdf_ready       = Column(Boolean, nullable=False, default=False)  # True once viewer_pdf is stored
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)

    session = relationship("BrainstormSession", back_populates="document")
