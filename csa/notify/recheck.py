"""The held-alert re-check the senders accept (inbox refinement 3, design 5.1).

Defined here so :mod:`csa.notify` does not import the Inbox: the apps inject
:func:`csa.inbox_queue.overdue_check.still_overdue`. Both senders (:mod:`csa.notify.sender` for
Telegram, :mod:`csa.notify.mail_sender` for staff email) re-check a row only when it was held by
quiet hours and is an ``inbox.overdue`` alert; an alert that is no longer true is cancelled
(``notify/cancel.sql``), not sent and not counted as a failure.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import structlog
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from csa.core import db
from csa.core.tenancy import tx

logger = structlog.get_logger(__name__)

OVERDUE_EVENT = "inbox.overdue"

OverdueCheck = Callable[[AsyncConnection[Any], UUID, dict[str, Any]], Awaitable[bool]]
"""``(connection, client id, payload) -> still true``, run on a tenant transaction."""


async def cancelled_as_moved_on(
    app_pool: AsyncConnectionPool, row: dict[str, Any], check: OverdueCheck | None
) -> bool:
    """True when ``row`` was a released overdue alert that no longer holds and is now cancelled.

    Only a claimed row with ``held_at`` set, event ``inbox.overdue`` and a ``check`` is asked;
    anything else (never held, another event, urgent, no check injected) returns False without
    calling ``check``. An error from ``check`` propagates, so the caller's failure path (retry
    with backoff) handles it: the row is neither sent nor cancelled blindly.
    """
    if (
        check is None
        or row.get("held_at") is None
        or row["event"] != OVERDUE_EVENT
        or row["priority"] == "urgent"  # urgent is never held; belt and braces
    ):
        return False
    client_id = row["client_id"]
    async with tx(app_pool, str(client_id)) as conn:
        if await check(conn, client_id, row["payload"]):
            return False
        cancelled = await db.q.fetch_one("notify/cancel.sql", (row["id"], client_id), conn=conn)
    if cancelled is not None:
        logger.info(
            "staff_notification_cancelled", notification_id=str(row["id"]), reason="moved_on"
        )
    return True
