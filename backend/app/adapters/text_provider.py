"""Resolve the explicitly selected text model without changing OCR settings."""
from dataclasses import dataclass, field
from typing import Literal

from app.config import settings


@dataclass(frozen=True)
class TextModelConfig:
    provider: Literal["glm", "deepseek"]
    api_key: str = field(repr=False)
    base_url: str
    model: str


def get_text_model_config() -> TextModelConfig:
    if settings.text_model_provider == "deepseek":
        return TextModelConfig(
            provider="deepseek",
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_text_model,
        )
    return TextModelConfig(
        provider="glm",
        api_key=settings.glm_api_key,
        base_url=settings.glm_base_url,
        model=settings.glm_text_model,
    )
