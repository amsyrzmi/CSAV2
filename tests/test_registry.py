"""Capability registry loader integration tests (LG02 §5-6)."""

from __future__ import annotations

import uuid

import psycopg
import pytest
from csa.contracts.registry import CapabilityRegistry, CapabilityRow
from csa.core import db
from pydantic import BaseModel

from tests.conftest import owner_dsn_for, requires_docker


class SearchInput(BaseModel):
    query: str


@pytest.fixture()
async def registry_context(migrated_dsn: str, pg_container: object, app_dsn: str):
    client_id = str(uuid.uuid4())
    other_client_id = str(uuid.uuid4())
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        for cid in (client_id, other_client_id):
            conn.execute(
                "INSERT INTO clients (id, slug, name, platform_version) VALUES (%s, %s, %s, %s)",
                (cid, f"registry-{cid}", f"registry-{cid}", "lg02-test"),
            )
        for cid, name, enabled, client_enabled, circuit_state in (
            (client_id, "order_agent", True, True, "closed"),
            (client_id, "disabled_agent", False, True, "closed"),
            (client_id, "client_disabled_agent", True, False, "closed"),
            (client_id, "open_agent", True, True, "open"),
            (client_id, "probe_agent", True, True, "half_open"),
            (other_client_id, "other_tenant_agent", True, True, "closed"),
        ):
            conn.execute(
                "INSERT INTO capability_registry "
                "(client_id, name, display_name, description, pattern, workflow_id, input_schema, "
                "is_enabled, client_enabled, circuit_state) "
                "VALUES (%s, %s, %s, 'test capability', 'test', 'legacy-workflow', "
                "'{}'::jsonb, %s, %s, %s)",
                (cid, name, name, enabled, client_enabled, circuit_state),
            )
    pool = db.build_pool(app_dsn, max_size=4)
    await pool.open(wait=True)
    try:
        yield CapabilityRegistry(pool), client_id, other_client_id, pg_container
    finally:
        await pool.close()


@requires_docker
async def test_loader_filters_disabled_and_open_capabilities(registry_context) -> None:
    registry, client_id, _, _ = registry_context
    rows = await registry.load(client_id)

    assert {row.name for row in rows} == {"order_agent", "probe_agent"}
    assert all(isinstance(row, CapabilityRow) for row in rows)


@requires_docker
async def test_loader_is_tenant_scoped_and_cache_can_be_invalidated(registry_context) -> None:
    registry, client_id, other_client_id, pg_container = registry_context
    first = await registry.load(client_id)
    other = await registry.load(other_client_id)
    assert {row.name for row in first} == {"order_agent", "probe_agent"}
    assert {row.name for row in other} == {"other_tenant_agent"}

    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        conn.execute(
            "UPDATE capability_registry SET circuit_state = 'open' "
            "WHERE client_id = %s AND name = 'probe_agent'",
            (client_id,),
        )
    cached = await registry.load(client_id)
    assert {row.name for row in cached} == {"order_agent", "probe_agent"}

    await registry.invalidate(client_id)
    refreshed = await registry.load(client_id)
    assert {row.name for row in refreshed} == {"order_agent"}


@requires_docker
async def test_execution_authorization_ignores_warm_caches_in_other_processes(
    registry_context,
) -> None:
    registry, client_id, other_id, pg_container = registry_context
    second = CapabilityRegistry(registry._pool)
    await registry.load(client_id)
    await second.load(client_id)
    tenant_id = uuid.UUID(client_id)
    assert await registry.allows_execution(tenant_id, "order_agent")
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        conn.execute(
            "UPDATE capability_registry SET client_enabled=false WHERE client_id=%s "
            "AND name='order_agent'",
            (client_id,),
        )
    assert not await registry.allows_execution(tenant_id, "order_agent")
    assert not await second.allows_execution(tenant_id, "order_agent")
    assert not await second.allows_execution(uuid.UUID(other_id), "order_agent")
    assert not await second.allows_execution(tenant_id, "missing")
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        conn.execute(
            "UPDATE capability_registry SET client_enabled=true, is_enabled=false "
            "WHERE client_id=%s AND name='order_agent'",
            (client_id,),
        )
    assert not await registry.allows_execution(tenant_id, "order_agent")
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        conn.execute(
            "UPDATE capability_registry SET is_enabled=true WHERE client_id=%s "
            "AND name='order_agent'",
            (client_id,),
        )
    assert await second.allows_execution(tenant_id, "order_agent")


@requires_docker
async def test_sync_input_schema_uses_pydantic_source(registry_context) -> None:
    registry, client_id, _, _ = registry_context
    updated = await registry.sync_input_schema(client_id, "order_agent", SearchInput)
    assert updated is True
    rows = await registry.load(client_id, refresh=True)

    row = next(row for row in rows if row.name == "order_agent")
    assert row.input_schema == SearchInput.model_json_schema()


def test_capability_row_defaults_pre_lg11_fields() -> None:
    row = CapabilityRow.model_validate(
        {
            "id": uuid.uuid4(),
            "client_id": uuid.uuid4(),
            "name": "legacy_agent",
            "display_name": "Legacy",
            "description": "Existing registry row",
            "pattern": "lookup",
            "tier": "core",
            "version": "1.0.0",
            "risk_class": "low",
            "workflow_id": "legacy-id",
            "input_schema": {},
            "config_schema": None,
            "timeout_ms": 20000,
            "max_retries": 1,
            "max_tool_calls": 5,
            "max_iterations": 3,
            "requires_human": False,
            "is_enabled": True,
            "circuit_state": "closed",
            "fail_count": 0,
            "opened_at": None,
            "capability_kind": "dispatched",
            "read_scopes": [],
            "write_scopes": [],
            "authorization_mode": "contact",
            "client_enabled": True,
            "client_enabled_changed_at": None,
            "client_enabled_changed_by": None,
        }
    )

    assert row.impl_kind == "n8n"
    assert row.impl_ref is None
    assert row.routing_hint is None
