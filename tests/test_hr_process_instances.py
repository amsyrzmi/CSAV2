"""Migration 0632: processes started, re-dated and ended from person changes, the manual start and
cancel, the probation date, and the completion rule (disposable database). Reuses the HrWorld
fixture of tests/test_hr_persistence.py; people are written through the real HR operations route,
leavers are finished through hr_finish_leaver, and the new functions run as csa_app inside the
caller's HR session, the way the service will.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any, LiteralString
from uuid import UUID, uuid4

import psycopg
import pytest
from csa.core.tenancy import tx
from psycopg.types.json import Jsonb

from tests.conftest import requires_docker
from tests.test_hr_people import (
    add_body,
    make_other_tenant_owner,
    make_people_manager,
    ok,
    person_row,
    update_body,
)
from tests.test_hr_people_records import db_today, finish, leave_body, set_last_day, versioned
from tests.test_hr_persistence import HrWorld, hr_database, world
from tests.test_hr_process_templates import (
    FP,
    OTHER_FP,
    apply,
    as_hr,
    create,
    create_body,
    department,
    step,
)

__all__ = ["hr_database", "world"]
pytestmark = [requires_docker, pytest.mark.asyncio]

START_SQL: LiteralString = "SELECT public.hr_process_start_apply(%s,%s,%s,%s) AS result"
CANCEL_SQL: LiteralString = "SELECT public.hr_process_cancel_apply(%s,%s,%s,%s) AS result"
PROBATION_SQL: LiteralString = "SELECT public.hr_probation_set(%s,%s,%s,%s) AS result"
PUBLIC_FUNCTIONS = (
    "hr_process_start_apply(uuid,uuid,text,jsonb)",
    "hr_process_cancel_apply(uuid,uuid,text,jsonb)",
    "hr_probation_set(uuid,uuid,text,jsonb)",
)
INTERNAL_FUNCTIONS = (
    "hr_process_start(uuid,uuid,text,uuid,uuid)",
    "hr_process_settle(uuid,uuid)",
    "hr_process_cancel(uuid,uuid,uuid,text)",
    "hr_process_redate(uuid,uuid,uuid)",
    "hr_process_reassign_manager(uuid,uuid,uuid)",
    "hr_process_person_left(uuid,uuid,uuid)",
    "hr_process_manager_of(uuid,uuid)",
    "hr_process_task_due(text,boolean,text,integer,jsonb)",
    "hr_process_on_employee()",
)
START = date(2026, 12, 1)


async def run(
    world: HrWorld,
    who: str,
    sql: LiteralString,
    body: dict[str, Any],
    *,
    key: UUID | None = None,
    fingerprint: str = FP,
    tenant: UUID | None = None,
) -> dict[str, Any]:
    params = (tenant or world.tenant, key or uuid4(), fingerprint, Jsonb(body))
    rows = await as_hr(world, who, sql, params)
    return rows[0]["result"]


async def run_in_other_tenant(
    world: HrWorld, sql: LiteralString, body: dict[str, Any]
) -> dict[str, Any]:
    """The other tenant's HR owner (make_other_tenant_owner) writes in the other tenant."""
    async with tx(world.service.pool, str(world.other_tenant)) as conn:
        await conn.execute(
            "SELECT set_config('app.hr_user_id', %s, true)", (str(world.users["owner"]),)
        )
        cursor = await conn.execute(sql, (world.other_tenant, uuid4(), FP, Jsonb(body)))
        row = await cursor.fetchone()
    assert row is not None
    return row["result"]


def start_body(
    employee: UUID, kind: str = "custom", template: UUID | None = None
) -> dict[str, Any]:
    return {
        "employee_id": str(employee),
        "kind": kind,
        "template_id": str(template) if template else None,
    }


async def processes(world: HrWorld, employee: UUID) -> list[dict[str, Any]]:
    return await world.q(
        "SELECT id, kind, status, template_id, template_version, anchor_dates, started_by,"
        " closed_at FROM hr_processes WHERE client_id=%s AND employee_id=%s"
        " ORDER BY started_at, id",
        (world.tenant, employee),
    )


async def tasks(world: HrWorld, process: UUID) -> list[dict[str, Any]]:
    return await world.q(
        "SELECT id, ordinal, title_en, owner_role, assignee_employee_id, anchor, due_offset_days,"
        " due_on, due_overridden, required, auto_check, status, completed_by, completed_at,"
        " note, version FROM hr_process_tasks WHERE client_id=%s AND process_id=%s"
        " ORDER BY ordinal",
        (world.tenant, process),
    )


async def events(world: HrWorld, process: UUID) -> list[dict[str, Any]]:
    return await world.q(
        "SELECT e.action, e.task_id, t.ordinal, e.actor, e.detail FROM hr_process_events e"
        " LEFT JOIN hr_process_tasks t ON t.client_id=e.client_id AND t.id=e.task_id"
        " WHERE e.client_id=%s AND e.process_id=%s ORDER BY e.at, e.id",
        (world.tenant, process),
    )


def actions(found: list[dict[str, Any]]) -> list[tuple[str, int | None]]:
    """The events as (action, task ordinal), sorted, so equal timestamps cannot reorder them."""
    return sorted(((e["action"], e["ordinal"]) for e in found), key=lambda a: (a[0], a[1] or 0))


def event(found: list[dict[str, Any]], action: str, ordinal: int | None = None) -> dict[str, Any]:
    [only] = [e for e in found if e["action"] == action and e["ordinal"] == ordinal]
    return only


async def only_process(world: HrWorld, employee: UUID) -> dict[str, Any]:
    [process] = await processes(world, employee)
    return process


async def set_task(world: HrWorld, process: UUID, ordinal: int, assignments: LiteralString) -> None:
    """A task change the later task actions (Task 6) will make; written as the owner here."""
    await world.q(
        "UPDATE hr_process_tasks SET "
        + assignments
        + " WHERE client_id=%s AND process_id=%s AND ordinal=%s",
        (world.tenant, process, ordinal),
    )


async def add_person(world: HrWorld, who: str = "owner", **values: Any) -> UUID:
    added = uuid4()
    await ok(world, who, add_body(added, start_date=START.isoformat(), **values))
    return added


