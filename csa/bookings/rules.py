"""Who may override which booking rule.

`findings_for` says which soft rules a proposed time breaks; `decide` says what the person acting
may do about them. Double bookings are not here: the database refuses those for everyone.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from csa.bookings.availability import (
    Calendar,
    hours_for,
    on_leave,
    touches_closed_day,
    within_hours,
)

Actor = Literal["bot", "staff", "admin"]
Finding = Literal["outside_hours", "closed_day", "short_notice", "on_leave"]

# What each actor may do about a finding. Absent means "allowed with nothing said".
_ACKNOWLEDGE: dict[Actor, frozenset[Finding]] = {
    "bot": frozenset(),
    "staff": frozenset({"outside_hours", "closed_day"}),
    "admin": frozenset({"outside_hours", "closed_day", "on_leave"}),
}
_ALLOWED: dict[Actor, frozenset[Finding]] = {
    "bot": frozenset(),
    "staff": frozenset({"short_notice"}),
    "admin": frozenset({"short_notice"}),
}


@dataclass(frozen=True)
class Decision:
    """`allowed` is true only when nothing is refused and nothing is left to acknowledge."""

    allowed: bool
    needs_acknowledging: set[Finding]
    refused: set[Finding]


def findings_for(
    calendar: Calendar,
    start: datetime,
    end: datetime,
    people: Sequence[UUID],
    resources: Sequence[UUID],
    min_notice: timedelta,
    now: datetime,
) -> set[Finding]:
    """The soft rules that booking [start, end) for these people and resources would break."""
    found: set[Finding] = set()
    subjects: Sequence[UUID | None] = [*people, *resources] or [None]
    if any(not within_hours(calendar, hours_for(calendar, s), start, end) for s in subjects):
        found.add("outside_hours")
    if touches_closed_day(calendar, start, end):
        found.add("closed_day")
    if start - now < min_notice:
        found.add("short_notice")
    if any(on_leave(calendar, s, start, end) for s in subjects if s is not None):
        found.add("on_leave")
    return found


def decide(actor: Actor, findings: set[Finding], acknowledged: set[Finding]) -> Decision:
    """Apply the owner's table.

    The bot is refused every finding. Staff are refused leave, must acknowledge hours and closed
    days, and are allowed short notice. An Admin acknowledges hours, closed days and leave, and is
    allowed short notice. Acknowledging a finding never overrides a refusal.
    """
    acknowledgeable = _ACKNOWLEDGE[actor]
    refused: set[Finding] = {f for f in findings if f not in acknowledgeable | _ALLOWED[actor]}
    needs: set[Finding] = {f for f in findings if f in acknowledgeable and f not in acknowledged}
    return Decision(allowed=not refused and not needs, needs_acknowledging=needs, refused=refused)
