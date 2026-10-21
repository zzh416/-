"""文字资料导入与版本响应。"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.common import APIModel

SourceFormat = Literal[
    "PASTED_TEXT",
    "MARKDOWN",
    "TXT_FILE",
    "MARKDOWN_FILE",
    "IMAGE",
    "PDF",
    "DOCX",
    "DOC",
    "WEB_PAGE",
]
AssetStatus = Literal[
    "QUEUED",
    "PROCESSING",
    "READY",
    "PARTIAL_SUCCESS",
    "WAITING_FOR_CONFIGURATION",
    "FAILED",
]
JobStatus = Literal[
    "QUEUED",
    "PROCESSING",
    "SUCCEEDED",
    "PARTIAL_SUCCESS",
    "WAITING_FOR_CONFIGURATION",
    "FAILED",
]


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


class AssetUrlCreate(APIModel):
    url: str = Field(min_length=8, max_length=2048)
    title: str | None = Field(default=None, max_length=200)

    @field_validator("url")
    @classmethod
    def url_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("网页地址不能为空")
        return value.strip()

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("标题不能为空")
        return value.strip()


class IngestionJobView(APIModel):
    id: UUID
    status: JobStatus
    stage: str
    processed_units: int
    total_units: int | None
    retry_count: int
    error_code: str | None
    error_message: str | None
    unit_failures: list[dict]
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class AssetSummary(APIModel):
    id: UUID
    collection_id: UUID
    title: str
    source_format: SourceFormat
    original_filename: str | None
    source_url: str | None
    content_type: str | None
    byte_size: int | None
    fetched_at: datetime | None
    status: AssetStatus
    current_version: int
    job: IngestionJobView | None = None
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
    block_type: str
    locator: dict


class AssetDetail(AssetSummary):
    content: str
    versions: list[AssetVersionView]
    chunks: list[TextChunkView]


class AssetPage(APIModel):
    items: list[AssetSummary]
