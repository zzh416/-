"""Versioned personal interviews; timer and unknown-answer transitions are local."""
from copy import deepcopy
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.identity import get_current_user_id
from app.idempotency import remember_request, replay_resource_id, request_hash
from app.models import InterviewSession, RecommendationItem, User
from app.modules import interview_engine as engine
from app.modules.interview_service import _fail_state, current, make_plan, now, save
from app.routes.recommendations import _owned_run, _run_view
from app.schemas.interviews import InterviewAction, InterviewAnswer, InterviewCreate, InterviewDraft, InterviewHistory, InterviewView
from app.workers.celery_app import celery_app

router = APIRouter(prefix="/interview-sessions", tags=["interviews"])


def _owned(db, session_id, owner_id, lock=False):
    query = select(InterviewSession).where(InterviewSession.id == session_id, InterviewSession.owner_id == owner_id)
    row = db.scalar(query.with_for_update() if lock else query)
    if not row:
        raise HTTPException(status_code=404, detail="模拟面试记录不存在")
    return row


def _view(db, row):
    state = row.state
    reveal = state["mode"] == "PREVIEW" or row.status == "COMPLETED"
    plan = [{key: value for key, value in item.items() if key != "reference_answer"} for item in state["plan"]]
    if not reveal:
        plan = [{"id": item["id"], "kind": item["kind"], "topics": item["topics"]} for item in plan]
    retry = row.job_started_at and (now() - row.job_started_at).total_seconds() > 600
    return InterviewView(id=row.id, version=row.version, status=row.status, server_now=now(), is_current=current(db, row),
        mode=state["mode"], question_count=state["question_count"], question_seconds=state["question_seconds"], plan=plan,
        turns=state["turns"], blind_topics=state["blind_topics"], skipped_plan_count=len(state["skipped_plan_ids"]), notice=state.get("notice", ""),
        finished_reason=state.get("finished_reason"), planning_error=state.get("planning_error"), model=state.get("model"), job_stage=state["job_stage"],
        can_retry=bool(retry or row.status == "PLAN_FAILED" or (row.status == "AWAITING_NEXT" and state["turns"] and state["turns"][-1].get("review_error"))), created_at=row.created_at)


def _dispatch(db, row):
    row.job_started_at = now()
    db.commit()
    try:
        celery_app.send_task("app.workers.tasks.process_interview", args=[str(row.id)])
    except Exception:
        db.refresh(row)
        if row.status in {"PLANNING", "REVIEWING"} and row.job_token is None:
            state = deepcopy(row.state)
            _fail_state(state, row.status, "后台服务不可用，数据已保存，可稍后重试或继续题库练习")
            save(db, row, state)


def _check(row, payload):
    if row.version != payload.expected_version:
        raise HTTPException(status_code=409, detail="面试记录已更新，请刷新后重试；当前文字请先保留")
    if payload.turn_id and (not row.state["turns"] or str(payload.turn_id) != row.state["turns"][-1]["id"]):
        raise HTTPException(status_code=409, detail="当前题目已变化，请刷新后重试")


@router.post("", response_model=InterviewView, status_code=202)
def create_interview(payload: InterviewCreate, db: Session = Depends(get_db), owner_id: UUID = Depends(get_current_user_id),
                     idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=200)):
    db.scalar(select(User).where(User.id == owner_id).with_for_update())
    digest = request_hash(payload)
    replay = replay_resource_id(db, owner_id, "interview:create", idempotency_key, digest)
    if replay:
        return _view(db, _owned(db, replay, owner_id))
    run = _owned_run(db, payload.recommendation_run_id, owner_id)
    if not _run_view(db, run, owner_id).is_current:
        raise HTTPException(status_code=409, detail="请先按当前画像和偏好重新生成复习清单")
    if db.scalar(select(InterviewSession.id).where(InterviewSession.owner_id == owner_id, InterviewSession.status != "COMPLETED")):
        raise HTTPException(status_code=409, detail="已有未结束的面试，请先继续或结束上一轮")
    confirmed = [item for item in run.profile_snapshot.get("projects", []) if item["review_status"] == "CONFIRMED"]
    if payload.project_id and not any(item["id"] == str(payload.project_id) for item in confirmed):
        raise HTTPException(status_code=422, detail="请选择当前画像中已确认的项目")
    questions = db.scalars(select(RecommendationItem).where(RecommendationItem.run_id == run.id, RecommendationItem.decision == "INCLUDED").order_by(RecommendationItem.position)).all()
    pool, length = [], 0
    for item in questions:
        if payload.project_id and not any(match["project_id"] == str(payload.project_id) for match in item.project_matches or []):
            continue
        if len(pool) >= 80 or length + len(item.prompt) > 40_000:
            continue
        project_id = str(payload.project_id) if payload.project_id else ((item.project_matches or [{}])[0].get("project_id"))
        pool.append({"id": str(item.id), "prompt": item.prompt, "source_statistics": item.source_statistics, "project_id": project_id,
                     "reference_answer": item.answer_content[:3000] if item.usable_answer_state == "ANSWERED" and item.answer_content else None})
        length += len(item.prompt)
    if not pool and not confirmed:
        raise HTTPException(status_code=409, detail="请先补充可用题目或已确认项目")
    state = {"status": "PLANNING", "mode": payload.mode, "project_id": str(payload.project_id) if payload.project_id else None,
             "question_count": payload.question_count, "question_seconds": payload.question_seconds, "candidates": pool,
             "existing_prompts": [item.prompt for item in questions], "profile_snapshot": run.profile_snapshot, "rule_snapshot": run.rule_snapshot,
             "plan": [], "turns": [], "cursor": 0, "blind_topics": [], "skipped_plan_ids": [], "job_stage": "QUEUED", "plan_attempts": 0}
    row = InterviewSession(owner_id=owner_id, recommendation_run_id=run.id, status="PLANNING", state=state)
    db.add(row); db.flush()
    remember_request(db, owner_id, "interview:create", idempotency_key, digest, row.id)
    db.commit(); _dispatch(db, row); db.refresh(row)
    return _view(db, row)


