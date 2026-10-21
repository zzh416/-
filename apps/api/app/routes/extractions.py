"""资料题答抽取任务接口。"""
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.identity import get_current_user_id
from app.idempotency import remember_request, replay_resource_id, request_hash
from app.models import Asset, AssetVersion, QuestionExtractionRun
from app.schemas.extractions import QuestionExtractionRunView
from app.workers.celery_app import celery_app

router = APIRouter(tags=["question-extractions"])


def _owned_run(db: Session, run_id: UUID, owner_id: UUID) -> QuestionExtractionRun:
    run = db.scalar(
        select(QuestionExtractionRun).where(
            QuestionExtractionRun.id == run_id,
            QuestionExtractionRun.owner_id == owner_id,
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="题目抽取任务不存在")
    return run


def _dispatch(db: Session, run: QuestionExtractionRun) -> None:
    try:
        result = celery_app.send_task(
            "app.workers.tasks.process_question_extraction",
            args=[str(run.id)],
        )
        task_id = getattr(result, "id", None)
        if isinstance(task_id, str):
            run.celery_task_id = task_id
            db.commit()
    except Exception:
        run.status = "FAILED"
        run.stage = "QUEUE"
        run.error_code = "QUEUE_UNAVAILABLE"
        run.error_message = "后台抽取服务暂时不可用，请稍后重试"
        db.commit()


@router.post(
    "/assets/{asset_id}/question-extraction-runs",
    response_model=QuestionExtractionRunView,
    status_code=status.HTTP_201_CREATED,
)
def create_question_extraction_run(
    asset_id: UUID,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
    idempotency_key: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=200
    ),
) -> QuestionExtractionRun:
    asset = db.scalar(
        select(Asset).where(Asset.id == asset_id, Asset.owner_id == owner_id).with_for_update()
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    if asset.status not in {"READY", "PARTIAL_SUCCESS"} or asset.current_version < 1:
        raise HTTPException(status_code=409, detail="资料尚未解析完成，不能抽取题目")
    version = db.scalar(
        select(AssetVersion).where(
            AssetVersion.asset_id == asset.id,
            AssetVersion.version == asset.current_version,
        )
    )
    if version is None:
        raise HTTPException(status_code=409, detail="资料当前版本不存在")

    digest = request_hash(
        {"assetId": str(asset.id), "assetVersionId": str(version.id)}
    )
    replay_id = replay_resource_id(
        db, owner_id, "question-extraction:create", idempotency_key, digest
    )
    if replay_id is not None:
        return _owned_run(db, replay_id, owner_id)

    existing_run = db.scalar(
        select(QuestionExtractionRun)
        .where(
            QuestionExtractionRun.owner_id == owner_id,
            QuestionExtractionRun.asset_version_id == version.id,
            QuestionExtractionRun.status.in_(
                {"QUEUED", "PROCESSING", "SUCCEEDED", "PARTIAL_SUCCESS"}
            ),
        )
        .order_by(QuestionExtractionRun.created_at.desc())
    )
    if existing_run is not None:
        remember_request(
            db,
            owner_id,
            "question-extraction:create",
            idempotency_key,
            digest,
            existing_run.id,
        )
        db.commit()
        return existing_run

    run = QuestionExtractionRun(
        owner_id=owner_id,
        asset_id=asset.id,
        asset_version_id=version.id,
        model=None,
    )
    db.add(run)
    try:
        db.flush()
        remember_request(
            db,
            owner_id,
            "question-extraction:create",
            idempotency_key,
            digest,
            run.id,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        replay_id = replay_resource_id(
            db, owner_id, "question-extraction:create", idempotency_key, digest
        )
        if replay_id is not None:
            return _owned_run(db, replay_id, owner_id)
        raise HTTPException(status_code=409, detail="重复的题目抽取请求") from exc
    _dispatch(db, run)
    db.refresh(run)
    return run


@router.get(
    "/question-extraction-runs/{run_id}",
    response_model=QuestionExtractionRunView,
)
def get_question_extraction_run(
    run_id: UUID,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> QuestionExtractionRun:
    return _owned_run(db, run_id, owner_id)
