from uuid import uuid4

from fastapi.testclient import TestClient

from app.identity import get_current_user_id
from app.main import app


client = TestClient(app)


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
    finally:
        app.dependency_overrides.clear()
        if question_id is not None:
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
