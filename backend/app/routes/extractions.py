"""资料题答抽取任务接口。"""
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.config import settings
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
from app.modules.question_history import record_question_change
from app.modules.question_identity import question_similarity
from app.modules.question_miss_detection import (
    CoveredQuestionRange,
    MissDetectionBlock,
    find_suspected_questions,
)
from app.schemas.extractions import (
    DuplicateQuestionCandidateView,
    ExtractionAnswerView,
    ExtractionReviewBulkResult,
    ExtractionReviewBulkUpdate,
    ExtractionReviewItemView,
    ExtractionReviewPage,
    ExtractionQualityView,
    ExtractionReviewUpdate,
    MissedQuestionCreate,
    QuestionExtractionRunView,
    SuspectedMissView,
)
from app.workers.celery_app import celery_app

router = APIRouter(tags=["question-extractions"])


@router.get("/assets/{asset_id}/question-extraction-runs/latest", response_model=QuestionExtractionRunView | None)
def get_latest_asset_extraction(asset_id: UUID, db: Session = Depends(get_db), owner_id: UUID = Depends(get_current_user_id)):
    asset = db.scalar(select(Asset).where(Asset.id == asset_id, Asset.owner_id == owner_id, Asset.kind == "LIBRARY"))
    if asset is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    return db.scalar(
        select(QuestionExtractionRun).join(AssetVersion, AssetVersion.id == QuestionExtractionRun.asset_version_id)
        .where(QuestionExtractionRun.asset_id == asset.id, QuestionExtractionRun.owner_id == owner_id, AssetVersion.version == asset.current_version)
        .order_by(QuestionExtractionRun.created_at.desc(), QuestionExtractionRun.id.desc()).limit(1)
    )

_REVIEW_ACTION_STATUS = {
    "CONFIRM": "CONFIRMED",
    "REJECT": "REJECTED",
    "RESET": "PENDING",
}
_DUPLICATE_REVIEW_THRESHOLD = 0.8


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
    duplicate_candidates: list[DuplicateQuestionCandidateView] | None = None,
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
        source_answer=occurrence.answer_quote,
        source_answer_differs=(
            occurrence.answer_quote != (answer.content if answer is not None else None)
        ),
        duplicate_candidates=duplicate_candidates or [],
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


def _collection_question_pool(
    db: Session, owner_id: UUID, collection_id: UUID | None
) -> list[Question]:
    if collection_id is None:
        return []
    has_occurrence = exists(
        select(QuestionOccurrence.id).where(
            QuestionOccurrence.question_id == Question.id
        )
    )
    has_visible_occurrence = exists(
        select(QuestionOccurrence.id).where(
            QuestionOccurrence.question_id == Question.id,
            QuestionOccurrence.review_status != "REJECTED",
        )
    )
    return list(
        db.scalars(
            select(Question)
            .where(
                Question.owner_id == owner_id,
                Question.collection_id == collection_id,
                Question.source_type != "SOURCE_REMOVED",
                or_(~has_occurrence, has_visible_occurrence),
            )
            .options(selectinload(Question.answer))
            .order_by(Question.created_at, Question.id)
        ).all()
    )


def _duplicate_candidate_views(
    question: Question, pool: list[Question]
) -> list[DuplicateQuestionCandidateView]:
    candidates: list[DuplicateQuestionCandidateView] = []
    for other in pool:
        if other.id == question.id:
            continue
        similarity = question_similarity(question.prompt, other.prompt)
        if similarity < _DUPLICATE_REVIEW_THRESHOLD:
            continue
        candidates.append(
            DuplicateQuestionCandidateView(
                question_id=other.id,
                prompt=other.prompt,
                has_answer=other.answer is not None,
                similarity=round(similarity, 4),
            )
        )
    return sorted(
        candidates,
        key=lambda item: (-item.similarity, item.prompt, str(item.question_id)),
    )[:3]


