from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.identity import get_current_user_id
from app.main import app
from app.models import User

client = TestClient(app)


@pytest.fixture
def owner(monkeypatch):
    owner_id = uuid4()
    with SessionLocal() as db:
        db.add(User(id=owner_id, display_name="AI答案隔离验收"))
        db.commit()
    app.dependency_overrides[get_current_user_id] = lambda: owner_id
    monkeypatch.setattr("app.workers.celery_app.celery_app.send_task", lambda *a, **k: None)
    yield owner_id
    app.dependency_overrides.pop(get_current_user_id, None)
    with SessionLocal() as db:
        db.delete(db.get(User, owner_id))
        db.commit()


def create(question, **kwargs):
    return client.post(f"/api/v1/questions/{question['id']}/ai-answer-runs", json={
        "expectedQuestionVersion": question["version"], "useProfile": False, "includeNote": False, **kwargs})


def complete(run, **kwargs):
    from app.adapters.ai_answer import GeneratedAnswer
    from app.modules.ai_answer_service import process_ai_answer
    process_ai_answer(UUID(run["id"]), generator=lambda *a, **k: GeneratedAnswer(
        content="Python 生成器按需产生值，适合流式处理；应根据具体场景核对。", basis="GENERAL_KNOWLEDGE", citations=[], **kwargs))
    return client.get(f"/api/v1/ai-answer-runs/{run['id']}").json()


def review(run, action, **kwargs):
    return client.put(f"/api/v1/ai-answer-runs/{run['id']}", json={"expectedVersion": run["version"], "action": action, **kwargs})


def test_draft_confirm_edit_discard_provenance_and_effective_answer(owner):
    q = client.post("/api/v1/questions", json={"prompt": "Python 生成器的作用是什么？"}).json()
    response = create(q)
    assert response.status_code == 202
    draft = complete(response.json())
    assert draft["status"] == "SUCCEEDED" and draft["reviewStatus"] == "DRAFT" and draft["isCurrent"]
    current = client.get(f"/api/v1/questions/{q['id']}").json()
    assert current["answer"] is None and current["effectiveAnswer"] is None
    assert current["aiAnswer"]["sourceType"] == "AI_GENERATED"
    confirmed = review(draft, "CONFIRM").json()
    current = client.get(f"/api/v1/questions/{q['id']}").json()
    assert current["answer"] is None and current["answerState"] == "AI_ANSWER"
    assert current["effectiveAnswer"]["sourceType"] == "AI_GENERATED"
    edited = review(confirmed, "SAVE_DRAFT", content="人工修改的AI草稿内容").json()
    assert edited["reviewStatus"] == "DRAFT" and edited["sourceType"] == "AI_GENERATED"
    assert client.get(f"/api/v1/questions/{q['id']}").json()["effectiveAnswer"] is None
    discarded = review(edited, "DISCARD").json()
    assert discarded["reviewStatus"] == "DISCARDED"


def test_source_answer_is_never_overwritten(owner):
    q = client.post("/api/v1/questions", json={"prompt": "Python 生成器是什么？", "answer": "原本人工写的答案"}).json()
    confirmed = review(complete(create(q).json()), "CONFIRM").json()
    current = client.get(f"/api/v1/questions/{q['id']}").json()
    assert confirmed["reviewStatus"] == "CONFIRMED"
    assert current["answer"]["content"] == "原本人工写的答案"
    assert current["effectiveAnswer"]["sourceType"] == "USER_WRITTEN"


def question_view(q):
    return client.get(f"/api/v1/questions/{q['id']}").json()


def lookup(q):
    return client.post("/api/v1/learning/lookup", json={"questionIds": [q["id"]]}).json()["items"][0]


