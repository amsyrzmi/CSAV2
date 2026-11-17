"""The inbox queue's scheduled work (design 2026-11-16, 5.2)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg

from csa.inbox_queue import transitions
from csa.jobs.clock import Clock


async def run_inbox_snooze_wake(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    clock: Clock,
    *,
    batch_size: int = 100,
) -> dict[str, Any]:
    """Return the company's snoozed conversations whose time has come to the queue."""
    return {"woken_count": await transitions.wake_snoozed(conn, client_id, limit=batch_size)}
