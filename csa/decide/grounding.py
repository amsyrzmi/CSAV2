"""Deterministic reply grounding: numbers in a draft must come from facts or the customer."""

from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_URL = re.compile(r"https?://\S+", re.IGNORECASE)


def _values(text: str) -> set[Decimal]:
    found: set[Decimal] = set()
    for token in _NUMBER.findall(text):
        try:
            found.add(Decimal(token.replace(",", "")).normalize())
        except InvalidOperation:
            continue
    return found


def ungrounded(draft: str, sources: Iterable[str]) -> list[str]:
    """Return numbers or URLs in ``draft`` that appear in none of ``sources``."""
    pool = list(sources)
    allowed: set[Decimal] = set()
    for source in pool:
        allowed |= _values(source)
    joined = "\n".join(pool).lower()
    problems = [
        token
        for token in _NUMBER.findall(draft)
        if _safe(token) is not None and _safe(token) not in allowed
    ]
    problems += [url for url in _URL.findall(draft) if url.lower().rstrip(".,)") not in joined]
    return problems


def _safe(token: str) -> Decimal | None:
    try:
        return Decimal(token.replace(",", "")).normalize()
    except InvalidOperation:
        return None
