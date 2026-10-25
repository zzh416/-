"""Render a saved review list without generating answers or refreshing its sources."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Mapping, Sequence
from urllib.parse import quote, urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo

from app.config import settings
from app.models import RecommendationItem
from app.schemas.recommendations import RecommendationRunView
from app.schemas.sources import SourceStatistics


def _inline(value: str) -> str:
    """Keep metadata on one line and prevent it becoming Markdown/HTML markup."""
    value = " ".join(value.split())
    return re.sub(r"([\\`*_{}\[\]<>()#!|])", r"\\\1", value)


def _safe_url(value: str | None) -> str | None:
    if not value or any(ord(char) < 33 for char in value):
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return None
        if parts.username is not None or parts.password is not None:
            return None
        return quote(value, safe=":/?#@!$&'*+,;=%~._-")
    except ValueError:
        return None


def _time(value: datetime) -> str:
    return value.astimezone(ZoneInfo(settings.app_timezone)).strftime("%Y-%m-%d %H:%M:%S %Z")


def _sources(item: RecommendationItem) -> str:
    if item.source_statistics is None:
        return "### 来源\n\n来源：未记录"
    stats = SourceStatistics.model_validate(item.source_statistics)
    lines = [
        "### 来源（清单快照）", "",
        f"资料 {stats.source_count} 份 · 面经 {stats.interview_source_count} · 知识库 {stats.knowledge_source_count}",
        "",
    ]
    for source in stats.sources:
        title = _inline(source.title)
        url = _safe_url(source.source_url)
        label = f"[{title}]({url})" if url else title
        purpose = "面经" if source.purpose == "INTERVIEW_EXPERIENCE" else "知识库"
        day = source.source_date.isoformat() if source.source_date else "日期未记录"
        lines.append(f"- {label} · {purpose} · {day}")
    if not stats.sources:
        lines.append("来源明细：未记录")
    return "\n".join(lines)


def render_prep_package(
    run: RecommendationRunView,
    items: Sequence[RecommendationItem],
    *,
    exported_at: datetime,
    notes: Mapping[UUID, str],
    include_notes: bool,
) -> str:
    """Inputs are owner-scoped by the route; only INCLUDED snapshots are rendered."""
    included = sorted((item for item in items if item.decision == "INCLUDED"), key=lambda item: item.position)
    sections = [
        "# 个人面试备考包",
        "\n".join([
            f"- 导出时间：{_time(exported_at)}",
            f"- 清单创建时间：{_time(run.created_at)}",
            f"- 清单 ID：{run.id}",
            f"- 画像版本：{run.profile_version}",
            f"- 算法版本：{_inline(run.algorithm_version)}",
            f"- 导出题目：{len(included)}（全部进入清单的题目，跨全部分页）",
            f"- 个人备注：{'包含导出时的当前备注' if include_notes else '未包含'}",
        ]),
        "> 当前清单：导出时与现有画像、偏好及题库一致。" if run.is_current else
        "> **历史清单**：画像、偏好、题库或算法已有变化；以下使用清单保存时的题目、答案和来源。",
        "## 画像与偏好（清单快照）",
        f"目标岗位：{_inline(run.profile_snapshot.target_role or '未设置')}",
    ]
    skills = run.profile_snapshot.skills
    sections.append("技能：" + ("、".join(
        f"{_inline(skill.display_name)}（{ {'CONFIRMED': '已确认', 'PENDING': '待确认', 'REJECTED': '已排除'}.get(skill.review_status, _inline(skill.review_status))}）"
        for skill in skills
    ) if skills else "未记录"))
    scope_labels = {"QUESTION": "题目", "ANSWER": "答案", "BOTH": "题目和答案"}
    sections.append("偏好规则：\n\n" + ("\n".join(
        f"- {'排除' if rule.rule_type == 'EXCLUDE' else '优先'} {_inline(rule.technology)} · {scope_labels[rule.scope]} · 版本 {rule.version}"
        for rule in run.rule_snapshot
    ) if run.rule_snapshot else "未设置"))
    sections.append("来源统计仅反映资料库中的记录，不代表实际面试概率。")
    for number, item in enumerate(included, 1):
        sections.extend(["---", f"## 第 {number} 题", "### 问题", item.prompt.strip()])
        if item.usable_answer_state == "ANSWERED" and item.answer_content and item.answer_content.strip():
            sections.extend(["### 资料答案", item.answer_content.strip()])
        elif item.answer_state == "SOURCE_ANSWER":
            sections.append("> 来源答案当前不适用，未导出答案正文。")
        sections.append(_sources(item))
        if include_notes and item.question_id is not None and (note := notes.get(item.question_id)):
            sections.extend(["### 导出时的个人备注", note.strip()])
    return "\n\n".join(sections) + "\n"
