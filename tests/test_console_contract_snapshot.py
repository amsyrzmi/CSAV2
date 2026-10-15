"""Keep the published frontend contract synchronized with actual Pydantic routes."""

from __future__ import annotations

import json

from scripts.export_console_openapi import OUTPUT, contract


def test_published_console_contract_matches_routes() -> None:
    assert json.loads(OUTPUT.read_text(encoding="utf-8")) == contract(), (
        "Run python -m scripts.export_console_openapi and review the contract changes"
    )
