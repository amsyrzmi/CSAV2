"""Source scan: every model client is built with a meter, and only evals may use ``NullMeter``."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.conftest import REPO_ROOT

CLIENTS = {"GroqClient", "GeminiClient"}
EVALS = REPO_ROOT / "csa" / "evals"


def _sources() -> list[Path]:
    return sorted(
        path
        for root in ("apps", "csa")
        for path in (REPO_ROOT / root).rglob("*.py")
        if ".venv" not in path.parts
    )


def _name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def test_every_client_construction_passes_a_meter() -> None:
    missing: list[str] = []
    seen = 0
    for path in _sources():
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.Call) and _name(node.func) in CLIENTS:
                seen += 1
                if "meter" not in {kw.arg for kw in node.keywords}:
                    missing.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert seen >= 8, (
        "the scan found too few client constructions; it is looking in the wrong place"
    )
    assert missing == []


def test_only_evals_may_use_the_null_meter() -> None:
    offenders: list[str] = []
    for path in _sources():
        if EVALS in path.parents or path == REPO_ROOT / "csa" / "metering" / "meter.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.ImportFrom) and any(a.name == "NullMeter" for a in node.names):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
            elif isinstance(node, ast.Name | ast.Attribute) and _name(node) == "NullMeter":
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert offenders == []
