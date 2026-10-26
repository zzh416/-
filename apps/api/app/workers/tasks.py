"""后台任务占位示例。

后续的解析（OCR / 文档解析）、题目抽取、向量化、答案整理等
耗时任务都将以 Celery 任务的形式挂在这里。
"""
from app.workers.celery_app import celery_app
from app.modules.ingestion_service import process_ingestion_job
from app.modules.question_extraction_service import process_question_extraction_run
from app.modules.profile_service import process_profile_extraction as run_profile_extraction
from uuid import UUID
from app.modules.project_followup_service import process_project_followup as run_project_followup
from app.modules.interview_service import process_interview as run_interview
from app.modules.ai_answer_service import process_ai_answer as run_ai_answer


@celery_app.task(name="app.workers.tasks.echo")
def echo(message: str) -> str:
    """验证 Worker 与 Redis 队列连通的占位任务。"""
    return f"echo: {message}"


@celery_app.task(name="app.workers.tasks.process_ingestion")
def process_ingestion(job_id: str) -> None:
    """只传任务 ID，原件和状态均从受控存储与数据库读取。"""
    process_ingestion_job(UUID(job_id))


@celery_app.task(name="app.workers.tasks.process_question_extraction")
def process_question_extraction(run_id: str) -> None:
    """从固定资料版本中抽取题目和原文答案。"""
    process_question_extraction_run(UUID(run_id))


@celery_app.task(name="app.workers.tasks.process_profile_extraction")
def process_profile_extraction(profile_id: str, expected_profile_version: int) -> None:
    """Extract an evidence-backed skill profile from the current resume version."""
    run_profile_extraction(UUID(profile_id), expected_profile_version)


@celery_app.task(name="app.workers.tasks.process_project_followup")
def process_project_followup(run_id: str) -> None:
    run_project_followup(UUID(run_id))


@celery_app.task(name="app.workers.tasks.process_interview")
def process_interview(session_id: str) -> None:
    run_interview(UUID(session_id))


@celery_app.task(name="app.workers.tasks.process_ai_answer")
def process_ai_answer(run_id: str) -> None:
    run_ai_answer(UUID(run_id))
