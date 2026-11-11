"""Product availability: our shop's rows and the TikTok Shop lookup, merged into named facts.

Pure wording, no reads. Every fact says where its number comes from ("in our shop", "on our
TikTok Shop"), and only a product that is selling is described as available: the shop rows come
from ``orders/search_products.sql`` (active products only) and ``tiktok_stock_fact`` fails
closed on a listing that is not selling. Fact ids are constants, with the row index as the only
suffix, so no SKU, name or contact data reaches ``agent_runs``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from csa.agents.ecommerce.agent import (
    ECOMMERCE_SOURCE,
    STOCK_NOT_FOUND_FACT,
    STOCK_NOT_FOUND_TEXT,
    tiktok_stock_fact,
)
from csa.agents.ecommerce.models import StockLookup
from csa.agents.facts import money
from csa.contracts.facts import Fact

SOURCE = "order_agent"
SHOP_PRODUCT_FACT = "shop_product"
SHOP_NOT_FOUND_FACT = "shop_not_found"
SHOP_NOT_FOUND_TEXT = "I couldn't find that item in our shop."


def shop_fact(index: int, row: Mapping[str, Any]) -> Fact:
    """One row of our own catalogue; ``available_qty`` is never negative (stock less reserved)."""
    head = f"{row['name']} ({row['sku']}): {money(row['price'], row['currency'])}"
    qty = int(row["available_qty"])
    stock = f"{qty} available in our shop." if qty > 0 else "out of stock in our shop."
    return Fact(id=f"{SHOP_PRODUCT_FACT}_{index}", text=f"{head}, {stock}", source=SOURCE)


def availability_facts(
    rows: Sequence[Mapping[str, Any]], lookup: StockLookup | None, now: datetime
) -> list[Fact]:
    """The facts of one availability question; ``lookup`` None means TikTok was not checked.

    Every shop row gives a fact. A clear TikTok match adds its own fact (a listing that is not
    selling adds nothing); TikTok's list of choices is only worth asking about when our shop has
    no row. With nothing at all, one not-found fact says what was checked.
    """
    facts = [shop_fact(index, row) for index, row in enumerate(rows)]
    if lookup is not None and lookup.outcome != "not_found":
        tiktok = tiktok_stock_fact(lookup, now)
        if tiktok.id != STOCK_NOT_FOUND_FACT and (lookup.outcome == "match" or not rows):
            facts.append(tiktok)
    if facts:
        return facts
    if lookup is None:
        return [Fact(id=SHOP_NOT_FOUND_FACT, text=SHOP_NOT_FOUND_TEXT, source=SOURCE)]
    return [Fact(id=STOCK_NOT_FOUND_FACT, text=STOCK_NOT_FOUND_TEXT, source=ECOMMERCE_SOURCE)]
