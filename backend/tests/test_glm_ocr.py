import base64
import json

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
    payload = json.loads(str(captured["payload"]))
    assert payload["model"] == "glm-ocr"
    assert payload["file"].startswith("data:image/png;base64,")
    assert base64.b64decode(payload["file"].split(",", 1)[1]).startswith(b"\x89PNG")
    assert result.markdown.startswith("# Python 面经")
    assert result.page_count == 1


@pytest.mark.parametrize(
    ("content", "expected_prefix"),
    [
        (b"\xff\xd8\xffimage", "data:image/jpeg;base64,"),
        (b"%PDF-1.7\ncontent", "data:application/pdf;base64,"),
    ],
)
def test_glm_ocr_adds_media_type_to_supported_base64_files(
    content: bytes,
    expected_prefix: str,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.read())
        return httpx.Response(200, json={"md_results": "recognized"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        extract_with_glm_ocr(
            content,
            api_key="test-key-not-secret",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            model="glm-ocr",
            client=client,
        )

    assert str(captured["payload"]["file"]).startswith(expected_prefix)  # type: ignore[index]


def test_glm_ocr_rejects_unsupported_bytes_before_request() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: pytest.fail("unsupported files must not reach the OCR provider")
        )
    ) as client:
        with pytest.raises(OCRServiceError) as error:
            extract_with_glm_ocr(
                b"not-an-image",
                api_key="test-key-not-secret",
                base_url="https://open.bigmodel.cn/api/paas/v4",
                model="glm-ocr",
                client=client,
            )

    assert error.value.code == "OCR_FILE_TYPE_UNSUPPORTED"
    assert error.value.retryable is False


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
                b"\x89PNG\r\n\x1a\nimage",
                api_key="test-key-not-secret",
                base_url="https://open.bigmodel.cn/api/paas/v4",
                model="glm-ocr",
                client=client,
            )
    assert error.value.code == expected_code
    assert error.value.retryable is retryable
    assert "sensitive provider body" not in str(error.value)


def test_glm_ocr_explains_provider_file_rejection_without_exposing_arbitrary_body() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            400,
            json={
                "error": {
                    "code": "1214",
                    "message": "provider text that must not be copied directly",
                }
            },
        )
    )
    with httpx.Client(transport=transport) as client:
        with pytest.raises(OCRServiceError) as error:
            extract_with_glm_ocr(
                b"\x89PNG\r\n\x1a\nimage",
                api_key="test-key-not-secret",
                base_url="https://open.bigmodel.cn/api/paas/v4",
                model="glm-ocr",
                client=client,
            )

    assert error.value.code == "OCR_FILE_REJECTED"
    assert "PDF、JPG、PNG" in str(error.value)
    assert "provider text" not in str(error.value)
