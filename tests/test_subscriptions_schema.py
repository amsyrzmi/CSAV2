"""Schema contract for migration 0480: subscription checks, decisions table, proposal kind."""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest
from psycopg import errors
from psycopg.rows import dict_row

from tests.conftest import requires_docker
from tests.test_console_sessions import SessionWorld

pytest_plugins = ["tests.test_console_sessions"]
pytestmark = [requires_docker, pytest.mark.asyncio]


async def _plan(world: SessionWorld, tenant: str, **overrides: Any) -> None:
    values = {"code": f"p{uuid.uuid4().hex[:8]}", "billing_interval": "month", **overrides}
    await world.q(
        "INSERT INTO plans (client_id, code, name, price, billing_interval)"
        " VALUES (%s, %s, 'Plan', 10, %s)",
        tenant,
        values["code"],
        values["billing_interval"],
    )


async def _subscription(world: SessionWorld, tenant: str, status: str = "active") -> str:
    [row] = await world.q(
        "INSERT INTO subscriptions (client_id, plan, status, price, started_at)"
        " VALUES (%s, 'basic', %s, 10, now()) RETURNING id",
        tenant,
        status,
    )
    return str(row["id"])


async def test_0480_constraints_table_grants_and_rls(sessions: SessionWorld) -> None:
    tenant = sessions.tenants[0]
    with pytest.raises(errors.CheckViolation):
        await _subscription(sessions, tenant, "trialing")
    for status in ("active", "paused", "cancelled"):
        await _subscription(sessions, tenant, status)
    [versioned] = await sessions.q(
        "SELECT version FROM subscriptions WHERE client_id = %s LIMIT 1", tenant
    )
    assert versioned["version"] == 1

    with pytest.raises(errors.CheckViolation):
        await _plan(sessions, tenant, billing_interval="week")
    for interval in ("month", "year"):
        await _plan(sessions, tenant, billing_interval=interval)

    [table] = await sessions.q(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
        " WHERE oid = 'public.subscription_request_decisions'::regclass"
    )
    assert table["relrowsecurity"] is True and table["relforcerowsecurity"] is False
    [policy] = await sessions.q(
        "SELECT qual, with_check FROM pg_policies"
        " WHERE tablename = 'subscription_request_decisions' AND policyname = 'tenant_isolation'"
    )
    expected = (
        "(client_id = (NULLIF(current_setting('app.client_id'::text, true), ''::text))::uuid)"
    )
    assert policy["qual"] == expected and policy["with_check"] == expected
    [grants] = await sessions.q(
        "SELECT"
        " has_table_privilege('csa_app', 'public.subscription_request_decisions', 'SELECT') AS sel,"
        " has_table_privilege('csa_app', 'public.subscription_request_decisions', 'INSERT') AS ins,"
        " has_table_privilege('csa_app', 'public.subscription_request_decisions', 'UPDATE') AS upd,"
        " has_table_privilege('csa_app', 'public.subscription_request_decisions', 'DELETE') AS del,"
        " has_table_privilege('csa_ro', 'public.subscription_request_decisions', 'SELECT') AS ro"
    )
    assert grants == {"sel": True, "ins": True, "upd": False, "del": False, "ro": True}

    # console_proposals accepts the new kind and still rejects an unknown one.
    [session] = await sessions.q(
        "SELECT id, user_id FROM console_sessions WHERE user_id = %s LIMIT 1", sessions.admin_id
    )
    insert = (
        "INSERT INTO console_proposals (client_id, user_id, login_session_id, kind, payload,"
        " payload_sha256, card, origin, status, expires_at)"
        " VALUES (%s, %s, %s, %s, '{}'::jsonb, %s, '{}'::jsonb, 'tab', 'requested',"
        " now() + interval '5 minutes') RETURNING id"
    )
    args = (tenant, session["user_id"], session["id"])
    [created] = await sessions.q(insert, *args, "subscription_action", "0" * 64)
    assert created["id"]
    with pytest.raises(errors.CheckViolation):
        await sessions.q(insert, *args, "not_a_kind", "0" * 64)
    [constraint] = await sessions.q(
        "SELECT conname FROM pg_constraint"
        " WHERE conrelid = 'public.console_proposals'::regclass AND contype = 'c'"
        " AND conname = 'console_proposals_kind_check'"
    )
    assert constraint["conname"] == "console_proposals_kind_check"


async def test_subscription_events_proposal_id_is_unique_when_present(
    sessions: SessionWorld,
) -> None:
    tenant = sessions.tenants[0]
    subscription = await _subscription(sessions, tenant)
    insert = (
        "INSERT INTO subscription_events (client_id, subscription_id, actor, action, proposal_id)"
        " VALUES (%s, %s, 'console', 'pause', %s)"
    )
    for _ in range(2):
        await sessions.q(insert, tenant, subscription, None)
    proposal = str(uuid.uuid4())
    await sessions.q(insert, tenant, subscription, proposal)
    with pytest.raises(errors.UniqueViolation):
        await sessions.q(insert, tenant, subscription, proposal)


async def test_rls_blocks_cross_tenant_subscription_reads_for_csa_app(
    sessions: SessionWorld,
) -> None:
    tenant_a, tenant_b = sessions.tenants
    mine = await _subscription(sessions, tenant_a)
    theirs = await _subscription(sessions, tenant_b)
    async with sessions.pool.connection() as conn:
        async with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT set_config('app.client_id', %s, true)", (tenant_a,))
            await cur.execute("SELECT id FROM subscriptions")
            visible = {str(row["id"]) for row in await cur.fetchall()}
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with conn.transaction():
                    await cur.execute(
                        "INSERT INTO subscriptions (client_id, plan, price, started_at)"
                        " VALUES (%s, 'x', 1, now())",
                        (tenant_b,),
                    )
    assert mine in visible and theirs not in visible
