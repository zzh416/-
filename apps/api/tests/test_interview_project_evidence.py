"""Regressions for valid project questions lost by quotes and a closed topic list."""
from uuid import uuid4

import pytest

from app.adapters.interview_coach import InterviewPlan
from app.modules.interview_planning import _payload, make_plan


def state():
    project_id = str(uuid4())
    return {"question_source": "AI", "question_count": 8, "project_id": project_id,
            "candidates": [], "existing_prompt_hashes": [], "plan": [],
            "profile_snapshot": {"skills": [], "projects": [{"id": project_id, "name": "合成知识库",
                "review_status": "CONFIRMED", "description":
                "使用 PyMuPDF 和 pdfplumber 实现结构化解析；调用 Qwen-VL 为图片生成语义描述。\n"
                "通过 RRF融合检索结果，使用 BGE-Reranker 对 Top-K 候\n选文档进行二阶段精排。\n"
                "集成 Ragas评估框架，建立质量评估流程；实现对话自动摘要压缩功能。"}]},
            "rule_snapshot": [{"rule_type": "EXCLUDE", "canonical_technology": "java",
                               "technology": "Java", "category": "LANGUAGE", "scope": "BOTH"}]}


def result(s, question, **basis):
    return InterviewPlan(new_questions=[{"project_id": s["project_id"], "question": question,
                                          "reason": "验证项目依据", **basis}])


@pytest.mark.parametrize(("question", "quote"), [
    ("PyMuPDF 与 pdfplumber 在结构化解析时如何分工？", "使用 PyMuPDF 和 pdfplumber 实现结构化解析"),
    ("RRF 如何融合不同检索结果，其参数如何确定？", "通过 RRF融合检索结果"),
    ("Ragas 评估流程中你会检查哪些指标？", "集成 Ragas评估框架，建立质量评估流程"),
    ("对话自动摘要压缩如何避免遗漏上下文？", "实现对话自动摘要压缩功能"),
])
def test_project_terms_outside_catalog_are_valid(question, quote):
    s = state()
    assert len(make_plan(s, result(s, question, project_quote=quote))) == 1


def test_whitespace_only_quote_difference_resolves_to_actual_source():
    s = state()
    plan = make_plan(s, result(s, "BGE-Reranker 如何处理候选文档？",
                              project_quote="BGE-Reranker 对 Top-K 候选文档进行二阶段精排"))
    assert len(plan) == 1
    assert plan[0]["project_quote"] in s["profile_snapshot"]["projects"][0]["description"]
    assert "候\n选" in plan[0]["project_quote"]


def test_server_evidence_id_resolves_quote_without_model_transcription():
    s = state()
    evidence = _payload(s)["projects"][0]["evidence"]
    item = next(e for e in evidence if "PyMuPDF" in e["text"])
    plan = make_plan(s, result(s, "PyMuPDF 如何进行结构化解析？", project_evidence_id=item["id"]))
    assert len(plan) == 1 and plan[0]["project_quote"] == item["text"]
    assert "PyMuPDF" in item["anchor_terms"]


def test_evidence_is_stable_scoped_and_does_not_accept_unrelated_or_excluded_questions():
    s = state()
    item = _payload(s)["projects"][0]["evidence"][0]
    assert _payload(s)["projects"][0]["evidence"][0] == item
    assert not make_plan(s, result(s, "如何计算期权的风险和收益？", project_evidence_id=item["id"]))
    assert not make_plan(s, result(s, "Java 如何调用 PyMuPDF？", project_evidence_id=item["id"]))
    assert not make_plan(s, result(s, "PyMuPDF 如何解析文件？", project_evidence_id="not-an-evidence-id"))
    s["project_id"] = str(uuid4())
    assert not make_plan(s, result(s, "PyMuPDF 如何解析文件？", project_evidence_id=item["id"]))


def test_quote_repair_never_accepts_paraphrases_or_generic_overlap():
    s = state()
    assert not make_plan(s, result(s, "PyMuPDF 如何进行结构化解析？", project_quote="使用 PyMuPDF 来识别所有文档"))
    assert not make_plan(s, result(s, "这个项目如何使用期权策略？", project_quote="实现对话自动摘要压缩功能"))
