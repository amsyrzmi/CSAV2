"""The console_notifications_purge job: a company's console notifications older than 30 days are
deleted, read or not (plan 2a, task 7; design 8).

Runs every six hours per company on the app pool (``Job.own_transactions``): each batch of
``batch_size`` rows is its own company transaction, committed before the next, and at most
``MAX_BATCHES`` batches go in a run, so a long backlog is cleared over several runs and no
transaction is held for long.
The cutoff comes from the job clock, so a test moves it with a ``FakeClock``.
"""

from __future__ import annotations

from datetime import timedelta
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
    """Delete the company's console notifications created more than 30 days ago."""
    cutoff = clock.now() - RETENTION
    purged = 0
    for _ in range(MAX_BATCHES):
        async with tx(app_pool, str(client_id)) as conn:
            row = await db.q.fetch_one(
                "notify/purge_console_notifications.sql",
                {"client_id": client_id, "cutoff": cutoff, "batch": batch_size},
                conn=conn,
            )
        batch = row["purged"] if row else 0
        purged += batch
        if batch < batch_size:
            break
    return {"purged": purged}
