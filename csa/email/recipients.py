"""Pure recipient rules: inside or outside the company, and staff placeholders."""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

PUBLIC_PROVIDERS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "icloud.com",
        "me.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
    }
)

STAFF_PLACEHOLDER = "[staff:"


class Recipient(NamedTuple):
    address: str
    outside: bool


def _domain(address: str) -> str:
    return address.rpartition("@")[2].strip().lower()


def classify(
    addresses: Sequence[str], *, directory_emails: frozenset[str], owner_address: str
) -> list[Recipient]:
    """Mark each address as outside unless it is in the tenant directory or the owner's own domain.

    The owner's domain counts as the company only when it is not a public provider.
    """
    directory = {email.strip().lower() for email in directory_emails}
    owner = owner_address.strip().lower()
    owner_domain = _domain(owner)
    company_domain = owner_domain if owner_domain and owner_domain not in PUBLIC_PROVIDERS else None
    result: list[Recipient] = []
    for address in addresses:
        folded = address.strip().lower()
        inside = (
            folded in directory
            or folded == owner
            or (company_domain is not None and _domain(folded) == company_domain)
        )
        result.append(Recipient(address, not inside))
    return result


def has_staff_placeholder(body: str) -> bool:
    """True when the draft still holds a `[STAFF: ...]` gap that a person must fill."""
    return STAFF_PLACEHOLDER in body.lower()
