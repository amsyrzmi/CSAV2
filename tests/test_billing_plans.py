"""Plan history rules (``csa.billing.plans``): spec "0552_client_billing_plans.sql", rules 1 to 7.

Pure functions; rule 7 (the audit row) is proven against the database in
tests/test_billing_plans_schema.py. All data is synthetic.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from csa.billing.cycles import Cycle, cycle_containing, kl_today
from csa.billing.plans import (
    PlanRow,
    PlanRuleError,
    billed_plan,
    billed_window,
    pilot_ended,
    plan_for_cycle,
    validate_new_plan,
)

ROOT = Path(__file__).resolve().parent.parent

AUG = Cycle(date(2026, 8, 5), date(2026, 9, 5))
SEP = Cycle(date(2026, 9, 5), date(2026, 10, 5))
OCT = Cycle(date(2026, 10, 5), date(2026, 11, 5))
NOV = Cycle(date(2026, 11, 5), date(2026, 12, 5))
DEC = Cycle(date(2026, 12, 5), date(2027, 1, 5))
JUL = Cycle(date(2026, 7, 5), date(2026, 8, 5))

_T0 = datetime(2026, 8, 1, tzinfo=UTC)


def row(stage: str, bundle: str, effective: date, minute: int = 0) -> PlanRow:
    """A plan row created ``minute`` minutes after a fixed instant (orders same-date rows)."""
    return PlanRow(stage, bundle, effective, _T0 + timedelta(minutes=minute))  # type: ignore[arg-type]


def refused(history: Sequence[PlanRow], new: PlanRow, today: date) -> str:
    with pytest.raises(PlanRuleError) as caught:
        validate_new_plan(history, new, today)
    return caught.value.code


JOIN = row("pilot", "individual", date(2026, 8, 15))


# ---------------------------------------------------------------------------------------------
# Rule 1: cycles (the cycle arithmetic itself is in tests/test_billing_cycles.py)
# ---------------------------------------------------------------------------------------------


def test_rule1_plans_are_read_per_cycle_5th_to_5th() -> None:
    assert cycle_containing(date(2026, 8, 15)) == AUG
    history = [JOIN, row("collaborator", "individual", date(2026, 9, 5), 1)]
    assert plan_for_cycle(history, AUG) == JOIN
    assert plan_for_cycle(history, SEP) == history[1]
    # A row dated a cycle's end belongs to the next cycle: end is exclusive.
    assert plan_for_cycle(history, AUG) != history[1]


# ---------------------------------------------------------------------------------------------
# Rule 2: the first row is the join date, inside the current cycle, pilot or collaborator
# ---------------------------------------------------------------------------------------------


def test_rule2_first_row_is_the_join_date_and_starts_billing() -> None:
    validate_new_plan([], JOIN, date(2026, 8, 20))
    assert billed_window([JOIN], AUG) == (date(2026, 8, 15), date(2026, 9, 5))
    assert billed_window([JOIN], SEP) == (date(2026, 9, 5), date(2026, 10, 5))


def test_rule2_cycles_before_the_first_row_are_not_set_up() -> None:
    assert plan_for_cycle([JOIN], JUL) is None
    assert billed_window([JOIN], JUL) is None
    assert billed_plan([JOIN], JUL) is None
    assert plan_for_cycle([], AUG) is None
    assert billed_window([], AUG) is None


def test_rule2_first_row_may_be_any_day_of_the_current_cycle() -> None:
    today = date(2026, 8, 20)
    for day in (date(2026, 8, 5), date(2026, 8, 20), date(2026, 8, 31), date(2026, 9, 4)):
        validate_new_plan([], row("pilot", "curated", day), today)
    validate_new_plan([], row("collaborator", "complete", date(2026, 8, 7)), today)


def test_rule2_first_row_earlier_than_the_current_cycle_is_refused() -> None:
    today = date(2026, 8, 20)
    assert refused([], row("pilot", "individual", date(2026, 8, 4)), today) == (
        "first_row_outside_current_cycle"
    )
    assert refused([], row("pilot", "individual", date(2026, 7, 15)), today) == (
        "first_row_outside_current_cycle"
    )


def test_rule2_first_row_in_a_later_cycle_is_refused() -> None:
    today = date(2026, 8, 20)
    assert refused([], row("pilot", "individual", date(2026, 9, 5)), today) == (
        "first_row_outside_current_cycle"
    )


def test_rule2_first_row_must_be_pilot_or_collaborator() -> None:
    today = date(2026, 8, 20)
    assert refused([], row("terminated", "individual", date(2026, 8, 20)), today) == (
        "first_row_must_be_pilot_or_collaborator"
    )
    # The stage is checked before the date: a terminated first row is wrong on any date.
    assert refused([], row("terminated", "individual", date(2026, 7, 1)), today) == (
        "first_row_must_be_pilot_or_collaborator"
    )


def test_rule2_later_rows_are_collaborator_terminated_or_a_same_stage_bundle_change() -> None:
    today = date(2026, 8, 20)
    validate_new_plan([JOIN], row("collaborator", "individual", date(2026, 9, 5)), today)
    validate_new_plan([JOIN], row("pilot", "complete", date(2026, 9, 5)), today)
    validate_new_plan([JOIN], row("terminated", "individual", date(2026, 8, 25)), today)
    converted = [JOIN, row("collaborator", "individual", date(2026, 9, 5), 1)]
    validate_new_plan(converted, row("collaborator", "curated", date(2026, 10, 5)), today)
    # Back to pilot after collaborator is not a same-stage change.
    assert refused(converted, row("pilot", "individual", date(2026, 10, 5)), today) == (
        "bundle_change_same_stage_only"
    )


def test_rule2_a_row_dated_before_the_join_date_is_refused() -> None:
    # Join 15 Aug; 5 Aug is the current cycle's start and a 5th, but billing had not started.
    today = date(2026, 8, 20)
    assert refused([JOIN], row("collaborator", "individual", date(2026, 8, 5)), today) == (
        "before_join_date"
    )
    assert refused([JOIN], row("terminated", "individual", date(2026, 8, 10)), today) == (
        "before_join_date"
    )


def test_rule2_join_inside_a_cycle_with_a_same_date_correction() -> None:
    # The first row is corrected on its own date (not a 5th): the window still starts there.
    corrected = row("pilot", "curated", date(2026, 8, 15), 5)
    validate_new_plan([JOIN], corrected, date(2026, 8, 20))
    history = [JOIN, corrected]
    assert plan_for_cycle(history, AUG) == corrected
    assert billed_window(history, AUG) == (date(2026, 8, 15), date(2026, 9, 5))
    # A correction of the first row may also change its stage (pilot to collaborator).
    validate_new_plan(
        [JOIN], row("collaborator", "individual", date(2026, 8, 15), 5), date(2026, 8, 20)
    )


# ---------------------------------------------------------------------------------------------
# Rule 3: stage and bundle changes are dated a 5th; latest row wins; same-date correction
# ---------------------------------------------------------------------------------------------


def test_rule3_pilot_to_collaborator_must_be_a_5th() -> None:
    today = date(2026, 8, 20)
    assert refused([JOIN], row("collaborator", "individual", date(2026, 9, 6)), today) == (
        "change_not_on_a_fifth"
    )
    assert refused([JOIN], row("pilot", "complete", date(2026, 8, 25)), today) == (
        "change_not_on_a_fifth"
    )
    validate_new_plan([JOIN], row("collaborator", "individual", date(2026, 9, 5)), today)


def test_rule3_change_dated_the_current_cycle_start_applies_to_the_whole_cycle() -> None:
    history = [row("pilot", "individual", date(2026, 8, 5))]
    today = date(2026, 9, 20)
    change = row("collaborator", "curated", date(2026, 9, 5), 1)
    validate_new_plan(history, change, today)
    after = [*history, change]
    assert plan_for_cycle(after, SEP) == change
    assert billed_plan(after, SEP) == change
    assert billed_window(after, SEP) == (date(2026, 9, 5), date(2026, 10, 5))


def test_rule3_same_date_correction_newest_wins_and_the_old_row_stays() -> None:
    mistake = row("collaborator", "complete", date(2026, 9, 5), 1)
    fix = row("collaborator", "curated", date(2026, 9, 5), 2)
    validate_new_plan([JOIN, mistake], fix, date(2026, 9, 10))
    history = [JOIN, mistake, fix]
    assert plan_for_cycle(history, SEP) == fix
    assert mistake in history  # nothing is edited; the old row stays for audit
    # Order of the input does not matter: newest created_at wins.
    assert plan_for_cycle([fix, mistake, JOIN], SEP) == fix


def test_rule3_same_date_correction_can_undo_a_conversion() -> None:
    mistake = row("collaborator", "individual", date(2026, 9, 5), 1)
    undo = row("pilot", "individual", date(2026, 9, 5), 2)
    validate_new_plan([JOIN, mistake], undo, date(2026, 9, 10))
    assert plan_for_cycle([JOIN, mistake, undo], SEP) == undo


def test_rule3_scheduled_future_row_applies_only_from_its_cycle() -> None:
    today = date(2026, 8, 20)
    scheduled = row("collaborator", "individual", date(2026, 10, 5), 1)
    validate_new_plan([JOIN], scheduled, today)
    history = [JOIN, scheduled]
    assert plan_for_cycle(history, AUG) == JOIN
    assert plan_for_cycle(history, SEP) == JOIN
    assert plan_for_cycle(history, OCT) == scheduled
    assert plan_for_cycle(history, NOV) == scheduled


def test_rule3_a_row_may_not_make_a_scheduled_row_go_back_to_pilot() -> None:
    # Pilot bundle change scheduled for 5 Oct; converting on 5 Sep would put pilot after
    # collaborator.
    history = [JOIN, row("pilot", "curated", date(2026, 10, 5), 1)]
    assert refused(
        history, row("collaborator", "individual", date(2026, 9, 5), 2), date(2026, 8, 20)
    ) == ("bundle_change_same_stage_only")


# ---------------------------------------------------------------------------------------------
# Rule 4: termination any day; final period prorated; nothing after terminated
# ---------------------------------------------------------------------------------------------


def test_rule4_termination_mid_cycle_bills_cycle_start_to_termination_date() -> None:
    term = row("terminated", "individual", date(2026, 9, 20), 1)
    validate_new_plan([JOIN], term, date(2026, 9, 10))
    history = [JOIN, term]
    assert billed_window(history, SEP) == (date(2026, 9, 5), date(2026, 9, 20))
    # The stage that prices the final period is the one before termination.
    assert billed_plan(history, SEP) == JOIN
    assert plan_for_cycle(history, SEP) == term
    # Every later cycle bills nothing.
    assert billed_window(history, OCT) is None
    assert billed_plan(history, OCT) is None
    assert plan_for_cycle(history, OCT) == term


def test_rule4_termination_in_the_join_cycle_runs_join_date_to_termination() -> None:
    term = row("terminated", "individual", date(2026, 8, 25), 1)
    validate_new_plan([JOIN], term, date(2026, 8, 20))
    assert billed_window([JOIN, term], AUG) == (date(2026, 8, 15), date(2026, 8, 25))


def test_rule4_termination_dated_a_cycle_start_bills_nothing_in_that_cycle() -> None:
    term = row("terminated", "individual", date(2026, 10, 5), 1)
    validate_new_plan([JOIN], term, date(2026, 9, 10))
    history = [JOIN, term]
    assert billed_window(history, SEP) == (date(2026, 9, 5), date(2026, 10, 5))
    assert billed_plan(history, SEP) == JOIN
    assert plan_for_cycle(history, SEP) == JOIN
    assert billed_window(history, OCT) is None
    assert billed_plan(history, OCT) is None


def test_rule4_termination_on_the_join_date_bills_nothing() -> None:
    term = row("terminated", "individual", date(2026, 8, 15), 1)
    validate_new_plan([JOIN], term, date(2026, 8, 20))
    assert billed_window([JOIN, term], AUG) is None


def test_rule4_termination_before_a_scheduled_row_is_accepted_and_final() -> None:
    # A conversion is scheduled for 5 Oct and the company leaves on 20 Sep. Rule 4 allows any
    # day from the current cycle's start; the termination is final, so the scheduled row is
    # ignored by every reader.
    scheduled = row("collaborator", "individual", date(2026, 10, 5), 1)
    term = row("terminated", "individual", date(2026, 9, 20), 2)
    today = date(2026, 9, 10)
    validate_new_plan([JOIN, scheduled], term, today)
    history = [JOIN, scheduled, term]
    assert billed_window(history, SEP) == (date(2026, 9, 5), date(2026, 9, 20))
    assert billed_plan(history, SEP) == JOIN
    assert billed_window(history, OCT) is None
    assert billed_plan(history, OCT) is None
    assert plan_for_cycle(history, OCT) == term
    assert plan_for_cycle(history, NOV) == term
    assert pilot_ended(history, date(2026, 12, 5)) is False
    assert refused(history, row("collaborator", "individual", date(2026, 11, 5), 3), today) == (
        "after_termination"
    )


def test_rule4_rows_on_the_termination_date_still_count_newest_wins() -> None:
    # A scheduled row on the same date as a later termination: the newest row of that date wins.
    scheduled = row("collaborator", "individual", date(2026, 10, 5), 1)
    term = row("terminated", "individual", date(2026, 10, 5), 2)
    validate_new_plan([JOIN, scheduled], term, date(2026, 9, 10))
    history = [JOIN, scheduled, term]
    assert plan_for_cycle(history, OCT) == term
    assert billed_window(history, OCT) is None
    assert billed_plan(history, SEP) == JOIN
    # Readers stay correct whatever order a history arrives in, earliest termination first.
    later_term = row("terminated", "individual", date(2026, 12, 20), 3)
    assert plan_for_cycle([later_term, *history], DEC) == term
    validate_new_plan(
        [JOIN, scheduled], row("terminated", "individual", date(2026, 10, 20), 2), date(2026, 9, 10)
    )


def test_rule4_termination_earlier_than_the_current_cycle_is_refused() -> None:
    assert refused(
        [JOIN], row("terminated", "individual", date(2026, 9, 1)), date(2026, 9, 10)
    ) == ("before_current_cycle")


def test_rule4_nothing_is_accepted_after_terminated() -> None:
    history = [JOIN, row("terminated", "individual", date(2026, 9, 20), 1)]
    today = date(2026, 9, 25)
    for new in (
        row("collaborator", "individual", date(2026, 10, 5), 2),
        row("pilot", "individual", date(2026, 10, 5), 2),
        row("terminated", "individual", date(2026, 9, 22), 2),
        # Even a scheduled termination cannot be moved, and even a row dated before it.
        row("pilot", "curated", date(2026, 9, 5), 2),
    ):
        assert refused(history, new, today) == "after_termination"
    scheduled = [JOIN, row("terminated", "individual", date(2026, 12, 20), 1)]
    assert refused(scheduled, row("collaborator", "individual", date(2026, 10, 5), 2), today) == (
        "after_termination"
    )


# ---------------------------------------------------------------------------------------------
# Rule 5: closed cycles are never edited
# ---------------------------------------------------------------------------------------------


def test_rule5_an_edit_dated_before_the_current_cycle_is_refused() -> None:
    history = [row("pilot", "individual", date(2026, 8, 5))]
    today = date(2026, 9, 10)
    assert refused(history, row("collaborator", "individual", date(2026, 8, 5), 1), today) == (
        "before_current_cycle"
    )
    # The current cycle's start is allowed.
    validate_new_plan(history, row("collaborator", "individual", date(2026, 9, 5), 1), today)


def test_rule5_a_same_date_correction_of_a_closed_cycle_is_refused() -> None:
    # Join 15 Aug; by 10 Sep the August cycle is closed, so its join row can no longer be corrected.
    assert refused([JOIN], row("pilot", "curated", date(2026, 8, 15), 1), date(2026, 9, 10)) == (
        "before_current_cycle"
    )


def test_rule5_17_00_utc_on_the_4th_is_already_the_5th_in_kl_and_closes_the_cycle() -> None:
    history = [row("pilot", "individual", date(2026, 8, 5))]
    change = row("collaborator", "individual", date(2026, 8, 5), 1)
    # 15:59:59Z on 4 Sep is still 4 Sep in Kuala Lumpur: the August cycle is open and the join
    # row may be corrected. 17:00Z on 4 Sep is 01:00 on 5 Sep there: August is closed.
    before_midnight = kl_today(datetime(2026, 9, 4, 15, 59, 59, tzinfo=UTC))
    after_midnight = kl_today(datetime(2026, 9, 4, 17, 0, tzinfo=UTC))
    assert (before_midnight, after_midnight) == (date(2026, 9, 4), date(2026, 9, 5))
    validate_new_plan(history, change, before_midnight)
    assert refused(history, change, after_midnight) == "before_current_cycle"


def test_rule_order_is_documented_and_specific_codes_win() -> None:
    # after_termination beats before_current_cycle, which beats change_not_on_a_fifth.
    terminated = [JOIN, row("terminated", "individual", date(2026, 9, 20), 1)]
    assert refused(
        terminated, row("pilot", "individual", date(2026, 7, 1), 2), date(2026, 9, 25)
    ) == ("after_termination")
    assert refused(
        [JOIN], row("collaborator", "individual", date(2026, 8, 20), 1), date(2026, 9, 10)
    ) == ("before_current_cycle")


# ---------------------------------------------------------------------------------------------
# Rule 6: pilot length three months; warn from the first cycle start on or after it
# ---------------------------------------------------------------------------------------------


def test_rule6_pilot_ended_from_the_first_cycle_start_on_or_after_three_months() -> None:
    # Join 15 Aug: three months is 15 Nov; the first cycle start on or after it is 5 Dec.
    assert pilot_ended([JOIN], date(2026, 11, 15)) is False
    assert pilot_ended([JOIN], date(2026, 12, 4)) is False
    assert pilot_ended([JOIN], date(2026, 12, 5)) is True
    assert pilot_ended([JOIN], date(2027, 3, 1)) is True


def test_rule6_a_join_on_a_5th_ends_on_the_5th_three_months_later() -> None:
    history = [row("pilot", "individual", date(2026, 8, 5))]
    assert pilot_ended(history, date(2026, 11, 4)) is False
    assert pilot_ended(history, date(2026, 11, 5)) is True


def test_rule6_not_ended_once_converted_or_terminated_or_for_a_collaborator() -> None:
    converted = [JOIN, row("collaborator", "individual", date(2026, 12, 5), 1)]
    assert pilot_ended(converted, date(2026, 12, 5)) is False
    terminated = [JOIN, row("terminated", "individual", date(2026, 12, 10), 1)]
    assert pilot_ended(terminated, date(2026, 12, 10)) is False
    collaborator = [row("collaborator", "individual", date(2026, 8, 15))]
    assert pilot_ended(collaborator, date(2027, 1, 5)) is False
    assert pilot_ended([], date(2027, 1, 5)) is False


def test_rule6_a_conversion_scheduled_for_a_later_cycle_still_warns_now() -> None:
    # Billing continues on pilot terms until the collaborator row's cycle: nothing automatic.
    history = [JOIN, row("collaborator", "individual", date(2027, 1, 5), 1)]
    assert pilot_ended(history, date(2026, 12, 10)) is True
    assert plan_for_cycle(history, DEC) == JOIN
    assert pilot_ended(history, date(2027, 1, 5)) is False


def test_rule6_pilot_bundle_change_keeps_the_first_row_date() -> None:
    history = [JOIN, row("pilot", "complete", date(2026, 10, 5), 1)]
    assert pilot_ended(history, date(2026, 12, 4)) is False
    assert pilot_ended(history, date(2026, 12, 5)) is True


# ---------------------------------------------------------------------------------------------
# Rule 7: every insert is audited (database test in test_billing_plans_schema.py)
# ---------------------------------------------------------------------------------------------


def test_rule7_insert_plan_writes_through_the_console_audit_sql() -> None:
    source = (ROOT / "csa" / "billing" / "plans.py").read_text("utf-8")
    assert '"console/audit.sql"' in source


# ---------------------------------------------------------------------------------------------
# Purity
# ---------------------------------------------------------------------------------------------


def test_cycles_module_does_no_io_and_plans_io_is_only_insert_plan() -> None:
    cycles = ast.parse((ROOT / "csa" / "billing" / "cycles.py").read_text("utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(cycles)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in (
            node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")]
        )
    }
    assert imported <= {"__future__", "dataclasses", "datetime", "zoneinfo"}
    plans = ast.parse((ROOT / "csa" / "billing" / "plans.py").read_text("utf-8"))
    async_defs = [n.name for n in ast.walk(plans) if isinstance(n, ast.AsyncFunctionDef)]
    assert async_defs == ["insert_plan"]
    text = (ROOT / "csa" / "billing" / "plans.py").read_text("utf-8")
    assert "langgraph" not in text
    assert "Decimal" not in text
