"""P2 原件存储：所有路径由服务端生成并限制在 DATA_DIR/assets 下。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path, PurePath
from uuid import UUID

from app.config import settings


class UnsafeStoragePathError(ValueError):
    """数据库中的存储键不满足当前资料的所有权约束。"""


def sanitize_filename(value: str | None) -> str:
    raw = (value or "").replace("\\", "/")
    return (PurePath(raw).name or "unnamed")[:255]


def _root() -> Path:
    root = (Path(settings.data_dir) / "assets").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _asset_directory(owner_id: UUID, asset_id: UUID) -> Path:
    directory = _root() / str(owner_id) / str(asset_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def resolve_storage_key(storage_key: str, owner_id: UUID, asset_id: UUID) -> Path:
    root = _root()
    expected_prefix = PurePath(str(owner_id), str(asset_id))
    supplied = PurePath(storage_key)
    if supplied.is_absolute() or supplied.parts[:2] != expected_prefix.parts:
        raise UnsafeStoragePathError("原件路径不属于当前资料")
    candidate = (root / supplied).resolve()
    if candidate == root or not candidate.is_relative_to(root):
        raise UnsafeStoragePathError("原件路径越出存储目录")
    return candidate


def save_original(owner_id: UUID, asset_id: UUID, filename: str, content: bytes) -> str:
    safe_name = sanitize_filename(filename)
    suffix = PurePath(safe_name).suffix.lower()
    directory = _asset_directory(owner_id, asset_id)
    destination = directory / f"original{suffix}"
    with tempfile.NamedTemporaryFile(dir=directory, prefix="upload-", delete=False) as temp:
        temp.write(content)
        temporary_path = Path(temp.name)
    try:
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    return str(PurePath(str(owner_id), str(asset_id), destination.name)).replace("\\", "/")


def read_original(storage_key: str, owner_id: UUID, asset_id: UUID) -> bytes:
    return resolve_storage_key(storage_key, owner_id, asset_id).read_bytes()


def delete_original(storage_key: str | None, owner_id: UUID, asset_id: UUID) -> None:
    if not storage_key:
        return
    path = resolve_storage_key(storage_key, owner_id, asset_id)
    path.unlink(missing_ok=True)
    for directory in (path.parent, path.parent.parent):
        try:
            directory.rmdir()
        except OSError:
            break
