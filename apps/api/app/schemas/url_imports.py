from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from app.schemas.assets import AssetSummary
from app.schemas.common import APIModel


class UrlImportCreate(APIModel):
    # A 2048+ URL is a per-line validation error; bound the request itself too.
    urls: list[Annotated[str, Field(max_length=4096)]] = Field(min_length=1, max_length=50)


class UrlImportItemView(APIModel):
    ordinal: int
    input_url: str
    normalized_url: str | None
    disposition: Literal["CREATED", "REUSED", "DUPLICATE", "INVALID"]
    message: str | None
    duplicate_of: int | None
    asset: AssetSummary | None


class UrlImportBatchView(APIModel):
    id: UUID
    collection_id: UUID
    created_at: datetime
    items: list[UrlImportItemView]


class UrlImportPage(APIModel):
    items: list[UrlImportBatchView]
