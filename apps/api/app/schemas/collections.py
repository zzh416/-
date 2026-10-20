"""资料库请求与响应。"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.common import APIModel

CollectionPurpose = Literal["INTERVIEW_EXPERIENCE", "KNOWLEDGE_BASE"]


class CollectionCreate(APIModel):
    name: str = Field(min_length=1, max_length=120)
    purpose: CollectionPurpose

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("资料库名称不能为空")
        return normalized


class CollectionView(APIModel):
    id: UUID
    name: str
    purpose: CollectionPurpose
    version: int
    created_at: datetime
    updated_at: datetime


class CollectionPage(APIModel):
    items: list[CollectionView]
