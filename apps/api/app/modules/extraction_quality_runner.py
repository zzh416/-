"""Load gold samples and score model extraction with production normalization rules."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.adapters.glm_text import ExtractedPair, ExtractedSpan, ExtractionBlock
from app.modules.extraction_quality import (
    ExpectedQuestion,
    ExtractionQualityReport,
    ObservedQuestion,
    QualityThresholds,
    evaluate_extraction_quality,
)
from app.modules.question_extraction_service import (
    initial_review_status,
    question_prompt_from_quote,
)
from app.modules.question_miss_detection import (
    CoveredQuestionRange,
    MissDetectionBlock,
    find_suspected_questions,
)


class QualityBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ordinal: int = Field(ge=0)
    content: str = Field(min_length=1)


class QualityExpectedQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1)
    answer: str | None = None


class QualitySample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    source_format: str = Field(min_length=1)
    blocks: list[QualityBlock] = Field(min_length=1)
    expected: list[QualityExpectedQuestion] = Field(min_length=1)


class ImageQualitySample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    source_format: str = "IMAGE"
    file: str = Field(min_length=1)
    expected: list[QualityExpectedQuestion] = Field(min_length=1)


@dataclass(frozen=True)
class SampleEvaluation:
    sample_name: str
    source_format: str
    report: ExtractionQualityReport
    invalid_candidate_count: int
    expected: tuple[ExpectedQuestion, ...]
    observed: tuple[ObservedQuestion, ...]
    suspected_misses: tuple[str, ...]


def load_quality_sample(path: Path) -> QualitySample:
    return QualitySample.model_validate_json(path.read_text(encoding="utf-8"))


def load_image_quality_sample(path: Path) -> ImageQualitySample:
    return ImageQualitySample.model_validate_json(path.read_text(encoding="utf-8"))


def image_quality_sample_from_markdown(
    sample: ImageQualitySample,
    markdown: str,
) -> QualitySample:
    return QualitySample(
        name=sample.name,
        source_format=sample.source_format,
        blocks=[QualityBlock(ordinal=0, content=markdown)],
        expected=sample.expected,
    )


def extraction_blocks(sample: QualitySample) -> list[ExtractionBlock]:
    return [
        ExtractionBlock(ordinal=block.ordinal, content=block.content)
        for block in sample.blocks
    ]


def _resolve_span(
    blocks: dict[int, QualityBlock],
    span: ExtractedSpan,
) -> tuple[QualityBlock, str, int, int] | None:
    block = blocks.get(span.block_ordinal)
    if block is None:
        return None
    start = block.content.find(span.quote)
    if start < 0 or block.content.find(span.quote, start + 1) >= 0:
        return None
    return block, span.quote.strip(), start, start + len(span.quote)


def evaluate_quality_sample(
    sample: QualitySample,
    pairs: list[ExtractedPair],
    *,
    thresholds: QualityThresholds | None = None,
) -> SampleEvaluation:
    blocks = {block.ordinal: block for block in sample.blocks}
    observed_by_range: dict[tuple[int, int, int], ObservedQuestion] = {}
    covered_ranges: list[CoveredQuestionRange] = []
    invalid_candidate_count = 0

    for pair in pairs:
        question_span = _resolve_span(blocks, pair.question)
        answer_spans = [_resolve_span(blocks, span) for span in pair.answer_spans]
        if question_span is None or any(span is None for span in answer_spans):
            invalid_candidate_count += 1
            continue
        question_block, _, question_start, question_end = question_span
        valid_answers = [span for span in answer_spans if span is not None]
        overlapping_answer_offsets = [
            answer_start - question_start
            for answer_block, _, answer_start, _ in valid_answers
            if answer_block.ordinal == question_block.ordinal
            and question_start < answer_start < question_end
        ]
        prompt = question_prompt_from_quote(
            pair.question.quote,
            answer_offset=(
                min(overlapping_answer_offsets) if overlapping_answer_offsets else None
            ),
        )
        if not prompt:
            invalid_candidate_count += 1
            continue
        answer_texts = [span[1] for span in valid_answers if span[1]]
        observed = ObservedQuestion(
            prompt=prompt,
            answer="\n\n".join(answer_texts) if answer_texts else None,
            visible=initial_review_status(pair.question.quote) != "REJECTED",
        )
        source_key = (question_block.ordinal, question_start, question_end)
        existing = observed_by_range.get(source_key)
        if existing is None or (existing.answer is None and observed.answer is not None):
            observed_by_range[source_key] = observed

    for ordinal, start, end in observed_by_range:
        covered_ranges.append(
            CoveredQuestionRange(block_key=ordinal, start=start, end=end)
        )
    suspected = find_suspected_questions(
        [
            MissDetectionBlock(
                key=block.ordinal,
                ordinal=block.ordinal,
                content=block.content,
                locator={"sourceFormat": sample.source_format},
            )
            for block in sample.blocks
        ],
        covered_ranges,
    )
    expected = tuple(
        ExpectedQuestion(prompt=item.prompt, answer=item.answer)
        for item in sample.expected
    )
    observed = tuple(observed_by_range.values())
    suspected_prompts = tuple(item.quote for item in suspected)
    report = evaluate_extraction_quality(
        list(expected),
        list(observed),
        suspected_misses=list(suspected_prompts),
        thresholds=thresholds,
    )
    return SampleEvaluation(
        sample_name=sample.name,
        source_format=sample.source_format,
        report=report,
        invalid_candidate_count=invalid_candidate_count,
        expected=expected,
        observed=observed,
        suspected_misses=suspected_prompts,
    )
