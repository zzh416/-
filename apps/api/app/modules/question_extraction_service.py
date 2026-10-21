"""将模型候选严格校验后写成可追溯题目。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from app.adapters.glm_text import (
    ExtractedPair,
    ExtractionBlock,
    TextModelError,
    TextModelNotConfigured,
    extract_questions_with_glm,
)
from app.config import settings
from app.db import SessionLocal
from app.models import (
    Answer,
    AnswerRevision,
    Asset,
    Question,
    QuestionExtractionRun,
    QuestionOccurrence,
    TextChunk,
)

Extractor = Callable[..., list[ExtractedPair]]


@dataclass(frozen=True)
class LoadedChunk:
    id: UUID
    ordinal: int
    content: str


def _build_extraction_batches(
    chunks: list[LoadedChunk],
    *,
    max_chars: int,
    max_blocks: int,
    overlap_blocks: int,
) -> list[list[LoadedChunk]]:
    if not chunks:
        return []
    if max_chars < 1 or max_blocks < 1 or overlap_blocks < 0:
        raise ValueError("题目抽取批次配置无效")
    batches: list[list[LoadedChunk]] = []
    start = 0
    while start < len(chunks):
        end = start
        char_count = 0
        while end < len(chunks) and end - start < max_blocks:
            next_size = len(chunks[end].content)
            if end > start and char_count + next_size > max_chars:
                break
            char_count += next_size
            end += 1
        batches.append(chunks[start:end])
        if end >= len(chunks):
            break
        start = max(start + 1, end - min(overlap_blocks, end - start - 1))
    return batches


def _finish_failure(run_id: UUID, code: str, message: str, status: str = "FAILED") -> None:
    with SessionLocal() as db:
        run = db.get(QuestionExtractionRun, run_id)
        if run is None:
            return
        run.status = status
        run.stage = "MODEL"
        run.error_code = code
        run.error_message = message[:500]
        run.finished_at = datetime.now(timezone.utc)
        db.commit()


def _validated_slice(
    chunks: dict[int, LoadedChunk],
    ordinal: int,
    start: int,
    end: int,
    quote: str,
) -> tuple[LoadedChunk, str, int, int]:
    chunk = chunks.get(ordinal)
    if chunk is None:
        raise TextModelError(
            "TEXT_MODEL_SOURCE_MISMATCH",
            "模型返回的题目或答案无法在当前资料版本中定位",
            retryable=False,
        )
    if end <= len(chunk.content) and chunk.content[start:end] == quote:
        resolved_start = start
    else:
        resolved_start = chunk.content.find(quote)
        if resolved_start < 0 or chunk.content.find(quote, resolved_start + 1) >= 0:
            raise TextModelError(
                "TEXT_MODEL_SOURCE_MISMATCH",
                "模型引文未在指定来源块中唯一出现",
                retryable=False,
            )
    return chunk, quote.strip(), resolved_start, resolved_start + len(quote)


def process_question_extraction_run(
    run_id: UUID,
    *,
    extractor: Extractor = extract_questions_with_glm,
) -> None:
    with SessionLocal() as db:
        run = db.scalar(
            select(QuestionExtractionRun)
            .where(QuestionExtractionRun.id == run_id)
            .with_for_update()
        )
        if run is None or run.status in {"SUCCEEDED", "PROCESSING"}:
            return
        run.status = "PROCESSING"
        run.stage = "MODEL"
        run.model = settings.glm_text_model
        run.started_at = datetime.now(timezone.utc)
        run.error_code = None
        run.error_message = None
        stored_chunks = list(
            db.scalars(
                select(TextChunk)
                .where(TextChunk.asset_version_id == run.asset_version_id)
                .order_by(TextChunk.ordinal)
            ).all()
        )
        chunks = [
            LoadedChunk(id=chunk.id, ordinal=chunk.ordinal, content=chunk.content)
            for chunk in stored_chunks
        ]
        db.commit()

    total_chars = sum(len(chunk.content) for chunk in chunks)
    if total_chars > settings.max_question_extraction_chars:
        _finish_failure(
            run_id,
            "EXTRACTION_INPUT_TOO_LARGE",
            "资料超过单次题目抽取上限；后续将按来源块分批处理",
        )
        return
    batches = _build_extraction_batches(
        chunks,
        max_chars=settings.question_extraction_batch_chars,
        max_blocks=settings.question_extraction_batch_blocks,
        overlap_blocks=settings.question_extraction_overlap_blocks,
    )
    try:
        pairs: list[ExtractedPair] = []
        for batch in batches:
            pairs.extend(
                extractor(
                    [
                        ExtractionBlock(ordinal=chunk.ordinal, content=chunk.content)
                        for chunk in batch
                    ],
                    api_key=settings.glm_api_key,
                    base_url=settings.glm_base_url,
                    model=settings.glm_text_model,
                    max_output_tokens=settings.question_extraction_max_output_tokens,
                )
            )
    except TextModelNotConfigured:
        _finish_failure(
            run_id,
            "GLM_NOT_CONFIGURED",
            "请先配置 GLM_API_KEY，再重新发起题目抽取",
            status="WAITING_FOR_CONFIGURATION",
        )
        return
    except TextModelError as exc:
        _finish_failure(run_id, exc.code, str(exc))
        return
    except Exception:
        _finish_failure(
            run_id,
            "TEXT_MODEL_INTERNAL_ERROR",
            "题目抽取任务发生内部错误，请稍后重试",
        )
        return

    chunk_map = {chunk.ordinal: chunk for chunk in chunks}
    try:
        with SessionLocal() as db:
            run = db.scalar(
                select(QuestionExtractionRun)
                .where(QuestionExtractionRun.id == run_id)
                .with_for_update()
            )
            if run is None:
                return
            asset = db.get(Asset, run.asset_id)
            if asset is None:
                return
            answered_count = 0
            seen_ranges: set[tuple[UUID, int, int]] = set()
            created_by_range: dict[
                tuple[UUID, int, int], tuple[Question, QuestionOccurrence]
            ] = {}
            for pair in pairs:
                source_chunk, prompt, question_start, question_end = _validated_slice(
                    chunk_map,
                    pair.question_block_ordinal,
                    pair.question_start,
                    pair.question_end,
                    pair.question_quote,
                )
                if not prompt:
                    raise TextModelError(
                        "TEXT_MODEL_SOURCE_MISMATCH", "模型返回了空题目", retryable=False
                    )
                source_key = (source_chunk.id, question_start, question_end)
                answer_chunk = None
                answer_text = None
                answer_start = None
                answer_end = None
                if pair.answer_quote is not None:
                    answer_chunk, answer_text, answer_start, answer_end = _validated_slice(
                        chunk_map,
                        pair.answer_block_ordinal,
                        pair.answer_start,
                        pair.answer_end,
                        pair.answer_quote,
                    )
                    if not answer_text:
                        raise TextModelError(
                            "TEXT_MODEL_SOURCE_MISMATCH", "模型返回了空答案", retryable=False
                        )
                existing = created_by_range.get(source_key)
                if existing is not None:
                    existing_question, existing_occurrence = existing
                    if existing_question.answer is None and answer_text is not None:
                        answer = Answer(
                            owner_id=run.owner_id,
                            content=answer_text,
                            source_type="SOURCE_EXTRACTED",
                        )
                        answer.revisions.append(
                            AnswerRevision(version=1, content=answer_text)
                        )
                        existing_question.answer = answer
                        existing_occurrence.answer_source_chunk_id = answer_chunk.id
                        existing_occurrence.answer_start = answer_start
                        existing_occurrence.answer_end = answer_end
                        existing_occurrence.answer_quote = pair.answer_quote
                        existing_occurrence.confidence = max(
                            existing_occurrence.confidence, pair.confidence
                        )
                        answered_count += 1
                    continue
                seen_ranges.add(source_key)
                question = Question(
                    owner_id=run.owner_id,
                    collection_id=asset.collection_id,
                    source_chunk_id=source_chunk.id,
                    source_type="SOURCE_EXTRACTED",
                    prompt=prompt,
                )
                if answer_text is not None:
                    answer = Answer(
                        owner_id=run.owner_id,
                        content=answer_text,
                        source_type="SOURCE_EXTRACTED",
                    )
                    answer.revisions.append(AnswerRevision(version=1, content=answer_text))
                    question.answer = answer
                    answered_count += 1
                db.add(question)
                db.flush()
                occurrence = QuestionOccurrence(
                        question_id=question.id,
                        extraction_run_id=run.id,
                        asset_version_id=run.asset_version_id,
                        source_chunk_id=source_chunk.id,
                        answer_source_chunk_id=answer_chunk.id if answer_chunk else None,
                        question_start=question_start,
                        question_end=question_end,
                        answer_start=answer_start,
                        answer_end=answer_end,
                        question_quote=pair.question_quote,
                        answer_quote=pair.answer_quote,
                        confidence=pair.confidence,
                        review_status="PENDING",
                    )
                db.add(occurrence)
                created_by_range[source_key] = (question, occurrence)
            run.question_count = len(seen_ranges)
            run.answered_count = answered_count
            run.missing_count = len(seen_ranges) - answered_count
            run.status = "SUCCEEDED"
            run.stage = "PERSISTED"
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
    except TextModelError as exc:
        _finish_failure(run_id, exc.code, str(exc))
    except Exception:
        _finish_failure(
            run_id,
            "EXTRACTION_PERSISTENCE_ERROR",
            "题目抽取结果保存失败，请稍后重试",
        )