async def insert_employee(
    world: HrWorld, *, active: bool = True, manager: UUID | None = None
) -> UUID:
    """A person written straight into the table (no session), as fixtures and old data do."""
    created = uuid4()
    await world.q(
        "INSERT INTO hr_employees(client_id,id,display_name,is_active,manager_id,start_date)"
        " VALUES (%s,%s,'Synthetic Direct',%s,%s,%s)",
        (world.tenant, created, active, manager, START),
    )
    return created


# ---- Starting -------------------------------------------------------------------------------


async def test_adding_a_person_starts_onboarding_from_the_matching_template(
    world: HrWorld,
) -> None:
    ops = await department(world, "Operations")
    default = await create(
        world,
        name="Default onboarding",
        steps=[step(title_en="Welcome pack", due_offset_days=-2)],
    )
    specific = await create(
        world,
        name="Operations onboarding",
        rules={"department_id": str(ops)},
        steps=[
            step(title_en="Safety briefing", due_offset_days=1),
            step(
                title_en="Probation review",
                anchor="probation_end",
                due_offset_days=-7,
                required=False,
            ),
        ],
    )
    added = uuid4()
    receipt = await ok(
        world, "owner", add_body(added, start_date=START.isoformat(), department_id=str(ops))
    )
    assert (receipt["operation"], receipt["version"], receipt["duplicate"]) == (
        "person.add",
        0,
        False,
    )
    process = await only_process(world, added)
    assert (
        process["kind"],
        process["status"],
        process["template_id"],
        process["template_version"],
        process["started_by"],
        process["closed_at"],
    ) == ("onboarding", "active", specific, 1, world.users["owner"], None)
    assert process["anchor_dates"] == {
        "start_date": START.isoformat(),
        "last_day": None,
        "probation_end": None,
    }
    found = await tasks(world, process["id"])
    assert [
        (t["ordinal"], t["title_en"], t["owner_role"], t["due_on"], t["required"], t["status"])
        for t in found
    ] == [
        (1, "Safety briefing", "hr", START + timedelta(days=1), True, "todo"),
        # Spec 5.7.3: a step anchored on a date that is not set waits with no due date.
        (2, "Probation review", "hr", None, False, "todo"),
    ]
    assert all(t["version"] == 1 and not t["due_overridden"] for t in found)
    logged = await events(world, process["id"])
    assert actions(logged) == [("process.started", None), ("task.created", 1), ("task.created", 2)]
    assert all(e["actor"] == world.users["owner"] for e in logged)
    assert event(logged, "process.started")["detail"] == {
        "kind": "onboarding",
        "template_id": str(specific),
        "template_version": 1,
    }
    assert event(logged, "task.created", 1)["detail"] == {
        "owner_role": "hr",
        "assignee_employee_id": None,
        "due_on": (START + timedelta(days=1)).isoformat(),
    }
    # Someone outside Operations gets the company default.
    other = await add_person(world)
    assert (await only_process(world, other))["template_id"] == default


async def test_no_matching_template_adds_the_person_and_starts_nothing(world: HrWorld) -> None:
    ops = await department(world, "Operations")
    await create(world, rules={"department_id": str(ops)})
    await create(world, kind="offboarding")
    inactive = await create(world, name="Inactive default")
    await apply(
        world,
        "owner",
        {"operation": "template.deactivate", "template_id": str(inactive), "expected_version": 1},
    )
    added = await add_person(world)
    assert (await person_row(world, added))["is_active"] is True
    assert await processes(world, added) == []
    # A person written with no session at all (fixtures, old data) is no different.
    direct = await insert_employee(world)
    assert await processes(world, direct) == []


async def test_process_is_a_frozen_copy(world: HrWorld) -> None:
    template = await create(world, steps=[step(title_en="Sign the contract", due_offset_days=0)])
    first = await add_person(world)
    process = await only_process(world, first)
    before = await tasks(world, process["id"])
    updated = await apply(
        world,
        "owner",
        {
            "operation": "template.update",
            "template_id": str(template),
            "expected_version": 1,
            "name": "Renamed",
            "rules": {},
            "steps": [
                step(title_en="Changed title", owner_role="manager", due_offset_days=9),
                step(title_en="New step"),
            ],
            "is_active": True,
        },
    )
    assert updated["version"] == 2
    assert await tasks(world, process["id"]) == before
    assert (await only_process(world, first))["template_version"] == 1
    # The next person gets the new version.
    second = await add_person(world)
    newer = await only_process(world, second)
    assert newer["template_version"] == 2
    assert [t["title_en"] for t in await tasks(world, newer["id"])] == ["Changed title", "New step"]


async def test_task_assignees_resolved(world: HrWorld) -> None:
    manager, outsider = world.employees["manager"], world.employees["outsider"]
    unlinked = await insert_employee(world)
    gone = await insert_employee(world, active=False)
    await create(
        world,
        steps=[
            step(title_en="Meet the team", owner_role="manager"),
            step(title_en="Fill in details", owner_role="employee"),
            step(title_en="Buddy lunch", owner_role="person", owner_employee_id=str(outsider)),
            step(title_en="Payroll set-up", owner_role="hr"),
        ],
    )

    async def assignees(employee: UUID) -> tuple[list[UUID | None], dict[str, Any]]:
        process = await only_process(world, employee)
        found = await tasks(world, process["id"])
        created = event(await events(world, process["id"]), "task.created", 1)
        return [t["assignee_employee_id"] for t in found], created["detail"]

    linked = await add_person(world, manager_id=str(manager))
    names, detail = await assignees(linked)
    assert names == [manager, linked, outsider, None]
    assert detail == {
        "owner_role": "manager",
        "assignee_employee_id": str(manager),
        "due_on": START.isoformat(),
    }
    # A manager with no console account, no manager, and an inactive manager: the HR pool.
    for person, reason in (
        (await add_person(world, manager_id=str(unlinked)), "manager_unlinked"),
        (await add_person(world), "no_manager"),
        (await insert_employee(world, manager=gone), "manager_inactive"),
    ):
        names, detail = await assignees(person)
        assert names == [None, person, outsider, None], reason
        assert detail == {
            "owner_role": "manager",
            "assignee_employee_id": None,
            "due_on": START.isoformat(),
            "fallback": "hr_pool",
            "reason": reason,
        }


