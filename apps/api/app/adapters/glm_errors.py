"""Read GLM error codes without exposing arbitrary provider response content."""
from __future__ import annotations

import json


RESOURCE_EXHAUSTED_MESSAGE = "GLM 账户余额不足或无可用资源包，请检查账户余额及资源包后重试"


def provider_error_code(response_content: bytes | bytearray) -> str | None:
    try:
        payload = json.loads(response_content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    return str(code) if isinstance(code, (str, int)) else None
