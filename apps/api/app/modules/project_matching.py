"""Optional, explainable project suggestions after the original hard filtering."""
from dataclasses import dataclass

from app.modules.recommendation_service import _alias_pattern
from app.modules.technology_catalog import TECHNOLOGIES

PROJECT_MATCH_VERSION = "project-topics-v1"

# These topics are local to project suggestions: do not alter skill/exclusion semantics.
_TOPICS = [(item.display_name, (item.display_name, item.canonical_name, *item.aliases)) for item in TECHNOLOGIES]
_TOPICS += [
    ("RAG", ("rag", "检索增强")),
    ("向量检索", ("向量检索", "向量数据库", "embedding", "embeddings", "向量化")),
    ("重排", ("重排", "rerank", "reranker")),
    ("多 Agent", ("multi-agent", "multi agent", "多agent", "多 agent", "多智能体")),
    ("消息队列", ("消息队列", "kafka", "rabbitmq")),
    ("缓存", ("缓存", "cache")),
    ("权限认证", ("权限", "鉴权", "jwt", "oauth")),
]
_PATTERNS = [(label, tuple(_alias_pattern(alias) for alias in aliases)) for label, aliases in _TOPICS]


def _topics(text: str) -> frozenset[str]:
    return frozenset(label for label, patterns in _PATTERNS if any(pattern.search(text) for pattern in patterns))


@dataclass(frozen=True)
class ProjectTopics:
    id: str
    name: str
    topics: frozenset[str]


def prepare_projects(projects: list[dict]) -> list[ProjectTopics]:
    return [ProjectTopics(item["id"], item["name"], _topics(item["name"] + "\n" + item["description"]))
            for item in projects if item["review_status"] == "CONFIRMED"]


def match_projects(prompt: str, projects: list[ProjectTopics]) -> list[dict]:
    if not projects:
        return []
    topics = _topics(prompt)
    return [{"project_id": project.id, "project_name": project.name,
             "matched_topics": sorted(topics & project.topics)}
            for project in projects if topics & project.topics]