def _suspected_misses(
    chunks: list[TextChunk], occurrences: list[QuestionOccurrence]
) -> list[SuspectedMissView]:
    detected = find_suspected_questions(
        [
            MissDetectionBlock(
                key=chunk.id,
                ordinal=chunk.ordinal,
                content=chunk.content,
                locator=chunk.locator,
            )
            for chunk in chunks
        ],
        [
            CoveredQuestionRange(
                block_key=occurrence.source_chunk_id,
                start=occurrence.question_start,
                end=occurrence.question_end,
            )
            for occurrence in occurrences
            if occurrence.source_chunk_id is not None
        ],
    )
    return [
        SuspectedMissView(
            source_chunk_id=item.block_key,
            source_block_ordinal=item.block_ordinal,
            source_locator=item.locator,
            start=item.start,
            end=item.end,
            quote=item.quote,
            reason=item.reason,
        )
        for item in detected
    ]


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
    if asset.kind != "LIBRARY":
        raise HTTPException(status_code=409, detail="简历资料不能用于题目抽取")
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


@router.post(
    "/question-extraction-runs/{run_id}/retry",
    response_model=QuestionExtractionRunView,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_question_extraction_run(
    run_id: UUID,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
    idempotency_key: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=200
    ),
) -> QuestionExtractionRun:
    digest = request_hash({"runId": str(run_id)})
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
    replay_id = replay_resource_id(
        db, owner_id, "question-extraction:retry", idempotency_key, digest
    )
    if replay_id is not None:
        return _owned_run(db, replay_id, owner_id)
    if run.status not in {"FAILED", "PARTIAL_SUCCESS", "WAITING_FOR_CONFIGURATION"}:
        raise HTTPException(status_code=409, detail="当前抽取任务状态不能重试")
    if run.retry_count >= settings.question_extraction_retry_limit:
        raise HTTPException(
            status_code=409,
            detail=f"已达到最多 {settings.question_extraction_retry_limit} 次重试上限",
        )

    run.retry_count += 1
    run.status = "QUEUED"
    run.stage = "QUEUED"
    run.celery_task_id = None
    run.error_code = None
    run.error_message = None
    run.started_at = None
    run.finished_at = None
    try:
        remember_request(
            db,
            owner_id,
            "question-extraction:retry",
            idempotency_key,
            digest,
            run.id,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        replay_id = replay_resource_id(
            db, owner_id, "question-extraction:retry", idempotency_key, digest
        )
        if replay_id is not None:
            return _owned_run(db, replay_id, owner_id)
        raise HTTPException(status_code=409, detail="重复的题目抽取重试请求") from exc
    _dispatch(db, run)
    db.refresh(run)
    return run


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
    asset = db.get(Asset, run.asset_id)
    question_pool = _collection_question_pool(
        db, owner_id, asset.collection_id if asset is not None else None
    )
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
    suspected_misses = _suspected_misses(chunks, all_occurrences)
    active_occurrences = [item for item in all_occurrences if item.review_status != "REJECTED"]
    source_answered = sum(bool(item.answer_quote and item.answer_quote.strip()) for item in active_occurrences)
    return ExtractionReviewPage(
        items=[
            _review_item(
                *row,
                duplicate_candidates=_duplicate_candidate_views(row[1], question_pool),
            )
            for row in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
        pending_count=status_counts.get("PENDING", 0),
        confirmed_count=status_counts.get("CONFIRMED", 0),
        rejected_count=status_counts.get("REJECTED", 0),
        suspected_misses=suspected_misses,
        quality=ExtractionQualityView(
            occurrence_count=len(all_occurrences),
            unique_question_count=len({item.question_id for item in all_occurrences}),
            active_question_count=len({item.question_id for item in active_occurrences}),
            pending_count=status_counts.get("PENDING", 0), confirmed_count=status_counts.get("CONFIRMED", 0),
            rejected_count=status_counts.get("REJECTED", 0), source_answered_count=source_answered,
            source_missing_count=len(active_occurrences) - source_answered,
            suspected_miss_count=len(suspected_misses), suspected_misses_truncated=len(suspected_misses) >= 100,
            is_current_version=asset is not None and db.scalar(
                select(AssetVersion.version).where(AssetVersion.id == run.asset_version_id)
            ) == asset.current_version,
            diagnostics=run.diagnostics,
        ),
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
    previous_status = occurrence.review_status
    occurrence.review_status = _REVIEW_ACTION_STATUS[payload.action]
    if previous_status != occurrence.review_status:
        record_question_change(
            db,
            question_id=question.id,
            owner_id=owner_id,
            field="REVIEW_STATUS",
            action="UPDATED",
            previous_value=previous_status,
            new_value=occurrence.review_status,
            occurrence_id=occurrence.id,
        )
    db.commit()
    db.refresh(occurrence)
    question_pool = _collection_question_pool(db, owner_id, question.collection_id)
    return _review_item(
        occurrence,
        question,
        answer,
        source_chunk,
        duplicate_candidates=_duplicate_candidate_views(question, question_pool),
    )


@router.put(
    "/question-extraction-runs/{run_id}/review-items",
    response_model=ExtractionReviewBulkResult,
)
def update_extraction_review_items(
    run_id: UUID,
    payload: ExtractionReviewBulkUpdate,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> ExtractionReviewBulkResult:
    run = _owned_run(db, run_id, owner_id)
    occurrence_ids = [item.occurrence_id for item in payload.items]
    rows = db.execute(
        select(QuestionOccurrence, Question, Answer, TextChunk)
        .join(Question, Question.id == QuestionOccurrence.question_id)
        .outerjoin(Answer, Answer.question_id == Question.id)
        .outerjoin(TextChunk, TextChunk.id == QuestionOccurrence.source_chunk_id)
        .where(
            QuestionOccurrence.extraction_run_id == run.id,
            QuestionOccurrence.id.in_(occurrence_ids),
        )
        .with_for_update(of=QuestionOccurrence)
    ).all()
    rows_by_id = {row[0].id: row for row in rows}
    if len(rows_by_id) != len(occurrence_ids):
        raise HTTPException(status_code=404, detail="部分抽取复核项不存在")
    for item in payload.items:
        occurrence = rows_by_id[item.occurrence_id][0]
        if occurrence.review_status != item.expected_status:
            raise HTTPException(status_code=409, detail="部分复核状态已变化，请刷新后重试")
    for item in payload.items:
        occurrence, question, _, _ = rows_by_id[item.occurrence_id]
        previous_status = occurrence.review_status
        occurrence.review_status = _REVIEW_ACTION_STATUS[item.action]
        if previous_status != occurrence.review_status:
            record_question_change(
                db,
                question_id=question.id,
                owner_id=owner_id,
                field="REVIEW_STATUS",
                action="UPDATED",
                previous_value=previous_status,
                new_value=occurrence.review_status,
                occurrence_id=occurrence.id,
            )
    db.commit()
    pools: dict[UUID | None, list[Question]] = {}
    for _, question, _, _ in rows:
        if question.collection_id not in pools:
            pools[question.collection_id] = _collection_question_pool(
                db, owner_id, question.collection_id
            )
    return ExtractionReviewBulkResult(
        items=[
            _review_item(
                *rows_by_id[item.occurrence_id],
                duplicate_candidates=_duplicate_candidate_views(
                    rows_by_id[item.occurrence_id][1],
                    pools[rows_by_id[item.occurrence_id][1].collection_id],
                ),
            )
            for item in payload.items
        ],
        updated_count=len(payload.items),
    )


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
    question_pool = _collection_question_pool(db, owner_id, question.collection_id)
    return _review_item(
        occurrence,
        question,
        None,
        source_chunk,
        duplicate_candidates=_duplicate_candidate_views(question, question_pool),
    )
