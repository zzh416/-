from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.common import APIModel
from app.schemas.sources import SourceStatistics


class InterviewCreate(APIModel):
    recommendation_run_id: UUID | None = None
    question_source: Literal["BANK", "AI", "HYBRID"] = "HYBRID"
    project_id: UUID | None = None
    question_count: int = Field(default=8, ge=3, le=12)
    question_seconds: int = Field(default=180, ge=60, le=900)
    mode: Literal["MOCK", "PREVIEW"] = "MOCK"

    @model_validator(mode="after")
    def bank_requires_run(self):
        if self.question_source != "AI" and self.recommendation_run_id is None:
            raise ValueError("题库和混合出题需要当前复习清单")
        return self


class InterviewVersion(APIModel):
    expected_version: int = Field(ge=1)
    turn_id: UUID | None = None


class InterviewAction(InterviewVersion):
    action: Literal["START", "PAUSE", "RESUME", "EXTEND", "NEXT", "FOLLOWUP", "FINISH", "USE_BANK", "RETRY_PLAN", "RETRY_REVIEW"]
    seconds: int = Field(default=60, ge=30, le=300)


class InterviewDraft(InterviewVersion):
    text: str = Field(default="", max_length=12_000)


class InterviewAnswer(InterviewDraft):
    action: Literal["ANSWER", "DONT_KNOW", "SKIP"] = "ANSWER"


class PlanItemView(APIModel):
    id: str
    prompt: str | None = None
    kind: str
    topics: list[str]
    reason: str = ""
    project_name: str | None = None
    project_quote: str | None = None
    skill_basis: str | None = None
    source_statistics: SourceStatistics | None = None


class FeedbackView(APIModel):
    summary: str
    covered_points: list[str]
    gaps: list[str]


class FollowupView(APIModel):
    prompt: str
    answer_quote: str
    project_quote: str | None = None
    question_quote: str | None = None


class TurnView(PlanItemView):
    root_id: str
    depth: int
    draft: str
    answer: str
    answer_status: Literal["DRAFT", "ANSWERED", "DONT_KNOW", "SKIPPED"]
    started_at: str
    deadline_at: str | None
    allocated_seconds: int
    remaining_seconds: float
    elapsed_seconds: int
    timed_out: bool
    feedback: FeedbackView | None = None
    followup: FollowupView | None = None
    review_error: str | None = None
    review_status: str
    review_attempts: int
    answer_quote: str | None = None
    question_quote: str | None = None


class InterviewView(APIModel):
    id: UUID
    version: int
    status: str
    server_now: datetime
    is_current: bool
    mode: str
    question_source: Literal["BANK", "AI", "HYBRID"] = "HYBRID"
    question_count: int
    question_seconds: int
    plan: list[PlanItemView]
    turns: list[TurnView]
    blind_topics: list[str]
    skipped_plan_count: int
    notice: str
    finished_reason: str | None = None
    planning_error: str | None = None
    model: str | None = None
    job_stage: str
    can_retry: bool
    created_at: datetime


class InterviewHistoryItem(APIModel):
    id: UUID
    status: str
    created_at: datetime


class InterviewHistory(APIModel):
    items: list[InterviewHistoryItem]
