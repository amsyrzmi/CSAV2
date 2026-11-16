"""Migration 0634: task actions and approvals (hr_task_apply), My tasks with the approvals waiting
for the caller, and who gets a leaver's open steps (disposable database). Reuses the HrWorld fixture
of tests/test_hr_persistence.py and the helpers of tests/test_hr_process_instances.py: every action
runs as csa_app inside the caller's HR session, the way the service will. In the fixture, staff and
owner report to manager; outsider reports to nobody; owner is the HR owner; operator has no
employee (people.manage when granted); reader is a read-only member; admin a tenant admin with no
HR authority.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, LiteralString
from uuid import UUID, uuid4

import psycopg
import pytest
from csa.core.tenancy import tx
from psycopg.types.json import Jsonb

from tests.conftest import requires_docker
from tests.test_hr_people import (
    make_other_tenant_owner,
    make_people_manager,
    ok,
    update_body,
)
from tests.test_hr_people_records import db_today, finish, finish_now, leave_body, set_last_day
from tests.test_hr_persistence import HrWorld, hr_database, world
from tests.test_hr_process_instances import (
    CANCEL_SQL,
    START,
    START_SQL,
    add_person,
    event,
    events,
    insert_employee,
    only_process,
    processes,
    run,
    start_body,
)
from tests.test_hr_process_reads import get as get_process
from tests.test_hr_process_reads import mine, read_as
from tests.test_hr_process_templates import FP, OTHER_FP, create, step

__all__ = ["hr_database", "world"]
pytestmark = [requires_docker, pytest.mark.asyncio]

TASK_SQL: LiteralString = "SELECT public.hr_task_apply(%s,%s,%s,%s) AS result"
REFUSED = (psycopg.errors.CheckViolation, psycopg.errors.InsufficientPrivilege)
INTERNAL_FUNCTIONS = (
    "hr_task_check(uuid,uuid,text)",
    "hr_task_approver(uuid,uuid)",
    "hr_task_approval_right(uuid,uuid)",
)
# The columns of 0633's My tasks; 0634 adds needs_my_approval and keeps every one of these.
MINE_COLUMNS = {
    "task_id",
    "process_id",
    "process_kind",
    "process_name",
    "employee_name",
    "ordinal",
    "title_en",
    "title_ms",
    "description_en",
    "description_ms",
    "owner_role",
    "due_on",
    "required",
    "approval_by",
    "auto_check",
    "reference_note",
    "status",
    "note",
    "version",
    "overdue",
    "pool",
}


async def act(
    world: HrWorld,
    who: str,
    operation: str,
    *,
    key: UUID | None = None,
    fingerprint: str = FP,
    tenant: UUID | None = None,
    **body: Any,
) -> dict[str, Any]:
    payload = {"operation": operation, **{k: _json(v) for k, v in body.items()}}
    return await run(world, who, TASK_SQL, payload, key=key, fingerprint=fingerprint, tenant=tenant)


def _json(value: Any) -> Any:
    return str(value) if isinstance(value, (UUID, date)) else value


async def refused(world: HrWorld, code: str, who: str, operation: str, **body: Any) -> None:
    with pytest.raises(REFUSED, match=rf"^{code}\b"):
        await act(world, who, operation, **body)


async def task(world: HrWorld, process: UUID, ordinal: int) -> dict[str, Any]:
    [row] = await world.q(
        "SELECT id, status, assignee_employee_id, assignee_overridden, due_on, due_overridden,"
        " completed_by, completed_at, approved_by, approved_at, note, sent_back_reason, version"
        " FROM hr_process_tasks WHERE client_id=%s AND process_id=%s AND ordinal=%s",
        (world.tenant, process, ordinal),
    )
    return row


async def versioned_task(world: HrWorld, process: UUID, ordinal: int) -> dict[str, Any]:
    """The body naming a task at its current version."""
    row = await task(world, process, ordinal)
    return {"task_id": row["id"], "expected_version": row["version"]}


async def start(world: HrWorld, employee: UUID, kind: str, template: UUID) -> UUID:
    result = await run(world, "owner", START_SQL, start_body(employee, kind, template))
    return UUID(result["process_id"])


async def custom(world: HrWorld, employee: UUID, *steps: dict[str, Any]) -> UUID:
    """A custom process for `employee` with these steps."""
    template = await create(world, kind="custom", name="Synthetic custom", steps=list(steps))
    return await start(world, employee, "custom", template)


async def process_status(world: HrWorld, process: UUID) -> str:
    [row] = await world.q(
        "SELECT status FROM hr_processes WHERE client_id=%s AND id=%s", (world.tenant, process)
    )
    return row["status"]


def last(found: list[dict[str, Any]], action: str, ordinal: int | None = None) -> dict[str, Any]:
    """The latest event of an action (and task)."""
    return [e for e in found if e["action"] == action and e["ordinal"] == ordinal][-1]


# ---- Completing -------------------------------------------------------------------------------


async def test_assignee_completes_own_task_and_hr_completes_for_the_person(
    world: HrWorld,
) -> None:
    staff = world.employees["staff"]
    pid = await custom(
        world,
        staff,
        step(title_en="Fill in details", owner_role="employee"),
        step(title_en="Sign the handbook", owner_role="employee"),
    )
    first = await versioned_task(world, pid, 1)
    # Someone with no part in the task learns nothing; the manager sees it but may not do it.
    await refused(world, "hr_task_unavailable", "outsider", "task.complete", **first)
    await refused(world, "hr_task_not_assignee", "manager", "task.complete", **first)

    receipt = await act(world, "staff", "task.complete", **first, note="Synthetic: done today")
    assert receipt == {
        "operation": "task.complete",
        "task_id": str(first["task_id"]),
        "process_id": str(pid),
        "task_status": "done",
        "version": 2,
        "process_status": "active",
        "status": "saved",
        "duplicate": False,
    }
    done = await task(world, pid, 1)
    assert (done["status"], done["completed_by"], done["note"]) == (
        "done",
        world.users["staff"],
        "Synthetic: done today",
    )
    assert done["completed_at"] is not None
    logged = await events(world, pid)
    completed = event(logged, "task.completed", 1)
    assert completed["actor"] == world.users["staff"]
    assert completed["detail"] == {"awaiting_approval": False, "on_behalf": False}

    # HR completes the person's other task on their behalf: the event names HR, the real actor.
    second = await act(world, "owner", "task.complete", **await versioned_task(world, pid, 2))
    assert (second["task_status"], second["process_status"]) == ("done", "completed")
    assert (await task(world, pid, 2))["completed_by"] == world.users["owner"]
    behalf = event(await events(world, pid), "task.completed", 2)
    assert behalf["actor"] == world.users["owner"]
    assert behalf["detail"] == {"awaiting_approval": False, "on_behalf": True}
    assert event(await events(world, pid), "process.completed")["actor"] is None


async def test_pool_task_any_hr_member_completes(world: HrWorld) -> None:
    staff = world.employees["staff"]
    pid = await custom(world, staff, step(title_en="Payroll set-up"), step(title_en="Order a desk"))
    first = await versioned_task(world, pid, 1)
    assert (await task(world, pid, 1))["assignee_employee_id"] is None  # the HR pool
    # The person is not the assignee of a pool task, and operator is not HR yet.
    await refused(world, "hr_task_unavailable", "staff", "task.complete", **first)
    await refused(world, "hr_task_unavailable", "operator", "task.complete", **first)
    await make_people_manager(world)
    assert (await act(world, "operator", "task.complete", **first))["task_status"] == "done"
    second = await act(world, "owner", "task.complete", **await versioned_task(world, pid, 2))
    assert second["process_status"] == "completed"
    logged = await events(world, pid)
    assert event(logged, "task.completed", 1)["actor"] == world.users["operator"]
    assert event(logged, "task.completed", 2)["actor"] == world.users["owner"]
    assert event(logged, "task.completed", 1)["detail"]["on_behalf"] is False


# ---- Approvals --------------------------------------------------------------------------------


async def test_approval_flow_and_send_back(world: HrWorld) -> None:
    staff = world.employees["staff"]
    pid = await custom(
        world,
        staff,
        step(title_en="Upload the form", owner_role="employee", approval_by="hr"),
        step(title_en="Bank details", owner_role="employee", approval_by="manager"),
    )
    body = await versioned_task(world, pid, 1)
    sent = await act(world, "staff", "task.complete", **body, note="Synthetic: uploaded")
    assert sent["task_status"] == "awaiting_approval"
    assert event(await events(world, pid), "task.completed", 1)["detail"] == {
        "awaiting_approval": True,
        "on_behalf": False,
    }
    waiting = await versioned_task(world, pid, 1)
    await refused(world, "hr_task_awaiting_approval", "staff", "task.complete", **waiting)
    await refused(world, "hr_task_not_approver", "staff", "task.approve", **waiting)
    # An HR approval is not the manager's.
    await refused(world, "hr_task_not_approver", "manager", "task.approve", **waiting)
    await refused(world, "hr_task_reason_invalid", "owner", "task.send_back", **waiting)
    await refused(
        world, "hr_task_reason_invalid", "owner", "task.send_back", **waiting, reason="   "
    )

    back = await act(world, "owner", "task.send_back", **waiting, reason="Synthetic: unsigned")
    assert back["task_status"] == "todo"
    returned = await task(world, pid, 1)
    assert (returned["status"], returned["completed_by"], returned["completed_at"]) == (
        "todo",
        None,
        None,
    )
    assert returned["note"] == "Synthetic: uploaded"  # the remark is not put in the note
    sent_back = event(await events(world, pid), "task.sent_back", 1)
    assert sent_back["detail"] == {"remark": "Synthetic: unsigned"}
    assert sent_back["actor"] == world.users["owner"]
    await refused(
        world,
        "hr_task_not_awaiting_approval",
        "owner",
        "task.approve",
        **await versioned_task(world, pid, 1),
    )

    await act(world, "staff", "task.complete", **await versioned_task(world, pid, 1))
    approved = await act(world, "owner", "task.approve", **await versioned_task(world, pid, 1))
    assert approved["task_status"] == "done"
    row = await task(world, pid, 1)
    assert (row["approved_by"], row["completed_by"]) == (world.users["owner"], world.users["staff"])
    assert row["approved_at"] is not None
    assert last(await events(world, pid), "task.approved", 1)["detail"] == {"approver": "hr"}

    # A manager approval is the person's manager's, not HR's while the manager resolves.
    await act(world, "staff", "task.complete", **await versioned_task(world, pid, 2))
    second = await versioned_task(world, pid, 2)
    await refused(world, "hr_task_not_approver", "owner", "task.approve", **second)
    done = await act(world, "manager", "task.approve", **second)
    assert (done["task_status"], done["process_status"]) == ("done", "completed")
    approval = event(await events(world, pid), "task.approved", 2)
    assert (approval["actor"], approval["detail"]) == (
        world.users["manager"],
        {"approver": "manager"},
    )


async def test_completer_cannot_approve_but_sole_hr_owner_can_with_the_flag(
    world: HrWorld,
) -> None:
    staff = world.employees["staff"]
    pid = await custom(
        world,
        staff,
        step(title_en="Check the contract", approval_by="hr"),
        step(title_en="Register EPF", approval_by="hr"),
        step(title_en="Register SOCSO", approval_by="hr"),
    )
    # The HR owner is the whole HR team: approving their own step is allowed, and flagged.
    await act(world, "owner", "task.complete", **await versioned_task(world, pid, 1))
    alone = await act(world, "owner", "task.approve", **await versioned_task(world, pid, 1))
    assert alone["task_status"] == "done"
    assert event(await events(world, pid), "task.approved", 1)["detail"] == {
        "approver": "hr",
        "approved_without_second_person": True,
    }

    # With a second HR team member, the owner who completed may not approve; the other may.
    await make_people_manager(world)
    await act(world, "owner", "task.complete", **await versioned_task(world, pid, 2))
    own = await versioned_task(world, pid, 2)
    await refused(world, "hr_task_self_approval", "owner", "task.approve", **own)
    await act(world, "operator", "task.approve", **own)
    assert event(await events(world, pid), "task.approved", 2)["detail"] == {"approver": "hr"}

    # A people.manage holder never gets the owner's exception.
    await act(world, "operator", "task.complete", **await versioned_task(world, pid, 3))
    theirs = await versioned_task(world, pid, 3)
    await refused(world, "hr_task_self_approval", "operator", "task.approve", **theirs)
    assert (await act(world, "owner", "task.approve", **theirs))["process_status"] == "completed"


async def test_manager_approval_falls_to_hr_when_no_manager_resolves(world: HrWorld) -> None:
    outsider, manager = world.employees["outsider"], world.employees["manager"]
    unlinked = await insert_employee(world)
    reporting = await insert_employee(world, manager=unlinked)
    approved_step = step(title_en="Bank details", owner_role="employee", approval_by="manager")
    for person, completer in ((outsider, "outsider"), (reporting, "owner")):
        pid = await custom(world, person, approved_step)
        await act(world, completer, "task.complete", **await versioned_task(world, pid, 1))
        body = await versioned_task(world, pid, 1)
        # No manager (outsider), or a manager with no console account: the HR team approves.
        await refused(world, "hr_task_unavailable", "manager", "task.approve", **body)
        if completer == "owner":
            await make_people_manager(world)
            await refused(world, "hr_task_self_approval", "owner", "task.approve", **body)
            await act(world, "operator", "task.approve", **body)
        else:
            await act(world, "owner", "task.approve", **body)
        assert event(await events(world, pid), "task.approved", 1)["detail"] == {"approver": "hr"}
    assert manager not in {outsider, reporting}


# ---- Automatic checks -------------------------------------------------------------------------


async def personal(world: HrWorld, employee: UUID, **fields: str | None) -> None:
    values = {
        "home_address": "Synthetic address",
        "personal_phone": "000",
        "emergency_name": "Synthetic contact",
        "emergency_relationship": "Synthetic",
        "emergency_phone": "000",
        **fields,
    }
    await world.q(
        "INSERT INTO hr_employee_personal(client_id,employee_id,home_address,personal_phone,"
        "emergency_name,emergency_relationship,emergency_phone) VALUES (%s,%s,%s,%s,%s,%s,%s)"
        " ON CONFLICT (client_id,employee_id) DO UPDATE SET home_address=EXCLUDED.home_address,"
        " personal_phone=EXCLUDED.personal_phone, emergency_name=EXCLUDED.emergency_name,"
        " emergency_relationship=EXCLUDED.emergency_relationship,"
        " emergency_phone=EXCLUDED.emergency_phone",
        (
            world.tenant,
            employee,
            values["home_address"],
            values["personal_phone"],
            values["emergency_name"],
            values["emergency_relationship"],
            values["emergency_phone"],
        ),
    )


async def document(world: HrWorld, employee: UUID, kind: str, *, removed: bool = False) -> None:
    owner = world.users["owner"]
    await world.q(
        "INSERT INTO hr_employee_documents(client_id,id,employee_id,kind,file_name,storage_name,"
        "media_type,byte_size,sha256,uploaded_by,removed_at,removed_by,request_key)"
        " VALUES (%s,gen_random_uuid(),%s,%s,'synthetic.pdf',%s,'application/pdf',1,%s,%s,%s,%s,"
        "gen_random_uuid())",
        (
            world.tenant,
            employee,
            kind,
            f"{uuid4()}.pdf",
            "0" * 64,
            owner,
            date(2026, 11, 1) if removed else None,
            owner if removed else None,
        ),
    )


async def test_auto_checks_pass_and_fail(world: HrWorld) -> None:
    staff = world.employees["staff"]
    pid = await custom(
        world,
        staff,
        step(
            title_en="Personal details",
            owner_role="employee",
            auto_check="personal_details_complete",
        ),
        step(title_en="Contract on file", auto_check="document_uploaded:contract"),
        step(title_en="Console account", auto_check="account_linked"),
        step(title_en="Remove access", auto_check="access_removed"),
    )
    await refused(
        world,
        "hr_task_check_not_met",
        "staff",
        "task.complete",
        **await versioned_task(world, pid, 1),
    )
    await personal(world, staff, emergency_phone="  ")
    await refused(
        world,
        "hr_task_check_not_met",
        "staff",
        "task.complete",
        **await versioned_task(world, pid, 1),
    )
    await personal(world, staff)
    await act(world, "staff", "task.complete", **await versioned_task(world, pid, 1))
    assert event(await events(world, pid), "task.completed", 1)["detail"] == {
        "awaiting_approval": False,
        "on_behalf": False,
        "auto_check": "personal_details_complete",
    }

    # A removed contract or a document of another kind does not count.
    await document(world, staff, "contract", removed=True)
    await document(world, staff, "identity")
    contract = await versioned_task(world, pid, 2)
    await refused(world, "hr_task_check_not_met", "owner", "task.complete", **contract)
    await document(world, staff, "contract")
    assert (await act(world, "owner", "task.complete", **contract))["task_status"] == "done"

    # staff is linked to a console account; a person written with no link is not.
    await act(world, "owner", "task.complete", **await versioned_task(world, pid, 3))
    unlinked = await insert_employee(world)
    other = await custom(world, unlinked, step(title_en="Account", auto_check="account_linked"))
    await refused(
        world,
        "hr_task_check_not_met",
        "owner",
        "task.complete",
        **await versioned_task(world, other, 1),
    )

    # access_removed is the leaver finish's; HR may still skip it.
    removal = await versioned_task(world, pid, 4)
    await refused(world, "hr_task_system_only", "owner", "task.complete", **removal)
    skipped = await act(world, "owner", "task.skip", **removal, reason="Synthetic: not needed")
    assert (skipped["task_status"], skipped["process_status"]) == ("skipped", "completed")


# ---- Rescheduling and reassigning -------------------------------------------------------------


async def test_reschedule_and_reassign_set_override_and_survive_a_last_day_change(
    world: HrWorld,
) -> None:
    today = await db_today(world)
    staff, manager, outsider = (world.employees[n] for n in ("staff", "manager", "outsider"))
    await create(
        world,
        kind="offboarding",
        steps=[
            step(title_en="Archive files", anchor="last_day", due_offset_days=-3),
            step(title_en="Handover talk", anchor="last_day", owner_role="manager"),
            step(title_en="Exit survey", anchor="last_day", owner_role="employee"),
        ],
    )
    last_day = today + timedelta(days=20)
    await ok(world, "owner", await leave_body(world, staff, last_day))
    pid = (await only_process(world, staff))["id"]
    moved_to = today + timedelta(days=40)
    rescheduled = await act(
        world, "owner", "task.reschedule", **await versioned_task(world, pid, 1), due_on=moved_to
    )
    assert rescheduled["version"] == 2
    row = await task(world, pid, 1)
    assert (row["due_on"], row["due_overridden"]) == (moved_to, True)
    assert event(await events(world, pid), "task.rescheduled", 1)["detail"] == {
        "old": (last_day - timedelta(days=3)).isoformat(),
        "new": moved_to.isoformat(),
    }
    survey = await versioned_task(world, pid, 3)
    for bad in ("not-a-date", None, 20270101):
        await refused(
            world, "hr_task_due_invalid", "owner", "task.reschedule", **survey, due_on=bad
        )
    await refused(
        world, "hr_people_manager_required", "staff", "task.reschedule", **survey, due_on=today
    )

    handover = await versioned_task(world, pid, 2)
    assert (await task(world, pid, 2))["assignee_employee_id"] == manager
    gone = await insert_employee(world, active=False)
    for bad in (gone, uuid4(), "not-a-uuid"):
        await refused(
            world,
            "hr_task_assignee_unavailable",
            "owner",
            "task.reassign",
            **handover,
            assignee_employee_id=bad,
        )
    await refused(world, "hr_task_assignee_unavailable", "owner", "task.reassign", **handover)
    await refused(
        world,
        "hr_people_manager_required",
        "manager",
        "task.reassign",
        **handover,
        assignee_employee_id=outsider,
    )
    await act(world, "owner", "task.reassign", **handover, assignee_employee_id=outsider)
    row = await task(world, pid, 2)
    assert (row["assignee_employee_id"], row["assignee_overridden"]) == (outsider, True)
    assert last(await events(world, pid), "task.reassigned", 2)["detail"] == {
        "from": str(manager),
        "to": str(outsider),
        "reason": "reassigned_by_hr",
    }

    # A new last day re-dates the tasks HR did not move by hand, and moves nobody.
    later = today + timedelta(days=25)
    await ok(world, "owner", await leave_body(world, staff, later))
    assert (await task(world, pid, 1))["due_on"] == moved_to
    assert (await task(world, pid, 2))["due_on"] == later
    assert (await task(world, pid, 2))["assignee_employee_id"] == outsider
    # Back to the HR pool by hand.
    await act(
        world,
        "owner",
        "task.reassign",
        **await versioned_task(world, pid, 3),
        assignee_employee_id=None,
    )
    assert (await task(world, pid, 3))["assignee_employee_id"] is None


async def test_a_reassigned_manager_task_stays_where_hr_put_it(world: HrWorld) -> None:
    """Carry B2: a manager change re-resolves open manager tasks, except those HR reassigned."""
    staff, manager, outsider = (world.employees[n] for n in ("staff", "manager", "outsider"))
    pid = await custom(
        world,
        staff,
        step(title_en="Set goals", owner_role="manager"),
        step(title_en="Meet the team", owner_role="manager"),
    )
    await act(
        world,
        "owner",
        "task.reassign",
        **await versioned_task(world, pid, 1),
        assignee_employee_id=world.employees["owner"],
    )
    before = await task(world, pid, 1)
    await ok(world, "owner", await update_body(world, staff, manager_id=str(outsider)))
    assert await task(world, pid, 1) == before
    assert (await task(world, pid, 2))["assignee_employee_id"] == outsider
    assert manager != outsider


# ---- Skipping and closing ---------------------------------------------------------------------


async def test_skip_and_close_anyway_are_hr_only_and_settle_the_process(world: HrWorld) -> None:
    staff = world.employees["staff"]
    steps = (
        step(title_en="Fill in details", owner_role="employee"),
        step(title_en="Payroll set-up"),
        step(title_en="Optional lunch", required=False),
    )
    pid = await custom(world, staff, *steps)
    first = await versioned_task(world, pid, 1)
    await refused(world, "hr_people_manager_required", "staff", "task.skip", **first, reason="x")
    await refused(world, "hr_task_reason_invalid", "owner", "task.skip", **first)
    await refused(world, "hr_task_reason_invalid", "owner", "task.skip", **first, reason="x" * 501)
    await act(world, "owner", "task.skip", **first, reason="Synthetic: done on paper")
    assert event(await events(world, pid), "task.skipped", 1)["detail"] == {
        "reason": "skipped_by_hr",
        "remark": "Synthetic: done on paper",
    }
    # Skipping the last required task settles the process; the optional one is closed with it.
    last_required = await act(
        world, "owner", "task.skip", **await versioned_task(world, pid, 2), reason="Synthetic"
    )
    assert last_required["process_status"] == "completed"
    assert (await task(world, pid, 3))["status"] == "skipped"

    other = await custom(world, staff, *steps)
    await act(world, "staff", "task.complete", **await versioned_task(world, other, 1))
    close: dict[str, Any] = {"process_id": other}
    await refused(world, "hr_people_manager_required", "staff", "process.close_anyway", **close)
    await refused(world, "hr_task_reason_invalid", "owner", "process.close_anyway", **close)
    for missing in (uuid4(), "not-a-uuid", None):
        await refused(
            world,
            "hr_process_unavailable",
            "owner",
            "process.close_anyway",
            process_id=missing,
            note="x",
        )
    closed = await act(
        world, "owner", "process.close_anyway", **close, note="Synthetic: handled offline"
    )
    assert closed == {
        "operation": "process.close_anyway",
        "process_id": str(other),
        "process_status": "completed",
        "status": "saved",
        "duplicate": False,
    }
    assert [(await task(world, other, n))["status"] for n in (1, 2, 3)] == [
        "done",
        "skipped",
        "skipped",
    ]
    logged = await events(world, other)
    assert event(logged, "process.completed")["detail"] == {
        "reason": "closed_anyway",
        "remark": "Synthetic: handled offline",
    }
    assert event(logged, "process.completed")["actor"] == world.users["owner"]
    assert event(logged, "task.skipped", 2)["detail"] == {"reason": "process_closed_anyway"}
    await refused(
        world, "hr_process_closed", "owner", "process.close_anyway", **close, note="Again"
    )


async def test_closed_tasks_refuse_actions(world: HrWorld) -> None:
    staff = world.employees["staff"]
    pid = await custom(
        world,
        staff,
        step(title_en="Done one", owner_role="employee"),
        step(title_en="Skipped one"),
        step(title_en="Still open"),
    )
    await act(world, "staff", "task.complete", **await versioned_task(world, pid, 1))
    await act(world, "owner", "task.skip", **await versioned_task(world, pid, 2), reason="x")
    for ordinal in (1, 2):
        body = await versioned_task(world, pid, ordinal)
        for operation, extra in (
            ("task.complete", {}),
            ("task.skip", {"reason": "x"}),
            ("task.approve", {}),
            ("task.send_back", {"reason": "x"}),
            ("task.reschedule", {"due_on": "2027-01-01"}),
            ("task.reassign", {"assignee_employee_id": None}),
        ):
            await refused(world, "hr_task_closed", "owner", operation, **body, **extra)
        await refused(
            world, "hr_task_closed", "owner", "task.note", task_id=body["task_id"], note="x"
        )
    # A task of a cancelled process is closed too.
    await run(world, "owner", CANCEL_SQL, {"process_id": str(pid)})
    await refused(
        world, "hr_task_closed", "owner", "task.complete", **await versioned_task(world, pid, 3)
    )


async def test_stale_version_and_replayed_key(world: HrWorld) -> None:
    staff = world.employees["staff"]
    pid = await custom(world, staff, step(title_en="Fill in details", owner_role="employee"))
    body = await versioned_task(world, pid, 1)
    for stale in (body["expected_version"] + 1, None, "1"):
        await refused(
            world,
            "hr_task_changed",
            "staff",
            "task.complete",
            task_id=body["task_id"],
            expected_version=stale,
        )
    await refused(world, "hr_task_changed", "staff", "task.complete", task_id=body["task_id"])
    await refused(world, "hr_operation_unsupported", "owner", "task.delete", **body)
    for missing in (uuid4(), "not-a-uuid", None):
        await refused(world, "hr_task_unavailable", "owner", "task.complete", task_id=missing)

    key = uuid4()
    first = await act(world, "staff", "task.complete", **body, key=key)
    written = await events(world, pid)
    # The same key and fingerprint return the first receipt, even though the version moved on.
    assert await act(world, "staff", "task.complete", **body, key=key) == {
        **first,
        "duplicate": True,
    }
    assert await events(world, pid) == written
    with pytest.raises(psycopg.errors.CheckViolation, match=r"^hr_idempotency_conflict\b"):
        await act(world, "staff", "task.complete", **body, key=key, fingerprint=OTHER_FP)
    # A note has no version; it may not be sent without a note key or with a non-string note.
    other = await custom(world, staff, step(title_en="Second", owner_role="employee"))
    target = (await task(world, other, 1))["id"]
    await refused(world, "hr_task_note_invalid", "staff", "task.note", task_id=target)
    await refused(world, "hr_task_note_invalid", "staff", "task.note", task_id=target, note=5)
    await refused(
        world, "hr_task_note_invalid", "staff", "task.note", task_id=target, note="x" * 2001
    )
    noted = await act(world, "staff", "task.note", task_id=target, note="  Synthetic note  ")
    assert noted["version"] == 2
    assert (await task(world, other, 1))["note"] == "Synthetic note"
    await act(world, "staff", "task.note", task_id=target, note="")
    assert (await task(world, other, 1))["note"] is None
    assert [e["action"] for e in await events(world, other)].count("task.noted") == 2


async def test_other_tenant_and_stranger_refused(world: HrWorld) -> None:
    staff = world.employees["staff"]
    pid = await custom(world, staff, step(title_en="Fill in details", owner_role="employee"))
    body = await versioned_task(world, pid, 1)
    for who in ("outsider", "admin", "operator"):
        await refused(world, "hr_task_unavailable", who, "task.complete", **body)
        await refused(
            world, "hr_task_unavailable", who, "task.note", task_id=body["task_id"], note="x"
        )
    # A read-only member holds only a read session (no CSRF proof); the function refuses there too.
    payload = {"operation": "task.complete", **{k: _json(v) for k, v in body.items()}}
    with pytest.raises(REFUSED, match=r"^hr_task_unavailable\b"):
        await read_as(world, "reader", TASK_SQL, (world.tenant, uuid4(), FP, Jsonb(payload)))
    # A foreign p_client in the owner's own session.
    with pytest.raises(REFUSED, match=r"^hr_task_unavailable\b"):
        await act(world, "owner", "task.complete", **body, tenant=world.other_tenant)
    with pytest.raises(REFUSED, match=r"^hr_task_unavailable\b"):
        await act(
            world,
            "owner",
            "process.close_anyway",
            process_id=pid,
            note="x",
            tenant=world.other_tenant,
        )
    # The other tenant's HR owner, in its own tenant, with this tenant's ids.
    await make_other_tenant_owner(world)
    for operation, extra in (
        ("task.complete", body),
        ("task.skip", {**body, "reason": "x"}),
        ("process.close_anyway", {"process_id": pid, "note": "x"}),
    ):
        code = (
            "hr_process_unavailable" if operation.startswith("process") else "hr_task_unavailable"
        )
        with pytest.raises(REFUSED, match=rf"^{code}\b"):
            await in_other_tenant(world, {"operation": operation, **extra})
    assert (await task(world, pid, 1))["version"] == 1
    assert await process_status(world, pid) == "active"


async def in_other_tenant(world: HrWorld, body: dict[str, Any]) -> dict[str, Any]:
    async with tx(world.service.pool, str(world.other_tenant)) as conn:
        await conn.execute(
            "SELECT set_config('app.hr_user_id', %s, true)", (str(world.users["owner"]),)
        )
        payload = {k: _json(v) for k, v in body.items()}
        cursor = await conn.execute(TASK_SQL, (world.other_tenant, uuid4(), FP, Jsonb(payload)))
        row = await cursor.fetchone()
    assert row is not None
    return row["result"]


# ---- Carries from the PR 1 reviews --------------------------------------------------------------


async def test_send_back_of_a_leavers_task_skips_it(world: HrWorld) -> None:
    """Carry B1 (T3 M2): after a leaver's finish, sending back their task waiting for approval
    skips it; it is never reopened for a person who has left."""
    staff = world.employees["staff"]
    await create(
        world,
        kind="offboarding",
        steps=[
            step(
                title_en="Return the laptop",
                anchor="last_day",
                owner_role="employee",
                approval_by="hr",
            ),
            step(title_en="Final pay", anchor="last_day"),
        ],
    )
    today = await db_today(world)
    await ok(world, "owner", await leave_body(world, staff, today))
    pid = (await only_process(world, staff))["id"]
    await act(world, "staff", "task.complete", **await versioned_task(world, pid, 1))
    await finish_now_after_leave(world, staff)
    waiting = await task(world, pid, 1)
    assert (waiting["status"], waiting["assignee_employee_id"]) == ("awaiting_approval", staff)
    back = await act(
        world, "owner", "task.send_back", **await versioned_task(world, pid, 1), reason="Scratched"
    )
    assert back["task_status"] == "skipped"
    assert last(await events(world, pid), "task.skipped", 1)["detail"] == {
        "reason": "person_left",
        "remark": "Scratched",
        "sent_back": True,
    }
    assert not [e for e in await events(world, pid) if e["action"] == "task.sent_back"]


async def finish_now_after_leave(world: HrWorld, employee: UUID) -> None:
    """The person is already marked leaving today: let the day pass and finish."""
    await set_last_day(world, employee, (await db_today(world)) - timedelta(days=1))
    assert await finish(world, employee) in {"finished", "moved_finished"}


async def test_my_tasks_lists_tasks_awaiting_my_approval(world: HrWorld) -> None:
    """Carry B3 (T4): My tasks lists the approvals waiting for the caller, flagged."""
    staff = world.employees["staff"]
    pid = await custom(
        world,
        staff,
        step(title_en="Bank details", owner_role="employee", approval_by="manager"),
        step(title_en="Handbook", owner_role="employee", approval_by="hr"),
        step(title_en="Payroll", approval_by="hr"),
    )
    for ordinal in (1, 2):
        await act(
            world,
            "staff",
            "task.complete",
            **await versioned_task(world, pid, ordinal),
            note="Synthetic: staff note",
        )
    await make_people_manager(world)
    await act(world, "owner", "task.complete", **await versioned_task(world, pid, 3))

    def flags(rows: list[dict[str, Any]]) -> dict[str, bool]:
        return {r["title_en"]: r["needs_my_approval"] for r in rows}

    by_manager = await mine(world, "manager")
    assert flags(by_manager) == {"Bank details": True}
    assert set(by_manager[0]) == MINE_COLUMNS | {"needs_my_approval", "sent_back_reason"}
    assert by_manager[0]["note"] is None  # a manager who only approves gets no note
    assert by_manager[0]["status"] == "awaiting_approval"
    # The owner completed Payroll (a pool task, listed), so it is not theirs to approve.
    assert flags(await mine(world, "owner")) == {"Handbook": True, "Payroll": False}
    assert flags(await mine(world, "operator")) == {"Handbook": True, "Payroll": True}
    by_staff = await mine(world, "staff")
    assert flags(by_staff) == {"Bank details": False, "Handbook": False}
    assert {r["note"] for r in by_staff} == {"Synthetic: staff note"}
    assert flags(await mine(world, "outsider")) == {}
    # Approved: gone from the approver's open list.
    await act(world, "manager", "task.approve", **await versioned_task(world, pid, 1))
    assert await mine(world, "manager") == []
    assert flags(await mine(world, "manager", status="done")) == {}


async def test_a_person_step_of_someone_who_left_goes_to_the_pool(world: HrWorld) -> None:
    """Carry B4 (I3): the template names a person who has left since it was saved."""
    outsider = world.employees["outsider"]
    await create(
        world,
        steps=[step(title_en="Buddy lunch", owner_role="person", owner_employee_id=str(outsider))],
    )
    await finish_now(world, outsider)
    hire = await add_person(world)
    pid = (await only_process(world, hire))["id"]
    assert (await task(world, pid, 1))["assignee_employee_id"] is None
    assert event(await events(world, pid), "task.created", 1)["detail"] == {
        "owner_role": "person",
        "assignee_employee_id": None,
        "due_on": START.isoformat(),
        "fallback": "hr_pool",
        "reason": "person_inactive",
    }
    pooled = [r for r in await mine(world, "owner") if r["process_id"] == pid]
    assert [(r["title_en"], r["pool"]) for r in pooled] == [("Buddy lunch", True)]


async def test_a_leavers_own_onboarding_and_custom_processes_are_cancelled(
    world: HrWorld,
) -> None:
    """Carry B5 (I4): the offboarding is completed as before; the others end with the employment."""
    await create(world, steps=[step(title_en="Payroll set-up")])
    await create(
        world,
        kind="offboarding",
        steps=[step(title_en="Remove access", anchor="last_day", auto_check="access_removed")],
    )
    hire = await add_person(world)
    onboarding = (await only_process(world, hire))["id"]
    plan = await custom(world, hire, step(title_en="Review plan"))
    await finish_now(world, hire)
    by_kind = {p["kind"]: p for p in await processes(world, hire)}
    assert {k: p["status"] for k, p in by_kind.items()} == {
        "onboarding": "cancelled",
        "custom": "cancelled",
        "offboarding": "completed",
    }
    for pid in (onboarding, plan):
        logged = await events(world, pid)
        assert event(logged, "process.cancelled")["detail"] == {"reason": "person_left"}
        assert event(logged, "process.cancelled")["actor"] is None
        assert event(logged, "task.skipped", 1)["detail"] == {"reason": "process_cancelled"}


async def test_a_leavers_step_in_someone_elses_process_goes_to_the_pool(world: HrWorld) -> None:
    """Carry B6 (T3 M7): a colleague's step in another person's process is real work: it goes to
    the HR pool, never skipped; the leaver's own steps in their own offboarding are skipped."""
    staff, outsider = world.employees["staff"], world.employees["outsider"]
    await create(
        world,
        kind="offboarding",
        steps=[
            step(title_en="Exit survey", anchor="last_day", owner_role="employee"),
            step(title_en="Final pay", anchor="last_day"),
        ],
    )
    pid = await custom(
        world,
        outsider,
        step(title_en="Mentor session", owner_role="person", owner_employee_id=str(staff)),
        step(
            title_en="Reviewed form",
            owner_role="person",
            owner_employee_id=str(staff),
            approval_by="hr",
        ),
        step(title_en="Order a desk"),
    )
    await act(world, "staff", "task.complete", **await versioned_task(world, pid, 2))
    await act(
        world,
        "owner",
        "task.reassign",
        **await versioned_task(world, pid, 3),
        assignee_employee_id=staff,
    )
    await finish_now(world, staff)
    rows = [await task(world, pid, n) for n in (1, 2, 3)]
    assert [(r["status"], r["assignee_employee_id"]) for r in rows] == [
        ("todo", None),
        ("awaiting_approval", None),
        ("todo", None),
    ]
    assert all(r["note"] is None for r in rows)
    assert await process_status(world, pid) == "active"
    logged = await events(world, pid)
    for ordinal in (1, 2, 3):
        assert last(logged, "task.reassigned", ordinal)["detail"] == {
            "from": str(staff),
            "to": None,
            "fallback": "hr_pool",
            "reason": "person_left",
        }
    assert not [e for e in logged if e["action"] == "task.skipped"]
    own = next(p for p in await processes(world, staff) if p["kind"] == "offboarding")
    assert (await task(world, own["id"], 1))["status"] == "skipped"
    assert event(await events(world, own["id"]), "task.skipped", 1)["detail"] == {
        "reason": "person_left"
    }


