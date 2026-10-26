"""Bounded JSON transport shared by interview features; no tools or external fetches."""
import json

import httpx

from app.adapters.glm_errors import provider_error_code
from app.adapters.glm_text import TextModelError, TextModelNotConfigured


def request_json(payload, *, system_prompt, api_key, base_url, model, provider, client=None, prefix="INTERVIEW", label="面试", timeout_seconds=120):
    if not api_key.strip():
        raise TextModelNotConfigured("尚未配置文本模型 API Key")
    own = client is None
    active = client or httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=min(20, timeout_seconds)))
    try:
        try:
            with active.stream("POST", f"{base_url.rstrip('/')}/chat/completions", headers={"Authorization": f"Bearer {api_key}"}, json={
                "model": model, "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                "temperature": 0, "max_tokens": 4096, "response_format": {"type": "json_object"},
            }) as response:
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 2 * 1024 * 1024:
                        raise TextModelError(f"{prefix}_RESPONSE_TOO_LARGE", f"{label}服务响应超过大小上限", retryable=False)
                status = response.status_code
        except httpx.HTTPError as exc:
            raise TextModelError(f"{prefix}_MODEL_UNAVAILABLE", f"{label}模型连接失败，请稍后重试", retryable=True) from exc
        if status in {401, 403}:
            raise TextModelError(f"{prefix}_MODEL_AUTH_FAILED", "文本模型 API Key 无效或无权限", retryable=False)
        if status == 402 or (provider == "glm" and status == 429 and provider_error_code(raw) == "1113"):
            raise TextModelError(f"{prefix}_RESOURCE_EXHAUSTED", "文本模型余额不足或无可用资源包", retryable=False)
        if status >= 400:
            raise TextModelError(f"{prefix}_MODEL_UNAVAILABLE", f"{label}模型请求失败，请检查配置或稍后重试", retryable=status >= 500 or status == 429)
        try:
            choice = json.loads(raw)["choices"][0]
            if choice.get("finish_reason") in {"length", "max_tokens"}:
                raise TextModelError(f"{prefix}_OUTPUT_TRUNCATED", f"{label}结果过长被截断，请减少输入后重试", retryable=True)
            content = choice["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("Model content must be text")
            content = content.strip()
            if content.startswith("```") and content.endswith("```"):
                first_line, separator, body = content.partition("\n")
                if separator and first_line.lower() in {"```", "```json"}:
                    content = body[:-3].strip()
            return json.loads(content)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise TextModelError(f"{prefix}_INVALID_RESPONSE", f"{label}模型返回了不符合约定的结果", retryable=False) from exc
    finally:
        if own:
            active.close()
