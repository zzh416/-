"""Source counts describe this library, never a probability of being interviewed."""
from datetime import date
from uuid import UUID

from pydantic import Field

from app.schemas.common import APIModel


class QuestionSourceView(APIModel):
    asset_id: UUID
    title: str
    source_url: str | None
    purpose: str
    source_date: date | None
    duplicate_asset_count: int


class SourceStatistics(APIModel):
    source_count: int = 0
    interview_source_count: int = 0
    knowledge_source_count: int = 0
    occurrence_count: int = 0
    recent_interview_source_count: int = 0
    unknown_date_source_count: int = 0
    latest_source_date: date | None = None
    sources: list[QuestionSourceView] = Field(default_factory=list)
