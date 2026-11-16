"""The HR process routes (templates, processes, a person's processes) and the person.probation_set
operation, through the real HR router (disposable database). Reuses the HrWorld fixture of
tests/test_hr_persistence.py: staff and owner report to manager; outsider reports to nobody;
operator has no employee (people.manage when granted); reader is a read-only member; admin a
tenant admin with no HR authority.
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path
from typing import Any, get_args
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from csa.hr import processes as process_service
from csa.hr.documents import DocumentKind
from csa.hr.operations import _PEOPLE_REFUSALS
from csa.hr.process_models import AutoCheck
from csa.hr.processes import PROBLEMS, REFUSALS

from tests.conftest import requires_docker
from tests.test_hr_people import make_other_tenant_owner, make_people_manager, ok, person_row
from tests.test_hr_people_records import db_today, leave_body, versioned
from tests.test_hr_persistence import ORIGIN, HrWorld, hr_database, world
from tests.test_hr_process_instances import START_SQL, run_in_other_tenant, start_body
from tests.test_hr_process_templates import (
    ANCHOR,
    apply_in_other_tenant,
    create_body,
    department,
    step,
)

__all__ = ["hr_database", "world"]
pytestmark = [requires_docker, pytest.mark.asyncio]

HR = "/console/v1/hr"
TEMPLATES = f"{HR}/templates"
PROCESSES = f"{HR}/processes"
OPERATIONS = f"{HR}/operations"
MIGRATIONS = Path(__file__).resolve().parents[1] / "db/migrations"
ONBOARDING_STEPS = [
    step(title_en="Prepare the laptop"),
    step(title_en="Introduce the team", owner_role="manager"),
    step(title_en="Submit bank details", owner_role="employee", description_en="Synthetic"),
]
# What each viewer gets of a process header and of a task (0633).
HEADER_ALL = {"process_id", "kind", "name", "status", "employee_name"}
HEADER_DATES = {"started_at", "closed_at"}
HEADER_PROGRESS = {"employee_id", "tasks_total", "tasks_done"}
HEADER_SELF = {"required_total", "required_done", "open_tasks"}
HEADER_HR = {"template_id", "template_version", "anchor_dates", "started_by"}
TASK_BASE = {
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
TASK_OWN = TASK_BASE | {
    "description_en",
    "description_ms",
    "anchor",
    "approval_by",
    "auto_check",
    "completed_at",
    "note",
    "version",
}
TASK_HR = TASK_OWN | {
    "assignee_employee_id",
    "due_offset_days",
    "due_overridden",
    "completed_by",
    "approved_by",
    "approved_at",
}
# Text of a database error that must never reach a response.
DATABASE_TEXT = re.compile(
    r"violates|constraint|ERROR|DETAIL|CONTEXT|PL/pgSQL|relation|function|SELECT|secret"
)


def template_body(
    kind: str = "onboarding",
    name: str = "Synthetic onboarding",
    rules: dict[str, Any] | None = None,
    steps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "name": name,
        "rules": rules,
        "steps": steps if steps is not None else [step(anchor=ANCHOR.get(kind, "start_date"))],
    }


def problem(response: httpx.Response, status: int, detail: str) -> None:
    """A refusal is exactly `{"detail": <stable code>}`, never database text."""
    assert (response.status_code, response.json()) == (status, {"detail": detail}), response.text
    assert not DATABASE_TEXT.search(response.text), response.text


async def post_template(world: HrWorld, who: str = "owner", **body: Any) -> dict[str, Any]:
    response = await world.call(who, "POST", TEMPLATES, template_body(**body))
    assert response.status_code == 201, response.text
    return response.json()


async def get_ok(world: HrWorld, who: str, path: str) -> Any:
    response = await world.call(who, "GET", path)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


async def start(
    world: HrWorld,
    employee: UUID,
    kind: str = "onboarding",
    template: str | None = None,
    *,
    key: UUID | None = None,
) -> dict[str, Any]:
    body = {"employee_id": str(employee), "kind": kind, "template_id": template}
    response = await world.call("owner", "POST", PROCESSES, body, key=key)
    assert response.status_code == 201, response.text
    return response.json()


async def counts(world: HrWorld) -> tuple[int, int, int]:
    found = []
    for table in ("hr_process_templates", "hr_processes", "hr_operations"):
        rows = await world.q(
            f"SELECT count(*) AS n FROM {table} WHERE client_id=%s",  # type: ignore[arg-type]
            (world.tenant,),
        )
        found.append(rows[0]["n"])
    return found[0], found[1], found[2]


async def without_key(
    world: HrWorld, who: str, method: str, path: str, body: Any
) -> httpx.Response:
    """A write with a valid CSRF proof and no Idempotency-Key."""
    await world.call(who, "GET", f"{HR}/context")  # opens the signed-in browser
    headers = {"Origin": ORIGIN, "X-CSRF-Token": world.csrf[who]}
    return await world.browsers[who].request(method, path, json=body, headers=headers)


async def without_csrf(
    world: HrWorld, who: str, method: str, path: str, body: Any
) -> httpx.Response:
    """A request with an Idempotency-Key and no X-CSRF-Token header at all."""
    await world.call(who, "GET", f"{HR}/context")  # opens the signed-in browser
    headers = {"Origin": ORIGIN, "Idempotency-Key": str(uuid4())}
    return await world.browsers[who].request(method, path, json=body, headers=headers)


# ---- Templates --------------------------------------------------------------------------------


async def test_template_crud_round_trip_and_validation_errors_are_stable_codes(
    world: HrWorld,
) -> None:
    await make_people_manager(world)
    ops = await department(world, "Operations")
    manager = world.employees["manager"]
    steps = [
        step(
            title_en="Sign the contract",
            title_ms="Tandatangan kontrak",
            description_en="Bring the identity card",
            approval_by="hr",
            auto_check="document_uploaded:contract",
            reference_note="Synthetic reference",
            due_offset_days=-3,
        ),
        step(
            title_en="Probation review",
            anchor="probation_end",
            owner_role="person",
            owner_employee_id=str(manager),
            required=False,
        ),
    ]
    rules = {
        "department_id": str(ops),
        "employment_type": "full_time",
        "legal_entity_reference": " Synthetic Sdn Bhd ",
        "region": None,
    }
    key = uuid4()
    created = await world.call(
        "operator", "POST", TEMPLATES, template_body(rules=rules, steps=steps), key=key
    )
    assert created.status_code == 201, created.text
    template = created.json()["template_id"]
    assert created.json() == {
        "operation": "template.create",
        "template_id": template,
        "version": 1,
        "conflicts": [],
        "warnings": [],
        "status": "saved",
        "duplicate": False,
    }
    # The same key and body is the first receipt; nothing more is written.
    before = await counts(world)
    again = await world.call(
        "operator", "POST", TEMPLATES, template_body(rules=rules, steps=steps), key=key
    )
    assert again.status_code == 201 and again.json() == {**created.json(), "duplicate": True}
    assert await counts(world) == before
    problem(
        await world.call("operator", "POST", TEMPLATES, template_body(name="Other"), key=key),
        409,
        "idempotency_conflict",
    )

    stored_rules = {**rules, "legal_entity_reference": "Synthetic Sdn Bhd"}
    listed = await get_ok(world, "owner", TEMPLATES)
    [item] = listed["items"]
    assert {k: v for k, v in item.items() if k not in {"created_at", "updated_at"}} == {
        "template_id": template,
        "kind": "onboarding",
        "name": "Synthetic onboarding",
        "version": 1,
        "is_active": True,
        "rules": stored_rules,
        "step_count": 2,
    }
    got = await get_ok(world, "operator", f"{TEMPLATES}/{template}")
    assert set(got) == {
        "template_id",
        "kind",
        "name",
        "version",
        "is_active",
        "created_at",
        "updated_at",
        "rules",
        "steps",
        "conflicts",
        "warnings",
    }
    assert got["rules"] == stored_rules
    assert got["steps"] == [
        {
            "ordinal": 1,
            "title_en": "Sign the contract",
            "title_ms": "Tandatangan kontrak",
            "description_en": "Bring the identity card",
            "description_ms": None,
            "owner_role": "hr",
            "owner_employee_id": None,
            "anchor": "start_date",
            "due_offset_days": -3,
            "required": True,
            "approval_by": "hr",
            "auto_check": "document_uploaded:contract",
            "reference_note": "Synthetic reference",
        },
        {
            "ordinal": 2,
            "title_en": "Probation review",
            "title_ms": None,
            "description_en": None,
            "description_ms": None,
            "owner_role": "person",
            "owner_employee_id": str(manager),
            "anchor": "probation_end",
            "due_offset_days": 0,
            "required": False,
            "approval_by": None,
            "auto_check": None,
            "reference_note": None,
        },
    ]

    # Update: name, rules (null: the company default) and steps replaced, version + 1.
    update = {
        "expected_version": 1,
        "name": "Renamed onboarding",
        "rules": None,
        "steps": [step(title_en="Only step")],
        "is_active": True,
    }
    updated = await world.call("owner", "PUT", f"{TEMPLATES}/{template}", update)
    assert updated.status_code == 200, updated.text
    assert updated.json()["operation"] == "template.update"
    assert updated.json()["version"] == 2
    got = await get_ok(world, "owner", f"{TEMPLATES}/{template}")
    assert (got["name"], got["version"], len(got["steps"])) == ("Renamed onboarding", 2, 1)
    assert got["rules"] == dict.fromkeys(stored_rules)
    problem(
        await world.call("owner", "PUT", f"{TEMPLATES}/{template}", update),
        409,
        "template_changed",
    )
    deactivated = await world.call(
        "owner", "POST", f"{TEMPLATES}/{template}/deactivate", {"expected_version": 2}
    )
    assert deactivated.status_code == 200, deactivated.text
    assert (deactivated.json()["operation"], deactivated.json()["version"]) == (
        "template.deactivate",
        3,
    )
    got = await get_ok(world, "owner", f"{TEMPLATES}/{template}")
    assert (got["is_active"], got["version"]) == (False, 3)
    problem(
        await world.call(
            "owner", "POST", f"{TEMPLATES}/{template}/deactivate", {"expected_version": 2}
        ),
        409,
        "template_changed",
    )
    problem(await world.call("owner", "GET", f"{TEMPLATES}/{uuid4()}"), 404, "resource_unavailable")
    problem(
        await world.call("owner", "PUT", f"{TEMPLATES}/{uuid4()}", update),
        404,
        "resource_unavailable",
    )

    # Rules that need stored state come back from the database as stable codes; nothing is saved.
    stranger, foreign_department = uuid4(), await department(world, "Foreign", world.other_tenant)
    await world.q(
        "INSERT INTO hr_employees(client_id,id,display_name) VALUES (%s,%s,'Synthetic Stranger')",
        (world.other_tenant, stranger),
    )
    before = await counts(world)
    for body, code in (
        (template_body(steps=[step(anchor="last_day")]), "template_step_anchor_invalid"),
        (
            template_body(kind="offboarding", steps=[step(anchor="start_date")]),
            "template_step_anchor_invalid",
        ),
        (template_body(steps=[step(owner_role="person")]), "template_step_person_unavailable"),
        (
            template_body(steps=[step(owner_role="person", owner_employee_id=str(stranger))]),
            "template_step_person_unavailable",
        ),
        (
            template_body(steps=[step(owner_employee_id=str(manager))]),
            "template_step_owner_invalid",
        ),
        (
            template_body(rules={"department_id": str(foreign_department)}),
            "department_unavailable",
        ),
    ):
        problem(await world.call("owner", "POST", TEMPLATES, body), 409, code)
    # Shapes the database would refuse are 422 here first, without echoing the input.
    long = "Synthetic-secret-" + "x" * 2000
    for body in (
        template_body(name=""),
        template_body(name="n" * 121),
        template_body(kind="probation"),
        template_body(steps=[]),
        template_body(steps=[step()] * 41),
        template_body(steps=[step(title_en="")]),
        template_body(steps=[step(title_en="t" * 161)]),
        template_body(steps=[step(title_ms="t" * 161)]),
        template_body(steps=[step(description_en=long)]),
        template_body(steps=[step(description_ms=long)]),
        template_body(steps=[step(reference_note="r" * 501)]),
        template_body(steps=[step(due_offset_days=366)]),
        template_body(steps=[step(due_offset_days=-366)]),
        template_body(steps=[step(due_offset_days="1")]),
        template_body(steps=[step(due_offset_days=1.5)]),
        template_body(steps=[step(required="yes")]),
        template_body(steps=[step(owner_role="boss")]),
        template_body(steps=[step(anchor="birthday")]),
        template_body(steps=[step(approval_by="ceo")]),
        template_body(steps=[step(auto_check="document_uploaded:payslip")]),
        template_body(steps=[step(colour="red")]),
        template_body(rules={"employment_type": "temp"}),
        template_body(rules={"region": "johor"}),
        template_body(rules={"legal_entity_reference": "e" * 201}),
        template_body(rules={"category": "x"}),
        {**template_body(), "extra": True},
    ):
        response = await world.call("owner", "POST", TEMPLATES, body)
        assert response.status_code == 422, (body, response.text)
        assert response.json()["detail"] == "invalid_hr_input"
        assert "Synthetic-secret" not in response.text
    assert await counts(world) == before

    # Conflicts and warnings come back on the receipt.
    first = await post_template(world, name="Default A")
    second = await post_template(world, name="Default B")
    assert second["conflicts"] == [first["template_id"]]
    leaving = await post_template(
        world,
        kind="offboarding",
        steps=[
            step(anchor="last_day"),
            step(anchor="last_day", owner_role="employee", due_offset_days=3),
        ],
    )
    assert leaving["warnings"] == [{"code": "step_after_last_day", "ordinal": 2}]
    got = await get_ok(world, "owner", f"{TEMPLATES}/{leaving['template_id']}")
    assert got["warnings"] == [{"code": "step_after_last_day", "ordinal": 2}]

    # A full template (40 long steps in two languages) is well over the 8 KB HR default.
    full = [
        step(title_en=f"Step {n}", description_en="d" * 2000, description_ms="m" * 2000)
        for n in range(40)
    ]
    big = await post_template(world, name="Full template", steps=full)
    got = await get_ok(world, "owner", f"{TEMPLATES}/{big['template_id']}")
    assert len(got["steps"]) == 40


async def test_template_update_without_rules_is_422(world: HrWorld) -> None:
    created = await post_template(
        world, rules={"employment_type": "full_time"}, steps=[step(title_en="First")]
    )
    path = f"{TEMPLATES}/{created['template_id']}"
    body = {"expected_version": 1, "name": "Renamed", "steps": [step(title_en="Second")]}
    response = await world.call("owner", "PUT", path, body)
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": "invalid_hr_input", "invalid_fields": 1}
    got = await get_ok(world, "owner", path)
    assert (got["name"], got["version"], got["rules"]["employment_type"]) == (
        "Synthetic onboarding",
        1,
        "full_time",
    )


# ---- Processes --------------------------------------------------------------------------------


async def test_start_and_cancel_process_routes(world: HrWorld) -> None:
    staff, outsider = world.employees["staff"], world.employees["outsider"]
    template = (await post_template(world, steps=ONBOARDING_STEPS))["template_id"]
    key = uuid4()
    receipt = await start(world, staff, key=key)
    process = receipt["process_id"]
    assert receipt == {
        "operation": "process.start",
        "process_id": process,
        "employee_id": str(staff),
        "kind": "onboarding",
        "template_id": template,
        "status": "saved",
        "duplicate": False,
    }
    before = await counts(world)
    assert await start(world, staff, key=key) == {**receipt, "duplicate": True}
    assert await counts(world) == before

    learning = (await person_row(world, staff))["department_id"]
    page = await get_ok(world, "owner", PROCESSES)
    [row] = page["items"]
    assert page["next_cursor"] is None
    assert {k: v for k, v in row.items() if k != "started_at"} == {
        "process_id": process,
        "employee_id": str(staff),
        "employee_name": "Synthetic Staff",
        "department_id": str(learning),
        "kind": "onboarding",
        "name": "Synthetic onboarding",
        "status": "active",
        "closed_at": None,
        "tasks_total": 3,
        "tasks_done": 0,
        "next_due_on": None,
        "overdue": False,
    }
    for query, expected in (
        ("kind=onboarding", [process]),
        ("kind=custom", []),
        ("status=active", [process]),
        ("status=cancelled", []),
        ("overdue=true", []),
        ("overdue=false", [process]),
        (f"department={learning}", [process]),
        (f"department={uuid4()}", []),
        (f"owner={world.employees['manager']}", [process]),
        (f"owner={outsider}", []),
    ):
        found = await get_ok(world, "owner", f"{PROCESSES}?{query}")
        assert [r["process_id"] for r in found["items"]] == expected, query

    detail = await get_ok(world, "owner", f"{PROCESSES}/{process}")
    assert detail["view"] == "hr"
    assert set(detail["process"]) == HEADER_ALL | HEADER_DATES | HEADER_PROGRESS | HEADER_HR
    assert detail["process"]["template_id"] == template
    assert detail["process"]["anchor_dates"] == {
        "start_date": None,
        "last_day": None,
        "probation_end": None,
    }
    assert detail["process"]["started_by"] == str(world.users["owner"])
    assert [set(task) for task in detail["tasks"]] == [TASK_HR] * 3
    assert [t["assignee_employee_id"] for t in detail["tasks"]] == [
        None,
        str(world.employees["manager"]),
        str(staff),
    ]
    started = [e for e in detail["events"] if e["action"] == "process.started"]
    assert [e["detail"] for e in started] == [
        {"kind": "onboarding", "template_id": template, "template_version": 1}
    ]
    assert sorted(e["action"] for e in detail["events"]) == [
        "process.started",
        "task.created",
        "task.created",
        "task.created",
    ]
    created = [e["detail"] for e in detail["events"] if e["action"] == "task.created"]
    assert {"owner_role": "hr", "assignee_employee_id": None, "due_on": None} in created

    # Refusals of the start.
    custom = (await post_template(world, kind="custom", name="Synthetic plan"))["template_id"]
    for body, status, code in (
        ({"employee_id": str(staff), "kind": "custom"}, 409, "process_template_required"),
        ({"employee_id": str(staff), "kind": "offboarding"}, 409, "person_not_leaving"),
        ({"employee_id": str(uuid4()), "kind": "onboarding"}, 404, "resource_unavailable"),
        (
            {"employee_id": str(staff), "kind": "custom", "template_id": template},
            409,
            "template_unavailable",
        ),
        (
            {"employee_id": str(staff), "kind": "onboarding", "template_id": custom},
            409,
            "template_unavailable",
        ),
    ):
        problem(await world.call("owner", "POST", PROCESSES, body), status, code)
    today = await db_today(world)
    await ok(world, "owner", await leave_body(world, outsider, today + timedelta(days=10)))
    problem(
        await world.call(
            "owner", "POST", PROCESSES, {"employee_id": str(outsider), "kind": "offboarding"}
        ),
        409,
        "process_no_template",
    )
    for body in (
        {"employee_id": str(staff), "kind": "probation"},
        {"employee_id": "not-a-uuid", "kind": "custom"},
        {"kind": "custom"},
        {"employee_id": str(staff), "kind": "custom", "note": "x"},
    ):
        response = await world.call("owner", "POST", PROCESSES, body)
        assert (response.status_code, response.json()["detail"]) == (422, "invalid_hr_input")

    # Cancel.
    cancel_key = uuid4()
    cancelled = await world.call("owner", "POST", f"{PROCESSES}/{process}/cancel", key=cancel_key)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json() == {
        "operation": "process.cancel",
        "process_id": process,
        "status": "saved",
        "duplicate": False,
    }
    detail = await get_ok(world, "owner", f"{PROCESSES}/{process}")
    assert detail["process"]["status"] == "cancelled"
    assert detail["process"]["closed_at"] is not None
    assert {t["status"] for t in detail["tasks"]} == {"skipped"}
    replay = await world.call("owner", "POST", f"{PROCESSES}/{process}/cancel", key=cancel_key)
    assert replay.json() == {**cancelled.json(), "duplicate": True}
    problem(
        await world.call("owner", "POST", f"{PROCESSES}/{process}/cancel"), 409, "process_closed"
    )
    problem(
        await world.call("owner", "POST", f"{PROCESSES}/{uuid4()}/cancel"),
        404,
        "resource_unavailable",
    )
    problem(await world.call("owner", "GET", f"{PROCESSES}/{uuid4()}"), 404, "resource_unavailable")

    # Paging: newest first, a cursor while more remain.
    plans = [(await start(world, outsider, "custom", custom))["process_id"] for _ in range(3)]
    first = await get_ok(world, "owner", f"{PROCESSES}?kind=custom&limit=2")
    assert [r["process_id"] for r in first["items"]] == plans[::-1][:2]
    assert first["next_cursor"] == plans[1]
    rest = await get_ok(world, "owner", f"{PROCESSES}?kind=custom&limit=2&after={plans[1]}")
    assert [r["process_id"] for r in rest["items"]] == [plans[0]]
    assert rest["next_cursor"] is None
    for query, status, code in (
        ("colour=red", 400, "invalid_hr_query"),
        ("kind=custom&kind=onboarding", 400, "invalid_hr_query"),
        ("kind=probation", 422, "invalid_hr_input"),
        ("status=open", 422, "invalid_hr_input"),
        ("limit=0", 422, "invalid_hr_input"),
        ("limit=201", 422, "invalid_hr_input"),
        ("owner=nobody", 422, "invalid_hr_input"),
    ):
        response = await world.call("owner", "GET", f"{PROCESSES}?{query}")
        assert (response.status_code, response.json()["detail"]) == (status, code), query


async def test_probation_operation_via_operations_route(world: HrWorld) -> None:
    staff = world.employees["staff"]
    steps = [step(title_en="Probation review", anchor="probation_end", due_offset_days=-7)]
    await post_template(world, steps=steps)
    process = (await start(world, staff))["process_id"]
    detail = await get_ok(world, "owner", f"{PROCESSES}/{process}")
    assert detail["tasks"][0]["due_on"] is None

    version = (await person_row(world, staff))["version"]
    body = await versioned(world, "person.probation_set", staff, probation_end="2027-03-01")
    key = uuid4()
    saved = await world.call("owner", "POST", OPERATIONS, body, key=key)
    assert saved.status_code == 200, saved.text
    assert saved.json() == {
        "operation": "person.probation_set",
        "resource_id": str(staff),
        "version": version + 1,
        "status": "saved",
        "duplicate": False,
    }
    replay = await world.call("owner", "POST", OPERATIONS, body, key=key)
    assert replay.json() == {**saved.json(), "duplicate": True}
    assert (await person_row(world, staff))["version"] == version + 1
    # The task counted from the probation end moved with it; the date reads back.
    detail = await get_ok(world, "owner", f"{PROCESSES}/{process}")
    assert detail["tasks"][0]["due_on"] == "2027-02-22"
    person = await get_ok(world, "owner", f"{HR}/people/{staff}/processes")
    assert person["anchor_dates"]["probation_end"] == "2027-03-01"

    problem(await world.call("owner", "POST", OPERATIONS, body), 409, "person_changed")
    for bad in (
        {k: v for k, v in body.items() if k != "probation_end"},
        {**body, "probation_end": "not-a-date"},
        {**body, "expected_version": "1"},
    ):
        response = await world.call("owner", "POST", OPERATIONS, bad)
        assert (response.status_code, response.json()["detail"]) == (422, "invalid_hr_input")
    cleared = await ok(
        world, "owner", await versioned(world, "person.probation_set", staff, probation_end=None)
    )
    assert cleared["version"] == version + 2
    person = await get_ok(world, "owner", f"{HR}/people/{staff}/processes")
    assert person["anchor_dates"]["probation_end"] is None
    detail = await get_ok(world, "owner", f"{PROCESSES}/{process}")
    assert detail["tasks"][0]["due_on"] is None
    # Only the HR team, and only people of this company.
    stale_free = await versioned(world, "person.probation_set", staff, probation_end="2027-04-01")
    for who in ("staff", "manager", "admin"):
        problem(await world.call(who, "POST", OPERATIONS, stale_free), 404, "resource_unavailable")
    problem(
        await world.call("owner", "POST", OPERATIONS, {**stale_free, "employee_id": str(uuid4())}),
        404,
        "resource_unavailable",
    )
    await make_people_manager(world)
    assert (await ok(world, "operator", stale_free))["version"] == version + 3


# ---- Who may do what --------------------------------------------------------------------------


async def test_every_route_requires_csrf_and_idempotency_where_it_writes(world: HrWorld) -> None:
    template = (await post_template(world, steps=ONBOARDING_STEPS))["template_id"]
    process = (await start(world, world.employees["staff"]))["process_id"]
    update = {"expected_version": 1, "name": "Renamed", "rules": None, "steps": [step()]}
    probation = await versioned(
        world, "person.probation_set", world.employees["staff"], probation_end="2027-03-01"
    )
    writes: list[tuple[str, str, Any]] = [
        ("POST", TEMPLATES, template_body(name="Another")),
        ("PUT", f"{TEMPLATES}/{template}", update),
        ("POST", f"{TEMPLATES}/{template}/deactivate", {"expected_version": 1}),
        ("POST", PROCESSES, {"employee_id": str(world.employees["staff"]), "kind": "onboarding"}),
        ("POST", f"{PROCESSES}/{process}/cancel", None),
        ("POST", OPERATIONS, probation),
    ]
    before = await counts(world)
    for method, path, body in writes:
        wrong_csrf = await world.call(
            "owner", method, path, body, headers={"X-CSRF-Token": "synthetic-wrong"}
        )
        problem(wrong_csrf, 403, "invalid csrf token")
        problem(await without_csrf(world, "owner", method, path, body), 403, "invalid csrf token")
        no_key = await without_key(world, "owner", method, path, body)
        assert (no_key.status_code, no_key.json()["detail"]) == (422, "invalid_hr_input"), path
        problem(await world.call("reader", method, path, body), 403, "read_only_session")
    assert await counts(world) == before
    got = await get_ok(world, "owner", f"{TEMPLATES}/{template}")
    assert (got["version"], got["is_active"]) == (1, True)
    assert (await get_ok(world, "owner", f"{PROCESSES}/{process}"))["process"]["status"] == "active"
    # The reads need no CSRF proof: a wrong one or none at all.
    for path in (
        TEMPLATES,
        f"{TEMPLATES}/{template}",
        PROCESSES,
        f"{PROCESSES}/{process}",
        f"{HR}/people/{world.employees['staff']}/processes",
    ):
        response = await world.call("owner", "GET", path, headers={"X-CSRF-Token": "x"})
        assert response.status_code == 200, path
        response = await without_csrf(world, "owner", "GET", path, None)
        assert response.status_code == 200, path


async def test_non_hr_and_other_tenant_are_refused(world: HrWorld) -> None:
    """Never 403 for a caller the route does not serve: 404, as every HR route (CONSOLE_API.md). 403
    is only for a read-only session's write and for naming another tenant in client_id."""
    staff = world.employees["staff"]
    template = (await post_template(world, steps=ONBOARDING_STEPS))["template_id"]
    process = (await start(world, staff))["process_id"]
    update = {"expected_version": 1, "name": "Renamed", "rules": None, "steps": [step()]}
    before = await counts(world)
    for who in ("staff", "manager", "outsider", "admin"):
        for method, path, body in (
            ("GET", TEMPLATES, None),
            ("GET", f"{TEMPLATES}/{template}", None),
            ("POST", TEMPLATES, template_body()),
            ("PUT", f"{TEMPLATES}/{template}", update),
            ("POST", f"{TEMPLATES}/{template}/deactivate", {"expected_version": 1}),
            ("POST", PROCESSES, {"employee_id": str(staff), "kind": "onboarding"}),
            ("POST", f"{PROCESSES}/{process}/cancel", None),
        ):
            problem(await world.call(who, method, path, body), 404, "resource_unavailable")
    for who in ("reader", "outsider", "admin"):
        assert (await get_ok(world, who, PROCESSES))["items"] == []
        problem(await world.call(who, "GET", f"{PROCESSES}/{process}"), 404, "resource_unavailable")
        problem(
            await world.call(who, "GET", f"{HR}/people/{staff}/processes"),
            404,
            "resource_unavailable",
        )
    problem(await world.call("reader", "GET", TEMPLATES), 404, "resource_unavailable")
    assert await counts(world) == before

    # Another tenant's template, process and person are not found from this tenant.
    await make_other_tenant_owner(world)
    foreign_template = (await apply_in_other_tenant(world, create_body(kind="custom")))[
        "template_id"
    ]
    foreign_process = (
        await run_in_other_tenant(
            world, START_SQL, start_body(staff, "custom", UUID(foreign_template))
        )
    )["process_id"]
    foreign_person = uuid4()
    await world.q(
        "INSERT INTO hr_employees(client_id,id,display_name) VALUES (%s,%s,'Synthetic Stranger')",
        (world.other_tenant, foreign_person),
    )
    for method, path, body, status, code in (
        ("GET", f"{TEMPLATES}/{foreign_template}", None, 404, "resource_unavailable"),
        ("PUT", f"{TEMPLATES}/{foreign_template}", update, 404, "resource_unavailable"),
        (
            "POST",
            f"{TEMPLATES}/{foreign_template}/deactivate",
            {"expected_version": 1},
            404,
            "resource_unavailable",
        ),
        (
            "POST",
            PROCESSES,
            {"employee_id": str(staff), "kind": "custom", "template_id": foreign_template},
            409,
            "template_unavailable",
        ),
        (
            "POST",
            PROCESSES,
            {"employee_id": str(foreign_person), "kind": "onboarding"},
            404,
            "resource_unavailable",
        ),
        ("GET", f"{PROCESSES}/{foreign_process}", None, 404, "resource_unavailable"),
        ("POST", f"{PROCESSES}/{foreign_process}/cancel", None, 404, "resource_unavailable"),
        ("GET", f"{HR}/people/{foreign_person}/processes", None, 404, "resource_unavailable"),
    ):
        problem(await world.call("owner", method, path, body), status, code)
    assert [r["process_id"] for r in (await get_ok(world, "owner", PROCESSES))["items"]] == [
        process
    ]
    assert [t["template_id"] for t in (await get_ok(world, "owner", TEMPLATES))["items"]] == [
        template
    ]
    problem(
        await world.call("owner", "GET", f"{TEMPLATES}?client_id={world.other_tenant}"),
        403,
        "tenant_not_authorized",
    )
    foreign = await world.q(
        "SELECT t.version, t.is_active, p.status FROM hr_process_templates t"
        " JOIN hr_processes p ON p.client_id=t.client_id AND p.template_id=t.id"
        " WHERE t.client_id=%s",
        (world.other_tenant,),
    )
    assert foreign == [{"version": 1, "is_active": True, "status": "active"}]


