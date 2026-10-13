"""Row-Level Security suite, run as csa_app (LG01 §5.5).

All nine tests exercise the staged RLS (ENABLE without FORCE) against a
testcontainers DB with the baseline + migrations applied. Seeding is done as the
owner ``csa`` (which bypasses policies); the isolation assertions run as
``csa_app`` through :func:`csa.core.tenancy.tx`.
"""  # LG01

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest
from csa.core import db
from csa.core.tenancy import tx

from tests.conftest import requires_docker


def _row(value: Any) -> Any:
    """Assert a fetched row is present and return it (narrows Optional)."""
    assert value is not None
    return value


pytestmark = [requires_docker, pytest.mark.asyncio]

CLIENT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CLIENT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

# Deterministic seed ids so tests can reference rows across connections.
CONTACT_A = "a1a1a1a1-0000-0000-0000-000000000001"
CONTACT_B = "b1b1b1b1-0000-0000-0000-000000000001"
PRODUCT_A = "a2a2a2a2-0000-0000-0000-000000000001"
PRODUCT_B = "b2b2b2b2-0000-0000-0000-000000000001"
CONVO_A = "a3a3a3a3-0000-0000-0000-000000000001"
CRED_A = "a4a4a4a4-0000-0000-0000-000000000001"
ENDPOINT_PUBLIC_ID = "endpointpublicid_A01"  # matches ^[A-Za-z0-9_-]{16,64}$


@pytest.fixture(scope="module")
def seeded_dsn(migrated_dsn: str) -> str:
    """Seed two tenants' rows as the owner csa; return the owner DSN."""
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        for cid, slug in ((CLIENT_A, "client-a"), (CLIENT_B, "client-b")):
            conn.execute(
                "INSERT INTO clients (id, slug, name, platform_version) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                (cid, slug, slug, "lg01-test"),
            )
        conn.execute(
            "INSERT INTO contacts (id, client_id) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
            (CONTACT_A, CLIENT_A),
        )
        conn.execute(
            "INSERT INTO contacts (id, client_id) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
            (CONTACT_B, CLIENT_B),
        )
        conn.execute(
            "INSERT INTO products (id, client_id, sku, name, price) "
            "VALUES (%s, %s, 'SKU-A', 'Widget A', 9.99) ON CONFLICT (id) DO NOTHING",
            (PRODUCT_A, CLIENT_A),
        )
        conn.execute(
            "INSERT INTO products (id, client_id, sku, name, price) "
            "VALUES (%s, %s, 'SKU-B', 'Widget B', 9.99) ON CONFLICT (id) DO NOTHING",
            (PRODUCT_B, CLIENT_B),
        )
        conn.execute(
            "INSERT INTO conversations (id, client_id, contact_id, channel, channel_thread) "
            "VALUES (%s, %s, %s, 'webhook', 'thread-a') ON CONFLICT (id) DO NOTHING",
            (CONVO_A, CLIENT_A, CONTACT_A),
        )
        conn.execute(
            "INSERT INTO channel_credentials (id, client_id, channel) "
            "VALUES (%s, %s, 'webhook') ON CONFLICT (id) DO NOTHING",
            (CRED_A, CLIENT_A),
        )
        conn.execute(
            "INSERT INTO webhook_endpoints (client_id, public_id, credential_id) "
            "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (CLIENT_A, ENDPOINT_PUBLIC_ID, CRED_A),
        )
    return migrated_dsn


@pytest.fixture()
async def app_pool(app_dsn: str):
    pool = db.build_pool(app_dsn, min_size=1, max_size=4)
    await pool.open(wait=True)
    try:
        yield pool
    finally:
        await pool.close()


# 1 (via fixture) + 2
async def test_cross_tenant_select_returns_zero(seeded_dsn: str, app_pool) -> None:
    async with tx(app_pool, CLIENT_A) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT count(*) AS n FROM products WHERE client_id = %s", (CLIENT_B,)
            )
            assert _row(await cur.fetchone())["n"] == 0


# 3
async def test_cross_tenant_insert_fails_with_check(seeded_dsn: str, app_pool) -> None:
    with pytest.raises(psycopg.errors.Error):
        async with tx(app_pool, CLIENT_A) as conn:
            await conn.execute(
                "INSERT INTO products (client_id, sku, name, price) VALUES (%s, 'X', 'X', 1.00)",
                (CLIENT_B,),
            )


