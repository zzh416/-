"""文字资料导入与版本响应。"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.common import APIModel

SourceFormat = Literal["PASTED_TEXT", "MARKDOWN", "TXT_FILE", "MARKDOWN_FILE"]


class AssetTextCreate(APIModel):
    title: str = Field(min_length=1, max_length=200)
    content: str
    source_format: Literal["PASTED_TEXT", "MARKDOWN"] = "PASTED_TEXT"

    @field_validator("title", "content")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("内容不能为空")
        return value.strip() if len(value) <= 200 else value


class AssetContentUpdate(APIModel):
    content: str
    expected_version: int = Field(ge=1)

    @field_validator("content")
    @classmethod
    def content_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("内容不能为空")
        return value


class AssetSummary(APIModel):
    id: UUID
    collection_id: UUID
    title: str
    source_format: SourceFormat
    original_filename: str | None
    status: Literal["READY"]
    current_version: int
    created_at: datetime
    updated_at: datetime


class AssetVersionView(APIModel):
    version: int
    content: str
    created_at: datetime


class TextChunkView(APIModel):
    id: UUID
    ordinal: int
    content: str
    start_offset: int
    end_offset: int


class AssetDetail(AssetSummary):
    content: str
    versions: list[AssetVersionView]
    chunks: list[TextChunkView]


class AssetPage(APIModel):
    items: list[AssetSummary]