async def test_a_managers_own_step_is_approved_by_hr_not_the_manager(world: HrWorld) -> None:
    """Carry B7: owner_role manager with approval_by manager can never be approved by the
    manager of the same person; the HR team approves, also when HR completed it for them."""
    staff, manager = world.employees["staff"], world.employees["manager"]
    pid = await custom(
        world,
        staff,
        step(title_en="Probation talk", owner_role="manager", approval_by="manager"),
        step(title_en="Goals", owner_role="manager", approval_by="manager"),
        step(
            title_en="Reference",
            owner_role="person",
            owner_employee_id=str(manager),
            approval_by="manager",
        ),
    )
    await act(world, "owner", "task.complete", **await versioned_task(world, pid, 1))
    await act(world, "manager", "task.complete", **await versioned_task(world, pid, 2))
    await act(world, "manager", "task.complete", **await versioned_task(world, pid, 3))
    assert {r["title_en"]: r["needs_my_approval"] for r in await mine(world, "manager")} == {
        "Probation talk": False,
        "Goals": False,
        "Reference": False,
    }
    for ordinal in (1, 2, 3):
        body = await versioned_task(world, pid, ordinal)
        await refused(world, "hr_task_not_approver", "manager", "task.approve", **body)
    await make_people_manager(world)
    await act(world, "operator", "task.approve", **await versioned_task(world, pid, 1))
    for ordinal in (2, 3):
        await act(world, "owner", "task.approve", **await versioned_task(world, pid, ordinal))
    logged = await events(world, pid)
    assert {event(logged, "task.approved", n)["detail"]["approver"] for n in (1, 2, 3)} == {"hr"}