@router.get("", response_model=InterviewHistory)
def history(db: Session = Depends(get_db), owner_id: UUID = Depends(get_current_user_id)):
    rows = db.scalars(select(InterviewSession).where(InterviewSession.owner_id == owner_id).order_by(InterviewSession.created_at.desc(), InterviewSession.id.desc()).limit(10)).all()
    return InterviewHistory(items=rows)


@router.get("/{session_id}", response_model=InterviewView)
def get_interview(session_id: UUID, db: Session = Depends(get_db), owner_id: UUID = Depends(get_current_user_id)):
    return _view(db, _owned(db, session_id, owner_id))


@router.put("/{session_id}/draft", response_model=InterviewView)
def draft(session_id: UUID, payload: InterviewDraft, db: Session = Depends(get_db), owner_id: UUID = Depends(get_current_user_id)):
    row = _owned(db, session_id, owner_id, True); _check(row, payload)
    if row.status not in {"ACTIVE", "PAUSED"} or not payload.turn_id:
        raise HTTPException(status_code=409, detail="当前不能保存作答草稿")
    state = deepcopy(row.state); state["turns"][-1]["draft"] = payload.text
    save(db, row, state)
    return _view(db, row)


@router.post("/{session_id}/answer", response_model=InterviewView)
def answer(session_id: UUID, payload: InterviewAnswer, db: Session = Depends(get_db), owner_id: UUID = Depends(get_current_user_id)):
    row = _owned(db, session_id, owner_id, True); _check(row, payload)
    if not payload.turn_id or not current(db, row):
        raise HTTPException(status_code=409, detail="题目依据已变化，请保存草稿后重新建立面试")
    state = deepcopy(row.state)
    try:
        engine.submit(state, payload.text, payload.action, now())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    row.job_token = None; row.job_started_at = None
    save(db, row, state)
    if row.status == "REVIEWING":
        _dispatch(db, row); db.refresh(row)
    return _view(db, row)


@router.post("/{session_id}/action", response_model=InterviewView)
def act(session_id: UUID, payload: InterviewAction, db: Session = Depends(get_db), owner_id: UUID = Depends(get_current_user_id)):
    row = _owned(db, session_id, owner_id, True); _check(row, payload)
    state = deepcopy(row.state)
    if payload.action not in {"FINISH", "PAUSE", "RESUME", "EXTEND"} and not current(db, row):
        raise HTTPException(status_code=409, detail="画像、偏好或题库已变化，请结束后重新建立面试")
    queue = False
    try:
        if payload.action == "USE_BANK" and row.status in {"PLANNING", "PLAN_FAILED"}:
            state["plan"] = make_plan(state)
            if not state["plan"]:
                raise ValueError("当前没有可用题库题目，请先补充资料")
            state["status"], state["job_stage"], state["planning_error"] = "READY", "IDLE", None
            state["notice"] = "本轮明确改用已有题库计划，未使用AI出题结果。"
        elif payload.action == "RETRY_PLAN" and row.status in {"PLAN_FAILED", "PLANNING"}:
            if row.status == "PLANNING" and (not row.job_started_at or (now() - row.job_started_at).total_seconds() <= 600):
                raise ValueError("出题任务尚未超时")
            if state["plan_attempts"] >= 3:
                raise ValueError("出题重试已达3次上限")
            state["plan_attempts"] += 1
            state["status"], state["job_stage"], state["planning_error"], queue = "PLANNING", "QUEUED", None, True
        elif payload.action == "RETRY_REVIEW" and row.status in {"AWAITING_NEXT", "REVIEWING"}:
            turn = state["turns"][-1]
            expired = row.job_started_at and (now() - row.job_started_at).total_seconds() > 600
            if turn["review_attempts"] >= 3 or (not turn.get("review_error") and not expired):
                raise ValueError("当前不能重试反馈，或已达3次上限")
            turn["review_attempts"] += 1
            turn["review_error"], turn["review_status"] = None, "QUEUED"
            state["status"], state["job_stage"], queue = "REVIEWING", "QUEUED", True
        else:
            if payload.action == "NEXT" and row.status == "REVIEWING":
                state["status"] = "AWAITING_NEXT"
                state["turns"][-1]["review_error"] = "用户选择不等待本题反馈"
            engine.command(state, payload.action, now(), payload.seconds)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    row.job_token = None; row.job_started_at = None
    save(db, row, state)
    if queue:
        _dispatch(db, row); db.refresh(row)
    return _view(db, row)
