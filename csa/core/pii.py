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


# What redact() puts in place of a card or phone number, e.g. "[CARD_0]" (the one shared pattern
# for code that must recognise it: the order-number contract and the fast-path guard).
NUMBER_PLACEHOLDER = r"\[(?:CARD|PHONE)_[0-9]{1,3}\]"


def redact(text: str) -> str:
    """Replace common PII with stable placeholders; raw text must remain separate.

    This intentionally does not promise broad named-entity redaction. It covers
    email addresses, Malaysian phone-number forms, and Luhn-valid card numbers.
    """
    return redact_with_values(text)[0]


def redact_with_values(text: str) -> tuple[str, dict[str, str]]:
    """``redact`` plus each placeholder's original text, from the same single pass.

    For server code that must act on a value the model only saw as a placeholder (a TikTok
    order number that looks like a card). The values never go to a model, a log or a reply.
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
    redacted = _PHONE.sub(
        lambda match: replace(phones, "PHONE", match.group()),
        redacted,
    )
    values = {
        placeholder: original
        for found in (emails, cards, phones)
        for original, placeholder in found.items()
    }
    return redacted, values
