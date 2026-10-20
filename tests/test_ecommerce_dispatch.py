"""Synthetic single-attempt dispatch and real database lifecycle races."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from csa.agents.ecommerce import dispatch as dispatch_module
from csa.agents.ecommerce.authorization import (
    TikTokAuthorizationError,
    TikTokGrantState,
    TikTokOperationEvidence,
    TikTokReadOperation,
)
from csa.agents.ecommerce.dispatch import TikTokReadRequest, begin_read, dispatch_read
from csa.agents.ecommerce.grants import create_verified_grant, invalidate_grant, load_grant
from csa.core.tenancy import tx
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

from tests.conftest import requires_docker
from tests.test_ecommerce_grants import events, seed

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_ecommerce_grants"]


def evidence_for(
    operation: TikTokReadOperation, grant: TikTokGrantState
) -> TikTokOperationEvidence:
    return TikTokOperationEvidence(
        operation_id=operation.id,
        context=operation.context,
        grant_id=grant.id,
        grant_revision=grant.revision,
        source="verified_authorization_and_authorized_shops",
        verified_at=datetime.now(UTC),
    )


async def setup_read(pool: AsyncConnectionPool, owner: str):
    grant = await seed(owner)
    await create_verified_grant(pool, grant)
    operation = await begin_read(
        pool,
        grant.context.binding.client_id,
        grant.id,
        TikTokReadRequest(kind="detail", order_id="SYNTH-ORDER"),
    )
    return grant, operation, evidence_for(operation, grant)


async def test_single_attempt_binds_request_and_blocks_replay_and_foreign_tenant(
    pool: AsyncConnectionPool,
    migrated_dsn: str,
) -> None:
    grant, operation, evidence = await setup_read(pool, migrated_dsn)
    tenant = grant.context.binding.client_id
    calls = []

    async def send(request: TikTokReadRequest) -> str:
        calls.append(request)
        return "SYNTH-RESULT"

    with pytest.raises(TikTokAuthorizationError, match="missing_operation"):
        await dispatch_read(pool, uuid4(), operation.id, evidence, send)
    assert await dispatch_read(pool, tenant, operation.id, evidence, send) == "SYNTH-RESULT"
    with pytest.raises(TikTokAuthorizationError, match="operation_consumed"):
        await dispatch_read(pool, tenant, operation.id, evidence, send)
    assert calls == [TikTokReadRequest(kind="detail", order_id="SYNTH-ORDER")]


@pytest.mark.parametrize("failure", ["error", "cancel", "timeout"])
async def test_uncertain_dispatch_outcome_is_durably_consumed(
    pool: AsyncConnectionPool,
    migrated_dsn: str,
    failure: str,
) -> None:
    grant, operation, evidence = await setup_read(pool, migrated_dsn)
    tenant = grant.context.binding.client_id
    calls = 0

    async def send(_: TikTokReadRequest) -> None:
        nonlocal calls
        calls += 1
        if failure == "cancel":
            raise asyncio.CancelledError
        if failure == "timeout":
            raise TimeoutError("synthetic response loss")
        raise RuntimeError("synthetic crash after send")

    expected = {
        "error": RuntimeError,
        "cancel": asyncio.CancelledError,
        "timeout": TimeoutError,
    }[failure]
    with pytest.raises(expected):
        await dispatch_read(pool, tenant, operation.id, evidence, send)
    with pytest.raises(TikTokAuthorizationError, match="operation_consumed"):
        await dispatch_read(pool, tenant, operation.id, evidence, send)
    assert calls == 1


async def test_concurrent_dispatch_has_one_sender(
    pool: AsyncConnectionPool,
    migrated_dsn: str,
) -> None:
    grant, operation, evidence = await setup_read(pool, migrated_dsn)
    calls = 0

    async def send(_: TikTokReadRequest) -> str:
        nonlocal calls
        calls += 1
        return "sent"

    results = await asyncio.gather(
        *(
            dispatch_read(pool, grant.context.binding.client_id, operation.id, evidence, send)
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert calls == 1
    assert results.count("sent") == 1
    assert sum(isinstance(result, TikTokAuthorizationError) for result in results) == 1


@pytest.mark.parametrize("change", ["revoke", "disconnect", "rotate", "delete", "shop", "meta"])
async def test_lifecycle_change_before_dispatch_never_sends(
    pool: AsyncConnectionPool,
    migrated_dsn: str,
    change: str,
) -> None:
    grant, operation, evidence = await setup_read(pool, migrated_dsn)
    binding = grant.context.binding
    if change in ("revoke", "disconnect"):
        await invalidate_grant(
            pool,
            binding.client_id,
            grant.id,
            expected_revision=1,
            state="revoked" if change == "revoke" else "disconnected",
        )
    else:
        async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
            if change == "delete":
                await conn.execute(
                    "DELETE FROM channel_credentials WHERE id=%s", (binding.record_id,)
                )
            elif change == "shop":
                await conn.execute(
                    "UPDATE channel_credentials SET shop_id='OTHER-SYNTH' WHERE id=%s",
                    (binding.record_id,),
                )
            elif change == "meta":
                await conn.execute(
                    'UPDATE channel_credentials SET meta=\'{"synthetic_app":"other"}\' WHERE id=%s',
                    (binding.record_id,),
                )
            else:
                await conn.execute(
                    "UPDATE channel_credentials SET access_token=%s WHERE id=%s",
                    ("synthetic-replacement", binding.record_id),
                )

    async def send(_: TikTokReadRequest) -> None:
        pytest.fail("invalidated operation must not dispatch")

    with pytest.raises(TikTokAuthorizationError, match="inactive_grant|credential_changed"):
        await dispatch_read(pool, binding.client_id, operation.id, evidence, send)
    current = await load_grant(pool, binding.client_id, grant.id)
    assert current.revision == 2
    assert len(await events(pool, binding.client_id, grant.id)) == 2


@pytest.mark.parametrize("change", ["revoke", "rotate", "delete"])
async def test_dispatch_winner_holds_fence_until_callback_finishes(
    pool: AsyncConnectionPool,
    migrated_dsn: str,
    change: str,
) -> None:
    grant, operation, evidence = await setup_read(pool, migrated_dsn)
    binding = grant.context.binding
    entered, release, attempted = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def send(_: TikTokReadRequest) -> str:
        entered.set()
        await release.wait()
        return "sent"

    async def mutate() -> None:
        if change == "revoke":
            attempted.set()
            await invalidate_grant(
                pool,
                binding.client_id,
                grant.id,
                expected_revision=1,
                state="revoked",
            )
        else:
            async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
                attempted.set()
                if change == "delete":
                    await conn.execute(
                        "DELETE FROM channel_credentials WHERE id=%s",
                        (binding.record_id,),
                    )
                else:
                    await conn.execute(
                        "UPDATE channel_credentials SET access_token=%s WHERE id=%s",
                        ("synthetic-rotation", binding.record_id),
                    )

    dispatch = asyncio.create_task(
        dispatch_read(
            pool,
            binding.client_id,
            operation.id,
            evidence,
            send,
        )
    )
    mutation = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        mutation = asyncio.create_task(mutate())
        await asyncio.wait_for(attempted.wait(), 5)
        # A second connection must observe a real conflicting database row lock.
        async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
            with pytest.raises(psycopg.errors.LockNotAvailable):
                table = "ecommerce_grants" if change == "revoke" else "channel_credentials"
                if table == "ecommerce_grants":
                    await conn.execute(
                        "SELECT id FROM ecommerce_grants WHERE id=%s FOR UPDATE NOWAIT",
                        (grant.id,),
                    )
                else:
                    await conn.execute(
                        "SELECT id FROM channel_credentials WHERE id=%s FOR UPDATE NOWAIT",
                        (binding.record_id,),
                    )
        assert not mutation.done()
    finally:
        release.set()
        await asyncio.gather(dispatch, *([mutation] if mutation is not None else []))
    assert dispatch.result() == "sent"
    assert (await load_grant(pool, binding.client_id, grant.id)).revision == 2


async def test_generation_managed_and_timestamp_only_update_keeps_grant(
    pool: AsyncConnectionPool,
    migrated_dsn: str,
) -> None:
    grant, operation, evidence = await setup_read(pool, migrated_dsn)
    binding = grant.context.binding
    async with tx(pool, str(binding.client_id)) as conn:
        await conn.execute(
            "UPDATE channel_credentials SET updated_at=clock_timestamp() WHERE id=%s",
            (binding.record_id,),
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        async with tx(pool, str(binding.client_id)) as conn:
            await conn.execute(
                "UPDATE channel_credentials SET credential_generation=9 WHERE id=%s",
                (binding.record_id,),
            )
    assert await load_grant(pool, binding.client_id, grant.id) == grant

    async def send(_: TikTokReadRequest) -> str:
        return "sent"

    assert await dispatch_read(pool, binding.client_id, operation.id, evidence, send) == "sent"


async def test_search_page_needs_its_own_verification_and_immutable_target(
    pool: AsyncConnectionPool,
    migrated_dsn: str,
) -> None:
    grant, first, old_evidence = await setup_read(pool, migrated_dsn)
    tenant = grant.context.binding.client_id
    request = TikTokReadRequest(kind="search", limit=1, page_token="opaque/+&=%")
    second = await begin_read(pool, tenant, grant.id, request)

    async def send(actual: TikTokReadRequest) -> str:
        assert actual == request
        return "sent"

    with pytest.raises(TikTokAuthorizationError, match="operation_mismatch"):
        await dispatch_read(pool, tenant, second.id, old_evidence, send)
    async with tx(pool, str(tenant)) as conn:
        assert (
            await (
                await conn.execute(
                    "SELECT consumed_at FROM ecommerce_read_operations WHERE id=%s",
                    (second.id,),
                )
            ).fetchone()
        ) == {"consumed_at": None}
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with tx(pool, str(tenant)) as conn:
            await conn.execute(
                "UPDATE ecommerce_read_operations SET request='{}' WHERE id=%s",
                (first.id,),
            )
    assert (
        await dispatch_read(
            pool,
            tenant,
            second.id,
            evidence_for(second, grant),
            send,
        )
        == "sent"
    )


async def test_revocation_between_consumption_and_send_is_rechecked(
    pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    grant, operation, evidence = await setup_read(pool, migrated_dsn)
    tenant = grant.context.binding.client_id
    original = dispatch_module._check_fence
    checks = 0

    async def check(conn, op, verified, grant_id):
        nonlocal checks
        checks += 1
        if checks == 2:
            await invalidate_grant(
                pool,
                tenant,
                grant.id,
                expected_revision=1,
                state="revoked",
            )
        return await original(conn, op, verified, grant_id)

    monkeypatch.setattr(dispatch_module, "_check_fence", check)

    async def send(_: TikTokReadRequest) -> None:
        pytest.fail("revocation in the commit gap must prevent sending")

    with pytest.raises(TikTokAuthorizationError, match="inactive_grant"):
        await dispatch_read(pool, tenant, operation.id, evidence, send)
    with pytest.raises(TikTokAuthorizationError, match="operation_consumed"):
        await dispatch_read(pool, tenant, operation.id, evidence, send)


async def test_real_callback_deadline_keeps_attempt_consumed_and_releases_fences(
    pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    grant, operation, evidence = await setup_read(pool, migrated_dsn)
    tenant = grant.context.binding.client_id
    entered = asyncio.Event()
    monkeypatch.setattr(dispatch_module, "DISPATCH_TIMEOUT_SECONDS", 0.2)

    async def send(_: TikTokReadRequest) -> None:
        entered.set()
        await asyncio.Event().wait()

    with pytest.raises(TimeoutError):
        await dispatch_read(pool, tenant, operation.id, evidence, send)
    assert entered.is_set()
    with pytest.raises(TikTokAuthorizationError, match="operation_consumed"):
        await dispatch_read(pool, tenant, operation.id, evidence, send)
    assert (
        await asyncio.wait_for(
            invalidate_grant(
                pool,
                tenant,
                grant.id,
                expected_revision=1,
                state="revoked",
            ),
            5,
        )
        == 2
    )


@pytest.mark.parametrize(
    "query",
    [
        {"kind": "detail"},
        {"kind": "detail", "order_id": "ONE,TWO"},
        {"kind": "detail", "order_id": "ONE", "page_token": "two"},
        {"kind": "search", "order_id": "ONE"},
        {"kind": "search", "limit": True},
        {"kind": "search", "limit": 51},
        {"kind": "search", "access_token": "SYNTH"},
    ],
)
async def test_dispatch_request_is_narrow_and_typed(query: dict) -> None:
    with pytest.raises(ValidationError):
        TikTokReadRequest.model_validate(query)
