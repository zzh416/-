"""批量导入记录与资料的关联；解析状态仍以资料任务为准。"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.core import Asset


class UrlImportBatch(Base):
    __tablename__ = "url_import_batches"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    collection_id: Mapped[UUID] = mapped_column(ForeignKey("collections.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    items: Mapped[list["UrlImportItem"]] = relationship(cascade="all, delete-orphan", order_by="UrlImportItem.ordinal")


class UrlImportItem(Base):
    __tablename__ = "url_import_items"
    __table_args__ = (UniqueConstraint("batch_id", "ordinal", name="uq_url_import_items_batch_ordinal"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("url_import_batches.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    input_url: Mapped[str] = mapped_column(Text)
    normalized_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    disposition: Mapped[str] = mapped_column(String(16))
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duplicate_of: Mapped[int | None] = mapped_column(Integer, nullable=True)
    asset_id: Mapped[UUID | None] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"), index=True, nullable=True)
    asset: Mapped[Asset | None] = relationship()
