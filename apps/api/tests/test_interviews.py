from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.adapters.interview_coach import InterviewPlan, InterviewReview
from app.adapters.text_provider import TextModelConfig
from app.db import SessionLocal
from app.identity import get_current_user_id
from app.main import app
from app.models import InterviewSession, User
from app.modules.interview_service import process_interview

client = TestClient(app)


@pytest.fixture
def context(monkeypatch):
    owner = uuid4()
    with SessionLocal() as db:
        db.add(User(id=owner, display_name="模拟面试测试")); db.commit()
    app.dependency_overrides[get_current_user_id] = lambda: owner
    calls = []
    monkeypatch.setattr("app.routes.interviews.celery_app.send_task", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr("app.modules.interview_service.get_text_model_config", lambda: TextModelConfig("deepseek", "synthetic-key", "https://example.test", "test-model"))
    collection = client.post("/api/v1/collections", json={"name": "面试验收", "purpose": "KNOWLEDGE_BASE"}).json()
    for prompt in ["RAG 为什么需要重排？", "RAG 如何评估效果？", "Python 的 GIL 是什么？", "Java 的 HashMap 是什么？"]:
        client.post("/api/v1/questions", json={"collectionId": collection["id"], "prompt": prompt, "answer": "不能在面试前展示的参考内容"})
    client.post("/api/v1/preference-rules", json={"ruleType": "EXCLUDE", "technology": "Java", "scope": "BOTH"})
    run = client.post("/api/v1/recommendation-runs", json={}).json()
    yield {"owner": owner, "run": run, "calls": calls}
    app.dependency_overrides.clear()
    with SessionLocal() as db:
        db.delete(db.get(User, owner)); db.commit()


def create(context, **changes):
    response = client.post("/api/v1/interview-sessions", json={"recommendationRunId": context["run"]["id"], "questionCount": 3, **changes}, headers={"Idempotency-Key": "interview-acceptance-1"})
    assert response.status_code == 202, response.text
    return response.json()


def act(session, action, **kwargs):
    return client.post(f"/api/v1/interview-sessions/{session['id']}/action", json={"expectedVersion": session["version"], "action": action, **kwargs})


def planner(payload, **kwargs):
    assert "reference_answer" not in str(payload)
    ordered = sorted(payload["candidates"], key=lambda item: ("RAG" not in item["prompt"], "为什么" not in item["prompt"]))
    return InterviewPlan(selected=[{"item_id": item["id"], "reason": "匹配当前技能与方向"} for item in ordered])


def ready(context, **changes):
    session = create(context, **changes)
    process_interview(UUID(session["id"]), planner=planner)
    return client.get(f"/api/v1/interview-sessions/{session['id']}").json()


def test_creation_plan_redaction_idempotency_and_owner_isolation(context):
    session = ready(context)
    assert session["status"] == "READY" and len(session["plan"]) == 3
    assert all(item["prompt"] is None for item in session["plan"])
    assert "不能在面试前展示的参考内容" not in str(session)
    assert create(context)["id"] == session["id"]
    conflict = client.post("/api/v1/interview-sessions", json={"recommendationRunId": context["run"]["id"]})
    assert conflict.status_code == 409
    app.dependency_overrides[get_current_user_id] = lambda: uuid4()
    assert client.get(f"/api/v1/interview-sessions/{session['id']}").status_code == 404
    assert act(session, "START").status_code == 404
    assert client.get("/api/v1/interview-sessions").json()["items"] == []


def test_draft_survives_refresh_timer_does_not_reset_and_unknown_never_calls_model(context):
    session = act(ready(context), "START").json()
    turn = session["turns"][-1]
    path = f"/api/v1/interview-sessions/{session['id']}"
    deadline = turn["deadlineAt"]
    draft = client.put(path + "/draft", json={"expectedVersion": session["version"], "turnId": turn["id"], "text": "我的回答草稿"})
    assert draft.status_code == 200
    restored = client.get(path).json()
    assert restored["turns"][-1]["draft"] == "我的回答草稿"
    assert restored["turns"][-1]["deadlineAt"] == deadline
    assert act(session, "PAUSE").status_code == 409
    paused = act(restored, "PAUSE").json()
    assert paused["status"] == "PAUSED" and paused["turns"][-1]["deadlineAt"] is None
    resumed = act(paused, "RESUME").json()
    before = len(context["calls"])
    answer = client.post(path + "/answer", json={"expectedVersion": resumed["version"], "turnId": turn["id"], "text": "我不知道", "action": "ANSWER"})
    assert answer.status_code == 200
    changed = answer.json()
    assert changed["turns"][0]["answerStatus"] == "DONT_KNOW"
    assert changed["turns"][-1]["prompt"] == "Python 的 GIL 是什么？"
    assert changed["skippedPlanCount"] == 1 and len(context["calls"]) == before
    assert client.post(path + "/answer", json={"expectedVersion": resumed["version"], "turnId": turn["id"], "text": "重复提交"}).status_code == 409


def test_answer_feedback_optional_followup_and_late_job_cannot_overwrite_next_question(context):
    session = act(ready(context), "START").json()
    path = f"/api/v1/interview-sessions/{session['id']}"
    submitted = client.post(path + "/answer", json={"expectedVersion": session["version"], "turnId": session["turns"][-1]["id"], "text": "先通过向量检索召回，再使用重排进行排序。"}).json()
    assert submitted["status"] == "REVIEWING" and submitted["turns"][-1]["deadlineAt"] is None
    def reviewer(payload, **kwargs):
        assert payload["answer"].startswith("先通过向量")
        return InterviewReview(summary="可以进一步说明评估方法", covered_points=["提到检索和重排"], gaps=["评估方法不清晰"], change_topic=False,
                               followup={"question": "RAG 如何评估效果？", "answer_quote": "通过向量检索召回", "project_quote": None})
    process_interview(UUID(session["id"]), reviewer=reviewer)
    reviewed = client.get(path).json()
    assert reviewed["status"] == "AWAITING_NEXT"
    assert reviewed["turns"][-1]["feedback"]["gaps"]
    # This proposed followup already exists in the plan, so it must not be offered twice.
    assert reviewed["turns"][-1]["followup"] is None
    next_question = act(reviewed, "NEXT").json()
    resubmitted = client.post(path + "/answer", json={"expectedVersion": next_question["version"], "turnId": next_question["turns"][-1]["id"], "text": "比较检索召回与重排后的排序效果。"}).json()
    def late(payload, **kwargs):
        current_session = client.get(path).json()
        assert act(current_session, "NEXT").status_code == 200
        return InterviewReview(summary="迟到反馈", covered_points=[], gaps=[], change_topic=False)
    process_interview(UUID(session["id"]), reviewer=late)
    final = client.get(path).json()
    assert final["turns"][-1]["prompt"] == "Python 的 GIL 是什么？"
    assert all(not item["feedback"] or item["feedback"]["summary"] != "迟到反馈" for item in final["turns"])


def test_model_failure_keeps_answers_and_allows_bank_fallback(context):
    from app.adapters.glm_text import TextModelNotConfigured
    session = create(context, mode="PREVIEW", questionSeconds=120)
    def missing(*args, **kwargs):
        raise TextModelNotConfigured("尚未配置模型")
    process_interview(UUID(session["id"]), planner=missing)
    failed = client.get(f"/api/v1/interview-sessions/{session['id']}").json()
    assert failed["status"] == "PLAN_FAILED"
    fallback = act(failed, "USE_BANK").json()
    assert fallback["status"] == "READY" and fallback["plan"][0]["prompt"]
    started = act(fallback, "START").json()
    assert started["turns"][-1]["allocatedSeconds"] == 120
    ended = act(started, "FINISH").json()
    assert ended["status"] == "COMPLETED"


def test_followup_requires_explicit_choice_and_unknown_answer_ends_that_direction(context):
    session = act(ready(context), "START").json()
    path = f"/api/v1/interview-sessions/{session['id']}"
    submitted = client.post(path + "/answer", json={"expectedVersion": session["version"], "turnId": session["turns"][-1]["id"], "text": "先通过向量检索召回，再重排。"}).json()
    result = InterviewReview(summary="可以解释召回问题", covered_points=["给出了步骤"], gaps=["缺少诊断过程"], change_topic=False,
                             followup={"question": "RAG 向量检索召回不足时如何定位？", "answer_quote": "通过向量检索召回"})
    process_interview(UUID(session["id"]), reviewer=lambda *args, **kwargs: result)
    reviewed = client.get(path).json()
    assert len(reviewed["turns"]) == 1 and reviewed["turns"][-1]["followup"]
    following = act(reviewed, "FOLLOWUP").json()
    assert following["turns"][-1]["depth"] == 1 and following["turns"][-1]["allocatedSeconds"] == 180
    calls = len(context["calls"])
    changed = client.post(path + "/answer", json={"expectedVersion": following["version"], "turnId": following["turns"][-1]["id"], "text": "", "action": "DONT_KNOW"}).json()
    assert changed["turns"][-1]["prompt"] == "Python 的 GIL 是什么？"
    assert len(context["calls"]) == calls