async def test_manager_person_and_assignee_views_omit_hidden_fields(world: HrWorld) -> None:
    staff, _manager, outsider = (world.employees[n] for n in ("staff", "manager", "outsider"))
    await post_template(world, steps=ONBOARDING_STEPS)
    process = (await start(world, staff))["process_id"]

    as_manager = await get_ok(world, "manager", f"{PROCESSES}/{process}")
    assert as_manager["view"] == "manager"
    assert "events" not in as_manager
    assert set(as_manager["process"]) == HEADER_ALL | HEADER_DATES | HEADER_PROGRESS
    # The manager's own task in full (without HR's fields), the others as titles and dates.
    assert [(t["mine"], set(t)) for t in as_manager["tasks"]] == [
        (False, TASK_BASE),
        (True, TASK_OWN),
        (False, TASK_BASE),
    ]
    assert [r["process_id"] for r in (await get_ok(world, "manager", PROCESSES))["items"]] == [
        process
    ]

    as_self = await get_ok(world, "staff", f"{PROCESSES}/{process}")
    assert as_self["view"] == "self"
    assert "events" not in as_self
    assert set(as_self["process"]) == HEADER_ALL | HEADER_DATES | HEADER_SELF
    assert (
        as_self["process"]["required_total"],
        as_self["process"]["required_done"],
        as_self["process"]["open_tasks"],
    ) == (3, 0, 3)
    [own] = as_self["tasks"]
    assert (own["title_en"], own["mine"], set(own)) == ("Submit bank details", True, TASK_OWN)
    assert own["description_en"] == "Synthetic"

    # An assignee of a custom process about someone else sees the header and their own task.
    plan = (
        await post_template(
            world,
            kind="custom",
            name="Synthetic plan",
            steps=[
                step(
                    title_en="Coach",
                    anchor="start_date",
                    owner_role="person",
                    owner_employee_id=str(staff),
                ),
                step(title_en="Review", anchor="start_date"),
            ],
        )
    )["template_id"]
    custom = (await start(world, outsider, "custom", plan))["process_id"]
    as_assignee = await get_ok(world, "staff", f"{PROCESSES}/{custom}")
    assert as_assignee["view"] == "assignee"
    assert set(as_assignee["process"]) == HEADER_ALL
    assert [(t["title_en"], set(t)) for t in as_assignee["tasks"]] == [("Coach", TASK_OWN)]
    problem(
        await world.call("manager", "GET", f"{PROCESSES}/{custom}"), 404, "resource_unavailable"
    )

    # The person view of the profile: anchor dates for HR and the person, the template flag for HR.
    as_hr = await get_ok(world, "owner", f"{HR}/people/{staff}/processes")
    assert set(as_hr) == {
        "view",
        "employee_id",
        "processes",
        "anchor_dates",
        "no_matching_template",
    }
    assert as_hr["no_matching_template"] == {"onboarding": False, "offboarding": False}
    [summary] = as_hr["processes"]
    assert {k: v for k, v in summary.items() if k != "started_at"} == {
        "process_id": process,
        "kind": "onboarding",
        "name": "Synthetic onboarding",
        "status": "active",
        "closed_at": None,
        "tasks_total": 3,
        "tasks_done": 0,
        "next_task": {"title_en": "Prepare the laptop", "title_ms": None, "due_on": None},
    }
    by_manager = await get_ok(world, "manager", f"{HR}/people/{staff}/processes")
    assert set(by_manager) == {"view", "employee_id", "processes"}
    assert by_manager["view"] == "manager"
    by_self = await get_ok(world, "staff", f"{HR}/people/{staff}/processes")
    assert set(by_self) == {"view", "employee_id", "processes", "anchor_dates"}
    assert by_self["processes"][0]["next_task"]["title_en"] == "Submit bank details"
    outsider_hr = await get_ok(world, "owner", f"{HR}/people/{outsider}/processes")
    assert [p["kind"] for p in outsider_hr["processes"]] == ["custom"]
    by_outsider = await get_ok(world, "outsider", f"{HR}/people/{outsider}/processes")
    assert by_outsider["processes"] == []  # a custom process about the person stays hidden
    assert by_outsider["anchor_dates"] == {
        "start_date": None,
        "last_day": None,
        "probation_end": None,
    }


