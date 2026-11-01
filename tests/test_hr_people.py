"""Migration 0520: departments, the new employee fields, change history and the People reads
(disposable database).
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from csa.core.db import q
from csa.core.tenancy import tx
from csa.hr.people_rules import person_from_row

from tests.conftest import requires_docker
from tests.test_hr_persistence import FIELDS, HrWorld, hr_database, workflow_grant, world

__all__ = ["hr_database", "world"]
pytestmark = [requires_docker, pytest.mark.asyncio]

TODAY = date(2026, 11, 1)
PEOPLE_COLUMNS = [
    "employee_id",
    "display_name",
    "job_title",
    "department_id",
    "department",
    "manager_id",
    "work_email",
    "work_phone",
    "start_date",
    "employment_type",
    "active",
    "region",
    "legal_entity_reference",
    "employment_category",
    "version",
    "account_user_id",
]
HR_ONLY = ("region", "legal_entity_reference", "employment_category", "version", "account_user_id")
READ_FUNCTIONS = (
    "hr_can_manage_people(uuid)",
    "hr_people_viewer(uuid)",
    "hr_people_rows(uuid)",
    "hr_department_rows(uuid)",
    "hr_employee_change_rows(uuid,uuid,integer)",
    "hr_people_access(uuid,uuid)",
    "hr_recruitment_members(uuid)",
)
INTERNAL_FUNCTIONS = (
    "hr_department_for(uuid,text)",
    "hr_employee_department_sync()",
    "hr_department_renamed()",
    "hr_employee_record_change()",
)


async def read_as(
    world: HrWorld, name: str, sql: str, params: tuple[object, ...] | None = None
) -> list[dict[str, Any]]:
    """Run a read the way the service does: in `tx`, inside the caller's HR session."""
    async with tx(world.service.pool, str(world.tenant)) as conn:
        await world.service._session(conn, world.proof(name))
        return await q.fetch_all(sql, params if params is not None else (world.tenant,), conn=conn)


async def read_as_other_tenant_owner(
    world: HrWorld, sql: str, params: tuple[object, ...] | None = None
) -> list[dict[str, Any]]:
    """The other tenant's real owner asks about `world.tenant` (or about `params`)."""
    async with tx(world.service.pool, str(world.other_tenant)) as conn:
        await conn.execute(
            "SELECT set_config('app.hr_user_id', %s, true)", (str(world.users["owner"]),)
        )
        return await q.fetch_all(sql, params if params is not None else (world.tenant,), conn=conn)


async def make_other_tenant_owner(world: HrWorld) -> None:
    await world.q(
        "INSERT INTO console_memberships(user_id,client_id,role) VALUES (%s,%s,'tenant_admin')",
        (world.users["owner"], world.other_tenant),
    )
    await world.q(
        "INSERT INTO hr_client_owners(client_id,user_id,approved_by) VALUES (%s,%s,%s)",
        (world.other_tenant, world.users["owner"], world.users["owner"]),
    )


async def update_as(
    world: HrWorld, actor: str | None, statement: str, params: tuple[object, ...]
) -> None:
    """An owner-pool write with `app.hr_user_id` set in the same transaction (as hr_admin_apply)."""
    async with world.owner_pool.connection() as conn:
        async with conn.transaction():
            if actor is not None:
                await conn.execute(
                    "SELECT set_config('app.hr_user_id', %s, true)", (str(world.users[actor]),)
                )
            await conn.execute(statement, params)  # type: ignore[arg-type]


async def departments(world: HrWorld) -> dict[str, UUID]:
    rows = await world.q("SELECT name, id FROM hr_departments WHERE client_id=%s", (world.tenant,))
    return {row["name"]: row["id"] for row in rows}


async def employee(world: HrWorld, employee_id: UUID) -> dict[str, Any]:
    rows = await world.q(
        "SELECT department_id, department, version FROM hr_employees WHERE client_id=%s AND id=%s",
        (world.tenant, employee_id),
    )
    return rows[0]


