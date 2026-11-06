"""Idempotent writes of mapped marketplace orders and listings.

Every function runs on a tenant connection from ``csa.core.tenancy.tx`` (RLS applies). Neither
touches ``products``, stock or reservations. The caller owns the transaction.
"""

from __future__ import annotations

from typing import Any, Literal

import psycopg
from psycopg.types.json import Jsonb

from csa.core.db import q
from csa.marketplace.mapping import MappedListing, MappedOrder

UpsertResult = Literal["inserted", "updated", "stale"]


async def upsert_order(conn: psycopg.AsyncConnection[Any], order: MappedOrder) -> UpsertResult:
    """Insert or refresh one order and replace its lines; ``stale`` writes nothing."""
    params = order.model_dump(exclude={"lines", "ship_to"})
    params["ship_to"] = None if order.ship_to is None else Jsonb(order.ship_to)
    row = await q.fetch_one("marketplace/upsert_order.sql", params, conn=conn)
    if row is None:
        return "stale"
    key = {"client_id": order.client_id, "order_id": order.id}
    await q.execute("marketplace/delete_order_items.sql", key, conn=conn)
    for line in order.lines:
        await q.execute(
            "marketplace/insert_order_item.sql", {**key, **line.model_dump()}, conn=conn
        )
    # The lines trigger rewrites subtotal (and zeroes it when lines go); the mapped one is final.
    await q.execute(
        "marketplace/restore_order_subtotal.sql", {**key, "subtotal": order.subtotal}, conn=conn
    )
    return "inserted" if row["inserted"] else "updated"


async def upsert_listings(conn: psycopg.AsyncConnection[Any], rows: list[MappedListing]) -> int:
    """Upsert listings; returns how many were written (stale rows are skipped, not counted)."""
    written = 0
    for row in rows:
        if await q.fetch_one("marketplace/upsert_listing.sql", row.model_dump(), conn=conn):
            written += 1
    return written
