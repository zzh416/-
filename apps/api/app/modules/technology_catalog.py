"""Canonical technology names shared by profiles and deterministic filtering."""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Technology:
    canonical_name: str
    display_name: str
    category: str
    aliases: tuple[str, ...]
    parent: str | None = None


TECHNOLOGIES = (
    Technology("python", "Python", "LANGUAGE", ("python", "py")),
    Technology("java", "Java", "LANGUAGE", ("java",)),
    Technology("javascript", "JavaScript", "LANGUAGE", ("javascript", "js")),
    Technology("typescript", "TypeScript", "LANGUAGE", ("typescript", "ts")),
    Technology("cpp", "C++", "LANGUAGE", ("c++", "cpp", "c plus plus")),
    Technology("go", "Go", "LANGUAGE", ("go", "golang")),
    Technology("rust", "Rust", "LANGUAGE", ("rust",)),
    Technology("database", "数据库通用", "DATABASE_GENERAL", ("数据库", "database", "db")),
    Technology("sql", "SQL", "DATABASE_GENERAL", ("sql",), "database"),
    Technology("mysql", "MySQL", "DATABASE_PRODUCT", ("mysql",), "database"),
    Technology("postgresql", "PostgreSQL", "DATABASE_PRODUCT", ("postgresql", "postgres"), "database"),
    Technology("redis", "Redis", "DATABASE_PRODUCT", ("redis",), "database"),
    Technology("mongodb", "MongoDB", "DATABASE_PRODUCT", ("mongodb", "mongo"), "database"),
    Technology("fastapi", "FastAPI", "FRAMEWORK", ("fastapi",), "python"),
    Technology("django", "Django", "FRAMEWORK", ("django",), "python"),
    Technology("flask", "Flask", "FRAMEWORK", ("flask",), "python"),
    Technology("spring", "Spring", "FRAMEWORK", ("spring", "spring boot", "springboot"), "java"),
    Technology("langgraph", "LangGraph", "FRAMEWORK", ("langgraph",), "python"),
    Technology("docker", "Docker", "TOOL", ("docker",)),
    Technology("kubernetes", "Kubernetes", "TOOL", ("kubernetes", "k8s")),
    Technology("git", "Git", "TOOL", ("git",)),
    Technology("linux", "Linux", "PLATFORM", ("linux",)),
)

_BY_CANONICAL = {item.canonical_name: item for item in TECHNOLOGIES}
_BY_ALIAS = {
    alias.casefold(): item
    for item in TECHNOLOGIES
    for alias in (item.canonical_name, item.display_name, *item.aliases)
}


def resolve_technology(value: str) -> Technology:
    normalized = value.strip().casefold()
    known = _BY_ALIAS.get(normalized)
    if known is not None:
        return known
    canonical = re.sub(r"[^a-z0-9+#.\u4e00-\u9fff]+", "-", normalized).strip("-")
    if not canonical:
        raise ValueError("技术名称不能为空")
    return Technology(canonical, value.strip(), "OTHER", (value.strip(),))


def get_technology(canonical_name: str) -> Technology | None:
    return _BY_CANONICAL.get(canonical_name)
