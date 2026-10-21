from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.adapters.glm_profile import ExtractedProfile, ExtractedProfileSkill
from app.db import SessionLocal
from app.identity import get_current_user_id
from app.main import app
from app.models import User
from app.modules.profile_service import process_profile_extraction

client = TestClient(app)


def _test_user() -> UUID:
    owner_id = uuid4()
    with SessionLocal() as db:
        db.add(User(id=owner_id, display_name="画像测试用户"))
        db.commit()
    app.dependency_overrides[get_current_user_id] = lambda: owner_id
    return owner_id


def _delete_user(owner_id: UUID) -> None:
    app.dependency_overrides.clear()
    with SessionLocal() as db:
        user = db.get(User, owner_id)
        if user is not None:
            db.delete(user)
            db.commit()


def test_resume_profile_extraction_and_versioned_skill_edits(monkeypatch) -> None:
    owner_id = _test_user()
    resume_text = "目标岗位：Python 后端工程师\n熟练使用 Python 和 FastAPI，了解 MySQL。"
    try:
        empty = client.get("/api/v1/profiles/me")
        assert empty.status_code == 200
        assert empty.json()["extractionStatus"] == "EMPTY"
        assert empty.json()["skills"] == []

        uploaded = client.post(
            "/api/v1/profiles/me/resume",
            headers={"Idempotency-Key": f"resume-{uuid4()}"},
            files={"file": ("resume.txt", resume_text.encode(), "text/plain")},
        )
        assert uploaded.status_code == 201
        uploaded_payload = uploaded.json()
        assert uploaded_payload["resume"]["collectionId"] is None
        assert uploaded_payload["resume"]["status"] == "READY"
        assert uploaded_payload["extractionStatus"] == "IDLE"

        monkeypatch.setattr(
            "app.routes.profiles.celery_app.send_task",
            lambda *args, **kwargs: SimpleNamespace(id="profile-task"),
        )
        started = client.post("/api/v1/profiles/me/extraction")
        assert started.status_code == 202
        started_payload = started.json()
        assert started_payload["extractionStatus"] == "PROCESSING"

        def fake_extractor(content, **kwargs):
            assert content == resume_text
            return ExtractedProfile(
                target_role="Python 后端工程师",
                target_role_evidence="目标岗位：Python 后端工程师",
                skills=[
                    ExtractedProfileSkill(
                        name="Python",
                        proficiency="ADVANCED",
                        evidence="熟练使用 Python 和 FastAPI",
                    ),
                    ExtractedProfileSkill(
                        name="FastAPI",
                        proficiency="UNSPECIFIED",
                        evidence="熟练使用 Python 和 FastAPI",
                    ),
                    ExtractedProfileSkill(
                        name="MySQL",
                        proficiency="BEGINNER",
                        evidence="了解 MySQL",
                    ),
                ],
            )

        process_profile_extraction(
            UUID(started_payload["id"]),
            started_payload["version"],
            extractor=fake_extractor,
        )
        extracted = client.get("/api/v1/profiles/me").json()
        assert extracted["extractionStatus"] == "SUCCEEDED"
        assert extracted["targetRole"] == "Python 后端工程师"
        assert extracted["currentVersion"] == 1
        assert {item["canonicalName"] for item in extracted["skills"]} == {
            "python",
            "fastapi",
            "mysql",
        }
        assert all(item["reviewStatus"] == "PENDING" for item in extracted["skills"])

        python_skill = next(
            item for item in extracted["skills"] if item["canonicalName"] == "python"
        )
        confirmed = client.put(
            f"/api/v1/profiles/me/skills/{python_skill['id']}",
            json={
                "displayName": "Python",
                "proficiency": "ADVANCED",
                "reviewStatus": "CONFIRMED",
                "expectedVersion": extracted["version"],
            },
        )
        assert confirmed.status_code == 200
        confirmed_payload = confirmed.json()
        assert confirmed_payload["currentVersion"] == 2
        assert next(
            item
            for item in confirmed_payload["skills"]
            if item["canonicalName"] == "python"
        )["reviewStatus"] == "CONFIRMED"

        added = client.post(
            "/api/v1/profiles/me/skills",
            json={
                "displayName": "Docker",
                "proficiency": "INTERMEDIATE",
                "expectedVersion": confirmed_payload["version"],
            },
        )
        assert added.status_code == 201
        added_payload = added.json()
        docker_skill = next(
            item for item in added_payload["skills"] if item["canonicalName"] == "docker"
        )
        assert docker_skill["source"] == "USER"
        assert docker_skill["reviewStatus"] == "CONFIRMED"

        deleted = client.delete(
            f"/api/v1/profiles/me/skills/{docker_skill['id']}",
            params={"expectedVersion": added_payload["version"]},
        )
        assert deleted.status_code == 200
        assert "docker" not in {
            item["canonicalName"] for item in deleted.json()["skills"]
        }
    finally:
        _delete_user(owner_id)


