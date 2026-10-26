from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.common import APIModel


class AIAnswerCreate(APIModel):
    expected_question_version: int = Field(ge=1)
    use_profile: bool = True
    include_note: bool = False


class AIAnswerVersion(APIModel):
    expected_version: int = Field(ge=1)


class AIAnswerReview(AIAnswerVersion):
    action: Literal["SAVE_DRAFT", "CONFIRM", "DISCARD"]
    content: str | None = Field(default=None, min_length=1, max_length=12_000)

    @field_validator("content")
    @classmethod
    def nonempty(cls, value):
        if value is not None and not value.strip():
            raise ValueError("答案内容不能为空")
        return value.strip() if value is not None else None


class AIAnswerCitation(APIModel):
    chunk_id: UUID
    quote: str


class AIAnswerView(APIModel):
    id: UUID
    question_id: UUID
    question_version: int
    version: int
    status: Literal["QUEUED", "PROCESSING", "SUCCEEDED", "FAILED"]
    review_status: Literal["DRAFT", "CONFIRMED", "DISCARDED"]
    source_type: Literal["AI_GENERATED"] = "AI_GENERATED"
    content: str | None
    basis: Literal["GENERAL_KNOWLEDGE", "SOURCE_ASSISTED"] | None
    citations: list[AIAnswerCitation]
    provider: str | None
    model: str | None
    used_profile: bool
    used_note: bool
    is_current: bool
    retry_count: int
    retry_limit: int = 3
    can_retry: bool = False
    error_code: str | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None


class EffectiveAnswerView(APIModel):
    content: str
    source_type: Literal["USER_WRITTEN", "SOURCE_EXTRACTED", "AI_GENERATED"]
    version: int
    ai_answer_id: UUID | None = None
