"""Text model selection stays explicit and independent from OCR."""
import pytest
from pydantic import ValidationError

from app.adapters import ocr_provider, text_provider
from app.adapters.glm_ocr import OCRResult
from app.config import Settings
from app.modules import ingestion_service


def _settings(**overrides) -> Settings:
    values = {
        "text_model_provider": "glm",
        "ocr_provider": "glm",
        "glm_api_key": "glm-test-secret",
        "glm_base_url": "https://glm.example.test/v4",
        "glm_text_model": "glm-test-text",
        "glm_ocr_model": "glm-test-ocr",
        "deepseek_api_key": "deepseek-test-secret",
        "deepseek_base_url": "https://deepseek.example.test",
        "deepseek_text_model": "deepseek-test-text",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize("provider", ["glm", "deepseek"])
def test_selected_provider_uses_its_own_credentials_and_model(monkeypatch, provider) -> None:
    configured = _settings(text_model_provider=provider)
    monkeypatch.setattr(text_provider, "settings", configured)

    result = text_provider.get_text_model_config()

    assert result.provider == provider
    assert result.api_key == getattr(configured, f"{provider}_api_key")
    assert result.base_url == getattr(configured, f"{provider}_base_url")
    assert result.model == getattr(configured, f"{provider}_text_model")
    assert result.api_key not in repr(result)


def test_default_provider_is_glm(monkeypatch) -> None:
    monkeypatch.delenv("TEXT_MODEL_PROVIDER", raising=False)
    assert Settings(_env_file=None).text_model_provider == "glm"


def test_invalid_provider_is_rejected() -> None:
    with pytest.raises(ValidationError, match="text_model_provider"):
        _settings(text_model_provider="unknown-provider")


def test_selected_provider_does_not_silently_fall_back_when_key_is_missing(monkeypatch) -> None:
    configured = _settings(text_model_provider="deepseek", deepseek_api_key="")
    monkeypatch.setattr(text_provider, "settings", configured)

    result = text_provider.get_text_model_config()

    assert result.provider == "deepseek"
    assert result.api_key == ""
    assert result.model == "deepseek-test-text"


def test_deepseek_selection_leaves_ocr_on_glm(monkeypatch) -> None:
    configured = _settings(text_model_provider="deepseek")
    monkeypatch.setattr(ingestion_service, "settings", configured)
    monkeypatch.setattr(ocr_provider, "settings", configured)

    def fake_ocr(content, **kwargs):
        assert content == b"image-data"
        assert kwargs == {
            "api_key": "glm-test-secret",
            "base_url": "https://glm.example.test/v4",
            "model": "glm-test-ocr",
        }
        return OCRResult("recognized text", 1, None)

    assert ingestion_service._ocr(b"image-data", fake_ocr).markdown == "recognized text"