async def test_hr_remarks_stay_in_events_and_the_note_is_shared(world: HrWorld) -> None:
    """Carry B8: the reasons of skip, send back and close anyway are HR-only remarks in the
    event; task.note is the note the assignee sees."""
    staff = world.employees["staff"]
    pid = await custom(
        world,
        staff,
        step(title_en="Upload the form", owner_role="employee", approval_by="hr"),
        step(title_en="Optional chat", owner_role="employee", required=False),
        step(title_en="Payroll"),
    )
    await act(
        world,
        "owner",
        "task.note",
        task_id=(await task(world, pid, 1))["id"],
        note="Synthetic: please use the new form",
    )
    await act(world, "staff", "task.complete", **await versioned_task(world, pid, 1))
    await act(
        world,
        "owner",
        "task.send_back",
        **await versioned_task(world, pid, 1),
        reason="Synthetic HR-only: wrong form",
    )
    await act(
        world,
        "owner",
        "task.skip",
        **await versioned_task(world, pid, 2),
        reason="Synthetic HR-only: not needed",
    )
    await act(
        world, "owner", "process.close_anyway", process_id=pid, note="Synthetic HR-only: closed"
    )
    assert [(await task(world, pid, n))["note"] for n in (1, 2, 3)] == [
        "Synthetic: please use the new form",
        None,
        None,
    ]
    shown = {r["title_en"]: r["note"] for r in await mine(world, "staff", status="skipped")}
    assert shown == {
        "Upload the form": "Synthetic: please use the new form",
        "Optional chat": None,
    }
    remarks = {e["detail"].get("remark") for e in await events(world, pid)} - {None}
    assert remarks == {
        "Synthetic HR-only: wrong form",
        "Synthetic HR-only: not needed",
        "Synthetic HR-only: closed",
    }


