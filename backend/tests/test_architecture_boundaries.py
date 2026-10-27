"""Keep domain modules independent from the HTTP route layer."""
from __future__ import annotations

import ast
from pathlib import Path


MODULES_DIR = Path(__file__).resolve().parents[1] / "app" / "modules"


def _module_dependencies() -> dict[str, set[str]]:
    module_names = {path.stem for path in MODULES_DIR.glob("*.py")}
    dependencies = {name: set() for name in module_names}
    for path in sorted(MODULES_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "app.modules":
                    dependencies[path.stem].update(
                        alias.name for alias in node.names if alias.name in module_names
                    )
                elif (node.module or "").startswith("app.modules."):
                    dependency = node.module.split(".", 2)[2].split(".", 1)[0]
                    if dependency in module_names:
                        dependencies[path.stem].add(dependency)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("app.modules."):
                        dependency = alias.name.split(".", 2)[2].split(".", 1)[0]
                        if dependency in module_names:
                            dependencies[path.stem].add(dependency)
    return dependencies


def test_domain_modules_do_not_import_routes() -> None:
    violations: list[str] = []

    for path in sorted(MODULES_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.routes"):
                violations.append(f"{path.name}:{node.lineno} imports {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("app.routes"):
                        violations.append(f"{path.name}:{node.lineno} imports {alias.name}")

    assert violations == [], "Domain modules must not import HTTP routes:\n" + "\n".join(violations)


def test_domain_module_dependencies_are_acyclic() -> None:
    dependencies = _module_dependencies()
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(module: str) -> None:
        if module in visiting:
            start = visiting.index(module)
            cycle = " -> ".join([*visiting[start:], module])
            raise AssertionError(f"Domain module dependency cycle: {cycle}")
        if module in visited:
            return
        visiting.append(module)
        for dependency in sorted(dependencies[module]):
            visit(dependency)
        visiting.pop()
        visited.add(module)

    for module in sorted(dependencies):
        visit(module)
