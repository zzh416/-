from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.identity import get_current_user_id
from app.main import app
from app.models import Question, User

client = TestClient(app)


@pytest.fixture
def context(monkeypatch):
    owner = uuid4()
    with SessionLocal() as db:
        db.add(User(id=owner, display_name="追问验收"))
        db.commit()
    app.dependency_overrides[get_current_user_id] = lambda: owner
    monkeypatch.setattr("app.routes.project_followups.celery_app.send_task", lambda *args, **kwargs: SimpleNamespace(id="fake-task"))
    profile = client.get("/api/v1/profiles/me").json()
    profile = client.post("/api/v1/profiles/me/projects", json={"name": "知识库", "description": "使用 Python、RAG 向量检索和重排。", "expectedVersion": profile["version"]}).json()
    project = profile["projects"][0]
    collection = client.post("/api/v1/collections", json={"name": "追问题库", "purpose": "KNOWLEDGE_BASE"}).json()
    for prompt in ["RAG 为什么需要重排？", "Java 如何实现 RAG？", "什么是进程？"]:
        client.post("/api/v1/questions", json={"collectionId": collection["id"], "prompt": prompt})
    client.post("/api/v1/preference-rules", json={"ruleType": "EXCLUDE", "technology": "Java", "scope": "BOTH"})
    run = client.post("/api/v1/recommendation-runs", json={}).json()
    items = client.get(f"/api/v1/recommendation-runs/{run['id']}/items").json()["items"]
    seed = next(item for item in items if item["prompt"] == "RAG 为什么需要重排？")
    yield {"owner": owner, "profile": profile, "project": project, "run": run, "items": items, "seed": seed}
    app.dependency_overrides.clear()
    with SessionLocal() as db:
        db.delete(db.get(User, owner))
        db.commit()


def create(context, **changes):
    return client.post(f"/api/v1/recommendation-runs/{context['run']['id']}/project-followups", json={
        "projectId": context["project"]["id"], "seedItemIds": [context["seed"]["id"]], **changes,
    }, headers={"Idempotency-Key": "followup-acceptance-0001"})


def test_followup_creation_is_idempotent_and_rejects_excluded_unmatched_or_foreign_inputs(context):
    for item in context["items"]:
        if item["id"] != context["seed"]["id"]:
            assert create(context, seedItemIds=[item["id"]]).status_code == 422
    assert create(context, projectId=str(uuid4())).status_code == 404
    assert create(context, seedItemIds=[str(uuid4())]).status_code == 422
    assert create(context, seedItemIds=[context["seed"]["id"]] * 2).status_code == 422
    first = create(context)
    assert first.status_code == 202
    assert create(context).json()["id"] == first.json()["id"]
    path = f"/api/v1/recommendation-runs/{context['run']['id']}/project-followups"
    assert client.post(path, json={"projectId": context["project"]["id"], "seedItemIds": [context["seed"]["id"]]}).status_code == 409
    assert client.get(f"{path}/latest", params={"projectId": context["project"]["id"]}).json()["id"] == first.json()["id"]
    app.dependency_overrides[get_current_user_id] = lambda: uuid4()
    assert client.get(f"/api/v1/project-followup-runs/{first.json()['id']}").status_code == 404
    assert client.post(f"/api/v1/project-followup-runs/{first.json()['id']}/retry").status_code == 404
    assert client.get(f"/api/v1/project-followup-runs/{first.json()['id']}/export").status_code == 404


def fake_output(context):
    from app.adapters.project_followups import FollowupCandidate
    return [FollowupCandidate(seed_item_id=context["seed"]["id"], level=1, question="RAG 重排如何评估效果？", project_quote="RAG 向量检索和重排", seed_quote="为什么需要重排")]


def model_config(monkeypatch):
    from app.adapters.text_provider import TextModelConfig
    monkeypatch.setattr("app.modules.project_followup_service.get_text_model_config", lambda: TextModelConfig("deepseek", "fake-key", "https://example.test", "test-model"))


def test_worker_saves_grounded_questions_without_changing_original_library(context, monkeypatch):
    from app.modules.project_followup_service import process_project_followup
    model_config(monkeypatch)
    run = create(context).json()
    calls = []
    def generator(payload, **kwargs):
        calls.append(payload)
        assert "answer" not in str(payload) and "evidence" not in payload["project"]
        return fake_output(context)
    with SessionLocal() as db:
        count = db.query(Question).filter_by(owner_id=context["owner"]).count()
    process_project_followup(UUID(run["id"]), generator=generator)
    process_project_followup(UUID(run["id"]), generator=generator)
    saved = client.get(f"/api/v1/project-followup-runs/{run['id']}").json()
    assert saved["status"] == "SUCCEEDED" and saved["isCurrent"] is True
    assert len(saved["items"]) == 1 and saved["model"] == "test-model" and len(calls) == 1
    assert saved["seeds"][0]["prompt"] == context["seed"]["prompt"]
    export = client.get(f"/api/v1/project-followup-runs/{run['id']}/export")
    assert export.status_code == 200 and "text/markdown" in export.headers["content-type"]
    assert "AI 推演" in export.text and "项目说明依据" in export.text and "种子题依据" in export.text
    with SessionLocal() as db:
        assert db.query(Question).filter_by(owner_id=context["owner"]).count() == count
    profile = context["profile"]
    client.put("/api/v1/profiles/me/target-role", json={"targetRole": "变更岗位", "expectedVersion": profile["version"]})
    assert client.get(f"/api/v1/project-followup-runs/{run['id']}").json()["isCurrent"] is False
    assert "历史" in client.get(f"/api/v1/project-followup-runs/{run['id']}/export").text
    assert create(context).json()["id"] == run["id"]  # Idempotent replay remains readable.