# ---- No database text -------------------------------------------------------------------------


def _raised_codes() -> set[str]:
    found: set[str] = set()
    for number in ("0631", "0632", "0633"):
        [path] = MIGRATIONS.glob(f"{number}_*.sql")
        found |= set(re.findall(r"RAISE EXCEPTION '([a-z_]+)'", path.read_text()))
    return found


async def test_responses_never_carry_database_text(
    world: HrWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every code the process migrations raise has a problem code, and every problem code a
    # sentence; a database code means the same on these routes and on POST /operations.
    raised = _raised_codes()
    assert raised and raised <= set(REFUSALS), raised - set(REFUSALS)
    assert set(REFUSALS.values()) <= set(PROBLEMS), set(REFUSALS.values()) - set(PROBLEMS)
    assert all(sentence.endswith(".") and len(sentence) <= 120 for sentence in PROBLEMS.values())
    assert not any(code.startswith("hr_") for code in REFUSALS.values())
    for code, problem_code in REFUSALS.items():
        assert _PEOPLE_REFUSALS.get(code, problem_code) == problem_code, code
    assert _PEOPLE_REFUSALS["hr_probation_end_invalid"] == "probation_end_invalid"
    # The automatic document checks are the document kinds.
    checks = {c.removeprefix("document_uploaded:") for c in get_args(AutoCheck) if ":" in c}
    assert checks == set(get_args(DocumentKind))

    # A database error that is not a known refusal is a bare 503, whatever its text says.
    template = (await post_template(world))["template_id"]

    class Broken:
        async def fetch_one(self, *args: Any, **kwargs: Any) -> Any:
            raise psycopg.errors.CheckViolation(
                'new row for relation "hr_process_templates" violates check constraint '
                '"hr_process_templates_version_check" DETAIL: Failing row contains (secret).'
            )

        async def fetch_all(self, *args: Any, **kwargs: Any) -> Any:
            raise psycopg.errors.UndefinedFunction("function hr_template_rows(uuid) does not exist")

    monkeypatch.setattr(process_service, "sql", Broken())
    for method, path, body in (
        ("POST", TEMPLATES, template_body()),
        ("POST", f"{TEMPLATES}/{template}/deactivate", {"expected_version": 1}),
        ("GET", TEMPLATES, None),
        ("GET", f"{PROCESSES}/{uuid4()}", None),
    ):
        problem(await world.call("owner", method, path, body), 503, "hr_database_unavailable")
    monkeypatch.undo()
    # Unreadable input is never repeated, refused writes name only their code.
    response = await world.call(
        "owner", "POST", PROCESSES, {"employee_id": "Synthetic-secret-id", "kind": "custom"}
    )
    assert response.status_code == 422 and "Synthetic-secret" not in response.text
    problem(
        await world.call(
            "owner",
            "POST",
            PROCESSES,
            {"employee_id": str(uuid4()), "kind": "custom", "template_id": template},
        ),
        404,
        "resource_unavailable",
    )


async def test_step_descriptions_keep_line_breaks_through_template_and_process(
    world: HrWorld,
) -> None:
    text = "Bring:\n- the identity card\n- a bank statement"
    steps = [
        step(title_en="Documents", description_en=text, description_ms="Bawa:\r\n- kad pengenalan")
    ]
    template = (await post_template(world, steps=steps))["template_id"]
    got = await get_ok(world, "owner", f"{TEMPLATES}/{template}")
    assert (got["steps"][0]["description_en"], got["steps"][0]["description_ms"]) == (
        text,
        "Bawa:\n- kad pengenalan",
    )
    process = (await start(world, world.employees["staff"]))["process_id"]
    detail = await get_ok(world, "owner", f"{PROCESSES}/{process}")
    assert detail["tasks"][0]["description_en"] == text
    before = await counts(world)
    for bad in ("tab\there", "nul\u0000here", "lone\rreturn"):
        response = await world.call(
            "owner", "POST", TEMPLATES, template_body(steps=[step(description_en=bad)])
        )
        assert (response.status_code, response.json()["detail"]) == (422, "invalid_hr_input")
    response = await world.call(
        "owner", "POST", TEMPLATES, template_body(steps=[step(title_en="two\nlines")])
    )
    assert response.status_code == 422
    assert await counts(world) == before