async def changes(world: HrWorld, employee_id: UUID) -> list[dict[str, Any]]:
    return await world.q(
        "SELECT changes, changed_by FROM hr_employee_changes WHERE client_id=%s AND employee_id=%s "
        "ORDER BY changed_at, id",
        (world.tenant, employee_id),
    )


async def change_count(world: HrWorld) -> int:
    rows = await world.q(
        "SELECT count(*) AS n FROM hr_employee_changes WHERE client_id=%s", (world.tenant,)
    )
    return rows[0]["n"]


async def test_departments_fold_case_and_spaces_on_write(world: HrWorld) -> None:
    written = {
        "Sales": uuid4(),
        "  sales ": uuid4(),
        "Sales   Ops": uuid4(),
        "sales ops": uuid4(),
        None: uuid4(),
    }
    for department, employee_id in written.items():
        await world.q(
            "INSERT INTO hr_employees(client_id,id,display_name,department) VALUES (%s,%s,%s,%s)",
            (world.tenant, employee_id, "Synthetic Seller", department),
        )
    found = await departments(world)
    # The fixture's "Learning" plus exactly two new departments.
    assert set(found) == {"Learning", "Sales", "Sales Ops"}
    expected = {
        "Sales": "Sales",
        "  sales ": "Sales",
        "Sales   Ops": "Sales Ops",
        "sales ops": "Sales Ops",
    }
    for department, canonical in expected.items():
        row = await employee(world, written[department])
        assert row["department_id"] == found[canonical] and row["department"] == canonical
    blank = await employee(world, written[None])
    assert blank["department_id"] is None and blank["department"] is None
    for name in ("staff", "manager", "owner", "outsider"):
        row = await employee(world, world.employees[name])
        assert row["department_id"] == found["Learning"] and row["department"] == "Learning"

    # A legacy text write (department_id unchanged) re-links by name.
    await world.q(
        "UPDATE hr_employees SET department='SALES  OPS' WHERE client_id=%s AND id=%s",
        (world.tenant, written["Sales"]),
    )
    row = await employee(world, written["Sales"])
    assert row["department_id"] == found["Sales Ops"] and row["department"] == "Sales Ops"
    # An id write wins over stale text, and the text follows the id.
    await world.q(
        "UPDATE hr_employees SET department_id=%s, department='Stale' WHERE client_id=%s AND id=%s",
        (found["Learning"], world.tenant, written["Sales"]),
    )
    row = await employee(world, written["Sales"])
    assert row["department_id"] == found["Learning"] and row["department"] == "Learning"
    # Clearing the text clears the link.
    await world.q(
        "UPDATE hr_employees SET department=NULL WHERE client_id=%s AND id=%s",
        (world.tenant, written["Sales"]),
    )
    row = await employee(world, written["Sales"])
    assert row["department_id"] is None and row["department"] is None
    assert set(await departments(world)) == {"Learning", "Sales", "Sales Ops"}


async def test_department_for_is_stable_and_skips_blank(world: HrWorld) -> None:
    async def department_for(name: str | None) -> UUID | None:
        rows = await world.q("SELECT hr_department_for(%s,%s) AS id", (world.tenant, name))
        return rows[0]["id"]

    first = await department_for("X")
    assert first is not None
    assert await department_for("X") == first
    assert await department_for("  x ") == first
    for blank in ("", "   ", None):
        assert await department_for(blank) is None
    assert set(await departments(world)) == {"Learning", "X"}
    # An archived name stays taken and keeps its id.
    await world.q(
        "UPDATE hr_departments SET archived_at=now() WHERE client_id=%s AND id=%s",
        (world.tenant, first),
    )
    assert await department_for("x") == first
    # Another tenant's department of the same name is its own row.
    other = await world.q("SELECT hr_department_for(%s,'X') AS id", (world.other_tenant,))
    assert other[0]["id"] not in (None, first)

    async with tx(world.service.pool, str(world.tenant)) as conn:
        await world.service._session(conn, world.proof("owner"))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute("SELECT hr_department_for(%s,'Y')", (world.tenant,))


