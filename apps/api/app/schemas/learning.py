from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field
from app.schemas.common import APIModel

LearningStatus = Literal["UNMARKED", "MASTERED", "WEAK", "UNKNOWN"]


class LearningLookup(APIModel):
    question_ids: list[UUID] = Field(min_length=1, max_length=100)


class LearningUpdate(APIModel):
    status: LearningStatus
    expected_version: int = Field(ge=0)
    question_version: int = Field(ge=1)
    answer_version: int | None
    ai_answer_id: UUID | None = None
    ai_answer_version: int | None = None


class LearningView(APIModel):
    id: UUID | None
    question_id: UUID | None
    source_kind: Literal["BANK", "INTERVIEW"]
    status: LearningStatus
    version: int
    question_version: int
    answer_version: int | None
    ai_answer_id: UUID | None = None
    ai_answer_version: int | None = None
    content_changed: bool
    updated_at: datetime | None


class LearningLookupPage(APIModel):
    items: list[LearningView]


class LearningItem(LearningView):
    prompt: str
    answer_content: str | None
    answer_source_type: str | None = None
    source_session_id: UUID | None
    source_turn_id: str | None


class LearningPage(APIModel):
    items: list[LearningItem]
    page: int
    page_size: int
    total: int
    excluded_count: int
    unknown_count: int
    weak_count: int
    mastered_count: int
    changed_count: int


class LearningCandidate(APIModel):
    turn_id: str
    prompt: str
    source_kind: Literal["BANK", "INTERVIEW"]
    confirmed: bool
    can_confirm: bool
    message: str | None
    learning: LearningView | None


class LearningCandidates(APIModel):
    items: list[LearningCandidate]
