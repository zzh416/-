"""Versioned profile snapshots and evidence-backed resume extraction."""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.adapters.glm_profile import ExtractedProfile, extract_profile_with_glm
from app.adapters.glm_text import TextModelError, TextModelNotConfigured
from app.adapters.text_provider import get_text_model_config
from app.config import settings
from app.db import SessionLocal
from app.models import Asset, AssetVersion, ProfileSkill, ProfileVersion, UserProfile
from app.modules.technology_catalog import resolve_technology

ProfileExtractor = Callable[..., ExtractedProfile]


@dataclass(frozen=True)
class SkillSnapshot:
    display_name: str
    canonical_name: str
    category: str
    proficiency: str
    evidence: str | None
    review_status: str
    source: str


def current_profile_version(db: Session, profile: UserProfile) -> ProfileVersion | None:
    if profile.current_version < 1:
        return None
    return db.scalar(
        select(ProfileVersion)
        .where(
            ProfileVersion.profile_id == profile.id,
            ProfileVersion.version == profile.current_version,
        )
        .options(selectinload(ProfileVersion.skills))
    )


def current_skill_snapshots(db: Session, profile: UserProfile) -> list[SkillSnapshot]:
    version = current_profile_version(db, profile)
    if version is None:
        return []
    return [
        SkillSnapshot(
            display_name=item.display_name,
            canonical_name=item.canonical_name,
            category=item.category,
            proficiency=item.proficiency,
            evidence=item.evidence,
            review_status=item.review_status,
            source=item.source,
        )
        for item in sorted(version.skills, key=lambda skill: (skill.category, skill.display_name))
    ]


def create_profile_snapshot(
    db: Session,
    profile: UserProfile,
    *,
    target_role: str | None,
    skills: list[SkillSnapshot],
    resume_asset_version_id: UUID | None,
    projects: list[dict] | None = None,
) -> ProfileVersion:
    if projects is None:
        current = current_profile_version(db, profile)
        projects = current.projects if current is not None else []
    next_version = profile.current_version + 1
    version = ProfileVersion(
        profile_id=profile.id,
        version=next_version,
        resume_asset_version_id=resume_asset_version_id,
        target_role=target_role,
        projects=deepcopy(projects),
    )
    version.skills = [
        ProfileSkill(
            display_name=item.display_name,
            canonical_name=item.canonical_name,
            category=item.category,
            proficiency=item.proficiency,
            evidence=item.evidence,
            review_status=item.review_status,
            source=item.source,
        )
        for item in skills
    ]
    db.add(version)
    profile.current_version = next_version
    profile.target_role = target_role
    profile.version += 1
    return version


def _mark_failure(
    profile_id: UUID,
    expected_profile_version: int,
    status: str,
    code: str,
    message: str,
) -> None:
    with SessionLocal() as db:
        profile = db.scalar(
            select(UserProfile).where(UserProfile.id == profile_id).with_for_update()
        )
        if profile is None or profile.version != expected_profile_version:
            return
        profile.extraction_status = status
        profile.error_code = code
        profile.error_message = message[:500]
        db.commit()


