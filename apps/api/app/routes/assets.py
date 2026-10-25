"""资料导入、查看与修订。"""
import hashlib
from pathlib import PurePath
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.db import get_db
from app.identity import get_current_user_id
from app.idempotency import remember_request, replay_resource_id, request_hash
from app.models import Asset, AssetVersion, Collection, IngestionJob, Question, TextChunk
from app.modules.content_versions import build_chunks, create_asset_version
from app.modules.ingestion_validation import classify_upload, normalize_public_url
from app.modules.storage import delete_original, sanitize_filename, save_original
from app.schemas.assets import (
    AssetContentUpdate,
    AssetSourceMetadataUpdate,
    AssetDetail,
    AssetPage,
    AssetSummary,
    AssetTextCreate,
    AssetUrlCreate,
    AssetVersionView,
    TextChunkView,
)
from app.workers.celery_app import celery_app

router = APIRouter(tags=["assets"])


@router.put("/assets/{asset_id}/source-metadata", response_model=AssetSummary)
def update_source_metadata(asset_id: UUID, payload: AssetSourceMetadataUpdate, db: Session = Depends(get_db), owner_id: UUID = Depends(get_current_user_id)) -> Asset:
    asset = db.scalar(select(Asset).where(Asset.id == asset_id, Asset.owner_id == owner_id, Asset.kind == "LIBRARY").with_for_update())
    if asset is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    if asset.source_metadata_version != payload.expected_version:
        raise HTTPException(status_code=409, detail="来源信息已变化，请刷新后重试")
    asset.source_date = payload.source_date
    asset.source_metadata_version += 1
    db.commit()
    db.refresh(asset)
    return asset


def _owned_collection(db: Session, collection_id: UUID, owner_id: UUID) -> Collection:
    collection = db.scalar(
        select(Collection).where(Collection.id == collection_id, Collection.owner_id == owner_id)
    )
    if collection is None:
        raise HTTPException(status_code=404, detail="资料库不存在")
    return collection


