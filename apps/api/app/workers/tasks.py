"""后台任务占位示例。

后续的解析（OCR / 文档解析）、题目抽取、向量化、答案整理等
耗时任务都将以 Celery 任务的形式挂在这里。
"""
from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.echo")
def echo(message: str) -> str:
    """验证 Worker 与 Redis 队列连通的占位任务。"""
    return f"echo: {message}"
