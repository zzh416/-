"""Current recommendation state assembled from shared domain services."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Question, QuestionOccurrence, RecommendationRun
from app.modules.ai_answer_service import attach_answers
from app.modules.project_matching import PROJECT_MATCH_VERSION
from app.modules.recommendation_context import (
    content_fingerprint,
    profile_context,
    rule_context,
)
from app.modules.recommendation_service import ALGORITHM_VERSION
from app.modules.source_statistics import question_source_statistics


def visible_questions(db: Session, owner_id: UUID) -> list[Question]:
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
