"""Prefer exact-source answer continuations within one extraction result only."""
from collections.abc import Mapping

from app.adapters.glm_text import ExtractedPair, ExtractedSpan


SourceRange = tuple[int, int]


def _strict_extension(
    previous: tuple[SourceRange, ...],
    candidate: tuple[SourceRange, ...],
    source: str,
) -> bool:
    if len(candidate) < len(previous) or candidate[: len(previous) - 1] != previous[:-1]:
        return False
    old_start, old_end = previous[-1]
    new_start, new_end = candidate[len(previous) - 1]
    if new_start != old_start or new_end < old_end:
        return False
    if len(candidate) == len(previous) and new_end == old_end:
        return False
    for following_start, following_end in candidate[len(previous) :]:
        if source[new_end:following_start].strip():
            return False
        new_end = following_end
    return True


def prefer_complete_source_answers(
    pairs: list[ExtractedPair],
    blocks: Mapping[int, str],
) -> list[ExtractedPair]:
    """Move a strict source continuation before its shorter duplicate.

    Every candidate is retained so the caller can still count invalid spans.
    This only reorders the current model result; it never reads or edits saved
    answers. Different source questions, rewrites, and nonadjacent additions
    cannot replace an earlier candidate.
    """
    offsets: dict[int, int] = {}
    source_parts: list[str] = []
    length = 0
    for ordinal, content in sorted(blocks.items()):
        offsets[ordinal] = length
        source_parts.append(content)
        length += len(content) + 2
    source = "\n\n".join(source_parts)

    def resolve(span: ExtractedSpan) -> SourceRange | None:
        content = blocks.get(span.block_ordinal)
        if content is None:
            return None
        start = content.find(span.quote)
        if start < 0 or content.find(span.quote, start + 1) >= 0:
            return None
        absolute = offsets[span.block_ordinal] + start
        return absolute, absolute + len(span.quote)

    question_ranges = [resolve(pair.question) for pair in pairs]
    known_questions = {span for span in question_ranges if span is not None}
    selected: dict[SourceRange, tuple[int, tuple[SourceRange, ...]]] = {}
    result = list(pairs)
    for index, pair in enumerate(pairs):
        question = question_ranges[index]
        answers = tuple(resolve(span) for span in pair.answer_spans)
        if question is None or not answers or any(span is None for span in answers):
            continue
        resolved = tuple(span for span in answers if span is not None)
        if resolved[0][0] < question[0] or any(
            current[0] < previous[1] for previous, current in zip(resolved, resolved[1:])
        ):
            continue
        if any(
            answer_start < question_end and question_start < answer_end
            for answer_start, answer_end in resolved
            for question_start, question_end in known_questions - {question}
        ):
            continue
        previous = selected.get(question)
        if previous is None:
            selected[question] = index, resolved
            continue
        first_index, previous_answers = previous
        if _strict_extension(previous_answers, resolved, source):
            result[index], result[first_index] = result[first_index], pair
            selected[question] = first_index, resolved
    return result
