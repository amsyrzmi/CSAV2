"""Maintenance and reaper jobs (LG10)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg

from csa.core import db
from csa.jobs.clock import Clock


async def run_pending_actions_expiry(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    clock: Clock,
    *,
    batch_size: int = 100,
) -> dict[str, Any]:
    """Mark expired pending_actions past expires_at."""
    row = await db.q.fetch_one(
        "jobs/pending_actions_expiry.sql",
        (client_id, batch_size, client_id),
        conn=conn,
    )
    count = row["count"] if row else 0
    return {"expired_count": count}


async def run_mute_expiry(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    clock: Clock,
    *,
    batch_size: int = 100,
) -> dict[str, Any]:
    """Release human_support conversations whose ai_muted_until has passed."""
    row = await db.q.fetch_one(
        "jobs/mute_expiry.sql",
        (client_id, batch_size, client_id),
        conn=conn,
    )
    count = row["count"] if row else 0
    return {"released_count": count}
