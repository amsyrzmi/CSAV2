"""Deterministic reply grounding: numbers in a draft must come from facts or the customer."""

from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
# The batch email check's links (``unsupported_figures``): a URL, a "www." link, or a bare host
# name, that is dotted words ending in a path ("shop.example/renew") or in a common top-level
# domain ("shop.example.com"). A host never starts right after "@", a word character, "." or
# "-", and never runs on into "@" or another ".word", so email addresses, "e.g.", "Mr.",
# "3.5 hours" and file names ("notes.pdf") are not links. Customer replies (``ungrounded``)
# keep the http(s) check.
_TLDS = "com|net|org|my|io|co|info|biz|app|shop|store"
_LINK = re.compile(
    r"https?://\S+|(?<![\w.@-])www\.\S+"
    r"|(?<![\w.@-])[a-z0-9-]+(?:\.[a-z0-9-]+)*"
    rf"\.(?:[a-z]{{2,}}/\S*|(?:{_TLDS})(?![\w@-]|\.\w)(?:/\S*)?)",
    re.IGNORECASE,
)


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
    """Spans of ``text`` holding a number or link that appears in none of ``sources``.

    The test of ``ungrounded``, with positions, so a caller can replace each one (a batch email
    draft puts a ``[STAFF: ...]`` placeholder there), and links without a scheme too (``_LINK``):
    a link is fine when its text appears in a source. Spans are in order and never overlap: a
    link is one span, and the numbers inside it are not counted again. A link's trailing ``.``,
    ``,`` or ``)`` stays outside its span.
    """
    pool = list(sources)
    allowed: set[Decimal] = set()
    for source in pool:
        allowed |= _values(source)
    joined = "\n".join(pool).lower()
    spans: list[tuple[int, int]] = []
    links: list[tuple[int, int]] = []
    for match in _LINK.finditer(text):
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
