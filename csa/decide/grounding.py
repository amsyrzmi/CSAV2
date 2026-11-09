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


def unsupported_figures(text: str, sources: Iterable[str]) -> list[tuple[int, int]]:
    """Spans of ``text`` holding a number or URL that appears in none of ``sources``.

    The same test as ``ungrounded``, with positions, so a caller can replace each one (a batch
    email draft puts a ``[STAFF: ...]`` placeholder there). Spans are in order and never overlap:
    a URL is one span, and the numbers inside it are not counted again. A URL's trailing
    ``.``, ``,`` or ``)`` stays outside its span.
    """
    pool = list(sources)
    allowed: set[Decimal] = set()
    for source in pool:
        allowed |= _values(source)
    joined = "\n".join(pool).lower()
    spans: list[tuple[int, int]] = []
    links: list[tuple[int, int]] = []
    for match in _URL.finditer(text):
        url = match.group().rstrip(".,)")
        links.append((match.start(), match.start() + len(url)))
        if url.lower() not in joined:
            spans.append(links[-1])
    for match in _NUMBER.finditer(text):
        inside = any(start <= match.start() < end for start, end in links)
        value = _safe(match.group())
        if not inside and value is not None and value not in allowed:
            spans.append((match.start(), match.end()))
    return sorted(spans)


def _safe(token: str) -> Decimal | None:
    try:
        return Decimal(token.replace(",", "")).normalize()
    except InvalidOperation:
        return None
