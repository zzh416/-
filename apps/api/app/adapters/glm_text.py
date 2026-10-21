"""GLM Chat Completions 题答抽取适配器。"""
from __future__ import annotations

import json
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


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


class ExtractedSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_ordinal: int = Field(ge=0)
    quote: str = Field(min_length=1, max_length=20_000)

    @field_validator("quote")
    @classmethod
    def quote_must_contain_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("quote must contain non-whitespace text")
        return value


class ExtractedPair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: ExtractedSpan
    answer_spans: list[ExtractedSpan] = Field(default_factory=list, max_length=40)
    confidence: float = Field(ge=0, le=1)


class ExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ExtractedPair] = Field(default_factory=list, max_length=500)


SYSTEM_PROMPT = """你是面试资料结构化提取器。只抽取输入块中明确出现的问题及其原文答案。
必须返回单个 JSON 对象，格式为 {"items": [...]}，不得返回 Markdown。
每项格式为 {"question":{"block_ordinal":整数,"quote":"原文"},
"answer_spans":[{"block_ordinal":整数,"quote":"原文"}],"confidence":0到1}。
question.quote 必须逐字复制问题所在块的原文片段。
答案可以跨多个块；answer_spans 按阅读顺序列出每段答案的块序号和逐字原文。
如果资料只有问题或无法确定答案归属，answer_spans 必须是空数组。
“什么是…”等问句、编号问句以及以知识点命名且正文紧随其后的标题都可作为问题。
不得补写、改写、解释或根据常识生成答案，也不得把下一道题算进上一题答案。"""


def extract_questions_with_glm(
    blocks: list[ExtractionBlock],
    *,
    api_key: str,
    base_url: str,
    model: str,
    max_output_tokens: int = 4_096,
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
                    "max_tokens": max_output_tokens,
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
            choice = envelope["choices"][0]
            if choice.get("finish_reason") in {"length", "max_tokens"}:
                raise TextModelError(
                    "TEXT_MODEL_OUTPUT_TRUNCATED",
                    "题目较多，模型输出被截断；请缩小单次抽取范围后重试",
                    retryable=True,
                )
            raw_content = choice["message"]["content"]
            parsed = json.loads(raw_content)
            result = ExtractionResponse.model_validate(parsed)
        except TextModelError:
            raise
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
