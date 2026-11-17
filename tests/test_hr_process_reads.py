"""Migration 0633: reading processes and tasks, and who sees what (spec 5.3) (disposable database).
Reuses the HrWorld fixture of tests/test_hr_persistence.py: processes are started through the 0632
entry points, and every read runs as csa_app inside the caller's HR session, the way the service
will. In the fixture, staff and owner report to manager; outsider reports to nobody; operator has
no employee (people.manage when granted); reader is a read-only member; admin a tenant admin with
no HR authority.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, LiteralString
from uuid import UUID, uuid4

import pytest
from csa.core.tenancy import tx

from tests.conftest import requires_docker
from tests.test_hr_people import make_other_tenant_owner, make_people_manager, ok, person_row
from tests.test_hr_people_records import (
    db_today,
    finish_now,
    leave_body,
    versioned,
)
from tests.test_hr_persistence import HrWorld, hr_database, world
from tests.test_hr_process_instances import (
    CANCEL_SQL,
    PROBATION_SQL,
    START_SQL,
    add_person,
    insert_employee,
    run,
    start_body,
)
from tests.test_hr_process_templates import create, deactivate, department, step

__all__ = ["hr_database", "world"]
pytestmark = [requires_docker, pytest.mark.asyncio]

ROWS_SQL: LiteralString = "SELECT * FROM public.hr_process_rows(%s,%s,%s,%s,%s,%s,%s,%s)"
GET_SQL: LiteralString = "SELECT public.hr_process_get(%s,%s) AS process"
MINE_SQL: LiteralString = "SELECT * FROM public.hr_task_rows_mine(%s,%s,%s,%s)"
PERSON_SQL: LiteralString = "SELECT public.hr_person_processes(%s,%s) AS person"
PUBLIC_FUNCTIONS = (
    "hr_process_rows(uuid,text,text,boolean,uuid,uuid,integer,uuid)",
    "hr_process_get(uuid,uuid)",
    "hr_task_rows_mine(uuid,text,integer,uuid)",
    "hr_person_processes(uuid,uuid)",
)
INTERNAL_FUNCTIONS = (
    "hr_process_caller(uuid)",
    "hr_process_summary(uuid,uuid,uuid)",
)
NAMES = {
    "staff": "Synthetic Staff",
    "manager": "Synthetic Manager",
    "owner": "Synthetic Owner",
    "outsider": "Synthetic Outsider",
}
ONBOARDING_STEPS = [
    step(title_en="Prepare the laptop"),
    step(title_en="Introduce the team", owner_role="manager"),
    step(title_en="Submit bank details", owner_role="employee"),
]
# What a manager may see of a task that is not theirs (spec 5.3).
MANAGER_TASK_KEYS = {
    "task_id",
    "ordinal",
    "title_en",
    "title_ms",
    "owner_role",
    "assignee_name",
    "due_on",
    "status",
    "required",
    "overdue",
    "mine",
}
HIDDEN_FROM_MANAGER = {
    "note",
    "completed_by",
    "completed_at",
    "approved_by",
    "approved_at",
    "description_en",
    "description_ms",
    "assignee_employee_id",
}


@pytest.fixture(autouse=True)
async def named(world: HrWorld) -> None:
    """Distinct display names, so a row says whose it is."""
    for name, display in NAMES.items():
        await world.q(
            "UPDATE hr_employees SET display_name=%s WHERE client_id=%s AND id=%s",
            (display, world.tenant, world.employees[name]),
        )


async def read_as(
    world: HrWorld, who: str, sql: LiteralString, params: tuple[object, ...]
) -> list[dict[str, Any]]:
    """Run `sql` as csa_app inside `who`'s read session (no CSRF proof, as a GET has), which a
    read-only member may hold too."""
    proof = world.proof(who).model_copy(update={"csrf_hash": None})
    async with tx(world.service.pool, str(world.tenant)) as conn:
        await world.service._session(conn, proof)
        cursor = await conn.execute(sql, params)
        return await cursor.fetchall()


async def rows(
    world: HrWorld,
    who: str,
    *,
    kind: str | None = None,
    status: str | None = None,
    overdue: bool | None = None,
    department_id: UUID | None = None,
    owner: UUID | None = None,
    limit: int = 200,
    after: UUID | None = None,
    tenant: UUID | None = None,
) -> list[dict[str, Any]]:
    params = (tenant or world.tenant, kind, status, overdue, department_id, owner, limit, after)
    return await read_as(world, who, ROWS_SQL, params)


async def ids(world: HrWorld, who: str, **filters: Any) -> list[UUID]:
    return [r["process_id"] for r in await rows(world, who, **filters)]


async def get(
    world: HrWorld, who: str, process: UUID, tenant: UUID | None = None
) -> dict[str, Any] | None:
    found = await read_as(world, who, GET_SQL, (tenant or world.tenant, process))
    return found[0]["process"]


async def mine(
    world: HrWorld,
    who: str,
    *,
    status: str | None = None,
    limit: int = 200,
    after: UUID | None = None,
    tenant: UUID | None = None,
) -> list[dict[str, Any]]:
    return await read_as(world, who, MINE_SQL, (tenant or world.tenant, status, limit, after))


async def titles(world: HrWorld, who: str, **filters: Any) -> list[str]:
    return [r["title_en"] for r in await mine(world, who, **filters)]


async def person(
    world: HrWorld, who: str, employee: UUID, tenant: UUID | None = None
) -> dict[str, Any] | None:
    found = await read_as(world, who, PERSON_SQL, (tenant or world.tenant, employee))
    return found[0]["person"]


async def in_other_tenant(
    world: HrWorld, sql: LiteralString, params: tuple[object, ...], who: str = "owner"
) -> list[dict[str, Any]]:
    """`who` reads in the other tenant (the owner is its HR owner after make_other_tenant_owner)."""
    async with tx(world.service.pool, str(world.other_tenant)) as conn:
        await conn.execute(
            "SELECT set_config('app.hr_user_id', %s, true)", (str(world.users[who]),)
        )
        cursor = await conn.execute(sql, params)
        return await cursor.fetchall()


async def start(
    world: HrWorld, employee: UUID, kind: str = "onboarding", template: UUID | None = None
) -> UUID:
    result = await run(world, "owner", START_SQL, start_body(employee, kind, template))
    return UUID(result["process_id"])


async def task_id(world: HrWorld, process: UUID, ordinal: int) -> UUID:
    found = await world.q(
        "SELECT id FROM hr_process_tasks WHERE client_id=%s AND process_id=%s AND ordinal=%s",
        (world.tenant, process, ordinal),
    )
    return found[0]["id"]


async def set_task(world: HrWorld, process: UUID, ordinal: int, **values: Any) -> None:
    """A task change the task actions (Task 6) will make; written as the owner here."""
    for column, value in values.items():
        assert column in {"due_on", "status", "note", "completed_by", "completed_at"}
        await world.q(
            f"UPDATE hr_process_tasks SET {column}=%s"  # type: ignore[arg-type]
            " WHERE client_id=%s AND process_id=%s AND ordinal=%s",
            (value, world.tenant, process, ordinal),
        )


async def onboarding_template(world: HrWorld) -> UUID:
    return await create(world, name="Standard onboarding", steps=ONBOARDING_STEPS)


# ---- HR team ----------------------------------------------------------------------------------


async def test_hr_team_sees_all_and_filters_work(world: HrWorld) -> None:
    staff, manager, owner, outsider = (
        world.employees[n] for n in ("staff", "manager", "owner", "outsider")
    )
    today = await db_today(world)
    ops = await department(world, "Operations")
    await world.q(
        "UPDATE hr_employees SET department_id=%s WHERE client_id=%s AND id=%s",
        (ops, world.tenant, staff),
    )
    await onboarding_template(world)
    custom = await create(
        world,
        kind="custom",
        name="Mentoring",
        steps=[step(title_en="Pick a mentor"), step(title_en="First check-in")],
    )
    staff_onboarding = await start(world, staff)
    outsider_custom = await start(world, outsider, "custom", custom)
    owner_onboarding = await start(world, owner)
    await run(world, "owner", CANCEL_SQL, {"process_id": str(owner_onboarding)})
    # Overdue: an open task due before today (Kuala Lumpur). A done task, a task of a cancelled
    # process and a task due later are not.
    await set_task(world, staff_onboarding, 3, due_on=today - timedelta(days=1))
    await set_task(world, staff_onboarding, 1, due_on=today + timedelta(days=4))
    await set_task(world, outsider_custom, 1, due_on=today - timedelta(days=3), status="done")
    await set_task(world, outsider_custom, 2, due_on=today + timedelta(days=5))
    await set_task(world, owner_onboarding, 1, due_on=today - timedelta(days=2))

    newest_first = [owner_onboarding, outsider_custom, staff_onboarding]
    assert await ids(world, "owner") == newest_first
    assert await ids(world, "owner", kind="onboarding") == [owner_onboarding, staff_onboarding]
    assert await ids(world, "owner", kind="custom") == [outsider_custom]
    assert await ids(world, "owner", status="active") == [outsider_custom, staff_onboarding]
    assert await ids(world, "owner", status="cancelled") == [owner_onboarding]
    assert await ids(world, "owner", overdue=True) == [staff_onboarding]
    assert await ids(world, "owner", overdue=False) == newest_first
    assert await ids(world, "owner", department_id=ops) == [staff_onboarding]
    assert await ids(world, "owner", department_id=uuid4()) == []
    # Owner: a person with a task in the process.
    assert await ids(world, "owner", owner=manager) == [owner_onboarding, staff_onboarding]
    assert await ids(world, "owner", owner=staff) == [staff_onboarding]
    assert await ids(world, "owner", owner=outsider) == []
    assert await ids(world, "owner", kind="onboarding", status="active", overdue=True) == [
        staff_onboarding
    ]
    # Keyset paging, newest first.
    assert await ids(world, "owner", limit=2) == [owner_onboarding, outsider_custom]
    assert await ids(world, "owner", limit=2, after=outsider_custom) == [staff_onboarding]
    assert await ids(world, "owner", limit=2, after=staff_onboarding) == []
    assert await ids(world, "owner", after=uuid4()) == []

    [row] = await rows(world, "owner", department_id=ops)
    assert {k: row[k] for k in row if k not in {"started_at"}} == {
        "process_id": staff_onboarding,
        "employee_id": staff,
        "employee_name": "Synthetic Staff",
        "department_id": ops,
        "kind": "onboarding",
        "name": "Standard onboarding",
        "status": "active",
        "closed_at": None,
        "tasks_total": 3,
        "tasks_done": 0,
        "next_due_on": today - timedelta(days=1),
        "overdue": True,
    }
    [cancelled] = await rows(world, "owner", status="cancelled")
    assert (cancelled["tasks_done"], cancelled["next_due_on"], cancelled["overdue"]) == (
        3,
        None,
        False,
    )
    # A people.manage holder sees what the owner sees; before the grant, nothing.
    assert await ids(world, "operator") == []
    await make_people_manager(world)
    assert await ids(world, "operator") == newest_first
    # A person who has left stays in the history for the HR team.
    leaver = await insert_employee(world)
    leaver_process = await start(world, leaver, "custom", custom)
    await world.q(
        "UPDATE hr_employees SET is_active=false WHERE client_id=%s AND id=%s",
        (world.tenant, leaver),
    )
    assert (await ids(world, "owner"))[0] == leaver_process
    full = await get(world, "owner", leaver_process)
    assert full is not None and full["view"] == "hr"


async def test_hr_team_gets_the_whole_process(world: HrWorld) -> None:
    staff = world.employees["staff"]
    template = await onboarding_template(world)
    process = await start(world, staff)
    await set_task(
        world,
        process,
        1,
        note="Synthetic private note",
        status="done",
        completed_by=world.users["owner"],
    )
    found = await get(world, "owner", process)
    assert found is not None
    assert found["view"] == "hr"
    assert found["process"]["process_id"] == str(process)
    assert found["process"]["employee_id"] == str(staff)
    assert found["process"]["template_id"] == str(template)
    assert found["process"]["anchor_dates"] == {
        "start_date": None,
        "last_day": None,
        "probation_end": None,
    }
    assert found["process"]["started_by"] == str(world.users["owner"])
    assert [t["title_en"] for t in found["tasks"]] == [s["title_en"] for s in ONBOARDING_STEPS]
    first = found["tasks"][0]
    assert first["note"] == "Synthetic private note"
    assert first["completed_by"] == str(world.users["owner"])
    assert first["assignee_employee_id"] is None
    assert found["tasks"][1]["assignee_name"] == "Synthetic Manager"
    assert [e["action"] for e in found["events"]].count("task.created") == 3
    assert {"actor", "detail", "at", "task_id", "event_id"} <= set(found["events"][0])
    assert await get(world, "owner", uuid4()) is None


# ---- Manager ----------------------------------------------------------------------------------


async def test_manager_sees_direct_reports_processes_without_notes(world: HrWorld) -> None:
    staff, manager, outsider = (world.employees[n] for n in ("staff", "manager", "outsider"))
    await onboarding_template(world)
    report = await start(world, staff)
    other = await start(world, outsider)
    await set_task(
        world,
        report,
        1,
        note="Synthetic private note",
        status="done",
        completed_by=world.users["owner"],
    )
    await set_task(world, report, 2, note="Synthetic manager note")

    assert await ids(world, "manager") == [report]
    assert await ids(world, "manager", owner=manager) == [report]
    seen = await get(world, "manager", report)
    assert seen is not None
    assert seen["view"] == "manager"
    assert "events" not in seen
    assert set(seen["process"]) == {
        "process_id",
        "employee_id",
        "employee_name",
        "kind",
        "name",
        "status",
        "started_at",
        "closed_at",
        "tasks_total",
        "tasks_done",
    }
    hr_task, own_task, staff_task = seen["tasks"]
    for task in (hr_task, staff_task):
        assert set(task) == MANAGER_TASK_KEYS
        assert not HIDDEN_FROM_MANAGER & set(task)
        assert task["mine"] is False
    assert (hr_task["title_en"], hr_task["status"], hr_task["assignee_name"]) == (
        "Prepare the laptop",
        "done",
        None,
    )
    assert (staff_task["owner_role"], staff_task["assignee_name"]) == (
        "employee",
        "Synthetic Staff",
    )
    # The manager's own task carries what they need to do it.
    assert own_task["mine"] is True
    assert own_task["note"] == "Synthetic manager note"
    assert not {"completed_by", "approved_by", "assignee_employee_id"} & set(own_task)
    # Not a direct report.
    assert await get(world, "manager", other) is None
    # The person's processes: no anchor dates, no template flags.
    profile = await person(world, "manager", staff)
    assert profile is not None
    assert profile["view"] == "manager"
    assert [p["process_id"] for p in profile["processes"]] == [str(report)]
    assert "anchor_dates" not in profile and "no_matching_template" not in profile
    assert await person(world, "manager", outsider) is None
    # A manager who has left sees nothing.
    await world.q(
        "UPDATE hr_employees SET is_active=false WHERE client_id=%s AND id=%s",
        (world.tenant, manager),
    )
    assert await ids(world, "manager") == []
    assert await get(world, "manager", report) is None
    assert await person(world, "manager", staff) is None
    assert await mine(world, "manager") == []


# ---- Assignee ---------------------------------------------------------------------------------


async def test_assignee_sees_only_own_tasks(world: HrWorld) -> None:
    staff, outsider = world.employees["staff"], world.employees["outsider"]
    today = await db_today(world)
    await onboarding_template(world)
    mentoring = await create(
        world,
        kind="custom",
        name="Mentoring",
        steps=[
            step(
                title_en="Mentor the new hire",
                owner_role="person",
                owner_employee_id=str(staff),
                reference_note="Synthetic mentoring guide, section 2",
            ),
            step(title_en="File the mentoring form"),
        ],
    )
    own = await start(world, staff)
    theirs = await start(world, outsider, "custom", mentoring)
    await set_task(world, own, 3, due_on=today + timedelta(days=2), note="Synthetic own note")
    await set_task(world, theirs, 1, due_on=today - timedelta(days=1))

    found = await mine(world, "staff")
    assert [(t["title_en"], t["process_name"], t["employee_name"]) for t in found] == [
        ("Mentor the new hire", "Mentoring", "Synthetic Outsider"),
        ("Submit bank details", "Standard onboarding", "Synthetic Staff"),
    ]
    assert [t["overdue"] for t in found] == [True, False]
    assert [t["pool"] for t in found] == [False, False]
    assert found[1]["note"] == "Synthetic own note"
    assert [t["reference_note"] for t in found] == ["Synthetic mentoring guide, section 2", None]
    assert found[0]["task_id"] == await task_id(world, theirs, 1)
    assert "employee_id" not in found[0]
    # Soonest due first, keyset paged.
    assert await titles(world, "staff", limit=1) == ["Mentor the new hire"]
    assert await titles(world, "staff", limit=1, after=found[0]["task_id"]) == [
        "Submit bank details"
    ]
    assert await titles(world, "staff", limit=1, after=found[1]["task_id"]) == []
    # Open tasks by default; one status when asked.
    assert await titles(world, "staff", status="done") == []
    await set_task(world, theirs, 1, status="done")
    assert await titles(world, "staff") == ["Submit bank details"]
    assert await titles(world, "staff", status="done") == ["Mentor the new hire"]

    seen = await get(world, "staff", theirs)
    assert seen is not None
    assert seen["view"] == "assignee"
    assert "events" not in seen
    assert seen["process"] == {
        "process_id": str(theirs),
        "kind": "custom",
        "name": "Mentoring",
        "status": "active",
        "employee_name": "Synthetic Outsider",
    }
    assert [t["title_en"] for t in seen["tasks"]] == ["Mentor the new hire"]
    assert seen["tasks"][0]["mine"] is True
    assert seen["tasks"][0]["reference_note"] == "Synthetic mentoring guide, section 2"
    assert not {"completed_by", "approved_by", "assignee_employee_id"} & set(seen["tasks"][0])
    # Their own onboarding is the person's view (test_person_sees_own_progress_and_own_tasks_only).
    own_view = await get(world, "staff", own)
    assert own_view is not None and own_view["view"] == "self"
    assert [t["title_en"] for t in own_view["tasks"]] == ["Submit bank details"]
    # An assignee lists only their own onboarding, not the processes they work in, and someone
    # with no task in a process sees none of it.
    assert await ids(world, "staff") == [own]
    assert await mine(world, "outsider") == []
    assert await get(world, "outsider", own) is None


async def test_pool_tasks_listed_for_hr_team_only(world: HrWorld) -> None:
    staff, outsider = world.employees["staff"], world.employees["outsider"]
    await onboarding_template(world)
    process = await start(world, staff)
    # outsider has no manager: the manager task falls to the HR pool too.
    managerless = await start(world, outsider)
    pool = {await task_id(world, process, 1), await task_id(world, managerless, 1)}
    fallback = await task_id(world, managerless, 2)

    owned = await mine(world, "owner")
    assert {t["task_id"] for t in owned} == pool | {fallback}
    assert all(t["pool"] for t in owned)
    assert await mine(world, "operator") == []
    await make_people_manager(world)
    assert {t["task_id"] for t in await mine(world, "operator")} == pool | {fallback}
    assert await titles(world, "staff") == ["Submit bank details"]
    assert await titles(world, "manager") == ["Introduce the team"]
    assert await titles(world, "outsider") == ["Submit bank details"]
    # A done pool task leaves the open list.
    await set_task(world, process, 1, status="done")
    done = await task_id(world, process, 1)
    assert done not in {t["task_id"] for t in await mine(world, "owner")}


# ---- Nobody else ------------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["outsider", "reader", "admin"])
async def test_stranger_and_read_only_see_nothing(world: HrWorld, who: str) -> None:
    staff = world.employees["staff"]
    await onboarding_template(world)
    process = await start(world, staff)
    # The HR team sees it, so an empty answer below is the filter, not an empty table.
    assert await ids(world, "owner") == [process]
    assert len(await mine(world, "owner")) == 1
    assert await ids(world, who) == []
    assert await get(world, who, process) is None
    assert await mine(world, who) == []
    assert await person(world, who, staff) is None


async def test_other_tenant_sees_nothing(world: HrWorld) -> None:
    staff = world.employees["staff"]
    await onboarding_template(world)
    process = await start(world, staff)
    # A foreign p_client in the owner's own session: nothing.
    other = world.other_tenant
    assert await rows(world, "owner", tenant=other) == []
    assert await get(world, "owner", process, tenant=other) is None
    assert await mine(world, "owner", tenant=other) == []
    assert await person(world, "owner", staff, tenant=other) is None
    # The other tenant's HR owner, in its own tenant, with this tenant's ids.
    await make_other_tenant_owner(world)
    nothing = (other, None, None, None, None, None, 200, None)
    assert await in_other_tenant(world, ROWS_SQL, nothing) == []
    [found] = await in_other_tenant(world, GET_SQL, (other, process))
    assert found["process"] is None
    assert await in_other_tenant(world, MINE_SQL, (other, None, 200, None)) == []
    # The same employee id is a distinct person there, with no processes.
    [theirs] = await in_other_tenant(world, PERSON_SQL, (other, staff))
    assert theirs["person"] is not None
    assert theirs["person"]["processes"] == []
    assert theirs["person"]["no_matching_template"] == {"onboarding": True, "offboarding": False}


async def test_a_link_in_another_tenant_is_never_used_from_this_session(world: HrWorld) -> None:
    """staff is also a member of the other tenant, linked there to an employee with a task."""
    other, there = world.other_tenant, world.employees["staff"]
    await world.q(
        "INSERT INTO console_memberships(user_id,client_id,role) VALUES (%s,%s,'tenant_staff')",
        (world.users["staff"], other),
    )
    await world.q(
        "INSERT INTO hr_employee_accounts(client_id,user_id,employee_id) VALUES (%s,%s,%s)",
        (other, world.users["staff"], there),
    )
    [created] = await world.q(
        "INSERT INTO hr_processes(client_id,employee_id,kind) VALUES (%s,%s,'onboarding')"
        " RETURNING id",
        (other, there),
    )
    theirs = created["id"]
    await world.q(
        "INSERT INTO hr_process_tasks(client_id,process_id,ordinal,title_en,owner_role,"
        "assignee_employee_id,anchor,due_offset_days)"
        " VALUES (%s,%s,1,'Synthetic task there','employee',%s,'start_date',0)",
        (other, theirs, there),
    )
    # In the other tenant itself, the link is used: the process and the task are theirs.
    nothing = (other, None, None, None, None, None, 200, None)
    assert len(await in_other_tenant(world, MINE_SQL, (other, None, 200, None), "staff")) == 1
    assert len(await in_other_tenant(world, ROWS_SQL, nothing, "staff")) == 1
    # From this tenant's session with the other tenant's id: nothing.
    assert await mine(world, "staff", tenant=other) == []
    assert await rows(world, "staff", tenant=other) == []
    assert await get(world, "staff", theirs, tenant=other) is None
    assert await person(world, "staff", there, tenant=other) is None


# ---- The person's processes -------------------------------------------------------------------


async def test_person_processes_report_no_matching_template(world: HrWorld) -> None:
    staff = world.employees["staff"]
    ops = await department(world, "Operations")
    profile = await person(world, "owner", staff)
    assert profile == {
        "view": "hr",
        "employee_id": str(staff),
        "anchor_dates": {"start_date": None, "last_day": None, "probation_end": None},
        "processes": [],
        # Offboarding counts only for a person with a last day.
        "no_matching_template": {"onboarding": True, "offboarding": False},
    }
    version = (await person_row(world, staff))["version"]
    await run(
        world,
        "owner",
        PROBATION_SQL,
        {"employee_id": str(staff), "expected_version": version, "probation_end": "2027-03-01"},
    )
    # A template for another department does not match.
    await create(world, kind="offboarding", rules={"department_id": str(ops)})
    template = await onboarding_template(world)
    profile = await person(world, "owner", staff)
    assert profile is not None
    assert profile["anchor_dates"]["probation_end"] == "2027-03-01"
    assert profile["no_matching_template"] == {"onboarding": False, "offboarding": False}
    last_day = (await db_today(world)) + timedelta(days=30)
    await ok(world, "owner", await leave_body(world, staff, last_day))
    profile = await person(world, "owner", staff)
    assert profile is not None
    assert profile["anchor_dates"]["last_day"] == last_day.isoformat()
    assert profile["no_matching_template"] == {"onboarding": False, "offboarding": True}
    process = await start(world, staff)
    await deactivate(world, template, 1)
    profile = await person(world, "owner", staff)
    assert profile is not None
    # A process of the kind exists, so nothing is missing even with no template left.
    assert profile["no_matching_template"] == {"onboarding": False, "offboarding": True}
    [summary] = profile["processes"]
    assert summary == {
        "process_id": str(process),
        "kind": "onboarding",
        "name": "Standard onboarding",
        "status": "active",
        "started_at": summary["started_at"],
        "closed_at": None,
        "tasks_total": 3,
        "tasks_done": 0,
        "next_task": {"title_en": "Prepare the laptop", "title_ms": None, "due_on": None},
    }
    assert await person(world, "owner", uuid4()) is None
    await make_people_manager(world)
    assert await person(world, "operator", staff) == profile


async def test_rehire_without_a_matching_template_is_reported(world: HrWorld) -> None:
    today = await db_today(world)
    onboarding = await onboarding_template(world)
    offboarding = await create(
        world,
        kind="offboarding",
        name="Standard offboarding",
        steps=[step(title_en="Return the laptop", anchor="last_day")],
    )
    hired = await add_person(world)

    async def flags() -> dict[str, bool]:
        profile = await person(world, "owner", hired)
        assert profile is not None
        return profile["no_matching_template"]

    assert await flags() == {"onboarding": False, "offboarding": False}
    await finish_now(world, hired)
    await deactivate(world, onboarding, 1)
    await deactivate(world, offboarding, 1)
    await ok(world, "owner", await versioned(world, "person.rehire", hired, start_date=str(today)))
    # The first employment's onboarding and offboarding stay in the history, but do not count for
    # the new one: no template matches now, and the read says so.
    profile = await person(world, "owner", hired)
    assert profile is not None
    assert sorted(p["kind"] for p in profile["processes"]) == ["offboarding", "onboarding"]
    assert profile["no_matching_template"] == {"onboarding": True, "offboarding": False}
    await ok(world, "owner", await leave_body(world, hired, today + timedelta(days=14)))
    assert await flags() == {"onboarding": True, "offboarding": True}


# ---- The person -------------------------------------------------------------------------------


async def test_person_sees_own_progress_and_own_tasks_only(world: HrWorld) -> None:
    staff = world.employees["staff"]
    today = await db_today(world)
    await onboarding_template(world)
    plan = await create(
        world, kind="custom", name="Performance plan", steps=[step(title_en="Review the plan")]
    )
    onboarding = await start(world, staff)
    performance = await start(world, staff, "custom", plan)
    await set_task(
        world,
        onboarding,
        1,
        note="Synthetic private note",
        status="done",
        completed_by=world.users["owner"],
    )
    await set_task(world, onboarding, 2, due_on=today - timedelta(days=1))
    await set_task(world, onboarding, 3, due_on=today + timedelta(days=3))

    # The list: their onboarding, not the custom process about them; dates from their own tasks.
    [row] = await rows(world, "staff")
    assert row["process_id"] == onboarding
    assert (row["tasks_total"], row["tasks_done"]) == (3, 1)
    assert (row["next_due_on"], row["overdue"]) == (today + timedelta(days=3), False)
    assert await ids(world, "staff", overdue=True) == []
    assert await ids(world, "owner", overdue=True) == [onboarding]

    seen = await get(world, "staff", onboarding)
    assert seen is not None
    assert seen["view"] == "self"
    assert "events" not in seen
    assert seen["process"] == {
        "process_id": str(onboarding),
        "kind": "onboarding",
        "name": "Standard onboarding",
        "status": "active",
        "employee_name": "Synthetic Staff",
        "started_at": seen["process"]["started_at"],
        "closed_at": None,
        "required_total": 3,
        "required_done": 1,
        "open_tasks": 2,
    }
    [task] = seen["tasks"]
    assert (task["title_en"], task["mine"]) == ("Submit bank details", True)
    assert not {"completed_by", "approved_by", "assignee_employee_id"} & set(task)
    assert "Synthetic private note" not in str(seen)

    profile = await person(world, "staff", staff)
    assert profile is not None
    assert set(profile) == {"view", "employee_id", "anchor_dates", "processes"}
    assert profile["view"] == "self"
    assert profile["anchor_dates"] == {"start_date": None, "last_day": None, "probation_end": None}
    [summary] = profile["processes"]
    assert summary["process_id"] == str(onboarding)
    assert summary["next_task"] == {
        "title_en": "Submit bank details",
        "title_ms": None,
        "due_on": (today + timedelta(days=3)).isoformat(),
    }

    # A custom process about the person stays hidden from them ...
    assert await get(world, "staff", performance) is None
    assert await ids(world, "staff", kind="custom") == []
    # ... until they own one of its tasks, and then they see only that task.
    await world.q(
        "UPDATE hr_process_tasks SET assignee_employee_id=%s WHERE client_id=%s AND process_id=%s",
        (staff, world.tenant, performance),
    )
    as_assignee = await get(world, "staff", performance)
    assert as_assignee is not None and as_assignee["view"] == "assignee"
    assert await ids(world, "staff") == [onboarding]


async def test_functions_are_pinned_definers_and_only_the_public_ones_are_granted(
    world: HrWorld,
) -> None:
    for signature in (*PUBLIC_FUNCTIONS, *INTERNAL_FUNCTIONS):
        found = await world.q(
            "SELECT p.prosecdef, p.proconfig,"
            " has_function_privilege('csa_app', p.oid, 'EXECUTE') AS csa_app,"
            " has_function_privilege('csa_ro', p.oid, 'EXECUTE') AS csa_ro,"
            " has_function_privilege('csa_identity', p.oid, 'EXECUTE') AS csa_identity,"
            " has_function_privilege('csa_hr_maintenance', p.oid, 'EXECUTE') AS maintenance,"
            " has_function_privilege('public', p.oid, 'EXECUTE') AS everyone"
            " FROM pg_proc p WHERE p.oid=%s::regprocedure",
            (f"public.{signature}",),
        )
        [row] = found
        assert row["prosecdef"] is True, signature
        assert row["proconfig"] == ["search_path=pg_catalog, public, pg_temp"], signature
        assert row["csa_app"] is (signature in PUBLIC_FUNCTIONS), signature
        assert not (row["csa_ro"] or row["csa_identity"] or row["maintenance"]), signature
        assert not row["everyone"], signature
    # csa_app still cannot read the tables directly (ruling R6).
    for table in ("hr_processes", "hr_process_tasks", "hr_process_events"):
        privilege = await world.q(
            "SELECT has_table_privilege('csa_app', %s, 'SELECT') AS can", (f"public.{table}",)
        )
        assert privilege == [{"can": False}], table
