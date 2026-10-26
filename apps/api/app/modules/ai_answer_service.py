"""Owner-scoped AI drafts, input snapshots and effective answer selection."""
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import hashlib
import json
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.adapters.ai_answer import generate_ai_answer
from app.adapters.glm_text import TextModelError, TextModelNotConfigured
from app.adapters.text_provider import get_text_model_config
from app.db import SessionLocal
from app.config import settings
from app.models import AIAnswerRun, Asset, AssetVersion, Question, QuestionOccurrence, TextChunk
from app.schemas.ai_answers import AIAnswerView, EffectiveAnswerView


def now():
    return datetime.now(timezone.utc)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


class InputContext:
    """Read shared profile/preferences once and source passages in a bounded bulk query."""
    def __init__(self, db, owner, questions):
        from app.routes.recommendations import _profile_context, _rule_context
        _, self.profile_version, profile, _ = _profile_context(db, owner)
        self.profile = {"target_role": profile.get("target_role"),
            "skills": [s for s in profile["skills"] if s["review_status"] == "CONFIRMED"],
            "projects": [{"name": p["name"], "description": p["description"][:1500]}
                         for p in profile.get("projects", []) if p["review_status"] == "CONFIRMED"][:5]}
        self.rules, _, self.rules_fingerprint = _rule_context(db, owner)
        ids = defaultdict(list)
        for q in questions:
            if q.source_chunk_id:
                ids[q.id].append(q.source_chunk_id)
        qids = [q.id for q in questions]
        if qids:
            for row in db.scalars(select(QuestionOccurrence).where(QuestionOccurrence.question_id.in_(qids),
                    QuestionOccurrence.review_status != "REJECTED").order_by(QuestionOccurrence.created_at, QuestionOccurrence.id)).all():
                ids[row.question_id].extend(i for i in (row.answer_source_chunk_id, row.source_chunk_id) if i)
                for span in row.answer_spans or []:
                    if isinstance(span, dict):
                        try:
                            ids[row.question_id].append(UUID(str(span.get("sourceChunkId"))))
                        except (ValueError, TypeError):
                            continue
        ids = {qid: list(dict.fromkeys(values)) for qid, values in ids.items()}
        chunk_ids = {cid for values in ids.values() for cid in values}
        chunks = {c.id: c for c in db.scalars(select(TextChunk).join(AssetVersion).join(Asset)
            .where(TextChunk.id.in_(chunk_ids), Asset.owner_id == owner,
                   AssetVersion.version == Asset.current_version)).all()} if chunk_ids else {}
        self.sources = {}
        for qid, values in ids.items():
            valid = [chunks[cid] for cid in values if cid in chunks]
            # Keep source documents together and their passages in original order.
            version_order = {vid: index for index, vid in enumerate(dict.fromkeys(c.asset_version_id for c in valid))}
            valid.sort(key=lambda c: (version_order[c.asset_version_id], c.ordinal, str(c.id)))
            # Historical or foreign passages must not consume the citation budget.
            self.sources[qid] = [{"chunk_id": str(c.id), "content": c.content[:2000]} for c in valid[:3]]

    def snapshot(self, question, use_profile, include_note):
        return {"question": question.prompt, "question_version": question.version,
                "original_answer": question.answer.content[:4000] if question.answer else None,
                "original_answer_version": question.answer.version if question.answer else None,
                "profile": self.profile if use_profile else None,
                "profile_version": self.profile_version if use_profile else None,
                "preferences": self.rules, "rules_fingerprint": self.rules_fingerprint,
                "note": question.note.content[:2000] if include_note and question.note else None,
                "note_version": question.note.version if include_note and question.note else None,
                "sources": self.sources.get(question.id, [])}


def question(db, owner, question_id, lock=False):
    statement = select(Question).where(Question.id == question_id, Question.owner_id == owner).options(
        selectinload(Question.answer), selectinload(Question.note))
    return db.scalar(statement.with_for_update() if lock else statement)


def is_current(db, run, q=None, context=None):
    q = q or question(db, run.owner_id, run.question_id)
    if q is None or q.version != run.question_version:
        return False
    context = context or InputContext(db, run.owner_id, [q])
    return fingerprint(context.snapshot(q, run.used_profile, run.used_note)) == run.input_fingerprint


