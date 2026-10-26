"""Route OCR explicitly, independently from the text model selection."""
from __future__ import annotations

from typing import Callable

from app.adapters.deepseek_ocr import extract_with_deepseek_ocr
from app.adapters.glm_ocr import OCRResult, extract_with_glm_ocr
from app.config import settings


def extract_with_configured_ocr(
    content: bytes,
    *,
    extractor: Callable[..., OCRResult] | None = None,
) -> OCRResult:
    if settings.ocr_provider == "deepseek":
        selected = extractor or extract_with_deepseek_ocr
        return selected(
            content,
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_ocr_model,
            max_output_tokens=settings.deepseek_ocr_max_output_tokens,
        )
    selected = extractor or extract_with_glm_ocr
    return selected(
        content,
        api_key=settings.glm_api_key,
        base_url=settings.glm_base_url,
        model=settings.glm_ocr_model,
    )