# 4
async def test_guc_unset_select_returns_zero(seeded_dsn: str, app_pool) -> None:
    async with app_pool.connection() as conn:  # no tx => app.client_id unset
        async with conn.cursor() as cur:
            await cur.execute("SELECT count(*) AS n FROM products")
            assert _row(await cur.fetchone())["n"] == 0


# 5
async def test_owner_unscoped_select_sees_all(seeded_dsn: str) -> None:
    with psycopg.connect(seeded_dsn, autocommit=True) as conn:
        n = _row(
            conn.execute(
                "SELECT count(*) FROM products WHERE client_id IN (%s, %s)",
                (CLIENT_A, CLIENT_B),
            ).fetchone()
        )[0]
    assert n == 2


# 6 — set-equality meta-test (no hardcoded count)
async def test_rls_set_equality(seeded_dsn: str) -> None:
    with psycopg.connect(seeded_dsn, autocommit=True) as conn:
        # Restrict to base tables (information_schema.columns also lists views
        # that expose a client_id column; a view is not an RLS target — §1.4).
        expected = {
            r[0]
            for r in conn.execute(
                "SELECT c.table_name FROM information_schema.columns c "
                "JOIN information_schema.tables t "
                "  ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
                "WHERE c.table_schema='public' AND c.column_name='client_id' "
                "  AND t.table_type='BASE TABLE'"
            ).fetchall()
        } - {"clients", "outbound_delivery_attempts"}
        actual = {
            r[0]
            for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' AND rowsecurity = true"
            ).fetchall()
        }
    assert expected == actual
    assert len(actual) == 48  # today's value, informational only


# 7 — write-path INSERT into a nextval() bigint-PK target (sequence USAGE closure)
async def test_write_path_bigint_pk_insert(seeded_dsn: str, app_pool) -> None:
    async with tx(app_pool, CLIENT_A) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO agent_runs (client_id, trace_id, agent, ok) "
                "VALUES (%s, %s, 'test', true) RETURNING id",
                (CLIENT_A, str(uuid.uuid4())),
            )
            new_id = _row(await cur.fetchone())["id"]
    assert new_id is not None

    # Companion: a cross-tenant INSERT still fails WITH CHECK (RLS not loosened).
    with pytest.raises(psycopg.errors.Error):
        async with tx(app_pool, CLIENT_A) as conn:
            await conn.execute(
                "INSERT INTO agent_runs (client_id, trace_id, agent, ok) "
                "VALUES (%s, %s, 'test', true)",
                (CLIENT_B, str(uuid.uuid4())),
            )


# 8 — trigger-firing writes under csa_app (function EXECUTE closure)
async def test_trigger_firing_writes_as_app(seeded_dsn: str, app_pool) -> None:
    async with tx(app_pool, CLIENT_A) as conn:
        # (a) UPDATE conversations fires trg_keep_escalated -> keep_escalated_status()
        await conn.execute(
            "UPDATE conversations SET status = 'closed' WHERE client_id = %s AND id = %s",
            (CLIENT_A, CONVO_A),
        )
        # (b) INSERT contact_identities fires trg_contact_identity_tenant ->
        #     contact_identity_tenant_check(), which reads contacts for the same tenant.
        await conn.execute(
            "INSERT INTO contact_identities (client_id, channel, external_id, contact_id) "
            "VALUES (%s, 'webhook', 'ext-a-1', %s)",
            (CLIENT_A, CONTACT_A),
        )


# 9 — resolve_endpoint definer-bypass + security_invoker view read
async def test_resolve_endpoint_and_invoker_view(seeded_dsn: str, app_pool) -> None:
    # resolve_endpoint as csa_app with GUC unset returns exactly one row for A.
    async with app_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM resolve_endpoint(%s)", (ENDPOINT_PUBLIC_ID,))
            rows = await cur.fetchall()
    assert len(rows) == 1
    assert str(rows[0]["client_id"]) == CLIENT_A

    # A security_invoker view (v_product_catalog over products): A sees its row,
    # B sees zero — base-table RLS applies to the invoking role.
    async with tx(app_pool, CLIENT_A) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT count(*) AS n FROM v_product_catalog WHERE client_id = %s", (CLIENT_A,)
            )
            assert _row(await cur.fetchone())["n"] == 1
    async with tx(app_pool, CLIENT_B) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT count(*) AS n FROM v_product_catalog WHERE client_id = %s", (CLIENT_A,)
            )
            assert _row(await cur.fetchone())["n"] == 0
