from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.identity import get_current_user_id
from app.main import app
from app.models import Asset, AssetVersion, Collection, Question, QuestionExtractionRun, QuestionOccurrence, User


client = TestClient(app)


@pytest.fixture
def owner():
    owner_id = uuid4()
    with SessionLocal() as db:
        db.add(User(id=owner_id, display_name="来源统计测试"))
        db.commit()
    app.dependency_overrides[get_current_user_id] = lambda: owner_id
    yield owner_id
    app.dependency_overrides.clear()
    with SessionLocal() as db:
        db.delete(db.get(User, owner_id))
        db.commit()


def add_source(db, owner, *, purpose="INTERVIEW_EXPERIENCE", url=None, digest=None, prompt="Python GIL 是什么？", review="PENDING", current=True):
    collection = Collection(owner_id=owner, name=str(uuid4()), purpose=purpose)
    db.add(collection)
    db.flush()
    asset = Asset(owner_id=owner, collection_id=collection.id, title="原文来源", source_format="WEB_PAGE" if url else "PDF", source_url=url, content_sha256=digest, current_version=1 if current else 2)
    db.add(asset)
    db.flush()
    version = AssetVersion(asset_id=asset.id, version=1, content=prompt)
    question = Question(owner_id=owner, collection_id=collection.id, source_type="SOURCE_EXTRACTED", prompt=prompt)
    db.add_all([version, question])
    db.flush()
    run = QuestionExtractionRun(owner_id=owner, asset_id=asset.id, asset_version_id=version.id, status="SUCCEEDED")
    db.add(run)
    db.flush()
    occurrence = QuestionOccurrence(question_id=question.id, extraction_run_id=run.id, asset_version_id=version.id, question_start=0, question_end=len(prompt), question_quote=prompt, confidence=1, review_status=review)
    db.add(occurrence)
    db.flush()
    return question, asset, run


def test_statistics_deduplicate_urls_files_and_exclude_rejected_old_and_foreign(owner):
    with SessionLocal() as db:
        question, asset, _ = add_source(db, owner, url="https://example.com/post?id=1&utm_source=a")
        add_source(db, owner, url="https://example.com/post?utm_source=b&id=1#")
        add_source(db, owner, digest="a" * 64)
        add_source(db, owner, digest="a" * 64)
        add_source(db, owner, purpose="KNOWLEDGE_BASE", digest="b" * 64)
        add_source(db, owner, url="https://example.com/rejected", review="REJECTED")
        add_source(db, owner, url="https://example.com/old", current=False)
        foreign = User(id=uuid4(), display_name="其他用户")
        db.add(foreign)
        db.flush()
        foreign_question, _, _ = add_source(db, foreign.id, url="https://example.com/foreign")
        db.commit()
        question_id, asset_id, foreign_id = str(question.id), str(asset.id), str(foreign_question.id)
        foreign_owner_id = foreign.id
    try:
        response = client.get(f"/api/v1/questions/{question_id}/sources")
        assert response.status_code == 200
        stats = response.json()
        assert stats["interviewSourceCount"] == 2
        assert stats["knowledgeSourceCount"] == 1
        assert stats["sourceCount"] == 3
        assert stats["occurrenceCount"] == 5
        assert stats["unknownDateSourceCount"] == 3
        assert stats["latestSourceDate"] is None
        assert len(stats["sources"]) == 3
        assert all("foreign" not in (source["sourceUrl"] or "") for source in stats["sources"])
        assert client.get(f"/api/v1/questions/{foreign_id}/sources").status_code == 404
        page = client.get("/api/v1/questions", params={"pageSize": 100}).json()
        item = next(item for item in page["items"] if item["id"] == question_id)
        assert item["sourceStatistics"]["sourceCount"] == 3

        updated = client.put(f"/api/v1/assets/{asset_id}/source-metadata", json={"sourceDate": "2026-10-01", "expectedVersion": 1})
        assert updated.status_code == 200
        assert updated.json()["sourceMetadataVersion"] == 2
        stats = client.get(f"/api/v1/questions/{question_id}/sources").json()
        assert stats["latestSourceDate"] == "2026-10-01"
        assert stats["recentInterviewSourceCount"] == 1
        assert client.put(f"/api/v1/assets/{asset_id}/source-metadata", json={"sourceDate": None, "expectedVersion": 1}).status_code == 409
    finally:
        with SessionLocal() as db:
            db.delete(db.get(User, foreign_owner_id))
            db.commit()


def test_source_date_rejects_future_and_metadata_is_owner_scoped(owner):
    with SessionLocal() as db:
        question, asset, _ = add_source(db, owner)
        db.commit()
        asset_id = str(asset.id)
    response = client.put(f"/api/v1/assets/{asset_id}/source-metadata", json={"sourceDate": str(date.today() + timedelta(days=2)), "expectedVersion": 1})
    assert response.status_code == 422
    assert client.put(f"/api/v1/assets/{uuid4()}/source-metadata", json={"sourceDate": None, "expectedVersion": 1}).status_code == 404


