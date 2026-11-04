"""Who may override which booking rule, and how the findings for a proposed time are worked out."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from csa.bookings.availability import Busy, Calendar
from csa.bookings.rules import Actor, Decision, Finding, decide, findings_for

KL = ZoneInfo("Asia/Kuala_Lumpur")
UTC = ZoneInfo("UTC")
WEEKDAYS = {d: [(time(9, 0), time(18, 0))] for d in range(5)}

# (actor, finding) -> "ack" | "allow" | "refuse": the owner's table.
TABLE: dict[tuple[Actor, Finding], str] = {
    ("bot", "outside_hours"): "refuse",
    ("bot", "closed_day"): "refuse",
    ("bot", "short_notice"): "refuse",
    ("bot", "on_leave"): "refuse",
    ("staff", "outside_hours"): "ack",
    ("staff", "closed_day"): "ack",
    ("staff", "short_notice"): "allow",
    ("staff", "on_leave"): "refuse",
    ("admin", "outside_hours"): "ack",
    ("admin", "closed_day"): "ack",
    ("admin", "short_notice"): "allow",
    ("admin", "on_leave"): "ack",
}


@pytest.mark.parametrize(("actor", "finding"), list(TABLE))
def test_decide_follows_the_owners_table(actor: Actor, finding: Finding) -> None:
    outcome = TABLE[(actor, finding)]
    unacknowledged = decide(actor, {finding}, set())
    if outcome == "allow":
        assert unacknowledged == Decision(True, set(), set())
    elif outcome == "ack":
        assert unacknowledged == Decision(False, {finding}, set())
        assert decide(actor, {finding}, {finding}) == Decision(True, set(), set())
    else:
        assert unacknowledged == Decision(False, set(), {finding})
        # Acknowledging never turns a refusal into a yes.
        assert decide(actor, {finding}, {finding}) == Decision(False, set(), {finding})


@pytest.mark.parametrize("actor", ["bot", "staff", "admin"])
def test_decide_with_no_findings_allows_everyone(actor: Actor) -> None:
    assert decide(actor, set(), set()) == Decision(True, set(), set())


def test_decide_mixes_the_outcomes_per_finding() -> None:
    result = decide("staff", {"outside_hours", "short_notice", "on_leave"}, {"outside_hours"})
    assert result == Decision(False, set(), {"on_leave"})
    result = decide("admin", {"closed_day", "on_leave"}, {"closed_day"})
    assert result == Decision(False, {"on_leave"}, set())


def _calendar(**changes: object) -> Calendar:
    base: dict[str, object] = {
        "timezone": KL,
        "company_hours": WEEKDAYS,
        "overrides": {},
        "closed_days": frozenset(),
        "leave": {},
        "busy": [],
    }
    base.update(changes)
    return Calendar(**base)  # type: ignore[arg-type]


NOW = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)  # Monday 08:00 in Kuala Lumpur
NOTICE = timedelta(hours=2)


def _findings(
    calendar: Calendar,
    start_local: datetime,
    *,
    length: timedelta = timedelta(hours=1),
    people: tuple[uuid.UUID, ...] = (),
    resources: tuple[uuid.UUID, ...] = (),
    now: datetime = NOW,
) -> set[Finding]:
    start = start_local.astimezone(UTC)
    return findings_for(calendar, start, start + length, people, resources, NOTICE, now)


def test_a_time_inside_hours_with_notice_has_no_findings() -> None:
    assert _findings(_calendar(), datetime(2026, 11, 3, 10, 0, tzinfo=KL)) == set()


def test_outside_hours_is_found_when_any_part_sticks_out() -> None:
    cal = _calendar()
    assert _findings(cal, datetime(2026, 11, 3, 8, 0, tzinfo=KL)) == {"outside_hours"}
    assert _findings(cal, datetime(2026, 11, 3, 17, 30, tzinfo=KL)) == {"outside_hours"}
    assert _findings(cal, datetime(2026, 11, 3, 17, 0, tzinfo=KL)) == set()


def test_a_weekend_with_no_hours_is_outside_hours() -> None:
    assert _findings(_calendar(), datetime(2026, 11, 7, 10, 0, tzinfo=KL)) == {"outside_hours"}


def test_a_closed_day_is_found() -> None:
    cal = _calendar(closed_days=frozenset({date(2026, 11, 3)}))
    assert "closed_day" in _findings(cal, datetime(2026, 11, 3, 10, 0, tzinfo=KL))
    assert "closed_day" not in _findings(cal, datetime(2026, 11, 4, 10, 0, tzinfo=KL))


def test_short_notice_is_found_when_the_start_is_nearer_than_the_notice() -> None:
    cal = _calendar()
    assert _findings(cal, datetime(2026, 11, 2, 9, 30, tzinfo=KL)) == {"short_notice"}
    assert _findings(cal, datetime(2026, 11, 2, 10, 0, tzinfo=KL)) == set()


def test_leave_is_found_for_the_person_on_it_only() -> None:
    away, here = uuid.uuid4(), uuid.uuid4()
    cal = _calendar(leave={away: [(date(2026, 11, 3), date(2026, 11, 3))]})
    at = datetime(2026, 11, 3, 10, 0, tzinfo=KL)
    assert _findings(cal, at, people=(away, here)) == {"on_leave"}
    assert _findings(cal, at, people=(here,)) == set()


def test_a_person_with_their_own_hours_is_judged_by_them() -> None:
    late = uuid.uuid4()
    cal = _calendar(overrides={late: {1: [(time(13, 0), time(21, 0))]}})
    evening = datetime(2026, 11, 3, 19, 0, tzinfo=KL)  # a Tuesday
    assert _findings(cal, evening) == {"outside_hours"}
    assert _findings(cal, evening, people=(late,)) == set()
    morning = datetime(2026, 11, 3, 10, 0, tzinfo=KL)
    assert _findings(cal, morning, people=(late,)) == {"outside_hours"}


def test_a_resource_follows_company_hours_unless_overridden() -> None:
    room = uuid.uuid4()
    evening = datetime(2026, 11, 3, 19, 0, tzinfo=KL)
    assert _findings(_calendar(), evening, resources=(room,)) == {"outside_hours"}
    cal = _calendar(overrides={room: {1: [(time(9, 0), time(22, 0))]}})
    assert _findings(cal, evening, resources=(room,)) == set()


def test_findings_use_the_company_time_zone() -> None:
    # 23:30 UTC on Monday 2 November is 07:30 Tuesday in Kuala Lumpur and 18:30 Monday in New York.
    start = datetime(2026, 11, 2, 23, 30, tzinfo=UTC)
    end = start + timedelta(minutes=30)
    long_ago = datetime(2026, 1, 1, tzinfo=UTC)
    tuesday_closed = _calendar(closed_days=frozenset({date(2026, 11, 3)}))
    assert findings_for(tuesday_closed, start, end, (), (), NOTICE, long_ago) == {
        "closed_day",
        "outside_hours",
    }
    new_york = ZoneInfo("America/New_York")
    monday_closed = _calendar(timezone=new_york, closed_days=frozenset({date(2026, 11, 2)}))
    assert findings_for(monday_closed, start, end, (), (), NOTICE, long_ago) == {
        "closed_day",
        "outside_hours",
    }
    ny_open = _calendar(timezone=new_york, company_hours={0: [(time(9, 0), time(19, 0))]})
    assert findings_for(ny_open, start, end, (), (), NOTICE, long_ago) == set()


def test_leave_covers_whole_local_days_in_findings() -> None:
    person = uuid.uuid4()
    cal = _calendar(leave={person: [(date(2026, 11, 3), date(2026, 11, 3))]})
    # 16:30 UTC on the 2nd is 00:30 on the 3rd in Kuala Lumpur: on leave, though still the UTC 2nd.
    start = datetime(2026, 11, 2, 16, 30, tzinfo=UTC)
    found = findings_for(cal, start, start + timedelta(hours=1), (person,), (), NOTICE, NOW)
    assert "on_leave" in found


def test_a_slot_running_into_a_leave_day_or_closed_day_is_found() -> None:
    # 23:30 on Monday to 00:30 on Tuesday: it starts on an open day and ends on the next one.
    person = uuid.uuid4()
    tuesday = date(2026, 11, 3)
    start = datetime(2026, 11, 2, 23, 30, tzinfo=KL)
    leave = _calendar(leave={person: [(tuesday, tuesday)]})
    assert "on_leave" in _findings(leave, start, people=(person,))
    closed = _calendar(closed_days=frozenset({tuesday}))
    assert "closed_day" in _findings(closed, start)


def test_a_slot_running_out_of_a_leave_day_or_closed_day_is_found() -> None:
    person = uuid.uuid4()
    monday = date(2026, 11, 2)
    start = datetime(2026, 11, 2, 23, 30, tzinfo=KL)
    leave = _calendar(leave={person: [(monday, monday)]})
    assert "on_leave" in _findings(leave, start, people=(person,))
    closed = _calendar(closed_days=frozenset({monday}))
    assert "closed_day" in _findings(closed, start)


def test_a_slot_ending_exactly_at_midnight_does_not_touch_the_next_day() -> None:
    person = uuid.uuid4()
    tuesday = date(2026, 11, 3)
    start = datetime(2026, 11, 2, 23, 0, tzinfo=KL)
    cal = _calendar(leave={person: [(tuesday, tuesday)]}, closed_days=frozenset({tuesday}))
    found = _findings(cal, start, people=(person,))
    assert "on_leave" not in found
    assert "closed_day" not in found


def test_busy_periods_are_not_a_finding() -> None:
    # Double bookings are refused for everyone by the database, so they are not an override.
    person = uuid.uuid4()
    at = datetime(2026, 11, 3, 10, 0, tzinfo=KL).astimezone(UTC)
    busy = Busy("person", person, at, at + timedelta(hours=1))
    assert _findings(_calendar(busy=[busy]), at, people=(person,)) == set()
