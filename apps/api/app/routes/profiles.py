"""Resume profile, editable skills, and global preference rules."""
from __future__ import annotations

from pathlib import PurePath
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.db import get_db
from app.identity import get_current_user_id
from app.models import Asset, AssetVersion, PreferenceRule, ProfileSkill, ProfileVersion, UserProfile
from app.modules.ingestion_validation import classify_upload
from app.modules.profile_service import (
    SkillSnapshot,
    create_profile_snapshot,
    current_profile_version,
    current_skill_snapshots,
)
from app.modules.storage import sanitize_filename
from app.modules.technology_catalog import TECHNOLOGIES, resolve_technology
from app.routes.assets import _create_asset, _create_ingestion_asset
from app.schemas.assets import AssetSummary
from app.schemas.profiles import (
    PreferenceRuleCreate,
    PreferenceRulePage,
    PreferenceRuleUpdate,
    PreferenceRuleView,
    ProfileSkillCreate,
    ProfileSkillUpdate,
    ProfileSkillView,
    ProfileTargetRoleUpdate,
    ProfileView,
    TechnologyView,
)
from app.workers.celery_app import celery_app

router = APIRouter(tags=["profiles"])


def _profile(db: Session, owner_id: UUID, *, lock: bool = False) -> UserProfile:
    statement = select(UserProfile).where(UserProfile.owner_id == owner_id)
    if lock:
        statement = statement.with_for_update()
    profile = db.scalar(statement)
    if profile is None:
        profile = UserProfile(owner_id=owner_id)
        db.add(profile)
        db.commit()
        db.refresh(profile)
        if lock:
            profile = db.scalar(
                select(UserProfile)
                .where(UserProfile.id == profile.id)
                .with_for_update()
            )
            assert profile is not None
    return profile


def _resume_asset_version_id(db: Session, profile: UserProfile) -> UUID | None:
    if profile.resume_asset_id is None:
        return None
    asset = db.scalar(
        select(Asset).where(
            Asset.id == profile.resume_asset_id,
            Asset.owner_id == profile.owner_id,
        )
    )
    if asset is None or asset.current_version < 1:
        return None
    return db.scalar(
        select(AssetVersion.id).where(
            AssetVersion.asset_id == asset.id,
            AssetVersion.version == asset.current_version,
        )
    )


