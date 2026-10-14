"""Clock abstraction for deterministic scheduled job execution and testing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """Protocol for reading the current time."""

    def now(self) -> datetime:
        """Return the current time with UTC timezone."""
        ...


class SystemClock:
    """Production clock returning real UTC time."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    """Controllable test clock that can jump forward."""

    def __init__(self, initial: datetime | None = None) -> None:
        self._current = initial or datetime.now(UTC)

    def now(self) -> datetime:
        return self._current

    def advance(self, duration: timedelta) -> datetime:
        self._current += duration
        return self._current

    def set(self, target: datetime) -> datetime:
        self._current = target
        return self._current
