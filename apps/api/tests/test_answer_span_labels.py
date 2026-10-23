import pytest

from app.adapters.glm_text import ExtractedPair, ExtractedSpan
from app.modules.extraction_quality_runner import QualitySample, evaluate_quality_sample
from app.modules.question_extraction_service import is_answer_label_only


@pytest.mark.parametrize(
    "quote",
    ["答", "答案", "参考答案：", "**答：**", "__答案__ : ", " ** 参考答案 ** ："],
)
def test_answer_label_recognizes_only_standalone_heading(quote: str) -> None:
    assert is_answer_label_only(quote) is True


@pytest.mark.parametrize("quote", ["不会", "不知道", "答：不知道", "答案是42", "答案。", "", "**"])
def test_answer_label_keeps_meaningful_or_unrecognized_content(quote: str) -> None:
    assert is_answer_label_only(quote) is False


def _sample_and_pairs():
    sample = QualitySample.model_validate(
        {
            "name": "重叠批次中的纯答案标签",
            "source_format": "DOCX",
            "blocks": [
                {"ordinal": 0, "content": "15.Agent执行流程？"},
                {"ordinal": 1, "content": "**答：**"},
                {"ordinal": 2, "content": "先规划，再执行并汇总结果。"},
            ],
            "expected": [
                {"prompt": "15.Agent执行流程？", "answer": "先规划，再执行并汇总结果。"}
            ],
        }
    )
    first = ExtractedPair(
        question=ExtractedSpan(block_ordinal=0, quote=sample.blocks[0].content),
        answer_spans=[ExtractedSpan(block_ordinal=1, quote=sample.blocks[1].content)],
        confidence=0.9,
    )
    complete = first.model_copy(
        update={
            "answer_spans": [
                *first.answer_spans,
                ExtractedSpan(block_ordinal=2, quote=sample.blocks[2].content),
            ]
        }
    )
    return sample, first, complete


def test_quality_runner_ignores_answer_label_and_fills_from_overlap() -> None:
    sample, first, complete = _sample_and_pairs()

    initially = evaluate_quality_sample(sample, [first])
    assert len(initially.observed) == 1
    assert initially.observed[0].answer is None

    filled = evaluate_quality_sample(sample, [first, complete])
    assert len(filled.observed) == 1
    assert filled.observed[0].answer == sample.expected[0].answer
    assert filled.report.passed is True
    assert filled.invalid_candidate_count == 0


@pytest.mark.parametrize("content", ["不会", "不知道", "答：实际内容", "**答案：实际内容**"])
def test_quality_runner_preserves_real_answer_content(content: str) -> None:
    sample, _, complete = _sample_and_pairs()
    sample.blocks[2].content = content
    sample.expected[0].answer = content
    complete.answer_spans[1] = ExtractedSpan(block_ordinal=2, quote=content)

    result = evaluate_quality_sample(sample, [complete])

    assert result.observed[0].answer == content
    assert result.report.passed is True


def test_quality_runner_still_validates_answer_label_source() -> None:
    sample, first, _ = _sample_and_pairs()
    first.answer_spans[0] = ExtractedSpan(block_ordinal=1, quote="参考答案：")

    result = evaluate_quality_sample(sample, [first])

    assert result.invalid_candidate_count == 1
    assert result.observed == ()


