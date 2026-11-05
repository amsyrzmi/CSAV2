"""Read-only TikTok answers from our copy, plus one live stock read (spec 6.7, 6.8).

- ``order_status_by_number``: a customer's TikTok order number -> status words, tracking and
  the last-update day. Nothing about the buyer, the items or the money.
- ``find_listings``: listings in our copy matching a customer's words.
- ``live_stock``: the stock of a listing's product read live from TikTok through the fenced
  reader (``processor.fenced_read``), updating our copy as a side effect, or our copy when the
  live read cannot run. It has no customer wording, so the workspace assistant can use it too.
- ``PostgresMarketplaceReader``: the customer agent's reader, with the per-conversation lookup
  limit.

Logs carry constant event names and safe codes only: never order numbers, tracking numbers,
queries, shop ids or provider text.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import structlog
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict

from csa.agents.base import AgentContext
from csa.agents.ecommerce.adapters.tiktok_shop import TikTokReadError
from csa.agents.ecommerce.agent import ORDER_LOOKUP_FACT_IDS
from csa.agents.ecommerce.app_loader import AppLoader, build_app_loader
from csa.agents.ecommerce.authorization import TikTokAuthorizationError
from csa.agents.ecommerce.dispatch import TikTokReadRequest
from csa.agents.ecommerce.models import (
    ListingChoice,
    LiveStock,
    OrderLookup,
    OrderStatusFact,
    SkuStock,
    StockLookup,
)
from csa.agents.ecommerce.tiktok_models import TikTokProductSnapshot
from csa.core import crypto, db
from csa.core.settings import Settings
from csa.core.tenancy import tx
from csa.marketplace import jobs
from csa.marketplace.mapping import MappingError
from csa.marketplace.processor import REMOVED_PRODUCT_STATUS, fenced_read, store_products

logger = structlog.get_logger(__name__)

# Spec 6.7 (my call): order-number lookups per conversation per rolling hour.
ORDER_LOOKUPS_PER_HOUR = 10
LISTING_MATCHES = 5
# The live stock read (authorized shops, then the product) must leave the spoke time to answer
# from the copy when TikTok is slow.
LIVE_READ_TIMEOUT_SECONDS = 8.0
# A copy this fresh is answered from without a live read (no TikTok call per repeated question).
COPY_FRESH_FOR = timedelta(seconds=60)
# Product statuses a customer may be told are available: an allowlist, so an unknown status
# fails closed. Verify the vocabulary in Partner Center (ledger Task 21) and widen.
SELLABLE_PRODUCT_STATUSES = frozenset({"ACTIVATE"})

# Local status of a synced order -> plain words for a customer (mapping in spec 6.3).
STATUS_WORDS: dict[str, str] = {
    "pending_confirmation": "waiting for payment",
    "confirmed": "being prepared",
    "processing": "being prepared",
    "shipped": "shipped",
    "delivered": "delivered",
    "cancelled": "cancelled",
}
_OTHER_STATUS_WORDS = "being processed"
_DATED_STATUSES = frozenset({"shipped", "delivered"})
# What a failed live read may raise; anything else is a bug and propagates.
_LIVE_READ_FAILURES = (
    TikTokReadError,
    TikTokAuthorizationError,
    MappingError,
    httpx.HTTPError,
    TimeoutError,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class LiveReadAccess:
    """What a live read needs besides the pool: the app registry and the credential key."""

    load_app: AppLoader
    decrypt: Callable[[str, bytes], str]


def live_read_access(settings: Settings, pool: AsyncConnectionPool) -> LiveReadAccess | None:
    """Live TikTok stock reads on this pool, only while TikTok is on (None: our copy only).

    The worker passes it to the customer assistant, the gateway to the workspace assistant.
    """
    if not settings.tiktok_enabled:
        return None
    return LiveReadAccess(load_app=build_app_loader(settings, pool), decrypt=crypto.decrypt)


class ListingMatch(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    id: UUID
    store_id: UUID
    external_product_id: str
    product_name: str
    variant_name: str
    seller_sku: str | None
    exact: bool


def escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def order_status_by_number(
    conn: AsyncConnection[Any],
    client_id: UUID,
    order_number: str,
    *,
    timezone: str = "Asia/Kuala_Lumpur",
) -> OrderStatusFact | None:
    """The tenant's TikTok order with this TikTok order number, as status words only."""
    row = await db.q.fetch_one(
        "marketplace/order_status_by_number.sql",
        {"client_id": client_id, "order_number": order_number},
        conn=conn,
    )
    if row is None:
        return None
    status = row["status"]
    updated = row["provider_updated_at"]
    on: date | None = None
    if status in _DATED_STATUSES and updated is not None:
        on = updated.astimezone(ZoneInfo(timezone)).date()
    return OrderStatusFact(
        status_text=STATUS_WORDS.get(status, _OTHER_STATUS_WORDS),
        tracking_no=row["tracking_no"] or None,
        carrier=row["carrier"] or None,
        shipped_or_delivered_on=on,
    )


