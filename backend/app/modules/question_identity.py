"""Conservative normalization for exact-equivalent interview questions."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher


def question_identity(prompt: str) -> str:
    """Ignore numbering and presentation differences without guessing semantics."""

    normalized = unicodedata.normalize("NFKC", prompt).casefold()
    normalized = re.sub(r"^\s{0,3}#{1,6}\s*", "", normalized)
    normalized = re.sub(r"^\s*[-+*>▶►▸•·]+\s*", "", normalized)
    normalized = re.sub(
        r"^\s*\d+(?:\.\d+)*(?:[.)、．]|(?=\s))\s*",
        "",
        normalized,
    )
    normalized = re.sub(r"[*_`#]", "", normalized)
    normalized = re.sub(r"\s+", "", normalized)
    return re.sub(r"[?!.。！？；;:：]+$", "", normalized)


def question_similarity(left: str, right: str) -> float:
    """Return a review hint score; callers must never use it for automatic merges."""

    left_identity = question_identity(left)
    right_identity = question_identity(right)
    if not left_identity or not right_identity:
        return 0.0
    if left_identity == right_identity:
        return 1.0
    return SequenceMatcher(None, left_identity, right_identity).ratio()
