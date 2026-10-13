"""Deterministic redaction for common email, phone, and payment-card values."""

from __future__ import annotations

import re

_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_PHONE = re.compile(r"(?<!\w)(?:\+?60|0)[\d ()-]{7,16}\d(?!\w)")


def _luhn_valid(value: str) -> bool:
    digits = [int(char) for char in value if char.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def redact(text: str) -> str:
    """Replace common PII with stable placeholders; raw text must remain separate.

    This intentionally does not promise broad named-entity redaction. It covers
    email addresses, Malaysian phone-number forms, and Luhn-valid card numbers.
    """
    emails: dict[str, str] = {}
    phones: dict[str, str] = {}
    cards: dict[str, str] = {}

    def replace(values: dict[str, str], label: str, value: str) -> str:
        if value not in values:
            values[value] = f"[{label}_{len(values)}]"
        return values[value]

    redacted = _EMAIL.sub(lambda match: replace(emails, "EMAIL", match.group()), text)
    redacted = _CARD.sub(
        lambda match: (
            replace(cards, "CARD", match.group()) if _luhn_valid(match.group()) else match.group()
        ),
        redacted,
    )
    return _PHONE.sub(
        lambda match: replace(phones, "PHONE", match.group()),
        redacted,
    )
