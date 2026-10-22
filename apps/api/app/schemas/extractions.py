"""题答抽取任务与来源证据响应。"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.common import APIModel

ExtractionStatus = Literal[
    "QUEUED",
    "PROCESSING",
    "SUCCEEDED",
    "PARTIAL_SUCCESS",
    "WAITING_FOR_CONFIGURATION",
    "FAILED",
]


class QuestionExtractionRunView(APIModel):
    id: UUID
    asset_id: UUID
    asset_version_id: UUID
    status: ExtractionStatus
    stage: str
    question_count: int
    answered_count: int
    missing_count: int
    retry_count: int
    model: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class AnswerSourceSpanView(APIModel):
    source_chunk_id: UUID
    block_ordinal: int
    start: int
    end: int
    quote: str


ReviewStatus = Literal["PENDING", "CONFIRMED", "REJECTED"]


class ExtractionReviewUpdate(APIModel):
    action: Literal["CONFIRM", "REJECT", "RESET"]
    expected_status: ReviewStatus


class MissedQuestionCreate(APIModel):
    source_chunk_id: UUID
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=20_000)
    prompt: str = Field(min_length=1, max_length=20_000)

    @model_validator(mode="after")
    def range_must_be_ordered(self) -> "MissedQuestionCreate":
        if self.end <= self.start:
            raise ValueError("原文结束位置必须大于开始位置")
        if not self.quote.strip() or not self.prompt.strip():
            raise ValueError("题目和原文引文不能为空")
        return self


class ExtractionAnswerView(APIModel):
    content: str
    source_type: Literal["USER_WRITTEN", "SOURCE_EXTRACTED"]
    version: int


class QuestionOccurrenceView(APIModel):
    id: UUID
    question_id: UUID
    extraction_run_id: UUID
    asset_version_id: UUID
    source_chunk_id: UUID | None
    answer_source_chunk_id: UUID | None
    question_start: int
    question_end: int
    answer_start: int | None
    answer_end: int | None
    question_quote: str
    answer_quote: str | None
    answer_spans: list[AnswerSourceSpanView]
    confidence: float
    review_status: ReviewStatus
    created_at: datetime


class ExtractionReviewItemView(APIModel):
    occurrence_id: UUID
    question_id: UUID
    question_version: int
    prompt: str
    answer: ExtractionAnswerView | None
    source_chunk_id: UUID | None
    source_block_ordinal: int | None
    source_locator: dict
    question_start: int
    question_end: int
    question_quote: str
    answer_spans: list[AnswerSourceSpanView]
    confidence: float
    review_status: ReviewStatus
    created_at: datetime


class SuspectedMissView(APIModel):
    source_chunk_id: UUID
    source_block_ordinal: int
    source_locator: dict
    start: int
    end: int
    quote: str
    reason: Literal["QUESTION_MARK", "HEADING", "QUESTION_PATTERN"]


class ExtractionReviewPage(APIModel):
    items: list[ExtractionReviewItemView]
    page: int
    page_size: int
    total: int
    pending_count: int
    confirmed_count: int
    rejected_count: int
    suspected_misses: list[SuspectedMissView]
