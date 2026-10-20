"""文字资料导入、查看与修订。"""
import re
from pathlib import PurePath
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.db import get_db
from app.identity import get_current_user_id
from app.idempotency import remember_request, replay_resource_id, request_hash
from app.models import Asset, AssetVersion, Collection, Question, TextChunk
from app.schemas.assets import (
    AssetContentUpdate,
    AssetDetail,
    AssetPage,
    AssetSummary,
    AssetTextCreate,
    AssetVersionView,
    TextChunkView,
)

router = APIRouter(tags=["assets"])


def _owned_collection(db: Session, collection_id: UUID, owner_id: UUID) -> Collection:
    collection = db.scalar(
        select(Collection).where(Collection.id == collection_id, Collection.owner_id == owner_id)
    )
    if collection is None:
        raise HTTPException(status_code=404, detail="资料库不存在")
    return collection


def _build_chunks(content: str, version: AssetVersion) -> list[TextChunk]:
    """按空行分段并保留字符偏移，超长段按 4000 字拆开。"""
    chunks: list[TextChunk] = []
    ordinal = 0
    for match in re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", content, re.DOTALL):
        block = match.group(0)
        for relative_start in range(0, len(block), 4000):
            piece = block[relative_start : relative_start + 4000]
            start = match.start() + relative_start
            chunks.append(
                TextChunk(
                    asset_version=version,
                    ordinal=ordinal,
                    content=piece,
                    start_offset=start,
                    end_offset=start + len(piece),
                )
            )
            ordinal += 1
    if not chunks:
        chunks.append(
            TextChunk(
                asset_version=version,
                ordinal=0,
                content=content,
                start_offset=0,
                end_offset=len(content),
            )
        )
    return chunks


def _create_asset(
    db: Session,
    owner_id: UUID,
    collection_id: UUID,
    *,
    title: str,
    content: str,
    source_format: str,
    original_filename: str | None = None,
    idempotency_key: str | None = None,
) -> Asset:
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
        title=title.strip(),
        source_format=source_format,
        original_filename=original_filename,
    )
    version = AssetVersion(asset=asset, version=1, content=content)
    _build_chunks(content, version)
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


def _asset_detail(db: Session, asset_id: UUID, owner_id: UUID) -> AssetDetail:
    asset = db.scalar(
        select(Asset)
        .where(Asset.id == asset_id, Asset.owner_id == owner_id)
        .options(selectinload(Asset.versions).selectinload(AssetVersion.chunks))
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    versions = sorted(asset.versions, key=lambda item: item.version, reverse=True)
    current = next(item for item in versions if item.version == asset.current_version)
    return AssetDetail(
        **AssetSummary.model_validate(asset).model_dump(),
        content=current.content,
        versions=[AssetVersionView.model_validate(item) for item in versions],
        chunks=[
            TextChunkView.model_validate(item)
            for item in sorted(current.chunks, key=lambda chunk: chunk.ordinal)
        ],
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
    raw_filename = (file.filename or "").replace("\\", "/")
    filename = PurePath(raw_filename).name[:255]
    suffix = PurePath(filename).suffix.lower()
    formats = {".txt": "TXT_FILE", ".md": "MARKDOWN_FILE", ".markdown": "MARKDOWN_FILE"}
    if suffix not in formats:
        raise HTTPException(status_code=415, detail="只支持 .txt、.md 和 .markdown 文件")

    raw = await file.read(settings.max_text_import_bytes + 1)
    await file.close()
    if len(raw) > settings.max_text_import_bytes:
        raise HTTPException(status_code=413, detail="文件不能超过 1 MiB")
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
        source_format=formats[suffix],
        original_filename=filename,
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
    asset.current_version += 1
    version = AssetVersion(asset=asset, version=asset.current_version, content=payload.content)
    _build_chunks(payload.content, version)
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
