"""Resume profile, skill snapshot, and preference rule contracts."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.assets import AssetSummary
from app.schemas.common import APIModel

ProfileExtractionStatus = Literal[
    "EMPTY",
    "IDLE",
    "PROCESSING",
    "SUCCEEDED",
    "PARTIAL_SUCCESS",
    "WAITING_FOR_CONFIGURATION",
    "FAILED",
]
SkillProficiency = Literal["UNSPECIFIED", "BEGINNER", "INTERMEDIATE", "ADVANCED"]
SkillReviewStatus = Literal["PENDING", "CONFIRMED", "REJECTED"]
PreferenceRuleType = Literal["INCLUDE", "EXCLUDE"]
PreferenceScope = Literal["QUESTION", "ANSWER", "BOTH"]


class ProfileSkillView(APIModel):
    id: UUID
    display_name: str
    canonical_name: str
    category: str
    proficiency: SkillProficiency
    evidence: str | None
    review_status: SkillReviewStatus
    source: Literal["MODEL", "USER"]


class ProfileView(APIModel):
    id: UUID
    version: int
    current_version: int
    extraction_status: ProfileExtractionStatus
    target_role: str | None
    model: str | None
    error_code: str | None
    error_message: str | None
    resume: AssetSummary | None
    skills: list[ProfileSkillView]
    created_at: datetime
    updated_at: datetime


class ProfileTargetRoleUpdate(APIModel):
    target_role: str | None = Field(default=None, max_length=200)
    expected_version: int = Field(ge=1)

    @field_validator("target_role")
    @classmethod
    def normalize_target_role(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None


class ProfileSkillCreate(APIModel):
    display_name: str = Field(min_length=1, max_length=120)
    proficiency: SkillProficiency = "UNSPECIFIED"
    expected_version: int = Field(ge=1)

    @field_validator("display_name")
    @classmethod
    def skill_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("技能名称不能为空")
        return value.strip()


class ProfileSkillUpdate(APIModel):
    display_name: str = Field(min_length=1, max_length=120)
    proficiency: SkillProficiency
    review_status: SkillReviewStatus
    expected_version: int = Field(ge=1)

    @field_validator("display_name")
    @classmethod
    def skill_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("技能名称不能为空")
        return value.strip()


class TechnologyView(APIModel):
    canonical_name: str
    display_name: str
    category: str
    aliases: list[str]
    parent: str | None


class PreferenceRuleCreate(APIModel):
    rule_type: PreferenceRuleType
    technology: str = Field(min_length=1, max_length=120)
    scope: PreferenceScope = "BOTH"

    @field_validator("technology")
    @classmethod
    def technology_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("技术名称不能为空")
        return value.strip()


class PreferenceRuleUpdate(PreferenceRuleCreate):
    is_active: bool
    expected_version: int = Field(ge=1)


class PreferenceRuleView(APIModel):
    id: UUID
    rule_type: PreferenceRuleType
    technology: str
    canonical_technology: str
    category: str
    scope: PreferenceScope
    is_active: bool
    version: int
    created_at: datetime
    updated_at: datetime


class PreferenceRulePage(APIModel):
    items: list[PreferenceRuleView]
