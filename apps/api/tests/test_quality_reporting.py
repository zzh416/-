from app.adapters.glm_text import ExtractedPair, ExtractedSpan
from app.modules.extraction_quality import QualityThresholds
from app.modules.extraction_quality_runner import QualitySample, evaluate_quality_sample
from tools.evaluate_extraction_quality import _render_report


def test_question_only_report_does_not_claim_answer_pairing_was_verified() -> None:
    sample = QualitySample(
        name="仅问题截图",
        source_format="IMAGE",
        blocks=[{"ordinal": 0, "content": "什么是 RAG？"}],
        expected=[{"prompt": "什么是 RAG？", "answer": None}],
    )
    evaluation = evaluate_quality_sample(
        sample,
        [ExtractedPair(question=ExtractedSpan(block_ordinal=0, quote="什么是 RAG？"), confidence=1)],
    )

    rendered = _render_report([evaluation], QualityThresholds(), "test-model")

    assert "| 原文答案完整一致率（严格） | 不适用（无带答案匹配题） |" in rendered
    assert "不能直接等同于答案错配率" in rendered
