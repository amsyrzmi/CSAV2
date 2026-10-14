"""Reservation reconciliation jobs (LG10)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg

from csa.core import db
from csa.jobs.clock import Clock

DEFAULT_HOLDING_STATUSES = ["draft", "pending_confirmation"]


async def run_reservation_reconcile(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    clock: Clock,
    *,
    batch_size: int = 100,
    holding_statuses: list[str] | None = None,
    repair: bool = False,
) -> dict[str, Any]:
    """Compare products.reserved_qty with the sum of quantities on active orders."""
    statuses = holding_statuses or DEFAULT_HOLDING_STATUSES

    if repair:
        rows = await db.q.fetch_all(
            "jobs/reservation_repair.sql",
            (client_id, statuses, client_id, client_id),
            conn=conn,
        )
        repaired = [
            {
                "id": str(r["id"]),
                "sku": r["sku"],
                "old_reserved": r["old_reserved"],
                "new_reserved": r["new_reserved"],
            }
            for r in rows
        ]
        return {
            "mode": "repair",
            "drift_count": len(repaired),
            "repaired": repaired,
        }

    rows = await db.q.fetch_all(
        "jobs/reservation_reconcile.sql",
        (client_id, statuses, client_id),
        conn=conn,
    )
    drifted = [
        {
            "id": str(r["id"]),
            "sku": r["sku"],
            "actual": r["actual"],
            "expected": r["expected"],
            "drift": r["drift"],
        }
        for r in rows
    ]
    return {
        "mode": "report",
        "drift_count": len(drifted),
        "drifted": drifted,
    }