def run_view(db, run, *, current=None):
    fields = {name: getattr(run, name) for name in AIAnswerView.model_fields if hasattr(run, name)}
    current = is_current(db, run) if current is None else current
    retryable = run.status == "FAILED" or (run.status in {"QUEUED", "PROCESSING"}
        and now() - (run.started_at or run.updated_at) > timedelta(minutes=5))
    return AIAnswerView(**fields, is_current=current, retry_limit=settings.ai_answer_retry_limit,
        can_retry=current and run.review_status != "DISCARDED" and retryable and run.retry_count < settings.ai_answer_retry_limit)


def attach_answers(db, questions):
    """Latest task is visible; only a current confirmed result is an effective fallback."""
    if not questions:
        return
    owner = questions[0].owner_id
    rows = db.scalars(select(AIAnswerRun).where(AIAnswerRun.owner_id == owner,
        AIAnswerRun.question_id.in_([q.id for q in questions])).order_by(AIAnswerRun.created_at.desc(), AIAnswerRun.id.desc())).all()
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.question_id].append(row)
    context = InputContext(db, owner, [q for q in questions if q.id in grouped]) if rows else None
    for q in questions:
        runs = grouped[q.id]
        currents = {r.id: is_current(db, r, q, context) for r in runs}
        q.ai_answer = run_view(db, runs[0], current=currents[runs[0].id]) if runs else None
        selected = next((r for r in runs if r.status == "SUCCEEDED" and r.review_status == "CONFIRMED" and currents[r.id]), None)
        q.confirmed_ai_answer_id = selected.id if selected is not None else None
        if q.answer is not None:
            q.effective_answer = EffectiveAnswerView(content=q.answer.content, source_type=q.answer.source_type,
                                                     version=q.answer.version, ai_answer_id=None)
        elif selected is not None:
            q.effective_answer = EffectiveAnswerView(content=selected.content, source_type="AI_GENERATED",
                                                     version=selected.version, ai_answer_id=selected.id)
        else:
            q.effective_answer = None


def process_ai_answer(run_id: UUID, *, generator=generate_ai_answer):
    token = uuid4()
    with SessionLocal() as db:
        run = db.scalar(select(AIAnswerRun).where(AIAnswerRun.id == run_id).with_for_update())
        if not run or run.status != "QUEUED":
            return
        if not is_current(db, run):
            run.status, run.error_code, run.error_message = "FAILED", "AI_ANSWER_INPUT_STALE", "题目或生成依据已变化，请重新生成"
            run.version += 1
            db.commit()
            return
        config = get_text_model_config()
        payload = run.input_snapshot
        run.job_token, run.status, run.started_at = token, "PROCESSING", now()
        run.provider, run.model = config.provider, config.model
        run.version += 1
        db.commit()
    result, error = None, None
    try:
        result = generator(payload, api_key=config.api_key, base_url=config.base_url, model=config.model, provider=config.provider)
    except (TextModelError, TextModelNotConfigured) as exc:
        error = (getattr(exc, "code", "AI_ANSWER_MODEL_NOT_CONFIGURED"), str(exc)[:500])
    except Exception:
        error = ("AI_ANSWER_FAILED", "AI补充答案处理失败，可稍后重试")
    with SessionLocal() as db:
        run = db.scalar(select(AIAnswerRun).where(AIAnswerRun.id == run_id).with_for_update())
        if not run or run.status != "PROCESSING" or run.job_token != token:
            return
        if not is_current(db, run):
            error = ("AI_ANSWER_INPUT_STALE", "题目或生成依据已变化，请重新生成")
        if error:
            run.status, run.error_code, run.error_message = "FAILED", *error
        else:
            run.content, run.basis = result.content, result.basis
            run.citations = [c.model_dump(mode="json") for c in result.citations]
            run.status, run.review_status = "SUCCEEDED", "DRAFT"
            run.error_code = run.error_message = None
        run.job_token, run.completed_at = None, now()
        run.version += 1
        db.commit()
