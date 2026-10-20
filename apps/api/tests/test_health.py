"""健康检查与根路由的契约测试。"""
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app

client = TestClient(app)


class HealthyDB:
    def execute(self, _statement):
        return None


class FailedDB:
    def execute(self, _statement):
        raise ConnectionError("database unavailable")


def override_db(connection):
    def _override():
        yield connection

    return _override


def test_root() -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_liveness_does_not_require_dependencies() -> None:
    resp = client.get("/api/v1/health/live")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "checks": {"api": "ok"}}


def test_unknown_browser_origin_is_rejected() -> None:
    resp = client.get(
        "/api/v1/health/live",
        headers={"Origin": "https://untrusted.example"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "不允许的请求来源"


def test_readiness_returns_ok_when_dependencies_are_available() -> None:
    app.dependency_overrides[get_db] = override_db(HealthyDB())
    try:
        with patch("app.routes.health.Redis.from_url") as redis_factory:
            redis_factory.return_value.ping.return_value = True
            resp = client.get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "checks": {"api": "ok", "database": "ok", "redis": "ok"},
    }
    redis_factory.return_value.close.assert_called_once()


def test_readiness_returns_503_when_dependencies_are_unavailable() -> None:
    app.dependency_overrides[get_db] = override_db(FailedDB())
    try:
        with patch("app.routes.health.Redis.from_url") as redis_factory:
            redis_factory.return_value.ping.side_effect = ConnectionError(
                "redis unavailable"
            )
            resp = client.get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 503
    assert resp.json() == {
        "status": "error",
        "checks": {
            "api": "ok",
            "database": "error: ConnectionError",
            "redis": "error: ConnectionError",
        },
    }


def test_legacy_health_route_uses_readiness_contract() -> None:
    app.dependency_overrides[get_db] = override_db(HealthyDB())
    try:
        with patch("app.routes.health.Redis.from_url") as redis_factory:
            redis_factory.return_value.ping.return_value = True
            resp = client.get("/api/v1/health")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
