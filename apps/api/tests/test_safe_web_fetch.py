import socket

import pytest

from app.modules.safe_web import (
    FetchResponse,
    SafeWebError,
    fetch_public_document,
    resolve_public_addresses,
)


def _resolver_for(*addresses: str):
    def resolver(host: str, port: int, **kwargs):
        del host, kwargs
        return [
            (socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))
            for address in addresses
        ]

    return resolver


def test_dns_resolution_rejects_any_private_or_reserved_address() -> None:
    public = resolve_public_addresses(
        "example.com",
        443,
        resolver=_resolver_for("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"),
    )
    assert public == ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"]

    with pytest.raises(SafeWebError) as error:
        resolve_public_addresses(
            "rebind.example",
            443,
            resolver=_resolver_for("93.184.216.34", "127.0.0.1"),
        )
    assert error.value.code == "URL_PRIVATE_ADDRESS"


def test_fetch_pins_validated_ip_and_accepts_bounded_html() -> None:
    calls: list[tuple[str, str, int, str, str]] = []

    def request(
        scheme: str,
        host: str,
        port: int,
        ip: str,
        target: str,
        timeout: float,
        max_bytes: int,
    ):
        del timeout, max_bytes
        calls.append((scheme, host, port, ip, target))
        return FetchResponse(
            status=200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"<html><body><article>public interview notes</article></body></html>",
        )

    result = fetch_public_document(
        "https://example.com/posts/python?q=1",
        max_bytes=1024,
        timeout=5,
        resolver=_resolver_for("93.184.216.34"),
        requester=request,
    )
    assert result.content_type == "text/html"
    assert b"interview notes" in result.content
    assert calls == [
        ("https", "example.com", 443, "93.184.216.34", "/posts/python?q=1")
    ]


@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (
            FetchResponse(
                status=302,
                headers={"location": "http://127.0.0.1/private", "content-type": "text/html"},
                content=b"",
            ),
            "WEB_REDIRECT_BLOCKED",
        ),
        (
            FetchResponse(
                status=200,
                headers={"content-type": "application/octet-stream"},
                content=b"binary",
            ),
            "WEB_CONTENT_TYPE_UNSUPPORTED",
        ),
        (
            FetchResponse(
                status=200,
                headers={"content-type": "text/html", "content-length": "5000"},
                content=b"small body",
            ),
            "WEB_RESPONSE_TOO_LARGE",
        ),
    ],
)
def test_fetch_rejects_redirect_unsupported_type_and_oversized_response(
    response: FetchResponse,
    expected_code: str,
) -> None:
    with pytest.raises(SafeWebError) as error:
        fetch_public_document(
            "https://example.com/article",
            max_bytes=1024,
            timeout=5,
            resolver=_resolver_for("93.184.216.34"),
            requester=lambda *args: response,
        )
    assert error.value.code == expected_code