def test_worker_handles_partial_failures_configuration_and_bounded_retry(context, monkeypatch):
    from app.modules.project_followup_service import process_project_followup
    from app.adapters.glm_text import TextModelError
    model_config(monkeypatch)
    run = create(context).json()
    path = f"/api/v1/project-followup-runs/{run['id']}"
    def broken(*args, **kwargs):
        raise TextModelError("TEST_UNAVAILABLE", "测试连接失败", retryable=True)
    process_project_followup(UUID(run["id"]), generator=broken)
    assert client.get(path).json()["status"] == "FAILED"
    assert client.get(path + "/export").status_code == 409
    for index in range(3):
        headers = {"Idempotency-Key": f"retry-acceptance-{index}"}
        retry = client.post(path + "/retry", headers=headers)
        assert retry.status_code == 202
        assert client.post(path + "/retry", headers=headers).json()["retryCount"] == index + 1
        process_project_followup(UUID(run["id"]), generator=broken)
    assert client.post(path + "/retry").status_code == 409


def test_generation_aborts_if_inputs_change_during_model_call(context, monkeypatch):
    from app.modules.project_followup_service import process_project_followup
    model_config(monkeypatch)
    run = create(context).json()
    def generator(*args, **kwargs):
        client.put("/api/v1/profiles/me/target-role", json={"targetRole": "更新", "expectedVersion": context["profile"]["version"]})
        return fake_output(context)
    process_project_followup(UUID(run["id"]), generator=generator)
    saved = client.get(f"/api/v1/project-followup-runs/{run['id']}").json()
    assert saved["status"] == "FAILED" and saved["items"] == []
    assert saved["errorCode"] == "FOLLOWUP_INPUT_STALE"
    assert client.post(f"/api/v1/project-followup-runs/{run['id']}/retry").status_code == 409


def test_partial_results_and_history_are_preserved(context, monkeypatch):
    from app.adapters.project_followups import FollowupCandidate
    from app.modules.project_followup_service import process_project_followup
    model_config(monkeypatch)
    run = create(context).json()
    bad = FollowupCandidate(seed_item_id=context["seed"]["id"], level=2, question="Java 中 RAG 如何使用？", project_quote="使用 Python", seed_quote="为什么需要重排")
    process_project_followup(UUID(run["id"]), generator=lambda *args, **kwargs: [*fake_output(context), bad])
    saved = client.get(f"/api/v1/project-followup-runs/{run['id']}").json()
    assert saved["status"] == "PARTIAL_SUCCESS" and saved["rejectionCounts"] == {"excluded": 1}
    assert len(saved["items"]) == 1
    history = client.get("/api/v1/project-followup-runs", params={"projectId": context["project"]["id"]}).json()
    assert history["items"][0]["id"] == run["id"]
    app.dependency_overrides[get_current_user_id] = lambda: uuid4()
    assert client.get("/api/v1/project-followup-runs", params={"projectId": context["project"]["id"]}).json()["items"] == []


def test_missing_model_and_queue_failures_are_recoverable(context, monkeypatch):
    from app.modules.project_followup_service import process_project_followup
    from app.adapters.text_provider import TextModelConfig
    monkeypatch.setattr("app.modules.project_followup_service.get_text_model_config", lambda: TextModelConfig("glm", "", "https://example.test", "test"))
    run = create(context).json()
    process_project_followup(UUID(run["id"]))
    saved = client.get(f"/api/v1/project-followup-runs/{run['id']}").json()
    assert saved["status"] == "WAITING_FOR_CONFIGURATION" and saved["canRetry"]
    def unavailable(*args, **kwargs):
        raise RuntimeError("synthetic queue error")
    monkeypatch.setattr("app.routes.project_followups.celery_app.send_task", unavailable)
    retry = client.post(f"/api/v1/project-followup-runs/{run['id']}/retry")
    assert retry.status_code == 202 and retry.json()["status"] == "FAILED"
    assert retry.json()["errorCode"] == "QUEUE_UNAVAILABLE"


def test_stale_admission_and_input_size_are_rejected(context):
    from app.models import RecommendationItem
    with SessionLocal() as db:
        db.get(RecommendationItem, UUID(context["seed"]["id"])).prompt = "RAG" * 7000
        db.commit()
    assert create(context).status_code == 422
    client.put("/api/v1/profiles/me/target-role", json={"targetRole": "新岗位", "expectedVersion": context["profile"]["version"]})
    assert create(context).status_code == 409


def test_expired_jobs_can_retry_without_accepting_late_previous_attempt(context, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.models import ProjectFollowupRun
    from app.modules.project_followup_service import _finish, process_project_followup
    model_config(monkeypatch)
    run = create(context).json()
    run_id = UUID(run["id"])
    with SessionLocal() as db:
        db.get(ProjectFollowupRun, run_id).updated_at = datetime.now(timezone.utc) - timedelta(seconds=601)
        db.commit()
    path = f"/api/v1/project-followup-runs/{run_id}"
    assert client.get(path).json()["canRetry"]
    assert client.post(path + "/retry").json()["retryCount"] == 1
    def generator(*args, **kwargs):
        _finish(run_id, 0, status="FAILED", code="OLD_ATTEMPT", message="过时的响应")
        assert client.get(path).json()["status"] == "PROCESSING"
        return fake_output(context)
    process_project_followup(run_id, generator=generator)
    assert client.get(path).json()["status"] == "SUCCEEDED"
