"""问答、答案和私人备注合约。"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, computed_field, field_validator

from app.schemas.common import APIModel


class QuestionCreate(APIModel):
    collection_id: UUID | None = None
    source_chunk_id: UUID | None = None
    prompt: str = Field(max_length=20_000)
    answer: str | None = Field(default=None, max_length=100_000)

    @field_validator("prompt")
    @classmethod
    def prompt_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("题目不能为空")
        return value.strip()


class VersionedContentUpdate(APIModel):
    content: str = Field(max_length=100_000)
    expected_version: int = Field(ge=0)


class QuestionUpdate(APIModel):
    prompt: str = Field(max_length=20_000)
    expected_version: int = Field(ge=1)

    @field_validator("prompt")
    @classmethod
    def prompt_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("题目不能为空")
        return value.strip()


class AnswerView(APIModel):
    id: UUID
    content: str
    source_type: Literal["USER_WRITTEN", "SOURCE_EXTRACTED"]
    version: int
    updated_at: datetime


class NoteView(APIModel):
    id: UUID
    content: str
    version: int
    updated_at: datetime


class QuestionView(APIModel):
    id: UUID
    collection_id: UUID | None
    source_chunk_id: UUID | None
    source_type: Literal["USER_WRITTEN", "SOURCE", "SOURCE_EXTRACTED", "SOURCE_REMOVED"]
    prompt: str
    version: int
    answer: AnswerView | None
    note: NoteView | None
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def answer_state(self) -> Literal["MISSING", "SOURCE_ANSWER"]:
        return "SOURCE_ANSWER" if self.answer is not None else "MISSING"


class QuestionPage(APIModel):
    items: list[QuestionView]
    page: int
    page_size: int
    total: int
    answered_count: int
    missing_count: int


class QuestionHistoryEntryView(APIModel):
    id: UUID
    question_id: UUID
    occurrence_id: UUID | None
    field: Literal["QUESTION", "ANSWER", "REVIEW_STATUS"]
    action: Literal["UPDATED", "DELETED", "RESTORED"]
    previous_value: str | None
    new_value: str | None
    entity_version: int | None
    created_at: datetime


class QuestionHistoryPage(APIModel):
    items: list[QuestionHistoryEntryView]


class QuestionHistoryRestore(APIModel):
    expected_version: int = Field(ge=0)
    expected_review_status: Literal["PENDING", "CONFIRMED", "REJECTED"] | None = None
