from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.common import APIModel
from app.schemas.profiles import ProfileProjectView
from app.schemas.sources import SourceStatistics


class FollowupCreate(APIModel):
    project_id: UUID
    seed_item_ids: list[UUID] = Field(min_length=1, max_length=3)

    @field_validator("seed_item_ids")
    @classmethod
    def unique_seeds(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("种子题不能重复")
        return value


class FollowupSeedView(APIModel):
    id: UUID
    question_id: UUID | None
    question_version: int
    prompt: str
    source_statistics: SourceStatistics | None = None


class FollowupItemView(APIModel):
    id: UUID
    parent_id: UUID | None
    seed_item_id: UUID
    level: int
    question: str
    project_quote: str
    seed_quote: str
    project_start: int
    project_end: int
    seed_start: int
    seed_end: int


class FollowupRunView(APIModel):
    id: UUID
    recommendation_run_id: UUID
    project_snapshot: ProfileProjectView
    seeds: list[FollowupSeedView]
    items: list[FollowupItemView]
    status: Literal["QUEUED", "PROCESSING", "SUCCEEDED", "PARTIAL_SUCCESS", "WAITING_FOR_CONFIGURATION", "FAILED"]
    algorithm_version: str
    provider: str | None
    model: str | None
    retry_count: int
    error_code: str | None
    error_message: str | None
    rejection_counts: dict[str, int]
    is_current: bool
    can_retry: bool
    created_at: datetime


class FollowupHistoryItem(APIModel):
    id: UUID
    status: str
    created_at: datetime
    recommendation_run_id: UUID


class FollowupHistoryPage(APIModel):
    items: list[FollowupHistoryItem]