def test_draft_not_counted_confirmation_propagates_and_versions_do_not_collide(owner):
    q = client.post("/api/v1/questions", json={"prompt": "Python 生成器的作用是什么？"}).json()
    draft = complete(create(q).json())
    run = client.post("/api/v1/recommendation-runs", json={}).json()
    assert run["missingCount"] == 1
    assert client.get("/api/v1/questions", params={"answerState": "ANSWERED"}).json()["total"] == 0
    confirmed = review(draft, "CONFIRM").json()
    assert not client.get(f"/api/v1/recommendation-runs/{run['id']}").json()["isCurrent"]
    assert client.get("/api/v1/questions", params={"answerState": "ANSWERED", "q": "流式处理"}).json()["total"] == 1
    run = client.post("/api/v1/recommendation-runs", json={}).json()
    item = client.get(f"/api/v1/recommendation-runs/{run['id']}/items").json()["items"][0]
    assert item["answerSourceType"] == "AI_GENERATED" and item["answerVersion"] is None
    assert item["aiAnswerId"] == confirmed["id"] and item["aiAnswerVersion"] == confirmed["version"]
    assert "AI补充参考答案" in client.get(f"/api/v1/recommendation-runs/{run['id']}/export").text
    state = lookup(q)
    assert state["status"] == "UNMARKED"
    payload = {"status": "MASTERED", "expectedVersion": state["version"], "questionVersion": q["version"],
               "answerVersion": None, "aiAnswerId": state["aiAnswerId"], "aiAnswerVersion": state["aiAnswerVersion"]}
    assert client.put(f"/api/v1/learning/questions/{q['id']}", json=payload).status_code == 200
    assert not lookup(q)["contentChanged"]
    assert client.get("/api/v1/learning/items", params={"state": "ANY"}).json()["items"][0]["answerSourceType"] == "AI_GENERATED"
    review(confirmed, "CONFIRM", content="生成器的人工修订AI参考，需核对适用条件。")
    assert lookup(q)["contentChanged"] and lookup(q)["status"] == "MASTERED"
    payload["expectedVersion"] = 1
    assert client.put(f"/api/v1/learning/questions/{q['id']}", json=payload).status_code == 409


def test_new_draft_failure_and_discard_keep_old_confirmed_answer(owner):
    from app.adapters.glm_text import TextModelError
    from app.modules.ai_answer_service import process_ai_answer
    q = client.post("/api/v1/questions", json={"prompt": "Python 生成器的作用是什么？"}).json()
    first = review(complete(create(q).json()), "CONFIRM").json()
    newer = complete(create(q).json())
    assert question_view(q)["aiAnswer"]["id"] == newer["id"]
    assert question_view(q)["effectiveAnswer"]["aiAnswerId"] == first["id"]
    review(newer, "DISCARD")
    failed = create(q).json()
    def failure(*args, **kwargs):
        raise TextModelError("AI_ANSWER_FAILED", "测试失败", retryable=True)
    process_ai_answer(UUID(failed["id"]), generator=failure)
    assert question_view(q)["aiAnswer"]["status"] == "FAILED"
    assert question_view(q)["effectiveAnswer"]["aiAnswerId"] == first["id"]
    latest = review(complete(create(q).json()), "CONFIRM").json()
    assert question_view(q)["effectiveAnswer"]["aiAnswerId"] == latest["id"]
    review(latest, "DISCARD")
    assert question_view(q)["effectiveAnswer"]["aiAnswerId"] == first["id"]
    review(first, "DISCARD")
    assert question_view(q)["effectiveAnswer"] is None


def test_question_restored_same_text_still_expires_and_original_answer_has_priority(owner):
    q = client.post("/api/v1/questions", json={"prompt": "Python 生成器的作用是什么？"}).json()
    confirmed = review(complete(create(q).json()), "CONFIRM").json()
    response = client.put(f"/api/v1/questions/{q['id']}/answer", json={"content": "人工原答案", "expectedVersion": 0})
    assert response.status_code == 200
    assert question_view(q)["effectiveAnswer"]["sourceType"] == "USER_WRITTEN"
    assert not question_view(q)["aiAnswer"]["isCurrent"]
    client.put(f"/api/v1/questions/{q['id']}", json={"prompt": "Python 如何迭代？", "expectedVersion": 1})
    client.put(f"/api/v1/questions/{q['id']}", json={"prompt": q["prompt"], "expectedVersion": 2})
    assert review(confirmed, "CONFIRM").status_code == 409


