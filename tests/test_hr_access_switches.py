"""The switch rules for the HR access page. Pure: no database."""

from __future__ import annotations

import random
from typing import get_args
from uuid import UUID, uuid4

import pytest
from csa.hr.access_switches import (
    EMPTY,
    PRESETS,
    SWITCH_IDS,
    SWITCHES,
    AccessLimitError,
    AccessRows,
    GrantRow,
    PersonAccess,
    ReportingLine,
    SwitchId,
    WorkflowRow,
    apply_preset,
    grant_switches,
    operations,
    rows,
    stamp,
    state,
    switch_set,
    warnings,
)
from csa.hr.automation_models import AccessSave, PrivateAccessSave

BASIC = ("assigned_manager", "department", "display_name", "job_title", "work_email")

OWNER_ROWS = AccessRows(
    frozenset({GrantRow("training.decide", "client_owner", ())}),
    frozenset(
        WorkflowRow(d, "decide", "client_owner") for d in ("recruitment", "attendance", "leave")
    ),
    frozenset(),
)


def person(
    switches: frozenset[SwitchId] = frozenset(),
    *,
    employee_id: UUID | None = None,
    team_size: int = 0,
    user_id: UUID | None = None,
) -> PersonAccess:
    return PersonAccess(
        user_id=user_id or uuid4(),
        display_name="Synthetic Person",
        rows=rows(switches, EMPTY),
        employee_id=employee_id,
        department=None,
        manager_id=None,
        active=True,
        team_size=team_size,
        is_owner=False,
    )


def test_switch_ids_match_the_table() -> None:
    assert set(SWITCH_IDS) == set(SWITCHES) == set(get_args(SwitchId))
    assert len(SWITCH_IDS) == len(SWITCHES)


def test_every_switch_set_is_accepted_by_the_operation_models() -> None:
    for switch, switch_rows in SWITCHES.items():
        built = operations(uuid4(), EMPTY, switch_rows)
        assert built, switch
        assert all(isinstance(op, (AccessSave, PrivateAccessSave)) for op in built)


def test_state_round_trips_each_switch_and_preset() -> None:
    for switch in SWITCHES:
        assert state(rows(frozenset({switch}), EMPTY)).on >= {switch}
    for preset, switches in PRESETS.items():
        assert state(rows(switches, EMPTY)).on == switches, preset


def test_hr_team_includes_directory() -> None:
    current = state(SWITCHES["hr_team"])
    assert dict(current.included) == {"directory": "hr_team"}
    assert current.on == {"directory", "hr_team"}
    assert current.uncovered == EMPTY


def test_shared_payroll_area_stays_until_both_payroll_switches_are_off() -> None:
    assert "payroll" in rows(switch_set("payroll_prepare"), EMPTY).areas
    assert "payroll" in rows(switch_set("payroll_review"), EMPTY).areas
    assert "payroll" not in rows(frozenset(), EMPTY).areas


def test_partial_and_owner_rows_are_uncovered_and_survive() -> None:
    partial = AccessRows(
        frozenset({GrantRow("directory.read", "self", ("display_name",))}), frozenset(), frozenset()
    )
    current = state(partial.union(OWNER_ROWS))
    assert current.on == frozenset()
    assert current.uncovered == partial.union(OWNER_ROWS)
    assert rows(frozenset(), current.uncovered) == partial.union(OWNER_ROWS)
    # With a switch on, the owner rows are still kept next to it.
    kept = rows(switch_set("own_requests"), current.uncovered)
    assert OWNER_ROWS.issubset(kept) and SWITCHES["own_requests"].issubset(kept)


def test_presets_only_add() -> None:
    assert "payroll_review" in apply_preset(switch_set("payroll_review"), "hr_team")
    assert apply_preset(frozenset(), "manager") == PRESETS["manager"]


def test_operations_split_grants_and_private_areas() -> None:
    user = uuid4()
    only_area = operations(user, EMPTY, SWITCHES["recruitment"])
    assert len(only_area) == 1
    assert isinstance(only_area[0], PrivateAccessSave)
    assert (only_area[0].area, only_area[0].enabled, only_area[0].user_id) == (
        "recruitment",
        True,
        user,
    )
    team = operations(user, EMPTY, SWITCHES["approves_team"])
    assert len(team) == 1 and isinstance(team[0], AccessSave)
    removed = operations(user, SWITCHES["recruitment"], EMPTY)
    assert [(op.area, op.enabled) for op in removed if isinstance(op, PrivateAccessSave)] == [
        ("recruitment", False)
    ]
    assert operations(user, SWITCHES["directory"], SWITCHES["directory"]) == []


def test_over_limit_rows_raise_access_limit_error() -> None:
    many = AccessRows(
        frozenset(GrantRow("directory.read", f"scope{i}", ()) for i in range(33)),
        frozenset(),
        frozenset(),
    )
    with pytest.raises(AccessLimitError):
        operations(uuid4(), EMPTY, many)
    too_many_workflows = AccessRows(
        frozenset(),
        frozenset(WorkflowRow("leave", "read", f"scope{i}") for i in range(49)),
        frozenset(),
    )
    with pytest.raises(AccessLimitError):
        operations(uuid4(), EMPTY, too_many_workflows)


