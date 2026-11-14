"""Billing cycles: the 5th 00:00 to the next 5th 00:00, Asia/Kuala_Lumpur.

Pure date arithmetic, no I/O. A cycle is a pair of local dates; its end is exclusive and is
always the next 5th, so a date belongs to exactly one cycle. A timestamp belongs to the cycle
containing its Kuala Lumpur local date: 4 Sep 2026 16:00:00Z is 5 Sep 00:00 there, the first
instant of the cycle 5 Sep to 5 Oct.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

__all__ = ["KL", "Cycle", "cycle_containing", "cycle_of", "cycle_range", "days_in", "kl_today"]

KL = ZoneInfo("Asia/Kuala_Lumpur")
_START_DAY = 5


def _next_fifth(start: date) -> date:
    if start.month == 12:
        return date(start.year + 1, 1, _START_DAY)
    return date(start.year, start.month + 1, _START_DAY)


@dataclass(frozen=True)
class Cycle:
    """One billing cycle: ``start`` (a 5th) up to ``end`` (the next 5th, exclusive)."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start.day != _START_DAY:
            raise ValueError(f"a cycle starts on a 5th, not {self.start.isoformat()}")
        if self.end != _next_fifth(self.start):
            raise ValueError(
                f"a cycle starting {self.start.isoformat()} ends on"
                f" {_next_fifth(self.start).isoformat()}, not {self.end.isoformat()}"
            )


def cycle_containing(d: date) -> Cycle:
    """The cycle a Kuala Lumpur local date belongs to (1 to 4 of a month: the previous 5th's)."""
    if d.day >= _START_DAY:
        start = date(d.year, d.month, _START_DAY)
    elif d.month == 1:
        start = date(d.year - 1, 12, _START_DAY)
    else:
        start = date(d.year, d.month - 1, _START_DAY)
    return Cycle(start, _next_fifth(start))


def kl_today(now: datetime) -> date:
    """The Kuala Lumpur date at ``now`` (the ``today`` the plan rules take). Aware only."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("kl_today needs a datetime with a timezone")
    return now.astimezone(KL).date()


def cycle_of(ts: datetime) -> Cycle:
    """The cycle containing ``ts``'s Kuala Lumpur local date. ``ts`` must carry a timezone."""
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError("cycle_of needs a datetime with a timezone")
    return cycle_containing(kl_today(ts))


def days_in(cycle: Cycle) -> int:
    """The number of days in a full cycle (28 to 31)."""
    return (cycle.end - cycle.start).days


def cycle_range(start: Cycle, end: Cycle) -> list[Cycle]:
    """Every cycle from ``start`` to ``end``, both included, oldest first."""
    if end.start < start.start:
        raise ValueError(
            f"cycle_range: {end.start.isoformat()} is before {start.start.isoformat()}"
        )
    cycles = [start]
    while cycles[-1] != end:
        cycles.append(Cycle(cycles[-1].end, _next_fifth(cycles[-1].end)))
    return cycles
