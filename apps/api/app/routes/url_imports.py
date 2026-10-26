"""有界批量 URL 导入，保存逐条结果并复用现有解析任务。"""
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.identity import get_current_user_id
from app.idempotency import remember_request, replay_resource_id, request_hash
from app.models import Asset, Collection, IngestionJob, UrlImportBatch, UrlImportItem
from app.modules.ingestion_validation import normalize_public_url
from app.routes.assets import _dispatch_ingestion, _owned_collection
from app.schemas.url_imports import UrlImportBatchView, UrlImportCreate, UrlImportPage

router = APIRouter(tags=["url-imports"])
_SCOPE = "url-import:create"


def _batch(db: Session, batch_id: UUID, owner_id: UUID) -> UrlImportBatch:
    batch = db.scalar(select(UrlImportBatch).where(
        UrlImportBatch.id == batch_id, UrlImportBatch.owner_id == owner_id,
    ).options(selectinload(UrlImportBatch.items).selectinload(UrlImportItem.asset).selectinload(Asset.job))
      .execution_options(populate_existing=True))
    if batch is None:
        raise HTTPException(status_code=404, detail="导入批次不存在")
    return batch


def _dispatch_pending(db: Session, batch: UrlImportBatch) -> None:
    # Replays recover a process that exited after commit but before publishing.
    # Row locks serialize recovery attempts; acknowledged jobs are never resent.
    asset_ids = dict.fromkeys(item.asset_id for item in batch.items if item.asset_id)
    for asset_id in asset_ids:
        job = db.scalar(select(IngestionJob).where(
            IngestionJob.asset_id == asset_id, IngestionJob.owner_id == batch.owner_id,
        ).with_for_update().execution_options(populate_existing=True))
        if job is not None and job.status == "QUEUED" and job.celery_task_id is None:
            _dispatch_ingestion(db, job.asset, job)
        else:
            db.commit()


@router.post("/collections/{collection_id}/url-imports", response_model=UrlImportBatchView, status_code=201)
def create_url_import(
    collection_id: UUID,
    payload: UrlImportCreate,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=200),
) -> UrlImportBatch:
    # Serialize batches in this collection, including reuse of queued resources.
    owned = db.scalar(select(Collection).where(
        Collection.id == collection_id, Collection.owner_id == owner_id,
    ).with_for_update())
    if owned is None:
        raise HTTPException(status_code=404, detail="资料库不存在")
    digest = request_hash({"collectionId": str(collection_id), "urls": payload.urls})
    replay_id = replay_resource_id(db, owner_id, _SCOPE, idempotency_key, digest)
    if replay_id is not None:
        batch = _batch(db, replay_id, owner_id)
        db.commit()
        _dispatch_pending(db, batch)
        return _batch(db, batch.id, owner_id)

    batch = UrlImportBatch(owner_id=owner_id, collection_id=collection_id)
    existing = db.scalars(select(Asset).where(
        Asset.owner_id == owner_id, Asset.collection_id == collection_id,
        Asset.kind == "LIBRARY", Asset.source_format == "WEB_PAGE",
    ).order_by(Asset.created_at, Asset.id)).all()
    by_url: dict[str, Asset] = {}
    for asset in existing:
        if asset.source_url:
            try:
                by_url.setdefault(normalize_public_url(asset.source_url), asset)
            except ValueError:
                continue
    first_rows: dict[str, int] = {}
    for ordinal, raw in enumerate(payload.urls, 1):
        item = UrlImportItem(ordinal=ordinal, input_url=raw.replace("\x00", "\\u0000"), disposition="INVALID")
        batch.items.append(item)
        try:
            if any(ord(character) < 32 or ord(character) == 127 for character in raw):
                raise ValueError("网页地址不能包含控制字符；不可显示字符已转义")
            if not raw.strip() or len(raw.strip()) > 2048:
                raise ValueError("网页地址不能为空且不能超过 2048 个字符")
            normalized = normalize_public_url(raw)
        except ValueError as exc:
            item.message = str(exc)[:500]
            continue
        item.normalized_url = normalized
        if normalized in first_rows:
            item.disposition = "DUPLICATE"
            item.duplicate_of = first_rows[normalized]
            item.asset = by_url[normalized]
            continue
        first_rows[normalized] = ordinal
        asset = by_url.get(normalized)
        if asset is not None:
            item.disposition = "REUSED"
        else:
            asset = Asset(owner_id=owner_id, collection_id=collection_id, kind="LIBRARY",
                          title=(urlsplit(normalized).hostname or "网页")[:200], source_format="WEB_PAGE",
                          source_url=normalized, content_type="text/html", status="QUEUED", current_version=0)
            asset.job = IngestionJob(owner_id=owner_id)
            by_url[normalized] = asset
            item.disposition = "CREATED"
        item.asset = asset
    db.add(batch)
    try:
        db.flush()
        remember_request(db, owner_id, _SCOPE, idempotency_key, digest, batch.id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        replay_id = replay_resource_id(db, owner_id, _SCOPE, idempotency_key, digest)
        if replay_id is None:
            raise HTTPException(status_code=409, detail="导入请求冲突，请重试") from exc
        batch = _batch(db, replay_id, owner_id)
    _dispatch_pending(db, batch)
    return _batch(db, batch.id, owner_id)


@router.get("/collections/{collection_id}/url-imports", response_model=UrlImportPage)
def list_url_imports(collection_id: UUID, db: Session = Depends(get_db), owner_id: UUID = Depends(get_current_user_id)):
    _owned_collection(db, collection_id, owner_id)
    batches = db.scalars(select(UrlImportBatch).where(
        UrlImportBatch.collection_id == collection_id, UrlImportBatch.owner_id == owner_id,
    ).order_by(UrlImportBatch.created_at.desc(), UrlImportBatch.id.desc()).limit(10)
      .options(selectinload(UrlImportBatch.items).selectinload(UrlImportItem.asset).selectinload(Asset.job))).all()
    return UrlImportPage(items=[UrlImportBatchView.model_validate(batch) for batch in batches])


@router.get("/url-imports/{batch_id}", response_model=UrlImportBatchView)
def get_url_import(batch_id: UUID, db: Session = Depends(get_db), owner_id: UUID = Depends(get_current_user_id)):
    return _batch(db, batch_id, owner_id)


@router.post("/url-imports/{batch_id}/dispatch", response_model=UrlImportBatchView)
def dispatch_url_import(batch_id: UUID, db: Session = Depends(get_db), owner_id: UUID = Depends(get_current_user_id)):
    batch = _batch(db, batch_id, owner_id)
    _dispatch_pending(db, batch)
    return _batch(db, batch.id, owner_id)
