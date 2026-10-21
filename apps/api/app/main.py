"""FastAPI 应用入口。"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import settings
from app.routes import assets, collections, extractions, health, questions

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

allowed_origins = {"http://localhost:5173", "http://127.0.0.1:5173"}


@app.middleware("http")
async def reject_unknown_browser_origins(request: Request, call_next):
    """本地模式拒绝来自其他网页 Origin 的请求。"""
    origin = request.headers.get("origin")
    if origin and origin not in allowed_origins:
        return JSONResponse(status_code=403, content={"detail": "不允许的请求来源"})
    return await call_next(request)

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


@app.get("/")
def root() -> dict:
    return {"service": settings.app_name, "status": "ok", "docs": "/api/docs"}