def test_warnings_each_kind() -> None:
    manager_user, manager_emp = uuid4(), uuid4()
    lonely_user = uuid4()
    both_user = uuid4()
    people = [
        person(
            switch_set("approves_team"), employee_id=manager_emp, team_size=1, user_id=manager_user
        ),
        person(switch_set("approves_team"), employee_id=uuid4(), team_size=0, user_id=lonely_user),
        person(switch_set("payroll_prepare", "payroll_review"), user_id=both_user),
    ]
    reports = uuid4()
    unattended, orphan, boss = uuid4(), uuid4(), uuid4()
    lines = [
        ReportingLine(reports, "Covered", manager_emp, manager_user),
        ReportingLine(unattended, "No approver", boss, None),
        ReportingLine(orphan, "No manager", None, None),
    ]
    found = warnings(people, lines)
    kinds = sorted(w.kind for w in found)
    assert kinds == sorted(
        ["no_approver", "no_manager", "manager_without_team", "prepares_and_reviews"]
    ), found
    by_kind = {w.kind: w for w in found}
    assert by_kind["no_approver"].employee_id == unattended
    assert by_kind["no_manager"].employee_id == orphan
    assert by_kind["manager_without_team"].user_id == lonely_user
    assert by_kind["prepares_and_reviews"].user_id == both_user
    assert [w.kind for w in found] == sorted(w.kind for w in found)
    # Nobody reviews payroll: the fifth kind.
    nobody = warnings([person(switch_set("payroll_prepare"))], [])
    assert [w.kind for w in nobody] == ["no_payroll_reviewer"]


def test_manager_whose_account_lacks_the_switch_is_a_no_approver_case() -> None:
    manager_user = uuid4()
    people = [person(switch_set("payroll_review"), user_id=manager_user, team_size=1)]
    found = warnings(people, [ReportingLine(uuid4(), "Report", uuid4(), manager_user)])
    assert [w.kind for w in found] == ["no_approver"]


def test_member_without_employee_has_switches_and_no_team() -> None:
    member = person(switch_set("approves_team", "payroll_review"), employee_id=None, team_size=0)
    found = warnings([member], [])
    assert [(w.kind, w.user_id, w.employee_id) for w in found] == [
        ("manager_without_team", member.user_id, None)
    ]


def test_stamp_ignores_order_and_changes_with_any_row() -> None:
    people = [
        person(switch_set("directory")),
        person(switch_set("own_requests")),
        person(frozenset()),
    ]
    manager = uuid4()
    lines = [ReportingLine(uuid4(), f"E{i}", manager, None) for i in range(4)]
    base = stamp(people, lines)
    shuffled_people, shuffled_lines = people[:], lines[:]
    random.Random(7).shuffle(shuffled_people)
    random.Random(8).shuffle(shuffled_lines)
    assert stamp(shuffled_people, shuffled_lines) == base
    assert len(base) == 64

    extra = people[2]._replace(rows=extra_row(people[2].rows))
    assert stamp([*people[:2], extra], lines) != base
    moved = lines[0]._replace(manager_employee_id=uuid4())
    assert stamp(people, [moved, *lines[1:]]) != base


def extra_row(current: AccessRows) -> AccessRows:
    return current.union(
        AccessRows(frozenset({GrantRow("training.read", "self", ())}), frozenset(), frozenset())
    )


def test_grant_switches_ignores_areas() -> None:
    only_workflows = SWITCHES["payroll_prepare"].workflows
    assert grant_switches(frozenset(), only_workflows) == {"payroll_prepare"}
    assert "recruitment" not in grant_switches(frozenset(), frozenset())
    both = SWITCHES["hr_team"]
    assert grant_switches(both.grants, both.workflows) == {"directory", "hr_team"}


def test_from_summary_accepts_the_database_row_shape() -> None:
    row = {
        "user_id": uuid4(),
        "display_name": "Synthetic Person",
        "grants": [
            {"action": "directory.read", "scope": "self", "fields": ["work_email", "display_name"]}
        ],
        "workflows": [{"domain": "leave", "action": "read", "scope": "self"}],
        "areas": ["payroll"],
        "employee_id": None,
        "department": None,
        "manager_id": None,
        "active": None,
        "team_size": 0,
        "is_owner": False,
    }
    parsed = PersonAccess.from_summary(row)
    assert parsed.rows.grants == {
        GrantRow("directory.read", "self", ("display_name", "work_email"))
    }
    assert parsed.rows.workflows == {WorkflowRow("leave", "read", "self")}
    assert parsed.rows.areas == {"payroll"}


def test_switch_grant_supersedes_partial_uncovered_grant() -> None:
    cases: list[tuple[SwitchId, str]] = [("own_requests", "self"), ("directory", "hr_operator")]
    for switch, scope in cases:
        partial = AccessRows(
            frozenset({GrantRow("directory.read", scope, ("display_name",))}),
            frozenset(),
            frozenset(),
        )
        assert state(partial).uncovered == partial
        saved = rows(switch_set(switch), partial)
        matching = [g for g in saved.grants if (g.action, g.scope) == ("directory.read", scope)]
        assert matching == [GrantRow("directory.read", scope, BASIC)]
        built = operations(uuid4(), partial, saved)
        saves = [op for op in built if isinstance(op, AccessSave)]
        assert len(saves) == 1
        pairs = [(g.action.value, g.scope.value) for g in saves[0].grants]
        assert len(pairs) == len(set(pairs))
