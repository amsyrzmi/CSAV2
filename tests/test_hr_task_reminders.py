"""Migration 0635: task reminders (hr_tasks_due, hr_task_reminder_put through csa.hr.reminders.emit)
and the worker round hr_tasks_round (disposable database). Reuses the HrWorld fixture of
tests/test_hr_persistence.py and the helpers of tests/test_hr_process_tasks.py. In the fixture,
staff and owner report to manager; outsider reports to nobody; owner is the HR owner; operator has
no employee (people.manage when granted); reader is a read-only member; admin a tenant admin with no
HR authority. The clock is moved through the round's `today` (hr_tasks_due's date argument), never
by sleeping.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import typing
from datetime import date, timedelta
from pathlib import Path
from typing import Any, LiteralString
from uuid import UUID, uuid4

import psycopg
import pytest
from csa.core.db import q
from csa.core.tenancy import tx
from csa.hr import reminders
from csa.hr.operation_records import NotificationRecord
from psycopg.rows import dict_row
from scripts import migrate
from structlog.testing import capture_logs

from tests.conftest import requires_docker
from tests.test_hr_people import make_other_tenant_owner, make_people_manager
from tests.test_hr_people_records import finish_now
from tests.test_hr_persistence import HrWorld, hr_database, world
from tests.test_hr_process_instances import (
    CANCEL_SQL,
    START_SQL,
    insert_employee,
    run,
    run_in_other_tenant,
    start_body,
)
from tests.test_hr_process_reads import mine
from tests.test_hr_process_tasks import act, custom, versioned_task
from tests.test_hr_process_templates import apply_in_other_tenant, create_body, step

__all__ = ["hr_database", "world"]
pytestmark = [requires_docker, pytest.mark.asyncio]

DUE: LiteralString = "SELECT * FROM public.hr_tasks_due(%s::uuid, %s::date)"
EARLIER_KINDS = {"workflow_review", "training_review", "interview_upcoming"}
TASK_KINDS = {"task_assigned", "task_due_soon", "task_due", "task_overdue"}
D = date(2026, 12, 10)  # a task's due date; the round's "today" moves around it


async def due_rows(
    world: HrWorld, on: date, *, tenant: UUID | None = None, setting: UUID | None = None
) -> list[dict[str, Any]]:
    """hr_tasks_due(tenant, on) in a system transaction of `setting` (no HR user)."""
    async with tx(world.service.pool, str(setting or world.tenant)) as conn:
        cursor = await conn.execute(DUE, (tenant or world.tenant, on))
        return await cursor.fetchall()


async def reminder_rows(world: HrWorld, tenant: UUID | None = None) -> list[dict[str, Any]]:
    return await world.q(
        "SELECT subject_id, reminder_key, kind, resource_id, due_on, subject FROM hr_notifications"
        " WHERE client_id=%s AND kind LIKE 'task\\_%%' ORDER BY created_at, id",
        (tenant or world.tenant,),
    )


async def sent(world: HrWorld, user: str, task: UUID | None = None) -> list[str]:
    """The task reminder kinds `user` holds in world.tenant, oldest first."""
    return [
        r["kind"]
        for r in await reminder_rows(world)
        if r["subject_id"] == world.users[user] and (task is None or r["resource_id"] == task)
    ]


async def round_on(world: HrWorld, on: date) -> tuple[int, int]:
    return await reminders.hr_tasks_round(world.service.pool, today=on)


async def set_due(world: HrWorld, task: UUID, day: date | None) -> None:
    await world.q(
        "UPDATE hr_process_tasks SET due_on=%s WHERE client_id=%s AND id=%s",
        (day, world.tenant, task),
    )


async def one_task(world: HrWorld, employee: UUID, **over: Any) -> tuple[UUID, UUID]:
    """A custom process for `employee` with one step due on D: (process, task)."""
    pid = await custom(world, employee, step(title_en="Synthetic step", **over))
    task = (await versioned_task(world, pid, 1))["task_id"]
    await set_due(world, task, D)
    return pid, task


async def membership(world: HrWorld, user: str, enabled: bool) -> None:
    await world.q(
        "UPDATE console_memberships SET is_enabled=%s WHERE user_id=%s AND client_id=%s",
        (enabled, world.users[user], world.tenant),
    )


def kinds_check(definition: str) -> set[str]:
    return set(re.findall(r"'([^']+)'", definition))


async def set_role(world: HrWorld, user: str, role: str) -> None:
    await world.q(
        "UPDATE console_memberships SET role=%s WHERE user_id=%s AND client_id=%s",
        (role, world.users[user], world.tenant),
    )


# ---- The migration -----------------------------------------------------------------------------


async def test_constraint_extended_keeps_every_earlier_kind(world: HrWorld) -> None:
    [row] = await world.q(
        "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint"
        " WHERE conrelid='public.hr_notifications'::regclass"
        " AND conname='hr_notifications_kind_check'"
    )
    allowed = kinds_check(row["d"])
    assert allowed == EARLIER_KINDS | TASK_KINDS
    # The Home read's model lists exactly the kinds the database allows.
    assert set(typing.get_args(NotificationRecord.model_fields["kind"].annotation)) == allowed
    columns = await world.q(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns"
        " WHERE table_name='hr_notifications' AND column_name IN ('due_on','subject')"
        " ORDER BY column_name"
    )
    assert columns == [
        {"column_name": "due_on", "data_type": "date", "is_nullable": "YES"},
        {"column_name": "subject", "data_type": "text", "is_nullable": "YES"},
    ]
    # An unknown kind is still refused.
    with pytest.raises(psycopg.errors.CheckViolation):
        await world.q(
            "INSERT INTO hr_notifications(client_id,subject_id,id,reminder_key,kind,resource_id)"
            " VALUES (%s,%s,%s,'synthetic','task_party',%s)",
            (world.tenant, world.users["staff"], uuid4(), uuid4()),
        )


async def snapshot(world: HrWorld) -> list[Any]:
    constraints = await world.q(
        "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint"
        " WHERE conrelid='public.hr_notifications'::regclass ORDER BY conname"
    )
    columns = await world.q(
        "SELECT column_name, data_type FROM information_schema.columns"
        " WHERE table_name='hr_notifications' ORDER BY ordinal_position"
    )
    functions = await world.q(
        "SELECT p.oid::regprocedure::text AS f, pg_get_functiondef(p.oid) AS d,"
        " p.proacl::text AS acl"
        " FROM pg_proc p WHERE p.proname IN ('hr_tasks_due','hr_tasks_due_all',"
        "'hr_tasks_due_tenants','hr_task_reminder_put','hr_task_reminder_user',"
        "'hr_task_rows_mine') ORDER BY 1"
    )
    return [constraints, columns, functions]


async def test_rerun_of_0635_changes_nothing(world: HrWorld) -> None:
    before = await snapshot(world)
    assert len(before[2]) == 6
    [path] = [p for v, p in migrate.discover_migrations() if v == "0635"]
    async with world.owner_pool.connection() as conn:
        await conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
    assert await snapshot(world) == before


async def test_a_kind_0635_does_not_know_survives_the_rebuild(world: HrWorld) -> None:
    """The live-list method: a kind added by a later migration (an Inbox-lane one merged after
    0635, gap-filled by the migrator) is kept when 0635 rebuilds the constraint. Run in a
    transaction that is rolled back, so the shared schema is untouched."""
    [path] = [p for v, p in migrate.discover_migrations() if v == "0635"]
    later = ["workflow_review", "training_review", "interview_upcoming", "synthetic_inbox_kind"]
    # task_due is missing, so 0635 must rebuild; NOT VALID keeps the existing task_due rows.
    listed = ", ".join(f"'{k}'" for k in [*later, "task_assigned", "task_due_soon", "task_overdue"])
    async with world.owner_pool.connection() as conn:
        async with conn.transaction(force_rollback=True):
            await conn.execute(
                "ALTER TABLE public.hr_notifications DROP CONSTRAINT hr_notifications_kind_check"
            )
            await conn.execute(
                "ALTER TABLE public.hr_notifications ADD CONSTRAINT hr_notifications_kind_check"
                f" CHECK (kind IN ({listed})) NOT VALID"  # type: ignore[arg-type]
            )
            await conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    "SELECT pg_get_constraintdef(oid) AS d, convalidated FROM pg_constraint"
                    " WHERE conrelid='public.hr_notifications'::regclass"
                    " AND conname='hr_notifications_kind_check'"
                )
                [rebuilt] = await cursor.fetchall()
    assert kinds_check(rebuilt["d"]) == EARLIER_KINDS | TASK_KINDS | {"synthetic_inbox_kind"}
    assert rebuilt["convalidated"] is True
    # Rolled back: the live constraint is the migrated one again.
    [row] = await world.q(
        "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint"
        " WHERE conrelid='public.hr_notifications'::regclass"
        " AND conname='hr_notifications_kind_check'"
    )
    assert kinds_check(row["d"]) == EARLIER_KINDS | TASK_KINDS


async def test_functions_are_pinned_definers_and_only_the_entry_points_are_granted(
    world: HrWorld,
) -> None:
    for signature, public in (
        ("hr_tasks_due(uuid,date)", True),
        ("hr_tasks_due_tenants(date)", True),
        ("hr_task_reminder_put(uuid,uuid,text,uuid,text,date,text)", True),
        ("hr_tasks_due_all(uuid,date)", False),
        ("hr_task_reminder_user(uuid,uuid)", False),
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


# ---- The round ---------------------------------------------------------------------------------


async def test_round_writes_each_reminder_once_per_day(world: HrWorld) -> None:
    _, task = await one_task(world, world.employees["staff"], owner_role="employee")
    first = await round_on(world, D)
    second = await round_on(world, D)
    assert first[1] >= 2 and second[1] == 0
    rows = await reminder_rows(world)
    assert [(r["subject_id"], r["kind"]) for r in rows] == [
        (world.users["staff"], "task_assigned"),
        (world.users["staff"], "task_due"),
    ]
    assigned, due = rows
    assert (due["resource_id"], due["due_on"], due["subject"]) == (
        task,
        D,
        "Due today: Synthetic step",
    )
    assert assigned["subject"] == "New task: Synthetic step"
    # The dedupe key names the task, the reminder and the recipient.
    assert str(task) in due["reminder_key"] and str(world.users["staff"]) in due["reminder_key"]
    # Nothing is left due for this day once written.
    assert await due_rows(world, D) == []


async def test_cadence_due_soon_due_overdue_daily_then_weekly(world: HrWorld) -> None:
    _, task = await one_task(world, world.employees["staff"], owner_role="employee")
    seen: list[tuple[int, str]] = []
    for offset in range(-5, 26):
        before = len(await sent(world, "staff", task))
        await round_on(world, D + timedelta(days=offset))
        seen += [(offset, k) for k in (await sent(world, "staff", task))[before:]]
    assert seen == [
        (-5, "task_assigned"),
        (-2, "task_due_soon"),
        (0, "task_due"),
        (1, "task_overdue"),
        (2, "task_overdue"),
        (3, "task_overdue"),
        (10, "task_overdue"),
        (17, "task_overdue"),
        (24, "task_overdue"),
    ]
    # A round that misses days catches up with one reminder, not one per missed day.
    _, late = await one_task(world, world.employees["staff"], owner_role="employee")
    await round_on(world, D + timedelta(days=12))
    assert await sent(world, "staff", late) == ["task_assigned", "task_overdue"]
    # A task with no due date only gets the assignment.
    _, undated = await one_task(world, world.employees["staff"], owner_role="employee")
    await set_due(world, undated, None)
    await round_on(world, D)
    assert await sent(world, "staff", undated) == ["task_assigned"]


async def test_pool_task_fans_out_per_member(world: HrWorld) -> None:
    await make_people_manager(world)
    _, task = await one_task(world, world.employees["staff"], owner_role="hr")
    await round_on(world, D - timedelta(days=1))
    rows = await reminder_rows(world)
    by_user = {(r["subject_id"], r["kind"]) for r in rows}
    assert by_user == {
        (world.users[who], kind)
        for who in ("owner", "operator")
        for kind in ("task_assigned", "task_due_soon")
    }
    assert len(rows) == 4 and len({r["reminder_key"] for r in rows}) == 4
    # Each recipient sees the task in My tasks.
    for who in ("owner", "operator"):
        assert task in {r["task_id"] for r in await mine(world, who)}, who


async def test_unlinked_person_reminders_go_to_the_pool(world: HrWorld) -> None:
    person = await insert_employee(world)  # no console account
    _, task = await one_task(world, person, owner_role="employee")
    await round_on(world, D)
    assert {r["subject_id"] for r in await reminder_rows(world)} == {world.users["owner"]}
    assert await sent(world, "owner", task) == ["task_assigned", "task_due"]
    # The reminder points at a step the HR team sees in My tasks (not a pool step: it is assigned).
    [row] = [r for r in await mine(world, "owner") if r["task_id"] == task]
    assert row["pool"] is False
    assert task not in {r["task_id"] for r in await mine(world, "staff")}

    # Once the person has an account, the reminders are theirs and HR's My tasks lets it go.
    user = uuid4()
    await world.q(
        "INSERT INTO console_users(id,email,display_name) VALUES (%s,%s,'Synthetic New')",
        (user, f"synthetic-{user}@example.test"),
    )
    await world.q(
        "INSERT INTO console_memberships(user_id,client_id,role) VALUES (%s,%s,'tenant_staff')",
        (user, world.tenant),
    )
    await world.q(
        "INSERT INTO hr_employee_accounts(client_id,user_id,employee_id) VALUES (%s,%s,%s)",
        (world.tenant, user, person),
    )
    await round_on(world, D + timedelta(days=1))
    mine_now = [(r["subject_id"], r["kind"]) for r in await reminder_rows(world)][2:]
    assert mine_now == [(user, "task_assigned"), (user, "task_overdue")]
    assert task not in {r["task_id"] for r in await mine(world, "owner")}


async def test_disabled_membership_gets_nothing(world: HrWorld) -> None:
    await make_people_manager(world)
    _, own = await one_task(world, world.employees["staff"], owner_role="employee")
    await membership(world, "staff", False)
    await membership(world, "operator", False)
    await round_on(world, D)
    users = {r["subject_id"] for r in await reminder_rows(world)}
    assert world.users["staff"] not in users and world.users["operator"] not in users
    # The disabled person's step goes to the HR pool instead, and the HR owner sees it.
    assert await sent(world, "owner", own) == ["task_assigned", "task_due"]
    assert own in {r["task_id"] for r in await mine(world, "owner")}

    # A leaver (membership disabled by the leaver finish) gets nothing either.
    await membership(world, "staff", True)
    await membership(world, "operator", True)
    outsider = world.employees["outsider"]
    _, colleague = await one_task(
        world, outsider, owner_role="person", owner_employee_id=str(world.employees["manager"])
    )
    await finish_now(world, world.employees["manager"])
    await round_on(world, D + timedelta(days=1))
    assert await sent(world, "manager") == []
    assert "task_overdue" in await sent(world, "owner", colleague)


async def test_done_or_cancelled_tasks_get_no_reminder(world: HrWorld) -> None:
    staff = world.employees["staff"]
    pid = await custom(
        world,
        staff,
        step(title_en="Done step", owner_role="employee"),
        step(title_en="Waiting step", owner_role="employee", approval_by="hr"),
        step(title_en="Open step", owner_role="employee"),
    )
    ids = []
    for ordinal in (1, 2, 3):
        body = await versioned_task(world, pid, ordinal)
        ids.append(body["task_id"])
        await set_due(world, body["task_id"], D)
    for ordinal in (1, 2):
        await act(world, "staff", "task.complete", **await versioned_task(world, pid, ordinal))
    cancelled, gone = await one_task(world, staff, owner_role="employee")
    await run(world, "owner", CANCEL_SQL, {"process_id": str(cancelled)})
    closed, shut = await one_task(world, staff, owner_role="hr")
    await run(
        world,
        "owner",
        "SELECT public.hr_task_apply(%s,%s,%s,%s) AS result",
        {"operation": "process.close_anyway", "process_id": str(closed), "note": "Synthetic"},
    )
    await round_on(world, D)
    assert {r["resource_id"] for r in await reminder_rows(world)} == {ids[2]}
    assert not {gone, shut, ids[0], ids[1]} & {r["resource_id"] for r in await reminder_rows(world)}


async def test_a_read_only_member_gets_no_reminders(world: HrWorld) -> None:
    """Task 8 deviation 3: read-only members cannot act, so they get no reminders, neither as
    the assignee (the HR pool gets them and sees the step) nor as the HR owner."""
    await make_people_manager(world)
    _, own = await one_task(world, world.employees["staff"], owner_role="employee")
    _, pooled = await one_task(world, world.employees["staff"], owner_role="hr")
    await set_role(world, "staff", "read_only")
    await set_role(world, "owner", "read_only")
    await round_on(world, D)
    assert {r["subject_id"] for r in await reminder_rows(world)} == {world.users["operator"]}
    for task in (own, pooled):
        assert await sent(world, "operator", task) == ["task_assigned", "task_due"]
    seen = {r["task_id"]: r for r in await mine(world, "operator")}
    assert {own, pooled} <= set(seen)
    assert (seen[own]["pool"], seen[own]["mine"], seen[own]["assignee_name"]) == (
        False,
        False,
        "Synthetic Staff",
    )


async def test_a_disabled_account_gets_no_reminders(world: HrWorld) -> None:
    _, own = await one_task(world, world.employees["staff"], owner_role="employee")
    await world.q("UPDATE console_users SET is_enabled=false WHERE id=%s", (world.users["staff"],))
    await round_on(world, D)
    assert await sent(world, "staff") == []
    assert await sent(world, "owner", own) == ["task_assigned", "task_due"]


async def test_a_new_due_date_starts_a_fresh_cadence(world: HrWorld) -> None:
    _, task = await one_task(world, world.employees["staff"], owner_role="employee")
    await round_on(world, D - timedelta(days=5))
    await round_on(world, D)
    later = D + timedelta(days=3)
    await set_due(world, task, later)  # rescheduled: no overdue for the old date
    for offset in (1, 2, 3, 3):
        await round_on(world, D + timedelta(days=offset))
    assert await sent(world, "staff", task) == [
        "task_assigned",
        "task_due",
        "task_due_soon",
        "task_due",
    ]
    keys = [r["reminder_key"] for r in await reminder_rows(world) if r["kind"] == "task_due"]
    assert len(keys) == 2 and f"due:{D}" in keys[0] and f"due:{later}" in keys[1]


async def test_the_worker_tenant_list_is_not_cut_by_the_due_cap(world: HrWorld) -> None:
    """Task 8 review M1: the worker's tenant list comes from hr_tasks_due_tenants (uncapped
    DISTINCT, ids only), never from the 5000-row hr_tasks_due. A real >5000-row tenant is too
    heavy for a test, so the cap is simulated with LIMIT 1 on the same uncapped set."""
    sql_text = Path(migrate.__file__).resolve().parents[1] / "db/sql/hr/tasks_due_tenants.sql"
    text = sql_text.read_text(encoding="utf-8")
    assert "hr_tasks_due_tenants(" in text and "hr_tasks_due(" not in text
    definitions = {
        r["f"]: r["d"]
        for r in await world.q(
            "SELECT p.proname AS f, pg_get_functiondef(p.oid) AS d FROM pg_proc p"
            " WHERE p.proname IN ('hr_tasks_due','hr_tasks_due_all','hr_tasks_due_tenants')"
        )
    }
    assert "LIMIT 5000" in definitions["hr_tasks_due"]
    for name in ("hr_tasks_due_all", "hr_tasks_due_tenants"):
        assert "LIMIT" not in definitions[name].upper(), name
    assert "title" not in definitions["hr_tasks_due_tenants"]

    await other_tenant_task(world)
    await one_task(world, world.employees["staff"], owner_role="employee")
    capped = {
        r["client_id"]
        for r in await world.q(
            "SELECT client_id FROM (SELECT * FROM public.hr_tasks_due_all(NULL,%s)"
            " ORDER BY client_id, task_id, kind, recipient_id LIMIT 1) d",
            (D,),
        )
    }
    async with world.service.pool.connection() as conn:
        async with conn.transaction():
            listed = await q.fetch_all("hr/tasks_due_tenants.sql", (D,), conn=conn)
    ids = [r["client_id"] for r in listed]
    assert all(set(r) == {"client_id"} for r in listed)
    assert ids == sorted(ids) and len(ids) == len(set(ids))
    assert {world.tenant, world.other_tenant} <= set(ids)
    assert len(capped) == 1 and len(ids) > len(capped)


# ---- emit --------------------------------------------------------------------------------------


async def test_emit_returns_false_on_the_second_call(world: HrWorld) -> None:
    _, task = await one_task(world, world.employees["staff"], owner_role="employee")
    args = (world.tenant, "task_due", world.users["staff"], f"synthetic:{uuid4()}", D)
    async with tx(world.service.pool, str(world.tenant)) as conn:
        assert await reminders.emit(conn, *args, "Due today: Synthetic", resource_id=task) is True
        assert await reminders.emit(conn, *args, "Due today: Synthetic", resource_id=task) is False
    assert len(await reminder_rows(world)) == 1

    refused = (psycopg.errors.InsufficientPrivilege, psycopg.errors.CheckViolation)
    cases: list[tuple[str, dict[str, Any]]] = [
        ("hr_reminder_unavailable", {"client_id": world.other_tenant}),  # not this transaction's
        ("hr_reminder_unavailable", {"resource_id": uuid4()}),  # no such task here
        ("hr_reminder_kind_invalid", {"event": "workflow_review"}),
        ("hr_reminder_invalid", {"subject": " "}),
        ("hr_reminder_recipient_unavailable", {"recipient_user_id": uuid4()}),
    ]
    for code, over in cases:
        values: dict[str, Any] = {
            "client_id": world.tenant,
            "event": "task_due",
            "recipient_user_id": world.users["staff"],
            "dedupe_key": f"synthetic:{uuid4()}",
            "due_on": D,
            "subject": "Synthetic",
            "resource_id": task,
            **over,
        }
        with pytest.raises(refused, match=rf"^{code}\b"):
            async with tx(world.service.pool, str(world.tenant)) as conn:
                await reminders.emit(conn, **values)
    # Only the system writes reminders: not from inside a person's HR session.
    with pytest.raises(refused, match=r"^hr_reminder_system_only\b"):
        async with tx(world.service.pool, str(world.tenant)) as conn:
            await world.service._session(conn, world.proof("owner"))
            await reminders.emit(conn, *args[:3], "synthetic:session", D, "S", resource_id=task)
    assert len(await reminder_rows(world)) == 1


# ---- Tenants and the worker --------------------------------------------------------------------


async def other_tenant_task(world: HrWorld) -> UUID:
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
    await world.q(
        "UPDATE hr_process_tasks SET due_on=%s WHERE client_id=%s AND id=%s",
        (D, world.other_tenant, row["id"]),
    )
    return row["id"]


async def test_foreign_tenant_tasks_never_produce_rows_for_this_tenants_members(
    world: HrWorld,
) -> None:
    foreign = await other_tenant_task(world)
    _, own = await one_task(world, world.employees["staff"], owner_role="hr")
    # A session of this tenant asking for the other gets nothing; its own rows only.
    assert await due_rows(world, D, tenant=world.other_tenant) == []
    mine_due = await due_rows(world, D)
    assert {r["task_id"] for r in mine_due} == {own}
    assert {r["client_id"] for r in mine_due} == {world.tenant}
    assert {r["title"] for r in mine_due} == {"Synthetic step"}
    # The worker's view (no tenant set) lists both tenants but no titles.
    async with world.service.pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(DUE, (None, D))
            everyone = await cursor.fetchall()
    assert {foreign, own} <= {r["task_id"] for r in everyone}
    assert {r["title"] for r in everyone} == {None}
    await round_on(world, D)
    # Every reminder row is about a task of its own tenant, for a member of that tenant.
    strays = await world.q(
        "SELECT n.client_id FROM hr_notifications n WHERE n.kind LIKE 'task\\_%%'"
        " AND NOT EXISTS(SELECT 1 FROM hr_process_tasks t"
        "  WHERE t.client_id=n.client_id AND t.id=n.resource_id)"
    )
    assert strays == []
    theirs = await reminder_rows(world, world.other_tenant)
    assert {r["resource_id"] for r in theirs} == {foreign}
    assert {r["subject_id"] for r in theirs} == {world.users["owner"]}
    assert foreign not in {r["resource_id"] for r in await reminder_rows(world)}


async def test_round_is_registered_and_runs_per_tenant(
    world: HrWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.worker import main as worker

    # Registered beside the leavers round, always on, every 900 s by default.
    source = inspect.getsource(worker.main)
    assert "hr_tasks = register_hr_tasks_task(app_pool)" in source
    assert re.search(r"background = \[[^\]]*\bhr_tasks\b", source)
    assert (
        inspect.signature(worker.register_hr_tasks_task).parameters["interval_seconds"].default
        == 900
    )
    ran = asyncio.Event()

    async def fake_round(pool: Any) -> tuple[int, int]:
        ran.set()
        raise RuntimeError("synthetic")  # a failing round is logged and the loop goes on

    monkeypatch.setattr(worker, "hr_tasks_round", fake_round)
    task = worker.register_hr_tasks_task(world.service.pool, interval_seconds=1)
    try:
        await asyncio.wait_for(ran.wait(), 5)
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        task.cancel()
    monkeypatch.undo()

    # One tenant per transaction: a failing tenant is logged by id and the others still run.
    foreign = await other_tenant_task(world)
    _, own = await one_task(world, world.employees["staff"], owner_role="employee")
    real_tx = reminders.tx

    def failing_tx(pool: Any, client_id: str) -> Any:
        if client_id == str(world.other_tenant):
            raise RuntimeError("synthetic failure")
        return real_tx(pool, client_id)

    monkeypatch.setattr(reminders, "tx", failing_tx)
    with capture_logs() as logs:
        seen, written = await round_on(world, D)
    assert seen >= 2 and written >= 2
    assert await sent(world, "staff", own) == ["task_assigned", "task_due"]
    assert await reminder_rows(world, world.other_tenant) == []
    failed = [log for log in logs if log["event"] == "hr_tasks_tenant_failed"]
    assert {log["client_id"] for log in failed} >= {str(world.other_tenant)}
    [summary] = [log for log in logs if log["event"] == "hr_tasks_round"]
    assert (summary["seen"], summary["written"]) == (seen, written) and summary["failed"] >= 1
    assert "Synthetic" not in json.dumps(logs, default=str)
    monkeypatch.setattr(reminders, "tx", real_tx)
    await round_on(world, D)
    assert {r["resource_id"] for r in await reminder_rows(world, world.other_tenant)} == {foreign}


# ---- The Home read -----------------------------------------------------------------------------


async def test_home_notifications_read_returns_the_task_reminders(world: HrWorld) -> None:
    _, task = await one_task(world, world.employees["staff"], owner_role="employee")
    await round_on(world, D + timedelta(days=1))
    response = await world.call("staff", "GET", "/console/v1/hr/records/notifications")
    assert response.status_code == 200, response.text
    items = {i["kind"]: i for i in response.json()["items"]}
    assert set(items) == {"task_assigned", "task_overdue"}
    overdue = items["task_overdue"]
    assert (overdue["resource_id"], overdue["due_on"], overdue["subject"]) == (
        str(task),
        D.isoformat(),
        "Overdue: Synthetic step",
    )
    # Marking one read works as for every other kind.
    read = await world.call(
        "staff",
        "POST",
        "/console/v1/hr/operations",
        {"operation": "notification.read", "notification_id": overdue["id"]},
    )
    assert read.status_code == 200, read.text
    # Someone else's reminders are not in another member's read.
    other = await world.call("manager", "GET", "/console/v1/hr/records/notifications")
    assert other.json()["items"] == []
