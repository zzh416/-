from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _create_collection() -> str:
    response = client.post(
        "/api/v1/collections",
        json={
            "name": f"资料导入测试-{uuid4()}",
            "purpose": "INTERVIEW_EXPERIENCE",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_paste_text_create_read_and_revise() -> None:
    collection_id = _create_collection()
    asset_id: str | None = None
    try:
        headers = {"Idempotency-Key": f"asset-{uuid4()}"}
        created = client.post(
            f"/api/v1/collections/{collection_id}/assets/text",
            headers=headers,
            json={
                "title": "Python 面经",
                "content": "什么是 GIL？\n它如何影响多线程？",
                "sourceFormat": "PASTED_TEXT",
            },
        )
        assert created.status_code == 201
        payload = created.json()
        assert payload["status"] == "READY"
        assert payload["currentVersion"] == 1

        asset_id = payload["id"]
        replayed = client.post(
            f"/api/v1/collections/{collection_id}/assets/text",
            headers=headers,
            json={
                "title": "Python 面经",
                "content": "什么是 GIL？\n它如何影响多线程？",
                "sourceFormat": "PASTED_TEXT",
            },
        )
        assert replayed.status_code == 201
        assert replayed.json()["id"] == asset_id
        detail = client.get(f"/api/v1/assets/{asset_id}")
        assert detail.status_code == 200
        assert detail.json()["content"].startswith("什么是 GIL")
        assert detail.json()["versions"][0]["version"] == 1
        assert detail.json()["chunks"][0]["startOffset"] == 0

        revised = client.put(
            f"/api/v1/assets/{asset_id}/content",
            json={"content": "更新后的原文", "expectedVersion": 1},
        )
        assert revised.status_code == 200
        assert revised.json()["currentVersion"] == 2

        stale = client.put(
            f"/api/v1/assets/{asset_id}/content",
            json={"content": "过期编辑", "expectedVersion": 1},
        )
        assert stale.status_code == 409

        history = client.get(f"/api/v1/assets/{asset_id}").json()["versions"]
        assert [item["version"] for item in history] == [2, 1]
    finally:
        if asset_id is not None:
            client.delete(f"/api/v1/assets/{asset_id}")
        client.delete(f"/api/v1/collections/{collection_id}")


def test_txt_and_markdown_upload_validate_type_encoding_and_size() -> None:
    collection_id = _create_collection()
    asset_id: str | None = None
    try:
        bom_content = "# Redis\n\n缓存穿透".encode("utf-8-sig")
        uploaded = client.post(
            f"/api/v1/collections/{collection_id}/assets/upload",
            files={"file": ("redis.md", bom_content, "text/markdown")},
        )
        assert uploaded.status_code == 201
        asset_id = uploaded.json()["id"]
        assert uploaded.json()["sourceFormat"] == "MARKDOWN_FILE"
        assert uploaded.json()["originalFilename"] == "redis.md"

        bad_extension = client.post(
            f"/api/v1/collections/{collection_id}/assets/upload",
            files={"file": ("notes.pdf", b"plain text", "text/plain")},
        )
        assert bad_extension.status_code == 415

        bad_encoding = client.post(
            f"/api/v1/collections/{collection_id}/assets/upload",
            files={"file": ("notes.txt", b"\xff\xfe\x00", "text/plain")},
        )
        assert bad_encoding.status_code == 422
    finally:
        if asset_id is not None:
            client.delete(f"/api/v1/assets/{asset_id}")
        client.delete(f"/api/v1/collections/{collection_id}")


def test_empty_and_oversized_text_are_rejected() -> None:
    collection_id = _create_collection()
    try:
        empty = client.post(
            f"/api/v1/collections/{collection_id}/assets/text",
            json={"title": "空资料", "content": "   ", "sourceFormat": "PASTED_TEXT"},
        )
        assert empty.status_code == 422

        oversized = client.post(
            f"/api/v1/collections/{collection_id}/assets/text",
            json={
                "title": "过大资料",
                "content": "a" * (1024 * 1024 + 1),
                "sourceFormat": "PASTED_TEXT",
            },
        )
        assert oversized.status_code == 413
    finally:
        client.delete(f"/api/v1/collections/{collection_id}")


def test_deleting_asset_preserves_question_and_marks_source_removed() -> None:
    collection_id = _create_collection()
    asset_id: str | None = None
    question_id: str | None = None
    try:
        asset = client.post(
            f"/api/v1/collections/{collection_id}/assets/text",
            json={"title": "来源资料", "content": "原文段落", "sourceFormat": "PASTED_TEXT"},
        )
        assert asset.status_code == 201
        asset_id = asset.json()["id"]
        chunk_id = client.get(f"/api/v1/assets/{asset_id}").json()["chunks"][0]["id"]
        question = client.post(
            "/api/v1/questions",
            json={
                "collectionId": collection_id,
                "sourceChunkId": chunk_id,
                "prompt": "来自原文的问题",
            },
        )
        assert question.status_code == 201
        question_id = question.json()["id"]

        deleted = client.delete(f"/api/v1/assets/{asset_id}")
        assert deleted.status_code == 204
        asset_id = None
        preserved = client.get(f"/api/v1/questions/{question_id}")
        assert preserved.status_code == 200
        assert preserved.json()["sourceType"] == "SOURCE_REMOVED"
        assert preserved.json()["sourceChunkId"] is None
    finally:
        if asset_id is not None:
            client.delete(f"/api/v1/assets/{asset_id}")
        if question_id is not None:
            client.delete(f"/api/v1/questions/{question_id}")
        client.delete(f"/api/v1/collections/{collection_id}")
