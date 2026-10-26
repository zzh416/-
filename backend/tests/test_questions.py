from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.db import SessionLocal
from app.identity import get_current_user_id
from app.main import app
from app.models import User


client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_question_test_user():
    owner_id = uuid4()
    with SessionLocal() as db:
        db.add(User(id=owner_id, display_name="问答测试用户"))
        db.commit()
    app.dependency_overrides[get_current_user_id] = lambda: owner_id
    try:
        yield owner_id
    finally:
        app.dependency_overrides.clear()
        with SessionLocal() as db:
            user = db.get(User, owner_id)
            if user is not None:
                db.delete(user)
                db.commit()


def _create_collection() -> str:
    response = client.post(
        "/api/v1/collections",
        json={"name": f"问答测试-{uuid4()}", "purpose": "KNOWLEDGE_BASE"},
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_question_answer_note_search_and_version_conflicts() -> None:
    collection_id = _create_collection()
    question_id: str | None = None
    try:
        headers = {"Idempotency-Key": f"question-{uuid4()}"}
        created = client.post(
            "/api/v1/questions",
            headers=headers,
            json={
                "collectionId": collection_id,
                "prompt": "Python 的 GIL 是什么？",
                "answer": "GIL 是 CPython 的全局解释器锁。",
            },
        )
        assert created.status_code == 201
        question = created.json()
        assert question["sourceType"] == "USER_WRITTEN"
        assert question["answer"]["version"] == 1
        question_id = question["id"]
        replayed = client.post(
            "/api/v1/questions",
            headers=headers,
            json={
                "collectionId": collection_id,
                "prompt": "Python 的 GIL 是什么？",
                "answer": "GIL 是 CPython 的全局解释器锁。",
            },
        )
        assert replayed.status_code == 201
        assert replayed.json()["id"] == question_id

        no_match = client.get("/api/v1/questions", params={"q": "Java"})
        assert no_match.status_code == 200
        assert all(item["id"] != question_id for item in no_match.json()["items"])

        match = client.get(
            "/api/v1/questions",
            params={"q": "cpython", "collectionId": collection_id},
        )
        assert match.status_code == 200
        assert match.json()["total"] >= 1
        assert any(item["id"] == question_id for item in match.json()["items"])

        updated = client.put(
            f"/api/v1/questions/{question_id}/answer",
            json={"content": "更新后的答案", "expectedVersion": 1},
        )
        assert updated.status_code == 200
        assert updated.json()["version"] == 2

        stale = client.put(
            f"/api/v1/questions/{question_id}/answer",
            json={"content": "过期答案", "expectedVersion": 1},
        )
        assert stale.status_code == 409

        history = client.get(f"/api/v1/questions/{question_id}/history")
        assert history.status_code == 200
        answer_changes = [
            item for item in history.json()["items"] if item["field"] == "ANSWER"
        ]
        assert answer_changes[0]["previousValue"] == "GIL 是 CPython 的全局解释器锁。"
        assert answer_changes[0]["newValue"] == "更新后的答案"

        stale_delete = client.delete(
            f"/api/v1/questions/{question_id}/answer",
            params={"expectedVersion": 1},
        )
        assert stale_delete.status_code == 409
        deleted_answer = client.delete(
            f"/api/v1/questions/{question_id}/answer",
            params={"expectedVersion": 2},
        )
        assert deleted_answer.status_code == 204
        assert client.get(f"/api/v1/questions/{question_id}").json()["answer"] is None

        history = client.get(f"/api/v1/questions/{question_id}/history").json()
        deletion = next(item for item in history["items"] if item["action"] == "DELETED")
        assert deletion["previousValue"] == "更新后的答案"
        assert deletion["newValue"] is None

        restored = client.post(
            f"/api/v1/questions/{question_id}/history/{deletion['id']}/restore",
            json={"expectedVersion": 0},
        )
        assert restored.status_code == 200
        assert restored.json()["answer"]["content"] == "更新后的答案"
        assert restored.json()["answer"]["version"] == 4

        stale_after_restore = client.put(
            f"/api/v1/questions/{question_id}/answer",
            json={"content": "不应覆盖恢复后的答案", "expectedVersion": 1},
        )
        assert stale_after_restore.status_code == 409

        renamed = client.put(
            f"/api/v1/questions/{question_id}",
            json={"prompt": "请解释 Python 的 GIL。", "expectedVersion": 1},
        )
        assert renamed.status_code == 200
        question_history = client.get(f"/api/v1/questions/{question_id}/history").json()
        prompt_change = next(
            item for item in question_history["items"] if item["field"] == "QUESTION"
        )
        assert prompt_change["previousValue"] == "Python 的 GIL 是什么？"
        assert prompt_change["newValue"] == "请解释 Python 的 GIL。"

        noted = client.put(
            f"/api/v1/questions/{question_id}/note",
            json={"content": "复习时补充线程切换例子", "expectedVersion": 0},
        )
        assert noted.status_code == 200
        assert noted.json()["version"] == 1

        detail = client.get(f"/api/v1/questions/{question_id}")
        assert detail.status_code == 200
        assert detail.json()["note"]["content"].startswith("复习时")

        deleted = client.delete(f"/api/v1/questions/{question_id}/note")
        assert deleted.status_code == 204
        assert client.get(f"/api/v1/questions/{question_id}").json()["note"] is None
    finally:
        app.dependency_overrides.clear()
        if question_id is not None:
            client.delete(f"/api/v1/questions/{question_id}")
        client.delete(f"/api/v1/collections/{collection_id}")


def test_question_can_have_no_answer_and_is_owner_scoped() -> None:
    collection_id = _create_collection()
    question_id: str | None = None
    try:
        created = client.post(
            "/api/v1/questions",
            json={"collectionId": collection_id, "prompt": "这道题暂时不会"},
        )
        assert created.status_code == 201
        assert created.json()["answer"] is None
        question_id = created.json()["id"]

        app.dependency_overrides[get_current_user_id] = lambda: uuid4()
        assert client.get(f"/api/v1/questions/{question_id}").status_code == 404
        assert client.get(f"/api/v1/questions/{question_id}/history").status_code == 404
    finally:
        app.dependency_overrides.clear()
        if question_id is not None:
            client.delete(f"/api/v1/questions/{question_id}")
        client.delete(f"/api/v1/collections/{collection_id}")


def test_question_list_filters_answer_state_and_returns_counts() -> None:
    collection_id = _create_collection()
    question_ids: list[str] = []
    try:
        answered = client.post(
            "/api/v1/questions",
            json={
                "collectionId": collection_id,
                "prompt": "Python 装饰器是什么？",
                "answer": "装饰器用于包装可调用对象。",
            },
        ).json()
        missing = client.post(
            "/api/v1/questions",
            json={"collectionId": collection_id, "prompt": "解释 Python 描述符协议。"},
        ).json()
        question_ids.extend([answered["id"], missing["id"]])

        all_questions = client.get(
            "/api/v1/questions",
            params={"collectionId": collection_id, "answerState": "ANY"},
        )
        assert all_questions.status_code == 200
        assert all_questions.json()["answeredCount"] == 1
        assert all_questions.json()["missingCount"] == 1
        states = {item["id"]: item["answerState"] for item in all_questions.json()["items"]}
        assert states[answered["id"]] == "SOURCE_ANSWER"
        assert states[missing["id"]] == "MISSING"

        answered_only = client.get(
            "/api/v1/questions",
            params={"collectionId": collection_id, "answerState": "ANSWERED"},
        ).json()
        assert [item["id"] for item in answered_only["items"]] == [answered["id"]]
        assert answered_only["total"] == 1
        assert answered_only["answeredCount"] == 1
        assert answered_only["missingCount"] == 1

        missing_only = client.get(
            "/api/v1/questions",
            params={"collectionId": collection_id, "answerState": "MISSING"},
        ).json()
        assert [item["id"] for item in missing_only["items"]] == [missing["id"]]

        invalid = client.get("/api/v1/questions", params={"answerState": "UNKNOWN"})
        assert invalid.status_code == 422
    finally:
        for question_id in question_ids:
            client.delete(f"/api/v1/questions/{question_id}")
        client.delete(f"/api/v1/collections/{collection_id}")


def test_pagination_limits_and_blank_question_validation() -> None:
    blank = client.post("/api/v1/questions", json={"prompt": "   "})
    assert blank.status_code == 422
    too_large = client.get("/api/v1/questions", params={"pageSize": 101})
    assert too_large.status_code == 422


def test_source_chunk_must_belong_to_question_collection() -> None:
    first_collection_id = _create_collection()
    second_collection_id = _create_collection()
    asset_id: str | None = None
    try:
        asset = client.post(
            f"/api/v1/collections/{first_collection_id}/assets/text",
            json={"title": "跨库来源", "content": "原文", "sourceFormat": "PASTED_TEXT"},
        )
        assert asset.status_code == 201
        asset_id = asset.json()["id"]
        chunk_id = client.get(f"/api/v1/assets/{asset_id}").json()["chunks"][0]["id"]

        response = client.post(
            "/api/v1/questions",
            json={
                "collectionId": second_collection_id,
                "sourceChunkId": chunk_id,
                "prompt": "不能跨库关联",
            },
        )
        assert response.status_code == 422
    finally:
        if asset_id is not None:
            client.delete(f"/api/v1/assets/{asset_id}")
        client.delete(f"/api/v1/collections/{first_collection_id}")
        client.delete(f"/api/v1/collections/{second_collection_id}")
