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

    assert "| 答案内容完整一致率（排版归一） | 不适用（无带答案匹配题） |" in rendered
    assert "| 答案基础规范化一致率（参考） | 不适用（无带答案匹配题） |" in rendered
    assert "不会忽略正文字符、数字、运算符或内容顺序" in rendered


def test_report_shows_layout_variations_separately_from_content_errors() -> None:
    sample = QualitySample(
        name="表格排版差异",
        source_format="PDF",
        blocks=[
            {"ordinal": 0, "content": "索引有哪些类型？"},
            {"ordinal": 1, "content": "类型 说明\nB+树 范围查询"},
        ],
        expected=[
            {
                "prompt": "索引有哪些类型？",
                "answer": "| 类型 | 说明 |\n| --- | --- |\n| B+树 | 范围查询 |",
            }
        ],
    )
    evaluation = evaluate_quality_sample(
        sample,
        [
            ExtractedPair(
                question=ExtractedSpan(block_ordinal=0, quote="索引有哪些类型？"),
                answer_spans=[
                    ExtractedSpan(block_ordinal=1, quote="类型 说明\nB+树 范围查询")
                ],
                confidence=1,
            )
        ],
    )

    rendered = _render_report([evaluation], QualityThresholds(), "test-model")

    assert "| 答案内容完整一致率（排版归一） | 100.0% |" in rendered
    assert "| 答案基础规范化一致率（参考） | 0.0% | 不参与门禁 |" in rendered
    assert "## 仅排版不同的答案" in rendered
    assert "- 索引有哪些类型？" in rendered
