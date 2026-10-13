"""Shared fact-formatting helpers so every agent renders money and dates alike."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo


def money(amount: Decimal | float | int | str, currency: str = "MYR") -> str:
    value = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{currency} {value:,.2f}"


def day(value: datetime | None, timezone: str) -> str:
    if value is None:
        return "unknown"
    return value.astimezone(ZoneInfo(timezone)).strftime("%d %b %Y")


def items_text(items: Iterable[Mapping[str, Any]]) -> str:
    parts = [f"{int(item.get('qty', 1))} x {item.get('name') or item.get('sku')}" for item in items]
    return ", ".join(parts) if parts else "no items"
