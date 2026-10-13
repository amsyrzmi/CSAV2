"""Staff notifier: fan an event out to matching targets as idempotent outbox rows (LG05)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from csa.core import db

PRIORITIES = ("low", "normal", "high", "urgent")


async def notify(
    conn: psycopg.AsyncConnection[Any],
    *,
    client_id: UUID,
    event: str,
    priority: str,
    payload: dict[str, object],
    dedupe_key: str,
) -> int:
    """Queue one row per matching target; replays with the same key add nothing.

    Runs on the caller's connection so the alert commits with the business change.
    Returns the number of newly queued rows.
    """
    if priority not in PRIORITIES:
        raise ValueError(f"unknown priority {priority!r}")
    targets = await db.q.fetch_all(
        "notify/matching_targets.sql", (client_id, event, priority), conn=conn
    )
    queued = 0
    for target in targets:
        row = await db.q.fetch_one(
            "notify/insert_notification.sql",
            (
                client_id,
                target["id"],
                event,
                priority,
                Jsonb(payload),
                f"notify:{event}:{dedupe_key}:{target['id']}",
            ),
            conn=conn,
        )
        queued += row is not None
    return queued
