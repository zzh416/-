"""Repeated batch candidates can extend source answers without guessing replacements."""
import pytest

from app.adapters.glm_text import ExtractedPair, ExtractedSpan
from app.modules.extraction_candidates import prefer_complete_source_answers
from app.modules.extraction_quality_runner import QualitySample, evaluate_quality_sample


BLOCKS = {
    0: "什么是 Function Calling？",
    1: "它让模型选择工具。工具由应用执行。",
    2: "执行结果交给模型。",
    3: "什么是 RAG？",
    4: "RAG 先检索，再生成。",
}


def _pair(*answers: tuple[int, str]) -> ExtractedPair:
    return ExtractedPair(
        question=ExtractedSpan(block_ordinal=0, quote=BLOCKS[0]),
        answer_spans=[ExtractedSpan(block_ordinal=ordinal, quote=quote) for ordinal, quote in answers],
        confidence=0.9,
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_strict_source_extension_wins_in_either_order(reverse) -> None:
    short = _pair((1, "它让模型选择工具。"))
    full = _pair((1, BLOCKS[1]), (2, BLOCKS[2]))
    pairs = [full, short] if reverse else [short, full]

    selected = prefer_complete_source_answers(pairs, BLOCKS)

    assert selected[0] == full
    assert len(selected) == 2
    assert pairs == ([full, short] if reverse else [short, full])


@pytest.mark.parametrize(
    "candidate",
    [
        _pair((1, "模型会选择并执行工具。")),  # Not a source quote.
        _pair((1, "工具由应用执行。"), (2, BLOCKS[2])),  # Drops the old answer's beginning.
        _pair((2, BLOCKS[2]), (1, BLOCKS[1])),  # Reorders source spans.
        _pair((1, BLOCKS[1]), (4, BLOCKS[4])),  # Skips intervening source paragraphs.
    ],
)
def test_invalid_or_non_extension_candidate_cannot_replace_existing_answer(candidate) -> None:
    short = _pair((1, "它让模型选择工具。"))

    assert prefer_complete_source_answers([short, candidate], BLOCKS) == [short, candidate]


def test_extension_covering_another_detected_question_is_not_promoted() -> None:
    short = _pair((1, "它让模型选择工具。"))
    wrong = _pair((1, BLOCKS[1]), (2, BLOCKS[2]), (3, BLOCKS[3]))
    other = ExtractedPair(
        question=ExtractedSpan(block_ordinal=3, quote=BLOCKS[3]),
        confidence=0.9,
    )

    assert prefer_complete_source_answers([short, wrong, other], BLOCKS) == [short, wrong, other]


def test_ambiguous_source_answer_is_not_promoted() -> None:
    blocks = {**BLOCKS, 1: BLOCKS[1] + BLOCKS[1]}
    short = _pair((2, BLOCKS[2]))
    ambiguous = _pair((1, BLOCKS[1]), (2, BLOCKS[2]))

    assert prefer_complete_source_answers([short, ambiguous], blocks) == [short, ambiguous]


def test_quality_runner_uses_complete_candidate_but_keeps_invalid_count() -> None:
    sample = QualitySample.model_validate(
        {
            "name": "分批答案补全",
            "source_format": "PDF",
            "blocks": [{"ordinal": key, "content": value} for key, value in BLOCKS.items()],
            "expected": [{"prompt": BLOCKS[0], "answer": BLOCKS[1] + "\n\n" + BLOCKS[2]}],
        }
    )
    pairs = [
        _pair((1, "它让模型选择工具。")),
        _pair((1, "模型不存在的原文")),
        _pair((1, BLOCKS[1]), (2, BLOCKS[2])),
    ]

    result = evaluate_quality_sample(sample, pairs)

    assert result.observed[0].answer == BLOCKS[1] + "\n\n" + BLOCKS[2]
    assert result.report.answer_pair_accuracy == 1.0
    assert result.invalid_candidate_count == 1
