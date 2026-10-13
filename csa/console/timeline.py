"""Tenant-scoped timeline reads for the web console."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from csa.core import db
from csa.core.tenancy import tx

MAX_PAGE = 200


async def list_traces(
    pool: AsyncConnectionPool,
    client_id: UUID,
    *,
    before: datetime | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    async with tx(pool, str(client_id)) as conn:
        return await db.q.fetch_all(
            "console/list_traces.sql",
            (client_id, before, before, min(limit, MAX_PAGE)),
            conn=conn,
        )


async def list_events(
    pool: AsyncConnectionPool,
    client_id: UUID,
    *,
    trace_id: UUID | None = None,
    after_id: int = 0,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Events in id order; pass the last seen id as ``after_id`` to poll for new ones."""
    async with tx(pool, str(client_id)) as conn:
        return await db.q.fetch_all(
            "console/list_events.sql",
            (client_id, after_id, trace_id, trace_id, min(limit, MAX_PAGE)),
            conn=conn,
        )
