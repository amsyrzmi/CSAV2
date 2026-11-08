"""What a turn key did, shared by the customer bot and the assistant's cards.

A customer message (`bot`) or a confirmed card (`executor`, keys prefixed `assistant:`) that
writes a booking records its key in `booking_turn_actions`, in the same transaction as the write.
A retry of that key finds the record and replays what was done instead of acting again.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from csa.bookings import service, setup
from csa.core import db


async def done_by(conn: service.Conn, client_id: UUID, turn_key: str) -> dict[str, Any] | None:
    """Queue behind any other delivery of this key, then say what it already did: the booking it
    made, moved or cancelled, the action, and whose booking that is; none if nothing yet."""
    await setup.lock(conn, "turn", client_id, turn_key)
    return await db.q.fetch_one(
        "bookings/booking_turn_action_get.sql",
        {"client_id": client_id, "turn_key": turn_key},
        conn=conn,
    )


async def remember(
    conn: service.Conn, client_id: UUID, turn_key: str, booking_id: UUID, action: str
) -> None:
    """Record what this key did, in the same transaction (or savepoint) as the write itself."""
    await db.q.execute(
        "bookings/booking_turn_action_insert.sql",
        {"client_id": client_id, "turn_key": turn_key, "booking_id": booking_id, "action": action},
        conn=conn,
    )
