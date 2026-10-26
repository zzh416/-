"""Deterministic quality metrics for question and answer extraction."""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata


@dataclass(frozen=True)
class ExpectedQuestion:
    prompt: str
    answer: str | None


@dataclass(frozen=True)
class ObservedQuestion:
    prompt: str
    answer: str | None
    visible: bool = True


@dataclass(frozen=True)
class QualityThresholds:
    question_recall: float = 0.95
    assisted_recall: float = 0.98
    visible_precision: float = 0.98
    answer_state_accuracy: float = 0.95
    answer_pair_accuracy: float = 0.95


@dataclass(frozen=True)
class ExtractionQualityReport:
    expected_count: int
    observed_count: int
    matched_count: int
    missed_count: int
    false_positive_count: int
    visible_false_positive_count: int
    visible_observed_count: int
    visible_matched_count: int
    suspected_miss_hits: int
    answer_state_correct_count: int
    answered_match_count: int
    correctly_paired_count: int
    strictly_paired_count: int
    prompt_variation_count: int
    question_recall: float
    assisted_question_recall: float
    raw_precision: float
    visible_precision: float
    answer_state_accuracy: float
    answer_pair_accuracy: float
    strict_answer_pair_accuracy: float
    missed_prompts: tuple[str, ...]
    false_positive_prompts: tuple[str, ...]
    wrong_answer_prompts: tuple[str, ...]
    formatting_variation_prompts: tuple[str, ...]
    prompt_variations: tuple[str, ...]
    passed: bool


_PROMPT_PREFIX = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*{1,2})?"
    r"(?:(?:[-+>▶►▸•·]\s*)|(?:\d+(?:\.\d+)*[.、)]\s*))*"
)


def _normalize_prompt(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = _PROMPT_PREFIX.sub("", normalized)
    normalized = normalized.replace("**", "")
    normalized = re.sub(r"\s+", "", normalized).casefold()
    return normalized.rstrip("?。.;:!")


def _normalize_answer(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"\s+", " ", normalized).strip().casefold()
    return normalized or None


_ANSWER_LABEL_LINE = re.compile(
    r"^(?:\*{1,2}|_{1,2})?\s*(?:答|答案|参考答案)\s*[：:]?\s*(?:\*{1,2}|_{1,2})?$",
    re.IGNORECASE,
)
_ANSWER_LABEL_PREFIX = re.compile(r"^(?:答|答案|参考答案)\s*[：:]\s*", re.IGNORECASE)
_PAIRED_EMPHASIS = re.compile(r"(\*\*|__)(.+?)\1")
_DECORATION_DOTS = re.compile(r"\.{8,}")
_HEADING_PREFIX = re.compile(r"^#{1,6}\s+")
_LIST_PREFIX = re.compile(
    r"^(?:[-+*]\s+|[•·▶►▸\uf0b7]\s*|(?:\d+(?:\.\d+)*[.、)]|[（(]\d+[）)]|[一二三四五六七八九十]+[、.])\s*)"
)
_INLINE_ORDERED_LIST_PREFIX = re.compile(
    r"(?<=[；;])\s*\d+(?:\.\d+)*[.、)]\s*"
)
_TABLE_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")


def _is_markdown_table_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip("|").split("|")]
    return bool(cells) and all(_TABLE_SEPARATOR_CELL.fullmatch(cell) for cell in cells)


def _normalize_answer_content(value: str | None) -> str | None:
    """Compare source content while ignoring only common document layout syntax.

    Characters that carry meaning, including punctuation, numbers and operators, remain
    in order. The normalization only removes standalone answer labels, Markdown heading
    prefixes, table separators/cell borders, list markers and whitespace introduced by
    PDF/DOCX layout conversion.
    """
    if value is None:
        return None
    lines: list[str] = []
    for raw_line in unicodedata.normalize("NFKC", value).splitlines():
        line = raw_line.strip()
        if not line or _ANSWER_LABEL_LINE.fullmatch(line):
            continue
        line = _PAIRED_EMPHASIS.sub(r"\2", line)
        line = _ANSWER_LABEL_PREFIX.sub("", line)
        line = _HEADING_PREFIX.sub("", line)
        if _is_markdown_table_separator(line):
            continue
        if line.startswith("|") or line.endswith("|") or line.count("|") >= 2:
            line = "".join(cell.strip() for cell in line.strip("|").split("|"))
        line = _LIST_PREFIX.sub("", line)
        line = _INLINE_ORDERED_LIST_PREFIX.sub("", line)
        line = _DECORATION_DOTS.sub("", line)
        lines.append(line)
    normalized = re.sub(r"\s+", "", "".join(lines)).casefold()
    return normalized or None


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


