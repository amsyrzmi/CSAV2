"""Contact fields and pure helpers for the customer inbox read side.

Everything here is personal data handling for the staff inbox: phone masking, hiding a
name that is only the contact's own number, the search term rules, and the list preview.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from csa.console.contracts import Contract
from csa.core.pii import redact

PREVIEW_LENGTH = 120
_MASK = "••••"
_NUMBER_ONLY = re.compile(r"[+\d\s().-]+")


class InboxContact(Contract):
    display_name: str | None
    telegram_username: str | None
    phone_masked: str | None
    # Set only by the single-conversation read; list rows carry the mask. Mutation results
    # carry no contact at all.
    phone: str | None = None
    kind: str
    known: bool


def _digits(value: str) -> str:
    return "".join(char for char in value if char.isdigit())


def mask_phone(phone: str | None) -> str | None:
    """Keep only the last four digits: "+60 12-555 0148" becomes "••••0148"."""
    digits = _digits(phone or "")
    if not digits:
        return None
    return _MASK + digits[-4:] if len(digits) >= 4 else _MASK


def display_name_or_none(full_name: str | None, phone: str | None) -> str | None:
    """The stored name, or None when it is blank or only the contact's own number.

    WhatsApp stores "+<number>" as the name when the customer has no profile name.
    """
    name = (full_name or "").strip()
    if not name:
        return None
    if phone and _NUMBER_ONLY.fullmatch(name) and _digits(name) == _digits(phone):
        return None
    return name


@dataclass(frozen=True)
class Search:
    like: str
    digits: str | None


def parse_search(q: str | None) -> Search | None:
    r"""Turn the raw search box text into a LIKE pattern and an optional digits-only term.

    None means no search. Text under two characters (after trimming and dropping one
    leading "@") raises ValueError. `%`, `_` and `\` are escaped so they only match
    themselves.
    """
    if q is None:
        return None
    term = q.strip()
    if term.startswith("@"):
        term = term[1:].strip()
    if len(term) < 2:
        raise ValueError("search needs at least two characters")
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    digits = _digits(term)
    return Search(like=f"%{escaped}%", digits=digits if len(digits) >= 2 else None)


def preview_text(body: str) -> str:
    """Redacted, whitespace-collapsed first 120 characters of a message body."""
    return " ".join(redact(body).split())[:PREVIEW_LENGTH]
