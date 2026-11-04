"""Migration 0482: the owner-only access summary and reporting-line reads (disposable database)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from apps.gateway.main import EXPECTED_SCHEMA_VERSION
from csa.core.db import q
from csa.core.tenancy import tx

from tests.conftest import requires_docker
from tests.test_hr_persistence import FIELDS, HrWorld, hr_database, workflow_grant, world

__all__ = ["hr_database", "world"]
pytestmark = [requires_docker, pytest.mark.asyncio]


async def read_as(
    world: HrWorld, name: str, sql: str, client: UUID | None = None
) -> list[dict[str, Any]]:
    """Run an owner-only read the way the service does: in `tx`, inside the caller's HR session."""
    async with tx(world.service.pool, str(world.tenant)) as conn:
        await world.service._session(conn, world.proof(name))
        return await q.fetch_all(sql, (client or world.tenant,), conn=conn)


async def read_as_other_tenant_owner(world: HrWorld, sql: str) -> list[dict[str, Any]]:
    """The other tenant's real owner asks about `world.tenant`."""
    async with tx(world.service.pool, str(world.other_tenant)) as conn:
        await conn.execute(
            "SELECT set_config('app.hr_user_id', %s, true)", (str(world.users["owner"]),)
        )
        return await q.fetch_all(sql, (world.tenant,), conn=conn)


async def make_other_tenant_owner(world: HrWorld) -> None:
    await world.q(
        "INSERT INTO console_memberships(user_id,client_id,role) VALUES (%s,%s,'tenant_admin')",
        (world.users["owner"], world.other_tenant),
    )
    await world.q(
        "INSERT INTO hr_client_owners(client_id,user_id,approved_by) VALUES (%s,%s,%s)",
        (world.other_tenant, world.users["owner"], world.users["owner"]),
    )


async def test_summary_returns_every_members_rows_for_the_owner_only(world: HrWorld) -> None:
    await workflow_grant(world, "staff", "leave", "read", "self")
    await workflow_grant(world, "manager", "leave", "decide", "assigned_manager")
    await workflow_grant(world, "manager", "attendance", "read", "assigned_manager")
    for area in ("payroll", "attendance"):
        await world.q(
            "INSERT INTO hr_private_grants(client_id,user_id,area,approved_by) "
            "VALUES (%s,%s,%s,%s)",
            (world.tenant, world.users["operator"], area, world.users["owner"]),
        )
    # A disabled member is not listed.
    await world.q(
        "UPDATE console_memberships SET is_enabled=false WHERE client_id=%s AND user_id=%s",
        (world.tenant, world.users["outsider"]),
    )
    await make_other_tenant_owner(world)

    rows = await read_as(world, "owner", "hr/access_summary.sql")
    by_user = {row["user_id"]: row for row in rows}
    assert len(rows) == len(by_user) == len(world.users) - 1
    assert world.users["outsider"] not in by_user
    assert set(by_user) == {u for name, u in world.users.items() if name != "outsider"}

    staff = by_user[world.users["staff"]]
    assert [(g["action"], g["scope"]) for g in staff["grants"]] == [
        ("directory.read", "self"),
        ("training.draft", "self"),
        ("training.read", "self"),
        ("training.submit", "self"),
        ("training.withdraw", "self"),
    ]
    assert staff["grants"][0]["fields"] == FIELDS and staff["grants"][1]["fields"] == []
    assert staff["workflows"] == [{"domain": "leave", "action": "read", "scope": "self"}]
    assert staff["areas"] == []
    assert by_user[world.users["manager"]]["workflows"] == [
        {"domain": "attendance", "action": "read", "scope": "assigned_manager"},
        {"domain": "leave", "action": "decide", "scope": "assigned_manager"},
    ]
    assert by_user[world.users["operator"]]["areas"] == ["attendance", "payroll"]
    # A member with nothing saved gets empty arrays, not NULL.
    assert by_user[world.users["admin"]]["grants"] == []
    assert (
        by_user[world.users["admin"]]["workflows"] == []
        and by_user[world.users["admin"]]["areas"] == []
    )
    assert rows == sorted(rows, key=lambda r: (r["display_name"].lower(), r["user_id"]))

    # A non-owner admin of the same tenant, and the other tenant's owner, get nothing.
    for name in ("admin", "manager", "staff"):
        assert await read_as(world, name, "hr/access_summary.sql") == []
    assert await read_as_other_tenant_owner(world, "hr/access_summary.sql") == []


