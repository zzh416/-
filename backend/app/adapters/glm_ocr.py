"""智谱 GLM-OCR 官方 layout_parsing 接口适配器。"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass

import httpx

from app.adapters.glm_errors import RESOURCE_EXHAUSTED_MESSAGE, provider_error_code


class OCRNotConfigured(RuntimeError):
    """未配置 OCR 服务密钥。"""


class OCRServiceError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class OCRResult:
    markdown: str
    page_count: int | None
    provider_task_id: str | None


def _file_data_url(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        media_type = "image/png"
    elif content.startswith(b"\xff\xd8\xff"):
        media_type = "image/jpeg"
    elif content.startswith(b"%PDF-"):
        media_type = "application/pdf"
    else:
        raise OCRServiceError(
            "OCR_FILE_TYPE_UNSUPPORTED",
            "OCR 仅支持 PDF、JPG、PNG 文件",
            retryable=False,
        )
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def extract_with_glm_ocr(
    content: bytes,
    *,
    api_key: str,
    base_url: str,
    model: str,
    client: httpx.Client | None = None,
) -> OCRResult:
    if not api_key.strip():
        raise OCRNotConfigured("尚未配置 GLM_API_KEY")
    file_data = _file_data_url(content)
    endpoint = f"{base_url.rstrip('/')}/layout_parsing"
    own_client = client is None
    active_client = client or httpx.Client(timeout=httpx.Timeout(120.0, connect=20.0))
    try:
        try:
            with active_client.stream(
                "POST",
                endpoint,
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "file": file_data,
                    "return_crop_images": False,
                    "need_layout_visualization": False,
                },
            ) as response:
                response_content = bytearray()
                for chunk in response.iter_bytes():
                    response_content.extend(chunk)
                    if len(response_content) > 10 * 1024 * 1024:
                        raise OCRServiceError(
                            "OCR_RESPONSE_TOO_LARGE",
                            "OCR 服务响应超过大小上限",
                            retryable=False,
                        )
                status = response.status_code
        except OCRServiceError:
            raise
        except httpx.HTTPError as exc:
            raise OCRServiceError(
                "OCR_SERVICE_UNAVAILABLE",
                "OCR 服务连接失败，请稍后重试",
                retryable=True,
            ) from exc

        if status in {401, 403}:
            raise OCRServiceError("OCR_AUTH_FAILED", "OCR API Key 无效或无权限", retryable=False)
        if status == 429:
            if provider_error_code(response_content) == "1113":
                raise OCRServiceError(
                    "OCR_RESOURCE_EXHAUSTED", RESOURCE_EXHAUSTED_MESSAGE, retryable=False
                )
            raise OCRServiceError("OCR_RATE_LIMITED", "OCR 服务限流，请稍后重试", retryable=True)
        if status >= 500:
            raise OCRServiceError(
                "OCR_SERVICE_UNAVAILABLE",
                "OCR 服务暂时不可用，请稍后重试",
                retryable=True,
            )
        if status >= 400:
            if provider_error_code(response_content) == "1214":
                raise OCRServiceError(
                    "OCR_FILE_REJECTED",
                    "OCR 仅支持 PDF、JPG、PNG；图片不超过 10 MB，PDF 不超过 50 MB 且最多 100 页",
                    retryable=False,
                )
            raise OCRServiceError("OCR_REQUEST_REJECTED", "OCR 服务拒绝了该文件", retryable=False)

        try:
            payload = json.loads(response_content)
            markdown = payload["md_results"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise OCRServiceError(
                "OCR_INVALID_RESPONSE",
                "OCR 服务返回了无法识别的结果",
                retryable=False,
            ) from exc
        if not isinstance(markdown, str) or not markdown.strip():
            raise OCRServiceError(
                "OCR_EMPTY_RESULT",
                "OCR 未识别到可用文字",
                retryable=False,
            )
        data_info = payload.get("data_info")
        page_count = data_info.get("num_pages") if isinstance(data_info, dict) else None
        if not isinstance(page_count, int):
            page_count = None
        task_id = payload.get("id")
        if not isinstance(task_id, str):
            task_id = None
        return OCRResult(
            markdown=markdown.strip(),
            page_count=page_count,
            provider_task_id=task_id,
        )
    finally:
        if own_client:
            active_client.close()
