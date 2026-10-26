"""Run the configured text model against versioned gold samples."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

from app.adapters.glm_ocr import OCRNotConfigured, OCRServiceError
from app.adapters.ocr_provider import extract_with_configured_ocr
from app.adapters.glm_text import (
    TextModelError,
    TextModelNotConfigured,
    extract_questions_with_glm,
)
from app.adapters.text_provider import get_text_model_config
from app.config import settings
from app.modules.extraction_quality import (
    ExtractionQualityReport,
    QualityThresholds,
    aggregate_quality_reports,
)
from app.modules.extraction_quality_runner import (
    SampleEvaluation,
    evaluate_quality_sample,
    extraction_blocks,
    image_quality_sample_from_markdown,
    load_image_quality_sample,
    load_quality_sample,
)


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _answer_completeness(report: ExtractionQualityReport) -> str:
    if not report.answered_match_count:
        return "不适用（无带答案匹配题）"
    return _percent(report.answer_pair_accuracy)


def _strict_answer_completeness(report: ExtractionQualityReport) -> str:
    if not report.answered_match_count:
        return "不适用（无带答案匹配题）"
    return _percent(report.strict_answer_pair_accuracy)


def _render_report(
    evaluations: list[SampleEvaluation],
    thresholds: QualityThresholds,
    model: str,
) -> str:
    overall = aggregate_quality_reports(
        [item.report for item in evaluations],
        thresholds=thresholds,
    )
    lines = [
        "# P3 题答抽取金样本质量报告",
        "",
        f"> 运行时间：{datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')}",
        f"> 模型：`{model}`",
        f"> 结论：{'通过' if overall.passed else '未通过'}",
        "",
        "## 总体指标",
        "",
        "| 指标 | 结果 | 门槛 |",
        "| --- | ---: | ---: |",
        f"| 原始题目召回率 | {_percent(overall.question_recall)} | {_percent(thresholds.question_recall)} |",
        f"| 疑似漏题辅助召回率 | {_percent(overall.assisted_question_recall)} | {_percent(thresholds.assisted_recall)} |",
        f"| 用户可见题目准确率 | {_percent(overall.visible_precision)} | {_percent(thresholds.visible_precision)} |",
        f"| 有/无答案状态准确率 | {_percent(overall.answer_state_accuracy)} | {_percent(thresholds.answer_state_accuracy)} |",
        f"| 答案内容完整一致率（排版归一） | {_answer_completeness(overall)} | {_percent(thresholds.answer_pair_accuracy)} |",
        f"| 答案基础规范化一致率（参考） | {_strict_answer_completeness(overall)} | 不参与门禁 |",
        "",
        (
            f"共 {overall.expected_count} 道预期题，模型产生 {overall.observed_count} 条有效候选；"
            f"漏题 {overall.missed_count} 条，误抽取 {overall.false_positive_count} 条，"
            f"其中用户可见误抽取 {overall.visible_false_positive_count} 条，"
            f"疑似漏题命中 {overall.suspected_miss_hits} 条，"
            f"轻微 OCR 字符差异 {overall.prompt_variation_count} 条。"
        ),
        "",
        "## 分样本结果",
        "",
        "| 样本 | 来源格式 | 预期题数 | 召回率 | 辅助召回率 | 可见准确率 | 答案状态 | 答案内容完整 | 非法候选 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for evaluation in evaluations:
        item = evaluation.report
        lines.append(
            f"| {evaluation.sample_name} | {evaluation.source_format} | {item.expected_count} | "
            f"{_percent(item.question_recall)} | {_percent(item.assisted_question_recall)} | "
            f"{_percent(item.visible_precision)} | {_percent(item.answer_state_accuracy)} | "
            f"{_answer_completeness(item)} | {evaluation.invalid_candidate_count} |"
        )
    if overall.missed_prompts:
        lines.extend(["", "## 漏题", ""])
        lines.extend(f"- {prompt}" for prompt in overall.missed_prompts)
    if overall.false_positive_prompts:
        lines.extend(["", "## 误抽取", ""])
        lines.extend(f"- {prompt}" for prompt in overall.false_positive_prompts)
    if overall.wrong_answer_prompts:
        lines.extend(["", "## 答案内容或范围不一致（需人工区分错配、漏段与串题）", ""])
        lines.extend(f"- {prompt}" for prompt in overall.wrong_answer_prompts)
    if overall.formatting_variation_prompts:
        lines.extend(["", "## 仅排版不同的答案", ""])
        lines.extend(f"- {prompt}" for prompt in overall.formatting_variation_prompts)
    if overall.prompt_variations:
        lines.extend(["", "## 轻微 OCR 字符差异", ""])
        lines.extend(f"- {variation}" for variation in overall.prompt_variations)
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "该报告使用逐字来源约束和严格一对一匹配；只对长度不少于 8 个字符且字符相似度达到 97% 的 OCR 微小差异计为同一道题，并单独列出差异。",
            "答案内容完整一致率只忽略独立答案标签、Markdown 标题前缀、表格边界/分隔行、列表编号/项目符号及空白；不会忽略正文字符、数字、运算符或内容顺序。该指标参与门禁。",
            "答案基础规范化一致率沿用旧口径，仅统一 Unicode、连续空白和大小写，用于显示跨格式排版差异，不参与门禁。无带答案样本时两项均不适用。",
            "普通样本只验证解析后题答抽取；显式启用的图片样本包含真实 OCR。PDF/DOCX 解析、生产分批与持久化仍需另行验收，不能由这些文字样本代替。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=Path, default=Path("quality_samples"))
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--include-images",
        action="store_true",
        help="将 images/ 中的文件发送到已配置的 OCR 服务",
    )
    args = parser.parse_args()

    paths = sorted(args.samples.glob("*.json"))
    image_paths = (
        sorted((args.samples / "images").glob("*.json"))
        if args.include_images
        else []
    )
    if not paths and not image_paths:
        print("没有找到金样本 JSON 文件", file=sys.stderr)
        return 2
    thresholds = QualityThresholds()
    model_config = get_text_model_config()
    evaluations: list[SampleEvaluation] = []
    try:
        for path in paths:
            sample = load_quality_sample(path)
            pairs = extract_questions_with_glm(
                extraction_blocks(sample),
                api_key=model_config.api_key,
                base_url=model_config.base_url,
                model=model_config.model,
                provider=model_config.provider,
                max_output_tokens=settings.question_extraction_max_output_tokens,
            )
            evaluations.append(
                evaluate_quality_sample(sample, pairs, thresholds=thresholds)
            )
        for path in image_paths:
            image_manifest = load_image_quality_sample(path)
            image_path = (path.parent / image_manifest.file).resolve()
            if path.parent.resolve() not in image_path.parents:
                print(f"图片样本路径越界：{image_manifest.file}", file=sys.stderr)
                return 2
            recognized = extract_with_configured_ocr(image_path.read_bytes())
            sample = image_quality_sample_from_markdown(
                image_manifest,
                recognized.markdown,
            )
            pairs = extract_questions_with_glm(
                extraction_blocks(sample),
                api_key=model_config.api_key,
                base_url=model_config.base_url,
                model=model_config.model,
                provider=model_config.provider,
                max_output_tokens=settings.question_extraction_max_output_tokens,
            )
            evaluations.append(
                evaluate_quality_sample(sample, pairs, thresholds=thresholds)
            )
    except (OCRNotConfigured, TextModelNotConfigured) as exc:
        print(f"金样本抽取失败：模型密钥未配置：{exc}", file=sys.stderr)
        return 2
    except (OCRServiceError, TextModelError) as exc:
        print(f"金样本抽取失败：{exc.code}：{exc}", file=sys.stderr)
        return 2

    report = _render_report(evaluations, thresholds, model_config.model)
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    print(report)
    overall = aggregate_quality_reports(
        [item.report for item in evaluations],
        thresholds=thresholds,
    )
    return 0 if overall.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