async def count_order_lookups(
    conn: AsyncConnection[Any], client_id: UUID, conversation_id: UUID
) -> int:
    """Order-number lookups recorded for this conversation in the last hour."""
    row = await db.q.fetch_one(
        "marketplace/count_order_lookups.sql",
        {
            "client_id": client_id,
            "conversation_id": conversation_id,
            "fact_ids": list(ORDER_LOOKUP_FACT_IDS),
        },
        conn=conn,
    )
    return int(row["used"]) if row is not None else 0


async def find_listings(
    conn: AsyncConnection[Any], client_id: UUID, query: str, limit: int = LISTING_MATCHES
) -> list[ListingMatch]:
    """Listings of the tenant whose name, variant or SKU contains the words; exact ones first."""
    words = query.strip()
    if not words:
        return []
    rows = await db.q.fetch_all(
        "marketplace/find_listings.sql",
        {
            "client_id": client_id,
            "pattern": f"%{escape_like(words)}%",
            "words": words,
            "sellable": sorted(SELLABLE_PRODUCT_STATUSES),
            "limit": limit,
        },
        conn=conn,
    )
    return [ListingMatch.model_validate(row) for row in rows]


def clear_match(matches: list[ListingMatch]) -> ListingMatch | None:
    """One listing, or the only exact one among several; otherwise the customer must choose."""
    if len(matches) == 1:
        return matches[0]
    exact = [match for match in matches if match.exact]
    return exact[0] if len(exact) == 1 else None


async def live_stock(
    pool: AsyncConnectionPool,
    client_id: UUID,
    listing_id: UUID,
    *,
    live: LiveReadAccess | None,
    clock: Callable[[], datetime] = _utc_now,
    include_unsellable: bool = False,
) -> LiveStock | None:
    """The stock of the listing's product, live when possible, else our copy.

    Live: the store must be connected; one fenced ``product_detail`` read, then the product's
    listings are upserted behind the store's write gate (a store disconnected meanwhile keeps
    its copy untouched). Copy: the copy is under ``COPY_FRESH_FOR`` old, TikTok is off here, the
    store cannot read, or the read failed; only sellable SKUs come from the copy, unless
    ``include_unsellable`` (staff only: the workspace assistant shows every SKU with its status
    except the ones TikTok removed, whose counts are stale; the customer path never sets it and
    stays fail-closed). ``sellable`` follows the live
    product status (or the copy's). None when the tenant has no such listing.
    """
    async with tx(pool, str(client_id)) as conn:
        listing = await db.q.fetch_one(
            "marketplace/get_listing_for_stock.sql",
            {"client_id": client_id, "listing_id": listing_id},
            conn=conn,
        )
        if listing is None:
            return None
        store = await jobs.get_sync_store(conn, client_id, listing["store_id"])
    store_id: UUID = listing["store_id"]
    product_id: str = listing["external_product_id"]
    code = "live_reads_off"
    fresh_copy = clock() - listing["synced_at"] < COPY_FRESH_FOR
    if fresh_copy:
        code = "copy_fresh"
    elif live is not None and store is not None:
        code = f"store_{store.sync_state}"
        if store.sync_state == "connected" and store.grant_id and store.shop_id:
            try:
                async with asyncio.timeout(LIVE_READ_TIMEOUT_SECONDS):
                    product = await fenced_read(
                        pool,
                        client_id,
                        store.grant_id,
                        TikTokReadRequest(kind="product_detail", product_id=product_id),
                        load_app=live.load_app,
                        decrypt=live.decrypt,
                    )
                if not isinstance(product, TikTokProductSnapshot) or product.id != product_id:
                    raise TikTokReadError("unexpected_read_result")
                now = clock()
                async with tx(pool, str(client_id)) as conn:
                    if await jobs.lock_store_for_write(conn, client_id, store_id, store.shop_id):
                        await store_products(conn, client_id, store_id, (product,), now)
                    stored = await _product_rows(
                        conn, client_id, store_id, product_id, sellable_only=False
                    )
                logger.info("marketplace_live_stock_read")
                return _from_live(listing_id, product, stored, now)
            except _LIVE_READ_FAILURES as exc:
                code = getattr(exc, "code", None) or (
                    "read_timeout" if isinstance(exc, TimeoutError) else "read_failed"
                )
    logger.info("marketplace_live_stock_unavailable", code=_safe(code))
    async with tx(pool, str(client_id)) as conn:
        stored = await _product_rows(
            conn,
            client_id,
            store_id,
            product_id,
            sellable_only=not include_unsellable,
            exclude_removed=include_unsellable,
        )
    return LiveStock(
        listing_id=listing_id,
        product_name=listing["product_name"],
        skus=tuple(_sku_from_row(row) for row in stored),
        checked_live=False,
        as_of=listing["synced_at"],
        sellable=_sellable(listing["product_status"]),
    )


