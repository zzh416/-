"""Personal self-assessment, separate from question content and model feedback."""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.core import Question


class LearningRecord(Base):
    __tablename__ = "learning_records"
    __table_args__ = (UniqueConstraint("owner_id", "identity_key", name="uq_learning_owner_identity"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    identity_key: Mapped[str] = mapped_column(String(100))
    question_id: Mapped[UUID | None] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True, nullable=True)
    source_kind: Mapped[str] = mapped_column(String(16))
    source_session_id: Mapped[UUID | None] = mapped_column(ForeignKey("interview_sessions.id", ondelete="SET NULL"), nullable=True)
    source_turn_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    prompt: Mapped[str] = mapped_column(Text)
    question_version: Mapped[int] = mapped_column(Integer)
    answer_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    question: Mapped[Question | None] = relationship()
