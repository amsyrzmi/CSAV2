"""The marketplace sync queue on a disposable migrated Postgres; synthetic stores only."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from csa.core.tenancy import tx
from csa.marketplace import jobs
from csa.marketplace.jobs import MarketplaceJobError, enqueue_job
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from tests.conftest import requires_docker

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_ecommerce_grants"]

MAX_ATTEMPTS = 3
STALE_AFTER = 900.0


async def make_store(owner: str, client: UUID | None = None) -> tuple[UUID, UUID]:
    """A tenant (new unless given) with one TikTok store; the shop id is unique per store."""
    store = uuid4()
    async with await psycopg.AsyncConnection.connect(owner) as conn:
        if client is None:
            client = uuid4()
            await conn.execute(
                "INSERT INTO clients(id,slug,name,platform_version,status) "
                "VALUES (%s,%s,'Synthetic','t','active')",
                (client, f"mkt-jobs-{client}"),
            )
        await conn.execute(
            "INSERT INTO channel_credentials(id,client_id,channel,shop_id) "
            "VALUES (%s,%s,'tiktok_shop',%s)",
            (store, client, f"SYNTH-SHOP-{store}"),
        )
    return client, store


async def job_rows(owner: str, store: UUID) -> list[dict]:
    async with await psycopg.AsyncConnection.connect(
        owner, row_factory=cast(Any, dict_row)
    ) as conn:
        cursor = await conn.execute(
            "SELECT id, kind, target, status, attempts, claimed_at, last_error_code, "
            "next_attempt_at FROM marketplace_sync_jobs WHERE store_id=%s "
            "ORDER BY created_at, kind, target NULLS FIRST",
            (store,),
        )
        return cast(list[dict], await cursor.fetchall())


async def enqueue(pool: AsyncConnectionPool, client: UUID, store: UUID, kind, target=None) -> bool:
    async with tx(pool, str(client)) as conn:
        return await enqueue_job(conn, client_id=client, store_id=store, kind=kind, target=target)


async def claim(pool: AsyncConnectionPool, client: UUID, now: datetime):
    async with tx(pool, str(client)) as conn:
        return await jobs.claim_job(conn, client, now=now)


def now_plus(seconds: float = 0) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=seconds)


async def test_duplicate_triggers_fold_into_one_pending_job(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    client, store = await make_store(migrated_dsn)

    assert await enqueue(pool, client, store, "order_refresh", "SYNTH-ORDER-1") is True
    assert await enqueue(pool, client, store, "order_refresh", "SYNTH-ORDER-1") is False
    assert await enqueue(pool, client, store, "order_refresh", "SYNTH-ORDER-2") is True
    # Whole-store jobs have no target: NULL folds with NULL.
    assert await enqueue(pool, client, store, "backfill_orders") is True
    assert await enqueue(pool, client, store, "backfill_orders") is False
    # Same target, other kind: a separate job.
    assert await enqueue(pool, client, store, "product_refresh", "SYNTH-ORDER-1") is True

    rows = await job_rows(migrated_dsn, store)
    assert sorted((r["kind"], r["target"]) for r in rows) == [
        ("backfill_orders", None),
        ("order_refresh", "SYNTH-ORDER-1"),
        ("order_refresh", "SYNTH-ORDER-2"),
        ("product_refresh", "SYNTH-ORDER-1"),
    ]
    assert {r["status"] for r in rows} == {"pending"}

    # Once claimed, the same trigger queues a fresh pending job (it may carry newer changes).
    claimed = []
    while (job := await claim(pool, client, now_plus(1))) is not None:
        claimed.append(job)
        async with tx(pool, str(client)) as conn:
            assert await jobs.finish_job(conn, job, "done") == "done"
    assert len(claimed) == 4
    assert await enqueue(pool, client, store, "order_refresh", "SYNTH-ORDER-1") is True


async def test_enqueue_rejects_bad_shapes_and_foreign_stores(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    client, store = await make_store(migrated_dsn)
    other_client, other_store = await make_store(migrated_dsn)

    bad = [
        ("order_refresh", None),
        ("product_refresh", None),
        ("backfill_orders", "SYNTH-1"),
        ("catch_up", "SYNTH-1"),
        ("product_refresh", "not/a/product"),
        ("order_refresh", "x" * 101),
        ("order_refresh", ""),
        ("delete_everything", None),
    ]
    for kind, target in bad:
        with pytest.raises(MarketplaceJobError) as caught:
            await enqueue(pool, client, store, kind, target)
        assert caught.value.code == "invalid_job"

    # Another tenant's store is invisible under RLS: refused, nothing queued anywhere.
    with pytest.raises(MarketplaceJobError) as caught:
        await enqueue(pool, client, other_store, "backfill_orders")
    assert caught.value.code == "unknown_store"
    assert await job_rows(migrated_dsn, other_store) == []
    assert await job_rows(migrated_dsn, store) == []
    assert other_client != client


async def test_claim_runs_one_job_per_store_at_a_time(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    client, first = await make_store(migrated_dsn)
    _, second = await make_store(migrated_dsn, client)
    await enqueue(pool, client, first, "backfill_orders")
    await enqueue(pool, client, first, "backfill_products")
    await enqueue(pool, client, second, "catch_up")

    a = await claim(pool, client, now_plus(1))
    b = await claim(pool, client, now_plus(1))
    assert a is not None and b is not None
    assert {a.store_id, b.store_id} == {first, second}
    assert (a.attempts, b.attempts) == (1, 1)
    # The first store already has a claimed job, the second has nothing left.
    assert await claim(pool, client, now_plus(1)) is None

    first_job = a if a.store_id == first else b
    async with tx(pool, str(client)) as conn:
        assert await jobs.finish_job(conn, first_job, "done") == "done"
    following = await claim(pool, client, now_plus(1))
    assert following is not None and following.store_id == first
    assert following.kind == "backfill_products"


async def test_concurrent_claims_never_take_two_jobs_of_one_store(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    client, store = await make_store(migrated_dsn)
    for target in ("SYNTH-A", "SYNTH-B", "SYNTH-C"):
        await enqueue(pool, client, store, "order_refresh", target)

    async with tx(pool, str(client)) as first:
        claimed = await jobs.claim_job(first, client, now=now_plus(1))
        assert claimed is not None
        # A second worker claims while the first claim is not committed yet: it waits for the
        # tenant's claim lock instead of taking another job of the same store.
        second = asyncio.create_task(claim(pool, client, now_plus(1)))
        await asyncio.sleep(0.5)
        assert not second.done()
    assert await second is None
    rows = await job_rows(migrated_dsn, store)
    assert sorted(r["status"] for r in rows) == ["claimed", "pending", "pending"]


async def test_jobs_wait_for_their_retry_time(pool: AsyncConnectionPool, migrated_dsn: str) -> None:
    client, store = await make_store(migrated_dsn)
    await enqueue(pool, client, store, "order_refresh", "SYNTH-RETRY")
    job = await claim(pool, client, now_plus(1))
    assert job is not None
    retry_at = now_plus(600)
    async with tx(pool, str(client)) as conn:
        status = await jobs.finish_job(
            conn, job, "pending", error_code="http_error", retry_at=retry_at
        )
    assert status == "pending"
    assert await claim(pool, client, now_plus(300)) is None
    again = await claim(pool, client, now_plus(601))
    assert again is not None and again.id == job.id and again.attempts == 2
    (row,) = await job_rows(migrated_dsn, store)
    assert row["last_error_code"] == "http_error"


async def test_stale_claim_is_requeued(pool: AsyncConnectionPool, migrated_dsn: str) -> None:
    client, store = await make_store(migrated_dsn)
    await enqueue(pool, client, store, "backfill_orders")
    claimed_at = now_plus(1)
    job = await claim(pool, client, claimed_at)
    assert job is not None

    async with tx(pool, str(client)) as conn:
        # Not stale yet: untouched.
        early = await jobs.requeue_stale_jobs(
            conn,
            client,
            now=claimed_at + timedelta(seconds=STALE_AFTER - 5),
            stale_after_seconds=STALE_AFTER,
            max_attempts=MAX_ATTEMPTS,
        )
    assert early == []

    later = claimed_at + timedelta(seconds=STALE_AFTER + 5)
    async with tx(pool, str(client)) as conn:
        requeued = await jobs.requeue_stale_jobs(
            conn, client, now=later, stale_after_seconds=STALE_AFTER, max_attempts=MAX_ATTEMPTS
        )
    assert [(r.id, r.store_id, r.status) for r in requeued] == [(job.id, store, "pending")]
    (row,) = await job_rows(migrated_dsn, store)
    assert (row["status"], row["claimed_at"], row["last_error_code"]) == (
        "pending",
        None,
        "claim_abandoned",
    )

    # The worker that lost the claim can neither heartbeat nor finish it any more.
    async with tx(pool, str(client)) as conn:
        assert await jobs.touch_job(conn, job, now=later) is False
        assert await jobs.finish_job(conn, job, "done") is None

    again = await claim(pool, client, later)
    assert again is not None and again.id == job.id and again.attempts == 2


async def test_stale_claim_folds_into_a_newer_pending_duplicate_or_fails_when_exhausted(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    client, store = await make_store(migrated_dsn)
    await enqueue(pool, client, store, "order_refresh", "SYNTH-DUP")
    job = await claim(pool, client, now_plus(1))
    assert job is not None
    # A webhook for the same order arrives while the first job is claimed.
    assert await enqueue(pool, client, store, "order_refresh", "SYNTH-DUP") is True
    later = now_plus(STALE_AFTER + 10)
    async with tx(pool, str(client)) as conn:
        requeued = await jobs.requeue_stale_jobs(
            conn, client, now=later, stale_after_seconds=STALE_AFTER, max_attempts=MAX_ATTEMPTS
        )
    assert [r.status for r in requeued] == ["cancelled"]
    rows = await job_rows(migrated_dsn, store)
    assert sorted(r["status"] for r in rows) == ["cancelled", "pending"]

    # A job whose attempts ran out fails instead of coming back.
    client2, store2 = await make_store(migrated_dsn)
    await enqueue(pool, client2, store2, "catch_up")
    when = now_plus(1)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        stale = await claim(pool, client2, when)
        assert stale is not None and stale.attempts == attempt
        when = when + timedelta(seconds=STALE_AFTER + 10)
        async with tx(pool, str(client2)) as conn:
            requeued = await jobs.requeue_stale_jobs(
                conn, client2, now=when, stale_after_seconds=STALE_AFTER, max_attempts=MAX_ATTEMPTS
            )
        expected = "failed" if attempt == MAX_ATTEMPTS else "pending"
        assert [r.status for r in requeued] == [expected]
    (row,) = await job_rows(migrated_dsn, store2)
    assert (row["status"], row["last_error_code"]) == ("failed", "claim_abandoned")


async def test_heartbeat_keeps_a_long_job_from_going_stale(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    client, store = await make_store(migrated_dsn)
    await enqueue(pool, client, store, "backfill_products")
    start = now_plus(1)
    job = await claim(pool, client, start)
    assert job is not None
    beat = start + timedelta(seconds=STALE_AFTER - 10)
    async with tx(pool, str(client)) as conn:
        assert await jobs.touch_job(conn, job, now=beat) is True
    async with tx(pool, str(client)) as conn:
        requeued = await jobs.requeue_stale_jobs(
            conn,
            client,
            now=start + timedelta(seconds=STALE_AFTER + 10),
            stale_after_seconds=STALE_AFTER,
            max_attempts=MAX_ATTEMPTS,
        )
    assert requeued == []


async def test_cancel_store_jobs_only_touches_that_stores_pending_jobs(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    client, store = await make_store(migrated_dsn)
    _, other = await make_store(migrated_dsn, client)
    await enqueue(pool, client, store, "backfill_orders")
    await enqueue(pool, client, store, "order_refresh", "SYNTH-X")
    await enqueue(pool, client, other, "backfill_orders")
    claimed = await claim(pool, client, now_plus(1))
    assert claimed is not None

    target = claimed.store_id
    async with tx(pool, str(client)) as conn:
        cancelled = await jobs.cancel_store_jobs(conn, client, target, code="store_disconnected")
    rows = await job_rows(migrated_dsn, target)
    # The claimed job is left to its worker, which finishes it itself.
    assert sorted(r["status"] for r in rows) == sorted(["claimed"] + ["cancelled"] * cancelled)
    assert {r["last_error_code"] for r in rows if r["status"] == "cancelled"} <= {
        "store_disconnected"
    }
    untouched = other if target == store else store
    assert {r["status"] for r in await job_rows(migrated_dsn, untouched)} == {"pending"}


async def test_store_state_reads_classify_each_kind_of_store(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    client, unverified = await make_store(migrated_dsn)
    _, disconnected = await make_store(migrated_dsn, client)
    _, expired = await make_store(migrated_dsn, client)
    pending = uuid4()
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        await conn.execute(
            "INSERT INTO channel_credentials(id,client_id,channel) VALUES (%s,%s,'tiktok_shop')",
            (pending, client),
        )
        await conn.execute(
            "UPDATE channel_credentials SET disconnected_at=now() WHERE id=%s", (disconnected,)
        )
        # Tokens but no verified grant yet: not readable, but nothing says it needs a reconnect.
        await conn.execute(
            "UPDATE channel_credentials SET access_token='v1:x', "
            "expires_at=now() + interval '1 day' WHERE id=%s",
            (unverified,),
        )
        await conn.execute(
            "UPDATE channel_credentials SET access_token='v1:x', "
            "expires_at=now() - interval '1 minute' WHERE id=%s",
            (expired,),
        )
    async with tx(pool, str(client)) as conn:
        states = {
            s: (await jobs.get_sync_store(conn, client, s)).sync_state  # type: ignore[union-attr]
            for s in (unverified, disconnected, expired, pending)
        }
        assert await jobs.list_connected_stores(conn, client) == []
        await jobs.set_store_state(
            conn, client, unverified, now=now_plus(), backfill_cursor='{"orders": null}'
        )
        await jobs.set_store_state(conn, client, unverified, now=now_plus(), last_error_code="x")
        stored = await jobs.get_sync_store(conn, client, unverified)
        assert stored is not None
        # Fields not named stay as they were.
        assert (stored.backfill_cursor, stored.last_error_code) == ('{"orders": null}', "x")
        assert await jobs.get_sync_store(conn, client, uuid4()) is None
    assert states == {
        unverified: "unavailable",
        disconnected: "disconnected",
        expired: "expired",
        pending: "pending",
    }