def test_extraction_run_persists_missing_label_and_fills_complete_overlap(monkeypatch) -> None:
    from types import SimpleNamespace
    from uuid import UUID, uuid4

    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.main import app
    from app.models import QuestionOccurrence
    from app.modules.question_extraction_service import process_question_extraction_run

    sample, first, complete = _sample_and_pairs()
    extra_content = ["什么是GIL？", "答案：", "不知道", "什么是TCP？", "参考答案："]
    content = "\n\n".join([*(block.content for block in sample.blocks), *extra_content])
    results_by_batch = [
        [first],
        [complete],
        [
            ExtractedPair(
                question=ExtractedSpan(block_ordinal=3, quote=extra_content[0]),
                answer_spans=[
                    ExtractedSpan(block_ordinal=4, quote=extra_content[1]),
                    ExtractedSpan(block_ordinal=5, quote=extra_content[2]),
                ],
                confidence=0.9,
            ),
            ExtractedPair(
                question=ExtractedSpan(block_ordinal=6, quote=extra_content[3]),
                answer_spans=[ExtractedSpan(block_ordinal=7, quote=extra_content[4])],
                confidence=0.9,
            ),
        ],
    ]
    monkeypatch.setattr(
        "app.modules.question_extraction_service._build_extraction_batches",
        lambda chunks, **kwargs: [chunks[:2], chunks[:3], chunks[3:]],
    )
    monkeypatch.setattr(
        "app.routes.extractions.celery_app.send_task",
        lambda *args, **kwargs: SimpleNamespace(id="answer-label-test-task"),
    )
    batch_calls = 0

    def fake_extractor(blocks, **kwargs):
        nonlocal batch_calls
        result = results_by_batch[batch_calls]
        batch_calls += 1
        return result

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/collections",
            json={"name": f"答案标签测试-{uuid4()}", "purpose": "KNOWLEDGE_BASE"},
        )
        assert response.status_code == 201
        collection_id = response.json()["id"]
        asset_id = None
        try:
            response = client.post(
                f"/api/v1/collections/{collection_id}/assets/text",
                json={"title": "答案标签", "content": content, "sourceFormat": "PASTED_TEXT"},
            )
            assert response.status_code == 201
            asset_id = response.json()["id"]
            response = client.post(
                f"/api/v1/assets/{asset_id}/question-extraction-runs",
                headers={"Idempotency-Key": str(uuid4())},
            )
            assert response.status_code == 201
            run_id = response.json()["id"]
            process_question_extraction_run(UUID(run_id), extractor=fake_extractor)
            assert batch_calls == 3
            finished = client.get(f"/api/v1/question-extraction-runs/{run_id}").json()
            assert finished["status"] == "SUCCEEDED"
            assert (finished["questionCount"], finished["answeredCount"], finished["missingCount"]) == (3, 2, 1)
            questions = client.get("/api/v1/questions", params={"collectionId": collection_id}).json()["items"]
            by_prompt = {item["prompt"]: item for item in questions}
            assert len(questions) == 3
            assert by_prompt[sample.expected[0].prompt]["answer"]["content"] == sample.expected[0].answer
            assert by_prompt[extra_content[0]]["answer"]["content"] == "不知道"
            assert by_prompt[extra_content[3]]["answerState"] == "MISSING"
            assert by_prompt[extra_content[3]]["answer"] is None
            with SessionLocal() as db:
                occurrences = list(db.scalars(select(QuestionOccurrence).where(
                    QuestionOccurrence.extraction_run_id == UUID(run_id)
                )))
                completed = next(item for item in occurrences if item.question_quote == sample.expected[0].prompt)
                assert completed.answer_quote == sample.expected[0].answer
                assert [span["quote"] for span in completed.answer_spans] == [sample.expected[0].answer]
                assert completed.answer_spans[0]["blockOrdinal"] == 2
                assert completed.answer_start == 0
                missing = next(item for item in occurrences if item.question_quote == extra_content[3])
                assert missing.answer_source_chunk_id is None
                assert missing.answer_quote is None
                assert missing.answer_spans == []
        finally:
            # Query again so an earlier failed assertion cannot leave created questions behind.
            remaining = client.get("/api/v1/questions", params={"collectionId": collection_id})
            assert remaining.status_code == 200
            for question in remaining.json()["items"]:
                deleted = client.delete(f"/api/v1/questions/{question['id']}")
                assert deleted.status_code == 204
            if asset_id is not None:
                deleted = client.delete(f"/api/v1/assets/{asset_id}")
                assert deleted.status_code == 204
            deleted = client.delete(f"/api/v1/collections/{collection_id}")
            assert deleted.status_code == 204
