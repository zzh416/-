"""Shared profile, preference and recommendation-state queries.

This module is intentionally independent from FastAPI routes so workers and
HTTP handlers use the same current-state rules without importing each other.
"""
from __future__ import annotations

import hashlib
import json
from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    PreferenceRule,
    ProfileVersion,
    Question,
    QuestionOccurrence,
    RecommendationRun,
    UserProfile,
)
from app.modules.project_matching import PROJECT_MATCH_VERSION
from app.modules.recommendation_service import (
    ALGORITHM_VERSION,
    PreferenceSnapshot,
    SkillSnapshot,
)
from app.modules.question_identity import question_identity
from app.modules.source_statistics import question_source_statistics


class RecommendationContextError(RuntimeError):
    """The saved profile points at an unavailable current version."""


def rule_context(
    db: Session, owner_id: UUID
) -> tuple[list[dict], list[PreferenceSnapshot], str]:
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
        json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
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


def profile_context(
    db: Session, owner_id: UUID
) -> tuple[UUID | None, int, dict, list[SkillSnapshot]]:
    profile = db.scalar(select(UserProfile).where(UserProfile.owner_id == owner_id))
    if profile is None or profile.current_version == 0:
        return (
            None,
            0,
            {
                "target_role": None,
                "skills": [],
                "projects": [],
                "project_matching_version": PROJECT_MATCH_VERSION,
            },
            [],
        )
    version = db.scalar(
        select(ProfileVersion)
        .where(
            ProfileVersion.profile_id == profile.id,
            ProfileVersion.version == profile.current_version,
        )
        .options(selectinload(ProfileVersion.skills))
    )
    if version is None:
        raise RecommendationContextError("当前画像版本不存在，请重新保存画像")
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
        {
            "target_role": version.target_role,
            "skills": skill_snapshot,
            "projects": version.projects,
            "project_matching_version": PROJECT_MATCH_VERSION,
        },
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


def visible_questions(db: Session, owner_id: UUID) -> list[Question]:
    # Imported lazily because AI answer freshness itself reads profile context.
    from app.modules.ai_answer_service import attach_answers

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
    questions = list(
        db.scalars(
            select(Question)
            .where(
                Question.owner_id == owner_id,
                or_(~has_occurrence, has_visible_occurrence),
            )
            .options(selectinload(Question.answer), selectinload(Question.note))
            .order_by(Question.id)
        ).all()
    )
    attach_answers(db, questions)
    return questions


def content_fingerprint(questions: list[Question], source_statistics: dict) -> str:
    snapshot = [
        {
            "id": str(question.id),
            "question_version": question.version,
            "answer_version": (
                question.answer.version if question.answer is not None else None
            ),
            **(
                {
                    "ai_answer_id": str(question.effective_answer.ai_answer_id),
                    "ai_answer_version": question.effective_answer.version,
                }
                if getattr(question, "effective_answer", None)
                and question.effective_answer.ai_answer_id
                else {}
            ),
            "sources": source_statistics[question.id].model_dump(mode="json"),
        }
        for question in questions
    ]
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def deduplicate_questions(questions: list[Question]) -> list[Question]:
    """Keep one equivalent prompt, preferring the most useful current answer."""

    selected: dict[str, Question] = {}
    for question in questions:
        identity = question_identity(question.prompt)
        current = selected.get(identity)
        if current is None:
            selected[identity] = question
            continue
        question_rank = (
            2
            if question.answer and question.answer.content.strip()
            else int(bool(getattr(question, "effective_answer", None))),
            question.answer.version if question.answer is not None else 0,
            question.version,
            question.updated_at,
            str(question.id),
        )
        current_rank = (
            2
            if current.answer and current.answer.content.strip()
            else int(bool(getattr(current, "effective_answer", None))),
            current.answer.version if current.answer is not None else 0,
            current.version,
            current.updated_at,
            str(current.id),
        )
        if question_rank > current_rank:
            selected[identity] = question
    return list(selected.values())


def recommendation_run_is_current(
    db: Session, run: RecommendationRun, owner_id: UUID
) -> bool:
    _, current_profile_version, _, _ = profile_context(db, owner_id)
    _, _, current_rules_fingerprint = rule_context(db, owner_id)
    questions = visible_questions(db, owner_id)
    current_content_fingerprint = content_fingerprint(
        questions, question_source_statistics(db, owner_id, questions)
    )
    return bool(
        run.profile_version == current_profile_version
        and run.rules_fingerprint == current_rules_fingerprint
        and run.content_fingerprint == current_content_fingerprint
        and run.algorithm_version == ALGORITHM_VERSION
        and run.profile_snapshot.get("project_matching_version")
        == PROJECT_MATCH_VERSION
    )
