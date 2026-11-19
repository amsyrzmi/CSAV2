"""The console_notifications_purge job: a company's console notifications older than 30 days are
deleted, read or not (plan 2a, task 7; design 8), and so are its person email outbox rows older
than 30 days (inbox refinement part 3).

Runs every six hours per company on the app pool (``Job.own_transactions``): each batch of
``batch_size`` rows is its own company transaction, committed before the next, and at most
``MAX_BATCHES`` batches go in a run, so a long backlog is cleared over several runs and no
transaction is held for long.
The cutoff comes from the job clock, so a test moves it with a ``FakeClock``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from csa.core import db
from csa.core.tenancy import tx
from csa.jobs.clock import Clock

RETENTION = timedelta(days=30)
MAX_BATCHES = 100


async def run_console_notifications_purge(
    app_pool: AsyncConnectionPool,
    client_id: UUID,
    clock: Clock,
    *,
    batch_size: int = 1000,
) -> dict[str, int]:
    """Delete the company's console notifications and email outbox rows created over 30 days ago.

    ``purged`` counts the notifications, ``email_purged`` the outbox rows.
    """
    cutoff = clock.now() - RETENTION
    purged = await _purge(
        app_pool, client_id, "notify/purge_console_notifications.sql", cutoff, batch_size
    )
    email_purged = await _purge(
        app_pool, client_id, "notify/purge_email_outbox.sql", cutoff, batch_size
    )
    return {"purged": purged, "email_purged": email_purged}


async def _purge(
    app_pool: AsyncConnectionPool, client_id: UUID, sql: str, cutoff: datetime, batch_size: int
) -> int:
    """Run one batched delete: each batch its own company transaction, at most MAX_BATCHES."""
    total = 0
    for _ in range(MAX_BATCHES):
        async with tx(app_pool, str(client_id)) as conn:
            row = await db.q.fetch_one(
                sql, {"client_id": client_id, "cutoff": cutoff, "batch": batch_size}, conn=conn
            )
        batch = row["purged"] if row else 0
        total += batch
        if batch < batch_size:
            break
    return total
