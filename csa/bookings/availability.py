"""Which times are free: working hours, closed days, leave and existing bookings.

Everything here is pure. The caller loads a `Calendar` from the database and asks questions of it.
Times are compared as aware datetimes (in UTC where it matters), while hours, closed days and leave
are read in the company's own time zone, so a slot at 23:30 UTC lands on the right local day.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

# Working hours by weekday (Monday is 0), each day a list of (opens, closes) local times.
Hours = Mapping[int, list[tuple[time, time]]]

# What a company has until it sets hours of its own: Monday to Friday, 09:00 to 18:00.
DEFAULT_COMPANY_HOURS: Hours = {day: [(time(9, 0), time(18, 0))] for day in range(5)}


@dataclass(frozen=True)
class Busy:
    """A period a person or resource is taken, including that booking's own buffer."""

    subject_kind: str  # "person" or "resource"
    subject_id: UUID
    start: datetime
    end: datetime


@dataclass(frozen=True)
class Calendar:
    """Everything availability needs to know about one company.

    `overrides` and `leave` are keyed by the booking person's or resource's id, not by an HR
    employee id: the service translates when it loads leave.
    """

    timezone: ZoneInfo
    company_hours: Hours
    overrides: Mapping[UUID, Hours]
    closed_days: frozenset[date]
    leave: Mapping[UUID, list[tuple[date, date]]]
    busy: list[Busy]


@dataclass(frozen=True)
class Slot:
    """A free time and everyone and everything that could take it, in UTC."""

    start: datetime
    end: datetime
    people: tuple[UUID, ...]
    resources: tuple[UUID, ...]


def _utc(zone: ZoneInfo, day: date, at: time) -> datetime:
    return datetime.combine(day, at, tzinfo=zone).astimezone(UTC)


def touched_dates(zone: ZoneInfo, start: datetime, end: datetime) -> list[date]:
    """The local calendar days that the period [start, end) touches."""
    first = start.astimezone(zone).date()
    last = (end - timedelta(microseconds=1)).astimezone(zone).date()
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def hours_for(calendar: Calendar, subject_id: UUID | None) -> Hours:
    """The hours a subject works: their own if they have any, otherwise the company's."""
    if subject_id is not None and subject_id in calendar.overrides:
        return calendar.overrides[subject_id]
    return calendar.company_hours


def within_hours(calendar: Calendar, hours: Hours, start: datetime, end: datetime) -> bool:
    """True when [start, end) lies wholly inside one working period of its local day."""
    zone = calendar.timezone
    day = start.astimezone(zone).date()
    return any(
        _utc(zone, day, opens) <= start and end <= _utc(zone, day, closes)
        for opens, closes in hours.get(day.weekday(), ())
    )


def touches_closed_day(calendar: Calendar, start: datetime, end: datetime) -> bool:
    """True when any local day of [start, end) is one the company is closed."""
    days = touched_dates(calendar.timezone, start, end)
    return any(day in calendar.closed_days for day in days)


def on_leave(calendar: Calendar, subject_id: UUID, start: datetime, end: datetime) -> bool:
    """True when [start, end) touches a day of leave. Leave always covers whole local days."""
    days = touched_dates(calendar.timezone, start, end)
    return any(
        first <= days[-1] and days[0] <= last for first, last in calendar.leave.get(subject_id, ())
    )


def _is_busy(
    busy: Sequence[tuple[datetime, datetime]], start: datetime, end_with_buffer: datetime
) -> bool:
    return any(b_start < end_with_buffer and start < b_end for b_start, b_end in busy)


def _candidate_starts(zone: ZoneInfo, day: date, step: timedelta):
    """Local wall-clock times on `day` that fall on the step grid counted from local midnight."""
    naive = datetime.combine(day, time.min)
    while naive.date() == day:
        yield naive
        naive += step


def _require_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def free_slots(
    calendar: Calendar,
    *,
    required_people: Sequence[UUID],
    any_of_people: Sequence[UUID],
    required_resources: Sequence[UUID],
    any_of_resources: Sequence[UUID],
    duration: timedelta,
    buffer: timedelta,
    start_from: datetime,
    until: datetime,
    step: timedelta = timedelta(minutes=15),
    limit: int | None = None,
) -> list[Slot]:
    """Every free slot that starts at or after `start_from` and ends by `until`, earliest first, or
    just the first `limit` of them: the scan stops as soon as it has that many.

    Candidates fall on the `step` grid of local clock times. A slot is kept when each required
    person and resource is free, and at least one of each `any_of` group is. A subject is free when
    the slot lies wholly inside their working hours (their own, else the company's), no local day
    of it is a closed day or one of their leave days, and nothing busy overlaps the slot plus
    `buffer`. The `any_of` groups list every free candidate in the order given, for `pick_person`.
    """
    if duration <= timedelta(0):
        raise ValueError("duration must be positive")
    if step <= timedelta(0):
        raise ValueError("step must be positive")
    if buffer < timedelta(0):
        raise ValueError("buffer must not be negative")
    _require_aware("start_from", start_from)
    _require_aware("until", until)
    if limit is not None and limit <= 0:
        return []

    zone = calendar.timezone
    busy: dict[tuple[str, UUID], list[tuple[datetime, datetime]]] = {}
    for item in calendar.busy:
        busy.setdefault((item.subject_kind, item.subject_id), []).append((item.start, item.end))

    def free(kind: str, subject_id: UUID, start: datetime, end: datetime) -> bool:
        return (
            within_hours(calendar, hours_for(calendar, subject_id), start, end)
            and not on_leave(calendar, subject_id, start, end)
            and not _is_busy(busy.get((kind, subject_id), ()), start, end + buffer)
        )

    everyone = [*required_people, *any_of_people, *required_resources, *any_of_resources]
    slots: list[Slot] = []
    day = start_from.astimezone(zone).date()
    last_day = until.astimezone(zone).date()
    while day <= last_day:
        for naive in _candidate_starts(zone, day, step):
            start = naive.replace(tzinfo=zone).astimezone(UTC)
            # A wall-clock time skipped by a daylight-saving jump does not exist: moving it to UTC
            # and back lands on a different time. Offer nothing there.
            if start.astimezone(zone).replace(tzinfo=None) != naive:
                continue
            end = start + duration  # real elapsed time, so a slot is always `duration` long
            if start < start_from or end > until or touches_closed_day(calendar, start, end):
                continue
            if not everyone and not within_hours(calendar, calendar.company_hours, start, end):
                continue
            if not all(free("person", p, start, end) for p in required_people):
                continue
            if not all(free("resource", r, start, end) for r in required_resources):
                continue
            people = [p for p in any_of_people if free("person", p, start, end)]
            resources = [r for r in any_of_resources if free("resource", r, start, end)]
            if (any_of_people and not people) or (any_of_resources and not resources):
                continue
            slots.append(
                Slot(
                    start,
                    end,
                    tuple(dict.fromkeys([*required_people, *people])),
                    tuple(dict.fromkeys([*required_resources, *resources])),
                )
            )
            if limit is not None and len(slots) >= limit:
                return slots
        day += timedelta(days=1)
    return slots


def pick_person(candidates: Sequence[UUID], bookings_that_day: Mapping[UUID, int]) -> UUID:
    """The candidate with the fewest bookings that day; ties go to the earlier in the list."""
    if not candidates:
        raise ValueError("there is no one to pick")
    return min(candidates, key=lambda person: bookings_that_day.get(person, 0))