def test_profile_rejects_untraceable_model_candidates(monkeypatch) -> None:
    owner_id = _test_user()
    try:
        uploaded = client.post(
            "/api/v1/profiles/me/resume",
            files={"file": ("resume.txt", "只会 Python".encode(), "text/plain")},
        )
        assert uploaded.status_code == 201
        monkeypatch.setattr(
            "app.routes.profiles.celery_app.send_task",
            lambda *args, **kwargs: SimpleNamespace(id="profile-task"),
        )
        started = client.post("/api/v1/profiles/me/extraction").json()

        def fake_extractor(content, **kwargs):
            return ExtractedProfile(
                skills=[
                    ExtractedProfileSkill(
                        name="Java",
                        proficiency="ADVANCED",
                        evidence="精通 Java",
                    ),
                    ExtractedProfileSkill(
                        name="Python",
                        proficiency="UNSPECIFIED",
                        evidence="只会 Python",
                    ),
                ]
            )

        process_profile_extraction(
            UUID(started["id"]), started["version"], extractor=fake_extractor
        )
        profile = client.get("/api/v1/profiles/me").json()
        assert profile["extractionStatus"] == "PARTIAL_SUCCESS"
        assert [item["canonicalName"] for item in profile["skills"]] == ["python"]
        assert "1 个候选" in profile["errorMessage"]
    finally:
        _delete_user(owner_id)


def test_profile_target_role_and_custom_skill_use_optimistic_versions() -> None:
    owner_id = _test_user()
    try:
        profile = client.get("/api/v1/profiles/me").json()
        updated = client.put(
            "/api/v1/profiles/me/target-role",
            json={
                "targetRole": "AI Agent 工程师",
                "expectedVersion": profile["version"],
            },
        )
        assert updated.status_code == 200
        updated_payload = updated.json()
        assert updated_payload["targetRole"] == "AI Agent 工程师"
        assert updated_payload["currentVersion"] == 1

        stale = client.put(
            "/api/v1/profiles/me/target-role",
            json={"targetRole": "后端工程师", "expectedVersion": profile["version"]},
        )
        assert stale.status_code == 409

        custom = client.post(
            "/api/v1/profiles/me/skills",
            json={
                "displayName": "消息队列",
                "proficiency": "INTERMEDIATE",
                "expectedVersion": updated_payload["version"],
            },
        )
        assert custom.status_code == 201
        skill = next(
            item for item in custom.json()["skills"] if item["displayName"] == "消息队列"
        )
        assert skill["canonicalName"] == "消息队列"
        assert skill["category"] == "OTHER"
    finally:
        _delete_user(owner_id)


def test_preference_rule_crud_canonicalizes_technology_and_isolates_owner() -> None:
    owner_id = _test_user()
    other_owner_id = uuid4()
    try:
        technologies = client.get("/api/v1/technologies")
        assert technologies.status_code == 200
        mysql = next(
            item for item in technologies.json() if item["canonicalName"] == "mysql"
        )
        assert mysql["parent"] == "database"
        assert mysql["category"] == "DATABASE_PRODUCT"

        created = client.post(
            "/api/v1/preference-rules",
            json={"ruleType": "EXCLUDE", "technology": "mysql", "scope": "BOTH"},
        )
        assert created.status_code == 201
        rule = created.json()
        assert rule["technology"] == "MySQL"
        assert rule["canonicalTechnology"] == "mysql"

        duplicate = client.post(
            "/api/v1/preference-rules",
            json={"ruleType": "EXCLUDE", "technology": "MySQL", "scope": "BOTH"},
        )
        assert duplicate.status_code == 409

        updated = client.put(
            f"/api/v1/preference-rules/{rule['id']}",
            json={
                "ruleType": "EXCLUDE",
                "technology": "MySQL",
                "scope": "BOTH",
                "isActive": False,
                "expectedVersion": rule["version"],
            },
        )
        assert updated.status_code == 200
        assert updated.json()["isActive"] is False

        with SessionLocal() as db:
            db.add(User(id=other_owner_id, display_name="另一个画像用户"))
            db.commit()
        app.dependency_overrides[get_current_user_id] = lambda: other_owner_id
        assert client.get("/api/v1/preference-rules").json()["items"] == []
        assert (
            client.put(
                f"/api/v1/preference-rules/{rule['id']}",
                json={
                    "ruleType": "EXCLUDE",
                    "technology": "MySQL",
                    "scope": "BOTH",
                    "isActive": True,
                    "expectedVersion": 2,
                },
            ).status_code
            == 404
        )

        app.dependency_overrides[get_current_user_id] = lambda: owner_id
        deleted = client.delete(
            f"/api/v1/preference-rules/{rule['id']}",
            params={"expectedVersion": updated.json()["version"]},
        )
        assert deleted.status_code == 204
        assert client.get("/api/v1/preference-rules").json()["items"] == []
    finally:
        app.dependency_overrides.clear()
        with SessionLocal() as db:
            for user_id in (owner_id, other_owner_id):
                user = db.get(User, user_id)
                if user is not None:
                    db.delete(user)
            db.commit()