async def test_function_execute_grants_are_pinned(world: HrWorld) -> None:
    roles = ("csa_app", "csa_ro", "csa_identity", "csa_hr_maintenance", "public")
    for signature in READ_FUNCTIONS + INTERNAL_FUNCTIONS:
        rows = await world.q(
            "SELECT p.prosecdef, p.proconfig, "
            + ", ".join(
                f"has_function_privilege('{role}', p.oid, 'EXECUTE') AS {role}" for role in roles
            )
            + " FROM pg_proc p WHERE p.oid = to_regprocedure(%s)",
            (f"public.{signature}",),
        )
        assert len(rows) == 1, signature
        row = rows[0]
        assert row["prosecdef"] is True, signature
        assert row["proconfig"] == ["search_path=pg_catalog, public, pg_temp"], signature
        readable = signature in READ_FUNCTIONS
        assert row["csa_app"] is readable, signature
        assert not any(row[role] for role in roles[1:]), signature


async def test_department_rename_updates_mirror_without_history_or_version(world: HrWorld) -> None:
    before_changes = await change_count(world)
    before = {name: await employee(world, world.employees[name]) for name in world.employees}
    learning = (await departments(world))["Learning"]
    await update_as(
        world,
        "owner",
        "UPDATE hr_departments SET name='Learning & Development' WHERE client_id=%s AND id=%s",
        (world.tenant, learning),
    )
    for name, old in before.items():
        row = await employee(world, world.employees[name])
        assert row["department_id"] == learning
        assert row["department"] == "Learning & Development"
        assert row["version"] == old["version"]
    assert await change_count(world) == before_changes


async def test_history_records_directory_field_changes_with_old_and_new(world: HrWorld) -> None:
    staff = world.employees["staff"]
    inserted = await changes(world, staff)
    # The fixture's INSERT recorded every non-null field with no old value, by nobody.
    assert len(inserted) == 1 and inserted[0]["changed_by"] is None
    assert inserted[0]["changes"] == {
        "display_name": {"old": None, "new": "Synthetic Staff"},
        "job_title": {"old": None, "new": "Trainer"},
        "department": {"old": None, "new": "Learning"},
        "manager": {"old": None, "new": "Synthetic Staff"},
        "work_email": {"old": None, "new": "staff@example.test"},
        "active": {"old": None, "new": "true"},
    }

    await update_as(
        world,
        "owner",
        "UPDATE hr_employees SET job_title='Lead', manager_id=NULL WHERE client_id=%s AND id=%s",
        (world.tenant, staff),
    )
    rows = await changes(world, staff)
    assert len(rows) == 2
    assert rows[1]["changes"] == {
        "job_title": {"old": "Trainer", "new": "Lead"},
        "manager": {"old": "Synthetic Staff", "new": None},
    }
    assert rows[1]["changed_by"] == world.users["owner"]

    # A write that changes nothing records nothing.
    await update_as(
        world,
        "owner",
        "UPDATE hr_employees SET job_title=job_title WHERE client_id=%s AND id=%s",
        (world.tenant, staff),
    )
    assert len(await changes(world, staff)) == 2

    # Dates are ISO, active is 'true'/'false', and the HR-only Overview fields keep old and new.
    sales = (await world.q("SELECT hr_department_for(%s,'Sales') AS id", (world.tenant,)))[0]["id"]
    await update_as(
        world,
        "platform",
        "UPDATE hr_employees SET start_date='2026-12-01', employment_type='contract', "
        "work_phone='+60 3-1234 5678', region='sabah', "
        "legal_entity_reference='Synthetic Sdn Bhd', employment_category='permanent', "
        "is_active=false, department_id=%s WHERE client_id=%s AND id=%s",
        (sales, world.tenant, staff),
    )
    rows = await changes(world, staff)
    assert len(rows) == 3 and rows[2]["changed_by"] == world.users["platform"]
    assert rows[2]["changes"] == {
        "department": {"old": "Learning", "new": "Sales"},
        "work_phone": {"old": None, "new": "+60 3-1234 5678"},
        "start_date": {"old": None, "new": "2026-12-01"},
        "employment_type": {"old": None, "new": "contract"},
        "active": {"old": "true", "new": "false"},
        "region": {"old": None, "new": "sabah"},
        "legal_entity_reference": {"old": None, "new": "Synthetic Sdn Bhd"},
        "employment_category": {"old": None, "new": "permanent"},
    }

    # The HR read: newest first, named actors, a platform admin as "FourStacks admin".
    history = await read_as(world, "owner", "hr/employee_changes.sql", (world.tenant, staff, 10))
    assert [row["changes"] for row in history] == [row["changes"] for row in reversed(rows)]
    assert [row["actor_name"] for row in history] == ["FourStacks admin", "Synthetic Staff", None]
    assert history == sorted(history, key=lambda row: row["changed_at"], reverse=True)
    assert (
        len(await read_as(world, "owner", "hr/employee_changes.sql", (world.tenant, staff, 2))) == 2
    )


