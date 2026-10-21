from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.adapters.glm_text import ExtractedPair, ExtractedSpan, TextModelError
from app.config import settings
from app.db import SessionLocal
from app.identity import get_current_user_id
from app.main import app
from app.models import QuestionOccurrence
from app.modules.question_extraction_service import process_question_extraction_run
from app.modules.question_extraction_service import LoadedChunk, _build_extraction_batches

client = TestClient(app)


def test_extraction_batches_overlap_without_losing_source_order() -> None:
    chunks = [LoadedChunk(id=uuid4(), ordinal=index, content="x" * 10) for index in range(7)]
    batches = _build_extraction_batches(
        chunks, max_chars=100, max_blocks=3, overlap_blocks=1
    )
    assert [[chunk.ordinal for chunk in batch] for batch in batches] == [
        [0, 1, 2],
        [2, 3, 4],
        [4, 5, 6],
    ]


def _create_collection() -> str:
    response = client.post(
        "/api/v1/collections",
        json={"name": f"抽取测试-{uuid4()}", "purpose": "INTERVIEW_EXPERIENCE"},
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_extraction_run_is_idempotent_and_persists_source_evidence(monkeypatch) -> None:
    collection_id = _create_collection()
    asset_id: str | None = None
    question_ids: list[str] = []
    content = (
        "#### 31.什么是零拷贝\n\n"
        "零拷贝减少用户态与内核态之间的数据复制。\n\n"
        "常见实现包括 mmap 和 sendfile。\n\n"
        "#### 32.I/O 多路复用"
    )
    try:
        asset = client.post(
            f"/api/v1/collections/{collection_id}/assets/text",
            json={"title": "Python 面经", "content": content, "sourceFormat": "PASTED_TEXT"},
        )
        assert asset.status_code == 201
        asset_id = asset.json()["id"]
        monkeypatch.setattr(
            "app.routes.extractions.celery_app.send_task",
            lambda *args, **kwargs: SimpleNamespace(id="celery-test-task"),
        )
        headers = {"Idempotency-Key": f"extract-{uuid4()}"}
        created = client.post(
            f"/api/v1/assets/{asset_id}/question-extraction-runs", headers=headers
        )
        assert created.status_code == 201
        assert created.json()["status"] == "QUEUED"
        run_id = created.json()["id"]
        replayed = client.post(
            f"/api/v1/assets/{asset_id}/question-extraction-runs", headers=headers
        )
        assert replayed.status_code == 201
        assert replayed.json()["id"] == run_id
        same_version = client.post(
            f"/api/v1/assets/{asset_id}/question-extraction-runs",
            headers={"Idempotency-Key": f"extract-{uuid4()}"},
        )
        assert same_version.status_code == 201
        assert same_version.json()["id"] == run_id

        first_question = "#### 31.什么是零拷贝"
        first_answer_parts = [
            "零拷贝减少用户态与内核态之间的数据复制。",
            "常见实现包括 mmap 和 sendfile。",
        ]
        first_answer = "\n\n".join(first_answer_parts)
        second_question = "#### 32.I/O 多路复用"

        def fake_extractor(blocks, **kwargs):
            assert [block.ordinal for block in blocks] == [0, 1, 2, 3]
            return [
                ExtractedPair(
                    question=ExtractedSpan(block_ordinal=0, quote=first_question),
                    answer_spans=[
                        ExtractedSpan(block_ordinal=1, quote=first_answer_parts[0]),
                        ExtractedSpan(block_ordinal=2, quote=first_answer_parts[1]),
                    ],
                    confidence=0.98,
                ),
                ExtractedPair(
                    question=ExtractedSpan(block_ordinal=3, quote=second_question),
                    confidence=0.88,
                ),
            ]

        process_question_extraction_run(UUID(run_id), extractor=fake_extractor)
        finished = client.get(f"/api/v1/question-extraction-runs/{run_id}")
        assert finished.status_code == 200
        assert finished.json()["status"] == "SUCCEEDED"
        assert finished.json()["questionCount"] == 2
        assert finished.json()["answeredCount"] == 1
        assert finished.json()["missingCount"] == 1

        questions = client.get(
            "/api/v1/questions", params={"collectionId": collection_id}
        ).json()
        extracted = [item for item in questions["items"] if item["sourceType"] == "SOURCE_EXTRACTED"]
        question_ids.extend(item["id"] for item in extracted)
        assert len(extracted) == 2
        assert {item["answerState"] for item in extracted} == {"SOURCE_ANSWER", "MISSING"}
        answered = next(item for item in extracted if item["answer"] is not None)
        assert answered["answer"]["sourceType"] == "SOURCE_EXTRACTED"
        assert answered["prompt"] == "31.什么是零拷贝"
        assert answered["answer"]["content"] == first_answer

        with SessionLocal() as db:
            occurrences = list(
                db.scalars(
                    select(QuestionOccurrence).where(
                        QuestionOccurrence.extraction_run_id == UUID(run_id)
                    )
                ).all()
            )
        assert len(occurrences) == 2
        assert all(item.review_status == "PENDING" for item in occurrences)
        assert any(item.answer_quote == first_answer for item in occurrences)
        answered_occurrence = next(item for item in occurrences if item.answer_quote == first_answer)
        assert [span["quote"] for span in answered_occurrence.answer_spans] == first_answer_parts
        second_occurrence = next(item for item in occurrences if item.question_quote == second_question)
        assert second_occurrence.question_start == 0

        app.dependency_overrides[get_current_user_id] = lambda: uuid4()
        assert client.get(f"/api/v1/question-extraction-runs/{run_id}").status_code == 404
    finally:
        app.dependency_overrides.clear()
        for question_id in question_ids:
            client.delete(f"/api/v1/questions/{question_id}")
        if asset_id is not None:
            client.delete(f"/api/v1/assets/{asset_id}")
        client.delete(f"/api/v1/collections/{collection_id}")


def test_extraction_requires_idempotency_key_and_ready_asset() -> None:
    missing = client.post(f"/api/v1/assets/{uuid4()}/question-extraction-runs")
    assert missing.status_code == 422


def test_extraction_preserves_valid_batches_when_one_batch_has_invalid_output(
    monkeypatch,
) -> None:
    collection_id = _create_collection()
    asset_id: str | None = None
    question_ids: list[str] = []
    try:
        asset = client.post(
            f"/api/v1/collections/{collection_id}/assets/text",
            json={
                "title": "分批容错测试",
                "content": "无关段落一\n\n无关段落二\n\n#### 什么是事件循环\n\n事件循环负责调度任务。",
                "sourceFormat": "PASTED_TEXT",
            },
        )
        assert asset.status_code == 201
        asset_id = asset.json()["id"]
        monkeypatch.setattr(
            "app.routes.extractions.celery_app.send_task",
            lambda *args, **kwargs: SimpleNamespace(id="celery-test-task"),
        )
        monkeypatch.setattr(settings, "question_extraction_batch_blocks", 2)
        monkeypatch.setattr(settings, "question_extraction_overlap_blocks", 0)
        created = client.post(
            f"/api/v1/assets/{asset_id}/question-extraction-runs",
            headers={"Idempotency-Key": f"extract-{uuid4()}"},
        )
        assert created.status_code == 201
        run_id = created.json()["id"]
        calls = 0

        def fake_extractor(blocks, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TextModelError(
                    "TEXT_MODEL_INVALID_RESPONSE",
                    "题目抽取服务返回了不符合约定的结果",
                    retryable=False,
                )
            return [
                ExtractedPair(
                    question=ExtractedSpan(block_ordinal=2, quote="#### 什么是事件循环"),
                    answer_spans=[
                        ExtractedSpan(block_ordinal=3, quote="事件循环负责调度任务。")
                    ],
                    confidence=0.95,
                )
            ]

        process_question_extraction_run(UUID(run_id), extractor=fake_extractor)
        finished = client.get(f"/api/v1/question-extraction-runs/{run_id}")
        assert finished.status_code == 200
        assert finished.json()["status"] == "PARTIAL_SUCCESS"
        assert finished.json()["questionCount"] == 1
        assert "1 个批次格式异常" in finished.json()["errorMessage"]

        questions = client.get(
            "/api/v1/questions", params={"collectionId": collection_id}
        ).json()
        extracted = [item for item in questions["items"] if item["sourceType"] == "SOURCE_EXTRACTED"]
        question_ids.extend(item["id"] for item in extracted)
        assert len(extracted) == 1
        assert extracted[0]["prompt"] == "什么是事件循环"
        assert extracted[0]["answer"]["content"] == "事件循环负责调度任务。"
    finally:
        for question_id in question_ids:
            client.delete(f"/api/v1/questions/{question_id}")
        if asset_id is not None:
            client.delete(f"/api/v1/assets/{asset_id}")
        client.delete(f"/api/v1/collections/{collection_id}")
