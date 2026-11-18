"""The inbox_reminders_sweep job: once a minute, each company's missing Inbox reminders (plan 2a,
task 5, decision 1).

The 0622 triggers put items in Needs us without Python, so this job schedules what they could
not: the overdue, stale-claim and snooze reminders of :mod:`csa.inbox_queue.reminders`. The
reminders themselves fire in the ``reminders_fire`` job.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg

from csa.inbox_queue.reminders import SWEEP_BATCH, schedule_missing
from csa.jobs.clock import Clock


async def run_inbox_reminders_sweep(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    clock: Clock,
    *,
    batch_size: int = SWEEP_BATCH,
) -> dict[str, int]:
    """Schedule the company's missing Inbox reminders, at most ``batch_size`` per kind a run."""
    return await schedule_missing(conn, client_id, clock.now(), limit=batch_size)