def _profile_view(db: Session, profile: UserProfile) -> ProfileView:
    resume = None
    if profile.resume_asset_id is not None:
        asset = db.scalar(
            select(Asset)
            .where(Asset.id == profile.resume_asset_id, Asset.owner_id == profile.owner_id)
            .options(selectinload(Asset.job))
        )
        if asset is not None:
            resume = AssetSummary.model_validate(asset)
    current = current_profile_version(db, profile)
    skills = current.skills if current is not None else []
    return ProfileView(
        id=profile.id,
        version=profile.version,
        current_version=profile.current_version,
        extraction_status=profile.extraction_status,
        target_role=profile.target_role,
        model=profile.model,
        error_code=profile.error_code,
        error_message=profile.error_message,
        resume=resume,
        skills=[
            ProfileSkillView.model_validate(item)
            for item in sorted(skills, key=lambda skill: (skill.category, skill.display_name))
        ],
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _check_editable(profile: UserProfile, expected_version: int) -> None:
    if profile.version != expected_version:
        raise HTTPException(status_code=409, detail="画像已被修改，请刷新后重试")
    if profile.extraction_status == "PROCESSING":
        raise HTTPException(status_code=409, detail="画像正在提取，请稍后再修改")


@router.get("/profiles/me", response_model=ProfileView)
def get_my_profile(
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> ProfileView:
    return _profile_view(db, _profile(db, owner_id))


@router.post("/profiles/me/resume", response_model=ProfileView, status_code=status.HTTP_201_CREATED)
async def upload_resume(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=8, max_length=200
    ),
) -> ProfileView:
    filename = sanitize_filename(file.filename)
    declared_type = file.content_type
    raw = await file.read(settings.max_file_import_bytes + 1)
    await file.close()
    if len(raw) > settings.max_file_import_bytes:
        raise HTTPException(status_code=413, detail="简历文件不能超过 25 MiB")
    if not raw:
        raise HTTPException(status_code=422, detail="简历文件不能为空")
    try:
        kind = classify_upload(filename, declared_type, raw)
    except ValueError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    if kind.source_format == "IMAGE" and len(raw) > settings.max_ocr_image_bytes:
        raise HTTPException(status_code=413, detail="OCR 图片不能超过 10 MiB")
    title = PurePath(filename).stem or "我的简历"
    if kind.asynchronous:
        asset = _create_ingestion_asset(
            db,
            owner_id,
            None,
            title=title,
            source_format=kind.source_format,
            original_filename=filename,
            content_type=kind.content_type,
            content=raw,
            source_url=None,
            idempotency_key=idempotency_key,
            asset_kind="RESUME",
        )
    else:
        if len(raw) > settings.max_text_import_bytes:
            raise HTTPException(status_code=413, detail="文字简历不能超过 1 MiB")
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=422, detail="文字简历必须使用 UTF-8 编码") from exc
        if not content.strip():
            raise HTTPException(status_code=422, detail="简历内容不能为空")
        asset = _create_asset(
            db,
            owner_id,
            None,
            title=title,
            content=content,
            source_format=kind.source_format,
            original_filename=filename,
            idempotency_key=idempotency_key,
            asset_kind="RESUME",
        )
    profile = _profile(db, owner_id, lock=True)
    profile.resume_asset_id = asset.id
    profile.extraction_status = "IDLE"
    profile.error_code = None
    profile.error_message = None
    profile.version += 1
    db.commit()
    db.refresh(profile)
    return _profile_view(db, profile)


