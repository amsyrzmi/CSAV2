"""Free slots from hours, closed days, leave and busy periods: pure, with no database."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from csa.bookings.availability import (
    DEFAULT_COMPANY_HOURS,
    Busy,
    Calendar,
    Slot,
    free_slots,
    pick_person,
)

KL = ZoneInfo("Asia/Kuala_Lumpur")
NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
WEEKDAYS = {d: [(time(9, 0), time(18, 0))] for d in range(5)}
ALL_DAY = {d: [(time(0, 0), time(23, 59))] for d in range(7)}
HOUR = timedelta(hours=1)


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


def _slots(
    calendar: Calendar,
    start_from: datetime,
    until: datetime,
    *,
    required_people: tuple[uuid.UUID, ...] = (),
    any_of_people: tuple[uuid.UUID, ...] = (),
    required_resources: tuple[uuid.UUID, ...] = (),
    any_of_resources: tuple[uuid.UUID, ...] = (),
    duration: timedelta = HOUR,
    buffer: timedelta = timedelta(0),
    step: timedelta = HOUR,
) -> list[Slot]:
    return free_slots(
        calendar,
        required_people=required_people,
        any_of_people=any_of_people,
        required_resources=required_resources,
        any_of_resources=any_of_resources,
        duration=duration,
        buffer=buffer,
        start_from=start_from,
        until=until,
        step=step,
    )


def _local_starts(slots: list[Slot]) -> list[tuple[int, int, int]]:
    """(day, hour, minute) of each slot start in company local time."""
    local = [s.start.astimezone(KL) for s in slots]
    return [(t.day, t.hour, t.minute) for t in local]


def _at(day: int, hour: int, minute: int = 0, *, month: int = 11, zone: ZoneInfo = KL) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=zone)


def test_slots_respect_hours_closed_days_and_busy() -> None:
    person = uuid.uuid4()
    busy = Busy("person", person, _at(2, 10).astimezone(UTC), _at(2, 11).astimezone(UTC))
    cal = _calendar(closed_days=frozenset({date(2026, 11, 3)}), busy=[busy])
    # Monday 2 to Wednesday 4 November, Tuesday closed.
    slots = _slots(cal, _at(2, 0), _at(5, 0), required_people=(person,))
    monday = [9, 11, 12, 13, 14, 15, 16, 17]  # 10:00 is busy; the last hour ends at 18:00
    wednesday = list(range(9, 18))
    assert _local_starts(slots) == [(2, h, 0) for h in monday] + [(4, h, 0) for h in wednesday]
    assert all(s.end - s.start == HOUR for s in slots)
    assert all(s.people == (person,) and s.resources == () for s in slots)


def test_slots_do_not_start_before_start_from_or_end_after_until() -> None:
    slots = _slots(_calendar(), _at(2, 9, 30), _at(2, 12, 30), step=timedelta(minutes=30))
    assert _local_starts(slots) == [(2, 9, 30), (2, 10, 0), (2, 10, 30), (2, 11, 0), (2, 11, 30)]


def test_start_times_are_aligned_to_local_clock_times() -> None:
    # Kuala Lumpur is UTC+8; a start_from of 09:07 local must not shift the grid to :07.
    slots = _slots(
        _calendar(),
        _at(2, 9, 7),
        _at(2, 11, 0),
        duration=timedelta(minutes=30),
        step=timedelta(minutes=15),
    )
    assert _local_starts(slots) == [
        (2, 9, 15),
        (2, 9, 30),
        (2, 9, 45),
        (2, 10, 0),
        (2, 10, 15),
        (2, 10, 30),
    ]


def test_the_buffer_keeps_the_gap_free() -> None:
    person = uuid.uuid4()
    # The busy period already includes its own buffer.
    busy = Busy("person", person, _at(2, 10).astimezone(UTC), _at(2, 11).astimezone(UTC))
    cal = _calendar(busy=[busy])
    slots = _slots(
        cal,
        _at(2, 9),
        _at(2, 12),
        required_people=(person,),
        duration=timedelta(minutes=30),
        buffer=timedelta(minutes=15),
        step=timedelta(minutes=15),
    )
    # 09:15 + 30 min + 15 min buffer just reaches 10:00; 09:30 would eat into the busy period.
    assert _local_starts(slots) == [(2, 9, 0), (2, 9, 15), (2, 11, 0), (2, 11, 15), (2, 11, 30)]


def test_the_buffer_does_not_push_a_slot_past_working_hours() -> None:
    # The buffer is free time, not working time: a slot ending at closing time is still fine.
    slots = _slots(
        _calendar(),
        _at(2, 16),
        _at(2, 19),
        duration=HOUR,
        buffer=timedelta(minutes=30),
        step=timedelta(minutes=30),
    )
    assert _local_starts(slots) == [(2, 16, 0), (2, 16, 30), (2, 17, 0)]


def test_a_person_on_leave_is_not_offered() -> None:
    away = uuid.uuid4()
    cal = _calendar(leave={away: [(date(2026, 11, 2), date(2026, 11, 2))]})
    slots = _slots(cal, _at(2, 0), _at(4, 0), required_people=(away,))
    assert {s.start.astimezone(KL).day for s in slots} == {3}


def test_leave_covers_whole_local_days() -> None:
    person = uuid.uuid4()
    cal = _calendar(company_hours=ALL_DAY, leave={person: [(date(2026, 11, 3), date(2026, 11, 3))]})
    # Leave on the 3rd local runs from 16:00 UTC on the 2nd to 16:00 UTC on the 3rd, so it spans
    # UTC midnight. Hours end at 23:59, so the 23:30 slot on the 2nd (15:30 UTC) does not fit.
    slots = _slots(
        cal,
        datetime(2026, 11, 2, 15, 0, tzinfo=UTC),
        datetime(2026, 11, 3, 17, 0, tzinfo=UTC),
        required_people=(person,),
        duration=timedelta(minutes=30),
        step=timedelta(minutes=30),
    )
    assert [s.start for s in slots] == [
        datetime(2026, 11, 2, 15, 0, tzinfo=UTC),
        datetime(2026, 11, 3, 16, 0, tzinfo=UTC),
        datetime(2026, 11, 3, 16, 30, tzinfo=UTC),
    ]


def test_any_free_person_needs_only_one_free() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    busy = Busy("person", first, _at(2, 10).astimezone(UTC), _at(2, 11).astimezone(UTC))
    cal = _calendar(busy=[busy])
    slots = _slots(cal, _at(2, 9), _at(2, 12), any_of_people=(first, second))
    by_hour = {s.start.astimezone(KL).hour: s.people for s in slots}
    assert by_hour == {9: (first, second), 10: (second,), 11: (first, second)}


def test_any_of_people_all_busy_offers_nothing() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    busy = [
        Busy("person", p, _at(2, 10).astimezone(UTC), _at(2, 11).astimezone(UTC))
        for p in (first, second)
    ]
    slots = _slots(_calendar(busy=busy), _at(2, 10), _at(2, 11), any_of_people=(first, second))
    assert slots == []


def test_required_and_any_of_people_are_listed_required_first() -> None:
    lead, first, second = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    slots = _slots(
        _calendar(),
        _at(2, 9),
        _at(2, 10),
        required_people=(lead,),
        any_of_people=(first, second),
    )
    assert [s.people for s in slots] == [(lead, first, second)]


def test_several_people_need_all_free() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    busy = Busy("person", second, _at(2, 10).astimezone(UTC), _at(2, 11).astimezone(UTC))
    cal = _calendar(busy=[busy])
    slots = _slots(cal, _at(2, 9), _at(2, 12), required_people=(first, second))
    assert _local_starts(slots) == [(2, 9, 0), (2, 11, 0)]
    assert all(s.people == (first, second) for s in slots)


def test_a_required_room_must_be_free() -> None:
    person, room = uuid.uuid4(), uuid.uuid4()
    busy = Busy("resource", room, _at(2, 10).astimezone(UTC), _at(2, 11).astimezone(UTC))
    cal = _calendar(busy=[busy])
    slots = _slots(
        cal, _at(2, 9), _at(2, 12), required_people=(person,), required_resources=(room,)
    )
    assert _local_starts(slots) == [(2, 9, 0), (2, 11, 0)]
    assert all(s.resources == (room,) for s in slots)


def test_any_free_room_needs_only_one_free() -> None:
    small, large = uuid.uuid4(), uuid.uuid4()
    busy = Busy("resource", small, _at(2, 10).astimezone(UTC), _at(2, 11).astimezone(UTC))
    cal = _calendar(busy=[busy])
    slots = _slots(cal, _at(2, 9), _at(2, 12), any_of_resources=(small, large))
    assert {s.start.astimezone(KL).hour: s.resources for s in slots} == {
        9: (small, large),
        10: (large,),
        11: (small, large),
    }


def test_a_busy_person_does_not_block_a_resource_with_another_id() -> None:
    person, room = uuid.uuid4(), uuid.uuid4()
    busy = Busy("person", person, _at(2, 10).astimezone(UTC), _at(2, 11).astimezone(UTC))
    slots = _slots(_calendar(busy=[busy]), _at(2, 10), _at(2, 11), required_resources=(room,))
    assert _local_starts(slots) == [(2, 10, 0)]


def test_override_hours_replace_company_hours() -> None:
    late = uuid.uuid4()
    cal = _calendar(overrides={late: {0: [(time(13, 0), time(15, 0))]}})
    own = _slots(cal, _at(2, 0), _at(4, 0), required_people=(late,))
    # Only Monday is listed for this person, so Tuesday has nothing, and Monday uses 13:00-15:00.
    assert _local_starts(own) == [(2, 13, 0), (2, 14, 0)]
    others = _slots(cal, _at(2, 8), _at(2, 11), required_people=(uuid.uuid4(),))
    assert _local_starts(others) == [(2, 9, 0), (2, 10, 0)]


def test_closed_days_apply_to_people_with_their_own_hours() -> None:
    late = uuid.uuid4()
    cal = _calendar(
        overrides={late: {0: [(time(13, 0), time(15, 0))]}},
        closed_days=frozenset({date(2026, 11, 2)}),
    )
    assert _slots(cal, _at(2, 0), _at(3, 0), required_people=(late,)) == []


def test_split_hours_leave_the_lunch_gap_empty() -> None:
    cal = _calendar(company_hours={0: [(time(9, 0), time(12, 0)), (time(14, 0), time(16, 0))]})
    slots = _slots(cal, _at(2, 0), _at(3, 0), duration=timedelta(hours=2), step=timedelta(hours=1))
    assert _local_starts(slots) == [(2, 9, 0), (2, 10, 0), (2, 14, 0)]


def test_hours_and_closed_days_use_the_company_time_zone() -> None:
    # 23:30 UTC on Monday 2 November is 07:30 Tuesday in Kuala Lumpur and 18:30 Monday in New York.
    start = datetime(2026, 11, 2, 23, 30, tzinfo=UTC)
    until = start + timedelta(minutes=30)
    half = timedelta(minutes=30)

    def offered(calendar: Calendar) -> list[Slot]:
        return _slots(calendar, start, until, duration=half, step=half)

    tuesday_closed = frozenset({date(2026, 11, 3)})
    monday_closed = frozenset({date(2026, 11, 2)})
    assert offered(_calendar(timezone=KL, company_hours=ALL_DAY)) != []
    assert offered(_calendar(timezone=KL, company_hours=ALL_DAY, closed_days=tuesday_closed)) == []
    assert offered(_calendar(timezone=KL, company_hours=ALL_DAY, closed_days=monday_closed)) != []
    assert offered(_calendar(timezone=NY, company_hours=ALL_DAY, closed_days=monday_closed)) == []
    assert offered(_calendar(timezone=NY, company_hours=ALL_DAY, closed_days=tuesday_closed)) != []
    # Weekday: Tuesday (1) in Kuala Lumpur, Monday (0) in New York.
    tuesday_morning = {1: [(time(6, 0), time(9, 0))]}
    assert offered(_calendar(timezone=KL, company_hours=tuesday_morning)) != []
    assert offered(_calendar(timezone=NY, company_hours=tuesday_morning)) == []
    monday_evening = {0: [(time(17, 0), time(19, 0))]}
    assert offered(_calendar(timezone=NY, company_hours=monday_evening)) != []
    assert offered(_calendar(timezone=KL, company_hours=monday_evening)) == []


def test_a_slot_is_returned_in_utc_with_its_real_length() -> None:
    slots = _slots(_calendar(), _at(2, 9), _at(2, 10))
    assert slots == [Slot(_at(2, 9).astimezone(UTC), _at(2, 10).astimezone(UTC), (), ())]
    assert slots[0].start.utcoffset() == timedelta(0)


def test_the_spring_forward_day_has_no_nonexistent_times_and_true_lengths() -> None:
    # New York, Sunday 8 March 2026: 02:00 jumps to 03:00.
    cal = _calendar(timezone=NY, company_hours={6: [(time(0, 0), time(6, 0))]})
    half = timedelta(minutes=30)
    slots = _slots(
        cal,
        _at(8, 0, month=3, zone=NY),
        _at(9, 0, month=3, zone=NY),
        duration=HOUR,
        step=half,
    )
    local = [(s.start.astimezone(NY).hour, s.start.astimezone(NY).minute) for s in slots]
    assert (1, 30) in local and (3, 0) in local
    assert not any(h == 2 for h, _ in local)
    assert all(s.end - s.start == HOUR for s in slots)
    # 01:30 EST plus an hour of real time is 03:30 EDT, not 02:30.
    slot = next(s for s in slots if s.start.astimezone(NY).hour == 1 and s.start.minute == 30)
    assert slot.end.astimezone(NY).hour == 3


def test_the_fall_back_day_does_not_crash_and_keeps_true_lengths() -> None:
    # New York, Sunday 1 November 2026: 02:00 falls back to 01:00.
    cal = _calendar(timezone=NY, company_hours={6: [(time(0, 0), time(6, 0))]})
    slots = _slots(
        cal,
        _at(1, 0, zone=NY),
        _at(2, 0, zone=NY),
        duration=HOUR,
        step=timedelta(minutes=30),
    )
    assert slots
    assert all(s.end - s.start == HOUR for s in slots)
    assert len({s.start for s in slots}) == len(slots)


def test_working_hours_on_a_transition_day_are_real_time_bounds() -> None:
    # 00:00-06:00 on the spring-forward day is only five real hours long.
    cal = _calendar(timezone=NY, company_hours={6: [(time(0, 0), time(6, 0))]})
    slots = _slots(
        cal,
        _at(8, 0, month=3, zone=NY),
        _at(9, 0, month=3, zone=NY),
        duration=timedelta(hours=5),
        step=timedelta(minutes=30),
    )
    assert [(s.start.astimezone(NY).hour, s.start.astimezone(NY).minute) for s in slots] == [(0, 0)]


def test_a_slot_that_reaches_only_part_way_into_a_busy_period_is_refused() -> None:
    person = uuid.uuid4()
    busy = Busy("person", person, _at(2, 10, 30).astimezone(UTC), _at(2, 11, 30).astimezone(UTC))
    slots = _slots(
        _calendar(busy=[busy]),
        _at(2, 9),
        _at(2, 13),
        required_people=(person,),
        step=timedelta(minutes=30),
    )
    assert _local_starts(slots) == [(2, 9, 0), (2, 9, 30), (2, 11, 30), (2, 12, 0)]


def test_pick_person_takes_the_fewest_bookings_then_the_order_given() -> None:
    first, second, third = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    assert pick_person([first, second, third], {first: 3, second: 1, third: 2}) == second
    assert pick_person([first, second, third], {second: 1, third: 1}) == first
    assert pick_person([third, second], {second: 2, third: 2}) == third
    assert pick_person([first], {}) == first


def test_pick_person_needs_someone_to_pick() -> None:
    with pytest.raises(ValueError):
        pick_person([], {})


@pytest.mark.parametrize(
    ("duration", "step"),
    [
        (timedelta(0), HOUR),
        (-HOUR, HOUR),
        (HOUR, timedelta(0)),
        (HOUR, -HOUR),
    ],
)
def test_duration_and_step_must_be_positive(duration: timedelta, step: timedelta) -> None:
    with pytest.raises(ValueError):
        _slots(_calendar(), _at(2, 9), _at(2, 17), duration=duration, step=step)


def test_a_negative_buffer_is_refused() -> None:
    with pytest.raises(ValueError):
        _slots(_calendar(), _at(2, 9), _at(2, 17), buffer=-HOUR)


def test_naive_datetimes_are_refused() -> None:
    with pytest.raises(ValueError):
        _slots(_calendar(), datetime(2026, 11, 2, 9), _at(2, 17))
    with pytest.raises(ValueError):
        _slots(_calendar(), _at(2, 9), datetime(2026, 11, 2, 17))


def test_the_default_company_hours_are_monday_to_friday_nine_to_six() -> None:
    cal = _calendar(company_hours=DEFAULT_COMPANY_HOURS)
    slots = _slots(cal, _at(6, 0), _at(9, 0))  # Friday 6 to Sunday 8 November, local
    assert {s.start.astimezone(KL).day for s in slots} == {6}
    assert len(slots) == 9