async def test_rehire_starts_a_new_onboarding_and_old_ones_stay_closed(world: HrWorld) -> None:
    today = await db_today(world)
    await create(
        world,
        steps=[
            step(title_en="Fill in details", owner_role="employee"),
            step(title_en="Order a laptop", required=False),
        ],
    )
    person = await add_person(world)
    first = await only_process(world, person)
    # Leaving: the person's own task is skipped, nothing required is left, so the onboarding
    # completes and the optional task is skipped by the system.
    await ok(world, "owner", await leave_body(world, person, today))
    await set_last_day(world, person, today - timedelta(days=1))
    assert await finish(world, person) == "moved_finished"
    closed = await only_process(world, person)
    assert (closed["status"], closed["closed_at"] is not None) == ("completed", True)
    assert [(t["status"], t["note"]) for t in await tasks(world, first["id"])] == [
        ("skipped", "person left"),
        ("skipped", None),
    ]
    logged = await events(world, first["id"])
    assert event(logged, "task.skipped", 1)["detail"] == {"reason": "person_left"}
    assert event(logged, "task.skipped", 2)["detail"] == {"reason": "process_completed"}
    assert event(logged, "process.completed")["actor"] is None
    closed_tasks = await tasks(world, first["id"])

    restart = today + timedelta(days=7)
    rehire = await versioned(world, "person.rehire", person, start_date=restart.isoformat())
    await ok(world, "owner", rehire)
    old, new = await processes(world, person)
    assert old == closed  # untouched
    assert await tasks(world, first["id"]) == closed_tasks
    assert (new["kind"], new["status"], new["anchor_dates"]["start_date"]) == (
        "onboarding",
        "active",
        restart.isoformat(),
    )
    assert [(t["status"], t["due_on"]) for t in await tasks(world, new["id"])] == [
        ("todo", restart),
        ("todo", restart),
    ]


async def test_starting_again_supersedes_the_active_process(world: HrWorld) -> None:
    """Ruling R1: a start first cancels a still-active process of the same person and kind, so
    the one-active index never fails inside a person write.
    """
    today = await db_today(world)
    await create(
        world,
        steps=[
            step(title_en="Fill in details", owner_role="employee"),
            step(title_en="Payroll set-up"),
        ],
    )
    person = await add_person(world)
    first = await only_process(world, person)
    await ok(world, "owner", await leave_body(world, person, today))
    await set_last_day(world, person, today - timedelta(days=1))
    await finish(world, person)
    # The HR task is still open, so the onboarding stays active after the person left.
    assert (await only_process(world, person))["status"] == "active"
    await ok(
        world,
        "owner",
        await versioned(world, "person.rehire", person, start_date=today.isoformat()),
    )
    old, new = await processes(world, person)
    assert (old["id"], old["status"], new["status"]) == (first["id"], "cancelled", "active")
    assert [t["status"] for t in await tasks(world, old["id"])] == ["skipped", "skipped"]
    logged = await events(world, old["id"])
    # The rehire closes the old employment's onboarding itself (spec 12: old ones stay closed).
    assert event(logged, "process.cancelled")["detail"] == {"reason": "rehired"}
    assert event(logged, "task.skipped", 2)["detail"] == {"reason": "process_cancelled"}
    assert event(logged, "process.cancelled")["actor"] == world.users["owner"]
    # A manual onboarding start for the same person supersedes the rehire's one.
    started = await run(world, "owner", START_SQL, start_body(person, "onboarding"))
    statuses = [(p["id"], p["status"]) for p in await processes(world, person)]
    assert statuses == [
        (old["id"], "cancelled"),
        (new["id"], "cancelled"),
        (UUID(started["process_id"]), "active"),
    ]
    superseded = event(await events(world, new["id"]), "process.cancelled")
    assert superseded["detail"] == {"reason": "superseded"}


async def test_rehire_with_no_matching_template_still_closes_the_old_onboarding(
    world: HrWorld,
) -> None:
    today = await db_today(world)
    template = await create(
        world,
        steps=[
            step(title_en="Fill in details", owner_role="employee"),
            step(title_en="Payroll set-up"),
        ],
    )
    person = await add_person(world)
    first = await only_process(world, person)
    await ok(world, "owner", await leave_body(world, person, today))
    await set_last_day(world, person, today - timedelta(days=1))
    await finish(world, person)
    left = await only_process(world, person)
    assert left["status"] == "active"  # Payroll still open
    await apply(
        world,
        "owner",
        {"operation": "template.deactivate", "template_id": str(template), "expected_version": 1},
    )
    before = await events(world, first["id"])
    restart = today + timedelta(days=7)
    await ok(
        world,
        "owner",
        await versioned(world, "person.rehire", person, start_date=restart.isoformat()),
    )
    # The old onboarding is closed, nothing new starts, and its tasks were never re-dated.
    closed = await only_process(world, person)
    assert (closed["id"], closed["status"]) == (first["id"], "cancelled")
    assert closed["anchor_dates"] == left["anchor_dates"]  # not moved to the new start date
    assert [(t["status"], t["due_on"]) for t in await tasks(world, first["id"])] == [
        ("skipped", START),
        ("skipped", START),
    ]
    logged = await events(world, first["id"])
    assert event(logged, "process.cancelled")["detail"] == {"reason": "rehired"}
    assert not [e for e in logged if e["action"] == "task.date_changed"]
    assert actions(logged[len(before) :]) == [("process.cancelled", None), ("task.skipped", 2)]


# ---- Leaving --------------------------------------------------------------------------------


async def test_marking_leaving_starts_offboarding_and_undo_cancels_it(world: HrWorld) -> None:
    today = await db_today(world)
    staff = world.employees["staff"]
    template = await create(
        world,
        kind="offboarding",
        steps=[
            step(title_en="Plan the handover", anchor="last_day", due_offset_days=-5),
            step(title_en="Return the laptop", anchor="last_day", owner_role="employee"),
            step(
                title_en="Remove access",
                anchor="last_day",
                auto_check="access_removed",
            ),
        ],
    )
    last = today + timedelta(days=10)
    await ok(world, "owner", await leave_body(world, staff, last))
    first = await only_process(world, staff)
    assert (first["kind"], first["status"], first["template_id"]) == (
        "offboarding",
        "active",
        template,
    )
    assert first["anchor_dates"]["last_day"] == last.isoformat()
    assert [(t["due_on"], t["assignee_employee_id"]) for t in await tasks(world, first["id"])] == [
        (last - timedelta(days=5), None),
        (last, staff),
        (last, None),
    ]

    await ok(world, "owner", await versioned(world, "person.leave_undo", staff))
    cancelled = await only_process(world, staff)
    assert (cancelled["status"], cancelled["closed_at"] is not None) == ("cancelled", True)
    assert [t["status"] for t in await tasks(world, first["id"])] == ["skipped"] * 3
    logged = await events(world, first["id"])
    assert actions(logged) == [
        ("process.cancelled", None),
        ("process.started", None),
        ("task.created", 1),
        ("task.created", 2),
        ("task.created", 3),
        ("task.skipped", 1),
        ("task.skipped", 2),
        ("task.skipped", 3),
    ]
    assert event(logged, "process.cancelled")["detail"] == {"reason": "last_day_cleared"}
    assert event(logged, "process.cancelled")["actor"] == world.users["owner"]

    # Marking again starts a fresh one; the cancelled one stays as it was.
    await ok(world, "owner", await leave_body(world, staff, last))
    old, new = await processes(world, staff)
    assert old == cancelled
    assert (new["status"], new["kind"]) == ("active", "offboarding")


