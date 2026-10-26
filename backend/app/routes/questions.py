"""问题、答案、备注与关键词检索。"""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.identity import get_current_user_id
from app.idempotency import remember_request, replay_resource_id, request_hash
from app.models import (
    Answer,
    AIAnswerRun,
    AnswerRevision,
    Asset,
    AssetVersion,
    Collection,
    Question,
    QuestionChange,
    QuestionNote,
    QuestionOccurrence,
    TextChunk,
)
from app.modules.question_history import record_question_change
from app.modules.ai_answer_service import attach_answers
from app.modules.source_statistics import question_source_statistics
from app.schemas.sources import SourceStatistics
from app.schemas.questions import (
    AnswerView,
    NoteView,
    QuestionCreate,
    QuestionPage,
    QuestionHistoryPage,
    QuestionHistoryRestore,
    QuestionUpdate,
    QuestionView,
    VersionedContentUpdate,
)

router = APIRouter(prefix="/questions", tags=["questions"])


def _question_query(owner_id: UUID):
    return (
        select(Question)
        .where(Question.owner_id == owner_id)
        .options(selectinload(Question.answer), selectinload(Question.note))
    )


def _get_question(db: Session, question_id: UUID, owner_id: UUID, lock: bool = False) -> Question:
    statement = _question_query(owner_id).where(Question.id == question_id)
    if lock:
        statement = statement.with_for_update()
    question = db.scalar(statement)
    if question is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    attach_answers(db, [question])
    return question


def _next_answer_version(
    db: Session,
    question_id: UUID,
    current_version: int = 0,
) -> int:
    """Keep answer versions increasing even after the answer row is deleted."""
    latest_history_version = db.scalar(
        select(func.max(QuestionChange.entity_version)).where(
            QuestionChange.question_id == question_id,
            QuestionChange.field == "ANSWER",
        )
    )
    return max(current_version, latest_history_version or 0) + 1