async def test_history_rows_are_immutable_and_hr_only(world: HrWorld) -> None:
    staff = world.employees["staff"]
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="hr_immutable_record"):
        await world.q(
            "UPDATE hr_employee_changes SET changes='{\"x\":1}' WHERE client_id=%s", (world.tenant,)
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="hr_immutable_record"):
        await world.q("DELETE FROM hr_employee_changes WHERE client_id=%s", (world.tenant,))
    assert await change_count(world) == 4  # the fixture's four inserts

    params = (world.tenant, staff, 50)
    for name in ("staff", "operator", "manager", "admin"):
        assert await read_as(world, name, "hr/employee_changes.sql", params) == []
    owner_rows = await read_as(world, "owner", "hr/employee_changes.sql", params)
    assert len(owner_rows) == 1 and owner_rows[0]["changes"]["job_title"] == {
        "old": None,
        "new": "Trainer",
    }

    # The table itself: SELECT only, for the owner (policy hr_change_read).
    async with tx(world.service.pool, str(world.tenant)) as conn:
        await world.service._session(conn, world.proof("operator"))
        assert await (await conn.execute("SELECT * FROM hr_employee_changes")).fetchall() == []
    async with tx(world.service.pool, str(world.tenant)) as conn:
        await world.service._session(conn, world.proof("owner"))
        assert len(await (await conn.execute("SELECT * FROM hr_employee_changes")).fetchall()) == 4
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute(
                "INSERT INTO hr_employee_changes(client_id,employee_id,changes) "
                "VALUES (%s,%s,'{\"x\":1}')",
                (world.tenant, staff),
            )


async def test_people_viewer_classes(world: HrWorld) -> None:
    expected = {
        "owner": "hr",
        "operator": "directory",
        "staff": "self",
        "manager": "self",
        "admin": "self",
    }
    for name, viewer in expected.items():
        assert await read_as(world, name, "hr/people_viewer.sql") == [{"viewer": viewer}], name
    # Without the full basic fields the hr_operator directory grant is not the directory switch.
    await world.q(
        "UPDATE hr_grants SET fields=ARRAY['display_name','job_title'] "
        "WHERE client_id=%s AND user_id=%s AND action='directory.read'",
        (world.tenant, world.users["operator"]),
    )
    assert await read_as(world, "operator", "hr/people_viewer.sql") == [{"viewer": "self"}]
    # No HR session: no viewer.
    async with tx(world.service.pool, str(world.tenant)) as conn:
        assert await q.fetch_all("hr/people_viewer.sql", (world.tenant,), conn=conn) == [
            {"viewer": None}
        ]