async def test_leaver_task_after_last_day_is_clamped(world: HrWorld) -> None:
    today = await db_today(world)
    staff, manager = world.employees["staff"], world.employees["manager"]
    await create(
        world,
        kind="offboarding",
        steps=[
            step(
                title_en="Exit survey", anchor="last_day", owner_role="employee", due_offset_days=3
            ),
            step(title_en="Archive files", anchor="last_day", due_offset_days=3),
            step(
                title_en="Reference letter",
                anchor="last_day",
                owner_role="person",
                owner_employee_id=str(manager),
                due_offset_days=3,
            ),
            step(
                title_en="Own handover note",
                anchor="last_day",
                owner_role="person",
                owner_employee_id=str(staff),
                due_offset_days=2,
            ),
            step(
                title_en="Early task", anchor="last_day", owner_role="employee", due_offset_days=-1
            ),
        ],
    )
    last = today + timedelta(days=4)
    await ok(world, "owner", await leave_body(world, staff, last))
    process = await only_process(world, staff)
    assert [t["due_on"] for t in await tasks(world, process["id"])] == [
        last,  # the leaver's own task, clamped
        last + timedelta(days=3),  # HR
        last + timedelta(days=3),  # someone else
        last,  # a person step naming the leaver, clamped
        last - timedelta(days=1),
    ]
    logged = await events(world, process["id"])
    clamped = {
        e["ordinal"]
        for e in logged
        if e["action"] == "task.created" and e["detail"].get("clamped") is True
    }
    assert clamped == {1, 4}


async def test_changing_last_day_recomputes_open_tasks_but_not_overridden_ones(
    world: HrWorld,
) -> None:
    today = await db_today(world)
    staff = world.employees["staff"]
    await create(
        world,
        kind="offboarding",
        steps=[
            step(title_en="Plan the handover", anchor="last_day", due_offset_days=-5),
            step(title_en="Overridden", anchor="last_day", due_offset_days=-2),
            step(title_en="Already done", anchor="last_day", due_offset_days=-1),
            step(
                title_en="Exit survey", anchor="last_day", owner_role="employee", due_offset_days=2
            ),
        ],
    )
    first_last = today + timedelta(days=10)
    await ok(world, "owner", await leave_body(world, staff, first_last))
    process = await only_process(world, staff)
    pid = process["id"]
    fixed = today + timedelta(days=30)
    # HR moved this one by hand (Task 6 writes due_overridden); it must stay where HR put it.
    await world.q(
        "UPDATE hr_process_tasks SET due_on=%s, due_overridden=true"
        " WHERE client_id=%s AND process_id=%s AND ordinal=2",
        (fixed, world.tenant, pid),
    )
    await set_task(world, pid, 3, "status='done', completed_at=now()")
    before = {t["ordinal"]: t for t in await tasks(world, pid)}

    # Marking again with a later day moves the open, not overridden tasks; no new process.
    second_last = today + timedelta(days=20)
    await ok(world, "owner", await leave_body(world, staff, second_last))
    assert [p["id"] for p in await processes(world, staff)] == [pid]
    assert (await only_process(world, staff))["anchor_dates"]["last_day"] == second_last.isoformat()
    after = {t["ordinal"]: t for t in await tasks(world, pid)}
    assert after[1]["due_on"] == second_last - timedelta(days=5)
    assert after[1]["version"] == before[1]["version"] + 1
    assert after[2] == before[2]
    assert after[3] == before[3]
    assert after[4]["due_on"] == second_last  # still clamped to the new last day
    logged = await events(world, pid)
    assert event(logged, "task.date_changed", 1)["detail"] == {
        "anchor": "last_day",
        "old": (first_last - timedelta(days=5)).isoformat(),
        "new": (second_last - timedelta(days=5)).isoformat(),
    }
    assert event(logged, "task.date_changed", 4)["detail"] == {
        "anchor": "last_day",
        "old": first_last.isoformat(),
        "new": second_last.isoformat(),
        "clamped": True,
    }
    assert {e["ordinal"] for e in logged if e["action"] == "task.date_changed"} == {1, 4}
    assert all(
        e["actor"] == world.users["owner"] for e in logged if e["action"] == "task.date_changed"
    )
    # A direct write with no session (the leaver test helpers) moves them too, as "System".
    await set_last_day(world, staff, second_last + timedelta(days=1))
    moved = event(
        [e for e in await events(world, pid) if e["actor"] is None], "task.date_changed", 1
    )
    assert moved["detail"]["new"] == (second_last - timedelta(days=4)).isoformat()


async def test_manager_change_moves_open_manager_tasks_only(world: HrWorld) -> None:
    manager, outsider = world.employees["manager"], world.employees["outsider"]
    await create(
        world,
        steps=[
            step(title_en="Meet the team", owner_role="manager"),
            step(title_en="Set goals", owner_role="manager"),
            step(title_en="Payroll set-up"),
            step(title_en="Fill in details", owner_role="employee"),
        ],
    )
    person = await add_person(world, manager_id=str(manager))
    pid = (await only_process(world, person))["id"]
    await set_task(world, pid, 2, "status='done', completed_at=now()")
    before = {t["ordinal"]: t for t in await tasks(world, pid)}

    await ok(world, "owner", await update_body(world, person, manager_id=str(outsider)))
    after = {t["ordinal"]: t for t in await tasks(world, pid)}
    assert after[1]["assignee_employee_id"] == outsider
    assert after[1]["version"] == before[1]["version"] + 1
    assert (after[2], after[3], after[4]) == (before[2], before[3], before[4])
    moved = event(await events(world, pid), "task.reassigned", 1)
    assert moved["detail"] == {"from": str(manager), "to": str(outsider)}
    assert moved["actor"] == world.users["owner"]

    # No manager any more: the HR pool, with the reason.
    await ok(world, "owner", await update_body(world, person, manager_id=None))
    assert (await tasks(world, pid))[0]["assignee_employee_id"] is None
    reassigned = [e for e in await events(world, pid) if e["action"] == "task.reassigned"]
    assert len(reassigned) == 2
    assert {"from": str(outsider), "to": None, "fallback": "hr_pool", "reason": "no_manager"} in [
        e["detail"] for e in reassigned
    ]
    # Another field changing writes no event.
    await ok(world, "owner", await update_body(world, person, job_title="Senior Executive"))
    assert len([e for e in await events(world, pid) if e["action"] == "task.reassigned"]) == 2


