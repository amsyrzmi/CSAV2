"""Checkpoint retention skeleton (LG01 §7.3).

Deletes ``lg.*`` checkpoints for threads idle longer than
``CHECKPOINT_RETENTION_DAYS`` (default 14) EXCEPT threads that still have an open
pending approval, so a human can always resume one.

Two connection paths, and the split is the whole point of the design:

* The ``lg.*`` deletes run on the dedicated **checkpoint pool** (``csa_app``,
  no ``app.client_id`` GUC). ``lg.*`` has no RLS, so that is fine.
* ``public.approvals`` IS RLS-enabled. A read on the GUC-less ``csa_app``
  checkpoint connection would be filtered to ZERO rows, silently matching no
  open approvals and deleting live threads. The open-approval set is therefore
  read on a SEPARATE short-lived OWNER ``csa`` connection (owner bypasses the
  non-FORCE policies), across all tenants, and passed into the delete.

LG01 ships the skeleton: the query shape, the exclusion, the interval config,
and a periodic worker registration. Production tuning (batching, backoff) is
LG10's concern.

"Latest activity" per thread is derived from the newest checkpoint id. LangGraph
checkpoint ids are UUIDv6 (time-ordered, with the creation timestamp embedded),
so the per-thread cutoff is computed from that embedded timestamp — the
checkpoint tables carry no explicit timestamp column.
"""  # LG01

from __future__ import annotations

import asyncio
import datetime as dt
import uuid

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg_pool import AsyncConnectionPool

# The retention loop runs on the dedicated checkpoint pool (dict-row).
CheckpointPool = AsyncConnectionPool[AsyncConnection[DictRow]]

# UUIDv6 encodes a 60-bit timestamp as the count of 100-nanosecond intervals
# since the Gregorian epoch (1582-10-15 00:00 UTC).
_GREGORIAN_EPOCH = dt.datetime(1582, 10, 15, tzinfo=dt.UTC)

# Default poll interval for the periodic worker task (seconds). Not tuned here.
DEFAULT_INTERVAL_SECONDS = 60 * 60 * 24  # once a day


def uuidv6_timestamp(value: str) -> dt.datetime:
    """Return the UTC creation time embedded in a UUIDv6 checkpoint id."""
    i = uuid.UUID(value).int
    time_high = (i >> 96) & 0xFFFFFFFF
    time_mid = (i >> 80) & 0xFFFF
    time_low = (i >> 64) & 0x0FFF
    intervals = (time_high << 28) | (time_mid << 12) | time_low
    return _GREGORIAN_EPOCH + dt.timedelta(microseconds=intervals / 10)


async def open_approval_trace_ids(owner_dsn: str) -> set[str]:
    """Return trace_ids with an open pending approval, read as OWNER ``csa``.

    Opens a separate short-lived owner connection (NOT the GUC-less csa_app
    checkpoint pool) so the RLS policy on ``public.approvals`` is bypassed and
    the set spans all tenants. "Open" = ``status='pending'`` and not expired.
    """
    async with await psycopg.AsyncConnection.connect(owner_dsn, autocommit=True) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT DISTINCT trace_id FROM public.approvals "
                "WHERE status = 'pending' "
                "  AND (expires_at IS NULL OR expires_at > now())"
            )
            return {str(row[0]) for row in await cur.fetchall()}


async def _latest_activity_by_thread(
    pool: CheckpointPool,
) -> dict[str, dt.datetime]:
    """Map each checkpoint thread_id to the time of its newest checkpoint."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # UUIDv6 ids sort lexically by time, so max(checkpoint_id) is newest.
            await cur.execute(
                "SELECT thread_id, max(checkpoint_id) AS latest FROM checkpoints GROUP BY thread_id"
            )
            rows = await cur.fetchall()
    return {str(row["thread_id"]): uuidv6_timestamp(str(row["latest"])) for row in rows}


def select_threads_for_deletion(
    latest_activity: dict[str, dt.datetime],
    open_trace_ids: set[str],
    *,
    cutoff: dt.datetime,
) -> list[str]:
    """Return threads older than ``cutoff`` and NOT in the open-approval set."""
    return [
        thread_id
        for thread_id, last in latest_activity.items()
        if last < cutoff and thread_id not in open_trace_ids
    ]


async def _delete_threads(pool: CheckpointPool, thread_ids: list[str]) -> None:
    """Delete the given threads from all three ``lg.*`` checkpoint tables."""
    if not thread_ids:
        return
    async with pool.connection() as conn:
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            await conn.execute(
                f"DELETE FROM {table} WHERE thread_id = ANY(%s)",  # noqa: S608
                (thread_ids,),
            )


async def run_retention_once(
    checkpoint_pool: CheckpointPool,
    owner_dsn: str,
    *,
    retention_days: int,
    now: dt.datetime | None = None,
) -> list[str]:
    """Run one retention pass; return the thread_ids that were deleted.

    The open-approval exclusion is read via ``owner_dsn`` (OWNER csa); the
    checkpoint reads/deletes run on ``checkpoint_pool`` (csa_app, lg schema).
    """
    now = now or dt.datetime.now(dt.UTC)
    cutoff = now - dt.timedelta(days=retention_days)

    latest_activity = await _latest_activity_by_thread(checkpoint_pool)
    open_trace_ids = await open_approval_trace_ids(owner_dsn)
    to_delete = select_threads_for_deletion(latest_activity, open_trace_ids, cutoff=cutoff)
    await _delete_threads(checkpoint_pool, to_delete)
    return to_delete


async def retention_loop(
    checkpoint_pool: CheckpointPool,
    owner_dsn: str,
    *,
    retention_days: int,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
) -> None:
    """Periodic retention task (simple asyncio loop; no external scheduler)."""
    while True:
        await run_retention_once(checkpoint_pool, owner_dsn, retention_days=retention_days)
        await asyncio.sleep(interval_seconds)
