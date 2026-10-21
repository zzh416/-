"""资料题答抽取任务接口。"""
import re
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.identity import get_current_user_id
from app.idempotency import remember_request, replay_resource_id, request_hash
from app.models import (
    Answer,
    Asset,
    AssetVersion,
    Question,
    QuestionExtractionRun,
    QuestionOccurrence,
    TextChunk,
)
from app.schemas.extractions import (
    ExtractionAnswerView,
    ExtractionReviewItemView,
    ExtractionReviewPage,
    ExtractionReviewUpdate,
    MissedQuestionCreate,
    QuestionExtractionRunView,
    SuspectedMissView,
)
from app.workers.celery_app import celery_app

router = APIRouter(tags=["question-extractions"])

_QUESTION_PATTERN = re.compile(
    r"(?:什么是|为什么|为何|如何|怎么|怎样|说说|介绍|区别|原理|作用|流程|是否|哪些|哪种|能否)"
)
_HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+\S")


def _owned_run(db: Session, run_id: UUID, owner_id: UUID) -> QuestionExtractionRun:
    run = db.scalar(
        select(QuestionExtractionRun).where(
            QuestionExtractionRun.id == run_id,
            QuestionExtractionRun.owner_id == owner_id,
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="题目抽取任务不存在")
    return run


def _dispatch(db: Session, run: QuestionExtractionRun) -> None:
    try:
        result = celery_app.send_task(
            "app.workers.tasks.process_question_extraction",
            args=[str(run.id)],
        )
        task_id = getattr(result, "id", None)
        if isinstance(task_id, str):
            run.celery_task_id = task_id
            db.commit()
    except Exception:
        run.status = "FAILED"
        run.stage = "QUEUE"
        run.error_code = "QUEUE_UNAVAILABLE"
        run.error_message = "后台抽取服务暂时不可用，请稍后重试"
        db.commit()


def _review_item(
    occurrence: QuestionOccurrence,
    question: Question,
    answer: Answer | None,
    source_chunk: TextChunk | None,
) -> ExtractionReviewItemView:
    return ExtractionReviewItemView(
        occurrence_id=occurrence.id,
        question_id=question.id,
        question_version=question.version,
        prompt=question.prompt,
        answer=(
            ExtractionAnswerView(
                content=answer.content,
                source_type=answer.source_type,
                version=answer.version,
            )
            if answer is not None
            else None
        ),
        source_chunk_id=occurrence.source_chunk_id,
        source_block_ordinal=source_chunk.ordinal if source_chunk is not None else None,
        source_locator=source_chunk.locator if source_chunk is not None else {},
        question_start=occurrence.question_start,
        question_end=occurrence.question_end,
        question_quote=occurrence.question_quote,
        answer_spans=occurrence.answer_spans,
        confidence=occurrence.confidence,
        review_status=occurrence.review_status,
        created_at=occurrence.created_at,
    )


def _suspected_misses(
    chunks: list[TextChunk], occurrences: list[QuestionOccurrence]
) -> list[SuspectedMissView]:
    covered: dict[UUID, list[tuple[int, int]]] = {}
    for occurrence in occurrences:
        if occurrence.source_chunk_id is not None:
            covered.setdefault(occurrence.source_chunk_id, []).append(
                (occurrence.question_start, occurrence.question_end)
            )

    misses: list[SuspectedMissView] = []
    for chunk in chunks:
        offset = 0
        for raw_line in chunk.content.splitlines(keepends=True):
            line = raw_line.rstrip("\r\n")
            stripped = line.strip()
            start = offset + len(line) - len(line.lstrip())
            end = start + len(stripped)
            offset += len(raw_line)
            if not stripped or end <= start:
                continue
            reason = None
            if "？" in stripped or "?" in stripped:
                reason = "QUESTION_MARK"
            elif _HEADING_PATTERN.match(line) and len(stripped) <= 160:
                reason = "HEADING"
            elif len(stripped) <= 160 and _QUESTION_PATTERN.search(stripped):
                reason = "QUESTION_PATTERN"
            if reason is None:
                continue
            if any(
                start < covered_end and end > covered_start
                for covered_start, covered_end in covered.get(chunk.id, [])
            ):
                continue
            misses.append(
                SuspectedMissView(
                    source_chunk_id=chunk.id,
                    source_block_ordinal=chunk.ordinal,
                    source_locator=chunk.locator,
                    start=start,
                    end=end,
                    quote=stripped,
                    reason=reason,
                )
            )
            if len(misses) >= 100:
                return misses
    return misses


@router.post(
    "/assets/{asset_id}/question-extraction-runs",
    response_model=QuestionExtractionRunView,
    status_code=status.HTTP_201_CREATED,
)
def create_question_extraction_run(
    asset_id: UUID,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
    idempotency_key: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=200
    ),
) -> QuestionExtractionRun:
    asset = db.scalar(
        select(Asset).where(Asset.id == asset_id, Asset.owner_id == owner_id).with_for_update()
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    if asset.status not in {"READY", "PARTIAL_SUCCESS"} or asset.current_version < 1:
        raise HTTPException(status_code=409, detail="资料尚未解析完成，不能抽取题目")
    version = db.scalar(
        select(AssetVersion).where(
            AssetVersion.asset_id == asset.id,
            AssetVersion.version == asset.current_version,
        )
    )
    if version is None:
        raise HTTPException(status_code=409, detail="资料当前版本不存在")

    digest = request_hash(
        {"assetId": str(asset.id), "assetVersionId": str(version.id)}
    )
    replay_id = replay_resource_id(
        db, owner_id, "question-extraction:create", idempotency_key, digest
    )
    if replay_id is not None:
        return _owned_run(db, replay_id, owner_id)

    existing_run = db.scalar(
        select(QuestionExtractionRun)
        .where(
            QuestionExtractionRun.owner_id == owner_id,
            QuestionExtractionRun.asset_version_id == version.id,
            QuestionExtractionRun.status.in_(
                {"QUEUED", "PROCESSING", "SUCCEEDED", "PARTIAL_SUCCESS"}
            ),
        )
        .order_by(QuestionExtractionRun.created_at.desc())
    )
    if existing_run is not None:
        remember_request(
            db,
            owner_id,
            "question-extraction:create",
            idempotency_key,
            digest,
            existing_run.id,
        )
        db.commit()
        return existing_run

    run = QuestionExtractionRun(
        owner_id=owner_id,
        asset_id=asset.id,
        asset_version_id=version.id,
        model=None,
    )
    db.add(run)
    try:
        db.flush()
        remember_request(
            db,
            owner_id,
            "question-extraction:create",
            idempotency_key,
            digest,
            run.id,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        replay_id = replay_resource_id(
            db, owner_id, "question-extraction:create", idempotency_key, digest
        )
        if replay_id is not None:
            return _owned_run(db, replay_id, owner_id)
        raise HTTPException(status_code=409, detail="重复的题目抽取请求") from exc
    _dispatch(db, run)
    db.refresh(run)
    return run


@router.get(
    "/question-extraction-runs/{run_id}",
    response_model=QuestionExtractionRunView,
)
def get_question_extraction_run(
    run_id: UUID,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> QuestionExtractionRun:
    return _owned_run(db, run_id, owner_id)


@router.get(
    "/question-extraction-runs/{run_id}/review-items",
    response_model=ExtractionReviewPage,
)
def list_extraction_review_items(
    run_id: UUID,
    review_status: str | None = Query(default=None, alias="reviewStatus"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100, alias="pageSize"),
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> ExtractionReviewPage:
    run = _owned_run(db, run_id, owner_id)
    if review_status not in {None, "PENDING", "CONFIRMED", "REJECTED"}:
        raise HTTPException(status_code=422, detail="复核状态无效")

    base_filters = [QuestionOccurrence.extraction_run_id == run.id]
    status_counts = dict(
        db.execute(
            select(QuestionOccurrence.review_status, func.count(QuestionOccurrence.id))
            .where(*base_filters)
            .group_by(QuestionOccurrence.review_status)
        ).all()
    )
    page_filters = list(base_filters)
    if review_status is not None:
        page_filters.append(QuestionOccurrence.review_status == review_status)
    total = db.scalar(
        select(func.count(QuestionOccurrence.id)).where(*page_filters)
    ) or 0
    rows = db.execute(
        select(QuestionOccurrence, Question, Answer, TextChunk)
        .join(Question, Question.id == QuestionOccurrence.question_id)
        .outerjoin(Answer, Answer.question_id == Question.id)
        .outerjoin(TextChunk, TextChunk.id == QuestionOccurrence.source_chunk_id)
        .where(*page_filters)
        .order_by(TextChunk.ordinal.asc().nulls_last(), QuestionOccurrence.created_at.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    all_occurrences = list(
        db.scalars(
            select(QuestionOccurrence).where(
                QuestionOccurrence.extraction_run_id == run.id
            )
        ).all()
    )
    chunks = list(
        db.scalars(
            select(TextChunk)
            .where(TextChunk.asset_version_id == run.asset_version_id)
            .order_by(TextChunk.ordinal)
        ).all()
    )
    return ExtractionReviewPage(
        items=[_review_item(*row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        pending_count=status_counts.get("PENDING", 0),
        confirmed_count=status_counts.get("CONFIRMED", 0),
        rejected_count=status_counts.get("REJECTED", 0),
        suspected_misses=_suspected_misses(chunks, all_occurrences),
    )


@router.put(
    "/question-extraction-items/{occurrence_id}/review",
    response_model=ExtractionReviewItemView,
)
def update_extraction_review_item(
    occurrence_id: UUID,
    payload: ExtractionReviewUpdate,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> ExtractionReviewItemView:
    row = db.execute(
        select(QuestionOccurrence, Question, Answer, TextChunk)
        .join(QuestionExtractionRun, QuestionExtractionRun.id == QuestionOccurrence.extraction_run_id)
        .join(Question, Question.id == QuestionOccurrence.question_id)
        .outerjoin(Answer, Answer.question_id == Question.id)
        .outerjoin(TextChunk, TextChunk.id == QuestionOccurrence.source_chunk_id)
        .where(
            QuestionOccurrence.id == occurrence_id,
            QuestionExtractionRun.owner_id == owner_id,
        )
        .with_for_update(of=QuestionOccurrence)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="抽取复核项不存在")
    occurrence, question, answer, source_chunk = row
    if occurrence.review_status != payload.expected_status:
        raise HTTPException(status_code=409, detail="复核状态已变化，请刷新后重试")
    occurrence.review_status = {
        "CONFIRM": "CONFIRMED",
        "REJECT": "REJECTED",
        "RESET": "PENDING",
    }[payload.action]
    db.commit()
    db.refresh(occurrence)
    return _review_item(occurrence, question, answer, source_chunk)


@router.post(
    "/question-extraction-runs/{run_id}/missed-items",
    response_model=ExtractionReviewItemView,
    status_code=status.HTTP_201_CREATED,
)
def create_missed_question(
    run_id: UUID,
    payload: MissedQuestionCreate,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> ExtractionReviewItemView:
    run = db.scalar(
        select(QuestionExtractionRun)
        .where(
            QuestionExtractionRun.id == run_id,
            QuestionExtractionRun.owner_id == owner_id,
        )
        .with_for_update()
    )
    if run is None:
        raise HTTPException(status_code=404, detail="题目抽取任务不存在")
    source_chunk = db.scalar(
        select(TextChunk).where(
            TextChunk.id == payload.source_chunk_id,
            TextChunk.asset_version_id == run.asset_version_id,
        )
    )
    if source_chunk is None:
        raise HTTPException(status_code=404, detail="来源文本块不存在")
    if (
        payload.end > len(source_chunk.content)
        or source_chunk.content[payload.start : payload.end] != payload.quote
    ):
        raise HTTPException(status_code=422, detail="所选文字无法回指当前资料版本")
    existing = db.scalar(
        select(QuestionOccurrence).where(
            QuestionOccurrence.extraction_run_id == run.id,
            QuestionOccurrence.source_chunk_id == source_chunk.id,
            QuestionOccurrence.question_start == payload.start,
            QuestionOccurrence.question_end == payload.end,
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="该原文范围已经建立题目")

    asset = db.get(Asset, run.asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="来源资料不存在")
    question = Question(
        owner_id=owner_id,
        collection_id=asset.collection_id,
        source_chunk_id=source_chunk.id,
        source_type="SOURCE",
        prompt=payload.prompt.strip(),
    )
    db.add(question)
    db.flush()
    occurrence = QuestionOccurrence(
        question_id=question.id,
        extraction_run_id=run.id,
        asset_version_id=run.asset_version_id,
        source_chunk_id=source_chunk.id,
        question_start=payload.start,
        question_end=payload.end,
        question_quote=payload.quote,
        answer_spans=[],
        confidence=1,
        review_status="CONFIRMED",
    )
    db.add(occurrence)
    run.question_count += 1
    run.missing_count += 1
    db.commit()
    db.refresh(occurrence)
    return _review_item(occurrence, question, None, source_chunk)
