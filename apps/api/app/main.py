"""FastAPI 应用入口。"""
import logging
import re
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import settings
from app.routes import assets, collections, extractions, health, interviews, profile_projects, profiles, project_followups, questions, recommendations

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

allowed_origins = {"http://localhost:5173", "http://127.0.0.1:5173"}
request_id_pattern = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# Reuse Uvicorn's configured error logger so the minimal records are emitted in
# containers even though Uvicorn's query-bearing default access log is disabled.
access_logger = logging.getLogger("uvicorn.error")
security_headers = {
    "Cache-Control": "no-store",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


@app.middleware("http")
async def secure_and_observe_requests(request: Request, call_next):
    """为本地私有 API 添加来源约束、关联 ID、安全头和最小访问日志。"""
    started_at = time.perf_counter()
    supplied_request_id = request.headers.get("x-request-id", "")
    request_id = (
        supplied_request_id
        if request_id_pattern.fullmatch(supplied_request_id)
        else str(uuid4())
    )
    request.state.request_id = request_id

    origin = request.headers.get("origin")
    if origin and origin not in allowed_origins:
        response = JSONResponse(
            status_code=403,
            content={"detail": "不允许的请求来源"},
        )
    else:
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001
            duration_ms = (time.perf_counter() - started_at) * 1000
            access_logger.exception(
                "request_failed method=%s path=%s duration_ms=%.1f request_id=%s",
                request.method,
                request.url.path,
                duration_ms,
                request_id,
            )
            raise

    response.headers["X-Request-ID"] = request_id
    for name, value in security_headers.items():
        response.headers[name] = value

    duration_ms = (time.perf_counter() - started_at) * 1000
    access_logger.info(
        "request_completed method=%s path=%s status=%d duration_ms=%.1f request_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        request_id,
    )
    return response

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["localhost", "127.0.0.1", "api", "testserver"],
)

# 开发阶段允许前端 Vite dev server 跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(allowed_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api/v1")
app.include_router(collections.router, prefix="/api/v1")
app.include_router(assets.router, prefix="/api/v1")
app.include_router(questions.router, prefix="/api/v1")
app.include_router(extractions.router, prefix="/api/v1")
app.include_router(profiles.router, prefix="/api/v1")
app.include_router(profile_projects.router, prefix="/api/v1")
app.include_router(recommendations.router, prefix="/api/v1")
app.include_router(project_followups.router, prefix="/api/v1")
app.include_router(interviews.router, prefix="/api/v1")


@app.get("/")
def root() -> dict:
    return {"service": settings.app_name, "status": "ok", "docs": "/api/docs"}
