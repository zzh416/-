import httpx
import pytest

from app.adapters.glm_ocr import OCRNotConfigured, OCRServiceError, extract_with_glm_ocr


def test_glm_ocr_uses_official_layout_endpoint_and_validates_markdown() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={
                "id": "task_123",
                "model": "GLM-OCR",
                "md_results": "# Python 面经\n\n什么是 GIL？",
                "layout_details": [],
                "data_info": {"num_pages": 1, "pages": [{"width": 1, "height": 1}]},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = extract_with_glm_ocr(
            b"\x89PNG\r\n\x1a\nimage",
            api_key="test-key-not-secret",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            model="glm-ocr",
            client=client,
        )

    assert captured["url"] == "https://open.bigmodel.cn/api/paas/v4/layout_parsing"
    assert captured["authorization"] == "Bearer test-key-not-secret"
    assert '"model":"glm-ocr"' in str(captured["payload"])
    assert result.markdown.startswith("# Python 面经")
    assert result.page_count == 1


def test_glm_ocr_requires_key_without_sending_request() -> None:
    with pytest.raises(OCRNotConfigured):
        extract_with_glm_ocr(
            b"image",
            api_key="",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            model="glm-ocr",
        )


@pytest.mark.parametrize(
    ("status", "expected_code", "retryable"),
    [
        (401, "OCR_AUTH_FAILED", False),
        (429, "OCR_RATE_LIMITED", True),
        (503, "OCR_SERVICE_UNAVAILABLE", True),
    ],
)
def test_glm_ocr_maps_provider_errors_without_exposing_body(
    status: int,
    expected_code: str,
    retryable: bool,
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status, json={"error": {"message": "sensitive provider body"}})
    )
    with httpx.Client(transport=transport) as client:
        with pytest.raises(OCRServiceError) as error:
            extract_with_glm_ocr(
                b"image",
                api_key="test-key-not-secret",
                base_url="https://open.bigmodel.cn/api/paas/v4",
                model="glm-ocr",
                client=client,
            )
    assert error.value.code == expected_code
    assert error.value.retryable is retryable
    assert "sensitive provider body" not in str(error.value)
