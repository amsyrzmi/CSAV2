"""Global app lifecycle, privileges and real multi-tenant fence races; synthetic only."""

import asyncio
from uuid import uuid4

import psycopg
import pytest
from csa.agents.ecommerce import dispatch as dispatch_module
from csa.agents.ecommerce.apps import register_app, require_app, transition_app
from csa.agents.ecommerce.authorization import TikTokAuthorizationError
from csa.agents.ecommerce.dispatch import TikTokReadRequest, begin_read, dispatch_read
from csa.agents.ecommerce.grants import create_verified_grant, load_grant
from csa.core.tenancy import tx
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

from tests.conftest import requires_docker
from tests.test_ecommerce_dispatch import setup_read
from tests.test_ecommerce_grants import seed

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_ecommerce_grants"]


async def test_operator_cas_audit_and_old_grants_never_reactivate(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    grant, operation, evidence = await setup_read(pool, migrated_dsn)
    app_id = grant.context.app_id
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        with pytest.raises(TikTokAuthorizationError, match="app_registration_conflict"):
            await register_app(conn, app_id)
        disabled = await transition_app(conn, app_id=app_id, expected_revision=2, state="disabled")
        assert disabled.revision == 3
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        with pytest.raises(TikTokAuthorizationError, match="stale_app_state"):
            await transition_app(conn, app_id=app_id, expected_revision=2, state="disabled")
        active = await transition_app(conn, app_id=app_id, expected_revision=3, state="active")
        assert active.revision == 4
    tenant = grant.context.binding.client_id

    async def forbidden(_: TikTokReadRequest):
        pytest.fail("old grant may not send even after reactivation")

    with pytest.raises(TikTokAuthorizationError, match="app_revision_unavailable"):
        await dispatch_read(pool, tenant, operation.id, evidence, forbidden)
    # App-wide denial does not rewrite individual tenant grant/audit history.
    assert await load_grant(pool, tenant, grant.id) == grant
    async with tx(pool, str(tenant)) as conn:
        await require_app(conn, app_id, 4)
        rows = await (
            await conn.execute(
                "SELECT revision,state FROM ecommerce_app_events WHERE app_id=%s ORDER BY revision",
                (app_id,),
            )
        ).fetchall()
        assert rows == [
            {"revision": 1, "state": "disabled"},
            {"revision": 2, "state": "active"},
            {"revision": 3, "state": "disabled"},
            {"revision": 4, "state": "active"},
        ]


async def test_disabled_commit_survives_operator_crash_and_affects_every_tenant(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    first = await seed(migrated_dsn)
    second = await seed(migrated_dsn)
    second = second.model_copy(
        update={"context": second.context.model_copy(update={"app_id": first.context.app_id})}
    )
    unrelated = await seed(migrated_dsn)
    for grant in (first, second, unrelated):
        await create_verified_grant(pool, grant)
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        await transition_app(
            conn, app_id=first.context.app_id, expected_revision=2, state="disabled"
        )
    # No external mutation or activation: a process exit here leaves a durable deny.
    for grant in (first, second):
        operation = await begin_read(
            pool,
            grant.context.binding.client_id,
            grant.id,
            TikTokReadRequest(kind="detail", order_id="SYNTHETIC"),
        )

        async def verify(*_):
            pytest.fail("disabled app must reject before resolver or HTTP")

        async def send(_):
            pytest.fail("disabled app must not send")

        with pytest.raises(TikTokAuthorizationError, match="app_revision_unavailable"):
            await dispatch_read(pool, grant.context.binding.client_id, operation.id, verify, send)
    async with tx(pool, str(unrelated.context.binding.client_id)) as conn:
        await require_app(conn, unrelated.context.app_id, unrelated.context.app_revision)
    fresh = await seed(migrated_dsn)
    fresh = fresh.model_copy(
        update={"context": fresh.context.model_copy(update={"app_id": first.context.app_id})}
    )
    with pytest.raises(TikTokAuthorizationError, match="app_revision_unavailable"):
        await create_verified_grant(pool, fresh)


async def wait_for_blocker(owner: str, pid: int) -> None:
    async with asyncio.timeout(5):
        async with await psycopg.AsyncConnection.connect(owner, autocommit=True) as conn:
            while True:
                row = await (
                    await conn.execute("SELECT cardinality(pg_blocking_pids(%s))", (pid,))
                ).fetchone()
                if row and row[0] > 0:
                    return
                await asyncio.sleep(0.01)


async def test_read_winner_holds_global_fence_until_bounded_send_finishes(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    grant, operation, evidence = await setup_read(pool, migrated_dsn)
    entered, release = asyncio.Event(), asyncio.Event()

    async def send(_: TikTokReadRequest):
        entered.set()
        await release.wait()
        return "synthetic-result"

    read = asyncio.create_task(
        dispatch_read(pool, grant.context.binding.client_id, operation.id, evidence, send)
    )
    disable = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
            pid = conn.info.backend_pid
            disable = asyncio.create_task(
                transition_app(
                    conn, app_id=grant.context.app_id, expected_revision=2, state="disabled"
                )
            )
            await wait_for_blocker(migrated_dsn, pid)
            assert not disable.done()
            release.set()
            assert await asyncio.wait_for(read, 5) == "synthetic-result"
            assert (await asyncio.wait_for(disable, 5)).revision == 3
    finally:
        release.set()
        for task in (read, disable):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(read, return_exceptions=True)
        if disable is not None:
            await asyncio.gather(disable, return_exceptions=True)


async def test_disable_winner_blocks_read_then_rejects_before_verification(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    grant, operation, evidence = await setup_read(pool, migrated_dsn)
    entered = asyncio.Event()
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as operator:
        await transition_app(
            operator, app_id=grant.context.app_id, expected_revision=2, state="disabled"
        )
        async with tx(pool, str(grant.context.binding.client_id)) as conn:

            async def check():
                entered.set()
                await require_app(conn, grant.context.app_id, grant.context.app_revision)

            read = asyncio.create_task(check())
            try:
                await entered.wait()
                await wait_for_blocker(migrated_dsn, conn.info.backend_pid)
                await operator.commit()
                with pytest.raises(TikTokAuthorizationError, match="app_revision_unavailable"):
                    await asyncio.wait_for(read, 5)
            finally:
                if not read.done():
                    read.cancel()
                await asyncio.gather(read, return_exceptions=True)

    async def forbidden(*_):
        pytest.fail("must deny before resolver or HTTP")

    with pytest.raises(TikTokAuthorizationError, match="app_revision_unavailable"):
        await dispatch_read(
            pool, grant.context.binding.client_id, operation.id, forbidden, forbidden
        )


async def test_disable_between_consumption_and_send_is_rechecked(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    grant, operation, evidence = await setup_read(pool, migrated_dsn)
    original = dispatch_module._check_fence
    checks = 0

    async def check(conn, op, verified, grant_id):
        nonlocal checks
        checks += 1
        if checks == 2:
            async with await psycopg.AsyncConnection.connect(migrated_dsn) as operator:
                await transition_app(
                    operator, app_id=grant.context.app_id, expected_revision=2, state="disabled"
                )
        return await original(conn, op, verified, grant_id)

    monkeypatch.setattr(dispatch_module, "_check_fence", check)

    async def forbidden(_):
        pytest.fail("commit-gap disable must prevent order send")

    tenant = grant.context.binding.client_id
    with pytest.raises(TikTokAuthorizationError, match="app_revision_unavailable"):
        await dispatch_read(pool, tenant, operation.id, evidence, forbidden)
    with pytest.raises(TikTokAuthorizationError, match="operation_consumed"):
        await dispatch_read(pool, tenant, operation.id, evidence, forbidden)


async def test_global_metadata_is_read_only_to_runtime_and_audit_is_atomic(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    grant = await seed(migrated_dsn)
    async with pool.connection() as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await register_app(conn, f"SYNTH-{uuid4()}")
    async with tx(pool, str(grant.context.binding.client_id)) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await transition_app(
                conn, app_id=grant.context.app_id, expected_revision=2, state="disabled"
            )
    for statement in (
        "DELETE FROM ecommerce_apps WHERE app_id=%s",
        "UPDATE ecommerce_apps SET revision=revision+1 WHERE app_id=%s",
        "UPDATE ecommerce_apps SET state='active',revision=revision+1 WHERE app_id=%s",
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
                await conn.execute(statement, (grant.context.app_id,))
    with pytest.raises(RuntimeError, match="synthetic crash"):
        async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
            await transition_app(
                conn, app_id=grant.context.app_id, expected_revision=2, state="disabled"
            )
            raise RuntimeError("synthetic crash")
    async with tx(pool, str(grant.context.binding.client_id)) as conn:
        await require_app(conn, grant.context.app_id, 2)
        assert await (
            await conn.execute(
                "SELECT revision FROM ecommerce_app_events WHERE app_id=%s ORDER BY revision",
                (grant.context.app_id,),
            )
        ).fetchall() == [{"revision": 1}, {"revision": 2}]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute("DELETE FROM ecommerce_app_events")


async def test_registration_is_disabled_and_reserved_revision_never_active(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    app_id = f"SYNTH-{uuid4()}"
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        initial = await register_app(conn, app_id)
        assert initial.state == "disabled" and initial.revision == 1
        with pytest.raises(ValidationError):
            await transition_app(conn, app_id=app_id, expected_revision=True, state="active")
    async with pool.connection() as conn:
        with pytest.raises(TikTokAuthorizationError, match="app_revision_unavailable"):
            await require_app(conn, app_id, 1)
    with pytest.raises(psycopg.errors.CheckViolation):
        async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
            await conn.execute(
                "INSERT INTO ecommerce_apps VALUES (%s,1,'active')", (f"SYNTH-{uuid4()}",)
            )


async def test_concurrent_operator_transitions_have_one_cas_winner(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    grant = await seed(migrated_dsn)

    async def disable():
        async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
            return await transition_app(
                conn, app_id=grant.context.app_id, expected_revision=2, state="disabled"
            )

    results = await asyncio.gather(disable(), disable(), return_exceptions=True)
    assert sum(isinstance(result, TikTokAuthorizationError) for result in results) == 1
    async with tx(pool, str(grant.context.binding.client_id)) as conn:
        rows = await (
            await conn.execute(
                "SELECT revision,state FROM ecommerce_app_events WHERE app_id=%s AND revision=3",
                (grant.context.app_id,),
            )
        ).fetchall()
        assert rows == [{"revision": 3, "state": "disabled"}]


async def test_shared_fences_allow_other_tenants_and_cancellation_releases_app_lock(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    grant, operation, evidence = await setup_read(pool, migrated_dsn)
    entered = asyncio.Event()

    async def send(_):
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(
        dispatch_read(pool, grant.context.binding.client_id, operation.id, evidence, send)
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        async with asyncio.timeout(5):
            async with tx(pool, str(uuid4())) as other:
                await require_app(other, grant.context.app_id, 2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    async with asyncio.timeout(5):
        async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
            assert (
                await transition_app(
                    conn, app_id=grant.context.app_id, expected_revision=2, state="disabled"
                )
            ).revision == 3
    with pytest.raises(TikTokAuthorizationError, match="operation_consumed"):
        await dispatch_read(pool, grant.context.binding.client_id, operation.id, evidence, send)
