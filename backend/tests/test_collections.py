"""资料库 API 的端到端契约测试。"""
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_create_list_and_delete_collection() -> None:
    name = f"P1 测试资料库 {uuid4()}"
    created_id: str | None = None

    try:
        created_response = client.post(
            "/api/v1/collections",
            json={"name": name, "purpose": "KNOWLEDGE_BASE"},
        )
        assert created_response.status_code == 201
        created = created_response.json()
        created_id = created["id"]
        assert created["name"] == name
        assert created["purpose"] == "KNOWLEDGE_BASE"
        assert created["version"] == 1

        listed_response = client.get("/api/v1/collections")
        assert listed_response.status_code == 200
        listed = listed_response.json()
        assert any(item["id"] == created_id for item in listed["items"])

        deleted_response = client.delete(f"/api/v1/collections/{created_id}")
        assert deleted_response.status_code == 204
        created_id = None
    finally:
        if created_id is not None:
            client.delete(f"/api/v1/collections/{created_id}")


def test_collection_name_cannot_be_blank() -> None:
    response = client.post(
        "/api/v1/collections",
        json={"name": "   ", "purpose": "INTERVIEW_EXPERIENCE"},
    )

    assert response.status_code == 422


def test_idempotency_key_replays_same_create_and_rejects_different_payload() -> None:
    key = f"collection-{uuid4()}"
    name = f"幂等测试-{uuid4()}"
    headers = {"Idempotency-Key": key}
    created_id: str | None = None
    try:
        first = client.post(
            "/api/v1/collections",
            headers=headers,
            json={"name": name, "purpose": "KNOWLEDGE_BASE"},
        )
        assert first.status_code == 201
        created_id = first.json()["id"]

        replay = client.post(
            "/api/v1/collections",
            headers=headers,
            json={"name": name, "purpose": "KNOWLEDGE_BASE"},
        )
        assert replay.status_code == 201
        assert replay.json()["id"] == created_id

        mismatch = client.post(
            "/api/v1/collections",
            headers=headers,
            json={"name": name + "-不同", "purpose": "KNOWLEDGE_BASE"},
        )
        assert mismatch.status_code == 409
    finally:
        if created_id is not None:
            client.delete(f"/api/v1/collections/{created_id}")


def test_non_empty_collection_cannot_be_deleted() -> None:
    created = client.post(
        "/api/v1/collections",
        json={"name": f"删除保护-{uuid4()}", "purpose": "INTERVIEW_EXPERIENCE"},
    )
    collection_id = created.json()["id"]
    asset_id: str | None = None
    try:
        asset = client.post(
            f"/api/v1/collections/{collection_id}/assets/text",
            json={"title": "不可级联删除", "content": "保留原文", "sourceFormat": "PASTED_TEXT"},
        )
        assert asset.status_code == 201
        asset_id = asset.json()["id"]
        blocked = client.delete(f"/api/v1/collections/{collection_id}")
        assert blocked.status_code == 409
    finally:
        if asset_id is not None:
            client.delete(f"/api/v1/assets/{asset_id}")
        client.delete(f"/api/v1/collections/{collection_id}")
