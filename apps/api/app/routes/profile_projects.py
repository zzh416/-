"""Project edits create profile snapshots; existing skills and history are preserved."""
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.identity import get_current_user_id
from app.modules.profile_service import create_profile_snapshot, current_profile_version, current_skill_snapshots
from app.routes.profiles import _check_editable, _profile, _profile_view, _resume_asset_version_id
from app.schemas.profiles import ProfileProjectWrite, ProfileView

router = APIRouter(prefix="/profiles/me/projects", tags=["profiles"])


def _projects(db, profile):
    current = current_profile_version(db, profile)
    return list(current.projects) if current is not None else []


def _save(db, profile, projects):
    create_profile_snapshot(
        db, profile, target_role=profile.target_role, skills=current_skill_snapshots(db, profile),
        resume_asset_version_id=_resume_asset_version_id(db, profile), projects=projects,
    )
    db.commit()
    db.refresh(profile)
    return _profile_view(db, profile)


@router.post("", response_model=ProfileView, status_code=201)
def create_project(payload: ProfileProjectWrite, db: Session = Depends(get_db), owner_id: UUID = Depends(get_current_user_id)):
    profile = _profile(db, owner_id, lock=True)
    _check_editable(profile, payload.expected_version)
    projects = _projects(db, profile)
    if len(projects) >= 20:
        raise HTTPException(status_code=409, detail="最多保存 20 个项目，请先整理已有项目")
    projects.append({
        **payload.model_dump(exclude={"expected_version"}), "id": str(uuid4()),
        "source": "USER", "evidence": None, "resume_asset_version_id": None,
    })
    return _save(db, profile, projects)


@router.put("/{project_id}", response_model=ProfileView)
def update_project(project_id: UUID, payload: ProfileProjectWrite, db: Session = Depends(get_db), owner_id: UUID = Depends(get_current_user_id)):
    profile = _profile(db, owner_id, lock=True)
    _check_editable(profile, payload.expected_version)
    projects = _projects(db, profile)
    selected = next((item for item in projects if item["id"] == str(project_id)), None)
    if selected is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    updated = {**selected, **payload.model_dump(exclude={"expected_version"})}
    if selected["name"] != payload.name or selected["description"] != payload.description:
        updated.update(source="USER", evidence=None, resume_asset_version_id=None)
    return _save(db, profile, [updated if item["id"] == str(project_id) else item for item in projects])


@router.delete("/{project_id}", response_model=ProfileView)
def delete_project(project_id: UUID, expected_version: int = Query(alias="expectedVersion", ge=1), db: Session = Depends(get_db), owner_id: UUID = Depends(get_current_user_id)):
    profile = _profile(db, owner_id, lock=True)
    _check_editable(profile, expected_version)
    projects = _projects(db, profile)
    remaining = [item for item in projects if item["id"] != str(project_id)]
    if len(remaining) == len(projects):
        raise HTTPException(status_code=404, detail="项目不存在")
    return _save(db, profile, remaining)
