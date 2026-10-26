"""Deterministic question-like line detection for extraction review."""
from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class MissDetectionBlock:
    key: Hashable
    ordinal: int
    content: str
    locator: dict


@dataclass(frozen=True)
class CoveredQuestionRange:
    block_key: Hashable
    start: int
    end: int


@dataclass(frozen=True)
class SuspectedQuestion:
    block_key: Hashable
    block_ordinal: int
    locator: dict
    start: int
    end: int
    quote: str
    reason: str


_QUESTION_LEAD_PATTERN = re.compile(
    r"^(?:(?:[-*+>]\s*)|(?:\d+[.、)）]\s*))*"
    r"(?:\*{1,2})?"
    r"(?:什么是|为什么|为何|如何|怎么|怎样|说说|(?:先)?介绍(?:一下)?|请介绍|谈谈|"
    r"是否|哪些|哪种|哪几|能否|有什么|什么情况下|什么时候)"
)
_HEADING_PATTERN = re.compile(r"^\s{0,3}(#{1,6})\s+(\S.*)$")
_NUMBERED_TITLE_PATTERN = re.compile(r"^(?:\*{1,2})?\d+[.、)）]\s*\S")
_TITLE_QUESTION_HINT_PATTERN = re.compile(
    r"(?:区别|对比|原理|作用|流程|过程|方式|机制|定义|分类|特点|优缺点|"
    r"场景|优化|调优|实现|攻击|问题)"
)


def find_suspected_questions(
    blocks: list[MissDetectionBlock],
    covered_ranges: list[CoveredQuestionRange],
    *,
    limit: int = 100,
) -> list[SuspectedQuestion]:
    covered: dict[Hashable, list[tuple[int, int]]] = {}
    for item in covered_ranges:
        covered.setdefault(item.block_key, []).append((item.start, item.end))

    misses: list[SuspectedQuestion] = []
    for block in blocks:
        offset = 0
        for raw_line in block.content.splitlines(keepends=True):
            line = raw_line.rstrip("\r\n")
            stripped = line.strip()
            start = offset + len(line) - len(line.lstrip())
            end = start + len(stripped)
            offset += len(raw_line)
            if not stripped or end <= start:
                continue
            reason = None
            candidate_text = re.sub(r"\]\([^)]*\)", "]", stripped)
            candidate_text = re.sub(r"https?://\S+", "", candidate_text)
            question_mark_positions = [
                position
                for position in (candidate_text.find("？"), candidate_text.find("?"))
                if position >= 0
            ]
            first_question_mark = min(question_mark_positions, default=-1)
            heading = _HEADING_PATTERN.match(line)
            heading_level = len(heading.group(1)) if heading else 0
            heading_text = heading.group(2).strip() if heading else ""
            if 0 <= first_question_mark <= 120:
                reason = "QUESTION_MARK"
            elif (
                heading
                and len(stripped) <= 160
                and (
                    _QUESTION_LEAD_PATTERN.search(heading_text)
                    or (
                        _NUMBERED_TITLE_PATTERN.match(heading_text)
                        and (
                            heading_level >= 3
                            or _TITLE_QUESTION_HINT_PATTERN.search(heading_text)
                        )
                    )
                )
            ):
                reason = "HEADING"
            elif len(stripped) <= 160 and _QUESTION_LEAD_PATTERN.search(candidate_text):
                reason = "QUESTION_PATTERN"
            if reason is None:
                continue
            if any(
                start < covered_end and end > covered_start
                for covered_start, covered_end in covered.get(block.key, [])
            ):
                continue
            misses.append(
                SuspectedQuestion(
                    block_key=block.key,
                    block_ordinal=block.ordinal,
                    locator=block.locator,
                    start=start,
                    end=end,
                    quote=stripped,
                    reason=reason,
                )
            )
            if len(misses) >= limit:
                return misses
    return misses
