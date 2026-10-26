"""Owner-scoped, opt-in follow-up jobs over current saved review lists."""
from datetime import datetime, timezone
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.identity import get_current_user_id
from app.idempotency import remember_request, replay_resource_id, request_hash
from app.models import ProjectFollowupRun, RecommendationItem, RecommendationRun
from app.modules.followup_export import render_followups
from app.modules.project_followup_service import inputs_current, retry_allowed
from app.routes.recommendations import _owned_run, _run_view
from app.schemas.project_followups import FollowupCreate, FollowupRunView, FollowupHistoryPage
from app.workers.celery_app import celery_app

router = APIRouter(tags=["project-followups"])


def _owned(db, run_id, owner_id, *, lock=False):
    statement = select(ProjectFollowupRun).where(ProjectFollowupRun.id == run_id, ProjectFollowupRun.owner_id == owner_id)
    run = db.scalar(statement.with_for_update() if lock else statement)
    if run is None:
        raise HTTPException(status_code=404, detail="项目追问任务不存在")
    return run


def _view(db, run):
    current = inputs_current(db, run)
    return FollowupRunView(**{field: getattr(run, field) for field in FollowupRunView.model_fields if field not in {"is_current", "can_retry"}},
                           is_current=current, can_retry=current and retry_allowed(run))


def _dispatch(db, run):
    try:
        celery_app.send_task("app.workers.tasks.process_project_followup", args=[str(run.id)])
    except Exception:
        run.status, run.error_code, run.error_message = "FAILED", "QUEUE_UNAVAILABLE", "后台追问服务不可用，请稍后重试"
        db.commit()


def _lock_recommendation(db, run_id, owner_id):
    run = db.scalar(select(RecommendationRun).where(RecommendationRun.id == run_id, RecommendationRun.owner_id == owner_id).with_for_update())
    if run is None:
        raise HTTPException(status_code=404, detail="个性化清单不存在")
    return run


@router.post("/recommendation-runs/{run_id}/project-followups", response_model=FollowupRunView, status_code=202)
def create_followup(run_id: UUID, payload: FollowupCreate, db: Session = Depends(get_db), owner_id: UUID = Depends(get_current_user_id),
                    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=200)):
    recommendation = _lock_recommendation(db, run_id, owner_id)
    digest = request_hash({"runId": str(run_id), **payload.model_dump(mode="json")})
    replay = replay_resource_id(db, owner_id, "project-followup:create", idempotency_key, digest)
    if replay:
        return _view(db, _owned(db, replay, owner_id))
    if not _run_view(db, recommendation, owner_id).is_current:
        raise HTTPException(status_code=409, detail="请先按最新画像和偏好重新生成复习清单")
    project = next((item for item in recommendation.profile_snapshot.get("projects", []) if item["id"] == str(payload.project_id) and item["review_status"] == "CONFIRMED"), None)
    if project is None:
        raise HTTPException(status_code=404, detail="清单中没有这个已确认项目")
    selected = db.scalars(select(RecommendationItem).where(RecommendationItem.run_id == run_id, RecommendationItem.id.in_(payload.seed_item_ids)).order_by(RecommendationItem.position)).all()
    if len(selected) != len(payload.seed_item_ids) or any(item.decision != "INCLUDED" or not any(match["project_id"] == str(payload.project_id) for match in item.project_matches or []) for item in selected):
        raise HTTPException(status_code=422, detail="只能选择这份清单中已关联该项目、且进入清单的题目")
    if len(project["description"]) + sum(len(item.prompt) for item in selected) > 20_000:
        raise HTTPException(status_code=422, detail="项目和种子题文字合计不能超过 20000 字符")
    if db.scalar(select(ProjectFollowupRun.id).where(ProjectFollowupRun.recommendation_run_id == run_id, ProjectFollowupRun.status.in_(["QUEUED", "PROCESSING"]))):
        raise HTTPException(status_code=409, detail="这份清单已有追问任务正在运行，请等待或处理超时任务")
    run = ProjectFollowupRun(owner_id=owner_id, recommendation_run_id=run_id, project_id=payload.project_id, project_snapshot=project,
                             seeds=[{"id": str(item.id), "question_id": str(item.question_id) if item.question_id else None,
                                     "question_version": item.question_version, "prompt": item.prompt, "source_statistics": item.source_statistics} for item in selected])
    db.add(run)
    db.flush()
    remember_request(db, owner_id, "project-followup:create", idempotency_key, digest, run.id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="创建请求冲突，请刷新后重试")
    _dispatch(db, run)
    db.refresh(run)
    return _view(db, run)


