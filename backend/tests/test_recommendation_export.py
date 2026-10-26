from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.identity import get_current_user_id
from app.main import app
from app.models import Collection, Question, QuestionNote, RecommendationItem, RecommendationRun, User


client = TestClient(app)


@pytest.fixture
def owner():
    owner_id = uuid4()
    with SessionLocal() as db:
        db.add(User(id=owner_id, display_name="导出测试"))
        db.commit()
    app.dependency_overrides[get_current_user_id] = lambda: owner_id
    yield owner_id
    app.dependency_overrides.clear()
    with SessionLocal() as db:
        db.delete(db.get(User, owner_id))
        db.commit()


def make_run(owner, count=25):
    response = client.post("/api/v1/recommendation-runs", json={})
    assert response.status_code == 201
    run_id = UUID(response.json()["id"])
    with SessionLocal() as db:
        run = db.get(RecommendationRun, run_id)
        run.algorithm_version = "test-historical"
        run.included_count = count
        run.total_count = count + 1
        run.excluded_count = 1
        # Insert in reverse order: export must use saved position, not insertion order.
        for position in range(count + 1, 0, -1):
            db.add(RecommendationItem(
                run_id=run_id, position=position, question_version=1,
                prompt=f"测试问题 {position:02d}？" if position <= count else "禁止导出的排除题",
                answer_content="```python\nprint('中文')\n```" if position == 1 else None,
                decision="INCLUDED" if position <= count else "EXCLUDED", score=50,
                answer_state="SOURCE_ANSWER" if position == 1 else "MISSING",
                usable_answer_state="ANSWERED" if position == 1 else "MISSING",
            ))
        db.commit()
    return run_id


