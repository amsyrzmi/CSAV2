"""Invoice references and invoice ids.

A reference is what a customer types or transfers with: six characters from `ALPHABET` split 4 + 2
by a dash, then one check character (`XXXX-XXC`). The check is Luhn mod 31 over the alphabet's
indices with one change: a doubled value is reduced mod 31 (`2x mod 31`), not folded into the sum
of its two base-31 digits. The fold is only injective for an even base; for 31 it maps x and x + 15
to the same value, so the textbook algorithm misses those single wrong characters. Reducing mod 31
(a prime) multiplies by 2, which is invertible, so every single wrong character and every swap of
two neighbouring characters changes the sum and is caught. The references of one company are also
unique in the database.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

# 2-9 and A-Z without I, L and O: no 0, 1, I, L, O to mix up when read aloud or typed.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
BASE = len(ALPHABET)
BODY_LENGTH = 6
# The same pattern as the invoices_reference_chk constraint (migration 0590).
REFERENCE_PATTERN = r"^[2-9A-HJKMNP-Z]{4}-[2-9A-HJKMNP-Z]{3}$"

_INDEX = {char: i for i, char in enumerate(ALPHABET)}
_IGNORED = re.compile(r"[\s-]")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _weighted_sum(codes: list[int], *, double_first: bool) -> int:
    """The sum of `codes` read from the right, every other value doubled mod `BASE`."""
    total, double = 0, double_first
    for code in reversed(codes):
        total += code * 2 % BASE if double else code
        double = not double
    return total


def _codes(text: str) -> list[int]:
    try:
        return [_INDEX[char] for char in text]
    except KeyError as exc:
        raise ValueError(f"{exc.args[0]!r} is not in the reference alphabet") from exc


def check_char(body: str) -> str:
    """The check character that completes `body` (characters of `ALPHABET`)."""
    total = _weighted_sum(_codes(body), double_first=True)
    return ALPHABET[(BASE - total % BASE) % BASE]


def new_reference(randbelow: Callable[[int], int] = secrets.randbelow) -> str:
    """A fresh reference: six random alphabet characters and their check character."""
    body = "".join(ALPHABET[randbelow(BASE)] for _ in range(BODY_LENGTH))
    return f"{body[:4]}-{body[4:]}{check_char(body)}"


def normalise_reference(text: str) -> str | None:
    """A typed reference in its canonical form `XXXX-XXC`, or None when it cannot be one.

    Case, spaces and dashes are ignored. The result has seven alphabet characters and a valid
    check character; anything else (an ambiguous character, a wrong length, a typo) is None.
    """
    compact = _IGNORED.sub("", text)
    if not compact.isascii():  # before upper(): "\u00df".upper() is "SS"
        return None
    compact = compact.upper()
    if len(compact) != BODY_LENGTH + 1:
        return None
    if any(char not in _INDEX for char in compact):
        return None
    if check_char(compact[:BODY_LENGTH]) != compact[BODY_LENGTH]:
        return None
    return f"{compact[:4]}-{compact[4:]}"


def new_invoice_id(now: datetime, token: Callable[[int], str] = secrets.token_hex) -> str:
    """`inv_` + 13 hex digits of epoch milliseconds + 12 random hex digits, so ids sort by time."""
    if now.tzinfo is None:
        raise ValueError("new_invoice_id needs a timezone-aware time")
    millis = (now - _EPOCH) // timedelta(milliseconds=1)
    return f"inv_{millis:013x}{token(6)}"
