from pathlib import Path

from app.modules.extraction_quality import (
    ExpectedQuestion,
    ObservedQuestion,
    QualityThresholds,
    aggregate_quality_reports,
    evaluate_extraction_quality,
)
from app.adapters.glm_text import ExtractedPair, ExtractedSpan
from app.modules.extraction_quality_runner import (
    QualitySample,
    evaluate_quality_sample,
    load_image_quality_sample,
    load_quality_sample,
)


QUALITY_SAMPLES = Path(__file__).resolve().parents[1] / "quality_samples"


def test_quality_report_counts_misses_false_positives_and_answer_errors() -> None:
    expected = [
        ExpectedQuestion(prompt="什么是 GIL？", answer="GIL 是全局解释器锁。"),
        ExpectedQuestion(prompt="协程和线程有什么区别？", answer=None),
        ExpectedQuestion(prompt="如何优化慢查询？", answer="先查看执行计划。"),
    ]
    observed = [
        ObservedQuestion(prompt="### 什么是 GIL?", answer="GIL 是全局解释器锁。"),
        ObservedQuestion(prompt="如何优化慢查询？", answer="这是下一道题的答案。"),
        ObservedQuestion(prompt="这道题暂时不会", answer=None, visible=False),
    ]

    report = evaluate_extraction_quality(
        expected,
        observed,
        suspected_misses=["协程和线程有什么区别？"],
        thresholds=QualityThresholds(
            question_recall=0.6,
            assisted_recall=1.0,
            visible_precision=1.0,
            answer_state_accuracy=1.0,
            answer_pair_accuracy=0.5,
        ),
    )

    assert report.expected_count == 3
    assert report.observed_count == 3
    assert report.matched_count == 2
    assert report.missed_prompts == ("协程和线程有什么区别？",)
    assert report.false_positive_prompts == ("这道题暂时不会",)
    assert report.suspected_miss_hits == 1
    assert report.question_recall == 2 / 3
    assert report.assisted_question_recall == 1.0
    assert report.visible_precision == 1.0
    assert report.answer_state_accuracy == 1.0
    assert report.answer_pair_accuracy == 0.5
    assert report.wrong_answer_prompts == ("如何优化慢查询？",)
    assert report.passed is True


def test_quality_report_uses_one_to_one_matching_for_duplicate_prompts() -> None:
    expected = [
        ExpectedQuestion(prompt="什么是索引？", answer=None),
        ExpectedQuestion(prompt="什么是索引？", answer=None),
    ]
    observed = [ObservedQuestion(prompt="什么是索引？", answer=None)]

    report = evaluate_extraction_quality(expected, observed)

    assert report.matched_count == 1
    assert report.missed_count == 1
    assert report.question_recall == 0.5
    assert report.passed is False


def test_quality_report_does_not_hide_visible_false_positive() -> None:
    report = evaluate_extraction_quality(
        [ExpectedQuestion(prompt="解释 TCP 三次握手", answer=None)],
        [
            ObservedQuestion(prompt="解释 TCP 三次握手", answer=None),
            ObservedQuestion(prompt="更新后的答案", answer=None),
        ],
    )

    assert report.raw_precision == 0.5
    assert report.visible_precision == 0.5
    assert report.visible_false_positive_count == 1
    assert report.passed is False


def test_quality_report_accepts_minor_ocr_variation_but_records_it() -> None:
    report = evaluate_extraction_quality(
        [
            ExpectedQuestion(
                prompt="流程是固定死的吗？还是说是一个比较自由的 agent loop？",
                answer=None,
            )
        ],
        [
            ObservedQuestion(
                prompt="▶ 流程是固定死的吗？还是说一个比较自由的 agent loop？",
                answer=None,
            )
        ],
    )

    assert report.question_recall == 1.0
    assert report.false_positive_count == 0
    assert report.prompt_variation_count == 1
    assert "→" in report.prompt_variations[0]


def test_quality_report_aggregation_is_weighted_by_question_count() -> None:
    small = evaluate_extraction_quality(
        [
            ExpectedQuestion(prompt="问题一是什么？", answer=None),
            ExpectedQuestion(prompt="问题二是什么？", answer=None),
        ],
        [ObservedQuestion(prompt="问题一是什么？", answer=None)],
    )
    large_expected = [
        ExpectedQuestion(prompt=f"第 {index} 个完整问题是什么？", answer=None)
        for index in range(8)
    ]
    large = evaluate_extraction_quality(
        large_expected,
        [ObservedQuestion(prompt=item.prompt, answer=None) for item in large_expected],
    )

    overall = aggregate_quality_reports([small, large])

    assert overall.expected_count == 10
    assert overall.matched_count == 9
    assert overall.question_recall == 0.9
    assert overall.passed is False


def test_gold_sample_runner_uses_source_spans_and_suspected_misses() -> None:
    sample = QualitySample.model_validate(
        {
            "name": "混合题答",
            "source_format": "PASTED_TEXT",
            "blocks": [
                {"ordinal": 0, "content": "### 什么是事件循环？"},
                {"ordinal": 1, "content": "事件循环负责调度任务。"},
                {"ordinal": 2, "content": "为什么需要重排？"},
            ],
            "expected": [
                {"prompt": "什么是事件循环？", "answer": "事件循环负责调度任务。"},
                {"prompt": "为什么需要重排？", "answer": None},
            ],
        }
    )
    pairs = [
        ExtractedPair(
            question=ExtractedSpan(block_ordinal=0, quote="### 什么是事件循环？"),
            answer_spans=[
                ExtractedSpan(block_ordinal=1, quote="事件循环负责调度任务。")
            ],
            confidence=0.95,
        )
    ]

    result = evaluate_quality_sample(sample, pairs)

    assert result.report.question_recall == 0.5
    assert result.report.assisted_question_recall == 1.0
    assert result.report.answer_pair_accuracy == 1.0
    assert result.invalid_candidate_count == 0


def test_versioned_quality_sample_manifests_are_valid() -> None:
    parsed_samples = [load_quality_sample(path) for path in QUALITY_SAMPLES.glob("*.json")]
    image_manifests = [
        load_image_quality_sample(path)
        for path in (QUALITY_SAMPLES / "images").glob("*.json")
    ]

    assert sum(len(sample.expected) for sample in parsed_samples) == 24
    assert all(
        (QUALITY_SAMPLES / "images" / sample.file).is_file()
        for sample in image_manifests
    )
