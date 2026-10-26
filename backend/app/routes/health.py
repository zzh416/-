"""健康检查接口。"""
from fastapi import APIRouter, Depends, Response, status
from redis import Redis
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db

router = APIRouter(tags=["health"])


@router.get("/health/live")
def liveness() -> dict:
    """仅表示 API 进程能够响应。"""
    return {"status": "ok", "checks": {"api": "ok"}}


def _readiness(db: Session) -> tuple[dict, bool]:
    """检查请求所必需的数据库与 Redis。"""
    checks: dict[str, str] = {"api": "ok"}

    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {exc.__class__.__name__}"

    redis_client = Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    try:
        redis_client.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {exc.__class__.__name__}"
    finally:
        redis_client.close()

    is_ready = checks["database"] == "ok" and checks["redis"] == "ok"
    return {"status": "ok" if is_ready else "error", "checks": checks}, is_ready


@router.get("/health/ready")
@router.get("/health")
def readiness(response: Response, db: Session = Depends(get_db)) -> dict:
    """依赖不可用时返回 503，供界面和编排系统判断就绪状态。"""
    payload, is_ready = _readiness(db)
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return payload
