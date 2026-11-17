"""The HR task routes (My tasks and the task actions of 0634, and closing a process anyway),
through the real HR router (disposable database). Reuses the HrWorld fixture of
tests/test_hr_persistence.py: staff and owner report to manager; outsider reports to nobody; owner
is the HR owner; operator has no employee (people.manage when granted); reader is a read-only
member; admin a tenant admin with no HR authority.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from csa.hr import processes as process_service

from tests.conftest import requires_docker
from tests.test_hr_people import make_other_tenant_owner, make_people_manager
from tests.test_hr_persistence import ORIGIN, HrWorld, hr_database, world
from tests.test_hr_process_api import (
    HR,
    PROCESSES,
    get_ok,
    post_template,
    problem,
    start,
    without_csrf,
    without_key,
)
from tests.test_hr_process_instances import START_SQL, run_in_other_tenant, start_body
from tests.test_hr_process_templates import apply_in_other_tenant, create_body, step

__all__ = ["hr_database", "world"]
pytestmark = [requires_docker, pytest.mark.asyncio]

TASKS = f"{HR}/tasks"
MINE = f"{TASKS}/mine"
# Every column of 0634's hr_task_rows_mine, as a My tasks row carries it.
ROW = {
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
    "needs_my_approval",
    "sent_back_reason",
}


async def custom_process(world: HrWorld, employee: UUID, *steps: dict[str, Any]) -> str:
    """A custom process for `employee` with these steps, started by the HR owner."""
    template = await post_template(world, kind="custom", name="Synthetic plan", steps=list(steps))
    return (await start(world, employee, "custom", template["template_id"]))["process_id"]


async def tasks_of(world: HrWorld, process: str) -> list[dict[str, Any]]:
    return (await get_ok(world, "owner", f"{PROCESSES}/{process}"))["tasks"]


async def task_of(world: HrWorld, process: str, ordinal: int) -> dict[str, Any]:
    [found] = [t for t in await tasks_of(world, process) if t["ordinal"] == ordinal]
    return found


async def at(world: HrWorld, process: str, ordinal: int) -> tuple[str, dict[str, Any]]:
    """The task's id and a body naming its current version."""
    found = await task_of(world, process, ordinal)
    return found["task_id"], {"expected_version": found["version"]}


async def act(
    world: HrWorld,
    who: str,
    task: str,
    action: str,
    body: dict[str, Any] | None = None,
    *,
    key: UUID | None = None,
) -> httpx.Response:
    return await world.call(who, "POST", f"{TASKS}/{task}/{action}", body, key=key)


