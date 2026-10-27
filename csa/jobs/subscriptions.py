"""Subscription flags that fall due: period-end cancellations, plan changes, resumes, renewals."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg

from csa.core import db
from csa.jobs.clock import Clock


async def run_subscriptions_due(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    clock: Clock,
    *,
    batch_size: int = 100,
) -> dict[str, Any]:
    """Apply the due subscription changes of one tenant; nothing is charged.

    Four rules run in order, each as one SQL statement claiming up to ``batch_size`` rows with
    ``FOR UPDATE SKIP LOCKED``: period-end cancellations first (so a cancelling subscription is
    never renewed in the same pass), then scheduled plan changes, resume dates and renewals.
    The rules run once per call, so a row advances by at most one renewal interval per run.
    """
    params = {"client_id": client_id, "now": clock.now(), "batch_size": batch_size}
    cancelled = await _one("jobs/subscriptions_due_cancel.sql", params, conn)
    plan = await _one("jobs/subscriptions_due_plan.sql", params, conn)
    resumed = await _one("jobs/subscriptions_due_resume.sql", params, conn)
    renewed = await _one("jobs/subscriptions_due_renew.sql", params, conn)
    return {
        "cancelled": cancelled["cancelled"],
        "plan_changed": plan["plan_changed"],
        "resumed": resumed["resumed"],
        "renewed": renewed["renewed"],
        "pending_skipped": plan["pending_skipped"],
    }


async def _one(path: str, params: dict[str, Any], conn: psycopg.AsyncConnection[Any]) -> Any:
    row = await db.q.fetch_one(path, params, conn=conn)
    if row is None:
        raise RuntimeError(f"{path} returned no row")
    return row
