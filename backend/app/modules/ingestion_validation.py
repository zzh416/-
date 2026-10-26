"""P2 文件与 URL 的边界校验。"""
from __future__ import annotations

import io
import ipaddress
from dataclasses import dataclass
from pathlib import PurePath
from urllib.parse import urlsplit, urlunsplit
from zipfile import BadZipFile, ZipFile


@dataclass(frozen=True)
class UploadKind:
    source_format: str
    content_type: str
    asynchronous: bool


_KINDS = {
    ".txt": UploadKind("TXT_FILE", "text/plain", False),
    ".md": UploadKind("MARKDOWN_FILE", "text/markdown", False),
    ".markdown": UploadKind("MARKDOWN_FILE", "text/markdown", False),
    ".png": UploadKind("IMAGE", "image/png", True),
    ".jpg": UploadKind("IMAGE", "image/jpeg", True),
    ".jpeg": UploadKind("IMAGE", "image/jpeg", True),
    ".pdf": UploadKind("PDF", "application/pdf", True),
    ".docx": UploadKind(
        "DOCX",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        True,
    ),
    ".doc": UploadKind("DOC", "application/msword", True),
}

_DECLARED_TYPES = {
    ".txt": {"text/plain", "application/octet-stream"},
    ".md": {"text/plain", "text/markdown", "application/octet-stream"},
    ".markdown": {"text/plain", "text/markdown", "application/octet-stream"},
    ".png": {"image/png", "application/octet-stream"},
    ".jpg": {"image/jpeg", "image/jpg", "application/octet-stream"},
    ".jpeg": {"image/jpeg", "image/jpg", "application/octet-stream"},
    ".pdf": {"application/pdf", "application/octet-stream"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
        "application/octet-stream",
    },
    ".doc": {"application/msword", "application/octet-stream"},
}


def classify_upload(filename: str, declared_type: str | None, content: bytes) -> UploadKind:
    suffix = PurePath(filename).suffix.lower()
    kind = _KINDS.get(suffix)
    if kind is None:
        raise ValueError("不支持该文件格式")
    normalized_type = (declared_type or "application/octet-stream").split(";", 1)[0].lower()
    if normalized_type not in _DECLARED_TYPES[suffix]:
        raise ValueError("文件声明类型与扩展名不一致")
    if suffix == ".png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("文件内容与扩展名不一致")
    if suffix in {".jpg", ".jpeg"} and not content.startswith(b"\xff\xd8\xff"):
        raise ValueError("文件内容与扩展名不一致")
    if suffix == ".pdf" and not content.startswith(b"%PDF-"):
        raise ValueError("文件内容与扩展名不一致")
    if suffix == ".doc" and not content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise ValueError("文件内容与扩展名不一致")
    if suffix == ".docx":
        _validate_docx(content)
    return kind


def _validate_docx(content: bytes) -> None:
    if not content.startswith(b"PK\x03\x04"):
        raise ValueError("文件内容与扩展名不一致")
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            names = {item.filename for item in infos}
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise ValueError("文件内容与扩展名不一致")
            if len(infos) > 2000 or sum(item.file_size for item in infos) > 100 * 1024 * 1024:
                raise ValueError("DOCX 解压后内容过大")
    except BadZipFile as exc:
        raise ValueError("文件内容与扩展名不一致") from exc


def normalize_public_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("网页地址只支持 http 或 https")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("网页地址格式不合法")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("网页端口不合法") from exc
    if port is not None and port not in {80, 443}:
        raise ValueError("网页地址只允许 80 或 443 端口")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("网页地址不能指向本机或内网")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("网页地址不能指向本机或内网")
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 443 if parsed.scheme == "https" else 80
    if port is not None and port != default_port:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path or "/", parsed.query, parsed.fragment))
