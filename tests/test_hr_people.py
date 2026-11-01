"""Migration 0520: departments, the new employee fields, change history, the People reads, the
Manage people switch and the people write paths (disposable database).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from csa.console.security import digest
from csa.core.db import q
from csa.core.tenancy import tx
from csa.hr.automation_models import (
    AccountLink,
    DepartmentSave,
    PersonAdd,
    PersonUpdate,
    PersonValues,
)
from csa.hr.people_rules import person_from_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from tests.conftest import requires_docker
from tests.test_hr_persistence import (
    ATTENDANCE,
    FIELDS,
    ORIGIN,
    HrWorld,
    hr_database,
    workflow_grant,
    world,
)

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
    "hr_department_canonical_name()",
)
MIGRATION = Path(__file__).resolve().parents[1] / "db/migrations/0520_hr_people.sql"


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


async def name_users(world: HrWorld) -> dict[str, str]:
    """Give every fixture user a distinct name (the fixture calls them all "Synthetic Staff")."""
    names = {name: f"Synthetic {name.title()} Account" for name in world.users}
    for name, user in world.users.items():
        await world.q("UPDATE console_users SET display_name=%s WHERE id=%s", (names[name], user))
    return names


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
        "Sales\n": uuid4(),
        "\tSales": uuid4(),
        "Sales\r\n": uuid4(),
        "Sales\tOps\n": uuid4(),
        "\n": uuid4(),
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
        "Sales\n": "Sales",
        "\tSales": "Sales",
        "Sales\r\n": "Sales",
        "Sales\tOps\n": "Sales Ops",
    }
    for department, canonical in expected.items():
        row = await employee(world, written[department])
        assert row["department_id"] == found[canonical] and row["department"] == canonical, (
            department
        )
    for blank_text in ("\n", None):
        blank = await employee(world, written[blank_text])
        assert blank["department_id"] is None and blank["department"] is None
    for name in ("staff", "manager", "owner", "outsider"):
        row = await employee(world, world.employees[name])
        assert row["department_id"] == found["Learning"] and row["department"] == "Learning"

    # A legacy text write that still names the linked department keeps the link.
    for variant in ("sales\n", "\tSALES", "Sales"):
        await world.q(
            "UPDATE hr_employees SET department=%s WHERE client_id=%s AND id=%s",
            (variant, world.tenant, written["Sales"]),
        )
        row = await employee(world, written["Sales"])
        assert row["department_id"] == found["Sales"] and row["department"] == "Sales", variant
    # A legacy text write naming another department re-links by name.
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


async def insert_without_triggers(
    world: HrWorld, rows: list[tuple[str, UUID]], block: str = ""
) -> None:
    """Rows as 0380-era code wrote them, with no 0520 trigger firing (as at migration time)."""
    async with world.owner_pool.connection() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL session_replication_role = replica")
            for text, employee_id in rows:
                await conn.execute(
                    "INSERT INTO hr_employees(client_id,id,display_name,department) "
                    "VALUES (%s,%s,'Synthetic Legacy',%s)",
                    (world.tenant, employee_id, text),
                )
            if block:
                await conn.execute(block)  # type: ignore[arg-type]


async def test_data_step_links_whitespace_edged_text_and_later_writes_keep_it(
    world: HrWorld,
) -> None:
    # The migration's own data step, run on pre-existing rows with tab and newline edges.
    block = MIGRATION.read_text().split("-- data step begin\n")[1].split("-- data step end")[0]
    legacy = {"Field Sales\n": uuid4(), "\tField Sales": uuid4(), "Field\r\nSales": uuid4()}
    blank = uuid4()
    await insert_without_triggers(world, [*legacy.items(), ("\n", blank)], block)
    field_sales = (await departments(world))["Field Sales"]
    for employee_id in legacy.values():
        row = await employee(world, employee_id)
        assert row["department_id"] == field_sales and row["department"] == "Field Sales"
    blank_row = await employee(world, blank)
    assert blank_row["department_id"] is None and blank_row["department"] is None

    # A row the step never saw (unlinked text) is linked by its next write, not wiped.
    unlinked = uuid4()
    await insert_without_triggers(world, [("Ops\t", unlinked)])
    # Later unrelated writes keep every link and text.
    for employee_id in [*legacy.values(), blank, unlinked]:
        await world.q(
            "UPDATE hr_employees SET job_title='Synthetic Role' WHERE client_id=%s AND id=%s",
            (world.tenant, employee_id),
        )
    for employee_id in legacy.values():
        row = await employee(world, employee_id)
        assert row["department_id"] == field_sales and row["department"] == "Field Sales"
    blank_row = await employee(world, blank)
    assert blank_row["department_id"] is None and blank_row["department"] is None
    healed = await employee(world, unlinked)
    assert healed["department_id"] == (await departments(world))["Ops"]
    assert healed["department"] == "Ops"
    assert set(await departments(world)) == {"Learning", "Field Sales", "Ops"}


async def test_department_for_is_stable_and_skips_blank(world: HrWorld) -> None:
    async def department_for(name: str | None) -> UUID | None:
        rows = await world.q("SELECT hr_department_for(%s,%s) AS id", (world.tenant, name))
        return rows[0]["id"]

    first = await department_for("X")
    assert first is not None
    assert await department_for("X") == first
    assert await department_for("  x ") == first
    assert await department_for("\tX\r\n") == first
    for blank in ("", "   ", "\t\n", None):
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

    await update_as(
        world,
        "owner",
        "UPDATE hr_departments SET name=%s WHERE client_id=%s AND id=%s",
        ("\tPeople\n  Development\t", world.tenant, learning),
    )
    assert (await departments(world)).get("People Development") == learning
    for name, old in before.items():
        row = await employee(world, world.employees[name])
        assert row["department_id"] == learning and row["department"] == "People Development"
        assert row["version"] == old["version"]
    assert await change_count(world) == before_changes
    assert set(await departments(world)) == {"People Development"}


async def test_history_records_directory_field_changes_with_old_and_new(world: HrWorld) -> None:
    names = await name_users(world)
    mentor, staff = uuid4(), uuid4()
    await world.q(
        "INSERT INTO hr_employees(client_id,id,display_name) VALUES (%s,%s,'Synthetic Mentor')",
        (world.tenant, mentor),
    )
    await world.q(
        "UPDATE hr_employees SET display_name='Synthetic Line Manager' "
        "WHERE client_id=%s AND id=%s",
        (world.tenant, world.employees["manager"]),
    )
    await world.q(
        "INSERT INTO hr_employees(client_id,id,display_name,job_title,department,work_email,"
        "manager_id) VALUES (%s,%s,'Synthetic Subject','Trainer','Learning',"
        "'subject@example.test',%s)",
        (world.tenant, staff, mentor),
    )
    inserted = await changes(world, staff)
    # The INSERT recorded every non-null field with no old value, by nobody.
    assert len(inserted) == 1 and inserted[0]["changed_by"] is None
    assert inserted[0]["changes"] == {
        "display_name": {"old": None, "new": "Synthetic Subject"},
        "job_title": {"old": None, "new": "Trainer"},
        "department": {"old": None, "new": "Learning"},
        "manager": {"old": None, "new": "Synthetic Mentor"},
        "work_email": {"old": None, "new": "subject@example.test"},
        "active": {"old": None, "new": "true"},
    }

    await update_as(
        world,
        "owner",
        "UPDATE hr_employees SET job_title='Lead', manager_id=%s WHERE client_id=%s AND id=%s",
        (world.employees["manager"], world.tenant, staff),
    )
    rows = await changes(world, staff)
    assert len(rows) == 2
    assert rows[1]["changes"] == {
        "job_title": {"old": "Trainer", "new": "Lead"},
        "manager": {"old": "Synthetic Mentor", "new": "Synthetic Line Manager"},
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
        "is_active=false, manager_id=NULL, department_id=%s WHERE client_id=%s AND id=%s",
        (sales, world.tenant, staff),
    )
    rows = await changes(world, staff)
    assert len(rows) == 3 and rows[2]["changed_by"] == world.users["platform"]
    assert rows[2]["changes"] == {
        "department": {"old": "Learning", "new": "Sales"},
        "manager": {"old": "Synthetic Line Manager", "new": None},
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
    assert [row["actor_name"] for row in history] == ["FourStacks admin", names["owner"], None]
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
    names = await name_users(world)
    await workflow_grant(world, "staff", "leave", "read", "self")
    staff_params = (world.tenant, world.employees["staff"])
    rows = await read_as(world, "owner", "hr/people_access.sql", staff_params)
    assert len(rows) == 1
    access = rows[0]
    assert access["user_id"] == world.users["staff"] and access["display_name"] == names["staff"]
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
    assert [(row["display_name"], row["is_owner"]) for row in owner_rows] == [
        (names["owner"], True)
    ]

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
    names = await name_users(world)
    assert await read_as(world, "operator", "hr/recruitment_members.sql") == []
    await world.q(
        "UPDATE console_memberships SET is_enabled=false WHERE client_id=%s AND user_id=%s",
        (world.tenant, world.users["outsider"]),
    )
    enabled = {user: names[name] for name, user in world.users.items() if name != "outsider"}
    owner = await read_as(world, "owner", "hr/recruitment_members.sql")
    assert {row["user_id"]: row["display_name"] for row in owner} == enabled
    assert len(owner) == len(enabled)
    assert [row["display_name"] for row in owner] == sorted(enabled.values(), key=str.lower)
    await world.q(
        "INSERT INTO hr_private_grants(client_id,user_id,area,approved_by) "
        "VALUES (%s,%s,'recruitment',%s)",
        (world.tenant, world.users["operator"], world.users["owner"]),
    )
    assert await read_as(world, "operator", "hr/recruitment_members.sql") == owner


# --- Task 3: the Manage people switch and the write paths -------------------------------------

OPERATIONS = "/console/v1/hr/operations"
WRITE_FUNCTIONS = (
    "hr_people_apply(uuid,uuid,text,jsonb)",
    "hr_admin_apply(uuid,uuid,text,jsonb)",
    "hr_apply_invitation_access(uuid,uuid,jsonb,text[],text[],boolean)",
)


async def op(
    world: HrWorld, who: str, body: dict[str, Any], *, key: UUID | None = None
) -> httpx.Response:
    return await world.call(who, "POST", OPERATIONS, body, key=key)


async def ok(world: HrWorld, who: str, body: dict[str, Any]) -> dict[str, Any]:
    response = await op(world, who, body)
    assert response.status_code == 200, response.text
    return response.json()


async def refused(world: HrWorld, who: str, body: dict[str, Any], status: int, detail: str) -> None:
    response = await op(world, who, body)
    assert (response.status_code, response.json()["detail"]) == (status, detail), response.text


def add_body(employee_id: UUID, **values: Any) -> dict[str, Any]:
    return {
        "operation": "person.add",
        "employee_id": str(employee_id),
        "values": {
            "display_name": "Synthetic Starter",
            "job_title": "Sales Executive",
            "start_date": "2026-12-01",
            **values,
        },
    }


async def person_row(world: HrWorld, employee_id: UUID) -> dict[str, Any]:
    rows = await world.q(
        "SELECT * FROM hr_employees WHERE client_id=%s AND id=%s", (world.tenant, employee_id)
    )
    assert len(rows) == 1
    return rows[0]


async def update_body(world: HrWorld, employee_id: UUID, **values: Any) -> dict[str, Any]:
    """A person.update that keeps every current value except the ones given."""
    row = await person_row(world, employee_id)
    current = {
        "display_name": row["display_name"],
        "job_title": row["job_title"],
        "start_date": row["start_date"].isoformat() if row["start_date"] else None,
        "manager_id": str(row["manager_id"]) if row["manager_id"] else None,
        "department_id": str(row["department_id"]) if row["department_id"] else None,
        "work_email": row["work_email"],
        "work_phone": row["work_phone"],
        "employment_type": row["employment_type"],
    }
    profile = (
        {
            "region": row["region"],
            "legal_entity_reference": row["legal_entity_reference"],
            "employment_category": row["employment_category"],
        }
        if row["region"]
        else None
    )
    return {
        "operation": "person.update",
        "employee_id": str(employee_id),
        "expected_version": row["version"],
        "values": {**current, **values},
        "employment_profile": profile,
    }


def department_body(
    department_id: UUID, name: str, version: int | None = None, archived: bool = False
) -> dict[str, Any]:
    return {
        "operation": "department.save",
        "department_id": str(department_id),
        "expected_version": version,
        "name": name,
        "archived": archived,
    }


async def make_people_manager(world: HrWorld, name: str = "operator") -> None:
    await world.grant(name, "people.manage", "hr_operator")


async def counts(world: HrWorld) -> tuple[int, int, int, int]:
    found = []
    for table in ("hr_employees", "hr_departments", "hr_operations", "hr_authority_events"):
        rows = await world.q(
            f"SELECT count(*) AS n FROM {table} WHERE client_id=%s",  # type: ignore[arg-type]
            (world.tenant,),
        )
        found.append(rows[0]["n"])
    return found[0], found[1], found[2], found[3]


async def test_people_manage_grant_check_and_resolution(world: HrWorld) -> None:
    await make_people_manager(world)
    stored = await world.q(
        "SELECT action,scope,fields FROM hr_grants WHERE client_id=%s AND user_id=%s "
        "AND action='people.manage'",
        (world.tenant, world.users["operator"]),
    )
    assert stored == [{"action": "people.manage", "scope": "hr_operator", "fields": []}]
    with pytest.raises(psycopg.errors.CheckViolation):
        await world.grant("staff", "people.manage", "hr_operator", ["display_name"])
    for scope in ("self", "assigned_manager", "client_owner"):
        with pytest.raises(psycopg.errors.CheckViolation):
            await world.grant("staff", "people.manage", scope)
    # The other 0380 checks are still there: fields stay limited, unknown pairs refused.
    with pytest.raises(psycopg.errors.CheckViolation):
        await world.grant("staff", "directory.read", "hr_operator", ["salary"])
    with pytest.raises(psycopg.errors.CheckViolation):
        await world.grant("staff", "training.decide", "hr_operator")
    checks = await world.q(
        "SELECT conname FROM pg_constraint WHERE conrelid='public.hr_grants'::regclass "
        "AND contype='c' AND pg_get_constraintdef(oid) LIKE '%%client_owner%%'"
    )
    assert checks == [{"conname": "hr_grants_scope_action"}]

    response = await world.call("operator", "GET", "/console/v1/hr/guidance")
    assert response.status_code == 200, response.text
    assert response.json()["actions"] == ["directory.read", "training.read"]
    assert "people.manage" not in response.json()["actions"]


async def test_people_manage_holder_reads_people_as_hr(world: HrWorld) -> None:
    await world.q(
        "UPDATE hr_employees SET is_active=false, region='sabah', "
        "legal_entity_reference='Synthetic Sdn Bhd', employment_category='permanent' "
        "WHERE client_id=%s AND id=%s",
        (world.tenant, world.employees["outsider"]),
    )
    before = await read_as(world, "operator", "hr/people_rows.sql")
    assert world.employees["outsider"] not in {row["employee_id"] for row in before}
    assert await read_as(world, "operator", "hr/people_viewer.sql") == [{"viewer": "directory"}]

    await make_people_manager(world)
    assert await read_as(world, "operator", "hr/people_viewer.sql") == [{"viewer": "hr"}]
    rows = {
        row["employee_id"]: row for row in await read_as(world, "operator", "hr/people_rows.sql")
    }
    assert set(rows) == set(world.employees.values())
    left = rows[world.employees["outsider"]]
    assert left["active"] is False and left["region"] == "sabah"
    assert left["employment_category"] == "permanent" and isinstance(left["version"], int)
    assert left["account_user_id"] == world.users["outsider"]
    history = await read_as(
        world, "operator", "hr/employee_changes.sql", (world.tenant, world.employees["staff"], 10)
    )
    assert len(history) == 1
    # A holder without it (the staff member) still sees only themselves, without HR-only fields.
    staff = await read_as(world, "staff", "hr/people_rows.sql")
    assert [row["employee_id"] for row in staff] == [world.employees["staff"]]
    assert all(staff[0][column] is None for column in HR_ONLY)


async def test_person_add_needs_owner_or_people_manager(world: HrWorld) -> None:
    for name in ("staff", "operator", "manager", "admin"):
        await refused(world, name, add_body(uuid4()), 404, "resource_unavailable")
    added = uuid4()
    receipt = await ok(world, "owner", add_body(added))
    assert receipt == {
        "operation": "person.add",
        "resource_id": str(added),
        "version": 0,
        "status": "saved",
        "duplicate": False,
    }
    await make_people_manager(world)
    second = uuid4()
    receipt = await ok(world, "operator", add_body(second, display_name="Synthetic Second"))
    assert receipt["resource_id"] == str(second)
    row = await person_row(world, second)
    assert row["display_name"] == "Synthetic Second" and row["is_active"] is True
    # An id already in use is refused, and nothing changes.
    before = await counts(world)
    await refused(world, "operator", add_body(second), 409, "employee_exists")
    await refused(world, "owner", add_body(world.employees["staff"]), 409, "employee_exists")
    assert await counts(world) == before


async def test_people_apply_refuses_other_tenants_and_direct_callers(world: HrWorld) -> None:
    await make_people_manager(world)
    body = add_body(uuid4())
    fingerprint = "0" * 64

    async def direct(name: str | None, client: UUID) -> None:
        async with tx(world.service.pool, str(world.tenant)) as conn:
            if name is not None:
                await world.service._session(conn, world.proof(name))
            await conn.execute(
                "SELECT public.hr_people_apply(%s,%s,%s,%s)",
                (client, uuid4(), fingerprint, Jsonb(body)),
            )

    # Another tenant's id, a session without the grant, and no session at all.
    for name, client in (
        ("operator", world.other_tenant),
        ("owner", world.other_tenant),
        ("staff", world.tenant),
        (None, world.tenant),
    ):
        with pytest.raises(
            psycopg.errors.InsufficientPrivilege, match="hr_people_manager_required"
        ):
            await direct(name, client)
    other_rows = await world.q(
        "SELECT id FROM hr_employees WHERE client_id=%s", (world.other_tenant,)
    )
    assert len(other_rows) == 1
    # References into another tenant are unavailable.
    stranger, foreign_department = uuid4(), uuid4()
    await world.q(
        "INSERT INTO hr_employees(client_id,id,display_name) VALUES (%s,%s,'Synthetic Stranger')",
        (world.other_tenant, stranger),
    )
    await world.q(
        "INSERT INTO hr_departments(client_id,id,name) VALUES (%s,%s,'Foreign')",
        (world.other_tenant, foreign_department),
    )
    await refused(
        world, "operator", add_body(uuid4(), manager_id=str(stranger)), 409, "manager_unavailable"
    )
    await refused(
        world,
        "operator",
        add_body(uuid4(), department_id=str(foreign_department)),
        409,
        "department_unavailable",
    )
    await refused(
        world,
        "operator",
        department_body(foreign_department, "Renamed", 0),
        409,
        "department_unavailable",
    )
    # The other tenant's own person cannot be updated through this tenant.
    other_update = {
        "operation": "person.update",
        "employee_id": str(stranger),
        "expected_version": 0,
        "values": {"display_name": "Synthetic Taken"},
    }
    await refused(world, "operator", other_update, 404, "resource_unavailable")
    stranger_row = await world.q(
        "SELECT display_name FROM hr_employees WHERE client_id=%s AND id=%s",
        (world.other_tenant, stranger),
    )
    assert stranger_row == [{"display_name": "Synthetic Stranger"}]


async def test_people_manager_cannot_change_access(world: HrWorld) -> None:
    await make_people_manager(world)
    before = await world.q(
        "SELECT user_id,action,scope FROM hr_grants WHERE client_id=%s ORDER BY 1,2,3",
        (world.tenant,),
    )
    for body in (
        {
            "operation": "access.save",
            "user_id": str(world.users["staff"]),
            "grants": [{"action": "people.manage", "scope": "hr_operator", "fields": []}],
            "workflows": [],
        },
        {
            "operation": "private_access.save",
            "user_id": str(world.users["operator"]),
            "area": "payroll",
            "enabled": True,
        },
        {
            "operation": "account.link",
            "employee_id": str(world.employees["staff"]),
            "account_id": str(world.users["operator"]),
        },
        {
            "operation": "employee.save",
            "employee_id": str(uuid4()),
            "values": {"display_name": "Synthetic Legacy"},
        },
    ):
        await refused(world, "operator", body, 404, "resource_unavailable")
    response = await world.call("operator", "GET", "/console/v1/hr/access/people")
    assert response.status_code == 404
    # Straight to the owner-only function: refused too.
    async with tx(world.service.pool, str(world.tenant)) as conn:
        await world.service._session(conn, world.proof("operator"))
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="hr_owner_required"):
            await conn.execute(
                "SELECT public.hr_admin_apply(%s,%s,%s,%s)",
                (
                    world.tenant,
                    uuid4(),
                    "0" * 64,
                    Jsonb(
                        {
                            "operation": "private_access.save",
                            "user_id": str(world.users["operator"]),
                            "area": "payroll",
                            "enabled": True,
                        }
                    ),
                ),
            )
    after = await world.q(
        "SELECT user_id,action,scope FROM hr_grants WHERE client_id=%s ORDER BY 1,2,3",
        (world.tenant,),
    )
    assert after == before
    areas = await world.q("SELECT 1 FROM hr_private_grants WHERE client_id=%s", (world.tenant,))
    assert areas == []


async def test_person_add_without_manager_records_history(world: HrWorld) -> None:
    added = uuid4()
    await ok(world, "owner", add_body(added, manager_id=None))
    row = await person_row(world, added)
    assert row["manager_id"] is None and row["department_id"] is None
    assert row["job_title"] == "Sales Executive" and row["start_date"] == date(2026, 12, 1)
    assert row["is_active"] is True and row["version"] == 0
    assert row["region"] is None and row["employment_type"] is None
    history = await changes(world, added)
    assert len(history) == 1 and history[0]["changed_by"] == world.users["owner"]
    assert history[0]["changes"] == {
        "display_name": {"old": None, "new": "Synthetic Starter"},
        "job_title": {"old": None, "new": "Sales Executive"},
        "start_date": {"old": None, "new": "2026-12-01"},
        "active": {"old": None, "new": "true"},
    }
    # Every field, a profile and a manager, from a people.manage holder.
    await make_people_manager(world)
    full = uuid4()
    body = add_body(
        full,
        manager_id=str(world.employees["manager"]),
        work_email="starter@example.test",
        work_phone="+60 3-1234 5678",
        employment_type="part_time",
    )
    body["employment_profile"] = {
        "region": "sarawak",
        "legal_entity_reference": "Synthetic Sdn Bhd",
        "employment_category": "permanent",
    }
    await ok(world, "operator", body)
    row = await person_row(world, full)
    assert row["manager_id"] == world.employees["manager"]
    assert (row["work_email"], row["work_phone"], row["employment_type"]) == (
        "starter@example.test",
        "+60 3-1234 5678",
        "part_time",
    )
    assert (row["region"], row["legal_entity_reference"], row["employment_category"]) == (
        "sarawak",
        "Synthetic Sdn Bhd",
        "permanent",
    )
    [entry] = await changes(world, full)
    assert entry["changed_by"] == world.users["operator"]
    assert entry["changes"]["manager"] == {"old": None, "new": "Synthetic Staff"}


async def test_person_add_inline_department_reuses_folded_name(world: HrWorld) -> None:
    before = await departments(world)
    learning = before["Learning"]
    first = uuid4()
    await ok(world, "owner", add_body(first, new_department="  learning "))
    row = await person_row(world, first)
    assert row["department_id"] == learning and row["department"] == "Learning"
    assert await departments(world) == before
    sales = (await world.q("SELECT hr_department_for(%s,'Sales') AS id", (world.tenant,)))[0]["id"]
    second = uuid4()
    await ok(world, "owner", add_body(second, new_department="  sales "))
    assert (await person_row(world, second))["department_id"] == sales
    # A name nobody has yet makes the department.
    third = uuid4()
    await ok(world, "owner", add_body(third, new_department="Field   Sales"))
    found = await departments(world)
    assert set(found) == {"Learning", "Sales", "Field Sales"}
    assert (await person_row(world, third))["department_id"] == found["Field Sales"]
    # Choosing both an existing department and a new one is refused before the database.
    both = add_body(uuid4(), department_id=str(learning), new_department="Other")
    response = await op(world, "owner", both)
    assert response.status_code == 422


async def test_person_add_refuses_archived_department(world: HrWorld) -> None:
    archive = uuid4()
    created = await ok(world, "owner", department_body(archive, "Archive Room"))
    assert created["resource_id"] == str(archive) and created["version"] == 0
    archived = await ok(world, "owner", department_body(archive, "Archive Room", 0, True))
    assert archived["version"] == 1
    before = await counts(world)
    await refused(
        world, "owner", add_body(uuid4(), department_id=str(archive)), 409, "department_archived"
    )
    await refused(
        world,
        "owner",
        add_body(uuid4(), new_department=" archive  room"),
        409,
        "department_archived",
    )
    await refused(
        world, "owner", add_body(uuid4(), department_id=str(uuid4())), 409, "department_unavailable"
    )
    assert await counts(world) == before


async def test_person_update_stale_version_is_person_changed(world: HrWorld) -> None:
    staff = world.employees["staff"]
    body = await update_body(world, staff, job_title="Senior Trainer")
    first = await ok(world, "owner", body)
    assert first["resource_id"] == str(staff) and first["version"] == body["expected_version"] + 1
    second = {**body, "values": {**body["values"], "job_title": "Lead Trainer"}}
    response = await op(world, "owner", second)
    assert response.status_code == 409 and response.json() == {"detail": "person_changed"}
    assert (await person_row(world, staff))["job_title"] == "Senior Trainer"
    missing = {**body, "employee_id": str(uuid4())}
    await refused(world, "owner", missing, 404, "resource_unavailable")


async def test_person_update_sets_every_field_and_clears_profile(world: HrWorld) -> None:
    await make_people_manager(world)
    staff = world.employees["staff"]
    finance = uuid4()
    await ok(world, "operator", department_body(finance, "Finance"))
    body = await update_body(
        world,
        staff,
        display_name="Synthetic Renamed",
        job_title=None,
        start_date="2026-11-20",
        manager_id=None,
        department_id=str(finance),
        work_email=None,
        work_phone="03-555 0101",
        employment_type="contract",
    )
    body["employment_profile"] = {
        "region": "sabah",
        "legal_entity_reference": "Synthetic Sdn Bhd",
        "employment_category": "permanent",
    }
    receipt = await ok(world, "operator", body)
    row = await person_row(world, staff)
    assert receipt["version"] == row["version"] == body["expected_version"] + 1
    assert (row["display_name"], row["job_title"], row["start_date"]) == (
        "Synthetic Renamed",
        None,
        date(2026, 11, 20),
    )
    assert (row["manager_id"], row["department_id"], row["department"]) == (
        None,
        finance,
        "Finance",
    )
    assert (row["work_email"], row["work_phone"], row["employment_type"]) == (
        None,
        "03-555 0101",
        "contract",
    )
    assert row["region"] == "sabah" and row["employment_category"] == "permanent"
    latest = (await changes(world, staff))[-1]
    assert latest["changed_by"] == world.users["operator"]
    assert latest["changes"]["department"] == {"old": "Learning", "new": "Finance"}
    # No profile clears all three columns; no department clears it.
    cleared = await update_body(world, staff, department_id=None)
    cleared["employment_profile"] = None
    await ok(world, "operator", cleared)
    row = await person_row(world, staff)
    assert (row["region"], row["legal_entity_reference"], row["employment_category"]) == (
        None,
        None,
        None,
    )
    assert row["department_id"] is None and row["department"] is None
    # Bad values never reach the database.
    for values in ({"work_phone": "call me"}, {"work_email": "two@@example.test"}):
        bad = await update_body(world, staff, **values)
        assert (await op(world, "operator", bad)).status_code == 422, values


async def test_person_update_keeps_unchanged_archived_department_and_departed_manager(
    world: HrWorld,
) -> None:
    staff = world.employees["staff"]
    learning = (await departments(world))["Learning"]
    await ok(world, "owner", department_body(learning, "Learning", 0, True))
    await world.q(
        "UPDATE hr_employees SET is_active=false WHERE client_id=%s AND id IN (%s,%s)",
        (world.tenant, world.employees["manager"], world.employees["outsider"]),
    )
    body = await update_body(world, staff, job_title="Senior Trainer")
    assert body["values"]["department_id"] == str(learning)
    assert body["values"]["manager_id"] == str(world.employees["manager"])
    receipt = await ok(world, "owner", body)
    row = await person_row(world, staff)
    assert receipt["version"] == row["version"] and row["job_title"] == "Senior Trainer"
    assert row["department_id"] == learning and row["manager_id"] == world.employees["manager"]
    # Choosing another inactive manager, or moving into an archived department, is refused.
    other = await update_body(world, staff, manager_id=str(world.employees["outsider"]))
    await refused(world, "owner", other, 409, "manager_unavailable")
    old = uuid4()
    await ok(world, "owner", department_body(old, "Old Team"))
    await ok(world, "owner", department_body(old, "Old Team", 0, True))
    owner_body = await update_body(world, world.employees["owner"], department_id=None)
    await ok(world, "owner", owner_body)
    moved = await update_body(world, world.employees["owner"], department_id=str(old))
    await refused(world, "owner", moved, 409, "department_archived")
    rejoin = await update_body(world, world.employees["owner"], department_id=str(learning))
    await refused(world, "owner", rejoin, 409, "department_archived")
    await refused(
        world,
        "owner",
        add_body(uuid4(), manager_id=str(world.employees["outsider"])),
        409,
        "manager_unavailable",
    )
    assert (await person_row(world, staff))["manager_id"] == world.employees["manager"]


async def test_person_update_refuses_manager_loops(world: HrWorld) -> None:
    staff, manager = world.employees["staff"], world.employees["manager"]
    # staff reports to manager; manager reporting to staff closes a loop.
    loop = await update_body(world, manager, manager_id=str(staff))
    await refused(world, "owner", loop, 409, "manager_loop")
    # A longer chain: a -> staff -> manager; manager -> a is a loop too.
    a = uuid4()
    await ok(world, "owner", add_body(a, manager_id=str(staff)))
    longer = await update_body(world, manager, manager_id=str(a))
    await refused(world, "owner", longer, 409, "manager_loop")
    assert (await person_row(world, manager))["manager_id"] is None
    # Self as manager: the contract refuses it, and so does the database on its own.
    self_body = await update_body(world, staff, manager_id=str(staff))
    assert (await op(world, "owner", self_body)).status_code == 422
    async with tx(world.service.pool, str(world.tenant)) as conn:
        await world.service._session(conn, world.proof("owner"))
        with pytest.raises(psycopg.errors.CheckViolation, match="hr_manager_loop"):
            await conn.execute(
                "SELECT public.hr_people_apply(%s,%s,%s,%s)",
                (world.tenant, uuid4(), "0" * 64, Jsonb(self_body)),
            )
    # The owner reports to manager too, so that is a loop; someone outside the chain is fine.
    via_owner = await update_body(world, manager, manager_id=str(world.employees["owner"]))
    await refused(world, "owner", via_owner, 409, "manager_loop")
    unrelated = uuid4()
    await ok(world, "owner", add_body(unrelated))
    await ok(world, "owner", await update_body(world, manager, manager_id=str(unrelated)))
    assert (await person_row(world, manager))["manager_id"] == unrelated


async def test_person_update_never_changes_active(world: HrWorld) -> None:
    outsider = world.employees["outsider"]
    await world.q(
        "UPDATE hr_employees SET is_active=false WHERE client_id=%s AND id=%s",
        (world.tenant, outsider),
    )
    body = await update_body(world, outsider, job_title="Former Trainer")
    await ok(world, "owner", body)
    row = await person_row(world, outsider)
    assert row["is_active"] is False and row["job_title"] == "Former Trainer"
    again = await update_body(world, outsider)
    again["active"] = True
    assert (await op(world, "owner", again)).status_code == 422
    with_active = await update_body(world, outsider, active=True)
    assert (await op(world, "owner", with_active)).status_code == 422
    assert (await person_row(world, outsider))["is_active"] is False


async def test_department_save_create_rename_archive_and_conflicts(world: HrWorld) -> None:
    await make_people_manager(world)
    finance = uuid4()
    created = await ok(world, "operator", department_body(finance, "Finance"))
    assert created == {
        "operation": "department.save",
        "resource_id": str(finance),
        "version": 0,
        "status": "saved",
        "duplicate": False,
    }
    await refused(
        world, "operator", department_body(uuid4(), " finance "), 409, "department_name_taken"
    )
    # The same id again, as a create, is someone else's change.
    await refused(world, "operator", department_body(finance, "Other"), 409, "department_changed")
    await ok(
        world,
        "operator",
        await update_body(world, world.employees["staff"], department_id=str(finance)),
    )
    staff_version = (await person_row(world, world.employees["staff"]))["version"]

    renamed = await ok(world, "operator", department_body(finance, "Finance  Ops", 0))
    assert renamed["version"] == 1
    staff = await person_row(world, world.employees["staff"])
    assert staff["department"] == "Finance Ops" and staff["version"] == staff_version
    await refused(
        world, "operator", department_body(finance, "Stale", 0), 409, "department_changed"
    )
    learning = (await departments(world))["Learning"]
    await refused(
        world, "operator", department_body(learning, "finance ops", 0), 409, "department_name_taken"
    )
    await refused(
        world, "operator", department_body(uuid4(), "Ghost", 0), 409, "department_unavailable"
    )
    # A case-only rename of itself is fine.
    await ok(world, "operator", department_body(finance, "FINANCE OPS", 1))

    await ok(world, "operator", department_body(finance, "FINANCE OPS", 2, True))
    listed = {
        row["name"]: row for row in await read_as(world, "operator", "hr/department_rows.sql")
    }
    assert listed["FINANCE OPS"]["archived"] is True and listed["FINANCE OPS"]["version"] == 3
    assert listed["Learning"]["archived"] is False
    stamp = await world.q(
        "SELECT archived_at FROM hr_departments WHERE client_id=%s AND id=%s",
        (world.tenant, finance),
    )
    # Saving it archived again keeps the first archive time; the archived name stays taken.
    await ok(world, "operator", department_body(finance, "FINANCE OPS", 3, True))
    again = await world.q(
        "SELECT archived_at FROM hr_departments WHERE client_id=%s AND id=%s",
        (world.tenant, finance),
    )
    assert again == stamp and stamp[0]["archived_at"] is not None
    await refused(
        world, "operator", department_body(uuid4(), "Finance Ops"), 409, "department_name_taken"
    )
    await ok(world, "operator", department_body(finance, "Finance Ops", 4, False))
    restored = await world.q(
        "SELECT archived_at, version FROM hr_departments WHERE client_id=%s AND id=%s",
        (world.tenant, finance),
    )
    assert restored == [{"archived_at": None, "version": 5}]
    # Staff and others without the grant cannot save departments.
    for name in ("staff", "manager", "admin"):
        await refused(world, name, department_body(uuid4(), "Nope"), 404, "resource_unavailable")


async def test_account_link_is_owner_only_and_unique(world: HrWorld) -> None:
    await make_people_manager(world)
    starter, other = uuid4(), uuid4()
    await ok(world, "owner", add_body(starter))
    await ok(world, "owner", add_body(other, display_name="Synthetic Other"))
    await ok(world, "owner", await update_body(world, starter, job_title="Account Executive"))

    def link(employee_id: UUID, account: UUID | None) -> dict[str, Any]:
        return {
            "operation": "account.link",
            "employee_id": str(employee_id),
            "account_id": str(account) if account else None,
        }

    admin = world.users["admin"]
    await refused(world, "operator", link(starter, admin), 404, "resource_unavailable")
    await refused(world, "staff", link(starter, admin), 404, "resource_unavailable")
    receipt = await ok(world, "owner", link(starter, admin))
    assert receipt["operation"] == "account.link" and receipt["resource_id"] == str(starter)
    assert receipt["version"] == (await person_row(world, starter))["version"] == 1
    accounts = await world.q(
        "SELECT user_id FROM hr_employee_accounts WHERE client_id=%s AND employee_id=%s",
        (world.tenant, starter),
    )
    assert accounts == [{"user_id": admin}]
    # Linking the same account to someone else is refused; to the same person again is fine.
    await refused(world, "owner", link(other, admin), 409, "account_taken")
    await refused(world, "owner", link(other, world.users["staff"]), 409, "account_taken")
    await ok(world, "owner", link(starter, admin))
    await refused(world, "owner", link(uuid4(), world.users["reader"]), 404, "resource_unavailable")
    await world.q(
        "UPDATE console_memberships SET is_enabled=false WHERE client_id=%s AND user_id=%s",
        (world.tenant, world.users["reader"]),
    )
    await refused(world, "owner", link(other, world.users["reader"]), 409, "member_unavailable")
    # Moving a link: None unlinks, and the account is free for another person.
    await ok(world, "owner", link(starter, None))
    assert (
        await world.q(
            "SELECT 1 FROM hr_employee_accounts WHERE client_id=%s AND employee_id=%s",
            (world.tenant, starter),
        )
        == []
    )
    await ok(world, "owner", link(other, admin))
    events = await world.q(
        "SELECT subject_id, details FROM hr_authority_events WHERE client_id=%s "
        "AND operation='account.link' ORDER BY occurred_at",
        (world.tenant,),
    )
    assert [e["details"] for e in events] == [
        {"employee_id": str(starter), "account_id": str(admin)},
        {"employee_id": str(starter), "account_id": str(admin)},
        {"employee_id": str(starter), "account_id": None},
        {"employee_id": str(other), "account_id": str(admin)},
    ]
    assert {e["subject_id"] for e in events} == {world.users["owner"]}
    # Access "Recent changes" leaves account links out.
    recent = await world.call("owner", "GET", "/console/v1/hr/access/recent")
    assert recent.status_code == 200 and recent.json()["items"] == []


async def test_employee_save_still_resolves_department_text(world: HrWorld) -> None:
    learning = (await departments(world))["Learning"]
    legacy = uuid4()
    await ok(
        world,
        "owner",
        {
            "operation": "employee.save",
            "employee_id": str(legacy),
            "values": {"display_name": "Synthetic Legacy", "department": " LEARNING "},
        },
    )
    row = await person_row(world, legacy)
    assert row["department_id"] == learning and row["department"] == "Learning"
    events = await world.q(
        "SELECT details FROM hr_authority_events WHERE client_id=%s AND operation='employee.save'",
        (world.tenant,),
    )
    assert [e["details"]["employee_id"] for e in events] == [str(legacy)]


async def test_people_operations_replay_by_key(world: HrWorld) -> None:
    await make_people_manager(world)
    for body in (add_body(uuid4()), department_body(uuid4(), "Replay Team")):
        key = uuid4()
        first = await op(world, "operator", body, key=key)
        assert first.status_code == 200, first.text
        before = await counts(world)
        replay = await op(world, "operator", body, key=key)
        assert replay.status_code == 200
        assert replay.json() == {**first.json(), "duplicate": True}
        assert await counts(world) == before
        other = (
            {**body, "values": {**body["values"], "display_name": "Synthetic Different"}}
            if body["operation"] == "person.add"
            else {**body, "name": "Different Team"}
        )
        conflict = await op(world, "operator", other, key=key)
        assert conflict.status_code == 409 and conflict.json()["detail"] == "idempotency_conflict"
        assert await counts(world) == before
    # People writes record a receipt and no authority event.
    rows = await world.q(
        "SELECT operation, subject_id FROM hr_operations WHERE client_id=%s ORDER BY occurred_at",
        (world.tenant,),
    )
    assert rows == [
        {"operation": "person.add", "subject_id": world.users["operator"]},
        {"operation": "department.save", "subject_id": world.users["operator"]},
    ]
    assert (await counts(world))[3] == 0


async def test_people_contracts_refuse_bad_shapes(world: HrWorld) -> None:
    employee_id = uuid4()
    with pytest.raises(ValidationError, match="one_department_choice"):
        PersonValues(display_name="A", department_id=uuid4(), new_department="B")
    with pytest.raises(ValidationError, match="title_and_start_required"):
        PersonAdd(
            operation="person.add", employee_id=employee_id, values=PersonValues(display_name="A")
        )
    with pytest.raises(ValidationError, match="manager_self"):
        PersonUpdate(
            operation="person.update",
            employee_id=employee_id,
            expected_version=0,
            values=PersonValues(display_name="A", manager_id=employee_id),
        )
    for bad in ({"work_phone": "12"}, {"work_phone": "+60 x"}, {"work_email": "a@b@c"}):
        with pytest.raises(ValidationError):
            PersonValues.model_validate({"display_name": "A", **bad})
    with pytest.raises(ValidationError):
        DepartmentSave(operation="department.save", department_id=uuid4(), name="")
    with pytest.raises(ValidationError):
        AccountLink.model_validate({"operation": "account.link", "employee_id": str(employee_id)})
    # The same refusals over HTTP are a content-free 422, and nothing is written.
    before = await counts(world)
    missing_start = add_body(uuid4())
    del missing_start["values"]["start_date"]
    response = await op(world, "owner", missing_start)
    assert response.status_code == 422 and response.json()["detail"] == "invalid_hr_input"
    assert await counts(world) == before


async def test_write_functions_are_pinned(world: HrWorld) -> None:
    roles = ("csa_app", "csa_ro", "csa_identity", "csa_hr_maintenance", "public")
    for signature in WRITE_FUNCTIONS:
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
        assert row["csa_app"] is True, signature
        assert not any(row[role] for role in roles[1:]), signature


async def test_people_manager_cannot_make_themselves_a_manager(world: HrWorld) -> None:
    # The holder is the "manager" user, linked to the manager employee.
    await make_people_manager(world, "manager")
    me, outsider = world.employees["manager"], world.employees["outsider"]
    before = await counts(world)
    await refused(world, "manager", add_body(uuid4(), manager_id=str(me)), 409, "manager_is_you")
    assert await counts(world) == before
    update = await update_body(world, outsider, manager_id=str(me))
    await refused(world, "manager", update, 409, "manager_is_you")
    assert (await person_row(world, outsider))["manager_id"] is None
    # Someone else as the manager is fine, on add and on update.
    other = world.employees["owner"]
    added = uuid4()
    await ok(world, "manager", add_body(added, manager_id=str(other)))
    assert (await person_row(world, added))["manager_id"] == other
    await ok(world, "manager", await update_body(world, outsider, manager_id=str(other)))
    assert (await person_row(world, outsider))["manager_id"] == other
    # A reporting line the owner already set to the holder can be kept while editing.
    staff = world.employees["staff"]
    assert (await person_row(world, staff))["manager_id"] == me
    await ok(world, "manager", await update_body(world, staff, job_title="Senior Trainer"))
    assert (await person_row(world, staff))["manager_id"] == me
    # The HR owner may make themselves anyone's manager.
    mine = world.employees["owner"]
    owner_added = uuid4()
    await ok(world, "owner", add_body(owner_added, manager_id=str(mine)))
    assert (await person_row(world, owner_added))["manager_id"] == mine
    await ok(world, "owner", await update_body(world, outsider, manager_id=str(mine)))
    assert (await person_row(world, outsider))["manager_id"] == mine
    # After the owner's change the holder still cannot move the line back to themselves.
    back = await update_body(world, outsider, manager_id=str(me))
    await refused(world, "manager", back, 409, "manager_is_you")


async def test_people_manager_cannot_change_their_own_manager(world: HrWorld) -> None:
    # The holder is the "staff" user, linked to the staff employee, who reports to manager.
    await make_people_manager(world, "staff")
    staff, manager = world.employees["staff"], world.employees["manager"]
    for chosen in (world.employees["owner"], None):
        body = await update_body(world, staff, manager_id=str(chosen) if chosen else None)
        await refused(world, "staff", body, 409, "own_manager_owner_only")
    assert (await person_row(world, staff))["manager_id"] == manager
    # Their own title, with the manager unchanged, saves.
    await ok(world, "staff", await update_body(world, staff, job_title="Senior Trainer"))
    row = await person_row(world, staff)
    assert row["job_title"] == "Senior Trainer" and row["manager_id"] == manager
    # They may still change someone else's manager.
    outsider = world.employees["outsider"]
    await ok(world, "staff", await update_body(world, outsider, manager_id=str(manager)))
    assert (await person_row(world, outsider))["manager_id"] == manager
    # The HR owner changes the holder's manager, and clears it.
    await ok(
        world, "owner", await update_body(world, staff, manager_id=str(world.employees["owner"]))
    )
    assert (await person_row(world, staff))["manager_id"] == world.employees["owner"]
    await ok(world, "owner", await update_body(world, staff, manager_id=None))
    assert (await person_row(world, staff))["manager_id"] is None
    # A holder with no linked employee is not limited by this rule.
    await make_people_manager(world)
    await ok(world, "operator", await update_body(world, staff, manager_id=str(manager)))
    assert (await person_row(world, staff))["manager_id"] == manager


# --- Task 4: the people reads over HTTP --------------------------------------------------------

BASE = "/console/v1/hr"
PEOPLE = f"{BASE}/people"


async def get(world: HrWorld, who: str, path: str) -> httpx.Response:
    return await world.call(who, "GET", path)


async def get_ok(world: HrWorld, who: str, path: str) -> dict[str, Any]:
    response = await get(world, who, path)
    assert response.status_code == 200, response.text
    return response.json()


async def get_refused(world: HrWorld, who: str, path: str, status: int, detail: str) -> None:
    response = await get(world, who, path)
    assert (response.status_code, response.json()["detail"]) == (status, detail), response.text


async def deactivate(world: HrWorld, name: str) -> None:
    await update_as(
        world,
        "owner",
        "UPDATE hr_employees SET is_active=false WHERE client_id=%s AND id=%s",
        (world.tenant, world.employees[name]),
    )


async def test_people_page_search_filters_and_paging(world: HrWorld) -> None:
    aina = uuid4()
    await ok(
        world,
        "owner",
        add_body(aina, display_name="Nur Aina binti Rahman", start_date="2035-01-01"),
    )
    for number in range(59):
        name = f"Synthetic Person {number:02d}"
        await ok(world, "owner", add_body(uuid4(), display_name=name, start_date="2020-01-01"))
    learning = (await departments(world))["Learning"]

    first = await get_ok(world, "operator", PEOPLE)
    assert (len(first["items"]), first["next_cursor"], first["total"]) == (50, "50", 64)
    names = [item["display_name"] for item in first["items"]]
    assert names == sorted(names, key=str.casefold)
    second = await get_ok(world, "operator", f"{PEOPLE}?cursor=50")
    assert (len(second["items"]), second["next_cursor"], second["total"]) == (14, None, 64)
    seen = {item["employee_id"] for item in first["items"] + second["items"]}
    assert len(seen) == 64
    # The operator is a directory viewer: no HR-only fields, though the keys are present.
    for item in first["items"]:
        assert item["employment_profile"] is None
        assert item["version"] is None and item["account_linked"] is None
        assert item["status"] in {"active", "starting"}

    found = await get_ok(world, "operator", f"{PEOPLE}?q=Rahman%20Aina")
    assert [(i["employee_id"], i["display_name"]) for i in found["items"]] == [
        (str(aina), "Nur Aina binti Rahman")
    ]
    assert found["total"] == 1 and found["next_cursor"] is None
    assert (await get_ok(world, "operator", f"{PEOPLE}?q=Nobody%20Here"))["total"] == 0

    in_learning = await get_ok(world, "operator", f"{PEOPLE}?department={learning}")
    assert in_learning["total"] == 4
    assert {i["department"] for i in in_learning["items"]} == {"Learning"}
    assert {i["department_id"] for i in in_learning["items"]} == {str(learning)}
    none = await get_ok(world, "operator", f"{PEOPLE}?department=none")
    assert none["total"] == 60 and all(i["department_id"] is None for i in none["items"][:50])

    reports = await get_ok(world, "operator", f"{PEOPLE}?manager={world.employees['manager']}")
    assert {i["employee_id"] for i in reports["items"]} == {
        str(world.employees["staff"]),
        str(world.employees["owner"]),
    }
    assert {i["manager_name"] for i in reports["items"]} == {"Synthetic Staff"}
    # A starter with a future start date shows as starting.
    starters = await get_ok(world, "operator", f"{PEOPLE}?status=starting&q=Rahman")
    assert starters["total"] == 1 and starters["items"][0]["status"] == "starting"
    assert (await get_ok(world, "operator", f"{PEOPLE}?status=active"))["total"] == 63

    # The HR viewer sees the HR-only fields on the same list.
    owner_first = (await get_ok(world, "owner", f"{PEOPLE}?q=Rahman"))["items"][0]
    assert owner_first["account_linked"] is False and isinstance(owner_first["version"], int)


async def test_left_people_are_hr_only(world: HrWorld) -> None:
    await deactivate(world, "outsider")
    outsider = str(world.employees["outsider"])
    await get_refused(world, "operator", f"{PEOPLE}?status=left", 404, "resource_unavailable")
    shown = await get_ok(world, "owner", f"{PEOPLE}?status=left")
    assert [(i["employee_id"], i["status"]) for i in shown["items"]] == [(outsider, "left")]
    assert shown["total"] == 1
    for who in ("operator", "owner"):
        default = await get_ok(world, who, PEOPLE)
        assert default["total"] == 3, who
        assert outsider not in {i["employee_id"] for i in default["items"]}, who
    # The operator is not told a Left person exists, even by id.
    await get_refused(world, "operator", f"{PEOPLE}/{outsider}", 404, "resource_unavailable")
    profile = await get_ok(world, "owner", f"{PEOPLE}/{outsider}")
    assert profile["person"]["status"] == "left"
    # A people manager holds the HR view.
    await make_people_manager(world)
    left = await get_ok(world, "operator", f"{PEOPLE}?status=left")
    assert [i["employee_id"] for i in left["items"]] == [outsider]


async def test_self_viewer_has_no_people_list_but_sees_own_profile(world: HrWorld) -> None:
    await get_refused(world, "staff", PEOPLE, 404, "resource_unavailable")
    profile = await get_ok(world, "staff", f"{PEOPLE}/{world.employees['staff']}")
    assert profile["sections"] == ["overview"] and profile["can_edit"] is False
    assert profile["person"]["employee_id"] == str(world.employees["staff"])
    assert profile["person"]["version"] is None and profile["person"]["account_linked"] is None
    for other in ("manager", "owner", "outsider"):
        await get_refused(
            world, "staff", f"{PEOPLE}/{world.employees[other]}", 404, "resource_unavailable"
        )
    for path in ("/org-chart", "/departments"):
        await get_refused(world, "staff", f"{BASE}{path}", 404, "resource_unavailable")
    await get_refused(
        world,
        "staff",
        f"{PEOPLE}/{world.employees['staff']}/history",
        404,
        "resource_unavailable",
    )
    # A departed person no longer sees their own profile.
    await deactivate(world, "staff")
    await get_refused(
        world, "staff", f"{PEOPLE}/{world.employees['staff']}", 404, "resource_unavailable"
    )


async def test_profile_sections_and_editing_by_viewer(world: HrWorld) -> None:
    staff = world.employees["staff"]
    await update_as(
        world,
        "owner",
        "UPDATE hr_employees SET region='sabah', legal_entity_reference='Synthetic Sdn Bhd', "
        "employment_category='permanent' WHERE client_id=%s AND id=%s",
        (world.tenant, staff),
    )
    owner = await get_ok(world, "owner", f"{PEOPLE}/{staff}")
    assert owner["sections"] == ["overview", "access", "history"] and owner["can_edit"] is True
    assert isinstance(owner["person"]["version"], int)
    assert owner["person"]["employment_profile"] == {
        "region": "sabah",
        "legal_entity_reference": "Synthetic Sdn Bhd",
        "employment_category": "permanent",
    }
    assert owner["person"]["account_linked"] is True
    assert owner["person"]["status"] == "active"

    response = await get(world, "operator", f"{PEOPLE}/{staff}")
    assert response.status_code == 200, response.text
    operator = response.json()
    assert operator["sections"] == ["overview"] and operator["can_edit"] is False
    assert operator["person"]["employment_profile"] is None
    assert operator["person"]["version"] is None and operator["person"]["account_linked"] is None
    assert "Synthetic Sdn Bhd" not in response.text and "sabah" not in response.text
    assert operator["person"]["manager_name"] == "Synthetic Staff"
    assert operator["person"]["department"] == "Learning"

    # An id nobody has, and another tenant's employee, are the same answer.
    foreign = uuid4()
    await world.q(
        "INSERT INTO hr_employees(client_id,id,display_name) VALUES (%s,%s,'Synthetic Foreign')",
        (world.other_tenant, foreign),
    )
    for who in ("owner", "operator"):
        for missing in (uuid4(), foreign):
            await get_refused(world, who, f"{PEOPLE}/{missing}", 404, "resource_unavailable")
    # A people manager is an HR viewer who can edit.
    await make_people_manager(world)
    holder = await get_ok(world, "operator", f"{PEOPLE}/{staff}")
    assert holder["sections"] == ["overview", "access", "history"] and holder["can_edit"] is True


async def test_history_and_access_reads_are_hr_only(world: HrWorld) -> None:
    names = await name_users(world)
    staff = world.employees["staff"]
    await ok(world, "owner", await update_body(world, staff, job_title="Lead Trainer"))
    history = await get_ok(world, "owner", f"{PEOPLE}/{staff}/history")
    assert history["truncated"] is False and len(history["items"]) == 2
    latest, created = history["items"]
    assert latest["actor_name"] == names["owner"]
    assert latest["fields"] == [{"field": "job_title", "old": "Trainer", "new": "Lead Trainer"}]
    assert created["actor_name"] is None
    assert {f["field"] for f in created["fields"]} >= {"display_name", "job_title", "department"}
    assert latest["changed_at"] >= created["changed_at"]

    for who in ("operator", "staff", "manager"):
        for tail in ("history", "access"):
            await get_refused(world, who, f"{PEOPLE}/{staff}/{tail}", 404, "resource_unavailable")
    # A people manager is HR, and reads both.
    await make_people_manager(world)
    assert (await get_ok(world, "operator", f"{PEOPLE}/{staff}/history"))["truncated"] is False
    await get_ok(world, "operator", f"{PEOPLE}/{staff}/access")

    access = await get_ok(world, "owner", f"{PEOPLE}/{staff}/access")
    assert access["account"] == {
        "user_id": str(world.users["staff"]),
        "display_name": names["staff"],
    }
    assert access["on"] == [] and access["is_owner"] is False
    assert "Sees their own training requests" in access["custom"]
    # With the leave and attendance rows added, the staff grants are the own_requests switch.
    for domain in ("attendance", "leave"):
        for action in ("read", "prepare"):
            await workflow_grant(world, "staff", domain, action, "self")
    access = await get_ok(world, "owner", f"{PEOPLE}/{staff}/access")
    assert access["on"] == ["own_requests"] and access["custom"] == []
    mine = await get_ok(world, "owner", f"{PEOPLE}/{world.employees['owner']}/access")
    assert mine["is_owner"] is True and mine["account"]["display_name"] == names["owner"]

    # No linked account: an empty answer, not an error.
    unlinked = uuid4()
    await ok(world, "owner", add_body(unlinked))
    empty = await get_ok(world, "owner", f"{PEOPLE}/{unlinked}/access")
    assert empty == {"account": None, "on": [], "custom": [], "is_owner": False}
    assert (await get_ok(world, "owner", f"{PEOPLE}/{unlinked}/history"))["items"] != []
    await get_refused(world, "owner", f"{PEOPLE}/{uuid4()}/access", 404, "resource_unavailable")


async def test_history_is_cut_at_two_hundred(world: HrWorld) -> None:
    staff = world.employees["staff"]

    async def retitle(number: int) -> None:
        await world.q(
            "UPDATE hr_employees SET job_title=%s WHERE client_id=%s AND id=%s",
            (f"Title {number}", world.tenant, staff),
        )

    # The fixture insert is one row; 199 updates make exactly 200.
    for number in range(199):
        await retitle(number)
    exact = await get_ok(world, "owner", f"{PEOPLE}/{staff}/history")
    assert (len(exact["items"]), exact["truncated"]) == (200, False)
    await retitle(199)
    cut = await get_ok(world, "owner", f"{PEOPLE}/{staff}/history")
    assert (len(cut["items"]), cut["truncated"]) == (200, True)
    # Newest first: the oldest row (the insert) is the one left out.
    assert cut["items"][0]["fields"] == [
        {"field": "job_title", "old": "Title 198", "new": "Title 199"}
    ]
    assert cut["items"][-1]["fields"] == [
        {"field": "job_title", "old": "Trainer", "new": "Title 0"}
    ]


async def test_org_chart_and_departments(world: HrWorld) -> None:
    await deactivate(world, "outsider")
    chart = await get_ok(world, "operator", f"{BASE}/org-chart")
    assert chart["looped"] == []
    assert {n["employee_id"] for n in chart["nodes"]} == {
        str(world.employees[n]) for n in ("staff", "manager", "owner")
    }
    assert str(world.employees["outsider"]) not in {n["employee_id"] for n in chart["nodes"]}
    for node in chart["nodes"]:
        assert set(node) == {"employee_id", "display_name", "job_title", "department", "manager_id"}
    parents = {n["employee_id"]: n["manager_id"] for n in chart["nodes"]}
    assert parents[str(world.employees["staff"])] == str(world.employees["manager"])
    assert parents[str(world.employees["manager"])] is None

    listed = await get_ok(world, "operator", f"{BASE}/departments")
    assert [(d["name"], d["people"], d["archived"]) for d in listed["items"]] == [
        ("Learning", 3, False)
    ]
    assert isinstance(listed["items"][0]["version"], int)
    owner = await get_ok(world, "owner", f"{BASE}/departments")
    assert owner == listed
    # A loop in the lines is reported, and its members are shown as roots.
    await world.q(
        "UPDATE hr_employees SET manager_id=%s WHERE client_id=%s AND id=%s",
        (world.employees["staff"], world.tenant, world.employees["manager"]),
    )
    looped = await get_ok(world, "operator", f"{BASE}/org-chart")
    assert set(looped["looped"]) == {str(world.employees[n]) for n in ("staff", "manager")}
    assert {n["employee_id"]: n["manager_id"] for n in looped["nodes"]}[
        str(world.employees["manager"])
    ] is None


async def test_home_counts_training_and_requests_waiting(world: HrWorld) -> None:
    zero = {"training_waiting": 0, "requests_waiting": 0}
    for who in ("manager", "staff", "operator", "owner"):
        assert await get_ok(world, who, f"{BASE}/home") == zero, who
    await world.submit("staff")
    assert await get_ok(world, "manager", f"{BASE}/home") == {**zero, "training_waiting": 1}
    assert await get_ok(world, "staff", f"{BASE}/home") == zero
    assert await get_ok(world, "operator", f"{BASE}/home") == zero

    await workflow_grant(world, "staff", "attendance", "prepare", "self")
    await workflow_grant(world, "staff", "attendance", "read", "self")
    await workflow_grant(world, "manager", "attendance", "decide", "assigned_manager")
    prepared = await world.call("staff", "POST", f"{BASE}/workflows", ATTENDANCE)
    assert prepared.status_code == 201, prepared.text
    assert await get_ok(world, "manager", f"{BASE}/home") == {
        "training_waiting": 1,
        "requests_waiting": 1,
    }
    assert await get_ok(world, "staff", f"{BASE}/home") == zero
    # Decided requests no longer wait.
    decided = await world.call(
        "manager",
        "POST",
        f"{BASE}/workflows/{prepared.json()['id']}/transitions",
        {"action": "approve", "expected_version": 0},
    )
    assert decided.status_code == 200, decided.text
    assert (await get_ok(world, "manager", f"{BASE}/home"))["requests_waiting"] == 0
    # A login with no HR rows at all still gets zeros.
    assert await get_ok(world, "outsider", f"{BASE}/home") == zero


async def test_context_reports_switches_workflows_and_duties(world: HrWorld) -> None:
    async def context(who: str) -> dict[str, Any]:
        return await get_ok(world, who, f"{BASE}/context")

    owner = await context("owner")
    assert (owner["switches"], owner["hr_duties"], owner["client_owner"]) == ([], True, True)
    staff = await context("staff")
    assert (staff["switches"], staff["workflows"], staff["hr_duties"]) == ([], [], False)
    operator = await context("operator")
    assert (operator["switches"], operator["hr_duties"]) == (["directory"], False)
    await make_people_manager(world)
    operator = await context("operator")
    assert operator["switches"] == ["directory", "manage_people"] and operator["hr_duties"] is True

    await workflow_grant(world, "staff", "leave", "read", "self")
    await workflow_grant(world, "staff", "attendance", "read", "self")
    await workflow_grant(world, "staff", "leave", "prepare", "self")
    staff = await context("staff")
    assert staff["workflows"] == ["attendance.read", "leave.prepare", "leave.read"]
    assert staff["switches"] == [] and staff["hr_duties"] is False
    await workflow_grant(world, "staff", "attendance", "prepare", "self")
    staff = await context("staff")
    assert staff["switches"] == ["own_requests"] and staff["hr_duties"] is False
    # A private area turns its switch on and counts as an HR duty.
    await world.q(
        "INSERT INTO hr_private_grants(client_id,user_id,area,approved_by) "
        "VALUES (%s,%s,'recruitment',%s)",
        (world.tenant, world.users["staff"], world.users["owner"]),
    )
    staff = await context("staff")
    assert staff["switches"] == ["own_requests", "recruitment"] and staff["hr_duties"] is True
    assert staff["private_areas"] == ["recruitment"]


async def test_records_interviewers_need_recruitment_area_or_owner(world: HrWorld) -> None:
    names = await name_users(world)
    path = f"{BASE}/records/interviewers"
    await get_refused(world, "operator", path, 404, "resource_unavailable")
    await get_refused(world, "staff", path, 404, "resource_unavailable")
    await world.q(
        "UPDATE console_memberships SET is_enabled=false WHERE client_id=%s AND user_id=%s",
        (world.tenant, world.users["outsider"]),
    )
    owner = await get_ok(world, "owner", path)
    members = {i["user_id"]: i["display_name"] for i in owner["items"]}
    assert members == {
        str(user): names[name] for name, user in world.users.items() if name != "outsider"
    }
    assert all(set(item) == {"user_id", "display_name"} for item in owner["items"])
    await world.q(
        "INSERT INTO hr_private_grants(client_id,user_id,area,approved_by) "
        "VALUES (%s,%s,'recruitment',%s)",
        (world.tenant, world.users["operator"], world.users["owner"]),
    )
    assert await get_ok(world, "operator", path) == owner


async def fresh_tenant(world: HrWorld, *, owner: bool) -> tuple[UUID, httpx.AsyncClient]:
    """A new tenant with one member (the HR owner, or a plain member) and no employees."""
    tenant, user, session = uuid4(), uuid4(), uuid4()
    token, csrf = f"synthetic-session-{uuid4()}", f"synthetic-csrf-{uuid4()}"
    await world.q(
        "INSERT INTO clients(id,slug,name,platform_version) VALUES (%s,%s,%s,'test')",
        (tenant, f"hr-{tenant}", "Synthetic empty tenant"),
    )
    await world.q(
        "INSERT INTO console_service_packages "
        "(client_id, package, status, activated_at, reference) "
        "VALUES (%s, 'hr', 'active', now(), 'test-fixture')",
        (tenant,),
    )
    await world.q(
        "INSERT INTO console_users(id,email,display_name) VALUES (%s,%s,'Synthetic Member')",
        (user, f"synthetic-{user}@example.test"),
    )
    await world.q(
        "INSERT INTO console_memberships(user_id,client_id,role) VALUES (%s,%s,%s)",
        (user, tenant, "tenant_admin" if owner else "tenant_staff"),
    )
    await world.q(
        "INSERT INTO console_sessions(id,user_id,token_hash,csrf_hash,client_id,expires_at) "
        "VALUES (%s,%s,%s,%s,%s,now()+interval '1 hour')",
        (session, user, digest(token), digest(csrf), tenant),
    )
    if owner:
        await world.q(
            "INSERT INTO hr_client_owners(client_id,user_id,approved_by) VALUES (%s,%s,%s)",
            (tenant, user, user),
        )
    browser = httpx.AsyncClient(transport=httpx.ASGITransport(app=world.app), base_url=ORIGIN)
    browser.cookies.set("csa_session", token)
    browser.cookies.set("csa_csrf", csrf)
    world.browsers[f"fresh-{tenant}"] = browser  # closed with the world
    return tenant, browser


async def fresh_get(browser: httpx.AsyncClient, path: str) -> httpx.Response:
    return await browser.get(path, headers={"Origin": ORIGIN})


async def test_people_reads_on_an_empty_tenant(world: HrWorld) -> None:
    tenant, owner = await fresh_tenant(world, owner=True)

    async def read(path: str) -> Any:
        response = await fresh_get(owner, path)
        assert response.status_code == 200, (path, response.text)
        return response.json()

    # No employees and no departments, and the owner has no employee link.
    assert await read(PEOPLE) == {"items": [], "next_cursor": None, "total": 0}
    assert await read(f"{PEOPLE}?status=left") == {"items": [], "next_cursor": None, "total": 0}
    assert await read(f"{PEOPLE}?q=anyone&department=none") == {
        "items": [],
        "next_cursor": None,
        "total": 0,
    }
    assert await read(f"{BASE}/org-chart") == {"nodes": [], "looped": []}
    assert await read(f"{BASE}/departments") == {"items": []}
    assert await read(f"{BASE}/home") == {"training_waiting": 0, "requests_waiting": 0}
    assert await read(f"{BASE}/records/employees") == {"items": []}
    context = await read(f"{BASE}/context")
    assert context["employee"] is None and context["client_owner"] is True
    assert (context["switches"], context["workflows"], context["hr_duties"]) == ([], [], True)
    members = await read(f"{BASE}/records/interviewers")
    assert [i["display_name"] for i in members["items"]] == ["Synthetic Member"]
    missing = uuid4()
    for tail in ("", "/history", "/access"):
        response = await fresh_get(owner, f"{PEOPLE}/{missing}{tail}")
        assert (response.status_code, response.json()["detail"]) == (404, "resource_unavailable")

    # Employees but no departments: people list without a department, an empty department list.
    person = uuid4()
    await world.q(
        "INSERT INTO hr_employees(client_id,id,display_name) VALUES (%s,%s,'Synthetic Solo')",
        (tenant, person),
    )
    solo = await read(f"{PEOPLE}?department=none")
    assert solo["total"] == 1 and solo["items"][0]["department_id"] is None
    assert solo["items"][0]["manager_name"] is None
    assert (await read(f"{BASE}/departments")) == {"items": []}
    assert [n["employee_id"] for n in (await read(f"{BASE}/org-chart"))["nodes"]] == [str(person)]
    assert (await read(f"{PEOPLE}/{person}/access"))["account"] is None
    assert (await read(f"{PEOPLE}/{person}"))["person"]["employee_id"] == str(person)

    # A tenant with no HR owner: a plain member has a context and zero counts, and no HR duties.
    _, member = await fresh_tenant(world, owner=False)
    context = (await fresh_get(member, f"{BASE}/context")).json()
    assert context["client_owner"] is False and context["employee"] is None
    assert (context["switches"], context["workflows"], context["hr_duties"]) == ([], [], False)
    assert (await fresh_get(member, f"{BASE}/home")).json() == {
        "training_waiting": 0,
        "requests_waiting": 0,
    }
    for path in (
        PEOPLE,
        f"{BASE}/org-chart",
        f"{BASE}/departments",
        f"{BASE}/records/interviewers",
    ):
        response = await fresh_get(member, path)
        assert (response.status_code, response.json()["detail"]) == (404, "resource_unavailable")
    # The first tenant's person never shows in the second.
    assert (await fresh_get(member, f"{PEOPLE}/{person}")).status_code == 404


async def test_people_query_keys_are_allowlisted(world: HrWorld) -> None:
    await get_refused(world, "owner", f"{PEOPLE}?x=1", 400, "invalid_hr_query")
    await get_refused(world, "owner", f"{PEOPLE}?q=a&q=b", 400, "invalid_hr_query")
    await get_refused(world, "owner", f"{PEOPLE}?limit=5", 400, "invalid_hr_query")
    await get_refused(world, "owner", f"{BASE}/departments?q=a", 400, "invalid_hr_query")
    await get_refused(world, "owner", f"{PEOPLE}?department=nope", 409, "invalid_department_filter")
    await get_refused(world, "owner", f"{PEOPLE}?cursor=abc", 409, "invalid_cursor")
    for bad in ("status=bogus", f"department={'x' * 37}", "manager=not-a-uuid", f"q={'x' * 121}"):
        response = await get(world, "owner", f"{PEOPLE}?{bad}")
        assert response.status_code == 422, bad
        assert response.json()["detail"] == "invalid_hr_input"
    # A filter that is allowed goes through, and the cursor passes the end of the list.
    ok_page = await get_ok(world, "owner", f"{PEOPLE}?department=none&cursor=99")
    assert ok_page == {"items": [], "next_cursor": None, "total": 0}
