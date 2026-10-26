import json
from uuid import uuid4

import httpx
import pytest

from app.adapters.glm_text import TextModelError, TextModelNotConfigured
from app.adapters.project_followups import generate_project_followups


def call(handler, provider="deepseek"):
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return generate_project_followups({"project": {"name": "合成项目", "description": "RAG"}, "seeds": []},
                                          api_key="synthetic-key", base_url="https://example.test/v1", model="test-model", provider=provider, client=client)


def test_followup_model_uses_bounded_json_protocol_and_never_requests_answers():
    def handler(request):
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"} and body["max_tokens"] == 4096
        assert "只生成问题" in body["messages"][0]["content"]
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"items": [{
            "seed_item_id": str(uuid4()), "level": 1, "question": "RAG 如何评估效果？", "project_quote": "项目使用RAG", "seed_quote": "如何使用RAG",
        }]})}}]})
    assert len(call(handler)) == 1


@pytest.mark.parametrize("status,provider,payload,code", [
    (401, "deepseek", {}, "FOLLOWUP_MODEL_AUTH_FAILED"),
    (402, "deepseek", {}, "FOLLOWUP_RESOURCE_EXHAUSTED"),
    (429, "glm", {"error": {"code": "1113"}}, "FOLLOWUP_RESOURCE_EXHAUSTED"),
    (503, "glm", {}, "FOLLOWUP_MODEL_UNAVAILABLE"),
    (200, "glm", {"choices": [{"finish_reason": "length"}]}, "FOLLOWUP_OUTPUT_TRUNCATED"),
    (200, "deepseek", {"choices": [{"message": {"content": ""}}]}, "FOLLOWUP_INVALID_RESPONSE"),
])
def test_followup_model_reports_safe_errors(status, provider, payload, code):
    with pytest.raises(TextModelError) as error:
        call(lambda request: httpx.Response(status, json=payload), provider)
    assert error.value.code == code


def test_followup_model_requires_key_and_rejects_answers_in_response():
    with pytest.raises(TextModelNotConfigured):
        generate_project_followups({}, api_key="", base_url="https://example.test", model="test", provider="glm")
    payload = {"items": [], "answer": "不允许生成答案"}
    with pytest.raises(TextModelError):
        call(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}]}))