@router.post("/profiles/me/extraction", response_model=ProfileView, status_code=status.HTTP_202_ACCEPTED)
def start_profile_extraction(
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> ProfileView:
    profile = _profile(db, owner_id, lock=True)
    if profile.extraction_status == "PROCESSING":
        return _profile_view(db, profile)
    if profile.resume_asset_id is None:
        raise HTTPException(status_code=409, detail="请先上传简历")
    asset = db.scalar(
        select(Asset).where(
            Asset.id == profile.resume_asset_id,
            Asset.owner_id == owner_id,
            Asset.kind == "RESUME",
        )
    )
    if asset is None:
        raise HTTPException(status_code=409, detail="简历资料不存在")
    if asset.status not in {"READY", "PARTIAL_SUCCESS"} or asset.current_version < 1:
        raise HTTPException(status_code=409, detail="简历尚未解析完成")
    profile.extraction_status = "PROCESSING"
    profile.model = settings.glm_text_model
    profile.error_code = None
    profile.error_message = None
    profile.version += 1
    expected_version = profile.version
    db.commit()
    try:
        celery_app.send_task(
            "app.workers.tasks.process_profile_extraction",
            args=[str(profile.id), expected_version],
        )
    except Exception:
        profile = _profile(db, owner_id, lock=True)
        if profile.version == expected_version:
            profile.extraction_status = "FAILED"
            profile.error_code = "QUEUE_UNAVAILABLE"
            profile.error_message = "后台画像提取服务暂时不可用，请稍后重试"
            db.commit()
    return _profile_view(db, _profile(db, owner_id))


@router.put("/profiles/me/target-role", response_model=ProfileView)
def update_target_role(
    payload: ProfileTargetRoleUpdate,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> ProfileView:
    profile = _profile(db, owner_id, lock=True)
    _check_editable(profile, payload.expected_version)
    create_profile_snapshot(
        db,
        profile,
        target_role=payload.target_role,
        skills=current_skill_snapshots(db, profile),
        resume_asset_version_id=_resume_asset_version_id(db, profile),
    )
    db.commit()
    db.refresh(profile)
    return _profile_view(db, profile)


@router.post("/profiles/me/skills", response_model=ProfileView, status_code=status.HTTP_201_CREATED)
def add_profile_skill(
    payload: ProfileSkillCreate,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> ProfileView:
    profile = _profile(db, owner_id, lock=True)
    _check_editable(profile, payload.expected_version)
    skills = current_skill_snapshots(db, profile)
    technology = resolve_technology(payload.display_name)
    if any(item.canonical_name == technology.canonical_name for item in skills):
        raise HTTPException(status_code=409, detail="该技能已存在")
    skills.append(
        SkillSnapshot(
            display_name=technology.display_name,
            canonical_name=technology.canonical_name,
            category=technology.category,
            proficiency=payload.proficiency,
            evidence=None,
            review_status="CONFIRMED",
            source="USER",
        )
    )
    create_profile_snapshot(
        db,
        profile,
        target_role=profile.target_role,
        skills=skills,
        resume_asset_version_id=_resume_asset_version_id(db, profile),
    )
    db.commit()
    db.refresh(profile)
    return _profile_view(db, profile)


def _current_skill(db: Session, profile: UserProfile, skill_id: UUID) -> ProfileSkill:
    version = current_profile_version(db, profile)
    if version is None:
        raise HTTPException(status_code=404, detail="技能不存在")
    skill = next((item for item in version.skills if item.id == skill_id), None)
    if skill is None:
        raise HTTPException(status_code=404, detail="技能不存在")
    return skill


@router.put("/profiles/me/skills/{skill_id}", response_model=ProfileView)
def update_profile_skill(
    skill_id: UUID,
    payload: ProfileSkillUpdate,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> ProfileView:
    profile = _profile(db, owner_id, lock=True)
    _check_editable(profile, payload.expected_version)
    selected = _current_skill(db, profile, skill_id)
    technology = resolve_technology(payload.display_name)
    skills = current_skill_snapshots(db, profile)
    if any(
        item.canonical_name == technology.canonical_name
        and item.canonical_name != selected.canonical_name
        for item in skills
    ):
        raise HTTPException(status_code=409, detail="该技能已存在")
    updated = [
        SkillSnapshot(
            display_name=technology.display_name,
            canonical_name=technology.canonical_name,
            category=technology.category,
            proficiency=payload.proficiency,
            evidence=item.evidence,
            review_status=payload.review_status,
            source=item.source,
        )
        if item.canonical_name == selected.canonical_name
        else item
        for item in skills
    ]
    create_profile_snapshot(
        db,
        profile,
        target_role=profile.target_role,
        skills=updated,
        resume_asset_version_id=_resume_asset_version_id(db, profile),
    )
    db.commit()
    db.refresh(profile)
    return _profile_view(db, profile)


@router.delete("/profiles/me/skills/{skill_id}", response_model=ProfileView)
def delete_profile_skill(
    skill_id: UUID,
    expected_version: int = Query(alias="expectedVersion", ge=1),
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> ProfileView:
    profile = _profile(db, owner_id, lock=True)
    _check_editable(profile, expected_version)
    selected = _current_skill(db, profile, skill_id)
    skills = [
        item
        for item in current_skill_snapshots(db, profile)
        if item.canonical_name != selected.canonical_name
    ]
    create_profile_snapshot(
        db,
        profile,
        target_role=profile.target_role,
        skills=skills,
        resume_asset_version_id=_resume_asset_version_id(db, profile),
    )
    db.commit()
    db.refresh(profile)
    return _profile_view(db, profile)


@router.get("/technologies", response_model=list[TechnologyView])
def list_technologies() -> list[TechnologyView]:
    return [
        TechnologyView(
            canonical_name=item.canonical_name,
            display_name=item.display_name,
            category=item.category,
            aliases=list(item.aliases),
            parent=item.parent,
        )
        for item in TECHNOLOGIES
    ]


@router.get("/preference-rules", response_model=PreferenceRulePage)
def list_preference_rules(
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> PreferenceRulePage:
    items = db.scalars(
        select(PreferenceRule)
        .where(PreferenceRule.owner_id == owner_id, PreferenceRule.deleted_at.is_(None))
        .order_by(PreferenceRule.created_at, PreferenceRule.id)
    ).all()
    return PreferenceRulePage(
        items=[PreferenceRuleView.model_validate(item) for item in items]
    )


def _duplicate_rule(
    db: Session,
    owner_id: UUID,
    rule_type: str,
    canonical: str,
    scope: str,
    *,
    exclude_id: UUID | None = None,
) -> bool:
    statement = select(PreferenceRule.id).where(
        PreferenceRule.owner_id == owner_id,
        PreferenceRule.deleted_at.is_(None),
        PreferenceRule.rule_type == rule_type,
        PreferenceRule.canonical_technology == canonical,
        PreferenceRule.scope == scope,
    )
    if exclude_id is not None:
        statement = statement.where(PreferenceRule.id != exclude_id)
    return db.scalar(statement) is not None


@router.post(
    "/preference-rules",
    response_model=PreferenceRuleView,
    status_code=status.HTTP_201_CREATED,
)
def create_preference_rule(
    payload: PreferenceRuleCreate,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> PreferenceRule:
    technology = resolve_technology(payload.technology)
    if _duplicate_rule(
        db, owner_id, payload.rule_type, technology.canonical_name, payload.scope
    ):
        raise HTTPException(status_code=409, detail="相同偏好规则已存在")
    rule = PreferenceRule(
        owner_id=owner_id,
        rule_type=payload.rule_type,
        technology=technology.display_name,
        canonical_technology=technology.canonical_name,
        category=technology.category,
        scope=payload.scope,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def _owned_rule(
    db: Session, rule_id: UUID, owner_id: UUID, *, lock: bool = False
) -> PreferenceRule:
    statement = select(PreferenceRule).where(
        PreferenceRule.id == rule_id,
        PreferenceRule.owner_id == owner_id,
        PreferenceRule.deleted_at.is_(None),
    )
    if lock:
        statement = statement.with_for_update()
    rule = db.scalar(statement)
    if rule is None:
        raise HTTPException(status_code=404, detail="偏好规则不存在")
    return rule


@router.put("/preference-rules/{rule_id}", response_model=PreferenceRuleView)
def update_preference_rule(
    rule_id: UUID,
    payload: PreferenceRuleUpdate,
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> PreferenceRule:
    rule = _owned_rule(db, rule_id, owner_id, lock=True)
    if rule.version != payload.expected_version:
        raise HTTPException(status_code=409, detail="偏好规则已被修改，请刷新后重试")
    technology = resolve_technology(payload.technology)
    if _duplicate_rule(
        db,
        owner_id,
        payload.rule_type,
        technology.canonical_name,
        payload.scope,
        exclude_id=rule.id,
    ):
        raise HTTPException(status_code=409, detail="相同偏好规则已存在")
    rule.rule_type = payload.rule_type
    rule.technology = technology.display_name
    rule.canonical_technology = technology.canonical_name
    rule.category = technology.category
    rule.scope = payload.scope
    rule.is_active = payload.is_active
    rule.version += 1
    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/preference-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_preference_rule(
    rule_id: UUID,
    expected_version: int = Query(alias="expectedVersion", ge=1),
    db: Session = Depends(get_db),
    owner_id: UUID = Depends(get_current_user_id),
) -> None:
    rule = _owned_rule(db, rule_id, owner_id, lock=True)
    if rule.version != expected_version:
        raise HTTPException(status_code=409, detail="偏好规则已被修改，请刷新后重试")
    from datetime import datetime, timezone

    rule.deleted_at = datetime.now(timezone.utc)
    rule.version += 1
    db.commit()
