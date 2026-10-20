"""资料库 CRUD。"""
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.identity import get_current_user_id
from app.idempotency import remember_request, replay_resource_id, request_hash
from app.models import Asset, Collection, Question
from app.schemas.collections import CollectionCreate, CollectionPage, CollectionView

router = APIRouter(prefix="/collections", tags=["collections"])


@router.post("", response_model=CollectionView, status_code=status.HTTP_201_CREATED)
def create_collection(
    payload: CollectionCreate,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=8, max_length=200
    ),
) -> Collection:
    digest = request_hash(payload)
    replay_id = replay_resource_id(db, owner_id, "collection:create", idempotency_key, digest)
    if replay_id is not None:
        existing = db.scalar(
            select(Collection).where(Collection.id == replay_id, Collection.owner_id == owner_id)
        )
        if existing is not None:
            return existing
        raise HTTPException(status_code=409, detail="该幂等请求对应的资源已删除")

    collection = Collection(
        owner_id=owner_id,
        name=payload.name,
        purpose=payload.purpose,
    )
    db.add(collection)
    try:
        db.flush()
        remember_request(
            db, owner_id, "collection:create", idempotency_key, digest, collection.id
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if idempotency_key is not None:
            replay_id = replay_resource_id(
                db, owner_id, "collection:create", idempotency_key, digest
            )
            if replay_id is not None:
                existing = db.scalar(
                    select(Collection).where(
                        Collection.id == replay_id, Collection.owner_id == owner_id
                    )
                )
                if existing is not None:
                    return existing
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="同名资料库已存在",
        ) from exc
    db.refresh(collection)
    return collection


@router.get("", response_model=CollectionPage)
def list_collections(
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> CollectionPage:
    items = db.scalars(
        select(Collection)
        .where(Collection.owner_id == owner_id)
        .order_by(Collection.updated_at.desc(), Collection.id.desc())
    ).all()
    return CollectionPage(items=list(items))


@router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_collection(
    collection_id: UUID,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> Response:
    collection = db.scalar(
        select(Collection).where(
            Collection.id == collection_id,
            Collection.owner_id == owner_id,
        )
    )
    if collection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="资料库不存在")
    has_content = db.scalar(
        select(Asset.id).where(Asset.collection_id == collection_id).limit(1)
    ) or db.scalar(select(Question.id).where(Question.collection_id == collection_id).limit(1))
    if has_content:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="资料库内仍有资料或问题，请先清空内容",
        )
    db.delete(collection)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
