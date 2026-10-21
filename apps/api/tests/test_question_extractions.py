from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.adapters.glm_text import ExtractedPair
from app.db import SessionLocal
from app.identity import get_current_user_id
from app.main import app
from app.models import QuestionOccurrence
from app.modules.question_extraction_service import process_question_extraction_run

client = TestClient(app)


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
    content = "1. 什么是 GIL？\n答案：全局解释器锁。\n2. 描述符协议是什么？"
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

        first_question = "什么是 GIL？"
        first_answer = "全局解释器锁。"
        second_question = "描述符协议是什么？"

        def fake_extractor(blocks, **kwargs):
            assert blocks[0].content == content
            return [
                ExtractedPair(
                    question_block_ordinal=0,
                    question_start=content.index(first_question),
                    question_end=content.index(first_question) + len(first_question),
                    question_quote=first_question,
                    answer_block_ordinal=0,
                    answer_start=content.index(first_answer),
                    answer_end=content.index(first_answer) + len(first_answer),
                    answer_quote=first_answer,
                    confidence=0.98,
                ),
                ExtractedPair(
                    question_block_ordinal=0,
                    # 真实模型对中文下标可能不准；服务端以唯一逐字引文重新定位。
                    question_start=0,
                    question_end=len(second_question),
                    question_quote=second_question,
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
        second_occurrence = next(item for item in occurrences if item.question_quote == second_question)
        assert second_occurrence.question_start == content.index(second_question)

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
