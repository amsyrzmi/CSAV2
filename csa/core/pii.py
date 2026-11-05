"""Deterministic redaction for common email, phone, and payment-card values."""

from __future__ import annotations

import re

_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_PHONE = re.compile(r"(?<!\w)(?:\+?60|0)[\d ()-]{7,16}\d(?!\w)")
_EMAIL_PLACEHOLDER = re.compile(r"\[EMAIL_(\d+)\]")


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


def real_email(value: str, raw: str) -> str | None:
    """The address in ``raw`` that ``value`` names, or None when the message holds no such address.

    ``value`` is a placeholder such as ``[EMAIL_1]``, numbered the way `redact` numbers them (each
    distinct address once, in the order it first appears), or an address typed out. Either way the
    answer is an address the customer really wrote, in their own spelling: a model that saw only
    the redacted text can name one but never invent one.
    """
    found = list(dict.fromkeys(match.group() for match in _EMAIL.finditer(raw)))
    placeholder = _EMAIL_PLACEHOLDER.fullmatch(value.strip())
    if placeholder is not None:
        index = int(placeholder.group(1))
        return found[index] if index < len(found) else None
    wanted = value.strip().casefold()
    return next((address for address in found if address.casefold() == wanted), None)


def is_email_placeholder(value: str) -> bool:
    return _EMAIL_PLACEHOLDER.fullmatch(value.strip()) is not None