async def test_functions_are_pinned_definers_and_only_the_entry_point_is_granted(
    world: HrWorld,
) -> None:
    for signature, public in (
        ("hr_task_apply(uuid,uuid,text,jsonb)", True),
        ("hr_task_rows_mine(uuid,text,integer,uuid)", True),
        *((s, False) for s in INTERNAL_FUNCTIONS),
    ):
        [row] = await world.q(
            "SELECT p.prosecdef, p.proconfig,"
            " has_function_privilege('csa_app', p.oid, 'EXECUTE') AS csa_app,"
            " has_function_privilege('csa_ro', p.oid, 'EXECUTE') AS csa_ro,"
            " has_function_privilege('csa_identity', p.oid, 'EXECUTE') AS csa_identity,"
            " has_function_privilege('csa_hr_maintenance', p.oid, 'EXECUTE') AS maintenance,"
            " has_function_privilege('public', p.oid, 'EXECUTE') AS everyone"
            " FROM pg_proc p WHERE p.oid=%s::regprocedure",
            (f"public.{signature}",),
        )
        assert row["prosecdef"] is True, signature
        assert row["proconfig"] == ["search_path=pg_catalog, public, pg_temp"], signature
        assert row["csa_app"] is public, signature
        assert not (row["csa_ro"] or row["csa_identity"] or row["maintenance"]), signature
        assert not row["everyone"], signature
    [column] = await world.q(
        "SELECT data_type, is_nullable, column_default FROM information_schema.columns"
        " WHERE table_name='hr_process_tasks' AND column_name='assignee_overridden'"
    )
    assert column == {"data_type": "boolean", "is_nullable": "NO", "column_default": "false"}