def test_quality_summary_is_unfiltered_and_legacy_diagnostics_unknown(owner):
    with SessionLocal() as db:
        _, asset, run = add_source(db, owner)
        db.commit()
        run_id = str(run.id)
        asset_id = str(asset.id)
    latest = client.get(f"/api/v1/assets/{asset_id}/question-extraction-runs/latest")
    assert latest.status_code == 200
    assert latest.json()["id"] == run_id
    response = client.get(f"/api/v1/question-extraction-runs/{run_id}/review-items", params={"reviewStatus": "CONFIRMED", "pageSize": 1})
    assert response.status_code == 200
    quality = response.json()["quality"]
    assert quality["occurrenceCount"] == 1
    assert quality["uniqueQuestionCount"] == 1
    assert quality["pendingCount"] == 1
    assert quality["sourceAnsweredCount"] == 0
    assert quality["sourceMissingCount"] == 1
    assert quality["diagnostics"] is None
    revised = client.put(f"/api/v1/assets/{asset_id}/content", json={"content": "新版本 Python 是什么？", "expectedVersion": 1})
    assert revised.status_code == 200
    assert client.get(f"/api/v1/assets/{asset_id}/question-extraction-runs/latest").json() is None
    assert client.get(f"/api/v1/question-extraction-runs/{run_id}/review-items").json()["quality"]["isCurrentVersion"] is False


def test_quality_limits_are_disclosed_and_source_answers_ignore_rejected_records(owner):
    with SessionLocal() as db:
        question, asset, run = add_source(db, owner, review="REJECTED")
        version = db.get(AssetVersion, run.asset_version_id)
        version.content = "\n".join(f"{index}. 什么是知识点{index}？" for index in range(110))
        from app.models import TextChunk
        db.add(TextChunk(asset_version_id=version.id, ordinal=0, content=version.content, start_offset=0, end_offset=len(version.content)))
        occurrence = db.query(QuestionOccurrence).filter_by(extraction_run_id=run.id).one()
        occurrence.answer_quote = "这只是被拒绝记录的答案"
        db.commit()
        run_id = str(run.id)
    quality = client.get(f"/api/v1/question-extraction-runs/{run_id}/review-items").json()["quality"]
    assert quality["rejectedCount"] == 1
    assert quality["activeQuestionCount"] == 0
    assert quality["sourceAnsweredCount"] == 0
    assert quality["suspectedMissCount"] == 100
    assert quality["suspectedMissesTruncated"] is True


def test_recommendation_sources_are_snapshotted_without_overriding_exclusions(owner):
    with SessionLocal() as db:
        question, asset, _ = add_source(db, owner, prompt="Java 的类加载机制是什么？")
        db.commit()
        asset_id = str(asset.id)
    rule = client.post("/api/v1/preference-rules", json={"ruleType": "EXCLUDE", "technology": "Java", "scope": "BOTH"})
    assert rule.status_code == 201
    run = client.post("/api/v1/recommendation-runs", json={}).json()
    item = client.get(f"/api/v1/recommendation-runs/{run['id']}/items").json()["items"][0]
    assert item["decision"] == "EXCLUDED"
    assert item["sourceStatistics"]["interviewSourceCount"] == 1
    assert item["sourceStatistics"]["latestSourceDate"] is None
    assert client.get(f"/api/v1/recommendation-runs/{run['id']}").json()["isCurrent"]
    assert client.put(f"/api/v1/assets/{asset_id}/source-metadata", json={"sourceDate": "2026-10-01", "expectedVersion": 1}).status_code == 200
    assert not client.get(f"/api/v1/recommendation-runs/{run['id']}").json()["isCurrent"]
    historical = client.get(f"/api/v1/recommendation-runs/{run['id']}/items").json()["items"][0]
    assert historical["sourceStatistics"]["latestSourceDate"] is None
    assert historical["decision"] == "EXCLUDED"


def test_new_extraction_reports_candidate_validation_diagnostics(owner, monkeypatch):
    from app.adapters.glm_text import ExtractedPair, ExtractedSpan
    from app.modules.question_extraction_service import process_question_extraction_run

    collection = client.post("/api/v1/collections", json={"name": str(uuid4()), "purpose": "INTERVIEW_EXPERIENCE"}).json()
    asset = client.post(f"/api/v1/collections/{collection['id']}/assets/text", json={"title": "诊断样本", "content": "Python 是什么？", "sourceFormat": "PASTED_TEXT"}).json()
    monkeypatch.setattr("app.routes.extractions.celery_app.send_task", lambda *a, **k: None)
    response = client.post(f"/api/v1/assets/{asset['id']}/question-extraction-runs", headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code == 201, response.text
    run = response.json()
    def extractor(blocks, **kwargs):
        return [
            ExtractedPair(question=ExtractedSpan(block_ordinal=0, quote="Python 是什么？"), confidence=1),
            ExtractedPair(question=ExtractedSpan(block_ordinal=0, quote="原文中不存在的问题？"), confidence=1),
        ]
    process_question_extraction_run(UUID(run["id"]), extractor=extractor)
    quality = client.get(f"/api/v1/question-extraction-runs/{run['id']}/review-items").json()["quality"]
    assert quality["occurrenceCount"] == 1
    assert quality["diagnostics"] == {"plannedBatchCount": 1, "completedBatchCount": 1, "failedBatchCount": 0, "modelCandidateCount": 2, "invalidSourceCount": 1, "duplicateRangeCount": 0}


def test_source_identity_keeps_content_queries_fragments_and_technology_names():
    from app.modules.source_statistics import source_identity
    a = Asset(source_url="https://example.com/app#/chapter/1")
    b = Asset(source_url="https://example.com/app#/chapter/2")
    assert source_identity(a) != source_identity(b)
    a.source_url = "https://example.com/article?id=1&utm_source=feed"
    b.source_url = "https://example.com/article?id=2&utm_source=feed"
    assert source_identity(a) != source_identity(b)
