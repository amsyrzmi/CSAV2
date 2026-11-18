"""Schedule and cancel reminders (design 2026-11-16, section 7; plan 2a, task 1).

A reminder is a generic timer: a kind (the name of its handler), the subject it is about, a due
time and a payload of ids and enums. Nothing here knows what the subject is. Both functions run on
the caller's tenant transaction (``app.client_id`` set), so the change commits or rolls back with
the transition that asked for it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from csa.core import db


async def schedule(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    kind: str,
    subject_type: str,
    subject_id: str,
    due_at: datetime,
    payload: dict[str, Any],
    dedupe_key: str,
) -> UUID:
    """Schedule a reminder and return its id; the same ``dedupe_key`` is one reminder.

    Scheduling a key that exists moves ``due_at`` and replaces ``payload`` only while the row is
    ``scheduled`` and has not fired (``fire_count = 0``). A fired, cancelled or done row is left
    as it is and its id is returned.
    """
    row = await db.q.fetch_one(
        "reminders/schedule.sql",
        {
            "client_id": client_id,
            "kind": kind,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "due_at": due_at,
            "payload": Jsonb(payload),
            "dedupe_key": dedupe_key,
        },
        conn=conn,
    )
    return row["id"]


async def cancel(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    kind: str,
    subject_type: str,
    subject_id: str,
) -> int:
    """Cancel the subject's scheduled reminders of exactly this ``kind``; return how many."""
    rows = await db.q.fetch_all(
        "reminders/cancel.sql",
        {
            "client_id": client_id,
            "kind": kind,
            "subject_type": subject_type,
            "subject_id": subject_id,
        },
        conn=conn,
    )
    return len(rows)