# ---- Review fix round 1 -----------------------------------------------------------------------


async def test_the_send_back_reason_is_shown_to_the_assignee_and_hr(world: HrWorld) -> None:
    """Review I1: a send-back reason is addressed to the assignee (sent_back_reason), apart from
    the shared note; a manager's view of other people's tasks and a stranger never get it."""
    staff = world.employees["staff"]
    pid = await custom(
        world,
        staff,
        step(title_en="Bank details", owner_role="employee", approval_by="manager"),
        step(title_en="Payroll"),
    )
    await act(
        world,
        "staff",
        "task.complete",
        **await versioned_task(world, pid, 1),
        note="Synthetic: staff note",
    )
    waiting = await versioned_task(world, pid, 1)
    await refused(
        world, "hr_task_reason_invalid", "manager", "task.send_back", **waiting, reason="x" * 501
    )
    reason = "Synthetic: the account number\nis missing a digit"
    await act(world, "manager", "task.send_back", **waiting, reason=f"  {reason}  ")
    row = await task(world, pid, 1)
    assert (row["status"], row["sent_back_reason"], row["note"]) == (
        "todo",
        reason,
        "Synthetic: staff note",
    )
    assert event(await events(world, pid), "task.sent_back", 1)["detail"] == {"remark": reason}
    [own] = [r for r in await mine(world, "staff") if r["title_en"] == "Bank details"]
    assert (own["sent_back_reason"], own["note"]) == (reason, "Synthetic: staff note")
    as_assignee = await get_process(world, "staff", pid)
    assert as_assignee is not None and as_assignee["view"] == "assignee"
    assert as_assignee["tasks"][0]["sent_back_reason"] == reason
    as_hr = await get_process(world, "owner", pid)
    assert as_hr is not None
    assert [t.get("sent_back_reason") for t in as_hr["tasks"]] == [reason, None]
    as_manager = await get_process(world, "manager", pid)
    assert as_manager is not None and as_manager["view"] == "manager"
    assert all("sent_back_reason" not in t for t in as_manager["tasks"])
    assert await mine(world, "manager") == []
    assert await get_process(world, "outsider", pid) is None
    assert await mine(world, "outsider") == []

    # Completing again clears it.
    await act(world, "staff", "task.complete", **await versioned_task(world, pid, 1))
    assert (await task(world, pid, 1))["sent_back_reason"] is None
    [again] = [r for r in await mine(world, "staff") if r["title_en"] == "Bank details"]
    assert again["sent_back_reason"] is None
    assert again["note"] == "Synthetic: staff note"