def _sellable(status: str | None) -> bool:
    return (status or "").upper() in SELLABLE_PRODUCT_STATUSES


async def _product_rows(
    conn: AsyncConnection[Any],
    client_id: UUID,
    store_id: UUID,
    product_id: str,
    *,
    sellable_only: bool,
    exclude_removed: bool = False,
) -> list[dict[str, Any]]:
    return await db.q.fetch_all(
        "marketplace/list_product_listings.sql",
        {
            "client_id": client_id,
            "store_id": store_id,
            "product_id": product_id,
            "sellable_only": sellable_only,
            "sellable": sorted(SELLABLE_PRODUCT_STATUSES),
            "exclude_removed": exclude_removed,
            "removed": REMOVED_PRODUCT_STATUS,
        },
        conn=conn,
    )


def _sku_from_row(row: dict[str, Any]) -> SkuStock:
    return SkuStock(
        listing_id=row["id"],
        external_sku_id=row["external_sku_id"],
        variant_name=row["variant_name"],
        seller_sku=row["seller_sku"],
        stock_qty=row["stock_qty"],
        price=row["price"],
        currency=row["currency"],
    )


def _from_live(
    listing_id: UUID,
    product: TikTokProductSnapshot,
    stored: list[dict[str, Any]],
    now: datetime,
) -> LiveStock:
    ids = {row["external_sku_id"]: row["id"] for row in stored}
    return LiveStock(
        listing_id=listing_id,
        product_name=product.name,
        skus=tuple(
            SkuStock(
                listing_id=ids.get(sku.id),
                external_sku_id=sku.id,
                variant_name=sku.variant_name,
                seller_sku=sku.seller_sku,
                stock_qty=sku.stock_qty,
                price=Decimal(sku.price).quantize(Decimal("0.01")),
                currency=sku.currency,
            )
            for sku in product.skus
        ),
        checked_live=True,
        as_of=now,
        sellable=_sellable(product.status),
    )


def _safe(code: str) -> str:
    return code if code.replace("_", "").isalnum() and len(code) <= 64 else "read_failed"


class PostgresMarketplaceReader:
    """The customer agent's reads, on the tenant-scoped app pool."""

    def __init__(
        self,
        pool: AsyncConnectionPool,
        *,
        live: LiveReadAccess | None,
        lookups_per_hour: int = ORDER_LOOKUPS_PER_HOUR,
    ) -> None:
        self._pool = pool
        self._live = live
        self._lookups_per_hour = lookups_per_hour

    async def order_status(self, ctx: AgentContext, order_number: str) -> OrderLookup:
        # Without a conversation the limit cannot be counted, so nothing is looked up.
        if ctx.conversation_id is None:
            logger.info("marketplace_order_lookup_limited")
            return OrderLookup(outcome="limited")
        async with tx(self._pool, str(ctx.client_id)) as conn:
            used = await count_order_lookups(conn, ctx.client_id, ctx.conversation_id)
            if used >= self._lookups_per_hour:
                logger.info("marketplace_order_lookup_limited")
                return OrderLookup(outcome="limited")
            fact = await order_status_by_number(
                conn, ctx.client_id, order_number, timezone=ctx.timezone
            )
        if fact is None:
            return OrderLookup(outcome="not_found")
        return OrderLookup(outcome="found", fact=fact)

    async def stock_check(self, ctx: AgentContext, query: str) -> StockLookup:
        async with tx(self._pool, str(ctx.client_id)) as conn:
            matches = await find_listings(conn, ctx.client_id, query)
        if not matches:
            return StockLookup(outcome="not_found")
        match = clear_match(matches)
        if match is None:
            return StockLookup(
                outcome="choices",
                choices=tuple(
                    ListingChoice(
                        listing_id=m.id,
                        product_name=m.product_name,
                        variant_name=m.variant_name,
                        seller_sku=m.seller_sku,
                    )
                    for m in matches
                ),
            )
        stock = await live_stock(
            self._pool, ctx.client_id, match.id, live=self._live, clock=ctx.now
        )
        if stock is None or not stock.sellable:
            return StockLookup(outcome="not_found")
        return StockLookup(outcome="match", stock=stock)


__all__ = [
    "COPY_FRESH_FOR",
    "LISTING_MATCHES",
    "SELLABLE_PRODUCT_STATUSES",
    "ORDER_LOOKUPS_PER_HOUR",
    "REMOVED_PRODUCT_STATUS",
    "STATUS_WORDS",
    "ListingMatch",
    "LiveReadAccess",
    "LiveStock",
    "OrderStatusFact",
    "PostgresMarketplaceReader",
    "count_order_lookups",
    "find_listings",
    "live_read_access",
    "live_stock",
    "order_status_by_number",
]
