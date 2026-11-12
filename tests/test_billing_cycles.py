"""Billing cycles (``csa.billing.cycles``): the 5th 00:00 to the next 5th 00:00, Kuala Lumpur.

Pure date arithmetic; no database. All dates are synthetic.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from csa.billing.cycles import KL, Cycle, cycle_containing, cycle_of, cycle_range, days_in


def test_kl_is_kuala_lumpur() -> None:
    assert str(KL) == "Asia/Kuala_Lumpur"
    assert datetime(2026, 9, 5, tzinfo=KL).utcoffset() == timedelta(hours=8)


def test_the_5th_boundary_is_midnight_in_kuala_lumpur_not_utc() -> None:
    # 4 Sep 2026 16:00:00Z is 5 Sep 00:00 in Kuala Lumpur: the new cycle starts there.
    old = cycle_of(datetime(2026, 9, 4, 15, 59, 59, tzinfo=UTC))
    new = cycle_of(datetime(2026, 9, 4, 16, 0, 0, tzinfo=UTC))
    assert old == Cycle(date(2026, 8, 5), date(2026, 9, 5))
    assert new == Cycle(date(2026, 9, 5), date(2026, 10, 5))


def test_cycle_of_uses_the_local_date_of_any_aware_timestamp() -> None:
    plus_eight = timezone(timedelta(hours=8))
    assert cycle_of(datetime(2026, 9, 5, 0, 0, tzinfo=plus_eight)).start == date(2026, 9, 5)
    assert cycle_of(datetime(2026, 9, 4, 23, 59, tzinfo=plus_eight)).start == date(2026, 8, 5)
    assert cycle_of(datetime(2026, 9, 5, 0, 0, tzinfo=KL)).start == date(2026, 9, 5)


def test_cycle_of_refuses_a_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone"):
        cycle_of(datetime(2026, 9, 5, 12, 0))


def test_cycle_containing_a_date() -> None:
    assert cycle_containing(date(2026, 8, 15)) == Cycle(date(2026, 8, 5), date(2026, 9, 5))
    assert cycle_containing(date(2026, 9, 4)) == Cycle(date(2026, 8, 5), date(2026, 9, 5))
    assert cycle_containing(date(2026, 9, 5)) == Cycle(date(2026, 9, 5), date(2026, 10, 5))
    assert cycle_containing(date(2026, 9, 1)) == Cycle(date(2026, 8, 5), date(2026, 9, 5))


def test_year_rollover() -> None:
    december = cycle_containing(date(2026, 12, 25))
    assert december == Cycle(date(2026, 12, 5), date(2027, 1, 5))
    assert cycle_containing(date(2027, 1, 4)) == december
    assert cycle_containing(date(2027, 1, 5)) == Cycle(date(2027, 1, 5), date(2027, 2, 5))
    # 1 to 4 January belong to the cycle that started on 5 December.
    assert cycle_of(datetime(2027, 1, 4, 15, 59, 59, tzinfo=UTC)) == december
    assert cycle_of(datetime(2027, 1, 4, 16, 0, tzinfo=UTC)).start == date(2027, 1, 5)


def test_cycle_lengths_28_30_31() -> None:
    assert days_in(Cycle(date(2027, 2, 5), date(2027, 3, 5))) == 28
    assert days_in(Cycle(date(2028, 2, 5), date(2028, 3, 5))) == 29  # leap year
    assert days_in(Cycle(date(2026, 9, 5), date(2026, 10, 5))) == 30
    assert days_in(Cycle(date(2026, 8, 5), date(2026, 9, 5))) == 31
    assert days_in(Cycle(date(2026, 12, 5), date(2027, 1, 5))) == 31


def test_a_cycle_must_run_from_a_5th_to_the_next_5th() -> None:
    with pytest.raises(ValueError):
        Cycle(date(2026, 9, 1), date(2026, 10, 5))
    with pytest.raises(ValueError):
        Cycle(date(2026, 9, 5), date(2026, 11, 5))
    with pytest.raises(ValueError):
        Cycle(date(2026, 9, 5), date(2026, 10, 4))


def test_cycle_range_is_inclusive_and_crosses_years() -> None:
    first = cycle_containing(date(2026, 11, 20))
    last = cycle_containing(date(2027, 2, 10))
    assert [c.start for c in cycle_range(first, last)] == [
        date(2026, 11, 5),
        date(2026, 12, 5),
        date(2027, 1, 5),
        date(2027, 2, 5),
    ]
    assert cycle_range(first, first) == [first]
    for earlier, later in zip(cycle_range(first, last), cycle_range(first, last)[1:], strict=False):
        assert earlier.end == later.start


def test_cycle_range_refuses_a_reversed_range() -> None:
    with pytest.raises(ValueError):
        cycle_range(cycle_containing(date(2026, 12, 5)), cycle_containing(date(2026, 11, 5)))