async def done(
    world: HrWorld, who: str, task: str, action: str, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    response = await act(world, who, task, action, body)
    assert response.status_code == 200, response.text
    return response.json()


async def mine(world: HrWorld, who: str, query: str = "") -> dict[str, Any]:
    return await get_ok(world, who, f"{MINE}?{query}" if query else MINE)


async def state(world: HrWorld) -> tuple[list[tuple[Any, ...]], int, int]:
    """Every task's status, version, note and assignee, the event count and the receipt count."""
    rows = await world.q(
        "SELECT id, status, version, note, assignee_employee_id, due_on FROM hr_process_tasks"
        " WHERE client_id=%s ORDER BY id",
        (world.tenant,),
    )
    [events] = await world.q(
        "SELECT count(*) AS n FROM hr_process_events WHERE client_id=%s", (world.tenant,)
    )
    [receipts] = await world.q(
        "SELECT count(*) AS n FROM hr_operations WHERE client_id=%s", (world.tenant,)
    )
    return [tuple(r.values()) for r in rows], events["n"], receipts["n"]


def receipt(
    operation: str, task: str, process: str, task_status: str, version: int, **over: Any
) -> dict[str, Any]:
    return {
        "operation": operation,
        "task_id": task,
        "process_id": process,
        "task_status": task_status,
        "version": version,
        "process_status": "active",
        "status": "saved",
        "duplicate": False,
        **over,
    }


async def foreign_ids(world: HrWorld) -> tuple[str, str]:
    """A task and a process of the other tenant (its HR owner is world's owner)."""
    await make_other_tenant_owner(world)
    template = (await apply_in_other_tenant(world, create_body(kind="custom")))["template_id"]
    process = (
        await run_in_other_tenant(
            world, START_SQL, start_body(world.employees["staff"], "custom", UUID(template))
        )
    )["process_id"]
    [row] = await world.q(
        "SELECT id FROM hr_process_tasks WHERE client_id=%s AND process_id=%s",
        (world.other_tenant, process),
    )
    return str(row["id"]), process


# ---- Each route -------------------------------------------------------------------------------


async def test_each_task_route_through_the_router(world: HrWorld) -> None:
    staff, manager = world.employees["staff"], world.employees["manager"]
    process = await custom_process(
        world,
        staff,
        step(title_en="Fill in details", owner_role="employee"),
        step(title_en="Bank details", owner_role="employee", approval_by="manager"),
        step(title_en="Upload the form", owner_role="employee", approval_by="manager"),
        step(title_en="Order a desk"),
    )

    # note, then complete: the assignee.
    task, body = await at(world, process, 1)
    note = "Synthetic: line one\r\nline two"
    noted = await done(world, "staff", task, "note", {"note": note})
    assert noted == receipt("task.note", task, process, "todo", 2)
    assert (await task_of(world, process, 1))["note"] == "Synthetic: line one\nline two"
    key = uuid4()
    completed = await act(world, "staff", task, "complete", {"expected_version": 2}, key=key)
    assert completed.status_code == 200, completed.text
    assert completed.json() == receipt("task.complete", task, process, "done", 3)
    before = await state(world)
    replay = await act(world, "staff", task, "complete", {"expected_version": 2}, key=key)
    assert replay.json() == {**completed.json(), "duplicate": True}
    assert await state(world) == before
    problem(
        await act(world, "staff", task, "complete", {"expected_version": 3}, key=key),
        409,
        "idempotency_conflict",
    )

    # approve: the manager of the person, for a step awaiting approval.
    task, body = await at(world, process, 2)
    waiting = await done(world, "staff", task, "complete", {**body, "note": "Synthetic: sent"})
    assert waiting == receipt("task.complete", task, process, "awaiting_approval", 2)
    approved = await done(world, "manager", task, "approve", {"expected_version": 2})
    assert approved == receipt("task.approve", task, process, "done", 3)

    # send-back: the approver; the reason goes to the assignee.
    task, body = await at(world, process, 3)
    await done(world, "staff", task, "complete", body)
    reason = "Synthetic: the form\nis unsigned"
    sent = await done(
        world, "manager", task, "send-back", {"expected_version": 2, "reason": reason}
    )
    assert sent == receipt("task.send_back", task, process, "todo", 3)
    returned = await task_of(world, process, 3)
    assert (returned["status"], returned["sent_back_reason"]) == ("todo", reason)

    # reschedule, reassign, skip: the HR team.
    task, body = await at(world, process, 4)
    rescheduled = await done(world, "owner", task, "reschedule", {**body, "due_on": "2027-01-15"})
    assert rescheduled == receipt("task.reschedule", task, process, "todo", 2)
    moved = await task_of(world, process, 4)
    assert (moved["due_on"], moved["due_overridden"]) == ("2027-01-15", True)
    reassigned = await done(
        world,
        "owner",
        task,
        "reassign",
        {"expected_version": 2, "assignee_employee_id": str(manager)},
    )
    assert reassigned == receipt("task.reassign", task, process, "todo", 3)
    assert (await task_of(world, process, 4))["assignee_employee_id"] == str(manager)
    back_to_pool = await done(
        world, "owner", task, "reassign", {"expected_version": 3, "assignee_employee_id": None}
    )
    assert back_to_pool["version"] == 4
    assert (await task_of(world, process, 4))["assignee_employee_id"] is None
    skipped = await done(
        world, "owner", task, "skip", {"expected_version": 4, "reason": "Synthetic: not needed"}
    )
    assert skipped == receipt("task.skip", task, process, "skipped", 5)

    # close: the HR team closes the process anyway; its open steps are skipped.
    closed = await world.call(
        "owner", "POST", f"{PROCESSES}/{process}/close", {"note": "Synthetic:\nclosed by HR"}
    )
    assert closed.status_code == 200, closed.text
    assert closed.json() == {
        "operation": "process.close_anyway",
        "process_id": process,
        "process_status": "completed",
        "status": "saved",
        "duplicate": False,
    }
    detail = await get_ok(world, "owner", f"{PROCESSES}/{process}")
    assert detail["process"]["status"] == "completed"
    assert [t["status"] for t in detail["tasks"]] == ["done", "done", "skipped", "skipped"]
    [closing] = [e for e in detail["events"] if e["action"] == "process.completed"]
    assert closing["detail"] == {"reason": "closed_anyway", "remark": "Synthetic:\nclosed by HR"}
    assert closing["actor"] == str(world.users["owner"])
    problem(
        await world.call(
            "owner", "POST", f"{PROCESSES}/{process}/close", {"note": "Synthetic: again"}
        ),
        409,
        "process_closed",
    )


# ---- Who may do what --------------------------------------------------------------------------


async def test_who_may_act_on_a_task(world: HrWorld) -> None:
    """The assignee completes and notes their own step, the manager approves and sends back a
    report's step, the HR team does the rest; a stranger, a tenant admin with no HR part and
    another tenant's ids get 404, a read-only session 403, and nothing is written."""
    staff, manager = world.employees["staff"], world.employees["manager"]
    process = await custom_process(
        world,
        staff,
        step(title_en="Fill in details", owner_role="employee"),
        step(title_en="Bank details", owner_role="employee", approval_by="manager"),
        step(title_en="Upload the form", owner_role="employee", approval_by="manager"),
        step(title_en="Order a desk"),
    )
    for ordinal in (2, 3):
        task, body = await at(world, process, ordinal)
        await done(world, "staff", task, "complete", body)
    own, own_body = await at(world, process, 1)
    approval, approval_body = await at(world, process, 2)
    back, back_body = await at(world, process, 3)
    pool, pool_body = await at(world, process, 4)
    reason = {"reason": "Synthetic: reason"}
    writes: list[tuple[str, str, dict[str, Any]]] = [
        (own, "complete", own_body),
        (own, "note", {"note": "Synthetic: note"}),
        (approval, "approve", approval_body),
        (back, "send-back", {**back_body, **reason}),
        (own, "skip", {**own_body, **reason}),
        (own, "reschedule", {**own_body, "due_on": "2027-01-15"}),
        (own, "reassign", {**own_body, "assignee_employee_id": str(manager)}),
    ]
    refusals: dict[tuple[str, str], tuple[int, str]] = {
        # The manager sees the report's steps but is not their assignee.
        ("manager", "complete"): (409, "task_not_assignee"),
        ("manager", "note"): (409, "task_not_assignee"),
        # The assignee and HR are not the approver of a manager approval while the manager
        # resolves.
        ("staff", "approve"): (409, "task_not_approver"),
        ("staff", "send-back"): (409, "task_not_approver"),
        ("owner", "approve"): (409, "task_not_approver"),
        ("owner", "send-back"): (409, "task_not_approver"),
    }
    before = await state(world)
    for task, action, body in writes:
        for who in ("staff", "manager", "owner", "outsider", "operator", "admin", "reader"):
            allowed = (who, action) in {
                ("staff", "complete"),
                ("staff", "note"),
                ("manager", "approve"),
                ("manager", "send-back"),
                ("owner", "complete"),
                ("owner", "note"),
                ("owner", "skip"),
                ("owner", "reschedule"),
                ("owner", "reassign"),
            }
            if allowed:
                continue
            response = await act(world, who, task, action, body)
            if who == "reader":
                problem(response, 403, "read_only_session")
            else:
                status, code = refusals.get((who, action), (404, "resource_unavailable"))
                problem(response, status, code)
    close = f"{PROCESSES}/{process}/close"
    for who in ("staff", "manager", "outsider", "operator", "admin"):
        problem(
            await world.call(who, "POST", close, {"note": "Synthetic"}), 404, "resource_unavailable"
        )
    problem(
        await world.call("reader", "POST", close, {"note": "Synthetic"}), 403, "read_only_session"
    )
    assert await state(world) == before

    # Another tenant's task and process are not found, whoever asks.
    foreign_task, foreign_process = await foreign_ids(world)
    for who in ("owner", "staff", "manager"):
        for _task, action, body in writes:
            problem(await act(world, who, foreign_task, action, body), 404, "resource_unavailable")
    problem(
        await world.call("owner", "POST", f"{PROCESSES}/{foreign_process}/close", {"note": "x"}),
        404,
        "resource_unavailable",
    )
    problem(
        await world.call(
            "owner",
            "POST",
            f"{TASKS}/{own}/complete?client_id={world.other_tenant}",
            own_body,
        ),
        403,
        "tenant_not_authorized",
    )
    [untouched] = await world.q(
        "SELECT status, version FROM hr_process_tasks WHERE client_id=%s AND id=%s",
        (world.other_tenant, foreign_task),
    )
    assert untouched == {"status": "todo", "version": 1}
    assert await state(world) == before

    # The allowed ones: the assignee, the approving manager, the HR team.
    assert (await done(world, "staff", own, "note", {"note": "Synthetic: mine"}))["version"] == 2
    assert (await done(world, "staff", own, "complete", {"expected_version": 2}))[
        "task_status"
    ] == "done"
    assert (await done(world, "manager", approval, "approve", approval_body))["task_status"] == (
        "done"
    )
    sent = await done(world, "manager", back, "send-back", {**back_body, **reason})
    assert sent["task_status"] == "todo"
    # HR completes the person's step on their behalf; a Manage people holder acts as HR.
    await make_people_manager(world)
    _back, again = await at(world, process, 3)
    assert (await done(world, "owner", back, "complete", again))["task_status"] == (
        "awaiting_approval"
    )
    assert (
        await done(world, "operator", pool, "reschedule", {**pool_body, "due_on": "2027-02-01"})
    )["version"] == 2
    assert (await done(world, "operator", pool, "skip", {"expected_version": 2, **reason}))[
        "task_status"
    ] == "skipped"
    closed = await world.call("operator", "POST", close, {"note": "Synthetic: done"})
    assert (closed.status_code, closed.json()["process_status"]) == (200, "completed")


async def test_every_task_write_requires_csrf_and_a_key(world: HrWorld) -> None:
    process = await custom_process(
        world,
        world.employees["staff"],
        step(title_en="Fill in details", owner_role="employee", approval_by="hr"),
    )
    task, body = await at(world, process, 1)
    writes: list[tuple[str, dict[str, Any]]] = [
        (f"{TASKS}/{task}/complete", body),
        (f"{TASKS}/{task}/skip", {**body, "reason": "Synthetic"}),
        (f"{TASKS}/{task}/approve", body),
        (f"{TASKS}/{task}/send-back", {**body, "reason": "Synthetic"}),
        (f"{TASKS}/{task}/note", {"note": "Synthetic"}),
        (f"{TASKS}/{task}/reschedule", {**body, "due_on": "2027-01-15"}),
        (f"{TASKS}/{task}/reassign", {**body, "assignee_employee_id": None}),
        (f"{PROCESSES}/{process}/close", {"note": "Synthetic"}),
    ]
    before = await state(world)
    for path, payload in writes:
        for who in ("owner", "staff"):
            wrong = await world.call(
                who, "POST", path, payload, headers={"X-CSRF-Token": "synthetic-wrong"}
            )
            problem(wrong, 403, "invalid csrf token")
            problem(
                await without_csrf(world, who, "POST", path, payload), 403, "invalid csrf token"
            )
            no_key = await without_key(world, who, "POST", path, payload)
            assert (no_key.status_code, no_key.json()["detail"]) == (422, "invalid_hr_input"), path
            bad_key = await world.call(
                who, "POST", path, payload, headers={"Idempotency-Key": "not-a-uuid"}
            )
            assert (bad_key.status_code, bad_key.json()["detail"]) == (422, "invalid_hr_input")
        problem(await world.call("reader", "POST", path, payload), 403, "read_only_session")
    assert await state(world) == before
    # My tasks is a read: no CSRF proof needed, a wrong one changes nothing.
    response = await world.call("staff", "GET", MINE, headers={"X-CSRF-Token": "x"})
    assert response.status_code == 200
    assert (await without_csrf(world, "staff", "GET", MINE, None)).status_code == 200


# ---- Stable error codes -----------------------------------------------------------------------


async def test_refusals_are_stable_codes_and_bad_bodies_are_422(world: HrWorld) -> None:
    staff, outsider = world.employees["staff"], world.employees["outsider"]
    process = await custom_process(
        world,
        staff,
        step(title_en="Fill in details", owner_role="employee"),
        step(
            title_en="Sign the contract",
            owner_role="employee",
            auto_check="document_uploaded:contract",
        ),
        step(title_en="Check the form", approval_by="hr"),
        step(title_en="Collect the laptop", owner_role="employee", auto_check="access_removed"),
    )
    first, body = await at(world, process, 1)
    checked, checked_body = await at(world, process, 2)
    form, form_body = await at(world, process, 3)
    laptop, laptop_body = await at(world, process, 4)
    # A stale version.
    problem(
        await act(world, "staff", first, "complete", {"expected_version": 9}), 409, "task_changed"
    )
    problem(
        await act(world, "owner", first, "skip", {"expected_version": 0, "reason": "Synthetic"}),
        409,
        "task_changed",
    )
    # An automatic check that does not hold; the system-only check.
    problem(await act(world, "staff", checked, "complete", checked_body), 409, "check_not_met")
    problem(await act(world, "owner", laptop, "complete", laptop_body), 409, "task_system_only")
    # Approval states.
    problem(
        await act(world, "owner", form, "approve", form_body), 409, "task_not_awaiting_approval"
    )
    await make_people_manager(world)
    await done(world, "owner", form, "complete", form_body)
    _form, waiting = await at(world, process, 3)
    problem(await act(world, "owner", form, "complete", waiting), 409, "task_awaiting_approval")
    problem(await act(world, "owner", form, "approve", waiting), 409, "task_self_approval")
    # The new assignee must be an active person of this company.
    problem(
        await act(
            world, "owner", first, "reassign", {**body, "assignee_employee_id": str(uuid4())}
        ),
        409,
        "task_assignee_unavailable",
    )
    # A closed task refuses every action.
    await done(world, "staff", first, "complete", body)
    _first, closed = await at(world, process, 1)
    for action, payload in (
        ("complete", closed),
        ("note", {"note": "Synthetic"}),
        ("skip", {**closed, "reason": "Synthetic"}),
        ("reschedule", {**closed, "due_on": "2027-01-15"}),
        ("reassign", {**closed, "assignee_employee_id": str(outsider)}),
    ):
        problem(await act(world, "owner", first, action, payload), 409, "task_closed")
    # Unknown ids.
    for action, payload in (("complete", body), ("note", {"note": "Synthetic"})):
        problem(
            await act(world, "owner", str(uuid4()), action, payload), 404, "resource_unavailable"
        )
    problem(
        await world.call("owner", "POST", f"{PROCESSES}/{uuid4()}/close", {"note": "Synthetic"}),
        404,
        "resource_unavailable",
    )

    # Shapes the database would refuse are 422 here first, and the input is never repeated.
    before = await state(world)
    secret = "Synthetic-secret"
    for action, payload in (
        ("complete", {}),
        ("complete", {"expected_version": "1"}),
        ("complete", {"expected_version": -1}),
        ("complete", {"expected_version": 1, "note": secret + "x" * 2000}),
        ("complete", {"expected_version": 1, "note": secret + "\there"}),
        ("complete", {"expected_version": 1, "colour": secret}),
        ("note", {}),
        ("note", {"note": secret + "\u0000"}),
        ("approve", {}),
        ("send-back", {"expected_version": 1}),
        ("send-back", {"expected_version": 1, "reason": "   "}),
        ("skip", {"expected_version": 1, "reason": secret + "r" * 500}),
        ("skip", {"expected_version": 1, "reason": secret + "\rlone"}),
        ("reschedule", {"expected_version": 1}),
        ("reschedule", {"expected_version": 1, "due_on": secret}),
        ("reassign", {"expected_version": 1}),
        ("reassign", {"expected_version": 1, "assignee_employee_id": secret}),
    ):
        response = await act(world, "owner", checked, action, payload)
        assert (response.status_code, response.json()["detail"]) == (422, "invalid_hr_input"), (
            action,
            payload,
        )
        assert secret not in response.text
    for payload in ({}, {"note": ""}, {"note": "n" * 501}, {"note": "x", "expected_version": 1}):
        response = await world.call("owner", "POST", f"{PROCESSES}/{process}/close", payload)
        assert (response.status_code, response.json()["detail"]) == (422, "invalid_hr_input")
    response = await world.call("owner", "POST", f"{TASKS}/not-a-uuid/complete", body)
    assert (response.status_code, response.json()["detail"]) == (422, "invalid_hr_input")
    assert await state(world) == before


async def test_task_responses_never_carry_database_text(
    world: HrWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = await custom_process(
        world, world.employees["staff"], step(title_en="Fill in details", owner_role="employee")
    )
    task, body = await at(world, process, 1)

    class Broken:
        async def fetch_one(self, *args: Any, **kwargs: Any) -> Any:
            raise psycopg.errors.CheckViolation(
                'new row for relation "hr_process_tasks" violates check constraint '
                '"hr_process_tasks_version_check" DETAIL: Failing row contains (secret).'
            )

        async def fetch_all(self, *args: Any, **kwargs: Any) -> Any:
            raise psycopg.errors.UndefinedFunction(
                "function hr_task_rows_mine(uuid) does not exist"
            )

    monkeypatch.setattr(process_service, "sql", Broken())
    for method, path, payload in (
        ("POST", f"{TASKS}/{task}/complete", body),
        ("POST", f"{PROCESSES}/{process}/close", {"note": "Synthetic"}),
        ("GET", MINE, None),
    ):
        problem(await world.call("owner", method, path, payload), 503, "hr_database_unavailable")


# ---- My tasks ---------------------------------------------------------------------------------


async def test_my_tasks_for_each_viewer(world: HrWorld) -> None:
    staff = world.employees["staff"]
    process = await custom_process(
        world,
        staff,
        step(
            title_en="Bank details",
            title_ms="Butiran bank",
            owner_role="employee",
            approval_by="manager",
            description_en="Synthetic: bring\nthe bank letter",
            reference_note="Synthetic bank form",
        ),
        step(title_en="Fill in details", owner_role="employee"),
        step(title_en="Order a desk"),
    )
    bank, bank_body = await at(world, process, 1)
    details, _details_body = await at(world, process, 2)
    desk, _desk_body = await at(world, process, 3)
    note = "Synthetic: line one\nline two"
    await done(world, "staff", bank, "complete", {**bank_body, "note": note})

    # The assignee: their own steps in full.
    by_staff = (await mine(world, "staff"))["items"]
    assert [r["task_id"] for r in by_staff] == sorted([bank, details], key=UUID)
    assert all(set(r) == ROW for r in by_staff)
    [own] = [r for r in by_staff if r["task_id"] == bank]
    assert own == {
        "task_id": bank,
        "process_id": process,
        "process_kind": "custom",
        "process_name": "Synthetic plan",
        "employee_name": "Synthetic Staff",
        "ordinal": 1,
        "title_en": "Bank details",
        "title_ms": "Butiran bank",
        "description_en": "Synthetic: bring\nthe bank letter",
        "description_ms": None,
        "owner_role": "employee",
        "due_on": None,
        "required": True,
        "approval_by": "manager",
        "auto_check": None,
        "reference_note": "Synthetic bank form",
        "status": "awaiting_approval",
        "note": note,
        "version": 2,
        "overdue": False,
        "pool": False,
        "needs_my_approval": False,
        "sent_back_reason": None,
    }
    # The manager: only the step waiting for their approval, flagged, without the assignee's text.
    [approval] = (await mine(world, "manager"))["items"]
    assert set(approval) == ROW
    assert (approval["task_id"], approval["needs_my_approval"], approval["status"]) == (
        bank,
        True,
        "awaiting_approval",
    )
    for withheld in ("description_en", "description_ms", "reference_note", "note"):
        assert approval[withheld] is None, withheld
    # The HR team: the pool; the person's own steps are not theirs.
    by_owner = (await mine(world, "owner"))["items"]
    assert [(r["task_id"], r["pool"]) for r in by_owner] == [(desk, True)]
    # Nobody else.
    for who in ("outsider", "admin", "reader", "operator"):
        assert (await mine(world, who)) == {"items": [], "next_cursor": None}, who

    # Sent back: the reason reaches the assignee, the note is kept; the manager no longer has it.
    reason = "Synthetic: the account number\nis missing a digit"
    await done(world, "manager", bank, "send-back", {"expected_version": 2, "reason": reason})
    [back] = [r for r in (await mine(world, "staff"))["items"] if r["task_id"] == bank]
    assert (back["status"], back["sent_back_reason"], back["note"]) == ("todo", reason, note)
    assert (await mine(world, "manager"))["items"] == []
    assert (await mine(world, "outsider"))["items"] == []
    as_manager = await get_ok(world, "manager", f"{PROCESSES}/{process}")
    assert all("sent_back_reason" not in t for t in as_manager["tasks"] if not t["mine"])
    # The status filter: done steps on request only.
    await done(world, "staff", details, "complete", {"expected_version": 1})
    assert [r["task_id"] for r in (await mine(world, "staff"))["items"]] == [bank]
    done_rows = (await mine(world, "staff", "status=done"))["items"]
    assert [(r["task_id"], r["status"]) for r in done_rows] == [(details, "done")]
    assert (await mine(world, "staff", "status=skipped"))["items"] == []
    for query, status, code in (
        ("colour=red", 400, "invalid_hr_query"),
        ("status=done&status=todo", 400, "invalid_hr_query"),
        ("status=open", 422, "invalid_hr_input"),
        ("limit=0", 422, "invalid_hr_input"),
        ("limit=201", 422, "invalid_hr_input"),
        ("after=nobody", 422, "invalid_hr_input"),
    ):
        response = await world.call("staff", "GET", f"{MINE}?{query}")
        assert (response.status_code, response.json()["detail"]) == (status, code), query
    problem(
        await world.call("staff", "GET", f"{MINE}?client_id={world.other_tenant}"),
        403,
        "tenant_not_authorized",
    )
    # Another tenant's steps never show.
    await foreign_ids(world)
    assert [r["task_id"] for r in (await mine(world, "owner"))["items"]] == [desk]


async def test_my_tasks_pages_soonest_due_first(world: HrWorld) -> None:
    process = await custom_process(
        world, world.employees["staff"], *(step(title_en=f"Pool step {n}") for n in range(1, 6))
    )
    base = date(2027, 1, 10)
    dated: dict[str, date] = {}
    for ordinal, offset in ((2, 5), (4, 1), (5, 3)):
        task, body = await at(world, process, ordinal)
        due = base + timedelta(days=offset)
        await done(world, "owner", task, "reschedule", {**body, "due_on": due.isoformat()})
        dated[task] = due
    undated = sorted(
        (t["task_id"] for t in await tasks_of(world, process) if t["task_id"] not in dated),
        key=UUID,
    )
    expected = sorted(dated, key=lambda t: dated[t]) + undated
    assert [r["task_id"] for r in (await mine(world, "owner"))["items"]] == expected

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        query = "limit=2" + (f"&after={cursor}" if cursor else "")
        page = await mine(world, "owner", query)
        seen += [r["task_id"] for r in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
        assert cursor == seen[-1]
    assert (seen, pages) == (expected, 3)
    # A cursor this caller cannot place (another tenant's step) gives an empty page.
    foreign_task, _process = await foreign_ids(world)
    assert (await mine(world, "owner", f"after={foreign_task}"))["items"] == []


async def test_note_and_complete_take_a_long_escaped_note_other_task_writes_keep_8192(
    world: HrWorld,
) -> None:
    """A 2000-character note sent with every character JSON-escaped (\\uXXXX pairs for an emoji)
    is far over 8192 bytes: complete and note take up to 32768; the other task writes keep 8192."""
    process = await custom_process(
        world,
        world.employees["staff"],
        step(title_en="Fill in details", owner_role="employee"),
        step(title_en="Order a desk"),
    )
    note = "\U0001f600" * 2000
    task, body = await at(world, process, 1)

    async def raw(who: str, path: str, payload: dict[str, Any]) -> httpx.Response:
        await world.call(who, "GET", MINE)  # opens the browser with its cookies
        content = json.dumps(payload, ensure_ascii=True).encode()
        assert len(content) > 8192
        return await world.browsers[who].post(
            path,
            content=content,
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": world.csrf[who],
                "Idempotency-Key": str(uuid4()),
                "Content-Type": "application/json",
            },
        )

    noted = await raw("staff", f"{TASKS}/{task}/note", {"note": note})
    assert noted.status_code == 200, noted.text
    completed = await raw(
        "staff", f"{TASKS}/{task}/complete", {"expected_version": 2, "note": note}
    )
    assert completed.status_code == 200, completed.text
    assert (await task_of(world, process, 1))["note"] == note
    other, other_body = await at(world, process, 2)
    skipped = await raw("owner", f"{TASKS}/{other}/skip", {**other_body, "reason": "x" * 9000})
    problem(skipped, 413, "hr_request_too_large")
    too_long = await raw("staff", f"{TASKS}/{task}/note", {"note": "\U0001f600" * 3000})
    problem(too_long, 413, "hr_request_too_large")
