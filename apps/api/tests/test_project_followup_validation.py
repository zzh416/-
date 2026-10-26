from uuid import uuid4

from app.adapters.project_followups import FollowupCandidate
from app.modules.followup_validation import validate_followups


PROJECT = {"id": str(uuid4()), "name": "知识库", "description": "使用 Python、RAG 向量检索和重排。", "review_status": "CONFIRMED"}
SEED = {"id": str(uuid4()), "prompt": "RAG 为什么需要重排？"}


def candidate(level=1, **changes):
    return FollowupCandidate.model_validate({
        "seed_item_id": SEED["id"], "level": level,
        "question": "RAG 重排如何评估效果？", "project_quote": "RAG 向量检索和重排", "seed_quote": "为什么需要重排", **changes,
    })


def test_followups_have_exact_dual_evidence_and_linear_parents():
    items, rejected = validate_followups(PROJECT, [SEED], [
        candidate(2, question="RAG 重排效果下降时如何定位问题？"), candidate(),
    ], skills=[], rules=[])
    assert len(items) == 2 and not rejected
    assert [item["level"] for item in items] == [1, 2]
    assert items[1]["parent_id"] == items[0]["id"]
    for item in items:
        assert PROJECT["description"][item["project_start"]:item["project_end"]] == item["project_quote"]
        assert SEED["prompt"][item["seed_start"]:item["seed_end"]] == item["seed_quote"]
        assert "answer" not in item


def test_followups_reject_invented_sources_unknown_seed_duplicates_and_broken_chains():
    for changes in [
        {"seed_item_id": str(uuid4())}, {"project_quote": "项目实际使用了 Java"},
        {"seed_quote": "原文没有的引文"}, {"question": SEED["prompt"]},
        {"question": "这道题暂时不会"}, {"question": "Java 的线程池如何优化？"},
    ]:
        items, rejected = validate_followups(PROJECT, [SEED], [candidate(**changes)], skills=[], rules=[])
        assert not items and sum(rejected.values()) == 1
    items, rejected = validate_followups(PROJECT, [SEED], [candidate(2)], skills=[], rules=[])
    assert not items and rejected["broken_chain"] == 1
    items, rejected = validate_followups(PROJECT, [SEED], [candidate(), candidate()], skills=[], rules=[])
    assert len(items) == 1 and sum(rejected.values()) == 1


def test_followups_apply_original_skill_and_preference_exclusions():
    from app.modules.recommendation_service import PreferenceSnapshot, SkillSnapshot
    question = "Java 中如何实现 RAG 重排？"
    for skills, rules in [
        ([SkillSnapshot("python", "Python", "LANGUAGE", "CONFIRMED")], []),
        ([], [PreferenceSnapshot("EXCLUDE", "java", "Java", "LANGUAGE", "QUESTION")]),
    ]:
        items, rejected = validate_followups(PROJECT, [SEED], [candidate(question=question)], skills=skills, rules=rules)
        assert not items and rejected["excluded"] == 1
