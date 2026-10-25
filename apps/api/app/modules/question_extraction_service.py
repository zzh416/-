"""将模型候选严格校验后写成可追溯题目。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.adapters.glm_text import (
    ExtractedPair,
    ExtractedSpan,
    ExtractionBlock,
    TextModelError,
    TextModelNotConfigured,
    extract_questions_with_glm,
)
from app.adapters.text_provider import get_text_model_config
from app.config import settings
from app.db import SessionLocal
from app.models import (
    Answer,
    AnswerRevision,
    Asset,
    Question,
    QuestionChange,
    QuestionExtractionRun,
    QuestionOccurrence,
    TextChunk,
)
from app.modules.extraction_candidates import prefer_complete_source_answers
from app.modules.question_identity import question_identity

Extractor = Callable[..., list[ExtractedPair]]


def _save_diagnostics(run_id: UUID, diagnostics: dict) -> None:
    with SessionLocal() as db:
        run = db.get(QuestionExtractionRun, run_id)
        if run is not None:
            run.diagnostics = dict(diagnostics)
            db.commit()


@dataclass(frozen=True)
class LoadedChunk:
    id: UUID
    ordinal: int
    content: str


def _starts_explicit_question(content: str) -> bool:
    """Use only a clear opening line as a batch boundary, never an inner answer question."""
    if not content.strip():
        return False
    line = content.lstrip().splitlines()[0].replace("**", "").strip()
    line = re.sub(r"^#{1,6}\s*", "", line)
    line = re.sub(r"^[-+*>▶►▸•·⭐]+\s*", "", line)
    line = re.sub(r"^\d+(?:\.\d+)*[.)、]\s*", "", line)
    line = re.sub(r"^面试官\s*[:：]\s*", "", line)
    if re.match(
        r"^(?:答案|参考答案|答[:：]|解析|例如|比如|示例|步骤|首先|其次|然后|判断|检查|将|把)",
        line,
    ):
        return False
    line = re.split(r"(?:参考答案|答案|答)\s*[:：]", line, maxsplit=1)[0].strip()
    if not line or len(line) > 240:
        return False
    request = re.match(
        r"^(?:请)?(?:先)?(?:介绍(?:一下)?|解释(?:一下)?|说说|说一下|谈谈|讲讲|聊聊|描述|简述|分析|比较)",
        line,
    )
    return bool(request) or bool(
        line.endswith(("?", "？"))
        and re.search(r"为什么|为何|怎么|如何|什么|哪些|哪个|是否|能否|几种|区别|作用|原理|流程|优势|吗|呢", line)
    )


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
    covered_until = 0
    while start < len(chunks):
        end = start
        char_count = 0
        while end < len(chunks) and end - start < max_blocks:
            next_size = len(chunks[end].content)
            if end > start and char_count + next_size > max_chars:
                break
            char_count += next_size
            end += 1
        if end < len(chunks) and not _starts_explicit_question(chunks[end].content):
            # Keep the last question with its answer in the next batch when possible.
            # Never backtrack into overlap-only material or increase the hard limits.
            for boundary in range(end - 1, max(start, covered_until), -1):
                if _starts_explicit_question(chunks[boundary].content):
                    end = boundary
                    break
        if end <= covered_until:
            # A large next block may leave room only for already-sent overlap.
            start = covered_until
            continue
        batches.append(chunks[start:end])
        covered_until = end
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


def is_answer_label_only(quote: str) -> bool:
    """A standalone answer heading carries no answer; preserve all other prose."""
    normalized = re.sub(r"\s+", "", quote).replace("**", "").replace("__", "")
    return re.fullmatch(r"(?:参考答案|答案|答)[:：]?", normalized) is not None


def question_prompt_from_quote(quote: str, *, answer_offset: int | None = None) -> str:
    candidate = quote
    if answer_offset is not None and 0 < answer_offset < len(candidate):
        candidate = candidate[:answer_offset].rstrip()
        emphasized_questions: list[str] = []
        for segment in re.findall(r"\*\*(.+?)\*\*", candidate):
            markers = list(re.finditer(r"[?？]", segment))
            if markers:
                emphasized_questions.append(segment[: markers[-1].end()].strip())
        if emphasized_questions:
            candidate = " ".join(emphasized_questions)
        else:
            markers = list(re.finditer(r"[?？]", candidate))
            if markers:
                candidate = candidate[: markers[-1].end()]
        candidate = candidate.replace("**", "")
    candidate = re.sub(r"^\s{0,3}#{1,6}\s*", "", candidate).strip()
    trailing_label = re.search(
        r"\s*(?:\*\*|__)?(?:参考答案|答案|答)\s*[:：](?:\*\*|__)?\s*$", candidate
    )
    if trailing_label and re.search(r"[?？]", candidate[: trailing_label.start()]):
        candidate = candidate[: trailing_label.start()].rstrip()
    return candidate


_QUESTION_CUE = re.compile(
    r"(?:为什么|为何|怎么|如何|什么|哪些|哪个|是否|能否|几种|区别|作用|原理)",
    re.IGNORECASE,
)
_QUESTION_REQUEST = re.compile(
    r"^(?:请|介绍|解释|说说|说一下|谈谈|讲讲|聊聊|描述|简述|分析|比较)",
    re.IGNORECASE,
)
_NUMBERED_TOPIC = re.compile(r"^(?:\*{0,2})?\d+(?:\.\d+)*[.、]\s*\S")
_NON_QUESTION_RESPONSE = re.compile(
    r"^(?:答案|答[:：]|解析|这道题|暂时不会|不会|不知道|待补充)",
    re.IGNORECASE,
)
_DECLARATIVE_MARKER = re.compile(
    r"(?:就是|指的是|主要是|就会|意味着|要略微|可以继续|负责|用于)",
    re.IGNORECASE,
)


def initial_review_status(question_quote: str) -> str:
    """Conservatively hide clear answer prose while keeping question-style titles reviewable."""

    prompt = question_prompt_from_quote(question_quote)
    if not prompt or _NON_QUESTION_RESPONSE.search(prompt):
        return "REJECTED"
    if "?" in prompt or "？" in prompt:
        return "PENDING"
    if _QUESTION_CUE.search(prompt) or _QUESTION_REQUEST.search(prompt):
        return "PENDING"
    if re.match(r"^\s{0,3}#{1,6}\s*", question_quote) or _NUMBERED_TOPIC.search(prompt):
        return "PENDING"
    if re.search(r"[。.;；:：]\s*$", prompt) or _DECLARATIVE_MARKER.search(prompt):
        return "REJECTED"
    return "PENDING"


def process_question_extraction_run(
    run_id: UUID,
    *,
    extractor: Extractor = extract_questions_with_glm,
) -> None:
    model_config = get_text_model_config()
    with SessionLocal() as db:
        run = db.scalar(
            select(QuestionExtractionRun)
            .where(QuestionExtractionRun.id == run_id)
            .with_for_update()
        )
        if run is None or run.status in {"SUCCEEDED", "PROCESSING"}:
            return
        run.status = "PROCESSING"
        run.diagnostics = None
        run.stage = "MODEL"
        run.model = model_config.model
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
    diagnostics = {"planned_batch_count": len(batches), "completed_batch_count": 0,
        "failed_batch_count": 0, "model_candidate_count": 0,
        "invalid_source_count": None, "duplicate_range_count": None}
    _save_diagnostics(run_id, diagnostics)
    for batch_number, batch in enumerate(batches, start=1):
        _set_batch_stage(run_id, batch_number, len(batches))
        try:
            pairs.extend(
                extractor(
                    [
                        ExtractionBlock(ordinal=chunk.ordinal, content=chunk.content)
                        for chunk in batch
                    ],
                    api_key=model_config.api_key,
                    base_url=model_config.base_url,
                    model=model_config.model,
                    provider=model_config.provider,
                    max_output_tokens=settings.question_extraction_max_output_tokens,
                )
            )
            diagnostics["completed_batch_count"] += 1
        except TextModelNotConfigured as exc:
            diagnostics["failed_batch_count"] += 1
            _finish_failure(
                run_id,
                "TEXT_MODEL_NOT_CONFIGURED",
                f"{exc}，配置后请重新发起题目抽取",
                status="WAITING_FOR_CONFIGURATION",
            )
            return
        except TextModelError as exc:
            diagnostics["failed_batch_count"] += 1
            if exc.code not in {
                "TEXT_MODEL_INVALID_RESPONSE",
                "TEXT_MODEL_OUTPUT_TRUNCATED",
            }:
                _finish_failure(run_id, exc.code, f"第 {batch_number} 批：{exc}")
                return
            batch_failures.append((batch_number, exc))
        except Exception:
            diagnostics["failed_batch_count"] += 1
            _finish_failure(
                run_id,
                "TEXT_MODEL_INTERNAL_ERROR",
                f"第 {batch_number} 批发生内部错误，请稍后重试",
            )
            return
        finally:
            diagnostics["model_candidate_count"] = len(pairs)
            _save_diagnostics(run_id, diagnostics)

    if not pairs and batch_failures:
        first_batch, first_error = batch_failures[0]
        _finish_failure(
            run_id,
            first_error.code,
            f"全部批次均未产生可用结果；第 {first_batch} 批：{first_error}",
        )
        return

    chunk_map = {chunk.ordinal: chunk for chunk in chunks}
    pairs = prefer_complete_source_answers(
        pairs, {chunk.ordinal: chunk.content for chunk in chunks}
    )
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
            reusable_questions = list(
                db.scalars(
                    select(Question)
                    .where(
                        Question.owner_id == run.owner_id,
                        Question.collection_id == asset.collection_id,
                        Question.source_type == "SOURCE_EXTRACTED",
                    )
                    .options(selectinload(Question.answer))
                    .order_by(Question.created_at, Question.id)
                    .with_for_update()
                ).all()
            )
            reusable_by_identity: dict[str, Question] = {}
            for reusable_question in reusable_questions:
                reusable_by_identity.setdefault(
                    question_identity(reusable_question.prompt), reusable_question
                )
            answer_edited_question_ids = set(
                db.scalars(
                    select(QuestionChange.question_id).where(
                        QuestionChange.owner_id == run.owner_id,
                        QuestionChange.field == "ANSWER",
                    )
                ).all()
            )
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
            duplicate_ranges = 0
            for pair in pairs:
                try:
                    source_chunk, question_quote, question_start, question_end = (
                        _validated_span(chunk_map, pair.question)
                    )
                    validated_answers = [
                        _validated_span(chunk_map, span) for span in pair.answer_spans
                    ]
                    overlapping_answer_offsets = [
                        answer_start - question_start
                        for answer_chunk, _, answer_start, _ in validated_answers
                        if answer_chunk.id == source_chunk.id
                        and question_start < answer_start < question_end
                    ]
                    prompt = question_prompt_from_quote(
                        pair.question.quote,
                        answer_offset=(
                            min(overlapping_answer_offsets)
                            if overlapping_answer_offsets
                            else None
                        ),
                    )
                    if not prompt:
                        raise TextModelError(
                            "TEXT_MODEL_SOURCE_MISMATCH",
                            "模型返回了空题目",
                            retryable=False,
                        )
                except TextModelError:
                    validation_failures += 1
                    continue
                source_key = (source_chunk.id, question_start, question_end)
                answer_sources = [
                    (item, span)
                    for item, span in zip(validated_answers, pair.answer_spans, strict=True)
                    if item[1] and not is_answer_label_only(item[1])
                ]
                answer_text = "\n\n".join(item[1] for item, _ in answer_sources) or None
                first_answer = answer_sources[0][0] if answer_sources else None
                answer_spans = [
                    {
                        "sourceChunkId": str(item[0].id),
                        "blockOrdinal": item[0].ordinal,
                        "start": item[2],
                        "end": item[3],
                        "quote": span.quote,
                    }
                    for item, span in answer_sources
                ]
                existing = created_by_range.get(source_key)
                if existing is not None:
                    duplicate_ranges += 1
                    existing_question, existing_occurrence = existing
                    if (
                        existing_question.answer is None
                        and answer_text is not None
                        and existing_question.version == 1
                        and existing_occurrence.review_status == "PENDING"
                        and existing_question.id not in answer_edited_question_ids
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
                identity = question_identity(prompt)
                question = reusable_by_identity.get(identity)
                if question is None:
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
                        answer.revisions.append(
                            AnswerRevision(version=1, content=answer_text)
                        )
                        question.answer = answer
                    db.add(question)
                    db.flush()
                    reusable_by_identity[identity] = question
                elif (
                    question.answer is None
                    and answer_text is not None
                    and question.version == 1
                    and question.id not in answer_edited_question_ids
                ):
                    answer = Answer(
                        owner_id=run.owner_id,
                        content=answer_text,
                        source_type="SOURCE_EXTRACTED",
                    )
                    answer.revisions.append(
                        AnswerRevision(version=1, content=answer_text)
                    )
                    question.answer = answer
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
                    review_status=initial_review_status(pair.question.quote),
                )
                db.add(occurrence)
                created_by_range[source_key] = (question, occurrence)
            diagnostics["invalid_source_count"] = validation_failures
            diagnostics["duplicate_range_count"] = duplicate_ranges
            run.diagnostics = dict(diagnostics)
            run.question_count = len(created_by_range)
            run.answered_count = sum(
                occurrence.answer_quote is not None
                for _, occurrence in created_by_range.values()
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
