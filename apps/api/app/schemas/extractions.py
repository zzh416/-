"""题答抽取任务与来源证据响应。"""
from datetime import datetime
from typing import Literal
from uuid import UUID

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
    review_status: Literal["PENDING", "CONFIRMED", "REJECTED"]
    created_at: datetime
