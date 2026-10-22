"""Deterministic technology tagging and explainable personalization rules."""
from __future__ import annotations

from dataclasses import dataclass
import re

from app.modules.technology_catalog import TECHNOLOGIES, Technology, get_technology


ALGORITHM_VERSION = "p3.5-v3"


@dataclass(frozen=True)
class TechnologyMatch:
    canonical_name: str
    display_name: str
    category: str


@dataclass(frozen=True)
class SkillSnapshot:
    canonical_name: str
    display_name: str
    category: str
    review_status: str


@dataclass(frozen=True)
class PreferenceSnapshot:
    rule_type: str
    canonical_technology: str
    technology: str
    category: str
    scope: str


@dataclass(frozen=True)
class RecommendationEvaluation:
    decision: str
    score: float
    answer_state: str
    usable_answer_state: str
    question_technologies: tuple[TechnologyMatch, ...]
    answer_technologies: tuple[TechnologyMatch, ...]
    reason_codes: tuple[str, ...]
    reasons: tuple[str, ...]


def _alias_pattern(alias: str) -> re.Pattern[str]:
    escaped = re.escape(alias)
    if re.fullmatch(r"[a-z0-9_ ]+", alias, flags=re.IGNORECASE):
        return re.compile(rf"(?<![a-z0-9_]){escaped}(?![a-z0-9_])", re.IGNORECASE)
    return re.compile(escaped, re.IGNORECASE)


def _mentions(text: str, technology: Technology) -> bool:
    return any(
        _alias_pattern(alias).search(text) is not None
        for alias in (technology.canonical_name, technology.display_name, *technology.aliases)
        if alias
    )


def _ancestors(canonical_name: str) -> tuple[str, ...]:
    result: list[str] = []
    current = get_technology(canonical_name)
    while current is not None and current.parent is not None:
        result.append(current.parent)
        current = get_technology(current.parent)
    return tuple(result)


def detect_technologies(text: str | None) -> list[TechnologyMatch]:
    if not text:
        return []
    found = {item.canonical_name for item in TECHNOLOGIES if _mentions(text, item)}
    for canonical_name in tuple(found):
        found.update(_ancestors(canonical_name))
    return [
        TechnologyMatch(item.canonical_name, item.display_name, item.category)
        for item in TECHNOLOGIES
        if item.canonical_name in found
    ]


def _matches_dependency(rule_or_skill: str, dependency: str) -> bool:
    return rule_or_skill == dependency or rule_or_skill in _ancestors(dependency)


def _related_to_skill(skill: str, dependency: str) -> bool:
    return _matches_dependency(skill, dependency)


def _language_roots(canonical_names: set[str]) -> set[str]:
    roots: set[str] = set()
    for canonical_name in canonical_names:
        technology = get_technology(canonical_name)
        if technology is None:
            continue
        chain = (canonical_name, *_ancestors(canonical_name))
        for item_name in chain:
            item = get_technology(item_name)
            if item is not None and item.category == "LANGUAGE":
                roots.add(item.canonical_name)
    return roots


def _rule_matches(rule: PreferenceSnapshot, dependencies: tuple[TechnologyMatch, ...]) -> bool:
    return any(
        _matches_dependency(rule.canonical_technology, item.canonical_name)
        for item in dependencies
    )