def test_note_opt_in_and_preference_invalidation(owner):
    q = client.post("/api/v1/questions", json={"prompt": "Python 生成器的作用是什么？"}).json()
    draft = complete(create(q).json())
    client.put(f"/api/v1/questions/{q['id']}/note", json={"content": "私人备注不要发送", "expectedVersion": 0})
    assert question_view(q)["aiAnswer"]["isCurrent"]
    from app.models import AIAnswerRun
    with SessionLocal() as db:
        snapshot = db.get(AIAnswerRun, UUID(draft["id"])).input_snapshot
        assert snapshot["note"] is None and snapshot["profile"] is None
    note_run = complete(create(q, includeNote=True).json())
    with SessionLocal() as db:
        assert db.get(AIAnswerRun, UUID(note_run["id"])).input_snapshot["note"] == "私人备注不要发送"
    client.put(f"/api/v1/questions/{q['id']}/note", json={"content": "改过的备注", "expectedVersion": 1})
    assert not question_view(q)["aiAnswer"]["isCurrent"]
    assert client.get(f"/api/v1/ai-answer-runs/{draft['id']}").json()["isCurrent"]
    client.post("/api/v1/preference-rules", json={"ruleType": "EXCLUDE", "technology": "Java", "scope": "BOTH"})
    assert not client.get(f"/api/v1/ai-answer-runs/{draft['id']}").json()["isCurrent"]


def test_request_idempotency_concurrency_quota_and_retry_limits(owner, monkeypatch):
    from app.config import settings
    from app.models import AIAnswerRun
    q = client.post("/api/v1/questions", json={"prompt": "Python 生成器的作用是什么？"}).json()
    path = f"/api/v1/questions/{q['id']}/ai-answer-runs"
    payload = {"expectedQuestionVersion": 1, "useProfile": False}
    headers = {"Idempotency-Key": "ai-answer-idempotent-test"}
    first = client.post(path, json=payload, headers=headers).json()
    assert client.post(path, json=payload, headers=headers).json()["id"] == first["id"]
    assert create(q).json()["id"] == first["id"]
    otherq = client.post("/api/v1/questions", json={"prompt": "什么是Python迭代器？"}).json()
    assert create(otherq).status_code == 429
    with SessionLocal() as db:
        row = db.get(AIAnswerRun, UUID(first["id"]))
        row.status = "FAILED"
        db.commit()
    monkeypatch.setattr(settings, "ai_answer_daily_limit", 1)
    assert client.post(f"/api/v1/ai-answer-runs/{first['id']}/retry", json={"expectedVersion": first["version"]}).status_code == 429
    monkeypatch.setattr(settings, "ai_answer_daily_limit", 30)
    monkeypatch.setattr(settings, "ai_answer_retry_limit", 1)
    response = client.post(f"/api/v1/ai-answer-runs/{first['id']}/retry", json={"expectedVersion": first["version"]})
    assert response.status_code == 202
    retried = response.json()
    assert retried["retryCount"] == 1 and retried["retryLimit"] == 1 and not retried["canRetry"]
    assert client.post(f"/api/v1/ai-answer-runs/{first['id']}/retry", json={"expectedVersion": retried["version"]}).status_code == 409


def test_worker_fence_prevents_discarded_result_and_stale_inputs(owner):
    from app.adapters.ai_answer import GeneratedAnswer
    from app.modules.ai_answer_service import process_ai_answer
    q = client.post("/api/v1/questions", json={"prompt": "Python 生成器的作用是什么？"}).json()
    run = create(q).json()
    calls = []
    def cancelled(*args, **kwargs):
        calls.append(1)
        current = client.get(f"/api/v1/ai-answer-runs/{run['id']}").json()
        assert review(current, "DISCARD").status_code == 200
        return GeneratedAnswer(content="生成器通过yield惰性提供数据。", basis="GENERAL_KNOWLEDGE")
    process_ai_answer(UUID(run["id"]), generator=cancelled)
    process_ai_answer(UUID(run["id"]), generator=cancelled)
    assert len(calls) == 1 and question_view(q)["aiAnswer"]["content"] is None
    run = create(q).json()
    def stale(*args, **kwargs):
        client.put(f"/api/v1/questions/{q['id']}", json={"prompt": "Python生成器如何迭代？", "expectedVersion": 1})
        return GeneratedAnswer(content="生成器通过yield惰性提供数据。", basis="GENERAL_KNOWLEDGE")
    process_ai_answer(UUID(run["id"]), generator=stale)
    result = question_view(q)["aiAnswer"]
    assert result["status"] == "FAILED" and result["errorCode"] == "AI_ANSWER_INPUT_STALE" and result["content"] is None


