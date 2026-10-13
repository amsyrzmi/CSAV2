"""Prove only csa/graph/ imports langgraph (LG01 §2.3, acceptance artifact).

Walks every ``.py`` under ``csa/`` and ``apps/`` except ``csa/graph/``, parses
each with :mod:`ast`, and asserts no ``langgraph`` / ``langgraph_checkpoint``
import appears. This is self-contained (no config to weaken) and complements the
import-linter contract.
"""  # LG01

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCAN_ROOTS = (_REPO_ROOT / "csa", _REPO_ROOT / "apps")
_EXEMPT_DIR = _REPO_ROOT / "csa" / "graph"
_FORBIDDEN_TOP_LEVEL = {"langgraph", "langgraph_checkpoint"}


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root in _SCAN_ROOTS:
        for path in root.rglob("*.py"):
            if _EXEMPT_DIR in path.parents or path == _EXEMPT_DIR:
                continue
            files.append(path)
    return files


def _forbidden_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in _FORBIDDEN_TOP_LEVEL:
                    hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            top = node.module.split(".")[0]
            if top in _FORBIDDEN_TOP_LEVEL:
                hits.append(node.module)
    return hits


def test_only_graph_imports_langgraph() -> None:
    scanned = _python_files()
    assert scanned, "expected to find Python files under csa/ and apps/"
    violations: dict[str, list[str]] = {}
    for path in scanned:
        hits = _forbidden_imports(path)
        if hits:
            violations[str(path.relative_to(_REPO_ROOT))] = hits
    assert not violations, f"langgraph imported outside csa/graph/: {violations}"
