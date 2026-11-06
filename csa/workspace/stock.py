"""TikTok Shop products and stock for staff in the workspace assistant (spec 6.8). Read-only.

The record summaries read our copy only. The stock read finds the listing in our copy, then
reads its product's stock live through ``reads.live_stock`` (the fenced reader the customer
assistant uses), or answers from our copy when the live read cannot run. Nothing here writes
to TikTok Shop or to the tenant's own products; the live read refreshes our copy of the
listing, as it does for customers.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from csa.console.business import BusinessOrder, marketplace_sync_state
from csa.core import db
from csa.core.pii import redact
from csa.core.tenancy import tx
from csa.marketplace import reads
from csa.workspace.contracts import (
    AnswerResult,
    ClarificationOption,
    ClarificationProposal,
    ProductStockReadProposal,
    ProductStockResult,
    RecordRef,
    StockVariant,
)

# Products offered when several match; listing rows read to find them (one row per variant).
PRODUCT_CHOICES = 5
LISTING_ROWS = 25
MAX_VARIANTS = 300
# TikTok's own status words are shown only when they look like a status code.
_STATUS_CODE = re.compile(r"[A-Z0-9_]{1,40}")

StockOutcome = ProductStockResult | ClarificationProposal | AnswerResult


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _plain(text: str | None, limit: int) -> str:
    """Shop text on one line, bounded (it reaches the planner and the console as plain text)."""
    return " ".join((text or "").split())[:limit]


def ago(age: timedelta, text: Mapping[str, str]) -> str:
    """ "just now", "3 min ago", "2 hours ago", "4 days ago" in the text table's language."""
    minutes = max(int(age.total_seconds() // 60), 0)
    if minutes < 1:
        return text["ago_now"]
    if minutes < 60:
        return text["ago_minutes"].format(n=minutes)
    hours = minutes // 60
    if hours < 24:
        return text["ago_hour"] if hours == 1 else text["ago_hours"].format(n=hours)
    days = hours // 24
    return text["ago_day"] if days == 1 else text["ago_days"].format(n=days)


def status_code(status: str | None) -> str | None:
    """TikTok's status, only when it is a plain status code."""
    value = (status or "").strip()
    return value if _STATUS_CODE.fullmatch(value) else None


def is_synced(order: BusinessOrder) -> bool:
    """The synced predicate of the Business pages: a TikTok order with its TikTok number."""
    return order.channel == "tiktok_shop" and bool(order.external_order_id)


def order_tiktok_line(order: BusinessOrder, now: datetime, text: Mapping[str, str]) -> str:
    """ "TikTok · synced 3 min ago, TikTok status AWAITING_SHIPMENT." States only."""
    synced = (
        text["synced_ago"].format(age=ago(now - order.synced_at, text))
        if order.synced_at is not None
        else text["not_synced"]
    )
    status = status_code(order.provider_status)
    return f"TikTok · {synced}" + (f", TikTok status {status}." if status else ".")


async def _product_rows(
    conn: AsyncConnection[Any], client_id: UUID, listing_id: UUID
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    listing = await db.q.fetch_one(
        "marketplace/get_listing_for_stock.sql",
        {"client_id": client_id, "listing_id": listing_id},
        conn=conn,
    )
    if listing is None:
        return None
    rows = await db.q.fetch_all(
        "marketplace/list_product_listings.sql",
        {
            "client_id": client_id,
            "store_id": listing["store_id"],
            "product_id": listing["external_product_id"],
            "sellable_only": False,
            "sellable": sorted(reads.SELLABLE_PRODUCT_STATUSES),
            # A SKU TikTok removed keeps its last count; it is not one of the variants.
            "exclude_removed": True,
            "removed": reads.REMOVED_PRODUCT_STATUS,
        },
        conn=conn,
    )
    return listing, rows


async def product_summary(
    pool: AsyncConnectionPool,
    client_id: UUID,
    listing_id: UUID,
    *,
    text: Mapping[str, str],
    now: datetime,
) -> str | None:
    """The open product's one-liner from our copy: name, variants, stock, sync age. No ids
    but the listing's own (as the console published it), never TikTok's product or SKU ids."""
    async with tx(pool, str(client_id)) as conn:
        found = await _product_rows(conn, client_id, listing_id)
    if found is None:
        return None
    listing, rows = found
    total = sum(max(row["stock_qty"] or 0, 0) for row in rows)
    count = len(rows)
    variants = text["variant"] if count == 1 else text["variants"]
    name = redact(_plain(listing["product_name"], 120))
    line = (
        f"TikTok Shop product “{name}” (listing {redact(str(listing_id))}): {count} "
        f"{variants}, {total} in stock in our copy, synced {ago(now - listing['synced_at'], text)}."
    )
    if (listing["product_status"] or "").upper() not in reads.SELLABLE_PRODUCT_STATUSES:
        status = status_code(listing["product_status"])
        line += f" Not on sale (TikTok status {status})." if status else " Not on sale."
    return line


def _listing_of(record: RecordRef | None) -> UUID | None:
    if record is None or record.kind != "product":
        return None
    try:
        return UUID(record.id)
    except ValueError:
        return None


def _chooser(
    matches: list[reads.ListingMatch], text: Mapping[str, str]
) -> ClarificationProposal | reads.ListingMatch:
    """One product among the matches, or a question listing up to five products.

    A listing row is one variant, so matches of one product are one answer (all its variants).
    """
    chosen = reads.clear_match(matches)
    if chosen is not None:
        return chosen
    products: dict[tuple[UUID, str], reads.ListingMatch] = {}
    for match in matches:
        products.setdefault((match.store_id, match.external_product_id), match)
    first = list(products.values())
    if len(first) == 1:
        return first[0]
    options: list[ClarificationOption] = []
    seen: dict[str, int] = {}
    for match in first[:PRODUCT_CHOICES]:
        label = _plain(match.product_name, 90) or "?"
        seen[label] = seen.get(label, 0) + 1
        if seen[label] > 1:
            label = f"{label} ({seen[label]})"
        options.append(ClarificationOption(label=label, value=str(match.id)))
    return ClarificationProposal(
        action="clarification", question=text["stock_which"], options=options
    )


async def stock_read(
    pool: AsyncConnectionPool,
    client_id: UUID,
    proposal: ProductStockReadProposal,
    record: RecordRef | None,
    *,
    live: reads.LiveReadAccess | None,
    text: Mapping[str, str],
    clock: Callable[[], datetime] = _utc_now,
) -> StockOutcome:
    """The stock of the product the staff member means, per variant.

    The caller has checked the role and the business package. Here: a connected TikTok Shop,
    then the listing (picked id, the words, or the open product record), then one live read.
    """
    listing_id = proposal.listing_id
    async with tx(pool, str(client_id)) as conn:
        if await marketplace_sync_state(conn, client_id) != "connected":
            return AnswerResult(action="answer", markdown=text["stock_no_store"])
        if listing_id is None and proposal.query is not None:
            matches = await reads.find_listings(conn, client_id, proposal.query, LISTING_ROWS)
            if not matches:
                return AnswerResult(action="answer", markdown=text["stock_not_found"])
            picked = _chooser(matches, text)
            if isinstance(picked, ClarificationProposal):
                return picked
            listing_id = picked.id
    if listing_id is None:
        listing_id = _listing_of(record)
    if listing_id is None:
        return ClarificationProposal(action="clarification", question=text["stock_ask_product"])
    # Staff see every SKU of the product, on sale or not, from the copy as well as live;
    # on_sale says whether TikTok sells it.
    stock = await reads.live_stock(
        pool, client_id, listing_id, live=live, clock=clock, include_unsellable=True
    )
    if stock is None:
        return AnswerResult(action="answer", markdown=text["stock_not_found"])
    age = clock() - stock.as_of
    if stock.checked_live:
        note = text["stock_live"]
    elif age < reads.COPY_FRESH_FOR:
        note = text["stock_copy_fresh"].format(age=ago(age, text))
    else:
        note = text["stock_copy"].format(age=ago(age, text))
    if not stock.skus:
        # Every SKU was removed by TikTok (or none is stored): say so, never "0 in stock".
        note = f"{text['stock_none_listed']} {note}"
    variants = [
        StockVariant(
            variant=_plain(sku.variant_name, 255),
            seller_sku=_plain(sku.seller_sku, 100) or None,
            stock=sku.stock_qty,
        )
        for sku in stock.skus[:MAX_VARIANTS]
    ]
    return ProductStockResult(
        action="product_stock_read",
        product=_plain(stock.product_name, 255) or "?",
        variants=variants,
        total=sum(max(variant.stock or 0, 0) for variant in variants),
        checked_live=stock.checked_live,
        on_sale=stock.sellable,
        as_of=stock.as_of,
        note=note,
    )
