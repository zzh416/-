"""OCR credentials and failures stay within the explicitly selected provider."""
import json
import sys
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.adapters import ocr_provider, text_provider
from app.adapters.glm_ocr import OCRNotConfigured, OCRResult, OCRServiceError
from app.adapters.glm_text import ExtractedPair, ExtractedSpan
from app.config import Settings


def _settings(**overrides) -> Settings:
    values = {
        "text_model_provider": "glm",
        "ocr_provider": "glm",
        "glm_api_key": "glm-test-secret",
        "glm_base_url": "https://glm.example.test/v4",
        "glm_ocr_model": "glm-test-ocr",
        "deepseek_api_key": "deepseek-test-secret",
        "deepseek_base_url": "https://deepseek.example.test",
        "deepseek_ocr_model": "deepseek-test-vision",
        "deepseek_ocr_max_output_tokens": 1234,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize("provider", ["glm", "deepseek"])
def test_ocr_routes_to_selected_provider_independently_of_text(monkeypatch, provider) -> None:
    configured = _settings(
        ocr_provider=provider,
        text_model_provider="deepseek" if provider == "glm" else "glm",
    )
    monkeypatch.setattr(ocr_provider, "settings", configured)
    result = OCRResult("问题原文", 1, "request-id")
    selected = Mock(return_value=result)
    unselected = Mock(side_effect=AssertionError("other provider must not be called"))
    monkeypatch.setattr(ocr_provider, f"extract_with_{provider}_ocr", selected)
    other = "glm" if provider == "deepseek" else "deepseek"
    monkeypatch.setattr(ocr_provider, f"extract_with_{other}_ocr", unselected)

    assert ocr_provider.extract_with_configured_ocr(b"image") is result
    expected = {
        "api_key": getattr(configured, f"{provider}_api_key"),
        "base_url": getattr(configured, f"{provider}_base_url"),
        "model": getattr(configured, f"{provider}_ocr_model"),
    }
    if provider == "deepseek":
        expected["max_output_tokens"] = 1234
    selected.assert_called_once_with(b"image", **expected)
    unselected.assert_not_called()


@pytest.mark.parametrize("provider", ["glm", "deepseek"])
@pytest.mark.parametrize("failure", ["missing_key", "resource_exhausted"])
def test_selected_ocr_failure_does_not_fall_back(monkeypatch, provider, failure) -> None:
    configured = _settings(ocr_provider=provider, **{f"{provider}_api_key": ""})
    monkeypatch.setattr(ocr_provider, "settings", configured)
    error = (
        OCRNotConfigured("selected key missing")
        if failure == "missing_key"
        else OCRServiceError("OCR_RESOURCE_EXHAUSTED", "selected balance exhausted", retryable=False)
    )
    selected = Mock(side_effect=error)
    other = "glm" if provider == "deepseek" else "deepseek"
    unselected = Mock(side_effect=AssertionError("must not fall back"))
    monkeypatch.setattr(ocr_provider, f"extract_with_{provider}_ocr", selected)
    monkeypatch.setattr(ocr_provider, f"extract_with_{other}_ocr", unselected)

    with pytest.raises(type(error)) as raised:
        ocr_provider.extract_with_configured_ocr(b"image")
    assert raised.value is error
    assert selected.call_args.kwargs["api_key"] == ""
    unselected.assert_not_called()


def test_ocr_defaults_remain_glm_with_separate_deepseek_vision_model(monkeypatch) -> None:
    for key in ("OCR_PROVIDER", "DEEPSEEK_OCR_MODEL", "DEEPSEEK_OCR_MAX_OUTPUT_TOKENS"):
        monkeypatch.delenv(key, raising=False)
    configured = Settings(_env_file=None)
    assert configured.ocr_provider == "glm"
    assert configured.deepseek_ocr_model == "deepseek-flash"
    assert configured.deepseek_ocr_max_output_tokens == 8192


@pytest.mark.parametrize(
    "overrides, field",
    [
        ({"ocr_provider": "unknown"}, "ocr_provider"),
        ({"deepseek_ocr_max_output_tokens": 0}, "deepseek_ocr_max_output_tokens"),
        ({"deepseek_ocr_max_output_tokens": 65_537}, "deepseek_ocr_max_output_tokens"),
    ],
)
def test_invalid_ocr_configuration_is_rejected(overrides, field) -> None:
    with pytest.raises(ValidationError, match=field):
        _settings(**overrides)


def test_image_quality_cli_uses_same_ocr_provider_as_ingestion(monkeypatch, tmp_path, capsys) -> None:
    from tools import evaluate_extraction_quality as evaluate

    configured = _settings(ocr_provider="deepseek", text_model_provider="glm")
    monkeypatch.setattr(ocr_provider, "settings", configured)
    monkeypatch.setattr(text_provider, "settings", configured)
    images = tmp_path / "images"
    images.mkdir()
    (images / "sample.png").write_bytes(b"synthetic-image")
    (images / "sample.json").write_text(json.dumps({
        "name": "OCR 选择测试",
        "file": "sample.png",
        "expected": [{"prompt": "什么是 RAG？", "answer": None}],
    }), encoding="utf-8")
    recognize = Mock(return_value=OCRResult("什么是 RAG？", 1, None))
    unused_glm = Mock(side_effect=AssertionError("OCR must use DeepSeek"))
    monkeypatch.setattr(ocr_provider, "extract_with_deepseek_ocr", recognize)
    monkeypatch.setattr(ocr_provider, "extract_with_glm_ocr", unused_glm)
    extract = Mock(return_value=[ExtractedPair(
        question=ExtractedSpan(block_ordinal=0, quote="什么是 RAG？"), confidence=1,
    )])
    monkeypatch.setattr(evaluate, "extract_questions_with_glm", extract)
    monkeypatch.setattr(sys, "argv", ["evaluate", "--samples", str(tmp_path), "--include-images"])

    assert evaluate.main() == 0
    recognize.assert_called_once()
    assert recognize.call_args.args == (b"synthetic-image",)
    assert extract.call_args.kwargs["provider"] == "glm"
    unused_glm.assert_not_called()
    assert "结论：通过" in capsys.readouterr().out