async def test_people_rows_project_by_viewer(world: HrWorld) -> None:
    await world.q(
        "UPDATE hr_employees SET is_active=false WHERE client_id=%s AND id=%s",
        (world.tenant, world.employees["outsider"]),
    )
    await world.q(
        "UPDATE hr_employees SET region='sabah', legal_entity_reference='Synthetic Sdn Bhd', "
        "employment_category='permanent' WHERE client_id=%s AND id=%s",
        (world.tenant, world.employees["staff"]),
    )
    active = {world.employees[name] for name in ("staff", "manager", "owner")}

    operator = await read_as(world, "operator", "hr/people_rows.sql")
    assert {row["employee_id"] for row in operator} == active
    assert all(list(row) == PEOPLE_COLUMNS for row in operator)
    assert all(row[column] is None for row in operator for column in HR_ONLY)
    assert all(row["active"] is True and row["department"] == "Learning" for row in operator)
    assert operator == sorted(
        operator, key=lambda row: (row["display_name"].lower(), row["employee_id"])
    )

    owner = await read_as(world, "owner", "hr/people_rows.sql")
    by_id = {row["employee_id"]: row for row in owner}
    assert set(by_id) == active | {world.employees["outsider"]}
    assert by_id[world.employees["outsider"]]["active"] is False
    staff_row = by_id[world.employees["staff"]]
    assert staff_row["region"] == "sabah" and staff_row["employment_category"] == "permanent"
    assert (
        isinstance(staff_row["version"], int)
        and staff_row["account_user_id"] == world.users["staff"]
    )
    assert staff_row["manager_id"] == world.employees["manager"]

    staff = await read_as(world, "staff", "hr/people_rows.sql")
    assert [row["employee_id"] for row in staff] == [world.employees["staff"]]
    assert all(staff[0][column] is None for column in HR_ONLY)

    # Every viewer's rows fit the Task 1 contract; only the hr viewer gets the HR-only fields.
    hr_people = [person_from_row(row, TODAY, {}) for row in owner]
    assert all(person.version is not None and person.account_linked is True for person in hr_people)
    hr_staff = next(p for p in hr_people if p.employee_id == world.employees["staff"])
    assert hr_staff.employment_profile is not None and hr_staff.employment_profile.region == "sabah"
    for row in operator + staff:
        person = person_from_row(row, TODAY, {})
        assert (
            person.employment_profile is None
            and person.version is None
            and person.account_linked is None
        )


async def test_department_rows_count_active_people_for_hr_and_directory(world: HrWorld) -> None:
    await world.q(
        "UPDATE hr_employees SET is_active=false WHERE client_id=%s AND id=%s",
        (world.tenant, world.employees["outsider"]),
    )
    empty = (await world.q("SELECT hr_department_for(%s,'archive room') AS id", (world.tenant,)))[
        0
    ]["id"]
    await world.q(
        "UPDATE hr_departments SET archived_at=now() WHERE client_id=%s AND id=%s",
        (world.tenant, empty),
    )
    learning = (await departments(world))["Learning"]
    expected = [
        {
            "department_id": empty,
            "name": "archive room",
            "archived": True,
            "version": 0,
            "people": 0,
        },
        {
            "department_id": learning,
            "name": "Learning",
            "archived": False,
            "version": 0,
            "people": 3,
        },
    ]
    for name in ("owner", "operator"):
        assert await read_as(world, name, "hr/department_rows.sql") == expected, name
    for name in ("staff", "manager"):
        assert await read_as(world, name, "hr/department_rows.sql") == [], name
    # The table policy: any HR session of the tenant may read departments, nobody may write them.
    async with tx(world.service.pool, str(world.tenant)) as conn:
        await world.service._session(conn, world.proof("staff"))
        assert len(await (await conn.execute("SELECT * FROM hr_departments")).fetchall()) == 2
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute("UPDATE hr_departments SET name='Changed'")
    async with tx(world.service.pool, str(world.tenant)) as conn:
        assert await (await conn.execute("SELECT * FROM hr_departments")).fetchall() == []


