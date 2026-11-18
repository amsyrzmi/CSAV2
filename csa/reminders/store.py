"""Schedule and cancel reminders (design 2026-11-16, section 7; plan 2a, task 1).

A reminder is a generic timer: a kind (the name of its handler), the subject it is about, a due
time and a payload of ids and enums. Nothing here knows what the subject is. Every function runs on
the caller's tenant transaction (``app.client_id`` set), so the change commits or rolls back with
the transition that asked for it.
"""

from __future__ import annotations

from collections.abc import Sequence
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
    params = {
        "client_id": client_id,
        "kind": kind,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "due_at": due_at,
        "payload": Jsonb(payload),
        "dedupe_key": dedupe_key,
    }
    # The fallback read runs on the statement's snapshot, so a row with the key that another
    # transaction committed after the snapshot and that the upsert may not move is not seen:
    # once more, on a fresh snapshot, it is.
    for _ in range(2):
        row = await db.q.fetch_one("reminders/schedule.sql", params, conn=conn)
        if row is not None:
            return row["id"]
    raise RuntimeError("reminder schedule returned no row")


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


async def cancel_skip_locked(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    kinds: Sequence[str],
    subject_type: str,
    subject_id: str,
    *,
    keep: Sequence[str] = (),
) -> int:
    """Cancel the subject's scheduled reminders of these ``kinds`` without waiting; return how many.

    For a caller that holds a lock a firing reminder's handler may wait for. The fire job holds the
    reminder row it fires (``claim_due.sql``, ``FOR UPDATE``) while its handler runs; a handler that
    then locks its subject (the Inbox's ``inbox.snooze_due`` locks the conversation) and a caller
    that holds the subject and then waits for the reminder row would deadlock. So a row another
    transaction holds (being fired right now, or being moved by :func:`schedule`) is skipped and
    stays ``scheduled``: the caller must accept that it may still fire, which is safe when the
    kind's handler re-checks its subject at every fire and drops a reminder whose subject moved on.
    Rows whose dedupe key is in ``keep`` are left alone, for a caller about to schedule them again
    (:func:`schedule` never revives a cancelled row). Rows this transaction holds are cancelled.
    """
    rows = await db.q.fetch_all(
        "reminders/cancel_skip_locked.sql",
        {
            "client_id": client_id,
            "kinds": list(kinds),
            "subject_type": subject_type,
            "subject_id": subject_id,
            "keep": list(keep),
        },
        conn=conn,
    )
    return len(rows)
