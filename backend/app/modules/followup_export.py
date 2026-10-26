"""Export AI suggestions separately from the original prep package."""
from datetime import datetime, timezone

from app.modules.recommendation_export import _inline, _safe_url, _time
from app.schemas.sources import SourceStatistics


def render_followups(run, *, is_current):
    parts = ["# 项目追问链（AI 推演）", f"项目：{_inline(run.project_snapshot['name'])}",
             f"导出时间：{_time(datetime.now(timezone.utc))}", f"清单 ID：{run.recommendation_run_id}",
             f"模型：{_inline(run.provider or '未记录')} / {_inline(run.model or '未记录')} · 规则：{run.algorithm_version}",
             "当前结果" if is_current else "**历史结果：当前画像、偏好或题库已变化。**",
             "> 以下追问是 AI 推演，非原文面试题；引用仅表明输入依据，不保证建议正确。本文件不生成答案。"]
    if run.rejection_counts:
        parts.append(f"未通过校验的候选：{sum(run.rejection_counts.values())} 条")
    for index, seed in enumerate(run.seeds, 1):
        parts.extend(["---", f"## 已有题目 {index}", seed["prompt"]])
        stats = SourceStatistics.model_validate(seed["source_statistics"]) if seed.get("source_statistics") else None
        if not stats or not stats.sources:
            parts.append("来源：未记录有效资料来源，不将此种子题宣称为原文题。")
        else:
            for source in stats.sources:
                url = _safe_url(source.source_url)
                title = _inline(source.title)
                parts.append(f"- 来源：[{title}]({url})" if url else f"- 来源：{title}")
        for item in run.items:
            if item["seed_item_id"] != seed["id"]:
                continue
            parts.extend([f"### 第 {item['level']} 层 · AI 推演", _inline(item["question"]),
                          "项目说明依据：\n\n> " + _inline(item["project_quote"]),
                          "种子题依据：\n\n> " + _inline(item["seed_quote"])])
    return "\n\n".join(parts) + "\n"
