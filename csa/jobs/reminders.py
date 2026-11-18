"""The reminders_fire job: once a minute, each company's due reminders (plan 2a, task 2)."""

from __future__ import annotations

from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from csa.jobs.clock import Clock
from csa.reminders.fire import BATCH_SIZE, fire_due


async def run_reminders_fire(
    app_pool: AsyncConnectionPool,
    client_id: UUID,
    clock: Clock,
    *,
    batch_size: int = BATCH_SIZE,
) -> dict[str, int]:
    """Fire the company's due reminders, each in its own transaction (`Job.own_transactions`)."""
    return await fire_due(app_pool, client_id, clock, batch_size=batch_size)
