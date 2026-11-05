"""Scheduled marketplace jobs: buyer-data retention (spec 6.6) and sync catch-up (spec 6.4).

Both run per client on the tenant's transaction from the job runner, as
``fn(conn, client_id, clock, *, batch_size)``, and are safe to run again at any time.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

import psycopg

from csa.core.db import q
from csa.jobs.clock import Clock
from csa.marketplace.disconnect import disconnect_in_transaction
from csa.marketplace.jobs import enqueue_job, list_connected_stores, reset_products_sync

# Buyer details (ship_to, any buyer name or phone) are cleared this many days after a TikTok
# order is delivered or cancelled. If TikTok's partner data policy sets a shorter limit, this
# one constant becomes that limit.
BUYER_DATA_DAYS = 90
# A disconnected store keeps its orders and listings read-only for this many days. After that
# its orders' buyer data is cleared and its listings are deleted. A reconnect before then
# clears disconnected_at, so nothing is purged and the sync resumes from its cursor.
DISCONNECT_PURGE_DAYS = 30
# A store marked ``expired`` (a refresh was refused) is shown as expired for this many days,
# counted from expires_at (set when it was marked), then disconnected with reason ``expired``.
EXPIRED_GRACE_DAYS = 30
# A pending store (the first Connect never came back) is abandoned after this many days.
PENDING_STORE_DAYS = 1


async def run_marketplace_purge(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    clock: Clock,
    *,
    batch_size: int = 100,
) -> dict[str, int]:
    """Apply the retention rules for one tenant; each step touches at most ``batch_size`` rows.

    1. ``expired_disconnected``: stores expired for ``EXPIRED_GRACE_DAYS`` become disconnected
       (reason ``expired``). Done first, so the disconnect clock starts today.
    2. ``buyer_data_aged``: TikTok orders ``delivered``/``cancelled`` for ``BUYER_DATA_DAYS``
       (at exactly the cutoff too) lose ``ship_to`` and get ``buyer_cleared_at``. The snapshot
       has no delivered or cancelled time, so the clock is ``provider_updated_at`` (TikTok's
       last update of the order), or ``synced_at`` if TikTok gave no update time.
    3. ``buyer_data_disconnected``: the same clearing for every order of a store disconnected
       for ``DISCONNECT_PURGE_DAYS``.
    4. ``listings_deleted``: those stores' listings are deleted, and each such store forgets that
       its products were copied (``products_reset``), so a reconnect queues the products backfill
       again. Its orders' progress is kept.
    5. ``pending_stores_deleted``: pending stores older than ``PENDING_STORE_DAYS`` with no live
       authorization attempt (and no grant history) are deleted.

    Order number, totals, statuses, dates and items are kept. A bigger backlog than
    ``batch_size`` is finished by the next runs.
    """
    if batch_size < 1:
        raise ValueError("invalid_batch_size")
    now = clock.now()
    base = {"client_id": client_id, "batch_size": batch_size, "now": now}
    buyer_cutoff = now - timedelta(days=BUYER_DATA_DAYS)
    disconnect_cutoff = now - timedelta(days=DISCONNECT_PURGE_DAYS)
    expired_cutoff = now - timedelta(days=EXPIRED_GRACE_DAYS)

    expired = await q.fetch_all(
        "marketplace/list_expired_stores.sql",
        {**base, "expired_cutoff": expired_cutoff},
        conn=conn,
    )
    expired_done = 0
    for row in expired:
        # The list locks its rows (a reconnect in flight is skipped); the guard re-tests the
        # expired state inside the disconnect, so a store reconnected meanwhile is left alone.
        if await disconnect_in_transaction(
            conn, client_id, row["id"], reason="expired", now=now, expired_before=expired_cutoff
        ):
            expired_done += 1

    scopes = {"buyer_cutoff": buyer_cutoff, "disconnect_cutoff": disconnect_cutoff}
    aged = await q.fetch_all(
        "marketplace/purge_orders.sql", {**base, **scopes, "scope": "aged"}, conn=conn
    )
    disconnected = await q.fetch_all(
        "marketplace/purge_orders.sql", {**base, **scopes, "scope": "disconnected"}, conn=conn
    )
    listings = await q.fetch_all(
        "marketplace/purge_listings.sql",
        {**base, "disconnect_cutoff": disconnect_cutoff},
        conn=conn,
    )
    products_reset = 0
    for store_id in dict.fromkeys(row["store_id"] for row in listings):
        if await reset_products_sync(conn, client_id, store_id, now=now):
            products_reset += 1
    pending = await q.fetch_all(
        "marketplace/purge_pending_stores.sql",
        {**base, "pending_cutoff": now - timedelta(days=PENDING_STORE_DAYS)},
        conn=conn,
    )
    return {
        "expired_disconnected": expired_done,
        "buyer_data_aged": len(aged),
        "buyer_data_disconnected": len(disconnected),
        "listings_deleted": len(listings),
        "products_reset": products_reset,
        "pending_stores_deleted": len(pending),
    }


async def run_marketplace_catch_up(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    clock: Clock,
    *,
    batch_size: int = 100,
) -> dict[str, int]:
    """Queue one ``catch_up`` job for every connected store of the tenant.

    Connected means an active grant at the current generation with its shop binding, no
    ``disconnected_at`` and live tokens (``list_connected_stores``). A job already pending for
    the store absorbs the new one (``folded``), so running often costs nothing. The processor
    turns a catch-up on a never-backfilled store into its backfills, which also covers a connect
    whose first-sync queueing failed.

    ``batch_size`` is accepted for the job interface but does not cap the stores: one cheap
    folded insert each, and a cap in a fixed order would starve the stores after it.
    """
    del clock, batch_size  # the jobs carry their own due time; see the docstring
    stores = await list_connected_stores(conn, client_id)
    queued = 0
    for store in stores:
        if await enqueue_job(conn, client_id=client_id, store_id=store.store_id, kind="catch_up"):
            queued += 1
    return {"stores": len(stores), "queued": queued, "folded": len(stores) - queued}