async def test_probation_date_fills_due_dates(world: HrWorld) -> None:
    await create(
        world,
        steps=[
            step(title_en="Welcome", due_offset_days=0),
            step(title_en="Probation review", anchor="probation_end", due_offset_days=-7),
        ],
    )
    person = await add_person(world)
    pid = (await only_process(world, person))["id"]
    assert [t["due_on"] for t in await tasks(world, pid)] == [START, None]
    version = (await person_row(world, person))["version"]
    end = date(2027, 3, 1)
    body = {"employee_id": str(person), "expected_version": version, "probation_end": "2027-03-01"}
    receipt = await run(world, "owner", PROBATION_SQL, body)
    assert receipt == {
        "operation": "person.probation_set",
        "resource_id": str(person),
        "version": version + 1,
        "status": "saved",
        "duplicate": False,
    }
    row = await person_row(world, person)
    assert (row["probation_end"], row["version"]) == (end, version + 1)
    found = await tasks(world, pid)
    assert [t["due_on"] for t in found] == [START, end - timedelta(days=7)]
    assert len(found) == 2  # no new task
    assert (await only_process(world, person))["anchor_dates"]["probation_end"] == "2027-03-01"
    filled = event(await events(world, pid), "task.date_changed", 2)
    assert filled["detail"] == {
        "anchor": "probation_end",
        "old": None,
        "new": (end - timedelta(days=7)).isoformat(),
    }
    assert filled["actor"] == world.users["owner"]
    # A start date change moves the start-anchored task.
    await ok(world, "owner", await update_body(world, person, start_date="2026-12-08"))
    assert (await tasks(world, pid))[0]["due_on"] == date(2026, 12, 8)
    # Clearing the probation date empties the due date again.
    version = (await person_row(world, person))["version"]
    cleared = {"employee_id": str(person), "expected_version": version, "probation_end": None}
    await run(world, "owner", PROBATION_SQL, cleared)
    assert (await tasks(world, pid))[1]["due_on"] is None
    assert (await person_row(world, person))["probation_end"] is None


async def test_probation_set_rejects_stale_version_and_non_hr(world: HrWorld) -> None:
    staff = world.employees["staff"]
    version = (await person_row(world, staff))["version"]
    body = {"employee_id": str(staff), "expected_version": version, "probation_end": "2027-01-31"}
    for who in ("staff", "manager", "admin"):
        with pytest.raises(
            psycopg.errors.InsufficientPrivilege, match=r"^hr_people_manager_required\b"
        ):
            await run(world, who, PROBATION_SQL, body)
    with pytest.raises(
        psycopg.errors.InsufficientPrivilege, match=r"^hr_people_manager_required\b"
    ):
        await run(world, "owner", PROBATION_SQL, body, tenant=world.other_tenant)
    assert (await person_row(world, staff))["probation_end"] is None
    await make_people_manager(world)
    key = uuid4()
    first = await run(world, "operator", PROBATION_SQL, body, key=key)
    assert first["version"] == version + 1
    # The same key and fingerprint returns the first receipt; another fingerprint is refused.
    assert await run(world, "operator", PROBATION_SQL, body, key=key) == {
        **first,
        "duplicate": True,
    }
    with pytest.raises(psycopg.errors.CheckViolation, match=r"^hr_idempotency_conflict\b"):
        await run(world, "operator", PROBATION_SQL, body, key=key, fingerprint=OTHER_FP)
    for stale in (body, {**body, "expected_version": None}, {**body, "expected_version": "2"}):
        with pytest.raises(psycopg.errors.CheckViolation, match=r"^hr_person_changed\b"):
            await run(world, "operator", PROBATION_SQL, stale)
    fresh = version + 1
    for bad in (
        {"employee_id": str(staff), "expected_version": fresh, "probation_end": "not-a-date"},
        {"employee_id": str(staff), "expected_version": fresh, "probation_end": 20270131},
        {"employee_id": str(staff), "expected_version": fresh},
    ):
        with pytest.raises(psycopg.errors.CheckViolation, match=r"^hr_probation_end_invalid\b"):
            await run(world, "operator", PROBATION_SQL, bad)
    for missing in (str(uuid4()), "not-a-uuid", None):
        with pytest.raises(psycopg.errors.CheckViolation, match=r"^hr_employee_unavailable\b"):
            await run(
                world,
                "operator",
                PROBATION_SQL,
                {"employee_id": missing, "expected_version": 1, "probation_end": None},
            )
    row = await person_row(world, staff)
    assert (row["probation_end"], row["version"]) == (date(2027, 1, 31), fresh)