def test_export_all_pages_in_snapshot_order_and_download_headers(owner):
    run_id = make_run(owner)
    response = client.get(f"/api/v1/recommendation-runs/{run_id}/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    assert "attachment;" in response.headers["content-disposition"]
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    content = response.content.decode("utf-8")
    assert "导出题目：25" in content
    assert content.count("### 问题") == 25
    assert content.index("测试问题 01") < content.index("测试问题 25")
    assert "禁止导出的排除题" not in content
    assert "```python\nprint('中文')\n```" in content
    assert "历史清单" in content
    assert "来源：未记录" in content
    assert content.count("### 资料答案") == 1
    with SessionLocal() as db:
        assert db.get(RecommendationRun, run_id).included_count == 25


def test_export_never_includes_unusable_answer(owner):
    run_id = make_run(owner, 1)
    with SessionLocal() as db:
        item = db.query(RecommendationItem).filter_by(run_id=run_id, position=1).one()
        item.usable_answer_state = "MISSING"
        item.answer_content = "不允许导出的 Java 答案"
        db.commit()
    content = client.get(f"/api/v1/recommendation-runs/{run_id}/export").text
    assert "来源答案当前不适用" in content
    assert "不允许导出的 Java 答案" not in content


def test_export_uses_saved_sources_with_safe_metadata_links(owner):
    run_id = make_run(owner, 1)
    with SessionLocal() as db:
        item = db.query(RecommendationItem).filter_by(run_id=run_id, position=1).one()
        item.source_statistics = {
            "source_count": 2, "interview_source_count": 1, "knowledge_source_count": 1,
            "sources": [
                {"asset_id": str(uuid4()), "title": "历史[博客]\n标题", "purpose": "INTERVIEW_EXPERIENCE", "source_url": "https://example.com/a(b)?q=中文", "source_date": "2026-10-01", "duplicate_asset_count": 1},
                {"asset_id": str(uuid4()), "title": "不安全链接", "purpose": "KNOWLEDGE_BASE", "source_url": "javascript:alert(1)", "source_date": None, "duplicate_asset_count": 1},
            ],
        }
        db.commit()
    content = client.get(f"/api/v1/recommendation-runs/{run_id}/export").text
    assert "面经 1" in content and "知识库 1" in content
    assert "2026-10-01" in content
    assert r"历史\[博客\] 标题" in content
    assert "https://example.com/a%28b%29?q=" in content
    assert "javascript:" not in content
    assert "日期未记录" in content


def test_export_notes_are_opt_in_current_and_owned(owner):
    run_id = make_run(owner, 1)
    foreign_id = uuid4()
    with SessionLocal() as db:
        collection = Collection(owner_id=owner, name="导出备注", purpose="KNOWLEDGE_BASE")
        db.add_all([collection, User(id=foreign_id, display_name="外部备注")])
        db.flush()
        question = Question(owner_id=owner, collection_id=collection.id, prompt="修改后的原题", source_type="MANUAL")
        db.add(question)
        db.flush()
        note = QuestionNote(owner_id=owner, question_id=question.id, content="当前个人备注")
        db.add(note)
        item = db.query(RecommendationItem).filter_by(run_id=run_id, position=1).one()
        item.question_id = question.id
        db.commit()
        question_id, note_id = question.id, note.id
    try:
        path = f"/api/v1/recommendation-runs/{run_id}/export"
        assert "当前个人备注" not in client.get(path).text
        content = client.get(path, params={"includeNotes": True}).text
        assert "当前个人备注" in content
        assert "导出时的个人备注" in content
        assert "修改后的原题" not in content
        # Both sides of the note/question relation must be scoped, even for inconsistent data.
        with SessionLocal() as db:
            db.get(QuestionNote, note_id).owner_id = foreign_id
            db.commit()
        assert "当前个人备注" not in client.get(path, params={"includeNotes": True}).text
        with SessionLocal() as db:
            db.get(QuestionNote, note_id).owner_id = owner
            db.get(Question, question_id).owner_id = foreign_id
            db.commit()
        assert "当前个人备注" not in client.get(path, params={"includeNotes": True}).text
    finally:
        with SessionLocal() as db:
            db.delete(db.get(User, foreign_id))
            db.commit()


def test_export_owner_isolation_and_unknown_run(owner):
    run_id = make_run(owner, 1)
    app.dependency_overrides[get_current_user_id] = lambda: uuid4()
    assert client.get(f"/api/v1/recommendation-runs/{run_id}/export").status_code == 404
    assert client.get(f"/api/v1/recommendation-runs/{uuid4()}/export").status_code == 404


def test_empty_run_export_is_explicit(owner):
    response = client.post("/api/v1/recommendation-runs", json={})
    path = f"/api/v1/recommendation-runs/{response.json()['id']}/export"
    response = client.get(path)
    assert response.status_code == 409
    assert "没有可导出的题目" in response.json()["detail"]


def test_real_filtered_run_keeps_profile_rules_and_old_question_answer_snapshots(owner):
    collection = client.post("/api/v1/collections", json={"name": "真实导出流程", "purpose": "KNOWLEDGE_BASE"}).json()
    question = client.post("/api/v1/questions", json={
        "collectionId": collection["id"], "prompt": "Python 生成器如何工作？", "answer": "使用 yield 返回值。",
    }).json()
    client.post("/api/v1/questions", json={"collectionId": collection["id"], "prompt": "MySQL 索引如何工作？"})
    profile = client.get("/api/v1/profiles/me").json()
    client.post("/api/v1/profiles/me/skills", json={
        "displayName": "Python", "proficiency": "ADVANCED", "expectedVersion": profile["version"],
    })
    rule = client.post("/api/v1/preference-rules", json={"ruleType": "EXCLUDE", "technology": "MySQL", "scope": "BOTH"})
    assert rule.status_code == 201
    run = client.post("/api/v1/recommendation-runs", json={}).json()
    assert run["includedCount"] == 1
    path = f"/api/v1/recommendation-runs/{run['id']}/export"
    content = client.get(path).text
    assert "当前清单：" in content and "历史清单" not in content
    assert "Python（已确认）" in content
    assert "排除 MySQL · 题目和答案 · 版本 1" in content
    assert "MySQL 索引如何工作？" not in content
    with SessionLocal() as db:
        live_question = db.get(Question, UUID(question["id"]))
        live_question.prompt = "后来改写的题干"
        live_question.version += 1
        live_question.answer.content = "后来修改的答案"
        live_question.answer.version += 1
        db.commit()
    content = client.get(path).text
    assert "历史清单" in content
    assert "Python 生成器如何工作？" in content
    assert "使用 yield 返回值。" in content
    assert "后来" not in content


def test_deleted_question_still_exports_snapshot_without_note(owner):
    run_id = make_run(owner, 1)
    # Null question_id represents a removed question; saved text remains exportable.
    content = client.get(f"/api/v1/recommendation-runs/{run_id}/export?includeNotes=true").text
    assert "测试问题 01？" in content
    assert "### 导出时的个人备注" not in content