def build_recommendation(
    *,
    prompt: str,
    answer: str | None,
    skills: list[SkillSnapshot],
    rules: list[PreferenceSnapshot],
) -> RecommendationEvaluation:
    question_technologies = tuple(detect_technologies(prompt))
    answer_technologies = tuple(detect_technologies(answer))
    question_names = {item.canonical_name for item in question_technologies}
    answer_names = {item.canonical_name for item in answer_technologies}
    reason_codes: list[str] = []
    reasons: list[str] = []
    score = 50.0

    confirmed_skill_names = {
        item.canonical_name for item in skills if item.review_status == "CONFIRMED"
    }
    candidate_skills = [item for item in skills if item.review_status != "REJECTED"]
    confirmed_languages = _language_roots(confirmed_skill_names)
    question_languages = _language_roots(question_names)
    answer_languages = _language_roots(answer_names)

    question_exclusion = next(
        (
            rule
            for rule in rules
            if rule.rule_type == "EXCLUDE"
            and rule.scope in {"QUESTION", "BOTH"}
            and _rule_matches(rule, question_technologies)
        ),
        None,
    )
    language_question_mismatch = bool(
        confirmed_languages and question_languages and confirmed_languages.isdisjoint(question_languages)
    )
    decision = "INCLUDED"
    if question_exclusion is not None:
        decision = "EXCLUDED"
        reason_codes.append("QUESTION_EXCLUDED_RULE")
        reasons.append(f"题干涉及已排除的 {question_exclusion.technology}")
    elif language_question_mismatch:
        decision = "EXCLUDED"
        reason_codes.append("QUESTION_LANGUAGE_MISMATCH")
        reasons.append("题干使用的编程语言不在已确认技能中")

    answer_state = "SOURCE_ANSWER" if answer and answer.strip() else "MISSING"
    answer_exclusion = next(
        (
            rule
            for rule in rules
            if rule.rule_type == "EXCLUDE"
            and rule.scope in {"ANSWER", "BOTH"}
            and _rule_matches(rule, answer_technologies)
        ),
        None,
    )
    language_answer_mismatch = bool(
        answer_state == "SOURCE_ANSWER"
        and confirmed_languages
        and answer_languages
        and confirmed_languages.isdisjoint(answer_languages)
    )
    usable_answer_state = answer_state
    if answer_state == "MISSING":
        reason_codes.append("NO_SOURCE_ANSWER")
        reasons.append("资料中没有可用答案")
    elif answer_exclusion is not None:
        usable_answer_state = "MISSING"
        reason_codes.append("ANSWER_EXCLUDED_RULE")
        reasons.append(f"原答案涉及已排除的 {answer_exclusion.technology}")
    elif language_answer_mismatch:
        usable_answer_state = "MISSING"
        reason_codes.append("ANSWER_LANGUAGE_MISMATCH")
        reasons.append("原答案使用的编程语言不在已确认技能中")
    else:
        usable_answer_state = "ANSWERED"

    matched_confirmed: set[str] = set()
    matched_candidates: set[str] = set()
    all_dependencies = question_names | answer_names
    for skill in candidate_skills:
        if not any(_related_to_skill(skill.canonical_name, item) for item in all_dependencies):
            continue
        if skill.review_status == "CONFIRMED":
            matched_confirmed.add(skill.display_name)
        else:
            matched_candidates.add(skill.display_name)
    if matched_confirmed:
        score += 30
        reason_codes.append("PROFILE_SKILL_MATCH")
        reasons.append(f"匹配已确认技能：{'、'.join(sorted(matched_confirmed))}")
    elif matched_candidates:
        score += 10
        reason_codes.append("PROFILE_CANDIDATE_MATCH")
        reasons.append(f"匹配待确认技能：{'、'.join(sorted(matched_candidates))}")

    matched_includes = {
        rule.technology
        for rule in rules
        if rule.rule_type == "INCLUDE"
        and (
            (rule.scope in {"QUESTION", "BOTH"} and _rule_matches(rule, question_technologies))
            or (rule.scope in {"ANSWER", "BOTH"} and _rule_matches(rule, answer_technologies))
        )
    }
    if matched_includes:
        score += 25
        reason_codes.append("PREFERENCE_MATCH")
        reasons.append(f"匹配优先偏好：{'、'.join(sorted(matched_includes))}")

    if not question_technologies:
        reason_codes.append("GENERAL_RELEVANCE")
        reasons.append("题干没有检测到需要排除的专属技术")
    if decision == "EXCLUDED":
        score = 0

    return RecommendationEvaluation(
        decision=decision,
        score=score,
        answer_state=answer_state,
        usable_answer_state=usable_answer_state,
        question_technologies=question_technologies,
        answer_technologies=answer_technologies,
        reason_codes=tuple(reason_codes),
        reasons=tuple(reasons),
    )
