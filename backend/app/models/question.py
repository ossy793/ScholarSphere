import uuid
from sqlalchemy import Column, String, Integer, ForeignKey, Text, Enum, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from ..database import Base
import enum


class QuestionType(str, enum.Enum):
    mcq = "mcq"
    short_answer = "short_answer"
    true_false = "true_false"


class Question(Base):
    __tablename__ = "questions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    quiz_id = Column(UUID(as_uuid=True), ForeignKey("quizzes.id", ondelete="CASCADE"), nullable=False)
    question_text = Column(Text, nullable=False)
    question_type = Column(Enum(QuestionType), default=QuestionType.mcq, nullable=False)
    options = Column(JSON, nullable=True)          # list of strings for MCQ/true_false
    correct_answer = Column(Text, nullable=False)  # index (str) for MCQ, text for short_answer
    explanation = Column(Text, nullable=True)
    order_index = Column(Integer, default=0, nullable=False)

    quiz = relationship("Quiz", back_populates="questions")
    attempt_answers = relationship("AttemptAnswer", back_populates="question", cascade="all, delete-orphan")
