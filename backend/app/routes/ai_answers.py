"""Explicit asynchronous AI supplements and review; source answers are never written."""
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.identity import get_current_user_id
from app.idempotency import remember_request, replay_resource_id, request_hash
from app.models import AIAnswerRun, User
from app.modules.ai_answer_service import InputContext, fingerprint, is_current, now, question, run_view
from app.schemas.ai_answers import AIAnswerCreate, AIAnswerReview, AIAnswerVersion, AIAnswerView
from app.workers.celery_app import celery_app

router = APIRouter(tags=["ai-answers"])


def owned(db, owner, run_id, lock=False):
    statement = select(AIAnswerRun).where(AIAnswerRun.id == run_id, AIAnswerRun.owner_id == owner)
    run = db.scalar(statement.with_for_update() if lock else statement)
    if run is None:
        raise HTTPException(status_code=404, detail="AI补充答案不存在")
    return run


def lock_owner(db, owner):
    db.scalar(select(User).where(User.id == owner).with_for_update())


def check_quota(db, owner, ignore=None):
    # Locking the owner serializes quota checks and submissions across tabs.
    pending = db.scalars(select(AIAnswerRun).where(AIAnswerRun.owner_id == owner,
        AIAnswerRun.status.in_(["QUEUED", "PROCESSING"]))).all()
    if sum(r.id != ignore for r in pending) >= settings.ai_answer_concurrency_limit:
        raise HTTPException(status_code=429, detail="已有AI补充答案正在生成，请完成后再试")
    day = now().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = db.scalars(select(AIAnswerRun).where(AIAnswerRun.owner_id == owner, AIAnswerRun.updated_at >= day)).all()
    if sum(sum(t >= day.isoformat() for t in r.request_times) for r in rows) >= settings.ai_answer_daily_limit:
        raise HTTPException(status_code=429, detail="今天的AI补充答案请求已达上限，重试也计入次数")


def dispatch(db, run):
    try:
        celery_app.send_task("app.workers.tasks.process_ai_answer", args=[str(run.id)])
    except Exception:
        db.refresh(run)
        if run.status == "QUEUED":
            run.status, run.error_code, run.error_message = "FAILED", "AI_ANSWER_QUEUE_UNAVAILABLE", "后台服务暂不可用，可稍后重试"
            run.version += 1
            db.commit()


@router.post("/questions/{question_id}/ai-answer-runs", response_model=AIAnswerView, status_code=202)
def create(question_id: UUID, payload: AIAnswerCreate, db: Session = Depends(get_db), owner: UUID = Depends(get_current_user_id),
           idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=200)):
    lock_owner(db, owner)
    q = question(db, owner, question_id, True)
    if q is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    digest = request_hash({"questionId": str(question_id), **payload.model_dump(mode="json")})
    replay = replay_resource_id(db, owner, "ai-answer:create", idempotency_key, digest)
    if replay:
        return run_view(db, owned(db, owner, replay))
    if q.version != payload.expected_question_version:
        raise HTTPException(status_code=409, detail="题目已变化，请刷新后生成")
    if len(q.prompt) > 6000:
        raise HTTPException(status_code=422, detail="单题内容超过6000字符，请先整理题干")
    duplicate = db.scalar(select(AIAnswerRun).where(AIAnswerRun.question_id == q.id,
        AIAnswerRun.owner_id == owner, AIAnswerRun.status.in_(["QUEUED", "PROCESSING"])))
    if duplicate:
        return run_view(db, duplicate)
    check_quota(db, owner)
    snapshot = InputContext(db, owner, [q]).snapshot(q, payload.use_profile, payload.include_note)
    run = AIAnswerRun(owner_id=owner, question_id=q.id, question_version=q.version,
        used_profile=payload.use_profile, used_note=payload.include_note, input_snapshot=snapshot,
        input_fingerprint=fingerprint(snapshot), request_times=[now().isoformat()])
    db.add(run)
    db.flush()
    remember_request(db, owner, "ai-answer:create", idempotency_key, digest, run.id)
    db.commit()
    dispatch(db, run)
    return run_view(db, run)


@router.get("/questions/{question_id}/ai-answer-runs/latest", response_model=AIAnswerView | None)
def latest(question_id: UUID, db: Session = Depends(get_db), owner: UUID = Depends(get_current_user_id)):
    if question(db, owner, question_id) is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    run = db.scalar(select(AIAnswerRun).where(AIAnswerRun.owner_id == owner, AIAnswerRun.question_id == question_id)
                    .order_by(AIAnswerRun.created_at.desc(), AIAnswerRun.id.desc()).limit(1))
    return run_view(db, run) if run else None


@router.get("/ai-answer-runs/{run_id}", response_model=AIAnswerView)
def get(run_id: UUID, db: Session = Depends(get_db), owner: UUID = Depends(get_current_user_id)):
    return run_view(db, owned(db, owner, run_id))


@router.post("/ai-answer-runs/{run_id}/retry", response_model=AIAnswerView, status_code=202)
def retry(run_id: UUID, payload: AIAnswerVersion, db: Session = Depends(get_db), owner: UUID = Depends(get_current_user_id)):
    lock_owner(db, owner)
    run = owned(db, owner, run_id, True)
    if payload.expected_version != run.version:
        raise HTTPException(status_code=409, detail="生成记录已变化，请刷新重试")
    stale_job = run.status in {"QUEUED", "PROCESSING"} and now() - (run.started_at or run.updated_at) > timedelta(minutes=5)
    if run.review_status == "DISCARDED" or (run.status != "FAILED" and not stale_job):
        raise HTTPException(status_code=409, detail="当前生成记录不能重试")
    if run.retry_count >= settings.ai_answer_retry_limit or not is_current(db, run):
        raise HTTPException(status_code=409, detail="重试次数已用完或依据已变化，请重新生成")
    check_quota(db, owner, ignore=run.id)
    run.retry_count += 1
    run.request_times = [*run.request_times, now().isoformat()]
    run.status, run.job_token, run.started_at = "QUEUED", None, None
    run.error_code = run.error_message = None
    run.version += 1
    db.commit()
    dispatch(db, run)
    return run_view(db, run)


@router.put("/ai-answer-runs/{run_id}", response_model=AIAnswerView)
def review(run_id: UUID, payload: AIAnswerReview, db: Session = Depends(get_db), owner: UUID = Depends(get_current_user_id)):
    lock_owner(db, owner)
    run = owned(db, owner, run_id, True)
    if payload.expected_version != run.version:
        raise HTTPException(status_code=409, detail="答案已变化，请刷新后再操作")
    if payload.action == "DISCARD":
        run.review_status, run.job_token = "DISCARDED", None
        if run.status in {"QUEUED", "PROCESSING"}:
            run.status, run.error_code, run.error_message = "FAILED", "AI_ANSWER_CANCELLED", "已取消本次生成"
    else:
        if run.status != "SUCCEEDED" or run.review_status == "DISCARDED" or not is_current(db, run):
            raise HTTPException(status_code=409, detail="结果尚未生成、已丢弃或依据已变化，不能确认")
        if payload.content is not None:
            run.content = payload.content
        run.review_status = "CONFIRMED" if payload.action == "CONFIRM" else "DRAFT"
    run.version += 1
    db.commit()
    return run_view(db, run)
