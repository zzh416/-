"""Celery 应用与队列配置。"""
from celery import Celery

from app.config import settings

celery_app = Celery(
    "bagu",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_time_limit=300,
    worker_prefetch_multiplier=1,
)
