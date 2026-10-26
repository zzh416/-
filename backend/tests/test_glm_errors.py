import httpx
import pytest

from app.adapters.glm_ocr import OCRServiceError, extract_with_glm_ocr
from app.adapters.glm_profile import extract_profile_with_glm
from app.adapters.glm_text import ExtractionBlock, TextModelError, extract_questions_with_glm


@pytest.mark.parametrize(
    ("extractor", "source", "error_type", "code_prefix"),
    [
        (extract_questions_with_glm, [ExtractionBlock(0, "问题")], TextModelError, "TEXT_MODEL"),
        (extract_profile_with_glm, "Python", TextModelError, "PROFILE_MODEL"),
        (extract_with_glm_ocr, b"\x89PNG\r\n\x1a\nimage", OCRServiceError, "OCR"),
    ],
    ids=["questions", "profile", "ocr"],
)
@pytest.mark.parametrize(
    ("body", "resource_exhausted"),
    [
        (b'{"error":{"code":"1113","message":"sensitive provider body"}}', True),
        (b'{"error":{"code":1113,"message":"sensitive provider body"}}', True),
        (b'{"error":{"code":"1302","message":"sensitive provider body"}}', False),
        (b'{"error":{"message":"sensitive provider body"}}', False),
        (b'{"error":null}', False),
        (b'[]', False),
        (b'{invalid-json', False),
        (b'\xff', False),
    ],
    ids=["quota-string", "quota-number", "rate-limit", "no-code", "null-error", "array", "invalid-json", "invalid-encoding"],
)
def test_glm_adapters_distinguish_resource_exhaustion_from_rate_limits(
    extractor,
    source,
    error_type,
    code_prefix: str,
    body: bytes,
    resource_exhausted: bool,
) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(429, content=body))
    with httpx.Client(transport=transport) as client:
        with pytest.raises(error_type) as error:
            extractor(
                source,
                api_key="test-key-not-secret",
                base_url="https://example.test/v4",
                model="glm-test",
                client=client,
            )

    suffix = "RESOURCE_EXHAUSTED" if resource_exhausted else "RATE_LIMITED"
    assert error.value.code == f"{code_prefix}_{suffix}"
    assert error.value.retryable is (not resource_exhausted)
    if resource_exhausted:
        assert str(error.value) == "GLM 账户余额不足或无可用资源包，请检查账户余额及资源包后重试"
    else:
        assert "限流" in str(error.value)
    assert "sensitive provider body" not in str(error.value)
    assert "test-key-not-secret" not in str(error.value)
