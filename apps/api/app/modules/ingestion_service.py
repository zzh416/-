"""P2 异步资料解析编排。"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select

from app.adapters.glm_ocr import (
    OCRNotConfigured,
    OCRResult,
    OCRServiceError,
)
from app.adapters.ocr_provider import extract_with_configured_ocr
from app.adapters.browser_renderer import (
    BrowserRenderError,
    RenderedPage,
    render_public_page,
)
from app.config import settings
from app.db import SessionLocal
from app.models import Asset, IngestionJob
from app.modules.content_versions import create_asset_version
from app.modules.parsers import (
    IngestionParseError,
    ParseResult,
    SourceBlock,
    convert_legacy_doc,
    extract_webpage,
    parse_docx,
    parse_pdf,
    render_source_blocks,
)
from app.modules.safe_web import FetchedDocument, SafeWebError, fetch_public_document
from app.modules.storage import read_original, save_original

logger = logging.getLogger(__name__)

OCRExtractor = Callable[..., OCRResult]
WebFetcher = Callable[..., FetchedDocument]
BrowserRenderer = Callable[..., RenderedPage]
StageReporter = Callable[[str], None]

_BROWSER_FALLBACK_FETCH_ERRORS = {
    "WEB_ACCESS_DENIED",
    "WEB_ENCODING_UNSUPPORTED",
    "WEB_REDIRECT_BLOCKED",
}


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
    source_blocks: list[SourceBlock] = field(default_factory=list)
    unit_failures: list[dict] = field(default_factory=list)


def _ocr(content: bytes, extractor: OCRExtractor | None) -> OCRResult:
    return extract_with_configured_ocr(content, extractor=extractor)


def _process_structured_result(
    parsed: ParseResult,
    extractor: OCRExtractor | None,
) -> _Processed:
    blocks = list(parsed.blocks)
    if not parsed.ocr_units:
        return _Processed(
            content=parsed.content,
            total_units=parsed.total_units,
            processed_units=parsed.total_units,
            source_blocks=blocks,
        )

    unit_failures: list[dict] = []
    for unit_index, unit in enumerate(parsed.ocr_units):
        try:
            recognized = _ocr(unit.content, extractor)
        except OCRNotConfigured:
            for pending in parsed.ocr_units[unit_index:]:
                unit_failures.append(
                    {
                        "code": "OCR_NOT_CONFIGURED",
                        "message": f"{pending.label}等待 OCR 配置",
                        "locator": pending.locator,
                    }
                )
            break
        original = blocks[unit.block_index]
        blocks[unit.block_index] = replace(
            original,
            content=recognized.markdown.strip(),
            locator={**original.locator, **unit.locator, "extraction": "OCR"},
        )

    if unit_failures and not any(block.content.strip() for block in blocks):
        raise OCRNotConfigured("missing")

    content = render_source_blocks(blocks)
    failed_empty_blocks = {
        unit.block_index
        for unit in parsed.ocr_units
        if not blocks[unit.block_index].content.strip()
    }
    if unit_failures:
        return _Processed(
            content=content,
            status="PARTIAL_SUCCESS",
            error_code="OCR_NOT_CONFIGURED",
            error_message=f"有 {len(unit_failures)} 个位置等待 OCR 配置",
            total_units=parsed.total_units,
            processed_units=max(0, parsed.total_units - len(failed_empty_blocks)),
            source_blocks=blocks,
            unit_failures=unit_failures,
        )
    return _Processed(
        content=content,
        total_units=parsed.total_units,
        processed_units=parsed.total_units,
        source_blocks=blocks,
    )


def _extract(
    asset: _AssetSnapshot,
    *,
    ocr_extractor: OCRExtractor | None,
    web_fetcher: WebFetcher,
    browser_renderer: BrowserRenderer,
    stage_reporter: StageReporter,
) -> _Processed:
    if asset.source_format == "WEB_PAGE":
        if not asset.source_url:
            raise IngestionParseError("WEB_URL_MISSING", "网页资料缺少来源地址")
        rendered_title: str | None = None
        fetched: FetchedDocument | None = None
        # Hash routes select client-side content, even when the static shell has text.
        if not urlsplit(asset.source_url).fragment.startswith(("/", "!/")):
            try:
                fetched = web_fetcher(
                    asset.source_url,
                    max_bytes=settings.max_url_response_bytes,
                    timeout=settings.ingestion_http_timeout_seconds,
                )
                try:
                    parsed = extract_webpage(fetched.content, fetched.url)
                except IngestionParseError as exc:
                    if exc.code != "WEB_CONTENT_EMPTY":
                        raise
                    fetched = None
            except SafeWebError as exc:
                if exc.code not in _BROWSER_FALLBACK_FETCH_ERRORS:
                    raise
                fetched = None

        if fetched is None:
            stage_reporter("RENDERING")
            rendered = browser_renderer(
                asset.source_url,
                service_url=settings.renderer_url,
                timeout_seconds=settings.renderer_timeout_seconds,
                max_html_bytes=settings.max_url_response_bytes,
            )
            rendered_title = rendered.title
            fetched = FetchedDocument(
                url=rendered.url,
                content_type=rendered.content_type,
                content=rendered.content,
            )
            parsed = extract_webpage(fetched.content, fetched.url)
        return _Processed(
            content=parsed.content,
            fetched=fetched,
            fetched_title=parsed.title or rendered_title,
            source_blocks=parsed.blocks,
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
            source_blocks=[
                SourceBlock(
                    "IMAGE_OCR",
                    recognized.markdown,
                    {"page": 1, "extraction": "OCR"},
                )
            ],
        )
    if asset.source_format == "PDF":
        parsed = parse_pdf(
            content,
            max_pages=settings.max_pdf_pages,
            max_content_stream_bytes=settings.max_pdf_content_stream_bytes,
        )
        return _process_structured_result(parsed, ocr_extractor)
    if asset.source_format == "DOCX":
        return _process_structured_result(parse_docx(content), ocr_extractor)
    if asset.source_format == "DOC":
        converted = convert_legacy_doc(content)
        return _process_structured_result(parse_docx(converted), ocr_extractor)
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


def _set_stage(job_id: UUID, stage: str) -> None:
    with SessionLocal() as db:
        job = db.scalar(
            select(IngestionJob)
            .where(IngestionJob.id == job_id)
            .with_for_update()
        )
        if job is None or job.status != "PROCESSING":
            return
        job.stage = stage
        db.commit()


def process_ingestion_job(
    job_id: UUID,
    *,
    ocr_extractor: OCRExtractor | None = None,
    web_fetcher: WebFetcher = fetch_public_document,
    browser_renderer: BrowserRenderer = render_public_page,
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
        job.unit_failures = []
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
            browser_renderer=browser_renderer,
            stage_reporter=lambda stage: _set_stage(job_id, stage),
        )
        if not processed.content.strip():
            raise IngestionParseError("EXTRACTED_TEXT_EMPTY", "没有提取到可用文字")
        if len(processed.content.encode("utf-8")) > settings.max_extracted_text_bytes:
            raise IngestionParseError("EXTRACTED_TEXT_TOO_LARGE", "提取文字超过 5 MiB 上限")
    except OCRNotConfigured:
        key_name = "DEEPSEEK_API_KEY" if settings.ocr_provider == "deepseek" else "GLM_API_KEY"
        _mark_failure(
            job_id,
            "WAITING_FOR_CONFIGURATION",
            "OCR_NOT_CONFIGURED",
            f"该资料需要 OCR；请在 infra/.env 配置 {key_name} 后重试",
        )
        return
    except OCRServiceError as exc:
        _mark_failure(job_id, "FAILED", exc.code, str(exc))
        return
    except BrowserRenderError as exc:
        _mark_failure(job_id, "FAILED", exc.code, exc.message)
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
        version = create_asset_version(asset, processed.content, processed.source_blocks)
        db.add(version)
        asset.status = processed.status
        job.status = "SUCCEEDED" if processed.status == "READY" else "PARTIAL_SUCCESS"
        job.stage = "COMPLETE"
        job.processed_units = processed.processed_units
        job.total_units = processed.total_units
        job.error_code = processed.error_code
        job.error_message = processed.error_message
        job.unit_failures = processed.unit_failures
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
