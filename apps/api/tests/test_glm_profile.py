import json

import httpx
import pytest

from app.adapters.glm_profile import extract_profile_with_glm
from app.adapters.glm_text import TextModelError, TextModelNotConfigured


def test_glm_profile_parses_evidence_backed_output() -> None:
    payload = {
        "target_role": "Python 后端工程师",
        "target_role_evidence": "目标岗位：Python 后端工程师",
        "skills": [
            {
                "name": "Python",
                "proficiency": "ADVANCED",
                "evidence": "熟练使用 Python",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert "不要提取软技能" in body["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(payload, ensure_ascii=False)},
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = extract_profile_with_glm(
            "目标岗位：Python 后端工程师\n熟练使用 Python",
            api_key="test-key",
            base_url="https://example.test/v4",
            model="glm-test",
            client=client,
        )
    assert result.target_role == "Python 后端工程师"
    assert result.skills[0].proficiency == "ADVANCED"


def test_glm_profile_rejects_unpaired_role_evidence_and_requires_key() -> None:
    payload = {
        "target_role": "Python 后端工程师",
        "target_role_evidence": None,
        "skills": [],
    }
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(payload)}}]},
        )
    )
    with httpx.Client(transport=transport) as client:
        with pytest.raises(TextModelError) as error:
            extract_profile_with_glm(
                "目标岗位：Python 后端工程师",
                api_key="test-key",
                base_url="https://example.test/v4",
                model="glm-test",
                client=client,
            )
    assert error.value.code == "PROFILE_MODEL_INVALID_RESPONSE"

    with pytest.raises(TextModelNotConfigured):
        extract_profile_with_glm(
            "Python",
            api_key="",
            base_url="https://example.test/v4",
            model="glm-test",
        )


def test_glm_profile_rejects_oversized_provider_response() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))
    )
    with httpx.Client(transport=transport) as client:
        with pytest.raises(TextModelError) as error:
            extract_profile_with_glm(
                "Python",
                api_key="test-key",
                base_url="https://example.test/v4",
                model="glm-test",
                client=client,
            )
    assert error.value.code == "PROFILE_MODEL_RESPONSE_TOO_LARGE"
