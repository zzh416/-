"""Server-owned source passages and lexical anchors for interview project questions."""
import hashlib
import re

from app.modules.project_matching import _topics
from app.modules.recommendation_service import _alias_pattern

_ASCII_TERM = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-_.+][A-Za-z0-9]+)*")
_COMMON_ENGLISH = {"the", "and", "with", "for", "from", "this", "that", "project", "system", "using", "use"}
_HAN_BREAKS = re.compile("使用|基于|实现|通过|进行|调用|支持|提高|以及|构建|建立|集成|功能|如何|为什么|什么|你们|你的|具体|和|的|为|并|与")
_GENERIC_HAN = {"项目名称", "核心工作", "设计实现", "系统实现", "进行处理", "技术方案", "实际使用", "功能实现", "项目背景", "业务场景", "解决问题", "这个项目"}


def _lexical_terms(text):
    ascii_terms = [m.group() for m in _ASCII_TERM.finditer(text)
                   if len(m.group()) >= 3 and m.group().casefold() not in _COMMON_ENGLISH]
    han_terms = [part for run in re.findall(r"[\u4e00-\u9fff]{4,}", text)
                 for part in _HAN_BREAKS.split(run) if len(part) >= 4 and part not in _GENERIC_HAN]
    return ascii_terms, han_terms


def anchor_terms(text):
    ascii_terms, han_terms = _lexical_terms(text)
    return list(dict.fromkeys([*ascii_terms, *sorted(_topics(text)), *(term[:16] for term in han_terms)]))[:24]


def project_evidence(project):
    """Each ID is tied to this project and an exact, bounded original passage."""
    passages = []
    for sentence in re.finditer(r"[^。！？；;]+[。！？；;]?", project["description"]):
        text = sentence.group()
        for start in range(0, len(text), 320):
            passage = text[start:start + 320].strip()
            if len(passage) < 4:
                continue
            key = hashlib.sha256((project["id"] + "\0" + passage).encode()).hexdigest()[:16]
            passages.append({"id": key, "text": passage, "anchor_terms": anchor_terms(passage)})
            if len(passages) == 64:
                return passages
    return passages


def resolve_source_quote(quote, description):
    """Ignore whitespace differences only; return actual source, never a paraphrase."""
    if not quote:
        return None
    compact_quote = re.sub(r"\s+", "", quote)
    if len(compact_quote) < 4:
        return None
    if quote in description:
        return quote
    positions = [index for index, char in enumerate(description) if not char.isspace()]
    compact_source = "".join(description[index] for index in positions)
    start = compact_source.find(compact_quote)
    if start < 0:
        return None
    return description[positions[start]:positions[start + len(compact_quote) - 1] + 1]


def related_to_evidence(question, passage):
    # The general project vocabulary is useful but not exhaustive (e.g. PyMuPDF/RRF/Ragas).
    if _topics(question) & _topics(passage):
        return True
    ascii_terms, source_han = _lexical_terms(passage)
    if any(_alias_pattern(term).search(question) for term in ascii_terms):
        return True
    _, question_han = _lexical_terms(question)
    source_grams = {term[i:i + 4] for term in source_han for i in range(len(term) - 3)}
    return any(term[i:i + 4] in source_grams and term[i:i + 4] not in _GENERIC_HAN
               for term in question_han for i in range(len(term) - 3))
