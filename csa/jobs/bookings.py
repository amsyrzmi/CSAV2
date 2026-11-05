"""Pending bookings the customer's bot made that ran out of time."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
import structlog

from csa.bookings import bot, customer_messages, service
from csa.core import db
from csa.jobs.clock import Clock

logger = structlog.get_logger(__name__)


async def run_pending_expiry(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    clock: Clock,
    *,
    batch_size: int = 100,
) -> dict[str, Any]:
    """Cancel the bot's pending bookings whose time is up, free their holds, and tell the customer.

    A booking lapses when `expires_at` has passed, or when it has already started: a pending booking
    moved earlier keeps the expiry it was made with. Only bookings the bot made with an expiry are
    here, so a pending booking staff made is never touched. Up to `batch_size` rows are claimed with
    `FOR UPDATE SKIP LOCKED`, so a booking a person is confirming at that moment waits for the next
    run, and each is settled in its own savepoint, so one that cannot be settled does not stop the
    rest.

    The hold always lapses. The customer is told on Telegram only when the company's gates allow an
    automatic message (replies not Off, Customer support active): nothing goes without them. The
    worker has no mailer, so a customer who is not on Telegram is not mailed about a lapse.
    """
    blocked, _ = await bot.gate(conn, client_id)
    due = await db.q.fetch_all(
        "bookings/pending_expired.sql",
        {"client_id": client_id, "now": clock.now(), "batch_size": batch_size},
        conn=conn,
    )
    expired = told = failed = 0
    for row in due:
        try:
            async with conn.transaction():
                found = await service.read_records(conn, client_id, booking_id=row["id"])
                lapsed = await service.expire_for(conn, client_id, found[0])
                if blocked is None and await customer_messages.lapsed(conn, client_id, lapsed):
                    told += 1
            expired += 1
        except Exception as exc:
            failed += 1
            logger.error(
                "booking_expiry_failed",
                client_id=str(client_id),
                booking_id=str(row["id"]),
                error=type(exc).__name__,
            )
    return {"expired": expired, "told": told, "failed": failed}
