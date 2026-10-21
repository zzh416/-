"""GLM adapter for evidence-backed resume profile extraction."""
from __future__ import annotations

import json

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.adapters.glm_text import TextModelError, TextModelNotConfigured


class ExtractedProfileSkill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    proficiency: str = Field(pattern="^(UNSPECIFIED|BEGINNER|INTERMEDIATE|ADVANCED)$")
    evidence: str = Field(min_length=1, max_length=2_000)

    @field_validator("name", "evidence")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must contain text")
        return value.strip()


class ExtractedProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_role: str | None = Field(default=None, max_length=200)
    target_role_evidence: str | None = Field(default=None, max_length=2_000)
    skills: list[ExtractedProfileSkill] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def role_evidence_is_paired(self) -> "ExtractedProfile":
        if bool(self.target_role) != bool(self.target_role_evidence):
            raise ValueError("target role and evidence must both be present or absent")
        return self


SYSTEM_PROMPT = """你是简历技能画像提取器。只提取简历原文明确出现的技术技能与目标岗位。
必须返回单个 JSON 对象，不得返回 Markdown：
{"target_role":字符串或null,"target_role_evidence":原文逐字引文或null,
 "skills":[{"name":技能名,"proficiency":"UNSPECIFIED|BEGINNER|INTERMEDIATE|ADVANCED","evidence":原文逐字引文}]}。
每个 evidence 必须逐字复制简历原文中的一段文字。
只有原文明确表达熟练程度时才能使用 BEGINNER、INTERMEDIATE 或 ADVANCED，否则使用 UNSPECIFIED。
不要从公司、岗位常识或项目背景推测未出现的技术，不要提取软技能、姓名、电话等个人信息。"""


def extract_profile_with_glm(
    content: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    max_output_tokens: int = 4_096,
    client: httpx.Client | None = None,
) -> ExtractedProfile:
    if not api_key.strip():
        raise TextModelNotConfigured("尚未配置 GLM_API_KEY")
    if not content.strip():
        return ExtractedProfile()
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    own_client = client is None
    active_client = client or httpx.Client(timeout=httpx.Timeout(120.0, connect=20.0))
    try:
        try:
            response = active_client.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": content},
                    ],
                    "temperature": 0,
                    "max_tokens": max_output_tokens,
                    "response_format": {"type": "json_object"},
                },
            )
        except httpx.HTTPError as exc:
            raise TextModelError(
                "PROFILE_MODEL_UNAVAILABLE", "画像提取服务连接失败，请稍后重试", retryable=True
            ) from exc
        if response.status_code in {401, 403}:
            raise TextModelError(
                "PROFILE_MODEL_AUTH_FAILED", "GLM API Key 无效或无权限", retryable=False
            )
        if response.status_code == 429:
            raise TextModelError(
                "PROFILE_MODEL_RATE_LIMITED", "画像提取服务限流，请稍后重试", retryable=True
            )
        if response.status_code >= 500:
            raise TextModelError(
                "PROFILE_MODEL_UNAVAILABLE", "画像提取服务暂时不可用", retryable=True
            )
        if response.status_code >= 400:
            raise TextModelError(
                "PROFILE_MODEL_REQUEST_REJECTED", "画像提取服务拒绝了请求", retryable=False
            )
        try:
            envelope = response.json()
            choice = envelope["choices"][0]
            if choice.get("finish_reason") in {"length", "max_tokens"}:
                raise TextModelError(
                    "PROFILE_MODEL_OUTPUT_TRUNCATED", "画像提取结果过长，请精简简历后重试", retryable=True
                )
            parsed = json.loads(choice["message"]["content"])
            return ExtractedProfile.model_validate(parsed)
        except TextModelError:
            raise
        except (ValueError, KeyError, IndexError, TypeError, ValidationError) as exc:
            raise TextModelError(
                "PROFILE_MODEL_INVALID_RESPONSE", "画像提取服务返回了不符合约定的结果", retryable=False
            ) from exc
    finally:
        if own_client:
            active_client.close()
