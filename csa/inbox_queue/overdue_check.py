"""Is a held ``inbox.overdue`` alert still true? (inbox refinement 3, design 5.1)

Quiet hours hold an overdue alert until they end (:mod:`csa.notify.sender`); by then the item may
have been answered, resolved or snoozed, or the customer may have written again and started a new
wait. The senders ask :func:`still_overdue` just before a released alert goes out and drop it when
the answer is no. The wiring (``apps/delivery`` and ``apps/worker``) injects this function, so
``csa.notify`` never imports the Inbox.

The alert's payload carries the conversation id and its ``cycle``, the ISO time the wait began
(the item's ``needs_staff_since``). The alert is true while the conversation (in this company) is
still in the queue state ``needs_staff`` and that time is unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from csa.core import db


def _cycle_time(value: object) -> datetime | None:
    """The payload's cycle as an aware time, or None when it is not an ISO-8601 string."""
    if not isinstance(value, str):
        return None
    try:
        at = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return at if at.tzinfo is not None else at.replace(tzinfo=UTC)


async def still_overdue(
    conn: AsyncConnection[Any], client_id: UUID, payload: dict[str, Any]
) -> bool:
    """True only while the alert's conversation still needs staff on the wait the alert names.

    False for a payload without a usable ``conversation_id`` or ``cycle``, a conversation that is
    not in this company, or one that left ``needs_staff`` or started a new wait. Never raises for
    an odd payload; a database error is the caller's to handle.
    """
    try:
        conversation_id = UUID(str(payload.get("conversation_id")))
    except ValueError:
        return False
    cycle = _cycle_time(payload.get("cycle"))
    if cycle is None:
        return False
    row = await db.q.fetch_one(
        "inbox/overdue_still_open.sql",
        {"client_id": client_id, "conversation_id": conversation_id, "cycle": cycle},
        conn=conn,
    )
    return bool(row is not None and row["still_open"])