def evaluate_extraction_quality(
    expected: list[ExpectedQuestion],
    observed: list[ObservedQuestion],
    *,
    suspected_misses: list[str] | None = None,
    thresholds: QualityThresholds | None = None,
) -> ExtractionQualityReport:
    """Score one-to-one prompt matches without semantic or fuzzy fallbacks."""
    active_thresholds = thresholds or QualityThresholds()
    observed_by_prompt: dict[str, deque[int]] = defaultdict(deque)
    for index, item in enumerate(observed):
        observed_by_prompt[_normalize_prompt(item.prompt)].append(index)

    matched: list[tuple[ExpectedQuestion, ObservedQuestion]] = []
    matched_observed_indexes: set[int] = set()
    unmatched_expected: list[ExpectedQuestion] = []
    for expected_item in expected:
        candidates = observed_by_prompt[_normalize_prompt(expected_item.prompt)]
        if not candidates:
            unmatched_expected.append(expected_item)
            continue
        observed_index = candidates.popleft()
        matched_observed_indexes.add(observed_index)
        matched.append((expected_item, observed[observed_index]))

    prompt_variations: list[str] = []
    near_candidates: list[tuple[float, int, int]] = []
    unmatched_observed_indexes = [
        index for index in range(len(observed)) if index not in matched_observed_indexes
    ]
    for expected_index, expected_item in enumerate(unmatched_expected):
        expected_key = _normalize_prompt(expected_item.prompt)
        for observed_index in unmatched_observed_indexes:
            observed_key = _normalize_prompt(observed[observed_index].prompt)
            if min(len(expected_key), len(observed_key)) < 8:
                continue
            similarity = SequenceMatcher(None, expected_key, observed_key).ratio()
            if similarity >= 0.97:
                near_candidates.append((similarity, expected_index, observed_index))
    matched_expected_indexes: set[int] = set()
    for _, expected_index, observed_index in sorted(near_candidates, reverse=True):
        if (
            expected_index in matched_expected_indexes
            or observed_index in matched_observed_indexes
        ):
            continue
        expected_item = unmatched_expected[expected_index]
        observed_item = observed[observed_index]
        matched_expected_indexes.add(expected_index)
        matched_observed_indexes.add(observed_index)
        matched.append((expected_item, observed_item))
        prompt_variations.append(f"{expected_item.prompt} → {observed_item.prompt}")

    missed = [
        item
        for index, item in enumerate(unmatched_expected)
        if index not in matched_expected_indexes
    ]

    false_positives = [
        item for index, item in enumerate(observed) if index not in matched_observed_indexes
    ]
    suspected_counts = Counter(
        _normalize_prompt(prompt) for prompt in (suspected_misses or [])
    )
    suspected_miss_hits = 0
    for missed_item in missed:
        key = _normalize_prompt(missed_item.prompt)
        if suspected_counts[key] > 0:
            suspected_counts[key] -= 1
            suspected_miss_hits += 1

    visible_observed_count = sum(item.visible for item in observed)
    visible_matched_count = sum(observed_item.visible for _, observed_item in matched)
    visible_false_positive_count = sum(item.visible for item in false_positives)

    answer_state_correct = sum(
        (expected_item.answer is None) == (observed_item.answer is None)
        for expected_item, observed_item in matched
    )
    answered_matches = [
        (expected_item, observed_item)
        for expected_item, observed_item in matched
        if expected_item.answer is not None
    ]
    strictly_paired = sum(
        _normalize_answer(expected_item.answer) == _normalize_answer(observed_item.answer)
        for expected_item, observed_item in answered_matches
    )
    correctly_paired = sum(
        _normalize_answer_content(expected_item.answer)
        == _normalize_answer_content(observed_item.answer)
        for expected_item, observed_item in answered_matches
    )
    wrong_answer_prompts = tuple(
        expected_item.prompt
        for expected_item, observed_item in answered_matches
        if _normalize_answer_content(expected_item.answer)
        != _normalize_answer_content(observed_item.answer)
    )
    formatting_variation_prompts = tuple(
        expected_item.prompt
        for expected_item, observed_item in answered_matches
        if _normalize_answer_content(expected_item.answer)
        == _normalize_answer_content(observed_item.answer)
        and _normalize_answer(expected_item.answer) != _normalize_answer(observed_item.answer)
    )

    question_recall = _ratio(len(matched), len(expected))
    assisted_question_recall = _ratio(
        len(matched) + suspected_miss_hits,
        len(expected),
    )
    raw_precision = _ratio(len(matched), len(observed))
    visible_precision = _ratio(visible_matched_count, visible_observed_count)
    answer_state_accuracy = _ratio(answer_state_correct, len(matched))
    answer_pair_accuracy = _ratio(correctly_paired, len(answered_matches))
    strict_answer_pair_accuracy = _ratio(strictly_paired, len(answered_matches))
    passed = (
        question_recall >= active_thresholds.question_recall
        and assisted_question_recall >= active_thresholds.assisted_recall
        and visible_precision >= active_thresholds.visible_precision
        and answer_state_accuracy >= active_thresholds.answer_state_accuracy
        and answer_pair_accuracy >= active_thresholds.answer_pair_accuracy
    )

    return ExtractionQualityReport(
        expected_count=len(expected),
        observed_count=len(observed),
        matched_count=len(matched),
        missed_count=len(missed),
        false_positive_count=len(false_positives),
        visible_false_positive_count=visible_false_positive_count,
        visible_observed_count=visible_observed_count,
        visible_matched_count=visible_matched_count,
        suspected_miss_hits=suspected_miss_hits,
        answer_state_correct_count=answer_state_correct,
        answered_match_count=len(answered_matches),
        correctly_paired_count=correctly_paired,
        strictly_paired_count=strictly_paired,
        prompt_variation_count=len(prompt_variations),
        question_recall=question_recall,
        assisted_question_recall=assisted_question_recall,
        raw_precision=raw_precision,
        visible_precision=visible_precision,
        answer_state_accuracy=answer_state_accuracy,
        answer_pair_accuracy=answer_pair_accuracy,
        strict_answer_pair_accuracy=strict_answer_pair_accuracy,
        missed_prompts=tuple(item.prompt for item in missed),
        false_positive_prompts=tuple(item.prompt for item in false_positives),
        wrong_answer_prompts=wrong_answer_prompts,
        formatting_variation_prompts=formatting_variation_prompts,
        prompt_variations=tuple(prompt_variations),
        passed=passed,
    )


