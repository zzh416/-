"""问答、答案和私人备注合约。"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

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
    source_type: Literal["USER_WRITTEN"]
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
    source_type: Literal["USER_WRITTEN", "SOURCE", "SOURCE_REMOVED"]
    prompt: str
    version: int
    answer: AnswerView | None
    note: NoteView | None
    created_at: datetime
    updated_at: datetime


class QuestionPage(APIModel):
    items: list[QuestionView]
    page: int
    page_size: int
    total: int