def process_profile_extraction(
    profile_id: UUID,
    expected_profile_version: int,
    *,
    extractor: ProfileExtractor = extract_profile_with_glm,
) -> None:
    model_config = get_text_model_config()
    with SessionLocal() as db:
        profile = db.scalar(
            select(UserProfile).where(UserProfile.id == profile_id).with_for_update()
        )
        if (
            profile is None
            or profile.version != expected_profile_version
            or profile.extraction_status != "PROCESSING"
            or profile.resume_asset_id is None
        ):
            return
        asset = db.scalar(
            select(Asset).where(
                Asset.id == profile.resume_asset_id,
                Asset.owner_id == profile.owner_id,
                Asset.kind == "RESUME",
            )
        )
        if asset is None or asset.status not in {"READY", "PARTIAL_SUCCESS"}:
            profile.extraction_status = "FAILED"
            profile.error_code = "RESUME_NOT_READY"
            profile.error_message = "简历尚未解析完成"
            db.commit()
            return
        asset_version = db.scalar(
            select(AssetVersion).where(
                AssetVersion.asset_id == asset.id,
                AssetVersion.version == asset.current_version,
            )
        )
        if asset_version is None:
            profile.extraction_status = "FAILED"
            profile.error_code = "RESUME_VERSION_MISSING"
            profile.error_message = "简历当前版本不存在"
            db.commit()
            return
        content = asset_version.content
        asset_version_id = asset_version.id
        db.commit()

    if len(content) > settings.max_profile_extraction_chars:
        _mark_failure(
            profile_id,
            expected_profile_version,
            "FAILED",
            "PROFILE_INPUT_TOO_LARGE",
            "简历文字超过画像提取上限，请精简后重试",
        )
        return
    try:
        extracted = extractor(
            content,
            api_key=model_config.api_key,
            base_url=model_config.base_url,
            model=model_config.model,
            provider=model_config.provider,
            max_output_tokens=settings.profile_extraction_max_output_tokens,
        )
    except TextModelNotConfigured as exc:
        _mark_failure(
            profile_id,
            expected_profile_version,
            "WAITING_FOR_CONFIGURATION",
            "TEXT_MODEL_NOT_CONFIGURED",
            f"{exc}，配置后请重新提取画像",
        )
        return
    except TextModelError as exc:
        _mark_failure(profile_id, expected_profile_version, "FAILED", exc.code, str(exc))
        return
    except Exception:
        _mark_failure(
            profile_id,
            expected_profile_version,
            "FAILED",
            "PROFILE_EXTRACTION_INTERNAL_ERROR",
            "画像提取发生内部错误，请稍后重试",
        )
        return

    validation_failures = 0
    target_role = extracted.target_role
    if extracted.target_role_evidence and extracted.target_role_evidence not in content:
        target_role = None
        validation_failures += 1

    proficiency_rank = {
        "UNSPECIFIED": 0,
        "BEGINNER": 1,
        "INTERMEDIATE": 2,
        "ADVANCED": 3,
    }
    validated: dict[str, SkillSnapshot] = {}
    for item in extracted.skills:
        if item.evidence not in content:
            validation_failures += 1
            continue
        technology = resolve_technology(item.name)
        candidate = SkillSnapshot(
            display_name=technology.display_name,
            canonical_name=technology.canonical_name,
            category=technology.category,
            proficiency=item.proficiency,
            evidence=item.evidence,
            review_status="PENDING",
            source="MODEL",
        )
        existing = validated.get(candidate.canonical_name)
        if existing is None or proficiency_rank[candidate.proficiency] > proficiency_rank[
            existing.proficiency
        ]:
            validated[candidate.canonical_name] = candidate

    projects = []
    project_names = set()
    for project in extracted.projects:
        if project.evidence not in content or project.name not in project.evidence:
            validation_failures += 1
            continue
        if project.name.casefold() in project_names:
            continue
        project_names.add(project.name.casefold())
        projects.append({
            "id": str(uuid4()), "name": project.name, "description": project.evidence,
            "evidence": project.evidence, "source": "MODEL", "review_status": "PENDING",
            "resume_asset_version_id": str(asset_version_id),
        })

    with SessionLocal() as db:
        profile = db.scalar(
            select(UserProfile).where(UserProfile.id == profile_id).with_for_update()
        )
        if (
            profile is None
            or profile.version != expected_profile_version
            or profile.extraction_status != "PROCESSING"
        ):
            return
        create_profile_snapshot(
            db,
            profile,
            target_role=target_role,
            skills=list(validated.values()),
            resume_asset_version_id=asset_version_id,
            projects=projects,
        )
        profile.model = model_config.model
        profile.extraction_status = (
            "PARTIAL_SUCCESS" if validation_failures else "SUCCEEDED"
        )
        profile.error_code = "PROFILE_SOURCE_MISMATCH" if validation_failures else None
        profile.error_message = (
            f"有 {validation_failures} 个候选无法回指简历原文，其他有效技能和项目已保留"
            if validation_failures
            else None
        )
        db.commit()