async def test_a_managers_approval_rows_hide_descriptions_and_reference(world: HrWorld) -> None:
    """Review m1: an approval row of a task that is not the manager's own carries what 0633's
    manager view shows (title, status, due date), not the descriptions or reference note."""
    staff = world.employees["staff"]
    pid = await custom(
        world,
        staff,
        step(
            title_en="Bank details",
            owner_role="employee",
            approval_by="manager",
            description_en="Synthetic: bring the bank letter",
            description_ms="Synthetic: bawa surat bank",
            reference_note="Synthetic bank form",
        ),
    )
    await act(world, "staff", "task.complete", **await versioned_task(world, pid, 1))
    [approval] = await mine(world, "manager")
    [own] = await mine(world, "staff")
    assert approval["needs_my_approval"] is True
    assert (approval["description_en"], approval["description_ms"], approval["reference_note"]) == (
        None,
        None,
        None,
    )
    assert (approval["title_en"], approval["status"], approval["due_on"]) == (
        own["title_en"],
        "awaiting_approval",
        own["due_on"],
    )
    assert (own["description_en"], own["description_ms"], own["reference_note"]) == (
        "Synthetic: bring the bank letter",
        "Synthetic: bawa surat bank",
        "Synthetic bank form",
    )


async def test_an_employee_step_for_a_person_who_left_goes_to_the_pool(world: HrWorld) -> None:
    """Review m2: a custom process may be started for someone who has left; their employee step
    starts in the HR pool instead of with a person who can never act on it."""
    gone = await insert_employee(world, active=False)
    pid = await custom(world, gone, step(title_en="Confirm bank account", owner_role="employee"))
    assert (await task(world, pid, 1))["assignee_employee_id"] is None
    assert event(await events(world, pid), "task.created", 1)["detail"] == {
        "owner_role": "employee",
        "assignee_employee_id": None,
        "due_on": START.isoformat(),
        "fallback": "hr_pool",
        "reason": "person_inactive",
    }
    pooled = [r for r in await mine(world, "owner") if r["process_id"] == pid]
    assert [(r["title_en"], r["pool"]) for r in pooled] == [("Confirm bank account", True)]


