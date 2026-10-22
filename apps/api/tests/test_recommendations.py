from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.identity import get_current_user_id
from app.main import app
from app.models import User


client = TestClient(app)


def _test_user() -> UUID:
    owner_id = uuid4()
    with SessionLocal() as db:
        db.add(User(id=owner_id, display_name="推荐测试用户"))
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


def _create_question(collection_id: str, prompt: str, answer: str | None = None) -> dict:
    payload = {"collectionId": collection_id, "prompt": prompt}
    if answer is not None:
        payload["answer"] = answer
    response = client.post("/api/v1/questions", json=payload)
    assert response.status_code == 201
    return response.json()


def test_recommendation_run_is_explainable_versioned_idempotent_and_owner_scoped() -> None:
    owner_id = _test_user()
    other_owner_id = uuid4()
    try:
        collection = client.post(
            "/api/v1/collections",
            json={"name": f"推荐测试-{uuid4()}", "purpose": "KNOWLEDGE_BASE"},
        ).json()
        java_question = _create_question(
            collection["id"], "Java 的 HashMap 为什么线程不安全？"
        )
        database_question = _create_question(
            collection["id"],
            "数据库事务的 ACID 是什么？",
            "ACID 指原子性、一致性、隔离性和持久性。",
        )
        answer_mismatch = _create_question(
            collection["id"],
            "如何避免并发修改导致的数据竞争？",
            "可以使用 Java synchronized 保护临界区。",
        )
        mysql_question = _create_question(
            collection["id"], "MySQL 的索引下推是什么？", "索引下推可以减少回表次数。"
        )

        profile = client.get("/api/v1/profiles/me").json()
        profile = client.post(
            "/api/v1/profiles/me/skills",
            json={
                "displayName": "Python",
                "proficiency": "ADVANCED",
                "expectedVersion": profile["version"],
            },
        ).json()
        mysql_rule = client.post(
            "/api/v1/preference-rules",
            json={"ruleType": "EXCLUDE", "technology": "MySQL", "scope": "BOTH"},
        ).json()

        headers = {"Idempotency-Key": f"recommendation-{uuid4()}"}
        created = client.post("/api/v1/recommendation-runs", headers=headers, json={})
        assert created.status_code == 201
        run = created.json()
        assert run["status"] == "SUCCEEDED"
        assert run["profileVersion"] == profile["currentVersion"]
        assert run["algorithmVersion"] == "p3.5-v3"
        assert run["isCurrent"] is True
        assert run["totalCount"] == 4
        assert run["includedCount"] == 2
        assert run["excludedCount"] == 2
        assert run["answeredCount"] == 1
        assert run["missingCount"] == 1
        assert run["ruleSnapshot"][0]["version"] == mysql_rule["version"]

        replayed = client.post("/api/v1/recommendation-runs", headers=headers, json={})
        assert replayed.status_code == 201
        assert replayed.json()["id"] == run["id"]

        included = client.get(
            f"/api/v1/recommendation-runs/{run['id']}/items",
            params={"decision": "INCLUDED"},
        )
        assert included.status_code == 200
        included_payload = included.json()
        assert included_payload["total"] == 2
        mismatch_item = next(
            item for item in included_payload["items"] if item["questionId"] == answer_mismatch["id"]
        )
        assert mismatch_item["answerState"] == "SOURCE_ANSWER"
        assert mismatch_item["usableAnswerState"] == "MISSING"
        assert "ANSWER_LANGUAGE_MISMATCH" in mismatch_item["reasonCodes"]
        assert {item["canonicalName"] for item in mismatch_item["answerTechnologies"]} == {
            "java"
        }

        excluded = client.get(
            f"/api/v1/recommendation-runs/{run['id']}/items",
            params={"decision": "EXCLUDED"},
        ).json()
        assert {item["questionId"] for item in excluded["items"]} == {
            java_question["id"],
            mysql_question["id"],
        }
        database_item = next(
            item
            for item in included_payload["items"]
            if item["questionId"] == database_question["id"]
        )
        assert database_item["usableAnswerState"] == "ANSWERED"

        latest = client.get("/api/v1/recommendation-runs/latest")
        assert latest.status_code == 200
        assert latest.json()["id"] == run["id"]

        updated_question = client.put(
            f"/api/v1/questions/{database_question['id']}",
            json={
                "prompt": "数据库事务的 ACID 分别代表什么？",
                "expectedVersion": database_question["version"],
            },
        )
        assert updated_question.status_code == 200
        assert client.get(f"/api/v1/recommendation-runs/{run['id']}").json()["isCurrent"] is False

        refreshed = client.post(
            "/api/v1/recommendation-runs",
            headers={"Idempotency-Key": f"recommendation-{uuid4()}"},
            json={},
        )
        assert refreshed.status_code == 201
        refreshed_run = refreshed.json()
        assert refreshed_run["isCurrent"] is True

        new_rule = client.post(
            "/api/v1/preference-rules",
            json={"ruleType": "INCLUDE", "technology": "Python", "scope": "BOTH"},
        )
        assert new_rule.status_code == 201
        assert (
            client.get(f"/api/v1/recommendation-runs/{refreshed_run['id']}").json()["isCurrent"]
            is False
        )

        with SessionLocal() as db:
            db.add(User(id=other_owner_id, display_name="另一个推荐用户"))
            db.commit()
        app.dependency_overrides[get_current_user_id] = lambda: other_owner_id
        assert client.get(f"/api/v1/recommendation-runs/{run['id']}").status_code == 404
    finally:
        app.dependency_overrides.clear()
        with SessionLocal() as db:
            for user_id in (owner_id, other_owner_id):
                user = db.get(User, user_id)
                if user is not None:
                    db.delete(user)
            db.commit()
