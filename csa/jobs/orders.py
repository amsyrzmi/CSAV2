"""Order and draft maintenance jobs (LG10)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

import psycopg

from csa.core import db
from csa.jobs.clock import Clock


async def run_order_draft_expiry(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    clock: Clock,
    *,
    batch_size: int = 100,
) -> dict[str, Any]:
    """Expire pending order_confirmations and release reservations aggregated per product."""
    row = await db.q.fetch_one(
        "jobs/order_draft_expiry.sql",
        (client_id, batch_size, client_id, client_id, client_id),
        conn=conn,
    )
    if row is None:
        return {"confirmations": 0, "orders_cancelled": 0, "products_released": 0}
    return {
        "confirmations": row["confirmations"],
        "orders_cancelled": row["orders_cancelled"],
        "products_released": row["products_released"],
    }


async def run_orphan_draft_expiry(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    clock: Clock,
    *,
    ttl: timedelta = timedelta(minutes=15),
    batch_size: int = 100,
) -> dict[str, Any]:
    """Cancel drafts older than 2x TTL with no pending confirmation and release reservations."""
    cutoff = clock.now() - (2 * ttl)
    row = await db.q.fetch_one(
        "jobs/orphan_draft_expiry.sql",
        (client_id, cutoff, client_id, batch_size, client_id, client_id, client_id),
        conn=conn,
    )
    if row is None:
        return {"orders_cancelled": 0, "products_released": 0}
    return {
        "orders_cancelled": row["orders_cancelled"],
        "products_released": row["products_released"],
    }
