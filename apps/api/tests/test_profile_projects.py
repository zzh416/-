from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.identity import get_current_user_id
from app.main import app
from app.models import ProfileVersion, User

client = TestClient(app)


@pytest.fixture
def owner():
    owner_id = uuid4()
    with SessionLocal() as db:
        db.add(User(id=owner_id, display_name="项目测试"))
        db.commit()
    app.dependency_overrides[get_current_user_id] = lambda: owner_id
    yield owner_id
    app.dependency_overrides.clear()
    with SessionLocal() as db:
        db.delete(db.get(User, owner_id))
        db.commit()


def add_project(profile=None, **changes):
    profile = profile or client.get("/api/v1/profiles/me").json()
    response = client.post("/api/v1/profiles/me/projects", json={
        "name": "知识库助手", "description": "使用 Python、LangGraph、RAG 与向量检索", "reviewStatus": "CONFIRMED",
        "expectedVersion": profile["version"], **changes,
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_projects_versioned_crud_preserved_across_other_profile_edits(owner):
    profile = add_project()
    project = profile["projects"][0]
    assert project["source"] == "USER" and project["evidence"] is None
    first_version = profile["currentVersion"]
    updated = client.put("/api/v1/profiles/me/target-role", json={"targetRole": "AI 开发", "expectedVersion": profile["version"]}).json()
    assert updated["projects"] == profile["projects"]
    path = f"/api/v1/profiles/me/projects/{project['id']}"
    payload = {"name": project["name"], "description": "Redis 缓存服务", "reviewStatus": "REJECTED", "expectedVersion": updated["version"]}
    changed = client.put(path, json=payload)
    assert changed.status_code == 200
    assert changed.json()["projects"][0]["reviewStatus"] == "REJECTED"
    assert client.put(path, json=payload).status_code == 409
    with SessionLocal() as db:
        saved = db.query(ProfileVersion).filter_by(profile_id=UUID(profile["id"]), version=first_version).one()
        assert saved.projects[0]["description"].startswith("使用 Python")
    removed = client.delete(path, params={"expectedVersion": changed.json()["version"]})
    assert removed.status_code == 200 and removed.json()["projects"] == []


def test_project_owner_isolation_and_input_validation(owner):
    profile = add_project()
    project_id = profile["projects"][0]["id"]
    other = uuid4()
    with SessionLocal() as db:
        db.add(User(id=other, display_name="外部项目"))
        db.commit()
    try:
        app.dependency_overrides[get_current_user_id] = lambda: other
        foreign = client.get("/api/v1/profiles/me").json()
        assert foreign["projects"] == []
        payload = {"name": "试图修改", "description": "外部", "reviewStatus": "CONFIRMED", "expectedVersion": foreign["version"]}
        assert client.put(f"/api/v1/profiles/me/projects/{project_id}", json=payload).status_code == 404
        assert client.delete(f"/api/v1/profiles/me/projects/{project_id}", params={"expectedVersion": foreign["version"]}).status_code == 404
        payload["name"] = "  "
        assert client.post("/api/v1/profiles/me/projects", json=payload).status_code == 422
    finally:
        app.dependency_overrides[get_current_user_id] = lambda: owner
        with SessionLocal() as db:
            db.delete(db.get(User, other))
            db.commit()


def test_project_limit_and_extraction_lock(owner):
    profile = client.get("/api/v1/profiles/me").json()
    for index in range(20):
        profile = add_project(profile, name=f"项目{index}")
    payload = {"name": "超限项目", "description": "RAG", "expectedVersion": profile["version"]}
    assert client.post("/api/v1/profiles/me/projects", json=payload).status_code == 409
    from app.models import UserProfile
    with SessionLocal() as db:
        db.get(UserProfile, UUID(profile["id"])).extraction_status = "PROCESSING"
        db.commit()
    assert client.post("/api/v1/profiles/me/projects", json=payload).status_code == 409


def test_model_projects_require_exact_evidence_and_confirmation(owner, monkeypatch):
    from app.adapters.glm_profile import ExtractedProfile, ExtractedProfileProject
    from app.modules.profile_service import process_profile_extraction
    content = "项目：知识库助手。使用 Python 和 RAG 做向量检索。"
    upload = client.post("/api/v1/profiles/me/resume", files={"file": ("resume.txt", content.encode(), "text/plain")})
    assert upload.status_code == 201
    monkeypatch.setattr("app.routes.profiles.celery_app.send_task", lambda *args, **kwargs: None)
    started = client.post("/api/v1/profiles/me/extraction").json()
    def extractor(*args, **kwargs):
        return ExtractedProfile(projects=[
            ExtractedProfileProject(name="知识库助手", evidence=content),
            ExtractedProfileProject(name="虚构项目", evidence=content),
            ExtractedProfileProject(name="Java服务", evidence="Java服务使用 Spring"),
        ])
    process_profile_extraction(UUID(started["id"]), started["version"], extractor=extractor)
    profile = client.get("/api/v1/profiles/me").json()
    assert profile["extractionStatus"] == "PARTIAL_SUCCESS"
    assert len(profile["projects"]) == 1
    project = profile["projects"][0]
    assert project["source"] == "MODEL" and project["reviewStatus"] == "PENDING"
    assert project["evidence"] == content and project["resumeAssetVersionId"]
    path = f"/api/v1/profiles/me/projects/{project['id']}"
    updated = client.put(path, json={"name": project["name"], "description": project["description"], "reviewStatus": "CONFIRMED", "expectedVersion": profile["version"]}).json()
    assert updated["projects"][0]["evidence"] == content
    edited = client.put(path, json={"name": "人工项目", "description": "人工添加 Redis 缓存", "reviewStatus": "CONFIRMED", "expectedVersion": updated["version"]}).json()
    assert edited["projects"][0]["source"] == "USER"
    assert edited["projects"][0]["evidence"] is None


def test_project_links_preserve_filters_snapshots_and_full_pagination(owner):
    collection = client.post("/api/v1/collections", json={"name": "关联验收", "purpose": "KNOWLEDGE_BASE"}).json()
    prompts = [f"RAG 的第 {index} 个检索问题是什么？" for index in range(25)]
    prompts += ["什么是进程和线程？", "接口如何设计？", "Java 和 RAG 如何集成？"]
    for prompt in prompts:
        payload = {"collectionId": collection["id"], "prompt": prompt}
        if prompt == "接口如何设计？":
            payload["answer"] = "可以使用 RAG 做检索。"
        assert client.post("/api/v1/questions", json=payload).status_code == 201
    client.post("/api/v1/preference-rules", json={"ruleType": "EXCLUDE", "technology": "Java", "scope": "BOTH"})
    baseline = client.post("/api/v1/recommendation-runs", json={}).json()
    base_items = client.get(f"/api/v1/recommendation-runs/{baseline['id']}/items?pageSize=100").json()["items"]
    profile = add_project(description="RAG 向量检索")
    project = profile["projects"][0]
    profile = add_project(profile, name="待确认项目", description="RAG", reviewStatus="PENDING")
    run = client.post("/api/v1/recommendation-runs", json={}).json()
    path = f"/api/v1/recommendation-runs/{run['id']}"
    items = client.get(f"{path}/items?pageSize=100").json()["items"]
    assert [(item["questionId"], item["decision"], item["score"], item["usableAnswerState"]) for item in items] == [
        (item["questionId"], item["decision"], item["score"], item["usableAnswerState"]) for item in base_items
    ]
    assert run["includedCount"] == 27 and run["excludedCount"] == 1
    linked = [item for item in items if item["projectMatches"]]
    assert len(linked) == 25
    assert all(item["projectMatches"][0]["projectId"] == project["id"] for item in linked)
    answer_only = next(item for item in items if item["prompt"] == "接口如何设计？")
    assert answer_only["projectMatches"] == []
    assert next(item for item in items if item["decision"] == "EXCLUDED")["projectMatches"] == []
    first = client.get(f"{path}/items", params={"projectId": project["id"], "pageSize": 20}).json()
    second = client.get(f"{path}/items", params={"projectId": project["id"], "page": 2}).json()
    assert first["total"] == 25 and len(first["items"]) == 20 and len(second["items"]) == 5
    unmatched = client.get(f"{path}/items", params={"unmatchedOnly": True}).json()
    assert unmatched["total"] == 2
    assert client.get(f"{path}/items", params={"projectId": project["id"], "unmatchedOnly": True}).status_code == 422
    assert client.get(f"{path}/items", params={"projectId": str(uuid4())}).json()["total"] == 0
    assert "共有主题：RAG" in client.get(f"{path}/export").text
    updated = client.put(f"/api/v1/profiles/me/projects/{project['id']}", json={
        "name": "缓存项目", "description": "Redis 缓存", "expectedVersion": profile["version"], "reviewStatus": "CONFIRMED",
    })
    assert updated.status_code == 200
    assert client.get(path).json()["isCurrent"] is False
    historical = client.get(f"{path}/items", params={"projectId": project["id"]}).json()
    assert historical["total"] == 25 and historical["items"][0]["projectMatches"][0]["projectName"] == "知识库助手"
    export = client.get(f"{path}/export").text
    assert "历史清单" in export and "知识库助手" in export and "缓存项目" not in export


def test_legacy_run_marks_missing_project_information(owner):
    from app.models import RecommendationItem, RecommendationRun
    profile = add_project()
    collection = client.post("/api/v1/collections", json={"name": "旧清单", "purpose": "KNOWLEDGE_BASE"}).json()
    client.post("/api/v1/questions", json={"collectionId": collection["id"], "prompt": "RAG 是什么？"})
    run = client.post("/api/v1/recommendation-runs", json={}).json()
    with SessionLocal() as db:
        saved = db.get(RecommendationRun, UUID(run["id"]))
        saved.profile_snapshot = {key: value for key, value in saved.profile_snapshot.items() if key not in {"projects", "project_matching_version"}}
        db.query(RecommendationItem).filter_by(run_id=saved.id).one().project_matches = None
        db.commit()
    path = f"/api/v1/recommendation-runs/{run['id']}"
    assert client.get(path).json()["isCurrent"] is False
    assert client.get(f"{path}/items?unmatchedOnly=true").json()["total"] == 0
    assert "项目关联：历史清单未记录" in client.get(f"{path}/export").text
