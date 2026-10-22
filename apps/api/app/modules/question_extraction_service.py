"""将模型候选严格校验后写成可追溯题目。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from uuid import UUID

from sqlalchemy import select

from app.adapters.glm_text import (
    ExtractedPair,
    ExtractedSpan,
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
        if not run.stage.startswith("MODEL_BATCH_"):
            run.stage = "MODEL"
        run.error_code = code
        run.error_message = message[:500]
        run.finished_at = datetime.now(timezone.utc)
        db.commit()


def _set_batch_stage(run_id: UUID, current: int, total: int) -> None:
    with SessionLocal() as db:
        run = db.get(QuestionExtractionRun, run_id)
        if run is None:
            return
        run.stage = f"MODEL_BATCH_{current}_OF_{total}"
        db.commit()


def _validated_span(
    chunks: dict[int, LoadedChunk],
    span: ExtractedSpan,
) -> tuple[LoadedChunk, str, int, int]:
    chunk = chunks.get(span.block_ordinal)
    if chunk is None:
        raise TextModelError(
            "TEXT_MODEL_SOURCE_MISMATCH",
            "模型返回的题目或答案无法在当前资料版本中定位",
            retryable=False,
        )
    resolved_start = chunk.content.find(span.quote)
    if resolved_start < 0 or chunk.content.find(span.quote, resolved_start + 1) >= 0:
        raise TextModelError(
            "TEXT_MODEL_SOURCE_MISMATCH",
            "模型引文未在指定来源块中唯一出现",
            retryable=False,
        )
    return (
        chunk,
        span.quote.strip(),
        resolved_start,
        resolved_start + len(span.quote),
    )


def _question_prompt(quote: str) -> str:
    return re.sub(r"^\s{0,3}#{1,6}\s*", "", quote).strip()


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
            (
                f"资料正文超过 {settings.max_question_extraction_chars:,} 字抽取上限；"
                "请精简内容或拆分为多份资料后重试"
            ),
        )
        return
    batches = _build_extraction_batches(
        chunks,
        max_chars=settings.question_extraction_batch_chars,
        max_blocks=settings.question_extraction_batch_blocks,
        overlap_blocks=settings.question_extraction_overlap_blocks,
    )
    pairs: list[ExtractedPair] = []
    batch_failures: list[tuple[int, TextModelError]] = []
    for batch_number, batch in enumerate(batches, start=1):
        _set_batch_stage(run_id, batch_number, len(batches))
        try:
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
            if exc.code not in {
                "TEXT_MODEL_INVALID_RESPONSE",
                "TEXT_MODEL_OUTPUT_TRUNCATED",
            }:
                _finish_failure(run_id, exc.code, f"第 {batch_number} 批：{exc}")
                return
            batch_failures.append((batch_number, exc))
        except Exception:
            _finish_failure(
                run_id,
                "TEXT_MODEL_INTERNAL_ERROR",
                f"第 {batch_number} 批发生内部错误，请稍后重试",
            )
            return

    if not pairs and batch_failures:
        first_batch, first_error = batch_failures[0]
        _finish_failure(
            run_id,
            first_error.code,
            f"全部批次均未产生可用结果；第 {first_batch} 批：{first_error}",
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
            created_by_range: dict[
                tuple[UUID, int, int], tuple[Question, QuestionOccurrence]
            ] = {}
            existing_rows = db.execute(
                select(QuestionOccurrence, Question)
                .join(Question, Question.id == QuestionOccurrence.question_id)
                .where(QuestionOccurrence.extraction_run_id == run.id)
            ).all()
            for occurrence, question in existing_rows:
                if occurrence.source_chunk_id is None:
                    continue
                created_by_range[
                    (
                        occurrence.source_chunk_id,
                        occurrence.question_start,
                        occurrence.question_end,
                    )
                ] = (question, occurrence)
            validation_failures = 0
            for pair in pairs:
                try:
                    source_chunk, question_quote, question_start, question_end = (
                        _validated_span(chunk_map, pair.question)
                    )
                    prompt = _question_prompt(question_quote)
                    if not prompt:
                        raise TextModelError(
                            "TEXT_MODEL_SOURCE_MISMATCH",
                            "模型返回了空题目",
                            retryable=False,
                        )
                    validated_answers = [
                        _validated_span(chunk_map, span) for span in pair.answer_spans
                    ]
                except TextModelError:
                    validation_failures += 1
                    continue
                source_key = (source_chunk.id, question_start, question_end)
                answer_texts = [item[1] for item in validated_answers if item[1]]
                answer_text = "\n\n".join(answer_texts) if answer_texts else None
                first_answer = validated_answers[0] if validated_answers else None
                answer_spans = [
                    {
                        "sourceChunkId": str(item[0].id),
                        "blockOrdinal": item[0].ordinal,
                        "start": item[2],
                        "end": item[3],
                        "quote": span.quote,
                    }
                    for item, span in zip(validated_answers, pair.answer_spans, strict=True)
                ]
                existing = created_by_range.get(source_key)
                if existing is not None:
                    existing_question, existing_occurrence = existing
                    if (
                        existing_question.answer is None
                        and answer_text is not None
                        and existing_question.version == 1
                        and existing_occurrence.review_status == "PENDING"
                    ):
                        answer = Answer(
                            owner_id=run.owner_id,
                            content=answer_text,
                            source_type="SOURCE_EXTRACTED",
                        )
                        answer.revisions.append(
                            AnswerRevision(version=1, content=answer_text)
                        )
                        existing_question.answer = answer
                        existing_occurrence.answer_source_chunk_id = first_answer[0].id
                        existing_occurrence.answer_start = first_answer[2]
                        existing_occurrence.answer_end = first_answer[3]
                        existing_occurrence.answer_quote = answer_text
                        existing_occurrence.answer_spans = answer_spans
                        existing_occurrence.confidence = max(
                            existing_occurrence.confidence, pair.confidence
                        )
                    continue
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
                db.add(question)
                db.flush()
                occurrence = QuestionOccurrence(
                    question_id=question.id,
                    extraction_run_id=run.id,
                    asset_version_id=run.asset_version_id,
                    source_chunk_id=source_chunk.id,
                    answer_source_chunk_id=first_answer[0].id if first_answer else None,
                    question_start=question_start,
                    question_end=question_end,
                    answer_start=first_answer[2] if first_answer else None,
                    answer_end=first_answer[3] if first_answer else None,
                    question_quote=pair.question.quote,
                    answer_quote=answer_text,
                    answer_spans=answer_spans,
                    confidence=pair.confidence,
                    review_status="PENDING",
                )
                db.add(occurrence)
                created_by_range[source_key] = (question, occurrence)
            run.question_count = len(created_by_range)
            run.answered_count = sum(
                question.answer is not None for question, _ in created_by_range.values()
            )
            run.missing_count = run.question_count - run.answered_count
            if not created_by_range and validation_failures:
                run.status = "FAILED"
                run.stage = "VALIDATION"
                run.error_code = "TEXT_MODEL_SOURCE_MISMATCH"
                run.error_message = "模型候选均无法回指当前资料原文，请重新整理"
                run.finished_at = datetime.now(timezone.utc)
                db.commit()
                return
            has_partial_failures = bool(batch_failures or validation_failures)
            run.status = "PARTIAL_SUCCESS" if has_partial_failures else "SUCCEEDED"
            run.stage = "PERSISTED"
            if has_partial_failures:
                run.error_code = "EXTRACTION_PARTIAL_RESULT"
                run.error_message = (
                    f"{len(batch_failures)} 个批次格式异常，"
                    f"{validation_failures} 个候选无法回指原文；其余结果已保存"
                )
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
