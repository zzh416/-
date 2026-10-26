"""Manual learning states and explicit intake of interview unknowns."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.identity import get_current_user_id
from app.idempotency import remember_request, replay_resource_id, request_hash
from app.models import InterviewSession, LearningRecord, Question, QuestionOccurrence, RecommendationItem, User
from app.modules.question_extraction_service import initial_review_status
from app.modules.recommendation_service import build_recommendation
from app.modules.ai_answer_service import attach_answers
from app.routes.recommendations import _profile_context, _rule_context
from app.schemas.learning import (LearningCandidate, LearningCandidates, LearningItem, LearningLookup,
    LearningLookupPage, LearningPage, LearningUpdate, LearningView)

router = APIRouter(prefix="/learning", tags=["learning"])
_CONFIRM_SCOPE = "learning:interview-turn"


def _visible():
    any_source = exists(select(QuestionOccurrence.id).where(QuestionOccurrence.question_id == Question.id))
    visible_source = exists(select(QuestionOccurrence.id).where(
        QuestionOccurrence.question_id == Question.id, QuestionOccurrence.review_status != "REJECTED"))
    return or_(~any_source, visible_source)


def _question(db, owner, question_id, lock=False):
    statement = select(Question).where(Question.id == question_id, Question.owner_id == owner, _visible()).options(selectinload(Question.answer), selectinload(Question.note))
    question = db.scalar(statement.with_for_update() if lock else statement)
    if question is None:
        raise HTTPException(status_code=404, detail="题目不存在或已隐藏")
    attach_answers(db, [question])
    return question


def _lock_owner(db, owner):
    db.scalar(select(User).where(User.id == owner).with_for_update())


def _versions(question):
    answer = question.effective_answer
    return (question.version, question.answer.version if question.answer else None,
            answer.ai_answer_id if answer else None, answer.version if answer and answer.ai_answer_id else None)


def _view(record, question=None):
    qv, av, ai_id, ai_version = _versions(question) if question else (1, None, None, None)
    return LearningView(id=record.id if record else None, question_id=question.id if question else None,
        source_kind=record.source_kind if record else "BANK", status=record.status if record else "UNMARKED",
        version=record.version if record else 0, question_version=qv, answer_version=av,
        ai_answer_id=ai_id, ai_answer_version=ai_version,
        content_changed=bool(record and question and (record.question_version, record.answer_version, record.ai_answer_id, record.ai_answer_version) != (qv, av, ai_id, ai_version)),
        updated_at=record.updated_at if record else None)


def _record(db, owner, record_id):
    record = db.scalar(select(LearningRecord).where(LearningRecord.id == record_id, LearningRecord.owner_id == owner))
    if record is None:
        raise HTTPException(status_code=404, detail="复习记录不存在")
    return record


def _write(db, owner, payload, *, question=None, record=None):
    if payload.expected_version != (record.version if record else 0):
        raise HTTPException(status_code=409, detail="复习状态已变化，请刷新后再标记")
    versions = _versions(question) if question else (1, None, None, None)
    if (payload.question_version, payload.answer_version, payload.ai_answer_id, payload.ai_answer_version) != versions:
        raise HTTPException(status_code=409, detail="题目或答案已更新，请核对当前内容后再标记")
    if record is None:
        record = LearningRecord(owner_id=owner, identity_key=f"question:{question.id}", question=question,
                                source_kind="BANK", prompt=question.prompt, version=1)
        db.add(record)
    else:
        record.version += 1
    record.status = payload.status
    record.question_version, record.answer_version, record.ai_answer_id, record.ai_answer_version = versions
    if question:
        record.prompt = question.prompt
    return record


@router.post("/lookup", response_model=LearningLookupPage)
def lookup(payload: LearningLookup, db: Session = Depends(get_db), owner: UUID = Depends(get_current_user_id)):
    ids = list(dict.fromkeys(payload.question_ids))
    questions = db.scalars(select(Question).where(Question.id.in_(ids), Question.owner_id == owner, _visible())
                          .options(selectinload(Question.answer), selectinload(Question.note))).all()
    if len(questions) != len(ids):
        raise HTTPException(status_code=404, detail="部分题目不存在或已隐藏，请刷新列表")
    attach_answers(db, questions)
    states = {r.question_id: r for r in db.scalars(select(LearningRecord).where(
        LearningRecord.owner_id == owner, LearningRecord.question_id.in_(ids))).all()}
    by_id = {q.id: q for q in questions}
    return LearningLookupPage(items=[_view(states.get(qid), by_id[qid]) for qid in ids])


@router.put("/questions/{question_id}", response_model=LearningView)
def mark_question(question_id: UUID, payload: LearningUpdate, db: Session = Depends(get_db), owner: UUID = Depends(get_current_user_id)):
    _lock_owner(db, owner)
    question = _question(db, owner, question_id, True)
    record = db.scalar(select(LearningRecord).where(LearningRecord.owner_id == owner, LearningRecord.question_id == question_id))
    record = _write(db, owner, payload, question=question, record=record)
    db.commit()
    return _view(record, question)


@router.put("/items/{record_id}", response_model=LearningView)
def mark_item(record_id: UUID, payload: LearningUpdate, db: Session = Depends(get_db), owner: UUID = Depends(get_current_user_id)):
    _lock_owner(db, owner)
    record = _record(db, owner, record_id)
    question = _question(db, owner, record.question_id, True) if record.question_id else None
    record = _write(db, owner, payload, question=question, record=record)
    db.commit()
    return _view(record, question)


@router.get("/items", response_model=LearningPage)
def items(db: Session = Depends(get_db), owner: UUID = Depends(get_current_user_id),
          state: Literal["TO_REVIEW", "ANY", "MASTERED", "WEAK", "UNKNOWN"] = "TO_REVIEW",
          q: str = Query(default="", max_length=200), page: int = Query(default=1, ge=1), page_size: int = Query(default=20, alias="pageSize", ge=1, le=100)):
    _, _, _, skills = _profile_context(db, owner)
    _, rules, _ = _rule_context(db, owner)
    records = db.scalars(select(LearningRecord).where(LearningRecord.owner_id == owner, LearningRecord.status != "UNMARKED")
                        .options(selectinload(LearningRecord.question).selectinload(Question.answer),
                                 selectinload(LearningRecord.question).selectinload(Question.note))).all()
    attach_answers(db, list({r.question_id: r.question for r in records if r.question is not None}.values()))
    question_ids = [r.question_id for r in records if r.question_id]
    visible_ids = set(db.scalars(select(Question.id).where(Question.id.in_(question_ids), Question.owner_id == owner, _visible())).all()) if question_ids else set()
    eligible, excluded = [], 0
    for record in records:
        question = record.question
        if record.source_kind == "BANK" and (question is None or question.id not in visible_ids):
            excluded += 1
            continue
        prompt = question.prompt if question else record.prompt
        effective = question.effective_answer if question else None
        answer = effective.content if effective else None
        evaluation = build_recommendation(prompt=prompt, answer=answer, skills=skills, rules=rules)
        if evaluation.decision != "INCLUDED":
            excluded += 1
            continue
        view = _view(record, question)
        eligible.append(LearningItem(**view.model_dump(), prompt=prompt,
            answer_content=answer if evaluation.usable_answer_state == "ANSWERED" else None,
            answer_source_type=effective.source_type if effective and evaluation.usable_answer_state == "ANSWERED" else None,
            source_session_id=record.source_session_id, source_turn_id=record.source_turn_id))
    counts = {s: sum(i.status == s for i in eligible) for s in ("UNKNOWN", "WEAK", "MASTERED")}
    changed = sum(i.content_changed for i in eligible)
    filtered = [i for i in eligible if (state == "ANY" or (state == "TO_REVIEW" and (i.status in {"UNKNOWN", "WEAK"} or i.content_changed)) or i.status == state)
                and (not q.strip() or q.strip().casefold() in (i.prompt + "\n" + (i.answer_content or "")).casefold())]
    filtered.sort(key=lambda i: ({"UNKNOWN": 0, "WEAK": 1}.get(i.status, 2), i.updated_at, str(i.id)))
    return LearningPage(items=filtered[(page - 1) * page_size:page * page_size], page=page, page_size=page_size,
                        total=len(filtered), excluded_count=excluded, unknown_count=counts["UNKNOWN"], weak_count=counts["WEAK"],
                        mastered_count=counts["MASTERED"], changed_count=changed)


def _session(db, owner, session_id):
    session = db.scalar(select(InterviewSession).where(InterviewSession.id == session_id, InterviewSession.owner_id == owner))
    if session is None:
        raise HTTPException(status_code=404, detail="模拟面试记录不存在")
    return session


def _turn_source(db, owner, session, turn):
    if turn.get("kind") != "BANK" or turn.get("depth", 0) != 0:
        if initial_review_status(turn["prompt"]) == "REJECTED":
            raise HTTPException(status_code=409, detail="这段内容不适合作为题目，请先人工核对")
        return None
    try:
        root_id = UUID(turn["root_id"])
    except (ValueError, KeyError):
        raise HTTPException(status_code=409, detail="原题关联不可用，请先核对面试记录")
    source = db.scalar(select(RecommendationItem).where(RecommendationItem.id == root_id, RecommendationItem.run_id == session.recommendation_run_id))
    if source is None or source.question_id is None:
        raise HTTPException(status_code=409, detail="原题已删除，无法确认加入")
    question = _question(db, owner, source.question_id)
    if question.version != source.question_version or question.prompt != turn["prompt"]:
        raise HTTPException(status_code=409, detail="原题已更新，请在题库核对后手动标记")
    return question


def _confirm_key(session_id, turn_id):
    return f"{session_id}:{turn_id}"


@router.get("/interviews/{session_id}/candidates", response_model=LearningCandidates)
def candidates(session_id: UUID, db: Session = Depends(get_db), owner: UUID = Depends(get_current_user_id)):
    session = _session(db, owner, session_id)
    result = []
    for turn in session.state.get("turns", []):
        if turn.get("answer_status") != "DONT_KNOW":
            continue
        turn_id = turn["id"]
        replay = replay_resource_id(db, owner, _CONFIRM_SCOPE, _confirm_key(session_id, turn_id), request_hash({"session": str(session_id), "turn": turn_id}))
        record = db.get(LearningRecord, replay) if replay else None
        error, question = None, None
        try:
            question = _turn_source(db, owner, session, turn)
        except HTTPException as exc:
            error = str(exc.detail)
        result.append(LearningCandidate(turn_id=turn_id, prompt=turn["prompt"], source_kind="BANK" if turn.get("kind") == "BANK" and not turn.get("depth") else "INTERVIEW",
            confirmed=replay is not None, can_confirm=error is None and replay is None, message=error or ("已确认，本次重复操作不会覆盖后续自评" if replay else None),
            learning=_view(record, question) if record else None))
    return LearningCandidates(items=result)


@router.post("/interviews/{session_id}/turns/{turn_id}", response_model=LearningView)
def confirm(session_id: UUID, turn_id: UUID, db: Session = Depends(get_db), owner: UUID = Depends(get_current_user_id)):
    _lock_owner(db, owner)
    session = _session(db, owner, session_id)
    turn = next((t for t in session.state.get("turns", []) if t["id"] == str(turn_id) and t.get("answer_status") == "DONT_KNOW"), None)
    if turn is None:
        raise HTTPException(status_code=404, detail="未找到可确认的不会题")
    key = _confirm_key(session_id, turn_id)
    digest = request_hash({"session": str(session_id), "turn": str(turn_id)})
    replay = replay_resource_id(db, owner, _CONFIRM_SCOPE, key, digest)
    if replay:
        record = _record(db, owner, replay)
        question = _question(db, owner, record.question_id) if record.question_id else None
        return _view(record, question)
    question = _turn_source(db, owner, session, turn)
    if question:
        record = db.scalar(select(LearningRecord).where(LearningRecord.owner_id == owner, LearningRecord.question_id == question.id))
        qv, av, ai_id, ai_version = _versions(question)
        record = _write(db, owner, LearningUpdate(status="UNKNOWN", expected_version=record.version if record else 0,
                                                question_version=qv, answer_version=av, ai_answer_id=ai_id,
                                                ai_answer_version=ai_version), question=question, record=record)
    else:
        record = LearningRecord(owner_id=owner, identity_key=f"interview:{session_id}:{turn_id}", source_kind="INTERVIEW",
            source_session_id=session_id, source_turn_id=str(turn_id), prompt=turn["prompt"], status="UNKNOWN", version=1, question_version=1)
        db.add(record)
    record.source_session_id, record.source_turn_id = session_id, str(turn_id)
    db.flush()
    remember_request(db, owner, _CONFIRM_SCOPE, key, digest, record.id)
    db.commit()
    return _view(record, question)