async def test_summary_links_employee_team_size_and_owner_flag(world: HrWorld) -> None:
    # An inactive report does not count towards the manager's team.
    await world.q(
        "INSERT INTO hr_employees(client_id,id,display_name,manager_id,is_active) "
        "VALUES (%s,%s,%s,%s,false)",
        (world.tenant, uuid4(), "Synthetic Leaver", world.employees["manager"]),
    )
    rows = await read_as(world, "owner", "hr/access_summary.sql")
    by_user = {row["user_id"]: row for row in rows}

    manager = by_user[world.users["manager"]]
    assert manager["employee_id"] == world.employees["manager"]
    assert (
        manager["department"] == "Learning"
        and manager["active"] is True
        and manager["manager_id"] is None
    )
    assert manager["team_size"] == 2  # staff and owner report to the manager
    staff = by_user[world.users["staff"]]
    assert staff["manager_id"] == world.employees["manager"] and staff["team_size"] == 0

    assert by_user[world.users["owner"]]["is_owner"] is True
    assert [u for u, row in by_user.items() if row["is_owner"]] == [world.users["owner"]]

    unlinked = by_user[world.users["operator"]]
    assert unlinked["employee_id"] is None and unlinked["department"] is None
    assert unlinked["manager_id"] is None and unlinked["active"] is None
    assert unlinked["team_size"] == 0


async def test_summary_has_no_member_cap(world: HrWorld) -> None:
    await world.q(
        "INSERT INTO console_users(id,email,display_name) "
        "SELECT gen_random_uuid(), 'bulk-' || gen_random_uuid() || '@example.test', "
        "'Bulk ' || lpad(i::text, 3, '0') "
        "FROM generate_series(1, 205) AS i"
    )
    await world.q(
        "INSERT INTO console_memberships(user_id,client_id,role) "
        "SELECT id, %s, 'tenant_staff' FROM console_users WHERE display_name LIKE 'Bulk %%'",
        (world.tenant,),
    )
    rows = await read_as(world, "owner", "hr/access_summary.sql")
    bulk = [row for row in rows if row["display_name"].startswith("Bulk ")]
    assert len(bulk) == 205 and len({row["user_id"] for row in bulk}) == 205
    assert len(rows) == 205 + len(world.users)
    assert [row["display_name"] for row in bulk] == sorted(row["display_name"] for row in bulk)


async def test_reporting_lines_name_the_managers_console_account(world: HrWorld) -> None:
    ghost_manager, ghost_report = uuid4(), uuid4()
    disabled_manager, disabled_report = uuid4(), uuid4()
    for employee, name, manager in (
        (ghost_manager, "Synthetic Ghost Manager", None),
        (ghost_report, "Synthetic Ghost Report", ghost_manager),
        (disabled_manager, "Synthetic Disabled Manager", None),
        (disabled_report, "Synthetic Disabled Report", disabled_manager),
    ):
        await world.q(
            "INSERT INTO hr_employees(client_id,id,display_name,manager_id) VALUES (%s,%s,%s,%s)",
            (world.tenant, employee, name, manager),
        )
    # The disabled manager has an account, but the membership is switched off.
    disabled_user = uuid4()
    await world.q(
        "INSERT INTO console_users(id,email,display_name) VALUES (%s,%s,%s)",
        (disabled_user, f"synthetic-{disabled_user}@example.test", "Synthetic Disabled Manager"),
    )
    await world.q(
        "INSERT INTO console_memberships(user_id,client_id,role,is_enabled) "
        "VALUES (%s,%s,'tenant_staff',false)",
        (disabled_user, world.tenant),
    )
    await world.q(
        "INSERT INTO hr_employee_accounts(client_id,user_id,employee_id) VALUES (%s,%s,%s)",
        (world.tenant, disabled_user, disabled_manager),
    )
    # Inactive employees are not listed.
    inactive = uuid4()
    await world.q(
        "INSERT INTO hr_employees(client_id,id,display_name,is_active) VALUES (%s,%s,%s,false)",
        (world.tenant, inactive, "Synthetic Leaver"),
    )

    rows = await read_as(world, "owner", "hr/access_reporting_lines.sql")
    by_employee = {row["employee_id"]: row for row in rows}
    assert inactive not in by_employee and len(rows) == len(by_employee) == 8
    staff = by_employee[world.employees["staff"]]
    assert staff["manager_employee_id"] == world.employees["manager"]
    assert staff["manager_user_id"] == world.users["manager"]
    assert by_employee[world.employees["manager"]]["manager_employee_id"] is None
    assert by_employee[world.employees["manager"]]["manager_user_id"] is None
    ghost = by_employee[ghost_report]
    assert ghost["manager_employee_id"] == ghost_manager and ghost["manager_user_id"] is None
    off = by_employee[disabled_report]
    assert off["manager_employee_id"] == disabled_manager and off["manager_user_id"] is None

    for name in ("admin", "manager"):
        assert await read_as(world, name, "hr/access_reporting_lines.sql") == []
    await make_other_tenant_owner(world)
    assert await read_as_other_tenant_owner(world, "hr/access_reporting_lines.sql") == []


async def test_0482_is_recorded_and_expected(world: HrWorld) -> None:
    versions = [row["version"] for row in await world.q("SELECT version FROM schema_migrations")]
    assert "0482" in versions
    assert max(versions) == EXPECTED_SCHEMA_VERSION
