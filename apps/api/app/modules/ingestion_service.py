"""P2 异步资料解析编排。"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select

from app.adapters.glm_ocr import (
    OCRNotConfigured,
    OCRResult,
    OCRServiceError,
    extract_with_glm_ocr,
)
from app.config import settings
from app.db import SessionLocal
from app.models import Asset, IngestionJob
from app.modules.content_versions import create_asset_version
from app.modules.parsers import (
    IngestionParseError,
    ParseResult,
    convert_legacy_doc,
    extract_webpage,
    parse_docx,
    parse_pdf,
)
from app.modules.safe_web import FetchedDocument, SafeWebError, fetch_public_document
from app.modules.storage import read_original, save_original

logger = logging.getLogger(__name__)

OCRExtractor = Callable[..., OCRResult]
WebFetcher = Callable[..., FetchedDocument]


@dataclass(frozen=True)
class _AssetSnapshot:
    id: UUID
    owner_id: UUID
    source_format: str
    source_url: str | None
    storage_key: str | None
    title: str


@dataclass(frozen=True)
class _Processed:
    content: str
    status: str = "READY"
    error_code: str | None = None
    error_message: str | None = None
    total_units: int = 1
    processed_units: int = 1
    fetched: FetchedDocument | None = None
    fetched_title: str | None = None


def _ocr(content: bytes, extractor: OCRExtractor) -> OCRResult:
    return extractor(
        content,
        api_key=settings.glm_api_key,
        base_url=settings.glm_base_url,
        model=settings.glm_ocr_model,
    )


def _append_embedded_image_ocr(
    parsed: ParseResult,
    extractor: OCRExtractor,
) -> _Processed:
    if not parsed.embedded_images:
        return _Processed(content=parsed.content)
    sections = [parsed.content] if parsed.content else []
    try:
        for image in parsed.embedded_images:
            recognized = _ocr(image.content, extractor)
            sections.append(f"## 内嵌图片：{image.name}\n\n{recognized.markdown}")
    except OCRNotConfigured:
        if not parsed.content:
            raise
        sections.append(
            f"> 有 {len(parsed.embedded_images)} 张内嵌图片尚未识别；配置 GLM_API_KEY 后可重试。"
        )
        return _Processed(
            content="\n\n".join(sections),
            status="PARTIAL_SUCCESS",
            error_code="OCR_NOT_CONFIGURED",
            error_message="DOCX 文字已提取，但内嵌图片等待 OCR 配置",
        )
    return _Processed(content="\n\n".join(sections))


def _extract(
    asset: _AssetSnapshot,
    *,
    ocr_extractor: OCRExtractor,
    web_fetcher: WebFetcher,
) -> _Processed:
    if asset.source_format == "WEB_PAGE":
        if not asset.source_url:
            raise IngestionParseError("WEB_URL_MISSING", "网页资料缺少来源地址")
        fetched = web_fetcher(
            asset.source_url,
            max_bytes=settings.max_url_response_bytes,
            timeout=settings.ingestion_http_timeout_seconds,
        )
        parsed = extract_webpage(fetched.content, fetched.url)
        return _Processed(
            content=parsed.content,
            fetched=fetched,
            fetched_title=parsed.title,
        )

    if not asset.storage_key:
        raise IngestionParseError("ORIGINAL_FILE_MISSING", "资料原件不存在")
    content = read_original(asset.storage_key, asset.owner_id, asset.id)
    if asset.source_format == "IMAGE":
        recognized = _ocr(content, ocr_extractor)
        return _Processed(
            content=recognized.markdown,
            total_units=recognized.page_count or 1,
            processed_units=recognized.page_count or 1,
        )
    if asset.source_format == "PDF":
        parsed = parse_pdf(
            content,
            max_pages=settings.max_pdf_pages,
            max_content_stream_bytes=settings.max_pdf_content_stream_bytes,
        )
        if not parsed.missing_units:
            return _Processed(
                content=parsed.content,
                total_units=parsed.total_units,
                processed_units=parsed.total_units,
            )
        try:
            recognized = _ocr(content, ocr_extractor)
        except OCRNotConfigured:
            if not parsed.content:
                raise
            pages = "、".join(str(page) for page in parsed.missing_units)
            return _Processed(
                content=(
                    f"{parsed.content}\n\n"
                    f"> 第 {pages} 页没有可提取文字；配置 GLM_API_KEY 后可重试 OCR。"
                ),
                status="PARTIAL_SUCCESS",
                error_code="OCR_NOT_CONFIGURED",
                error_message=f"第 {pages} 页等待 OCR 配置",
                total_units=parsed.total_units,
                processed_units=parsed.total_units - len(parsed.missing_units),
            )
        return _Processed(
            content=recognized.markdown,
            total_units=recognized.page_count or parsed.total_units,
            processed_units=recognized.page_count or parsed.total_units,
        )
    if asset.source_format == "DOCX":
        return _append_embedded_image_ocr(parse_docx(content), ocr_extractor)
    if asset.source_format == "DOC":
        converted = convert_legacy_doc(content)
        return _append_embedded_image_ocr(parse_docx(converted), ocr_extractor)
    raise IngestionParseError("SOURCE_FORMAT_UNSUPPORTED", "该资料格式没有可用解析器")


def _mark_failure(job_id: UUID, status: str, code: str, message: str) -> None:
    with SessionLocal() as db:
        job = db.scalar(
            select(IngestionJob)
            .where(IngestionJob.id == job_id)
            .with_for_update()
        )
        if job is None:
            return
        job.status = status
        job.stage = "CONFIGURATION" if status == "WAITING_FOR_CONFIGURATION" else "FAILED"
        job.error_code = code
        job.error_message = message[:500]
        job.finished_at = datetime.now(timezone.utc)
        job.asset.status = status
        db.commit()


def process_ingestion_job(
    job_id: UUID,
    *,
    ocr_extractor: OCRExtractor = extract_with_glm_ocr,
    web_fetcher: WebFetcher = fetch_public_document,
) -> None:
    with SessionLocal() as db:
        job = db.scalar(
            select(IngestionJob)
            .where(IngestionJob.id == job_id)
            .with_for_update()
        )
        if job is None or job.status not in {"QUEUED", "FAILED", "WAITING_FOR_CONFIGURATION"}:
            return
        asset = job.asset
        job.status = "PROCESSING"
        job.stage = "FETCHING" if asset.source_format == "WEB_PAGE" else "EXTRACTING"
        job.error_code = None
        job.error_message = None
        job.started_at = datetime.now(timezone.utc)
        job.finished_at = None
        asset.status = "PROCESSING"
        snapshot = _AssetSnapshot(
            id=asset.id,
            owner_id=asset.owner_id,
            source_format=asset.source_format,
            source_url=asset.source_url,
            storage_key=asset.storage_key,
            title=asset.title,
        )
        db.commit()

    try:
        processed = _extract(
            snapshot,
            ocr_extractor=ocr_extractor,
            web_fetcher=web_fetcher,
        )
        if not processed.content.strip():
            raise IngestionParseError("EXTRACTED_TEXT_EMPTY", "没有提取到可用文字")
        if len(processed.content.encode("utf-8")) > settings.max_extracted_text_bytes:
            raise IngestionParseError("EXTRACTED_TEXT_TOO_LARGE", "提取文字超过 5 MiB 上限")
    except OCRNotConfigured:
        _mark_failure(
            job_id,
            "WAITING_FOR_CONFIGURATION",
            "OCR_NOT_CONFIGURED",
            "该资料需要 OCR；请在 infra/.env 配置 GLM_API_KEY 后重试",
        )
        return
    except OCRServiceError as exc:
        _mark_failure(job_id, "FAILED", exc.code, str(exc))
        return
    except (IngestionParseError, SafeWebError) as exc:
        _mark_failure(job_id, "FAILED", exc.code, exc.message)
        return
    except Exception:
        logger.exception("Unexpected ingestion failure for job %s", job_id)
        _mark_failure(job_id, "FAILED", "INGESTION_INTERNAL_ERROR", "解析发生内部错误，请稍后重试")
        return

    with SessionLocal() as db:
        job = db.scalar(
            select(IngestionJob)
            .where(IngestionJob.id == job_id)
            .with_for_update()
        )
        if job is None:
            return
        asset = job.asset
        if processed.fetched is not None:
            storage_key = save_original(
                asset.owner_id,
                asset.id,
                "page.html",
                processed.fetched.content,
            )
            asset.storage_key = storage_key
            asset.content_type = processed.fetched.content_type
            asset.byte_size = len(processed.fetched.content)
            asset.content_sha256 = hashlib.sha256(processed.fetched.content).hexdigest()
            asset.fetched_at = datetime.now(timezone.utc)
            host = urlsplit(asset.source_url or "").hostname
            if processed.fetched_title and asset.title in {host, "网页"}:
                asset.title = processed.fetched_title[:200]
        version = create_asset_version(asset, processed.content)
        db.add(version)
        asset.status = processed.status
        job.status = "SUCCEEDED" if processed.status == "READY" else "PARTIAL_SUCCESS"
        job.stage = "COMPLETE"
        job.processed_units = processed.processed_units
        job.total_units = processed.total_units
        job.error_code = processed.error_code
        job.error_message = processed.error_message
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
