"""Create and read versioned personalized review lists."""
from __future__ import annotations

import hashlib
import json
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
    PreferenceRule,
    ProfileVersion,
    Question,
    QuestionOccurrence,
    RecommendationItem,
    RecommendationRun,
    UserProfile,
)
from app.modules.recommendation_service import (
    ALGORITHM_VERSION,
    PreferenceSnapshot,
    SkillSnapshot,
    build_recommendation,
)
from app.schemas.recommendations import (
    RecommendationItemPage,
    RecommendationRunCreate,
    RecommendationRunView,
)


router = APIRouter(prefix="/recommendation-runs", tags=["recommendations"])


def _rule_context(db: Session, owner_id: UUID) -> tuple[list[dict], list[PreferenceSnapshot], str]:
    rules = db.scalars(
        select(PreferenceRule)
        .where(
            PreferenceRule.owner_id == owner_id,
            PreferenceRule.deleted_at.is_(None),
            PreferenceRule.is_active.is_(True),
        )
        .order_by(PreferenceRule.created_at, PreferenceRule.id)
    ).all()
    snapshot = [
        {
            "id": str(rule.id),
            "rule_type": rule.rule_type,
            "technology": rule.technology,
            "canonical_technology": rule.canonical_technology,
            "category": rule.category,
            "scope": rule.scope,
            "version": rule.version,
        }
        for rule in rules
    ]
    fingerprint = hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return (
        snapshot,
        [
            PreferenceSnapshot(
                rule.rule_type,
                rule.canonical_technology,
                rule.technology,
                rule.category,
                rule.scope,
            )
            for rule in rules
        ],
        fingerprint,
    )


def _profile_context(
    db: Session, owner_id: UUID
) -> tuple[UUID | None, int, dict, list[SkillSnapshot]]:
    profile = db.scalar(select(UserProfile).where(UserProfile.owner_id == owner_id))
    if profile is None or profile.current_version == 0:
        return None, 0, {"target_role": None, "skills": []}, []
    version = db.scalar(
        select(ProfileVersion)
        .where(
            ProfileVersion.profile_id == profile.id,
            ProfileVersion.version == profile.current_version,
        )
        .options(selectinload(ProfileVersion.skills))
    )
    if version is None:
        raise HTTPException(status_code=409, detail="当前画像版本不存在，请重新保存画像")
    skill_snapshot = [
        {
            "canonical_name": skill.canonical_name,
            "display_name": skill.display_name,
            "category": skill.category,
            "review_status": skill.review_status,
        }
        for skill in version.skills
    ]
    return (
        version.id,
        version.version,
        {"target_role": version.target_role, "skills": skill_snapshot},
        [
            SkillSnapshot(
                skill.canonical_name,
                skill.display_name,
                skill.category,
                skill.review_status,
            )
            for skill in version.skills
        ],
    )


def _visible_questions(db: Session, owner_id: UUID) -> list[Question]:
    has_occurrence = exists(
        select(QuestionOccurrence.id).where(QuestionOccurrence.question_id == Question.id)
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
                or_(~has_occurrence, has_visible_occurrence),
            )
            .options(selectinload(Question.answer))
            .order_by(Question.id)
        ).all()
    )


def _content_fingerprint(questions: list[Question]) -> str:
    snapshot = [
        {
            "id": str(question.id),
            "question_version": question.version,
            "answer_version": question.answer.version if question.answer is not None else None,
        }
        for question in questions
    ]
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _run_view(db: Session, run: RecommendationRun, owner_id: UUID) -> RecommendationRunView:
    _, current_profile_version, _, _ = _profile_context(db, owner_id)
    _, _, current_fingerprint = _rule_context(db, owner_id)
    current_content_fingerprint = _content_fingerprint(_visible_questions(db, owner_id))
    return RecommendationRunView(
        id=run.id,
        status=run.status,
        algorithm_version=run.algorithm_version,
        profile_version=run.profile_version,
        profile_snapshot=run.profile_snapshot,
        rule_snapshot=run.rule_snapshot,
        is_current=(
            run.profile_version == current_profile_version
            and run.rules_fingerprint == current_fingerprint
            and run.content_fingerprint == current_content_fingerprint
            and run.algorithm_version == ALGORITHM_VERSION
        ),
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
    content_fingerprint = _content_fingerprint(questions)
    digest = request_hash(
        {
            **payload.model_dump(mode="json"),
            "profileVersion": profile_version,
            "rulesFingerprint": rules_fingerprint,
            "contentFingerprint": content_fingerprint,
            "algorithmVersion": ALGORITHM_VERSION,
        }
    )
    replay_id = replay_resource_id(
        db, owner_id, "recommendation-run:create", idempotency_key, digest
    )
    if replay_id is not None:
        return _run_view(db, _owned_run(db, replay_id, owner_id), owner_id)

    evaluated = []
    for question in questions:
        answer_content = question.answer.content if question.answer is not None else None
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
    for position, (question, evaluation) in enumerate(evaluated, start=1):
        answer = question.answer
        db.add(
            RecommendationItem(
                run_id=run.id,
                question_id=question.id,
                collection_id=question.collection_id,
                position=position,
                question_version=question.version,
                answer_version=answer.version if answer is not None else None,
                prompt=question.prompt,
                answer_content=answer.content if answer is not None else None,
                answer_source_type=answer.source_type if answer is not None else None,
                decision=evaluation.decision,
                score=evaluation.score,
                answer_state=evaluation.answer_state,
                usable_answer_state=evaluation.usable_answer_state,
                question_technologies=[item.__dict__ for item in evaluation.question_technologies],
                answer_technologies=[item.__dict__ for item in evaluation.answer_technologies],
                reason_codes=list(evaluation.reason_codes),
                reasons=list(evaluation.reasons),
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
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> RecommendationItemPage:
    run = _owned_run(db, run_id, owner_id)
    filters = [RecommendationItem.run_id == run.id]
    if decision != "ANY":
        filters.append(RecommendationItem.decision == decision)
    if usable_answer_state != "ANY":
        filters.append(RecommendationItem.usable_answer_state == usable_answer_state)
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