def test_owner_scope_for_every_ai_endpoint(owner):
    q = client.post("/api/v1/questions", json={"prompt": "Python 生成器的作用是什么？"}).json()
    run = complete(create(q).json())
    other = uuid4()
    with SessionLocal() as db:
        db.add(User(id=other, display_name="隔离用户"))
        db.commit()
    app.dependency_overrides[get_current_user_id] = lambda: other
    try:
        assert create(q).status_code == 404
        assert client.get(f"/api/v1/questions/{q['id']}/ai-answer-runs/latest").status_code == 404
        assert client.get(f"/api/v1/ai-answer-runs/{run['id']}").status_code == 404
        assert review(run, "CONFIRM").status_code == 404
        assert client.post(f"/api/v1/ai-answer-runs/{run['id']}/retry", json={"expectedVersion": run["version"]}).status_code == 404
    finally:
        app.dependency_overrides[get_current_user_id] = lambda: owner
        with SessionLocal() as db:
            db.delete(db.get(User, other))
            db.commit()


def test_source_revision_invalidates_previous_ai_and_excludes_old_chunks(owner):
    from app.models import Asset, AssetVersion, TextChunk, AIAnswerRun
    collection = client.post("/api/v1/collections", json={"name": "AI引用测试", "purpose": "KNOWLEDGE_BASE"}).json()
    with SessionLocal() as db:
        asset = Asset(owner_id=owner, collection_id=UUID(collection["id"]), title="来源", source_format="TEXT")
        version = AssetVersion(version=1, content="生成器惰性产生值。")
        chunk = TextChunk(ordinal=0, content=version.content, start_offset=0, end_offset=len(version.content))
        version.chunks.append(chunk)
        asset.versions.append(version)
        db.add(asset)
        db.commit()
        asset_id, chunk_id = asset.id, chunk.id
    q = client.post("/api/v1/questions", json={"prompt": "Python 生成器的作用是什么？", "sourceChunkId": str(chunk_id)}).json()
    confirmed = review(complete(create(q).json()), "CONFIRM").json()
    with SessionLocal() as db:
        db.get(Asset, asset_id).current_version = 2
        db.add(AssetVersion(asset_id=asset_id, version=2, content="更正后的来源内容"))
        db.commit()
    assert not question_view(q)["aiAnswer"]["isCurrent"] and question_view(q)["effectiveAnswer"] is None
    assert review(confirmed, "CONFIRM").status_code == 409
    newrun = create(q).json()
    with SessionLocal() as db:
        assert db.get(AIAnswerRun, UUID(newrun["id"])).input_snapshot["sources"] == []


def test_adapter_derives_basis_and_rejects_fabricated_citations(monkeypatch):
    from app.adapters.ai_answer import generate_ai_answer
    from app.adapters.glm_text import TextModelError
    content = "生成器按需产生值，可减少一次性存储开销。"
    monkeypatch.setattr("app.adapters.ai_answer.request_json", lambda *a, **k: {"content": content})
    assert generate_ai_answer({}).basis == "GENERAL_KNOWLEDGE"
    cid = str(uuid4())
    raw = {"content": content, "basis": "GENERAL_KNOWLEDGE", "citations": [{"chunk_id": cid, "quote": "生成器按需产生值"}]}
    monkeypatch.setattr("app.adapters.ai_answer.request_json", lambda *a, **k: raw)
    assert generate_ai_answer({"sources": [{"chunk_id": cid, "content": content}]}).basis == "SOURCE_ASSISTED"
    with pytest.raises(TextModelError) as error:
        generate_ai_answer({"sources": []})
    assert error.value.code == "AI_ANSWER_INVALID_CITATION"
    raw["citations"][0]["quote"] = "与原文并不相符的引文"
    with pytest.raises(TextModelError):
        generate_ai_answer({"sources": [{"chunk_id": cid, "content": content}]})


