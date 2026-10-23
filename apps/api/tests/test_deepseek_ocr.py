import base64
import json

import httpx
import pytest

from app.adapters.deepseek_ocr import extract_with_deepseek_ocr
from app.adapters.glm_ocr import OCRNotConfigured, OCRServiceError


PNG = b"\x89PNG\r\n\x1a\nimage"


def _response(content='{"markdown":"什么是 RAG？\\n\\n只转录原文。"}', reason="stop"):
    return {"id": "ocr-test", "choices": [{"finish_reason": reason, "message": {"content": content}}]}


def _extract(client, content=PNG, **kwargs):
    return extract_with_deepseek_ocr(
        content, api_key="test-key", base_url="https://api.deepseek.com",
        model="deepseek-flash", client=client, **kwargs,
    )


@pytest.mark.parametrize("content,mime", [(PNG, "image/png"), (b"\xff\xd8\xffimage", "image/jpeg")])
def test_vision_sends_image_and_returns_only_transcribed_markdown(content, mime):
    def handler(request):
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == "deepseek-flash"
        assert body["thinking"] == {"type": "disabled"}
        assert body["response_format"] == {"type": "json_object"}
        assert body["max_tokens"] == 8192
        user = body["messages"][-1]
        assert user["role"] == "user"
        image = next(part["image_url"] for part in user["content"] if part["type"] == "image_url")
        assert image["detail"] == "original"
        prefix, data = image["url"].split(",", 1)
        assert prefix == f"data:{mime};base64"
        assert base64.b64decode(data) == content
        return httpx.Response(200, json=_response())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = _extract(client, content)
    assert result.markdown == "什么是 RAG？\n\n只转录原文。"
    assert result.page_count == 1
    assert result.provider_task_id == "ocr-test"


@pytest.mark.parametrize("payload,code", [
    (_response(reason="length"), "OCR_OUTPUT_TRUNCATED"),
    (_response(reason="content_filter"), "OCR_REQUEST_REJECTED"),
    (_response(reason=None), "OCR_INVALID_RESPONSE"),
    (_response(content=None), "OCR_INVALID_RESPONSE"),
    (_response(content='{"markdown":123}'), "OCR_INVALID_RESPONSE"),
    (_response(content='{"markdown":"   "}'), "OCR_EMPTY_RESULT"),
    (_response(content='{"answer":"模型补写的答案"}'), "OCR_INVALID_RESPONSE"),
    (_response(content="不是 JSON"), "OCR_INVALID_RESPONSE"),
    ({"choices": []}, "OCR_INVALID_RESPONSE"),
])
def test_incomplete_or_invalid_output_never_becomes_source_text(payload, code):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
        with pytest.raises(OCRServiceError) as error:
            _extract(client)
    assert error.value.code == code


@pytest.mark.parametrize("status,code,retryable", [
    (400, "OCR_REQUEST_REJECTED", False),
    (401, "OCR_AUTH_FAILED", False),
    (402, "OCR_RESOURCE_EXHAUSTED", False),
    (403, "OCR_AUTH_FAILED", False),
    (429, "OCR_RATE_LIMITED", True),
    (503, "OCR_SERVICE_UNAVAILABLE", True),
])
def test_provider_errors_do_not_expose_keys_or_response(status, code, retryable):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
        status, json={"error": {"code": "1113", "message": "private-content test-key"}}
    ))) as client:
        with pytest.raises(OCRServiceError) as error:
            _extract(client)
    assert error.value.code == code
    assert error.value.retryable is retryable
    assert "private-content" not in str(error.value)
    assert "test-key" not in str(error.value)


def test_missing_key_rejects_without_network():
    with pytest.raises(OCRNotConfigured, match="DEEPSEEK_API_KEY"):
        extract_with_deepseek_ocr(PNG, api_key="", base_url="https://api.deepseek.com", model="deepseek-flash")


@pytest.mark.parametrize("content,code", [
    (b"unsupported", "OCR_FILE_TYPE_UNSUPPORTED"),
    (PNG + b"x" * (10 * 1024 * 1024), "OCR_FILE_TOO_LARGE"),
])
def test_unsupported_or_oversize_image_rejects_before_network(content, code):
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("must not send"))) as client:
        with pytest.raises(OCRServiceError) as error:
            _extract(client, content)
    assert error.value.code == code


def test_response_size_is_bounded():
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b" " * (2 * 1024 * 1024 + 1)))) as client:
        with pytest.raises(OCRServiceError, match="大小上限") as error:
            _extract(client)
    assert error.value.code == "OCR_RESPONSE_TOO_LARGE"


def test_network_failure_is_retryable():
    def fail(request):
        raise httpx.ReadTimeout("private-content", request=request)

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(OCRServiceError) as error:
            _extract(client)
    assert error.value.code == "OCR_SERVICE_UNAVAILABLE"
    assert error.value.retryable
    assert "private-content" not in str(error.value)


def test_pdf_ocr_converts_single_page_to_image(monkeypatch):
    monkeypatch.setattr("app.adapters.deepseek_ocr.render_ocr_pdf_page", lambda content: PNG)

    def handler(request):
        body = json.loads(request.content)
        image = body["messages"][-1]["content"][-1]["image_url"]["url"]
        assert image.startswith("data:image/png;base64,")
        return httpx.Response(200, json=_response())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert _extract(client, b"%PDF-single-page").page_count == 1
