"""Durable lifecycle checks on disposable synthetic databases only."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from csa.agents.ecommerce.apps import register_app, transition_app
from csa.agents.ecommerce.authorization import (
    TikTokAuthorizationContext,
    TikTokAuthorizationError,
    TikTokGrantState,
    TikTokOperationEvidence,
    TikTokReadOperation,
    require_tiktok_order_read,
)
from csa.agents.ecommerce.grants import (
    create_verified_grant,
    invalidate_grant,
    load_grant,
)
from csa.agents.ecommerce.stores import StoreBinding
from csa.core import db
from csa.core.tenancy import tx
from psycopg_pool import AsyncConnectionPool

from tests.conftest import CSA_RO_PASSWORD, _app_dsn, requires_docker

pytestmark = [requires_docker, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def pool(app_dsn: str) -> AsyncIterator[AsyncConnectionPool]:
    pool = db.build_pool(app_dsn, max_size=3)
    await pool.open(wait=True)
    try:
        yield pool
    finally:
        await pool.close()


async def seed(owner: str) -> TikTokGrantState:
    client_id, record_id = uuid4(), uuid4()
    app_id = f"SYNTH-{uuid4()}"
    async with await psycopg.AsyncConnection.connect(owner) as conn:
        await register_app(conn, app_id)
        await transition_app(conn, app_id=app_id, expected_revision=1, state="active")
        await conn.execute(
            "INSERT INTO clients(id,slug,name,platform_version) VALUES (%s,%s,'Synthetic','test')",
            (client_id, f"synthetic-{client_id}"),
        )
        await conn.execute(
            "INSERT INTO channel_credentials(id,client_id,channel,shop_id) "
            "VALUES (%s,%s,'tiktok_shop','SYNTH-SHOP')",
            (record_id, client_id),
        )
    now = datetime.now(UTC)
    return TikTokGrantState(
        id=uuid4(),
        context=TikTokAuthorizationContext(
            binding=StoreBinding(
                client_id=client_id,
                record_id=record_id,
                platform="tiktok_shop",
                shop_id="SYNTH-SHOP",
            ),
            app_id=app_id,
            app_revision=2,
            credential_generation=1,
        ),
        revision=1,
        state="active",
        source="verified_authorization_exchange",
        scopes=frozenset({"seller.order.info", "seller.authorization.info"}),
        verified_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(hours=1),
    )


async def events(pool: AsyncConnectionPool, tenant: UUID, grant_id: UUID) -> list[dict]:
    async with tx(pool, str(tenant)) as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT revision,state FROM ecommerce_grant_events "
                "WHERE client_id=%s AND grant_id=%s ORDER BY revision",
                (tenant, grant_id),
            )
            return await cursor.fetchall()


async def test_roundtrip_and_terminal_state_are_durable(
    pool: AsyncConnectionPool, migrated_dsn: str, app_dsn: str
) -> None:
    grant = await seed(migrated_dsn)
    tenant = grant.context.binding.client_id
    await create_verified_grant(pool, grant)
    assert await load_grant(pool, tenant, grant.id) == grant
    assert await events(pool, tenant, grant.id) == [{"revision": 1, "state": "active"}]
    revision = await invalidate_grant(pool, tenant, grant.id, expected_revision=1, state="revoked")
    assert revision == 2
    restarted_pool = db.build_pool(app_dsn)
    await restarted_pool.open(wait=True)
    try:
        current = await load_grant(restarted_pool, tenant, grant.id)
        assert current.state == "revoked" and current.revision == 2
    finally:
        await restarted_pool.close()
    assert await events(pool, tenant, grant.id) == [
        {"revision": 1, "state": "active"},
        {"revision": 2, "state": "revoked"},
    ]
    with pytest.raises(TikTokAuthorizationError, match="stale_grant"):
        await invalidate_grant(pool, tenant, grant.id, expected_revision=1, state="disconnected")
    with pytest.raises(TikTokAuthorizationError, match="stale_grant"):
        await invalidate_grant(pool, tenant, grant.id, expected_revision=2, state="disconnected")
    with pytest.raises(TikTokAuthorizationError, match="grant_create_conflict"):
        await create_verified_grant(pool, grant)


async def test_tenant_rls_and_explicit_scope_hide_foreign_grants(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    grant, foreign = await seed(migrated_dsn), await seed(migrated_dsn)
    await create_verified_grant(pool, grant)
    await create_verified_grant(pool, foreign)
    tenant = grant.context.binding.client_id
    async with tx(pool, str(tenant)) as conn:
        rows = await (await conn.execute("SELECT id FROM ecommerce_grants")).fetchall()
        assert rows == [{"id": grant.id}]
        rows = await (await conn.execute("SELECT grant_id FROM ecommerce_grant_events")).fetchall()
        assert rows == [{"grant_id": grant.id}]
    async with pool.connection() as conn:
        assert await (await conn.execute("SELECT id FROM ecommerce_grants")).fetchall() == []
    with pytest.raises(TikTokAuthorizationError, match="missing_grant"):
        await load_grant(pool, tenant, foreign.id)
    with pytest.raises(TikTokAuthorizationError, match="stale_grant"):
        await invalidate_grant(pool, tenant, foreign.id, expected_revision=1, state="revoked")
    assert (await load_grant(pool, foreign.context.binding.client_id, foreign.id)).state == "active"


@pytest.mark.parametrize("mismatch", ["tenant", "record", "shop"])
async def test_create_requires_current_exact_local_store_identity(
    pool: AsyncConnectionPool, migrated_dsn: str, mismatch: str
) -> None:
    grant = await seed(migrated_dsn)
    foreign = await seed(migrated_dsn)
    raw = grant.model_dump()
    binding = raw["context"]["binding"]
    if mismatch == "tenant":
        binding["client_id"] = foreign.context.binding.client_id
    elif mismatch == "record":
        binding["record_id"] = uuid4()
    else:
        binding["shop_id"] = "OTHER"
    grant = TikTokGrantState.model_validate(raw)
    with pytest.raises(TikTokAuthorizationError, match="grant_create_conflict"):
        await create_verified_grant(pool, grant)
    assert await events(pool, binding["client_id"], grant.id) == []


async def test_concurrent_terminal_transitions_have_one_winner_and_one_audit(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    grant = await seed(migrated_dsn)
    tenant = grant.context.binding.client_id
    await create_verified_grant(pool, grant)
    results = await asyncio.gather(
        invalidate_grant(pool, tenant, grant.id, expected_revision=1, state="revoked"),
        invalidate_grant(pool, tenant, grant.id, expected_revision=1, state="disconnected"),
        return_exceptions=True,
    )
    assert sum(value == 2 for value in results) == 1
    assert sum(isinstance(value, TikTokAuthorizationError) for value in results) == 1
    current = await load_grant(pool, tenant, grant.id)
    assert current.revision == 2 and current.state in ("revoked", "disconnected")
    assert len(await events(pool, tenant, grant.id)) == 2


async def test_database_guards_prevent_reactivation_evidence_rewrite_and_audit_tampering(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    grant = await seed(migrated_dsn)
    tenant = grant.context.binding.client_id
    await create_verified_grant(pool, grant)
    with pytest.raises(psycopg.errors.CheckViolation):
        async with tx(pool, str(tenant)) as conn:
            await conn.execute(
                "UPDATE ecommerce_grants SET revision=revision+1 WHERE id=%s", (grant.id,)
            )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with tx(pool, str(tenant)) as conn:
            await conn.execute("UPDATE ecommerce_grants SET scopes='{}' WHERE id=%s", (grant.id,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with tx(pool, str(tenant)) as conn:
            await conn.execute("DELETE FROM ecommerce_grant_events WHERE grant_id=%s", (grant.id,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with tx(pool, str(tenant)) as conn:
            await conn.execute(
                "INSERT INTO ecommerce_grant_events(client_id,grant_id,revision,state) "
                "VALUES (%s,%s,9,'active')",
                (tenant, grant.id),
            )
    assert await load_grant(pool, tenant, grant.id) == grant
    assert len(await events(pool, tenant, grant.id)) == 1
    await invalidate_grant(pool, tenant, grant.id, expected_revision=1, state="disconnected")
    with pytest.raises(psycopg.errors.CheckViolation):
        async with tx(pool, str(tenant)) as conn:
            await conn.execute(
                "UPDATE ecommerce_grants SET state='active',revision=3 WHERE id=%s", (grant.id,)
            )
    assert len(await events(pool, tenant, grant.id)) == 2


async def test_terminal_state_and_audit_rollback_together(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    grant = await seed(migrated_dsn)
    tenant = grant.context.binding.client_id
    await create_verified_grant(pool, grant)
    with pytest.raises(RuntimeError, match="synthetic rollback"):
        async with tx(pool, str(tenant)) as conn:
            await db.q.fetch_one(
                "ecommerce/invalidate_grant.sql",
                ("revoked", tenant, grant.id, 1),
                conn=conn,
            )
            raise RuntimeError("synthetic rollback")
    assert await load_grant(pool, tenant, grant.id) == grant
    assert len(await events(pool, tenant, grant.id)) == 1


async def test_reloaded_revocation_denies_previously_verified_operation(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    grant = await seed(migrated_dsn)
    tenant = grant.context.binding.client_id
    await create_verified_grant(pool, grant)
    now = datetime.now(UTC)
    operation = TikTokReadOperation(id=uuid4(), context=grant.context, started_at=now)
    evidence = TikTokOperationEvidence(
        operation_id=operation.id,
        context=grant.context,
        grant_id=grant.id,
        grant_revision=1,
        source="verified_authorization_and_authorized_shops",
        verified_at=now,
    )
    require_tiktok_order_read(operation, grant=grant, evidence=evidence, now=now)
    await invalidate_grant(pool, tenant, grant.id, expected_revision=1, state="revoked")
    current = await load_grant(pool, tenant, grant.id)
    with pytest.raises(TikTokAuthorizationError, match="inactive_grant"):
        require_tiktok_order_read(operation, grant=current, evidence=evidence, now=now)


async def test_deleted_credential_does_not_destroy_lifecycle_history(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    grant = await seed(migrated_dsn)
    tenant = grant.context.binding.client_id
    await create_verified_grant(pool, grant)
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        await conn.execute(
            "DELETE FROM channel_credentials WHERE client_id=%s AND id=%s",
            (tenant, grant.context.binding.record_id),
        )
    current = await load_grant(pool, tenant, grant.id)
    assert current.state == "disconnected" and current.revision == 2
    assert len(await events(pool, tenant, grant.id)) == 2


async def test_initial_state_and_revision_validation(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    grant = await seed(migrated_dsn)
    for change in ({"state": "revoked"}, {"revision": 2}):
        invalid = TikTokGrantState.model_validate({**grant.model_dump(), **change})
        with pytest.raises(TikTokAuthorizationError, match="invalid_initial_grant"):
            await create_verified_grant(pool, invalid)
    with pytest.raises(TikTokAuthorizationError, match="invalid_revision"):
        await invalidate_grant(
            pool,
            grant.context.binding.client_id,
            grant.id,
            expected_revision=True,
            state="revoked",
        )
    assert await events(pool, grant.context.binding.client_id, grant.id) == []


async def test_read_only_role_is_scoped_and_cannot_write(
    pool: AsyncConnectionPool, migrated_dsn: str, pg_container: object
) -> None:
    grant = await seed(migrated_dsn)
    await create_verified_grant(pool, grant)
    readonly = db.build_pool(_app_dsn(pg_container, role="csa_ro", password=CSA_RO_PASSWORD))
    await readonly.open(wait=True)
    tenant = grant.context.binding.client_id
    try:
        assert await load_grant(readonly, tenant, grant.id) == grant
        assert await events(readonly, uuid4(), grant.id) == []
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await invalidate_grant(readonly, tenant, grant.id, expected_revision=1, state="revoked")
    finally:
        await readonly.close()
    assert await load_grant(pool, tenant, grant.id) == grant
