"""GLM Chat Completions 题答抽取适配器。"""
from __future__ import annotations

import json
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class TextModelNotConfigured(RuntimeError):
    """未配置文本模型密钥。"""


class TextModelError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class ExtractionBlock:
    ordinal: int
    content: str


class ExtractedPair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_block_ordinal: int = Field(ge=0)
    question_start: int = Field(ge=0)
    question_end: int = Field(gt=0)
    question_quote: str = Field(min_length=1, max_length=20_000)
    answer_block_ordinal: int | None = Field(default=None, ge=0)
    answer_start: int | None = Field(default=None, ge=0)
    answer_end: int | None = Field(default=None, gt=0)
    answer_quote: str | None = Field(default=None, min_length=1, max_length=100_000)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def valid_ranges(self) -> "ExtractedPair":
        if self.question_end <= self.question_start:
            raise ValueError("question_end 必须大于 question_start")
        answer_values = (
            self.answer_block_ordinal,
            self.answer_start,
            self.answer_end,
            self.answer_quote,
        )
        if any(value is not None for value in answer_values) and not all(
            value is not None for value in answer_values
        ):
            raise ValueError("答案位置和值必须同时存在或同时为空")
        if self.answer_start is not None and self.answer_end is not None:
            if self.answer_end <= self.answer_start:
                raise ValueError("answer_end 必须大于 answer_start")
        return self


class ExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ExtractedPair] = Field(default_factory=list, max_length=500)


def _normalize_empty_answers(payload: object) -> object:
    """GLM 偶尔用空字符串和零长度范围表达“无答案”。

    只把明确的零长度空答案归一为 null；其他不完整位置仍由严格 Schema 拒绝。
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return payload
    for item in payload["items"]:
        if not isinstance(item, dict):
            continue
        if (
            item.get("answer_quote") == ""
            and isinstance(item.get("answer_start"), int)
            and item.get("answer_start") == item.get("answer_end")
        ):
            item["answer_block_ordinal"] = None
            item["answer_start"] = None
            item["answer_end"] = None
            item["answer_quote"] = None
    return payload


SYSTEM_PROMPT = """你是面试资料结构化提取器。只抽取输入块中明确出现的问题及其原文答案。
必须返回单个 JSON 对象，格式为 {"items": [...]}，不得返回 Markdown。
每项必须包含 question_block_ordinal、question_start、question_end、question_quote、
answer_block_ordinal、answer_start、answer_end、answer_quote、confidence。
start/end 是对应块内从 0 开始、左闭右开的字符下标；quote 必须逐字等于该范围原文。
如果资料只有问题或无法确定答案归属，四个 answer 字段全部为 null。
不得补写、改写、解释或根据常识生成答案。标题不是问题时不要抽取。"""


def extract_questions_with_glm(
    blocks: list[ExtractionBlock],
    *,
    api_key: str,
    base_url: str,
    model: str,
    client: httpx.Client | None = None,
) -> list[ExtractedPair]:
    if not api_key.strip():
        raise TextModelNotConfigured("尚未配置 GLM_API_KEY")
    if not blocks:
        return []
    source = "\n\n".join(
        f"<block ordinal=\"{block.ordinal}\">\n{block.content}\n</block>"
        for block in blocks
    )
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
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
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": source},
                    ],
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
            ) as response:
                response_content = bytearray()
                for chunk in response.iter_bytes():
                    response_content.extend(chunk)
                    if len(response_content) > 2 * 1024 * 1024:
                        raise TextModelError(
                            "TEXT_MODEL_RESPONSE_TOO_LARGE",
                            "题目抽取服务响应超过大小上限",
                            retryable=False,
                        )
                response_status = response.status_code
        except httpx.HTTPError as exc:
            raise TextModelError(
                "TEXT_MODEL_UNAVAILABLE",
                "题目抽取服务连接失败，请稍后重试",
                retryable=True,
            ) from exc
        if response_status in {401, 403}:
            raise TextModelError("TEXT_MODEL_AUTH_FAILED", "GLM API Key 无效或无权限", retryable=False)
        if response_status == 429:
            raise TextModelError("TEXT_MODEL_RATE_LIMITED", "题目抽取服务限流，请稍后重试", retryable=True)
        if response_status >= 500:
            raise TextModelError(
                "TEXT_MODEL_UNAVAILABLE", "题目抽取服务暂时不可用，请稍后重试", retryable=True
            )
        if response_status >= 400:
            raise TextModelError("TEXT_MODEL_REQUEST_REJECTED", "题目抽取服务拒绝了请求", retryable=False)
        try:
            envelope = json.loads(response_content)
            raw_content = envelope["choices"][0]["message"]["content"]
            parsed = _normalize_empty_answers(json.loads(raw_content))
            result = ExtractionResponse.model_validate(parsed)
        except (ValueError, KeyError, IndexError, TypeError, ValidationError) as exc:
            raise TextModelError(
                "TEXT_MODEL_INVALID_RESPONSE",
                "题目抽取服务返回了不符合约定的结果",
                retryable=False,
            ) from exc
        return result.items
    finally:
        if own_client:
            active_client.close()
