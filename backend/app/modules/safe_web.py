"""将用户 URL 固定到已验证公网 IP 的受限网页获取器。"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

from app.modules.ingestion_validation import normalize_public_url


class SafeWebError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class FetchResponse:
    status: int
    headers: dict[str, str]
    content: bytes


@dataclass(frozen=True)
class FetchedDocument:
    url: str
    content_type: str
    content: bytes


Resolver = Callable[..., list[tuple]]
Requester = Callable[[str, str, int, str, str, float, int], FetchResponse]


def resolve_public_addresses(
    hostname: str,
    port: int,
    *,
    resolver: Resolver = socket.getaddrinfo,
) -> list[str]:
    try:
        records = resolver(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise SafeWebError("URL_DNS_FAILED", "网页域名无法解析") from exc
    addresses: list[str] = []
    for record in records:
        address = str(record[4][0])
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise SafeWebError("URL_DNS_FAILED", "网页域名返回了无效地址") from exc
        if not parsed.is_global:
            raise SafeWebError("URL_PRIVATE_ADDRESS", "网页地址不能指向本机、内网或保留地址")
        normalized = parsed.compressed
        if normalized not in addresses:
            addresses.append(normalized)
    if not addresses:
        raise SafeWebError("URL_DNS_FAILED", "网页域名没有可用地址")
    return addresses


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, ip: str, timeout: float) -> None:
        super().__init__(host=host, port=port, timeout=timeout)
        self._pinned_ip = ip

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._pinned_ip, self.port),
            self.timeout,
            self.source_address,
        )
        if self._tunnel_host:
            self._tunnel()


class _PinnedHTTPSConnection(_PinnedHTTPConnection):
    def __init__(self, host: str, port: int, ip: str, timeout: float) -> None:
        super().__init__(host, port, ip, timeout)
        self._context = ssl.create_default_context()

    def connect(self) -> None:
        super().connect()
        assert self.sock is not None
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def _request_pinned(
    scheme: str,
    host: str,
    port: int,
    ip: str,
    target: str,
    timeout: float,
    max_bytes: int,
) -> FetchResponse:
    connection_type = _PinnedHTTPSConnection if scheme == "https" else _PinnedHTTPConnection
    connection = connection_type(host, port, ip, timeout)
    default_port = 443 if scheme == "https" else 80
    host_header = host if port == default_port else f"{host}:{port}"
    try:
        connection.request(
            "GET",
            target,
            headers={
                "Host": host_header,
                "User-Agent": "InterviewKnowledgeAssistant/0.2",
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8",
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        declared_length = headers.get("content-length")
        if declared_length and declared_length.isdigit() and int(declared_length) > max_bytes:
            content = b""
        else:
            content = response.read(max_bytes + 1)
        return FetchResponse(status=response.status, headers=headers, content=content)
    finally:
        connection.close()


def fetch_public_document(
    value: str,
    *,
    max_bytes: int,
    timeout: float,
    resolver: Resolver = socket.getaddrinfo,
    requester: Requester = _request_pinned,
) -> FetchedDocument:
    try:
        normalized_url = normalize_public_url(value)
    except ValueError as exc:
        raise SafeWebError("URL_INVALID", str(exc)) from exc
    parsed = urlsplit(normalized_url)
    host = parsed.hostname
    if host is None:
        raise SafeWebError("URL_INVALID", "网页地址缺少域名")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = resolve_public_addresses(host, port, resolver=resolver)
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"

    last_error: OSError | None = None
    response: FetchResponse | None = None
    for address in addresses:
        try:
            response = requester(
                parsed.scheme,
                host,
                port,
                address,
                target,
                timeout,
                max_bytes,
            )
            break
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            last_error = exc
    if response is None:
        raise SafeWebError("WEB_FETCH_FAILED", "网页连接失败，请检查地址后重试") from last_error

    if 300 <= response.status < 400:
        raise SafeWebError(
            "WEB_REDIRECT_BLOCKED",
            "网页发生重定向；为防止访问内网，请使用跳转后的最终公开地址",
        )
    if response.status in {401, 403}:
        raise SafeWebError("WEB_ACCESS_DENIED", "网页需要登录或拒绝访问，可改用粘贴文字")
    if response.status == 429:
        raise SafeWebError("WEB_RATE_LIMITED", "网页请求过于频繁，请稍后重试")
    if response.status >= 400:
        raise SafeWebError("WEB_HTTP_ERROR", f"网页返回 HTTP {response.status}")

    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    allowed_types = {"text/html", "application/xhtml+xml", "text/plain", "text/markdown"}
    if content_type not in allowed_types:
        raise SafeWebError("WEB_CONTENT_TYPE_UNSUPPORTED", "链接返回的不是可解析网页正文")
    declared_length = response.headers.get("content-length")
    if declared_length and declared_length.isdigit() and int(declared_length) > max_bytes:
        raise SafeWebError("WEB_RESPONSE_TOO_LARGE", "网页正文超过大小上限")
    if len(response.content) > max_bytes:
        raise SafeWebError("WEB_RESPONSE_TOO_LARGE", "网页正文超过大小上限")
    encoding = response.headers.get("content-encoding", "identity").lower()
    if encoding not in {"", "identity"}:
        raise SafeWebError("WEB_ENCODING_UNSUPPORTED", "网页返回了不支持的压缩编码")
    return FetchedDocument(url=normalized_url, content_type=content_type, content=response.content)