@router.get("/recommendation-runs/{run_id}/project-followups/latest", response_model=FollowupRunView | None)
def latest_followup(run_id: UUID, project_id: UUID = Query(alias="projectId"), db: Session = Depends(get_db), owner_id: UUID = Depends(get_current_user_id)):
    _owned_run(db, run_id, owner_id)
    run = db.scalar(select(ProjectFollowupRun).where(ProjectFollowupRun.owner_id == owner_id, ProjectFollowupRun.recommendation_run_id == run_id, ProjectFollowupRun.project_id == project_id).order_by(ProjectFollowupRun.created_at.desc(), ProjectFollowupRun.id.desc()).limit(1))
    return _view(db, run) if run else None


@router.get("/project-followup-runs/{run_id}", response_model=FollowupRunView)
def get_followup(run_id: UUID, db: Session = Depends(get_db), owner_id: UUID = Depends(get_current_user_id)):
    return _view(db, _owned(db, run_id, owner_id))


@router.get("/project-followup-runs", response_model=FollowupHistoryPage)
def followup_history(project_id: UUID = Query(alias="projectId"), db: Session = Depends(get_db), owner_id: UUID = Depends(get_current_user_id)):
    runs = db.scalars(select(ProjectFollowupRun).where(ProjectFollowupRun.owner_id == owner_id, ProjectFollowupRun.project_id == project_id)
                      .order_by(ProjectFollowupRun.created_at.desc(), ProjectFollowupRun.id.desc()).limit(10)).all()
    return FollowupHistoryPage(items=runs)


@router.post("/project-followup-runs/{run_id}/retry", response_model=FollowupRunView, status_code=202)
def retry_followup(run_id: UUID, db: Session = Depends(get_db), owner_id: UUID = Depends(get_current_user_id),
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=200)):
    run = _owned(db, run_id, owner_id, lock=True)
    _lock_recommendation(db, run.recommendation_run_id, owner_id)
    digest = request_hash({"runId": str(run_id)})
    if replay_resource_id(db, owner_id, "project-followup:retry", idempotency_key, digest):
        return _view(db, run)
    if not inputs_current(db, run) or not retry_allowed(run):
        raise HTTPException(status_code=409, detail="当前状态不能重试，或已达3次上限；依据变化时请重新生成")
    if db.scalar(select(ProjectFollowupRun.id).where(ProjectFollowupRun.recommendation_run_id == run.recommendation_run_id,
                     ProjectFollowupRun.id != run.id, ProjectFollowupRun.status.in_(["QUEUED", "PROCESSING"]))):
        raise HTTPException(status_code=409, detail="另一个追问任务正在运行")
    run.retry_count += 1
    run.status, run.error_code, run.error_message = "QUEUED", None, None
    run.items, run.rejection_counts = [], {}
    run.started_at, run.finished_at, run.updated_at = None, None, datetime.now(timezone.utc)
    remember_request(db, owner_id, "project-followup:retry", idempotency_key, digest, run.id)
    db.commit()
    _dispatch(db, run)
    db.refresh(run)
    return _view(db, run)


@router.get("/project-followup-runs/{run_id}/export", response_class=Response)
def export_followup(run_id: UUID, db: Session = Depends(get_db), owner_id: UUID = Depends(get_current_user_id)):
    run = _owned(db, run_id, owner_id)
    if run.status not in {"SUCCEEDED", "PARTIAL_SUCCESS"} or not run.items:
        raise HTTPException(status_code=409, detail="还没有可导出的追问结果")
    return Response(render_followups(run, is_current=inputs_current(db, run)), media_type="text/markdown", headers={
        "Cache-Control": "no-store", "Content-Disposition": f"attachment; filename=followups-{run.id}.md; filename*=UTF-8''{quote('项目追问链-' + str(run.id)[:8] + '.md')}",
    })