async def test_finishing_the_leaver_skips_open_leaver_tasks_and_completes_offboarding(
    world: HrWorld,
) -> None:
    today = await db_today(world)
    staff, manager, outsider = (world.employees[n] for n in ("staff", "manager", "outsider"))
    await create(
        world,
        kind="offboarding",
        steps=[
            step(title_en="Collect the laptop", anchor="last_day"),
            step(title_en="Exit survey", anchor="last_day", owner_role="employee"),
            step(title_en="Remove access", anchor="last_day", auto_check="access_removed"),
            step(title_en="Farewell", anchor="last_day", owner_role="manager", required=False),
        ],
    )
    # A task of someone else's process that names the leaver.
    custom = await create(
        world,
        kind="custom",
        steps=[
            step(title_en="Mentor session", owner_role="person", owner_employee_id=str(staff)),
            step(title_en="Optional follow-up", required=False),
        ],
    )
    mentored = await run(world, "owner", START_SQL, start_body(outsider, "custom", custom))
    await ok(world, "owner", await leave_body(world, staff, today))
    offboarding = await only_process(world, staff)
    pid = offboarding["id"]
    await set_task(world, pid, 1, "status='done', completed_at=now()")
    await set_last_day(world, staff, today - timedelta(days=1))
    assert await finish(world, staff) == "moved_finished"

    done = await only_process(world, staff)
    assert (done["status"], done["closed_at"] is not None) == ("completed", True)
    assert [(t["status"], t["note"]) for t in await tasks(world, pid)] == [
        ("done", None),
        ("skipped", "person left"),
        ("done", None),  # access_removed, checked by the system
        ("skipped", None),  # optional, closed by the completion
    ]
    access = (await tasks(world, pid))[2]
    assert access["completed_by"] is None and access["completed_at"] is not None
    logged = await events(world, pid)
    assert event(logged, "task.completed", 3)["detail"] == {"auto_check": "access_removed"}
    assert event(logged, "task.skipped", 2)["detail"] == {"reason": "person_left"}
    assert event(logged, "task.skipped", 4)["detail"] == {"reason": "process_completed"}
    # The worker's finish has no HR user: every one of these is the system's.
    finished_actions = ("task.completed", "task.skipped", "process.completed")
    assert all(e["actor"] is None for e in logged if e["action"] in finished_actions)
    # The other process: the leaver's step is skipped and, nothing required left, it completes.
    other_id = UUID(mentored["process_id"])
    assert [(t["status"], t["note"]) for t in await tasks(world, other_id)] == [
        ("skipped", "person left"),
        ("skipped", None),
    ]
    assert (await only_process(world, outsider))["status"] == "completed"

    # A required HR task still open keeps the offboarding active; the leaver's own task is skipped.
    await ok(world, "owner", await leave_body(world, manager, today))
    second = (await only_process(world, manager))["id"]
    await set_last_day(world, manager, today - timedelta(days=1))
    assert await finish(world, manager) == "moved_finished"
    assert (await only_process(world, manager))["status"] == "active"
    assert [t["status"] for t in await tasks(world, second)] == [
        "todo",
        "skipped",
        "done",
        "todo",
    ]


async def test_a_leaving_managers_reports_tasks_move_to_the_heir(world: HrWorld) -> None:
    """The report move of hr_finish_leaver re-resolves the reports' manager tasks (trigger step 5),
    as the system."""
    today = await db_today(world)
    manager, heir = world.employees["manager"], world.employees["outsider"]
    await create(world, steps=[step(title_en="Meet the team", owner_role="manager")])
    report = await add_person(world, manager_id=str(manager))
    pid = (await only_process(world, report))["id"]
    assert (await tasks(world, pid))[0]["assignee_employee_id"] == manager
    await ok(world, "owner", await leave_body(world, manager, today, new_manager=heir))
    # On the last day the reports move while the manager is still active: the move itself
    # re-resolves the task.
    assert await finish(world, manager) == "moved"
    assert (await person_row(world, manager))["is_active"] is True
    assert (await person_row(world, report))["manager_id"] == heir
    [task] = await tasks(world, pid)
    assert (task["assignee_employee_id"], task["status"]) == (heir, "todo")
    moved = event(await events(world, pid), "task.reassigned", 1)
    assert moved["detail"] == {"from": str(manager), "to": str(heir)}
    assert moved["actor"] is None
    # The day after, the manager finishes; the task stays with the heir.
    await set_last_day(world, manager, today - timedelta(days=1))
    assert await finish(world, manager) == "finished"
    assert (await tasks(world, pid))[0]["assignee_employee_id"] == heir
    assert len([e for e in await events(world, pid) if e["action"] == "task.reassigned"]) == 1


async def test_a_leaving_managers_task_in_a_former_reports_offboarding_goes_to_the_pool(
    world: HrWorld,
) -> None:
    """An inactive former report is not moved by hr_finish_leaver; its still-active offboarding's
    manager task, assigned to the leaving manager, goes to the HR pool when the manager leaves."""
    today = await db_today(world)
    manager = world.employees["manager"]
    await create(
        world,
        kind="offboarding",
        steps=[
            step(title_en="Collect the laptop", anchor="last_day"),
            step(title_en="Handover talk", anchor="last_day", owner_role="manager"),
        ],
    )
    former = await add_person(world, manager_id=str(manager))
    await ok(world, "owner", await leave_body(world, former, today))
    pid = (await only_process(world, former))["id"]
    await set_last_day(world, former, today - timedelta(days=1))
    await finish(world, former)
    assert (await person_row(world, former))["is_active"] is False
    assert (await only_process(world, former))["status"] == "active"  # HR task still open
    assert (await tasks(world, pid))[1]["assignee_employee_id"] == manager

    await ok(world, "owner", await leave_body(world, manager, today))
    await set_last_day(world, manager, today - timedelta(days=1))
    assert await finish(world, manager) == "moved_finished"
    assert (await person_row(world, former))["manager_id"] == manager  # not moved: inactive
    handover = (await tasks(world, pid))[1]
    assert (handover["assignee_employee_id"], handover["status"]) == (None, "todo")
    moved = event(await events(world, pid), "task.reassigned", 2)
    assert moved["detail"] == {
        "from": str(manager),
        "to": None,
        "fallback": "hr_pool",
        "reason": "manager_inactive",
    }
    assert moved["actor"] is None


async def test_finishing_the_leaver_returns_hand_assigned_hr_tasks_to_the_pool(
    world: HrWorld,
) -> None:
    today = await db_today(world)
    staff = world.employees["staff"]
    await create(world, steps=[step(title_en="Payroll set-up"), step(title_en="Order a desk")])
    person = await add_person(world)
    pid = (await only_process(world, person))["id"]
    # HR handed the payroll task to the staff member (Task 6 writes this).
    await world.q(
        "UPDATE hr_process_tasks SET assignee_employee_id=%s"
        " WHERE client_id=%s AND process_id=%s AND ordinal=1",
        (staff, world.tenant, pid),
    )
    before = await tasks(world, pid)
    await ok(world, "owner", await leave_body(world, staff, today))
    await set_last_day(world, staff, today - timedelta(days=1))
    assert await finish(world, staff) == "moved_finished"
    after = await tasks(world, pid)
    assert (after[0]["assignee_employee_id"], after[0]["status"]) == (None, "todo")
    assert after[0]["version"] == before[0]["version"] + 1
    assert after[1] == before[1]
    returned = event(await events(world, pid), "task.reassigned", 1)
    assert returned["detail"] == {
        "from": str(staff),
        "to": None,
        "fallback": "hr_pool",
        "reason": "person_left",
    }
    assert returned["actor"] is None
    assert (await only_process(world, person))["status"] == "active"


