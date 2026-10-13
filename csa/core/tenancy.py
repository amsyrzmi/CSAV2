"""Transaction-local tenancy GUC (LG01 §4.4).

:func:`tx` is the single code path that sets ``app.client_id``. It borrows a
pooled connection, opens a transaction, and sets the GUC with
``set_config(..., true)`` so it is transaction-local (reset at COMMIT/ROLLBACK)
and never leaks one tenant's id to the next borrower of a pooled connection.

Tenancy is enforced by the database (RLS policies read ``app.client_id``); this
module never issues ``WHERE client_id=...`` itself. There is deliberately no API
to set the GUC permanently on a connection.
"""  # LG01

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from psycopg_pool import AsyncConnectionPool


@asynccontextmanager
async def tx(
    pool: AsyncConnectionPool,
    client_id: str,
) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    """Borrow a pooled connection, open a transaction, set the tenant GUC.

    ``client_id`` is validated as a UUID string before binding (malformed input
    raises :class:`ValueError`); the RLS policy additionally casts via ``::uuid``
    so a bad value cannot silently match. The caller must do all tenant-scoped
    reads/writes on the yielded connection inside the ``with`` block.
    """
    # Validate before touching the DB; reject anything that is not a UUID.
    try:
        uuid.UUID(str(client_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"client_id is not a valid UUID: {client_id!r}") from exc

    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.client_id', %s, true)",
                (str(client_id),),
            )
            yield conn
