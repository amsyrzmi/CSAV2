"""LG05: agent code carries no hard-coded chat, workflow, or tenant identifiers."""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_PATTERNS = [
    re.compile(r"\b\d{8,}\b"),
    re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I),
    re.compile(r"workflow_id|chat_id\s*=\s*['\"]?\d"),
]


def test_agents_and_sql_have_no_hardcoded_ids() -> None:
    files = [*(_ROOT / "csa" / "agents").rglob("*.py")]
    files += [*(_ROOT / "db" / "sql" / "orders").glob("*.sql")]
    files += [*(_ROOT / "db" / "sql" / "agents").glob("*.sql")]
    assert files
    offenders = [
        f"{path.relative_to(_ROOT)}: {pattern.pattern}"
        for path in files
        for pattern in _PATTERNS
        if pattern.search(path.read_text())
    ]
    assert offenders == []