async def test_a_template_of_another_tenant_never_starts_or_touches_anything(
    world: HrWorld,
) -> None:
    """Tenancy comes from the written row: a person write in this tenant ignores the other
    tenant's matching templates and leaves its tables alone."""
    today = await db_today(world)
    await make_other_tenant_owner(world)
    for kind in ("onboarding", "offboarding"):
        await apply_other(world, create_body(kind=kind))

    async def other_tenant_state() -> list[dict[str, Any]]:
        return await world.q(
            "SELECT (SELECT count(*) FROM hr_processes WHERE client_id=%s) AS processes,"
            " (SELECT count(*) FROM hr_process_tasks WHERE client_id=%s) AS tasks,"
            " (SELECT count(*) FROM hr_process_events WHERE client_id=%s) AS events,"
            " (SELECT jsonb_agg(jsonb_build_array(id, version, is_active) ORDER BY id)"
            "  FROM hr_process_templates WHERE client_id=%s) AS templates",
            (world.other_tenant,) * 4,
        )

    theirs = await other_tenant_state()
    assert theirs[0]["processes"] == 0 and len(theirs[0]["templates"]) == 2
    added = await add_person(world)
    staff = world.employees["staff"]  # the same id exists in the other tenant
    await ok(world, "owner", await leave_body(world, staff, today))
    await set_last_day(world, staff, today - timedelta(days=1))
    await finish(world, staff)
    assert await processes(world, added) == []
    assert await processes(world, staff) == []
    assert await other_tenant_state() == theirs


# ---- Started and cancelled by HR ------------------------------------------------------------


async def test_custom_process_started_by_hr_and_two_custom_allowed(world: HrWorld) -> None:
    staff = world.employees["staff"]
    await make_people_manager(world)
    onboarding = await create(world)
    custom = await create(
        world,
        kind="custom",
        name="Promotion",
        steps=[step(title_en="New contract", anchor="start_date", due_offset_days=5)],
    )
    key = uuid4()
    first = await run(world, "operator", START_SQL, start_body(staff, "custom", custom), key=key)
    assert first == {
        "operation": "process.start",
        "process_id": first["process_id"],
        "employee_id": str(staff),
        "kind": "custom",
        "template_id": str(custom),
        "status": "saved",
        "duplicate": False,
    }
    replay = await run(world, "operator", START_SQL, start_body(staff, "custom", custom), key=key)
    assert replay == {**first, "duplicate": True}
    with pytest.raises(psycopg.errors.CheckViolation, match=r"^hr_idempotency_conflict\b"):
        await run(
            world,
            "operator",
            START_SQL,
            start_body(staff, "custom", custom),
            key=key,
            fingerprint=OTHER_FP,
        )
    second = await run(world, "operator", START_SQL, start_body(staff, "custom", custom))
    found = await processes(world, staff)
    assert [(p["kind"], p["status"], p["started_by"]) for p in found] == [
        ("custom", "active", world.users["operator"]),
        ("custom", "active", world.users["operator"]),
    ]
    assert {str(p["id"]) for p in found} == {first["process_id"], second["process_id"]}
    # The start date of the fixture's staff is not set, so the task waits for a date.
    assert [t["due_on"] for t in await tasks(world, found[0]["id"])] == [None]

    # Refusals, each with nothing written.
    for body, code in (
        (start_body(staff, "custom"), "hr_process_template_required"),
        (start_body(staff, "custom", onboarding), "hr_template_unavailable"),
        (start_body(staff, "custom", uuid4()), "hr_template_unavailable"),
        ({**start_body(staff, "custom"), "template_id": "not-a-uuid"}, "hr_template_unavailable"),
        (start_body(staff, "offboarding"), "hr_person_not_leaving"),
        (start_body(uuid4(), "custom", custom), "hr_employee_unavailable"),
        ({**start_body(staff), "employee_id": "not-a-uuid"}, "hr_employee_unavailable"),
        (start_body(staff, "other", custom), "hr_process_kind_invalid"),
    ):
        with pytest.raises(psycopg.errors.CheckViolation, match=rf"^{code}\b"):
            await run(world, "operator", START_SQL, body)
    with pytest.raises(
        psycopg.errors.InsufficientPrivilege, match=r"^hr_people_manager_required\b"
    ):
        await run(world, "staff", START_SQL, start_body(staff, "custom", custom))
    assert len(await processes(world, staff)) == 2

    # A manual onboarding for an existing person takes the matching template.
    manual = await run(world, "operator", START_SQL, start_body(staff, "onboarding"))
    assert manual["template_id"] == str(onboarding)
    await apply(
        world,
        "owner",
        {"operation": "template.deactivate", "template_id": str(onboarding), "expected_version": 1},
    )
    outsider = world.employees["outsider"]
    with pytest.raises(psycopg.errors.CheckViolation, match=r"^hr_process_no_template\b"):
        await run(world, "operator", START_SQL, start_body(outsider, "onboarding"))
    assert await processes(world, outsider) == []


