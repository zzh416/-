import httpx
import pytest

from app.adapters.browser_renderer import (
    BrowserRenderError,
    render_public_page,
)


def test_browser_renderer_returns_bounded_rendered_html() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == "http://renderer:9100/render"
        assert request.headers["content-type"].startswith("application/json")
        return httpx.Response(
            200,
            json={
                "url": "https://example.com/article",
                "contentType": "text/html",
                "title": "Rendered article",
                "html": "<html><body><article>动态正文内容足够长，可以被解析。</article></body></html>",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = render_public_page(
            "https://example.com/article",
            service_url="http://renderer:9100",
            timeout_seconds=30,
            max_html_bytes=1024 * 1024,
            client=client,
        )

    assert result.url == "https://example.com/article"
    assert result.title == "Rendered article"
    assert "动态正文" in result.content.decode("utf-8")


@pytest.mark.parametrize(
    ("status", "payload", "expected_code"),
    [
        (422, {"code": "WEB_AUTH_REQUIRED", "message": "页面需要登录"}, "WEB_AUTH_REQUIRED"),
        (503, {"code": "RENDERER_BUSY", "message": "busy"}, "DYNAMIC_RENDER_UNAVAILABLE"),
    ],
)
def test_browser_renderer_maps_safe_errors(
    status: int,
    payload: dict[str, str],
    expected_code: str,
) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json=payload))
    ) as client:
        with pytest.raises(BrowserRenderError) as error:
            render_public_page(
                "https://example.com/article",
                service_url="http://renderer:9100",
                timeout_seconds=30,
                max_html_bytes=1024 * 1024,
                client=client,
            )

    assert error.value.code == expected_code
    assert "busy" not in str(error.value)


def test_browser_renderer_rejects_oversized_response() -> None:
    html = "x" * 2048
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "url": "https://example.com/article",
                    "contentType": "text/html",
                    "title": "Too large",
                    "html": html,
                },
            )
        )
    ) as client:
        with pytest.raises(BrowserRenderError) as error:
            render_public_page(
                "https://example.com/article",
                service_url="http://renderer:9100",
                timeout_seconds=30,
                max_html_bytes=1024,
                client=client,
            )

    assert error.value.code == "DYNAMIC_RENDER_TOO_LARGE"
