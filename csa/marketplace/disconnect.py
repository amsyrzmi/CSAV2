"""Disconnect a TikTok Shop store (spec 6.6): stop syncing, drop the tokens, keep the data.

``disconnect_store`` is the one entry point for the three ways a store ends: an Admin pressing
Disconnect (``admin``), TikTok's deauthorization webhook (``deauthorized``) and a store that sat
``expired`` for 30 days (``expired``, from the purge job). All three do the same thing in one
transaction under the tenant:

* set ``disconnected_at`` and clear both encrypted tokens in a single UPDATE, so the 0320 fence
  bumps ``credential_generation`` once and disconnects the active grant (reads in flight under
  the old generation fail their fence);
* cancel the store's pending sync jobs (a claimed job is left to its worker, which re-checks the
  store before every write);
* leave orders and listings in place, read-only, until the retention job purges them.

Calling it again for a disconnected store changes nothing and is not an error.

The reason is only logged, as one of three constants. No column holds it: this project adds no
migration for it, and ``channel_credentials.meta`` would keep a stale reason after a reconnect.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, get_args
from uuid import UUID

import psycopg
import structlog
from psycopg_pool import AsyncConnectionPool

from csa.core.db import q
from csa.core.tenancy import tx
from csa.marketplace.jobs import cancel_store_jobs

logger = structlog.get_logger(__name__)

DisconnectReason = Literal["admin", "deauthorized", "expired"]
CANCEL_CODE = "store_disconnected"


class StoreNotFoundError(LookupError):
    """The id is not a connectable TikTok store of this tenant.

    That covers another tenant's store (RLS hides it), a missing id, and a pending store that
    has no shop yet. The code is fixed (``store_not_found``) and the message is only that code;
    the console route maps it to 404 ``store_not_found``.
    """

    code = "store_not_found"

    def __init__(self) -> None:
        super().__init__(self.code)


async def disconnect_in_transaction(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    store_id: UUID,
    *,
    reason: DisconnectReason,
    now: datetime | None = None,
    expired_before: datetime | None = None,
) -> bool:
    """Disconnect on the caller's tenant transaction; True when this call did it.

    ``expired_before`` is the expired-conversion guard: the row must still be expired (both
    tokens cleared, ``expires_at`` at or before it) when locked, else nothing changes and the
    store's jobs are kept (False). Without it the store is disconnected whatever its state.

    Raises ``StoreNotFoundError`` (see there) and ``ValueError("invalid_reason")``. Pending jobs
    are cancelled on every call, so a repeat also finishes a half-done earlier attempt.
    """
    if reason not in get_args(DisconnectReason):
        raise ValueError("invalid_reason")
    row = await q.fetch_one(
        "marketplace/disconnect_store.sql",
        {
            "client_id": client_id,
            "store_id": store_id,
            "now": now or datetime.now(UTC),
            "expired_before": expired_before,
        },
        conn=conn,
    )
    if row is None or not row["found"]:
        raise StoreNotFoundError
    if row["disconnected"]:
        await cancel_store_jobs(conn, client_id, store_id, code=CANCEL_CODE)
    if row["changed"]:
        logger.info(
            "marketplace_store_disconnected",
            client_id=str(client_id),
            store_id=str(store_id),
            reason=reason,
        )
    return bool(row["changed"])


async def disconnect_store(
    pool: AsyncConnectionPool,
    client_id: UUID,
    store_id: UUID,
    *,
    reason: DisconnectReason,
) -> None:
    """Disconnect one store in its own tenant transaction (see the module docstring)."""
    async with tx(pool, str(client_id)) as conn:
        await disconnect_in_transaction(conn, client_id, store_id, reason=reason)