async def test_cancel_closes_open_tasks(world: HrWorld) -> None:
    staff = world.employees["staff"]
    custom = await create(
        world,
        kind="custom",
        steps=[step(title_en="First"), step(title_en="Second"), step(title_en="Third")],
    )
    started = await run(world, "owner", START_SQL, start_body(staff, "custom", custom))
    pid = UUID(started["process_id"])
    await set_task(world, pid, 1, "status='done', completed_at=now()")
    key = uuid4()
    body = {"process_id": str(pid)}
    receipt = await run(world, "owner", CANCEL_SQL, body, key=key)
    assert receipt == {
        "operation": "process.cancel",
        "process_id": str(pid),
        "status": "saved",
        "duplicate": False,
    }
    process = await only_process(world, staff)
    assert (process["status"], process["closed_at"] is not None) == ("cancelled", True)
    assert [t["status"] for t in await tasks(world, pid)] == ["done", "skipped", "skipped"]
    logged = await events(world, pid)
    assert event(logged, "process.cancelled")["detail"] == {"reason": "cancelled_by_hr"}
    assert event(logged, "process.cancelled")["actor"] == world.users["owner"]
    assert {e["ordinal"] for e in logged if e["action"] == "task.skipped"} == {2, 3}
    assert await run(world, "owner", CANCEL_SQL, body, key=key) == {**receipt, "duplicate": True}
    # The status is the guard: a closed process cannot be cancelled again.
    with pytest.raises(psycopg.errors.CheckViolation, match=r"^hr_process_closed\b"):
        await run(world, "owner", CANCEL_SQL, {**body, "expected_version": 1})
    for missing in (str(uuid4()), "not-a-uuid", None):
        with pytest.raises(psycopg.errors.CheckViolation, match=r"^hr_process_unavailable\b"):
            await run(world, "owner", CANCEL_SQL, {"process_id": missing})
    with pytest.raises(
        psycopg.errors.InsufficientPrivilege, match=r"^hr_people_manager_required\b"
    ):
        await run(world, "staff", CANCEL_SQL, body)
    assert len(await events(world, pid)) == len(logged)


async def test_other_tenant_cannot_start_or_cancel(world: HrWorld) -> None:
    staff, manager = world.employees["staff"], world.employees["manager"]
    custom = await create(world, kind="custom")
    mine = await run(world, "owner", START_SQL, start_body(staff, "custom", custom))
    before = await processes(world, staff)
    # This tenant's HR owner naming another tenant.
    named: list[tuple[LiteralString, dict[str, Any]]] = [
        (START_SQL, start_body(staff, "custom", custom)),
        (CANCEL_SQL, {"process_id": mine["process_id"]}),
        (PROBATION_SQL, {"employee_id": str(staff), "expected_version": 1, "probation_end": None}),
    ]
    for sql, body in named:
        with pytest.raises(
            psycopg.errors.InsufficientPrivilege, match=r"^hr_people_manager_required\b"
        ):
            await run(world, "owner", sql, body, tenant=world.other_tenant)
    # The other tenant's owner naming this tenant's ids from inside the other tenant.
    await make_other_tenant_owner(world)
    theirs = UUID((await apply_other(world, create_body(kind="custom")))["template_id"])
    crossing: list[tuple[LiteralString, dict[str, Any], str]] = [
        (START_SQL, start_body(manager, "custom", theirs), "hr_employee_unavailable"),
        (START_SQL, start_body(staff, "custom", custom), "hr_template_unavailable"),
        (CANCEL_SQL, {"process_id": mine["process_id"]}, "hr_process_unavailable"),
        (
            PROBATION_SQL,
            {"employee_id": str(manager), "expected_version": 1, "probation_end": None},
            "hr_employee_unavailable",
        ),
    ]
    for sql, body, code in crossing:
        with pytest.raises(psycopg.errors.CheckViolation, match=rf"^{code}\b"):
            await run_in_other_tenant(world, sql, body)
    assert await processes(world, staff) == before
    other = await world.q(
        "SELECT count(*) AS n FROM hr_processes WHERE client_id=%s", (world.other_tenant,)
    )
    assert other == [{"n": 0}]


async def apply_other(world: HrWorld, body: dict[str, Any]) -> dict[str, Any]:
    return await run_in_other_tenant(
        world, "SELECT public.hr_template_apply(%s,%s,%s,%s) AS result", body
    )


async def test_concurrent_start_maps_to_a_stable_code(world: HrWorld) -> None:
    """Ledger M7: a start that meets another transaction's new active process of the same kind
    (the one-active index) is refused with a stable code, not a raw unique violation.
    """
    staff = world.employees["staff"]
    template = await create(world)
    async with world.owner_pool.connection() as rival:
        async with rival.transaction():
            await rival.execute(
                "INSERT INTO hr_processes(client_id,employee_id,kind,template_id,template_version)"
                " VALUES (%s,%s,'onboarding',%s,1)",
                (world.tenant, staff, template),
            )
            racing = asyncio.create_task(
                run(world, "owner", START_SQL, start_body(staff, "onboarding"))
            )
            for _ in range(200):
                waiting = await world.q(
                    "SELECT count(*) AS n FROM pg_stat_activity WHERE wait_event_type='Lock'"
                    " AND query LIKE '%%hr_process_start_apply%%'"
                )
                if waiting[0]["n"]:
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("the start never waited for the rival transaction")
    with pytest.raises(psycopg.errors.CheckViolation, match=r"^hr_process_already_active\b"):
        await racing
    assert [p["status"] for p in await processes(world, staff)] == ["active"]


async def test_functions_are_pinned_definers_and_only_the_public_ones_are_granted(
    world: HrWorld,
) -> None:
    for signature in (*PUBLIC_FUNCTIONS, *INTERNAL_FUNCTIONS):
        rows = await world.q(
            "SELECT p.prosecdef, p.proconfig,"
            " has_function_privilege('csa_app', p.oid, 'EXECUTE') AS csa_app,"
            " has_function_privilege('csa_ro', p.oid, 'EXECUTE') AS csa_ro,"
            " has_function_privilege('csa_identity', p.oid, 'EXECUTE') AS csa_identity,"
            " has_function_privilege('csa_hr_maintenance', p.oid, 'EXECUTE') AS maintenance,"
            " has_function_privilege('public', p.oid, 'EXECUTE') AS everyone"
            " FROM pg_proc p WHERE p.oid=%s::regprocedure",
            (f"public.{signature}",),
        )
        [row] = rows
        assert row["prosecdef"] is True, signature
        assert row["proconfig"] == ["search_path=pg_catalog, public, pg_temp"], signature
        assert row["csa_app"] is (signature in PUBLIC_FUNCTIONS), signature
        assert not (row["csa_ro"] or row["csa_identity"] or row["maintenance"]), signature
        assert not row["everyone"], signature
    trigger = await world.q(
        "SELECT tgenabled, pg_get_triggerdef(t.oid) AS definition FROM pg_trigger t"
        " WHERE t.tgrelid='public.hr_employees'::regclass AND t.tgname='hr_process_on_employee'"
    )
    assert len(trigger) == 1 and trigger[0]["tgenabled"] == "O"
    assert "AFTER INSERT OR UPDATE OF" in trigger[0]["definition"]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await as_hr(
            world,
            "owner",
            "SELECT public.hr_process_settle(%s,%s)",
            (world.tenant, uuid4()),
        )
