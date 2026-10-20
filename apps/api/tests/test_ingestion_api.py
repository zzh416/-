import base64
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


client = TestClient(app)


def _create_collection() -> str:
    response = client.post(
        "/api/v1/collections",
        json={
            "name": f"P2 导入测试-{uuid4()}",
            "purpose": "INTERVIEW_EXPERIENCE",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_png_upload_creates_queued_asset_and_ingestion_job() -> None:
    collection_id = _create_collection()
    asset_id: str | None = None
    one_pixel_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )

    try:
        with patch("app.workers.celery_app.celery_app.send_task") as send_task:
            response = client.post(
                f"/api/v1/collections/{collection_id}/assets/upload",
                files={"file": ("question.png", one_pixel_png, "image/png")},
                headers={"Idempotency-Key": f"image-{uuid4()}"},
            )

        assert response.status_code == 201
        payload = response.json()
        asset_id = payload["id"]
        assert payload["sourceFormat"] == "IMAGE"
        assert payload["status"] == "QUEUED"
        assert payload["currentVersion"] == 0
        assert payload["originalFilename"] == "question.png"
        assert payload["job"]["status"] == "QUEUED"
        assert payload["job"]["stage"] == "STORED"
        assert payload["job"]["retryCount"] == 0
        send_task.assert_called_once()

        detail = client.get(f"/api/v1/assets/{asset_id}")
        assert detail.status_code == 200
        assert detail.json()["content"] == ""
        assert detail.json()["versions"] == []
    finally:
        if asset_id is not None:
            client.delete(f"/api/v1/assets/{asset_id}")
        client.delete(f"/api/v1/collections/{collection_id}")


def test_url_import_creates_queued_asset_but_rejects_private_targets() -> None:
    collection_id = _create_collection()
    asset_id: str | None = None
    try:
        with patch("app.workers.celery_app.celery_app.send_task") as send_task:
            created = client.post(
                f"/api/v1/collections/{collection_id}/assets/url",
                json={"url": "https://example.com/interview/python", "title": "公开面经"},
                headers={"Idempotency-Key": f"url-{uuid4()}"},
            )

        assert created.status_code == 201
        payload = created.json()
        asset_id = payload["id"]
        assert payload["sourceFormat"] == "WEB_PAGE"
        assert payload["sourceUrl"] == "https://example.com/interview/python"
        assert payload["status"] == "QUEUED"
        assert payload["job"]["status"] == "QUEUED"
        send_task.assert_called_once()

        blocked_urls = [
            "http://127.0.0.1:9000/api/v1/health/live",
            "http://localhost/private",
            "http://169.254.169.254/latest/meta-data/",
            "file:///etc/passwd",
        ]
        for blocked_url in blocked_urls:
            blocked = client.post(
                f"/api/v1/collections/{collection_id}/assets/url",
                json={"url": blocked_url},
            )
            assert blocked.status_code == 422
    finally:
        if asset_id is not None:
            client.delete(f"/api/v1/assets/{asset_id}")
        client.delete(f"/api/v1/collections/{collection_id}")


def test_upload_rejects_extension_content_mismatch() -> None:
    collection_id = _create_collection()
    try:
        disguised = client.post(
            f"/api/v1/collections/{collection_id}/assets/upload",
            files={"file": ("fake.png", b"this is not a png", "image/png")},
        )
        assert disguised.status_code == 415
        assert "内容与扩展名不一致" in disguised.json()["detail"]
    finally:
        client.delete(f"/api/v1/collections/{collection_id}")


def test_image_upload_enforces_ocr_provider_size_limit() -> None:
    collection_id = _create_collection()
    one_pixel_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    try:
        with patch.object(settings, "max_ocr_image_bytes", len(one_pixel_png) - 1):
            response = client.post(
                f"/api/v1/collections/{collection_id}/assets/upload",
                files={"file": ("too-large.png", one_pixel_png, "image/png")},
            )
        assert response.status_code == 413
        assert "10 MiB" in response.json()["detail"]
    finally:
        client.delete(f"/api/v1/collections/{collection_id}")
