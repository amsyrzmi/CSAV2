"""A person's email preferences for Inbox notifications (inbox refinement part 3, design
2026-11-19, sections 5.1 and 5.4).

One ``console_notification_prefs`` row per person and company, written only when the person saves:
no row means email off, with the defaults below. ``notify_people`` reads the rows in SQL when it
queues an email (``email_outbox_put.sql``); this module is the API's read and write path. Both run
on the caller's company transaction. Last write wins: there is no version.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg

from csa.core import db

KINDS: tuple[str, ...] = (
    "inbox.assigned",
    "inbox.overdue",
    "inbox.snooze_due",
    "inbox.stale_claim",
    "inbox.follow_up",
    "inbox.handback",
)
PRIORITY_RANK: dict[str, int] = {"low": 0, "normal": 1, "high": 2, "urgent": 3}
DEFAULT_MIN_PRIORITY = "high"


@dataclass(frozen=True)
class Prefs:
    """What a person gets by email: on or off, which of the six kinds, and the priority floor."""

    email_enabled: bool
    kinds: tuple[str, ...]
    min_priority: str


def _from_row(row: dict[str, Any]) -> Prefs:
    return Prefs(
        email_enabled=row["email_enabled"],
        kinds=tuple(row["kinds"]),
        min_priority=row["min_priority"],
    )


async def get_prefs(conn: psycopg.AsyncConnection[Any], client_id: UUID, user_id: UUID) -> Prefs:
    """The person's saved preferences, or the defaults (email off) when they never saved."""
    row = await db.q.fetch_one(
        "notify/prefs_get.sql", {"client_id": client_id, "user_id": user_id}, conn=conn
    )
    if row is None:
        return Prefs(email_enabled=False, kinds=KINDS, min_priority=DEFAULT_MIN_PRIORITY)
    return _from_row(row)


async def save_prefs(
    conn: psycopg.AsyncConnection[Any], client_id: UUID, user_id: UUID, prefs: Prefs
) -> Prefs:
    """Save the person's preferences (last write wins) and clear their failure flag.

    Raises ``ValueError`` for an unknown kind or priority before touching the database.
    """
    unknown = [kind for kind in prefs.kinds if kind not in KINDS]
    if unknown:
        raise ValueError(f"unknown notification kind {unknown[0]!r}")
    if prefs.min_priority not in PRIORITY_RANK:
        raise ValueError(f"unknown priority {prefs.min_priority!r}")
    row = await db.q.fetch_one(
        "notify/prefs_upsert.sql",
        {
            "client_id": client_id,
            "user_id": user_id,
            "email_enabled": prefs.email_enabled,
            "kinds": list(prefs.kinds),
            "min_priority": prefs.min_priority,
        },
        conn=conn,
    )
    if row is None:  # an upsert always returns its row
        raise RuntimeError("notification preferences were not saved")
    return _from_row(row)
