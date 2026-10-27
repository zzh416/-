"""Claim jobs once; validate all generated content against immutable owner-scoped inputs."""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from app.adapters.glm_text import TextModelError, TextModelNotConfigured
from app.adapters.project_followups import generate_project_followups
from app.adapters.text_provider import get_text_model_config
from app.db import SessionLocal
from app.models import ProjectFollowupRun, RecommendationRun, RecommendationItem
from app.modules.followup_validation import validate_followups
from app.modules.recommendation_state import recommendation_run_is_current
from app.modules.recommendation_service import PreferenceSnapshot, SkillSnapshot

FOLLOWUP_VERSION = "project-followups-v1"


def inputs_current(db, run):
    recommendation = db.get(RecommendationRun, run.recommendation_run_id)
    return bool(recommendation and recommendation.owner_id == run.owner_id and
                run.algorithm_version == FOLLOWUP_VERSION and
                recommendation_run_is_current(db, recommendation, run.owner_id))


def retry_allowed(run):
    expired = run.status in {"QUEUED", "PROCESSING"} and (datetime.now(timezone.utc) - run.updated_at).total_seconds() > 600
    return run.retry_count < 3 and (run.status in {"FAILED", "WAITING_FOR_CONFIGURATION"} or expired)


def _finish(run_id, attempt, *, status, code=None, message=None, items=None, rejections=None):
    with SessionLocal() as db:
        run = db.scalar(select(ProjectFollowupRun).where(ProjectFollowupRun.id == run_id).with_for_update())
        if run is None or run.status != "PROCESSING" or run.retry_count != attempt:
            return
        if not inputs_current(db, run):
            status, code, message, items = "FAILED", "FOLLOWUP_INPUT_STALE", "画像、偏好或题库已变化，请重新生成清单及追问", []
        run.status, run.error_code, run.error_message = status, code, message
        run.items, run.rejection_counts = items or [], rejections or {}
        run.finished_at = datetime.now(timezone.utc)
        db.commit()


def process_project_followup(run_id: UUID, *, generator=generate_project_followups):
    config = get_text_model_config()
    with SessionLocal() as db:
        run = db.scalar(select(ProjectFollowupRun).where(ProjectFollowupRun.id == run_id).with_for_update())
        if run is None or run.status != "QUEUED":
            return
        run.status, run.started_at = "PROCESSING", datetime.now(timezone.utc)
        run.provider, run.model = config.provider, config.model
        attempt = run.retry_count
        if not inputs_current(db, run):
            run.status, run.error_code = "FAILED", "FOLLOWUP_INPUT_STALE"
            run.error_message = "生成依据已变化，请重新生成清单后再试"
            db.commit()
            return
        project, seeds = run.project_snapshot, run.seeds
        recommendation = db.get(RecommendationRun, run.recommendation_run_id)
        existing_prompts = db.scalars(select(RecommendationItem.prompt).where(RecommendationItem.run_id == run.recommendation_run_id)).all()
        skills = [SkillSnapshot(**item) for item in recommendation.profile_snapshot["skills"]]
        rules = [PreferenceSnapshot(item["rule_type"], item["canonical_technology"], item["technology"], item["category"], item["scope"]) for item in recommendation.rule_snapshot]
        db.commit()
    payload = {
        "project": {"name": project["name"], "description": project["description"]},
        "seeds": [{"id": item["id"], "prompt": item["prompt"]} for item in seeds],
        "confirmed_skills": [skill.canonical_name for skill in skills if skill.review_status == "CONFIRMED"],
        "excluded_technologies": [rule.technology for rule in rules if rule.rule_type == "EXCLUDE" and rule.scope in {"QUESTION", "BOTH"}],
    }
    try:
        candidates = generator(payload, api_key=config.api_key, base_url=config.base_url, model=config.model, provider=config.provider)
        items, rejected = validate_followups(project, seeds, candidates, skills=skills, rules=rules, existing_prompts=existing_prompts)
        if not items:
            _finish(run_id, attempt, status="FAILED", code="FOLLOWUP_NO_VALID_ITEMS", message="没有通过依据与筛选校验的追问，可更换种子题重试", rejections=rejected)
        else:
            _finish(run_id, attempt, status="PARTIAL_SUCCESS" if rejected else "SUCCEEDED", items=items, rejections=rejected)
    except TextModelNotConfigured:
        _finish(run_id, attempt, status="WAITING_FOR_CONFIGURATION", code="TEXT_MODEL_NOT_CONFIGURED", message="请先配置文本模型 API Key，再重试追问")
    except TextModelError as exc:
        _finish(run_id, attempt, status="FAILED", code=exc.code, message=str(exc))
    except Exception:
        _finish(run_id, attempt, status="FAILED", code="FOLLOWUP_INTERNAL_ERROR", message="追问生成出现内部错误，请稍后重试")
