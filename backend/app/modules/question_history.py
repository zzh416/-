"""Durable audit entries for edits to extracted questions."""
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import QuestionChange


def record_question_change(
    db: Session,
    *,
    question_id: UUID,
    owner_id: UUID,
    field: str,
    action: str,
    previous_value: str | None,
    new_value: str | None,
    entity_version: int | None = None,
    occurrence_id: UUID | None = None,
) -> QuestionChange:
    change = QuestionChange(
        question_id=question_id,
        occurrence_id=occurrence_id,
        owner_id=owner_id,
        field=field,
        action=action,
        previous_value=previous_value,
        new_value=new_value,
        entity_version=entity_version,
    )
    db.add(change)
    return change
