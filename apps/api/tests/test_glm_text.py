import json

import httpx
import pytest

from app.adapters.glm_text import (
    ExtractionBlock,
    TextModelError,
    TextModelNotConfigured,
    extract_questions_with_glm,
)


def test_glm_text_parses_strict_structured_output() -> None:
    payload = {
        "items": [
            {
                "question_block_ordinal": 0,
                "question_start": 0,
                "question_end": 8,
                "question_quote": "什么是 GIL？",
                "answer_block_ordinal": None,
                "answer_start": None,
                "answer_end": None,
                "answer_quote": None,
                "confidence": 0.91,
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path.endswith("/chat/completions")
        assert body["response_format"] == {"type": "json_object"}
        assert body["max_tokens"] == 4096
        assert "不得补写" in body["messages"][0]["content"]
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = extract_questions_with_glm(
            [ExtractionBlock(ordinal=0, content="什么是 GIL？")],
            api_key="test-key",
            base_url="https://example.test/v4",
            model="glm-test",
            client=client,
        )
    assert result[0].question_quote == "什么是 GIL？"
    assert result[0].answer_quote is None


def test_glm_text_rejects_partial_answer_location() -> None:
    bad_payload = {
        "items": [
            {
                "question_block_ordinal": 0,
                "question_start": 0,
                "question_end": 2,
                "question_quote": "问题",
                "answer_block_ordinal": 0,
                "answer_start": None,
                "answer_end": None,
                "answer_quote": "答案",
                "confidence": 1,
            }
        ]
    }
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(bad_payload)}}]},
        )
    )
    with httpx.Client(transport=transport) as client:
        with pytest.raises(TextModelError, match="不符合约定") as error:
            extract_questions_with_glm(
                [ExtractionBlock(ordinal=0, content="问题答案")],
                api_key="test-key",
                base_url="https://example.test/v4",
                model="glm-test",
                client=client,
            )
    assert error.value.code == "TEXT_MODEL_INVALID_RESPONSE"


def test_glm_text_normalizes_explicit_zero_length_empty_answer() -> None:
    payload = {
        "items": [
            {
                "question_block_ordinal": 0,
                "question_start": 0,
                "question_end": 2,
                "question_quote": "问题",
                "answer_block_ordinal": 0,
                "answer_start": 2,
                "answer_end": 2,
                "answer_quote": "",
                "confidence": 0,
            }
        ]
    }
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(payload)}}]},
        )
    )
    with httpx.Client(transport=transport) as client:
        result = extract_questions_with_glm(
            [ExtractionBlock(ordinal=0, content="问题")],
            api_key="test-key",
            base_url="https://example.test/v4",
            model="glm-test",
            client=client,
        )
    assert result[0].answer_quote is None
    assert result[0].answer_start is None


def test_glm_text_requires_api_key() -> None:
    with pytest.raises(TextModelNotConfigured):
        extract_questions_with_glm(
            [ExtractionBlock(ordinal=0, content="问题")],
            api_key="",
            base_url="https://example.test/v4",
            model="glm-test",
        )


def test_glm_text_reports_truncated_output_separately() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": '{"items":['},
                    }
                ]
            },
        )
    )
    with httpx.Client(transport=transport) as client:
        with pytest.raises(TextModelError, match="输出被截断") as error:
            extract_questions_with_glm(
                [ExtractionBlock(ordinal=0, content="问题")],
                api_key="test-key",
                base_url="https://example.test/v4",
                model="glm-test",
                client=client,
            )
    assert error.value.code == "TEXT_MODEL_OUTPUT_TRUNCATED"
