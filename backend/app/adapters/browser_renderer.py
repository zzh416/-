"""隔离浏览器渲染服务客户端。"""
from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

from app.modules.ingestion_validation import normalize_public_url


class BrowserRenderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class RenderedPage:
    url: str
    content_type: str
    content: bytes
    title: str | None


_SAFE_RENDER_ERRORS: dict[str, tuple[str, bool]] = {
    "WEB_AUTH_REQUIRED": ("该页面需要登录，当前链接导入不支持登录状态；可上传 PDF、HTML 或粘贴正文", False),
    "WEB_ACCESS_DENIED": ("网页拒绝匿名浏览器访问；可上传 PDF、HTML 或粘贴正文", False),
    "WEB_CONTENT_EMPTY": ("浏览器渲染后仍没有可用正文；请确认链接能直接打开目标文章", False),
    "WEB_PRIVATE_ADDRESS": ("网页脚本尝试访问本机、内网或保留地址，已停止解析", False),
    "DYNAMIC_RENDER_TIMEOUT": ("网页动态内容加载超时，请稍后重试", True),
    "DYNAMIC_RENDER_TOO_LARGE": ("网页渲染结果超过大小上限", False),
    "DYNAMIC_RENDER_REQUEST_LIMIT": ("网页加载的资源数量超过上限", False),
}


def render_public_page(
    url: str,
    *,
    service_url: str,
    timeout_seconds: float,
    max_html_bytes: int,
    client: httpx.Client | None = None,
) -> RenderedPage:
    endpoint = f"{service_url.rstrip('/')}/render"
    own_client = client is None
    active_client = client or httpx.Client(
        timeout=httpx.Timeout(timeout_seconds + 5.0, connect=5.0)
    )
    max_response_bytes = max_html_bytes * 2 + 1024 * 1024
    try:
        try:
            with active_client.stream(
                "POST",
                endpoint,
                json={"url": url, "maxHtmlBytes": max_html_bytes},
            ) as response:
                response_content = bytearray()
                for chunk in response.iter_bytes():
                    response_content.extend(chunk)
                    if len(response_content) > max_response_bytes:
                        raise BrowserRenderError(
                            "DYNAMIC_RENDER_TOO_LARGE",
                            "浏览器渲染服务响应超过大小上限",
                            retryable=False,
                        )
                status = response.status_code
        except BrowserRenderError:
            raise
        except httpx.HTTPError as exc:
            raise BrowserRenderError(
                "DYNAMIC_RENDER_UNAVAILABLE",
                "动态网页渲染服务暂时不可用，请稍后重试",
                retryable=True,
            ) from exc

        try:
            payload = json.loads(response_content)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise BrowserRenderError(
                "DYNAMIC_RENDER_INVALID_RESPONSE",
                "动态网页渲染服务返回了无效结果",
                retryable=True,
            ) from exc

        if status >= 400:
            code = payload.get("code") if isinstance(payload, dict) else None
            if isinstance(code, str) and code in _SAFE_RENDER_ERRORS:
                message, retryable = _SAFE_RENDER_ERRORS[code]
                raise BrowserRenderError(code, message, retryable=retryable)
            raise BrowserRenderError(
                "DYNAMIC_RENDER_UNAVAILABLE",
                "动态网页渲染服务暂时不可用，请稍后重试",
                retryable=True,
            )

        try:
            rendered_url = normalize_public_url(payload["url"])
            html = payload["html"]
        except (KeyError, TypeError, ValueError) as exc:
            raise BrowserRenderError(
                "DYNAMIC_RENDER_INVALID_RESPONSE",
                "动态网页渲染服务返回了无效结果",
                retryable=True,
            ) from exc
        if not isinstance(html, str) or not html.strip():
            raise BrowserRenderError(
                "WEB_CONTENT_EMPTY",
                _SAFE_RENDER_ERRORS["WEB_CONTENT_EMPTY"][0],
                retryable=False,
            )
        content = html.encode("utf-8")
        if len(content) > max_html_bytes:
            raise BrowserRenderError(
                "DYNAMIC_RENDER_TOO_LARGE",
                _SAFE_RENDER_ERRORS["DYNAMIC_RENDER_TOO_LARGE"][0],
                retryable=False,
            )
        title = payload.get("title")
        if not isinstance(title, str) or not title.strip():
            title = None
        else:
            title = " ".join(title.split())[:200]
        return RenderedPage(
            url=rendered_url,
            content_type="text/html",
            content=content,
            title=title,
        )
    finally:
        if own_client:
            active_client.close()
