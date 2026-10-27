"""Create and read versioned personalized review lists."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.identity import get_current_user_id
from app.idempotency import remember_request, replay_resource_id, request_hash
from app.models import (
    Answer,
    Question,
    QuestionNote,
    RecommendationItem,
    RecommendationRun,
)
from app.modules.recommendation_context import (
    content_fingerprint as _content_fingerprint,
    deduplicate_questions as _deduplicate_questions,
    profile_context as _profile_context,
    rule_context as _rule_context,
)
from app.modules.recommendation_state import (
    recommendation_run_is_current,
    visible_questions as _visible_questions,
)
from app.modules.recommendation_service import (
    ALGORITHM_VERSION,
    build_recommendation,
)
from app.modules.recommendation_export import render_prep_package
from app.modules.project_matching import PROJECT_MATCH_VERSION, match_projects, prepare_projects
from app.modules.source_statistics import question_source_statistics
from app.schemas.recommendations import (
    RecommendationItemPage,
    RecommendationRunCreate,
    RecommendationRunView,
)


router = APIRouter(prefix="/recommendation-runs", tags=["recommendations"])


def _run_view(db: Session, run: RecommendationRun, owner_id: UUID) -> RecommendationRunView:
    return RecommendationRunView(
        id=run.id,
        status=run.status,
        algorithm_version=run.algorithm_version,
        profile_version=run.profile_version,
        profile_snapshot=run.profile_snapshot,
        rule_snapshot=run.rule_snapshot,
        is_current=recommendation_run_is_current(db, run, owner_id),
        total_count=run.total_count,
        included_count=run.included_count,
        excluded_count=run.excluded_count,
        answered_count=run.answered_count,
        missing_count=run.missing_count,
        created_at=run.created_at,
    )


def _owned_run(db: Session, run_id: UUID, owner_id: UUID) -> RecommendationRun:
    run = db.scalar(
        select(RecommendationRun).where(
            RecommendationRun.id == run_id, RecommendationRun.owner_id == owner_id
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="个性化清单不存在")
    return run


@router.post("", response_model=RecommendationRunView, status_code=status.HTTP_201_CREATED)
def create_recommendation_run(
    payload: RecommendationRunCreate,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=8, max_length=200
    ),
) -> RecommendationRunView:
    profile_version_id, profile_version, profile_snapshot, skills = _profile_context(db, owner_id)
    rule_snapshot, rules, rules_fingerprint = _rule_context(db, owner_id)
    questions = _visible_questions(db, owner_id)
    source_statistics = question_source_statistics(db, owner_id, questions)
    content_fingerprint = _content_fingerprint(questions, source_statistics)
    digest = request_hash(
        {
            **payload.model_dump(mode="json"),
            "profileVersion": profile_version,
            "rulesFingerprint": rules_fingerprint,
            "contentFingerprint": content_fingerprint,
            "algorithmVersion": ALGORITHM_VERSION,
            "projectMatchingVersion": PROJECT_MATCH_VERSION,
        }
    )
    replay_id = replay_resource_id(
        db, owner_id, "recommendation-run:create", idempotency_key, digest
    )
    if replay_id is not None:
        return _run_view(db, _owned_run(db, replay_id, owner_id), owner_id)

    evaluated = []
    for question in _deduplicate_questions(questions):
        answer_content = question.effective_answer.content if question.effective_answer is not None else None
        evaluation = build_recommendation(
            prompt=question.prompt,
            answer=answer_content,
            skills=skills,
            rules=rules,
        )
        evaluated.append((question, evaluation))
    evaluated.sort(
        key=lambda pair: (
            pair[1].decision != "INCLUDED",
            -pair[1].score,
            pair[0].prompt.casefold(),
            str(pair[0].id),
        )
    )

    run = RecommendationRun(
        owner_id=owner_id,
        profile_version_id=profile_version_id,
        profile_version=profile_version,
        profile_snapshot=profile_snapshot,
        rule_snapshot=rule_snapshot,
        rules_fingerprint=rules_fingerprint,
        content_fingerprint=content_fingerprint,
        algorithm_version=ALGORITHM_VERSION,
        total_count=len(evaluated),
    )
    db.add(run)
    db.flush()
    projects = prepare_projects(profile_snapshot["projects"])
    for position, (question, evaluation) in enumerate(evaluated, start=1):
        answer = question.effective_answer
        db.add(
            RecommendationItem(
                run_id=run.id,
                question_id=question.id,
                collection_id=question.collection_id,
                position=position,
                question_version=question.version,
                answer_version=question.answer.version if question.answer is not None else None,
                ai_answer_id=answer.ai_answer_id if answer else None,
                ai_answer_version=answer.version if answer and answer.ai_answer_id else None,
                prompt=question.prompt,
                answer_content=answer.content if answer is not None else None,
                answer_source_type=answer.source_type if answer is not None else None,
                decision=evaluation.decision,
                score=evaluation.score,
                answer_state="AI_ANSWER" if answer and answer.source_type == "AI_GENERATED" else evaluation.answer_state,
                usable_answer_state=evaluation.usable_answer_state,
                question_technologies=[item.__dict__ for item in evaluation.question_technologies],
                answer_technologies=[item.__dict__ for item in evaluation.answer_technologies],
                reason_codes=list(evaluation.reason_codes),
                reasons=[*evaluation.reasons, "采用已人工确认的AI补充参考，仍需核对"] if answer and answer.source_type == "AI_GENERATED" else list(evaluation.reasons),
                source_statistics=source_statistics[question.id].model_dump(mode="json"),
                project_matches=match_projects(question.prompt, projects) if evaluation.decision == "INCLUDED" else [],
            )
        )
    run.included_count = sum(item.decision == "INCLUDED" for _, item in evaluated)
    run.excluded_count = run.total_count - run.included_count
    run.answered_count = sum(
        item.decision == "INCLUDED" and item.usable_answer_state == "ANSWERED"
        for _, item in evaluated
    )
    run.missing_count = run.included_count - run.answered_count
    try:
        remember_request(
            db, owner_id, "recommendation-run:create", idempotency_key, digest, run.id
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        replay_id = replay_resource_id(
            db, owner_id, "recommendation-run:create", idempotency_key, digest
        )
        if replay_id is not None:
            return _run_view(db, _owned_run(db, replay_id, owner_id), owner_id)
        raise HTTPException(status_code=409, detail="重复的个性化清单创建请求") from exc
    db.refresh(run)
    return _run_view(db, run, owner_id)


@router.get("/latest", response_model=RecommendationRunView | None)
def get_latest_recommendation_run(
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> RecommendationRunView | None:
    run = db.scalar(
        select(RecommendationRun)
        .where(RecommendationRun.owner_id == owner_id)
        .order_by(RecommendationRun.created_at.desc(), RecommendationRun.id.desc())
        .limit(1)
    )
    return _run_view(db, run, owner_id) if run is not None else None


@router.get("/{run_id}", response_model=RecommendationRunView)
def get_recommendation_run(
    run_id: UUID,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> RecommendationRunView:
    return _run_view(db, _owned_run(db, run_id, owner_id), owner_id)


@router.get("/{run_id}/items", response_model=RecommendationItemPage)
def list_recommendation_items(
    run_id: UUID,
    decision: Literal["ANY", "INCLUDED", "EXCLUDED"] = "ANY",
    usable_answer_state: Literal["ANY", "ANSWERED", "MISSING"] = Query(
        default="ANY", alias="usableAnswerState"
    ),
    q: str | None = None,
    project_id: UUID | None = Query(default=None, alias="projectId"),
    unmatched_only: bool = Query(default=False, alias="unmatchedOnly"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> RecommendationItemPage:
    run = _owned_run(db, run_id, owner_id)
    if project_id is not None and unmatched_only:
        raise HTTPException(status_code=422, detail="项目与未匹配筛选不能同时指定")
    filters = [RecommendationItem.run_id == run.id]
    if decision != "ANY":
        filters.append(RecommendationItem.decision == decision)
    if usable_answer_state != "ANY":
        filters.append(RecommendationItem.usable_answer_state == usable_answer_state)
    if project_id is not None:
        filters.append(cast(RecommendationItem.project_matches, JSONB).contains([{"project_id": str(project_id)}]))
    if unmatched_only:
        filters.extend([
            RecommendationItem.decision == "INCLUDED",
            cast(RecommendationItem.project_matches, JSONB) == [],
        ])
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        filters.append(
            or_(
                RecommendationItem.prompt.ilike(pattern),
                RecommendationItem.answer_content.ilike(pattern),
            )
        )
    total = db.scalar(select(func.count(RecommendationItem.id)).where(*filters)) or 0
    items = db.scalars(
        select(RecommendationItem)
        .where(*filters)
        .order_by(RecommendationItem.position)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return RecommendationItemPage(
        items=list(items),
        page=page,
        page_size=page_size,
        total=total,
        included_count=run.included_count,
        excluded_count=run.excluded_count,
        answered_count=run.answered_count,
        missing_count=run.missing_count,
    )


@router.get("/{run_id}/export", response_class=Response)
def export_recommendation_run(
    run_id: UUID,
    include_notes: bool = Query(default=False, alias="includeNotes"),
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> Response:
    run = _owned_run(db, run_id, owner_id)
    items = db.scalars(select(RecommendationItem).where(
        RecommendationItem.run_id == run.id,
        RecommendationItem.decision == "INCLUDED",
    ).order_by(RecommendationItem.position)).all()
    if not items:
        raise HTTPException(status_code=409, detail="这份清单没有可导出的题目")
    notes = {}
    if include_notes:
        # Join the owned run rather than a large IN list; both note and question must be owned.
        notes = dict(db.execute(
            select(QuestionNote.question_id, QuestionNote.content)
            .join(Question, Question.id == QuestionNote.question_id)
            .join(RecommendationItem, RecommendationItem.question_id == Question.id)
            .where(
                QuestionNote.owner_id == owner_id, Question.owner_id == owner_id,
                RecommendationItem.run_id == run.id, RecommendationItem.decision == "INCLUDED",
            )
        ).all())
    content = render_prep_package(
        _run_view(db, run, owner_id), items,
        exported_at=datetime.now(timezone.utc), notes=notes, include_notes=include_notes,
    )
    filename = f"面试备考包-{str(run.id)[:8]}.md"
    return Response(content=content, media_type="text/markdown", headers={
        "Content-Disposition": f"attachment; filename=\"interview-prep-{run.id}.md\"; filename*=UTF-8''{quote(filename)}",
        "Cache-Control": "no-store",
    })
