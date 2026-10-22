"""P1 的本地用户与资料库实体。"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    display_name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    collections: Mapped[list["Collection"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )


class Collection(Base):
    __tablename__ = "collections"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_collections_owner_name"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    purpose: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    owner: Mapped[User] = relationship(back_populates="collections")


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    collection_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), index=True, nullable=True
    )
    kind: Mapped[str] = mapped_column(String(24), default="LIBRARY", server_default="LIBRARY")
    title: Mapped[str] = mapped_column(String(200))
    source_format: Mapped[str] = mapped_column(String(32))
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="READY", server_default="READY")
    current_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    versions: Mapped[list["AssetVersion"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan"
    )
    job: Mapped["IngestionJob | None"] = relationship(
        back_populates="asset", cascade="all, delete-orphan", uselist=False
    )


class AssetVersion(Base):
    __tablename__ = "asset_versions"
    __table_args__ = (UniqueConstraint("asset_id", "version", name="uq_asset_versions_asset_version"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    asset_id: Mapped[UUID] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    asset: Mapped[Asset] = relationship(back_populates="versions")
    chunks: Mapped[list["TextChunk"]] = relationship(
        back_populates="asset_version", cascade="all, delete-orphan"
    )


class TextChunk(Base):
    __tablename__ = "text_chunks"
    __table_args__ = (UniqueConstraint("asset_version_id", "ordinal", name="uq_chunks_version_ordinal"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    asset_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("asset_versions.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    block_type: Mapped[str] = mapped_column(String(32), default="TEXT", server_default="TEXT")
    locator: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")

    asset_version: Mapped[AssetVersion] = relationship(back_populates="chunks")


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", server_default="QUEUED")
    stage: Mapped[str] = mapped_column(String(32), default="STORED", server_default="STORED")
    processed_units: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    celery_task_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    unit_failures: Mapped[list[dict]] = mapped_column(JSON, default=list, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    asset: Mapped[Asset] = relationship(back_populates="job")


class QuestionExtractionRun(Base):
    __tablename__ = "question_extraction_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[UUID] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    asset_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("asset_versions.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", server_default="QUEUED")
    stage: Mapped[str] = mapped_column(String(32), default="QUEUED", server_default="QUEUED")
    question_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    answered_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    missing_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    collection_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("collections.id", ondelete="SET NULL"), index=True, nullable=True
    )
    source_chunk_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("text_chunks.id", ondelete="SET NULL"), nullable=True
    )
    source_type: Mapped[str] = mapped_column(String(24))
    prompt: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    answer: Mapped["Answer | None"] = relationship(
        back_populates="question", cascade="all, delete-orphan", uselist=False
    )
    note: Mapped["QuestionNote | None"] = relationship(
        back_populates="question", cascade="all, delete-orphan", uselist=False
    )


class QuestionOccurrence(Base):
    __tablename__ = "question_occurrences"
    __table_args__ = (
        UniqueConstraint(
            "extraction_run_id",
            "source_chunk_id",
            "question_start",
            "question_end",
            name="uq_question_occurrences_run_source_range",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    question_id: Mapped[UUID] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), index=True
    )
    extraction_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("question_extraction_runs.id", ondelete="CASCADE"), index=True
    )
    asset_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("asset_versions.id", ondelete="CASCADE"), index=True
    )
    source_chunk_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("text_chunks.id", ondelete="SET NULL"), nullable=True
    )
    answer_source_chunk_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("text_chunks.id", ondelete="SET NULL"), nullable=True
    )
    question_start: Mapped[int] = mapped_column(Integer)
    question_end: Mapped[int] = mapped_column(Integer)
    answer_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    answer_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    question_quote: Mapped[str] = mapped_column(Text)
    answer_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_spans: Mapped[list[dict]] = mapped_column(JSON, default=list, server_default="[]")
    confidence: Mapped[float] = mapped_column(Float)
    review_status: Mapped[str] = mapped_column(
        String(24), default="PENDING", server_default="PENDING"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Answer(Base):
    __tablename__ = "answers"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    question_id: Mapped[UUID] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), unique=True, index=True
    )
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(24), default="USER_WRITTEN")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    question: Mapped[Question] = relationship(back_populates="answer")
    revisions: Mapped[list["AnswerRevision"]] = relationship(cascade="all, delete-orphan")


class AnswerRevision(Base):
    __tablename__ = "answer_revisions"
    __table_args__ = (UniqueConstraint("answer_id", "version", name="uq_answer_revisions_version"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    answer_id: Mapped[UUID] = mapped_column(ForeignKey("answers.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QuestionNote(Base):
    __tablename__ = "question_notes"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    question_id: Mapped[UUID] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), unique=True, index=True
    )
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    question: Mapped[Question] = relationship(back_populates="note")


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    resume_asset_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL"), unique=True, nullable=True
    )
    current_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    extraction_status: Mapped[str] = mapped_column(
        String(32), default="EMPTY", server_default="EMPTY"
    )
    target_role: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProfileVersion(Base):
    __tablename__ = "profile_versions"
    __table_args__ = (
        UniqueConstraint("profile_id", "version", name="uq_profile_versions_profile_version"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_profiles.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    resume_asset_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("asset_versions.id", ondelete="SET NULL"), nullable=True
    )
    target_role: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    skills: Mapped[list["ProfileSkill"]] = relationship(
        back_populates="profile_version", cascade="all, delete-orphan"
    )


class ProfileSkill(Base):
    __tablename__ = "profile_skills"
    __table_args__ = (
        UniqueConstraint(
            "profile_version_id",
            "canonical_name",
            name="uq_profile_skills_version_canonical",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    profile_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("profile_versions.id", ondelete="CASCADE"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(120))
    canonical_name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(40))
    proficiency: Mapped[str] = mapped_column(
        String(24), default="UNSPECIFIED", server_default="UNSPECIFIED"
    )
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_status: Mapped[str] = mapped_column(
        String(24), default="PENDING", server_default="PENDING"
    )
    source: Mapped[str] = mapped_column(String(24), default="MODEL", server_default="MODEL")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    profile_version: Mapped[ProfileVersion] = relationship(back_populates="skills")


class PreferenceRule(Base):
    __tablename__ = "preference_rules"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    rule_type: Mapped[str] = mapped_column(String(16))
    technology: Mapped[str] = mapped_column(String(120))
    canonical_technology: Mapped[str] = mapped_column(String(120), index=True)
    category: Mapped[str] = mapped_column(String(40))
    scope: Mapped[str] = mapped_column(String(16), default="BOTH", server_default="BOTH")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RecommendationRun(Base):
    __tablename__ = "recommendation_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    profile_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("profile_versions.id", ondelete="SET NULL"), nullable=True
    )
    profile_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    profile_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    rule_snapshot: Mapped[list[dict]] = mapped_column(JSON, default=list, server_default="[]")
    rules_fingerprint: Mapped[str] = mapped_column(String(64))
    algorithm_version: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(24), default="SUCCEEDED", server_default="SUCCEEDED")
    total_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    included_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    excluded_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    answered_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    missing_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    items: Mapped[list["RecommendationItem"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class RecommendationItem(Base):
    __tablename__ = "recommendation_items"
    __table_args__ = (
        UniqueConstraint("run_id", "question_id", name="uq_recommendation_items_run_question"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("recommendation_runs.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("questions.id", ondelete="SET NULL"), index=True, nullable=True
    )
    collection_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("collections.id", ondelete="SET NULL"), nullable=True
    )
    position: Mapped[int] = mapped_column(Integer)
    question_version: Mapped[int] = mapped_column(Integer)
    answer_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt: Mapped[str] = mapped_column(Text)
    answer_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_source_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    decision: Mapped[str] = mapped_column(String(16))
    score: Mapped[float] = mapped_column(Float)
    answer_state: Mapped[str] = mapped_column(String(24))
    usable_answer_state: Mapped[str] = mapped_column(String(24))
    question_technologies: Mapped[list[dict]] = mapped_column(JSON, default=list, server_default="[]")
    answer_technologies: Mapped[list[dict]] = mapped_column(JSON, default=list, server_default="[]")
    reason_codes: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    run: Mapped[RecommendationRun] = relationship(back_populates="items")


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "scope", "idempotency_key", name="uq_idempotency_owner_scope_key"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    scope: Mapped[str] = mapped_column(String(80))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[UUID] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
