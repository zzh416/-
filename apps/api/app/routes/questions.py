"""问题、答案、备注与关键词检索。"""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.identity import get_current_user_id
from app.idempotency import remember_request, replay_resource_id, request_hash
from app.models import (
    Answer,
    AnswerRevision,
    Asset,
    AssetVersion,
    Collection,
    Question,
    QuestionNote,
    TextChunk,
)
from app.schemas.questions import (
    AnswerView,
    NoteView,
    QuestionCreate,
    QuestionPage,
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
    return question


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
    filters = [Question.owner_id == owner_id]
    if collection_id is not None:
        filters.append(Question.collection_id == collection_id)
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        filters.append(or_(Question.prompt.ilike(pattern), Answer.content.ilike(pattern)))

    answered_count = db.scalar(
        select(func.count(Question.id))
        .outerjoin(Answer)
        .where(*filters, Answer.id.is_not(None))
    ) or 0
    missing_count = db.scalar(
        select(func.count(Question.id))
        .outerjoin(Answer)
        .where(*filters, Answer.id.is_(None))
    ) or 0
    page_filters = list(filters)
    if answer_state == "ANSWERED":
        page_filters.append(Answer.id.is_not(None))
    elif answer_state == "MISSING":
        page_filters.append(Answer.id.is_(None))

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
    return QuestionPage(
        items=list(items),
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
) -> Question:
    return _get_question(db, question_id, owner_id)


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
    question.prompt = payload.prompt
    question.version += 1
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
        )
        answer.revisions.append(AnswerRevision(version=1, content=payload.content))
        db.add(answer)
    else:
        answer = question.answer
        if answer.version != payload.expected_version:
            raise HTTPException(status_code=409, detail="答案已被修改，请刷新后重试")
        answer.version += 1
        answer.content = payload.content
        answer.revisions.append(AnswerRevision(version=answer.version, content=payload.content))
    db.commit()
    db.refresh(answer)
    return answer


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