async def test_close_anyway_replayed_with_the_same_key_writes_one_event(world: HrWorld) -> None:
    pid = await custom(world, world.employees["staff"], step(title_en="Payroll"))
    key = uuid4()
    first = await act(world, "owner", "process.close_anyway", process_id=pid, note="x", key=key)
    written = await events(world, pid)
    again = await act(world, "owner", "process.close_anyway", process_id=pid, note="x", key=key)
    assert again == {**first, "duplicate": True}
    assert await events(world, pid) == written
    assert [e["action"] for e in written].count("process.completed") == 1


async def test_a_caller_whose_employee_is_no_longer_active_gets_nothing(world: HrWorld) -> None:
    """A member whose linked employee is inactive (still signed in) is nobody to the task
    functions: hr_process_caller resolves only an active linked employee."""
    staff, outsider = world.employees["staff"], world.employees["outsider"]
    pid = await custom(
        world,
        outsider,
        step(title_en="Mentor session", owner_role="person", owner_employee_id=str(staff)),
    )
    await world.q(
        "UPDATE hr_employees SET is_active=false WHERE client_id=%s AND id=%s",
        (world.tenant, staff),
    )
    # The trigger sent the step to the pool; put it back on the person, as old data could hold
    # it, so only the caller rule is under test.
    await world.q(
        "UPDATE hr_process_tasks SET assignee_employee_id=%s WHERE client_id=%s AND process_id=%s",
        (staff, world.tenant, pid),
    )
    body = await versioned_task(world, pid, 1)
    await refused(world, "hr_task_unavailable", "staff", "task.complete", **body)
    await refused(
        world, "hr_task_unavailable", "staff", "task.note", task_id=body["task_id"], note="x"
    )
    assert await mine(world, "staff") == []
    assert (await task(world, pid, 1))["version"] == body["expected_version"]
