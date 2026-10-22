"""Versioned, explainable personalized review list contracts."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from app.schemas.common import APIModel


class RecommendationRunCreate(APIModel):
    pass


class TechnologyMatchView(APIModel):
    canonical_name: str
    display_name: str
    category: str


class RecommendationSkillSnapshot(APIModel):
    canonical_name: str
    display_name: str
    category: str
    review_status: str


class RecommendationProfileSnapshot(APIModel):
    target_role: str | None
    skills: list[RecommendationSkillSnapshot]


class RecommendationRuleSnapshot(APIModel):
    id: UUID
    rule_type: Literal["INCLUDE", "EXCLUDE"]
    technology: str
    canonical_technology: str
    category: str
    scope: Literal["QUESTION", "ANSWER", "BOTH"]
    version: int


class RecommendationRunView(APIModel):
    id: UUID
    status: Literal["SUCCEEDED"]
    algorithm_version: str
    profile_version: int
    profile_snapshot: RecommendationProfileSnapshot
    rule_snapshot: list[RecommendationRuleSnapshot]
    is_current: bool
    total_count: int
    included_count: int
    excluded_count: int
    answered_count: int
    missing_count: int
    created_at: datetime


class RecommendationItemView(APIModel):
    id: UUID
    question_id: UUID | None
    collection_id: UUID | None
    position: int
    question_version: int
    answer_version: int | None
    prompt: str
    answer_content: str | None
    answer_source_type: str | None
    decision: Literal["INCLUDED", "EXCLUDED"]
    score: float
    answer_state: Literal["MISSING", "SOURCE_ANSWER"]
    usable_answer_state: Literal["MISSING", "ANSWERED"]
    question_technologies: list[TechnologyMatchView]
    answer_technologies: list[TechnologyMatchView]
    reason_codes: list[str]
    reasons: list[str]


class RecommendationItemPage(APIModel):
    items: list[RecommendationItemView]
    page: int
    page_size: int
    total: int
    included_count: int
    excluded_count: int
    answered_count: int
    missing_count: int