def _create_asset(
    db: Session,
    owner_id: UUID,
    collection_id: UUID | None,
    *,
    title: str,
    content: str,
    source_format: str,
    original_filename: str | None = None,
    idempotency_key: str | None = None,
    asset_kind: str = "LIBRARY",
) -> Asset:
    if collection_id is not None:
        _owned_collection(db, collection_id, owner_id)
    if len(content.encode("utf-8")) > settings.max_text_import_bytes:
        raise HTTPException(status_code=413, detail="文字资料不能超过 1 MiB")

    digest = request_hash(
        {
            "collectionId": str(collection_id),
            "title": title.strip(),
            "content": content,
            "sourceFormat": source_format,
            "originalFilename": original_filename,
            "assetKind": asset_kind,
        }
    )
    replay_id = replay_resource_id(db, owner_id, "asset:create", idempotency_key, digest)
    if replay_id is not None:
        existing = db.scalar(select(Asset).where(Asset.id == replay_id, Asset.owner_id == owner_id))
        if existing is not None:
            return existing
        raise HTTPException(status_code=409, detail="该幂等请求对应的资源已删除")

    asset = Asset(
        owner_id=owner_id,
        collection_id=collection_id,
        kind=asset_kind,
        title=title.strip(),
        source_format=source_format,
        original_filename=original_filename,
        content_type="text/markdown" if "MARKDOWN" in source_format else "text/plain",
        byte_size=len(content.encode("utf-8")),
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )
    version = AssetVersion(asset=asset, version=1, content=content)
    build_chunks(content, version)
    db.add(asset)
    try:
        db.flush()
        remember_request(db, owner_id, "asset:create", idempotency_key, digest, asset.id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        replay_id = replay_resource_id(db, owner_id, "asset:create", idempotency_key, digest)
        if replay_id is not None:
            existing = db.scalar(
                select(Asset).where(Asset.id == replay_id, Asset.owner_id == owner_id)
            )
            if existing is not None:
                return existing
        raise HTTPException(status_code=409, detail="重复的资料创建请求") from exc
    db.refresh(asset)
    return asset


def _dispatch_ingestion(db: Session, asset: Asset, job: IngestionJob) -> None:
    try:
        result = celery_app.send_task(
            "app.workers.tasks.process_ingestion",
            args=[str(job.id)],
        )
        task_id = getattr(result, "id", None)
        if isinstance(task_id, str):
            job.celery_task_id = task_id
            db.commit()
    except Exception:
        job.status = "FAILED"
        job.error_code = "QUEUE_UNAVAILABLE"
        job.error_message = "后台解析服务暂时不可用，请稍后重试"
        asset.status = "FAILED"
        db.commit()


def _create_ingestion_asset(
    db: Session,
    owner_id: UUID,
    collection_id: UUID | None,
    *,
    title: str,
    source_format: str,
    original_filename: str | None,
    content_type: str | None,
    content: bytes | None,
    source_url: str | None,
    idempotency_key: str | None,
    asset_kind: str = "LIBRARY",
) -> Asset:
    if collection_id is not None:
        _owned_collection(db, collection_id, owner_id)
    content_digest = hashlib.sha256(content).hexdigest() if content is not None else None
    digest = request_hash(
        {
            "collectionId": str(collection_id),
            "title": title.strip(),
            "sourceFormat": source_format,
            "originalFilename": original_filename,
            "sourceUrl": source_url,
            "contentSha256": content_digest,
            "assetKind": asset_kind,
        }
    )
    replay_id = replay_resource_id(db, owner_id, "asset:create", idempotency_key, digest)
    if replay_id is not None:
        existing = db.scalar(
            select(Asset)
            .where(Asset.id == replay_id, Asset.owner_id == owner_id)
            .options(selectinload(Asset.job))
        )
        if existing is not None:
            return existing
        raise HTTPException(status_code=409, detail="该幂等请求对应的资源已删除")

    asset_id = uuid4()
    storage_key = None
    if content is not None and original_filename is not None:
        storage_key = save_original(owner_id, asset_id, original_filename, content)
    asset = Asset(
        id=asset_id,
        owner_id=owner_id,
        collection_id=collection_id,
        kind=asset_kind,
        title=title.strip(),
        source_format=source_format,
        original_filename=original_filename,
        source_url=source_url,
        storage_key=storage_key,
        content_type=content_type,
        byte_size=len(content) if content is not None else None,
        content_sha256=content_digest,
        status="QUEUED",
        current_version=0,
    )
    job = IngestionJob(owner_id=owner_id, asset=asset)
    db.add(asset)
    try:
        db.flush()
        remember_request(db, owner_id, "asset:create", idempotency_key, digest, asset.id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        delete_original(storage_key, owner_id, asset_id)
        replay_id = replay_resource_id(db, owner_id, "asset:create", idempotency_key, digest)
        if replay_id is not None:
            existing = db.scalar(
                select(Asset)
                .where(Asset.id == replay_id, Asset.owner_id == owner_id)
                .options(selectinload(Asset.job))
            )
            if existing is not None:
                return existing
        raise HTTPException(status_code=409, detail="重复的资料创建请求") from exc
    _dispatch_ingestion(db, asset, job)
    db.refresh(asset)
    return asset


def _asset_detail(db: Session, asset_id: UUID, owner_id: UUID) -> AssetDetail:
    asset = db.scalar(
        select(Asset)
        .where(Asset.id == asset_id, Asset.owner_id == owner_id)
        .options(
            selectinload(Asset.versions).selectinload(AssetVersion.chunks),
            selectinload(Asset.job),
        )
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    versions = sorted(asset.versions, key=lambda item: item.version, reverse=True)
    current = next(
        (item for item in versions if item.version == asset.current_version),
        None,
    )
    return AssetDetail(
        **AssetSummary.model_validate(asset).model_dump(),
        content=current.content if current is not None else "",
        versions=[AssetVersionView.model_validate(item) for item in versions],
        chunks=(
            [
                TextChunkView.model_validate(item)
                for item in sorted(current.chunks, key=lambda chunk: chunk.ordinal)
            ]
            if current is not None
            else []
        ),
    )


@router.post(
    "/collections/{collection_id}/assets/text",
    response_model=AssetSummary,
    status_code=status.HTTP_201_CREATED,
)
def create_text_asset(
    collection_id: UUID,
    payload: AssetTextCreate,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=8, max_length=200
    ),
) -> Asset:
    return _create_asset(
        db,
        owner_id,
        collection_id,
        title=payload.title,
        content=payload.content,
        source_format=payload.source_format,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/collections/{collection_id}/assets/upload",
    response_model=AssetSummary,
    status_code=status.HTTP_201_CREATED,
)
async def upload_text_asset(
    collection_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=8, max_length=200
    ),
) -> Asset:
    filename = sanitize_filename(file.filename)
    raw = await file.read(settings.max_file_import_bytes + 1)
    await file.close()
    if len(raw) > settings.max_file_import_bytes:
        raise HTTPException(status_code=413, detail="文件不能超过 25 MiB")
    if not raw:
        raise HTTPException(status_code=422, detail="文件内容不能为空")
    try:
        kind = classify_upload(filename, file.content_type, raw)
    except ValueError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    if kind.source_format == "IMAGE" and len(raw) > settings.max_ocr_image_bytes:
        raise HTTPException(status_code=413, detail="OCR 图片不能超过 10 MiB")
    if kind.asynchronous:
        return _create_ingestion_asset(
            db,
            owner_id,
            collection_id,
            title=PurePath(filename).stem or "未命名资料",
            source_format=kind.source_format,
            original_filename=filename,
            content_type=kind.content_type,
            content=raw,
            source_url=None,
            idempotency_key=idempotency_key,
        )
    if len(raw) > settings.max_text_import_bytes:
        raise HTTPException(status_code=413, detail="文字文件不能超过 1 MiB")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="文件必须使用 UTF-8 编码") from exc
    if not content.strip():
        raise HTTPException(status_code=422, detail="文件内容不能为空")

    return _create_asset(
        db,
        owner_id,
        collection_id,
        title=PurePath(filename).stem or "未命名资料",
        content=content,
        source_format=kind.source_format,
        original_filename=filename,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/collections/{collection_id}/assets/url",
    response_model=AssetSummary,
    status_code=status.HTTP_201_CREATED,
)
def create_url_asset(
    collection_id: UUID,
    payload: AssetUrlCreate,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=8, max_length=200
    ),
) -> Asset:
    try:
        normalized_url = normalize_public_url(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    hostname = urlsplit(normalized_url).hostname or "网页"
    return _create_ingestion_asset(
        db,
        owner_id,
        collection_id,
        title=payload.title or hostname,
        source_format="WEB_PAGE",
        original_filename=None,
        content_type="text/html",
        content=None,
        source_url=normalized_url,
        idempotency_key=idempotency_key,
    )


@router.get("/collections/{collection_id}/assets", response_model=AssetPage)
def list_assets(
    collection_id: UUID,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> AssetPage:
    _owned_collection(db, collection_id, owner_id)
    items = db.scalars(
        select(Asset)
        .where(Asset.collection_id == collection_id, Asset.owner_id == owner_id)
        .options(selectinload(Asset.job))
        .order_by(Asset.updated_at.desc(), Asset.id.desc())
    ).all()
    return AssetPage(items=[AssetSummary.model_validate(item) for item in items])


@router.get("/assets/{asset_id}", response_model=AssetDetail)
def get_asset(
    asset_id: UUID,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> AssetDetail:
    return _asset_detail(db, asset_id, owner_id)


@router.post("/assets/{asset_id}/retry", response_model=AssetSummary)
def retry_ingestion(
    asset_id: UUID,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> Asset:
    asset = db.scalar(
        select(Asset)
        .where(Asset.id == asset_id, Asset.owner_id == owner_id)
        .options(selectinload(Asset.job))
        .with_for_update()
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    job = asset.job
    if job is None:
        raise HTTPException(status_code=409, detail="这份资料不需要异步解析")
    if job.status not in {"FAILED", "WAITING_FOR_CONFIGURATION", "PARTIAL_SUCCESS"}:
        raise HTTPException(status_code=409, detail="当前任务状态不能重试")
    if job.retry_count >= settings.ingestion_retry_limit:
        raise HTTPException(status_code=409, detail="已达到最多 3 次重试上限")
    job.retry_count += 1
    job.status = "QUEUED"
    job.stage = "STORED"
    job.processed_units = 0
    job.total_units = None
    job.error_code = None
    job.error_message = None
    job.unit_failures = []
    job.started_at = None
    job.finished_at = None
    asset.status = "QUEUED"
    db.commit()
    _dispatch_ingestion(db, asset, job)
    db.refresh(asset)
    return asset


@router.put("/assets/{asset_id}/content", response_model=AssetSummary)
def revise_asset(
    asset_id: UUID,
    payload: AssetContentUpdate,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> Asset:
    if len(payload.content.encode("utf-8")) > settings.max_text_import_bytes:
        raise HTTPException(status_code=413, detail="文字资料不能超过 1 MiB")
    asset = db.scalar(
        select(Asset)
        .where(Asset.id == asset_id, Asset.owner_id == owner_id)
        .with_for_update()
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    if asset.current_version != payload.expected_version:
        raise HTTPException(status_code=409, detail="资料已被修改，请刷新后重试")
    version = create_asset_version(asset, payload.content)
    db.add(version)
    db.commit()
    db.refresh(asset)
    return asset


@router.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(
    asset_id: UUID,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> None:
    asset = db.scalar(select(Asset).where(Asset.id == asset_id, Asset.owner_id == owner_id))
    if asset is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    storage_key = asset.storage_key
    chunk_ids = db.scalars(
        select(TextChunk.id)
        .join(AssetVersion)
        .where(AssetVersion.asset_id == asset_id)
    ).all()
    if chunk_ids:
        db.execute(
            update(Question)
            .where(Question.owner_id == owner_id, Question.source_chunk_id.in_(chunk_ids))
            .values(source_chunk_id=None, source_type="SOURCE_REMOVED")
        )
    db.delete(asset)
    db.commit()
    delete_original(storage_key, owner_id, asset_id)
