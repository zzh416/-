"""Keep domain modules independent from the HTTP route layer."""
from __future__ import annotations

import ast
from pathlib import Path


MODULES_DIR = Path(__file__).resolve().parents[1] / "app" / "modules"


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