@router.post("", response_model=QuestionView, status_code=status.HTTP_201_CREATED)
def create_question(
    payload: QuestionCreate,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=8, max_length=200
    ),
) -> Question:
    digest = request_hash(payload)
    replay_id = replay_resource_id(db, owner_id, "question:create", idempotency_key, digest)
    if replay_id is not None:
        existing = db.scalar(_question_query(owner_id).where(Question.id == replay_id))
        if existing is not None:
            return existing
        raise HTTPException(status_code=409, detail="该幂等请求对应的资源已删除")

    resolved_collection_id = payload.collection_id
    if resolved_collection_id is not None:
        collection = db.scalar(
            select(Collection).where(
                Collection.id == resolved_collection_id, Collection.owner_id == owner_id
            )
        )
        if collection is None:
            raise HTTPException(status_code=404, detail="资料库不存在")

    source_type = "USER_WRITTEN"
    if payload.source_chunk_id is not None:
        chunk = db.scalar(
            select(TextChunk)
            .join(TextChunk.asset_version)
            .join(AssetVersion.asset)
            .where(TextChunk.id == payload.source_chunk_id, Asset.owner_id == owner_id)
        )
        if chunk is None:
            raise HTTPException(status_code=404, detail="来源文本块不存在")
        source_collection_id = chunk.asset_version.asset.collection_id
        if resolved_collection_id is None:
            resolved_collection_id = source_collection_id
        elif resolved_collection_id != source_collection_id:
            raise HTTPException(status_code=422, detail="题目和来源文本块必须属于同一个资料库")
        source_type = "SOURCE"

    question = Question(
        owner_id=owner_id,
        collection_id=resolved_collection_id,
        source_chunk_id=payload.source_chunk_id,
        source_type=source_type,
        prompt=payload.prompt,
    )
    if payload.answer is not None and payload.answer.strip():
        answer = Answer(owner_id=owner_id, content=payload.answer, source_type="USER_WRITTEN")
        answer.revisions.append(AnswerRevision(version=1, content=payload.answer))
        question.answer = answer
    db.add(question)
    try:
        db.flush()
        remember_request(db, owner_id, "question:create", idempotency_key, digest, question.id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        replay_id = replay_resource_id(db, owner_id, "question:create", idempotency_key, digest)
        if replay_id is not None:
            existing = db.scalar(_question_query(owner_id).where(Question.id == replay_id))
            if existing is not None:
                return existing
        raise HTTPException(status_code=409, detail="重复的题目创建请求") from exc
    return _get_question(db, question.id, owner_id)


@router.get("", response_model=QuestionPage)
def list_questions(
    q: str | None = None,
    collection_id: UUID | None = Query(default=None, alias="collectionId"),
    answer_state: Literal["ANY", "ANSWERED", "MISSING"] = Query(
        default="ANY", alias="answerState"
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> QuestionPage:
    has_occurrence = exists(
        select(QuestionOccurrence.id).where(QuestionOccurrence.question_id == Question.id)
    )
    has_visible_occurrence = exists(
        select(QuestionOccurrence.id).where(
            QuestionOccurrence.question_id == Question.id,
            QuestionOccurrence.review_status != "REJECTED",
        )
    )
    filters = [
        Question.owner_id == owner_id,
        or_(~has_occurrence, has_visible_occurrence),
    ]
    if collection_id is not None:
        filters.append(Question.collection_id == collection_id)
    ai_questions = list(db.scalars(select(Question).where(*filters, Question.answer == None,
        exists(select(AIAnswerRun.id).where(AIAnswerRun.question_id == Question.id,
            AIAnswerRun.status == "SUCCEEDED", AIAnswerRun.review_status == "CONFIRMED")))
        .options(selectinload(Question.answer), selectinload(Question.note))).all())
    attach_answers(db, ai_questions)
    ai_ids = [item.id for item in ai_questions if item.effective_answer is not None]
    has_answer = or_(Answer.id.is_not(None), Question.id.in_(ai_ids))
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        ai_search_ids = [item.id for item in ai_questions if item.effective_answer
                         and q.strip().casefold() in item.effective_answer.content.casefold()]
        filters.append(or_(Question.prompt.ilike(pattern), Answer.content.ilike(pattern), Question.id.in_(ai_search_ids)))

    answered_count = db.scalar(
        select(func.count(Question.id))
        .outerjoin(Answer)
        .where(*filters, has_answer)
    ) or 0
    missing_count = db.scalar(
        select(func.count(Question.id))
        .outerjoin(Answer)
        .where(*filters, ~has_answer)
    ) or 0
    page_filters = list(filters)
    if answer_state == "ANSWERED":
        page_filters.append(has_answer)
    elif answer_state == "MISSING":
        page_filters.append(~has_answer)

    total = db.scalar(
        select(func.count(Question.id)).outerjoin(Answer).where(*page_filters)
    ) or 0
    items = db.scalars(
        select(Question)
        .outerjoin(Answer)
        .where(*page_filters)
        .options(selectinload(Question.answer), selectinload(Question.note))
        .order_by(Question.updated_at.desc(), Question.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    statistics = question_source_statistics(db, owner_id, list(items))
    attach_answers(db, list(items))
    return QuestionPage(
        items=[QuestionView.model_validate(question).model_copy(update={"source_statistics": statistics[question.id]}) for question in items],
        page=page,
        page_size=page_size,
        total=total,
        answered_count=answered_count,
        missing_count=missing_count,
    )


@router.get("/{question_id}", response_model=QuestionView)
def get_question(
    question_id: UUID,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> QuestionView:
    question = _get_question(db, question_id, owner_id)
    return QuestionView.model_validate(question).model_copy(
        update={"source_statistics": question_source_statistics(db, owner_id, [question])[question.id]}
    )


@router.get("/{question_id}/sources", response_model=SourceStatistics)
def get_question_sources(question_id: UUID, db: Session = Depends(get_db), owner_id: UUID = Depends(get_current_user_id)) -> SourceStatistics:
    question = _get_question(db, question_id, owner_id)
    return question_source_statistics(db, owner_id, [question])[question.id]


@router.put("/{question_id}", response_model=QuestionView)
def update_question(
    question_id: UUID,
    payload: QuestionUpdate,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> Question:
    question = _get_question(db, question_id, owner_id, lock=True)
    if question.version != payload.expected_version:
        raise HTTPException(status_code=409, detail="题目已被修改，请刷新后重试")
    previous_prompt = question.prompt
    question.prompt = payload.prompt
    question.version += 1
    record_question_change(
        db,
        question_id=question.id,
        owner_id=owner_id,
        field="QUESTION",
        action="UPDATED",
        previous_value=previous_prompt,
        new_value=question.prompt,
        entity_version=question.version,
    )
    db.commit()
    return _get_question(db, question_id, owner_id)


@router.put("/{question_id}/answer", response_model=AnswerView)
def upsert_answer(
    question_id: UUID,
    payload: VersionedContentUpdate,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> Answer:
    if not payload.content.strip():
        raise HTTPException(status_code=422, detail="答案不能为空")
    question = _get_question(db, question_id, owner_id, lock=True)
    if question.answer is None:
        if payload.expected_version != 0:
            raise HTTPException(status_code=409, detail="答案状态已变化，请刷新后重试")
        answer = Answer(
            question=question,
            owner_id=owner_id,
            content=payload.content,
            source_type="USER_WRITTEN",
            version=_next_answer_version(db, question.id),
        )
        answer.revisions.append(AnswerRevision(version=answer.version, content=payload.content))
        db.add(answer)
        previous_content = None
    else:
        answer = question.answer
        if answer.version != payload.expected_version:
            raise HTTPException(status_code=409, detail="答案已被修改，请刷新后重试")
        previous_content = answer.content
        answer.version = _next_answer_version(db, question.id, answer.version)
        answer.content = payload.content
        answer.source_type = "USER_WRITTEN"
        answer.revisions.append(AnswerRevision(version=answer.version, content=payload.content))
    record_question_change(
        db,
        question_id=question.id,
        owner_id=owner_id,
        field="ANSWER",
        action="UPDATED",
        previous_value=previous_content,
        new_value=payload.content,
        entity_version=answer.version,
    )
    db.commit()
    db.refresh(answer)
    return answer


@router.delete("/{question_id}/answer", status_code=status.HTTP_204_NO_CONTENT)
def delete_answer(
    question_id: UUID,
    expected_version: int = Query(alias="expectedVersion", ge=1),
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> None:
    question = _get_question(db, question_id, owner_id, lock=True)
    answer = question.answer
    if answer is None:
        raise HTTPException(status_code=404, detail="答案不存在")
    if answer.version != expected_version:
        raise HTTPException(status_code=409, detail="答案已被修改，请刷新后重试")
    record_question_change(
        db,
        question_id=question.id,
        owner_id=owner_id,
        field="ANSWER",
        action="DELETED",
        previous_value=answer.content,
        new_value=None,
        entity_version=_next_answer_version(db, question.id, answer.version),
    )
    db.delete(answer)
    db.commit()


@router.get("/{question_id}/history", response_model=QuestionHistoryPage)
def list_question_history(
    question_id: UUID,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> QuestionHistoryPage:
    _get_question(db, question_id, owner_id)
    items = db.scalars(
        select(QuestionChange)
        .where(
            QuestionChange.question_id == question_id,
            QuestionChange.owner_id == owner_id,
        )
        .order_by(QuestionChange.created_at.desc(), QuestionChange.id.desc())
        .limit(100)
    ).all()
    return QuestionHistoryPage(items=list(items))


@router.post(
    "/{question_id}/history/{change_id}/restore",
    response_model=QuestionView,
)
def restore_question_history(
    question_id: UUID,
    change_id: UUID,
    payload: QuestionHistoryRestore,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> Question:
    question = _get_question(db, question_id, owner_id, lock=True)
    change = db.scalar(
        select(QuestionChange).where(
            QuestionChange.id == change_id,
            QuestionChange.question_id == question.id,
            QuestionChange.owner_id == owner_id,
        )
    )
    if change is None:
        raise HTTPException(status_code=404, detail="修改记录不存在")

    if change.field == "QUESTION":
        if question.version != payload.expected_version:
            raise HTTPException(status_code=409, detail="题目已被修改，请刷新后重试")
        if change.previous_value is None:
            raise HTTPException(status_code=409, detail="该记录没有可恢复的题目内容")
        current_value = question.prompt
        question.prompt = change.previous_value
        question.version += 1
        entity_version = question.version
    elif change.field == "ANSWER":
        answer = question.answer
        current_version = answer.version if answer is not None else 0
        if current_version != payload.expected_version:
            raise HTTPException(status_code=409, detail="答案已被修改，请刷新后重试")
        current_value = answer.content if answer is not None else None
        next_answer_version = _next_answer_version(db, question.id, current_version)
        if change.previous_value is None:
            if answer is not None:
                db.delete(answer)
            entity_version = next_answer_version
        elif answer is None:
            answer = Answer(
                question=question,
                owner_id=owner_id,
                content=change.previous_value,
                source_type="USER_WRITTEN",
                version=next_answer_version,
            )
            answer.revisions.append(
                AnswerRevision(version=answer.version, content=change.previous_value)
            )
            db.add(answer)
            entity_version = answer.version
        else:
            answer.version = next_answer_version
            answer.content = change.previous_value
            answer.source_type = "USER_WRITTEN"
            answer.revisions.append(
                AnswerRevision(version=answer.version, content=change.previous_value)
            )
            entity_version = answer.version
    elif change.field == "REVIEW_STATUS":
        if change.occurrence_id is None or change.previous_value is None:
            raise HTTPException(status_code=409, detail="该记录没有可恢复的复核状态")
        occurrence = db.scalar(
            select(QuestionOccurrence)
            .where(
                QuestionOccurrence.id == change.occurrence_id,
                QuestionOccurrence.question_id == question.id,
            )
            .with_for_update()
        )
        if occurrence is None:
            raise HTTPException(status_code=409, detail="原复核项已不存在")
        if occurrence.review_status != payload.expected_review_status:
            raise HTTPException(status_code=409, detail="复核状态已变化，请刷新后重试")
        current_value = occurrence.review_status
        occurrence.review_status = change.previous_value
        entity_version = None
    else:
        raise HTTPException(status_code=409, detail="不支持恢复该类修改记录")

    record_question_change(
        db,
        question_id=question.id,
        owner_id=owner_id,
        field=change.field,
        action="RESTORED",
        previous_value=current_value,
        new_value=change.previous_value,
        entity_version=entity_version,
        occurrence_id=change.occurrence_id,
    )
    db.commit()
    return _get_question(db, question_id, owner_id)


@router.put("/{question_id}/note", response_model=NoteView)
def upsert_note(
    question_id: UUID,
    payload: VersionedContentUpdate,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> QuestionNote:
    if not payload.content.strip():
        raise HTTPException(status_code=422, detail="备注不能为空")
    question = _get_question(db, question_id, owner_id, lock=True)
    if question.note is None:
        if payload.expected_version != 0:
            raise HTTPException(status_code=409, detail="备注状态已变化，请刷新后重试")
        note = QuestionNote(question=question, owner_id=owner_id, content=payload.content)
        db.add(note)
    else:
        note = question.note
        if note.version != payload.expected_version:
            raise HTTPException(status_code=409, detail="备注已被修改，请刷新后重试")
        note.version += 1
        note.content = payload.content
    db.commit()
    db.refresh(note)
    return note


@router.delete("/{question_id}/note", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(
    question_id: UUID,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> None:
    question = _get_question(db, question_id, owner_id)
    if question.note is None:
        raise HTTPException(status_code=404, detail="备注不存在")
    db.delete(question.note)
    db.commit()


@router.delete("/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_question(
    question_id: UUID,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> None:
    question = _get_question(db, question_id, owner_id)
    db.delete(question)
    db.commit()