async def test_people_access_shows_the_linked_accounts_rows_for_hr_only(world: HrWorld) -> None:
    await workflow_grant(world, "staff", "leave", "read", "self")
    staff_params = (world.tenant, world.employees["staff"])
    rows = await read_as(world, "owner", "hr/people_access.sql", staff_params)
    assert len(rows) == 1
    access = rows[0]
    assert access["user_id"] == world.users["staff"] and access["display_name"] == "Synthetic Staff"
    assert [(g["action"], g["scope"]) for g in access["grants"]] == [
        ("directory.read", "self"),
        ("training.draft", "self"),
        ("training.read", "self"),
        ("training.submit", "self"),
        ("training.withdraw", "self"),
    ]
    assert access["grants"][0]["fields"] == FIELDS
    assert access["workflows"] == [{"domain": "leave", "action": "read", "scope": "self"}]
    assert access["areas"] == [] and access["is_owner"] is False
    owner_rows = await read_as(
        world, "owner", "hr/people_access.sql", (world.tenant, world.employees["owner"])
    )
    assert [row["is_owner"] for row in owner_rows] == [True]

    unlinked = uuid4()
    await world.q(
        "INSERT INTO hr_employees(client_id,id,display_name) VALUES (%s,%s,'Synthetic Unlinked')",
        (world.tenant, unlinked),
    )
    assert await read_as(world, "owner", "hr/people_access.sql", (world.tenant, unlinked)) == []
    for name in ("operator", "staff", "manager"):
        assert await read_as(world, name, "hr/people_access.sql", staff_params) == [], name


async def test_people_reads_refuse_another_tenant(world: HrWorld) -> None:
    await make_other_tenant_owner(world)
    assert await read_as_other_tenant_owner(world, "hr/people_viewer.sql") == [{"viewer": None}]
    assert await read_as_other_tenant_owner(world, "hr/people_rows.sql") == []
    assert await read_as_other_tenant_owner(world, "hr/department_rows.sql") == []
    assert (
        await read_as_other_tenant_owner(
            world, "hr/people_access.sql", (world.tenant, world.employees["staff"])
        )
        == []
    )
    assert (
        await read_as_other_tenant_owner(
            world, "hr/employee_changes.sql", (world.tenant, world.employees["staff"], 10)
        )
        == []
    )
    assert await read_as_other_tenant_owner(world, "hr/recruitment_members.sql") == []
    # The same owner reads their own tenant: only that tenant's person.
    own = await read_as_other_tenant_owner(world, "hr/people_rows.sql", (world.other_tenant,))
    assert [row["display_name"] for row in own] == ["Synthetic other tenant"]


async def test_recruitment_members_need_owner_or_recruitment_area(world: HrWorld) -> None:
    assert await read_as(world, "operator", "hr/recruitment_members.sql") == []
    await world.q(
        "UPDATE console_memberships SET is_enabled=false WHERE client_id=%s AND user_id=%s",
        (world.tenant, world.users["outsider"]),
    )
    enabled = {user for name, user in world.users.items() if name != "outsider"}
    owner = await read_as(world, "owner", "hr/recruitment_members.sql")
    assert {row["user_id"] for row in owner} == enabled and len(owner) == len(enabled)
    assert all(row["display_name"] == "Synthetic Staff" for row in owner)
    await world.q(
        "INSERT INTO hr_private_grants(client_id,user_id,area,approved_by) "
        "VALUES (%s,%s,'recruitment',%s)",
        (world.tenant, world.users["operator"], world.users["owner"]),
    )
    assert await read_as(world, "operator", "hr/recruitment_members.sql") == owner