def test_structured_profile_only_and_version_change_invalidates_opted_in_answer(owner):
    from app.models import AIAnswerRun
    profile = client.get("/api/v1/profiles/me").json()
    profile = client.post("/api/v1/profiles/me/skills", json={"displayName": "Python", "proficiency": "ADVANCED", "expectedVersion": profile["version"]}).json()
    profile = client.post("/api/v1/profiles/me/projects", json={"name": "待确认经历", "description": "不可发送的未确认经历", "reviewStatus": "PENDING", "expectedVersion": profile["version"]}).json()
    profile = client.post("/api/v1/profiles/me/projects", json={"name": "已确认项目", "description": "使用Python进行数据处理", "reviewStatus": "CONFIRMED", "expectedVersion": profile["version"]}).json()
    q = client.post("/api/v1/questions", json={"prompt": "Python 生成器的作用是什么？"}).json()
    run = complete(create(q, useProfile=True).json())
    with SessionLocal() as db:
        snapshot = db.get(AIAnswerRun, UUID(run["id"])).input_snapshot
        assert "不可发送的未确认经历" not in str(snapshot)
        assert list(snapshot["profile"]) == ["target_role", "skills", "projects"]
        assert snapshot["profile"]["projects"] == [{"name": "已确认项目", "description": "使用Python进行数据处理"}]
    client.put("/api/v1/profiles/me/target-role", json={"targetRole": "Python研发", "expectedVersion": profile["version"]})
    assert not question_view(q)["aiAnswer"]["isCurrent"]


def test_interview_reference_keeps_ai_provenance_and_is_not_returned_as_answer(owner):
    from app.adapters.interview_coach import InterviewReview, REVIEW_PROMPT
    from app.modules.interview_service import process_interview
    q = client.post("/api/v1/questions", json={"prompt": "Python 生成器的作用是什么？"}).json()
    review(complete(create(q).json()), "CONFIRM")
    run = client.post("/api/v1/recommendation-runs", json={}).json()
    session = client.post("/api/v1/interview-sessions", json={"questionSource": "BANK", "questionCount": 3, "recommendationRunId": run["id"], "mode": "PREVIEW"}).json()
    assert session["plan"][0]["referenceAnswerSourceType"] == "AI_GENERATED"
    assert "referenceAnswer" not in session["plan"][0]
    session = client.post(f"/api/v1/interview-sessions/{session['id']}/action", json={"action": "START", "expectedVersion": session["version"]}).json()
    result = client.post(f"/api/v1/interview-sessions/{session['id']}/answer", json={"expectedVersion": session["version"], "turnId": session["turns"][-1]["id"], "text": "生成器通过 yield 语句按需产生结果"})
    assert result.status_code == 200
    received = []
    def reviewer(payload, **kwargs):
        received.append(payload)
        return InterviewReview(summary="已覆盖按需产生值的概念", covered_points=["按需产生值"], gaps=[], change_topic=False)
    process_interview(UUID(session["id"]), reviewer=reviewer)
    assert received[0]["reference_answer_source_type"] == "AI_GENERATED"
    assert received[0]["reference_answer"]
    assert "不是权威标准" in REVIEW_PROMPT and "唯一正确答案" in REVIEW_PROMPT


def test_answer_attachment_uses_constant_query_count_without_ai_records(owner):
    from sqlalchemy import event, select
    from sqlalchemy.orm import selectinload
    from app.db import engine
    from app.models import Question
    from app.modules.ai_answer_service import attach_answers
    with SessionLocal() as db:
        db.add_all(Question(owner_id=owner, source_type="USER_WRITTEN", prompt=f"Python生成器如何使用{i}？") for i in range(120))
        db.commit()
        questions = db.scalars(select(Question).where(Question.owner_id == owner).options(selectinload(Question.answer), selectinload(Question.note))).all()
        queries = []
        def count(*args):
            queries.append(1)
        event.listen(engine, "before_cursor_execute", count)
        try:
            attach_answers(db, questions)
        finally:
            event.remove(engine, "before_cursor_execute", count)
        assert len(queries) == 1
