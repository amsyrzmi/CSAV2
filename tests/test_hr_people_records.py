"""Migration 0521: the Sensitive details switch, personal details, documents, leaving
(disposable database). Reuses the HrWorld fixture of tests/test_hr_people.py.
"""

from __future__ import annotations

import psycopg
import pytest
from csa.core.db import q
from csa.core.tenancy import tx

from tests.conftest import requires_docker
from tests.test_hr_people import (
    make_people_manager,
    read_as,
    read_as_other_tenant_owner,
    refused,
)
from tests.test_hr_persistence import HrWorld, hr_database, world

__all__ = ["hr_database", "world"]
pytestmark = [requires_docker, pytest.mark.asyncio]


async def make_sensitive_holder(world: HrWorld, name: str = "operator") -> None:
    await world.grant(name, "people.sensitive", "hr_operator")


async def test_people_sensitive_grant_check_and_resolution(world: HrWorld) -> None:
    # The owner's access.save accepts the new grant.
    saved = await world.call(
        "owner",
        "POST",
        "/console/v1/hr/operations",
        {
            "operation": "access.save",
            "user_id": str(world.users["operator"]),
            "grants": [{"action": "people.sensitive", "scope": "hr_operator", "fields": []}],
            "workflows": [],
        },
    )
    assert saved.status_code == 200, saved.text
    stored = await world.q(
        "SELECT action,scope,fields FROM hr_grants WHERE client_id=%s AND user_id=%s "
        "AND action='people.sensitive'",
        (world.tenant, world.users["operator"]),
    )
    assert stored == [{"action": "people.sensitive", "scope": "hr_operator", "fields": []}]
    # The CHECK keeps the hr_operator scope only, and no fields.
    for scope in ("self", "assigned_manager", "client_owner"):
        with pytest.raises(psycopg.errors.CheckViolation):
            await world.grant("staff", "people.sensitive", scope)
    with pytest.raises(psycopg.errors.CheckViolation):
        await world.grant("staff", "people.sensitive", "hr_operator", ["display_name"])
    # The earlier pairs are still there.
    await world.grant("manager", "people.manage", "hr_operator")
    with pytest.raises(psycopg.errors.CheckViolation):
        await world.grant("staff", "training.decide", "hr_operator")
    checks = await world.q(
        "SELECT conname FROM pg_constraint WHERE conrelid='public.hr_grants'::regclass "
        "AND contype='c' AND pg_get_constraintdef(oid) LIKE '%%client_owner%%'"
    )
    assert checks == [{"conname": "hr_grants_scope_action"}]

    holder = await read_as(world, "operator", "hr/sensitive_viewer.sql", (world.tenant,))
    assert holder == [{"sensitive": True}]
    # A people.manage holder does not see sensitive details; the HR owner does.
    assert await read_as(world, "manager", "hr/sensitive_viewer.sql", (world.tenant,)) == [
        {"sensitive": False}
    ]
    assert await read_as(world, "owner", "hr/sensitive_viewer.sql", (world.tenant,)) == [
        {"sensitive": True}
    ]
    assert await read_as(world, "staff", "hr/sensitive_viewer.sql", (world.tenant,)) == [
        {"sensitive": False}
    ]
    # Another tenant's real owner gets false for this tenant, and a session without an HR user too.
    assert await read_as_other_tenant_owner(world, "hr/sensitive_viewer.sql") == [
        {"sensitive": False}
    ]
    async with tx(world.service.pool, str(world.tenant)) as conn:
        assert await q.fetch_all("hr/sensitive_viewer.sql", (world.tenant,), conn=conn) == [
            {"sensitive": False}
        ]


async def test_people_manager_cannot_grant_sensitive_details(world: HrWorld) -> None:
    await make_people_manager(world)
    # The API turns permission_denied into a content-free 404, like every owner-only refusal.
    await refused(
        world,
        "operator",
        {
            "operation": "access.save",
            "user_id": str(world.users["staff"]),
            "grants": [{"action": "people.sensitive", "scope": "hr_operator", "fields": []}],
            "workflows": [],
        },
        404,
        "resource_unavailable",
    )
    assert (
        await world.q(
            "SELECT 1 FROM hr_grants WHERE client_id=%s AND action='people.sensitive'",
            (world.tenant,),
        )
        == []
    )
    # Holding Sensitive details does not grant access changes either.
    await make_sensitive_holder(world, "manager")
    await refused(
        world,
        "manager",
        {
            "operation": "access.save",
            "user_id": str(world.users["staff"]),
            "grants": [{"action": "people.sensitive", "scope": "hr_operator", "fields": []}],
            "workflows": [],
        },
        404,
        "resource_unavailable",
    )
