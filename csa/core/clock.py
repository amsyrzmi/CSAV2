"""Clock helpers (LG01 skeleton).

A single source of UTC "now" so time can be injected/frozen in later tests.
"""  # LG01

from __future__ import annotations

from datetime import UTC, datetime


def now() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(UTC)
