"""创建接口的持久化幂等记录。"""
import hashlib
import json
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import IdempotencyRecord


def request_hash(payload: BaseModel | dict[str, Any]) -> str:
    data = payload.model_dump(mode="json", by_alias=True) if isinstance(payload, BaseModel) else payload
    serialized = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def replay_resource_id(
    db: Session,
    owner_id: UUID,
    scope: str,
    idempotency_key: str | None,
    digest: str,
) -> UUID | None:
    if idempotency_key is None:
        return None
    record = db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.owner_id == owner_id,
            IdempotencyRecord.scope == scope,
            IdempotencyRecord.idempotency_key == idempotency_key,
        )
    )
    if record is None:
        return None
    if record.request_hash != digest:
        raise HTTPException(status_code=409, detail="同一个 Idempotency-Key 不能用于不同请求")
    return record.resource_id


def remember_request(
    db: Session,
    owner_id: UUID,
    scope: str,
    idempotency_key: str | None,
    digest: str,
    resource_id: UUID,
) -> None:
    if idempotency_key is None:
        return
    db.add(
        IdempotencyRecord(
            owner_id=owner_id,
            scope=scope,
            idempotency_key=idempotency_key,
            request_hash=digest,
            resource_id=resource_id,
        )
    )