def aggregate_quality_reports(
    reports: list[ExtractionQualityReport],
    *,
    thresholds: QualityThresholds | None = None,
) -> ExtractionQualityReport:
    active_thresholds = thresholds or QualityThresholds()
    expected_count = sum(item.expected_count for item in reports)
    observed_count = sum(item.observed_count for item in reports)
    matched_count = sum(item.matched_count for item in reports)
    missed_count = sum(item.missed_count for item in reports)
    false_positive_count = sum(item.false_positive_count for item in reports)
    visible_false_positive_count = sum(
        item.visible_false_positive_count for item in reports
    )
    visible_observed_count = sum(item.visible_observed_count for item in reports)
    visible_matched_count = sum(item.visible_matched_count for item in reports)
    suspected_miss_hits = sum(item.suspected_miss_hits for item in reports)
    answer_state_correct_count = sum(
        item.answer_state_correct_count for item in reports
    )
    answered_match_count = sum(item.answered_match_count for item in reports)
    correctly_paired_count = sum(item.correctly_paired_count for item in reports)
    strictly_paired_count = sum(item.strictly_paired_count for item in reports)
    prompt_variation_count = sum(item.prompt_variation_count for item in reports)
    question_recall = _ratio(matched_count, expected_count)
    assisted_question_recall = _ratio(
        matched_count + suspected_miss_hits,
        expected_count,
    )
    raw_precision = _ratio(matched_count, observed_count)
    visible_precision = _ratio(visible_matched_count, visible_observed_count)
    answer_state_accuracy = _ratio(answer_state_correct_count, matched_count)
    answer_pair_accuracy = _ratio(correctly_paired_count, answered_match_count)
    strict_answer_pair_accuracy = _ratio(strictly_paired_count, answered_match_count)
    passed = (
        question_recall >= active_thresholds.question_recall
        and assisted_question_recall >= active_thresholds.assisted_recall
        and visible_precision >= active_thresholds.visible_precision
        and answer_state_accuracy >= active_thresholds.answer_state_accuracy
        and answer_pair_accuracy >= active_thresholds.answer_pair_accuracy
    )
    return ExtractionQualityReport(
        expected_count=expected_count,
        observed_count=observed_count,
        matched_count=matched_count,
        missed_count=missed_count,
        false_positive_count=false_positive_count,
        visible_false_positive_count=visible_false_positive_count,
        visible_observed_count=visible_observed_count,
        visible_matched_count=visible_matched_count,
        suspected_miss_hits=suspected_miss_hits,
        answer_state_correct_count=answer_state_correct_count,
        answered_match_count=answered_match_count,
        correctly_paired_count=correctly_paired_count,
        strictly_paired_count=strictly_paired_count,
        prompt_variation_count=prompt_variation_count,
        question_recall=question_recall,
        assisted_question_recall=assisted_question_recall,
        raw_precision=raw_precision,
        visible_precision=visible_precision,
        answer_state_accuracy=answer_state_accuracy,
        answer_pair_accuracy=answer_pair_accuracy,
        strict_answer_pair_accuracy=strict_answer_pair_accuracy,
        missed_prompts=tuple(
            prompt for item in reports for prompt in item.missed_prompts
        ),
        false_positive_prompts=tuple(
            prompt for item in reports for prompt in item.false_positive_prompts
        ),
        wrong_answer_prompts=tuple(
            prompt for item in reports for prompt in item.wrong_answer_prompts
        ),
        formatting_variation_prompts=tuple(
            prompt
            for item in reports
            for prompt in item.formatting_variation_prompts
        ),
        prompt_variations=tuple(
            variation for item in reports for variation in item.prompt_variations
        ),
        passed=passed,
    )
