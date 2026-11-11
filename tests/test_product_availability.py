"""One product availability read across our shop and our TikTok Shop (customer bot PR 2).

Synthetic data only. Every TikTok call goes through the processor's fenced read on its own
transport factory, replaced with ``httpx.MockTransport`` (``FakeTikTok``). Each test makes its
own tenant. The merge rules are also pinned without a database in tests/test_ecommerce_agent.py.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from csa.agents.base import AgentContext
from csa.agents.ecommerce.agent import STOCK_UNCHECKED_TEXT
from csa.agents.orders.agent import OrderAgent, OrderInput
from csa.agents.orders.availability import SHOP_NOT_FOUND_TEXT
from csa.contracts.facts import AgentResult
from csa.marketplace.reads import LiveReadAccess, PostgresMarketplaceReader
from psycopg_pool import AsyncConnectionPool

from tests.conftest import requires_docker
from tests.test_ecommerce_storage import decrypt
from tests.test_marketplace_processor import FakeTikTok, any_app_loader, server_error, store_for
from tests.test_marketplace_reads import make_ctx, now, plain_store, seed_listings
from tests.test_tiktok_product_reads import product_payload, sku_payload

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_ecommerce_grants"]

NOT_FOUND_EVERYWHERE = (
    "I couldn't find that item in our shop or on our TikTok Shop. Our staff can help you."
)


class Switch:
    """The ecommerce_agent capability of the company, counting how often it is asked."""

    def __init__(self, on: bool) -> None:
        self.on = on
        self.asked: list[UUID] = []

    async def __call__(self, client_id: UUID) -> bool:
        self.asked.append(client_id)
        return self.on


async def add_product(
    migrated_dsn: str,
    client: UUID,
    sku: str,
    name: str,
    *,
    stock: int,
    reserved: int = 0,
    price: str = "12.00",
    active: bool = True,
) -> None:
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        await conn.execute(
            "INSERT INTO products (id, client_id, sku, name, price, stock_qty, reserved_qty, "
            "is_active) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (uuid4(), client, sku, name, price, stock, reserved, active),
        )


def live_access(pool: AsyncConnectionPool) -> LiveReadAccess:
    return LiveReadAccess(load_app=any_app_loader(pool), decrypt=decrypt)


async def ask(
    pool: AsyncConnectionPool,
    ctx: AgentContext,
    query: str,
    *,
    capability: Switch,
    live: LiveReadAccess | None = None,
) -> AgentResult:
    agent = OrderAgent(PostgresMarketplaceReader(pool, live=live), marketplace_on=capability)
    return await agent.run(
        ctx, OrderInput.model_validate({"action": "product_availability", "query": query})
    )


def pairs(result: AgentResult) -> list[tuple[str, str]]:
    assert result.ok, result.error
    return [(fact.id, fact.text) for fact in result.facts]


def tiktok_mug(tiktok_id: str = "SYNTH-P-MUG", *, qty: int = 10) -> dict[str, Any]:
    payload = product_payload(tiktok_id)
    payload["skus"] = [sku_payload("SYNTH-SKU-1", quantities=(qty,))]
    return payload


async def test_a_product_only_in_our_shop_names_the_shop_and_calls_no_tiktok(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = await store_for(migrated_dsn, pool)
    client = binding.client_id
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    await seed_listings(pool, client, binding.record_id, tiktok_mug(), synced_at=now())
    await add_product(migrated_dsn, client, "LOCAL-LAMP", "Synthetic lamp", stock=7)
    off = Switch(False)
    result = await ask(pool, make_ctx(pool, client), "lamp", capability=off, live=live_access(pool))
    assert pairs(result) == [
        ("shop_product_0", "Synthetic lamp (LOCAL-LAMP): MYR 12.00, 7 available in our shop.")
    ]
    # The company has not switched on the TikTok service: it is not read, not even from the copy.
    assert off.asked == [client]
    assert tiktok.requests == []
    # A TikTok product the customer names is not described either.
    result = await ask(pool, make_ctx(pool, client), "mug", capability=off, live=live_access(pool))
    assert pairs(result) == [("shop_not_found", "I couldn't find that item in our shop.")]
    assert tiktok.requests == []


async def test_a_product_only_on_tiktok_is_answered_from_tiktok(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = await store_for(migrated_dsn, pool)
    client = binding.client_id
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    old = tiktok_mug(qty=1)
    old["update_time"] = 1_793_750_000
    await seed_listings(pool, client, binding.record_id, old, synced_at=now() - timedelta(hours=2))
    tiktok.product_details["SYNTH-P-MUG"] = tiktok_mug(qty=8)
    on = Switch(True)
    result = await ask(
        pool, make_ctx(pool, client), "mug-blk-m", capability=on, live=live_access(pool)
    )
    assert pairs(result) == [
        ("tiktok_stock", "Synthetic mug (Black / M), MYR 19.90: 8 available on our TikTok Shop.")
    ]
    assert [kind for kind, _, _ in tiktok.requests] == ["shops", "product_detail"]


async def test_a_product_in_both_shows_both_numbers_with_their_sources(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = await store_for(migrated_dsn, pool)
    client = binding.client_id
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    old = tiktok_mug(qty=1)
    old["update_time"] = 1_793_750_000
    await seed_listings(pool, client, binding.record_id, old, synced_at=now() - timedelta(hours=2))
    tiktok.product_details["SYNTH-P-MUG"] = tiktok_mug(qty=8)
    await add_product(migrated_dsn, client, "MUG-BLK-M", "Synthetic mug", stock=5, price="9.50")
    result = await ask(
        pool, make_ctx(pool, client), "mug-blk-m", capability=Switch(True), live=live_access(pool)
    )
    assert pairs(result) == [
        ("shop_product_0", "Synthetic mug (MUG-BLK-M): MYR 9.50, 5 available in our shop."),
        ("tiktok_stock", "Synthetic mug (Black / M), MYR 19.90: 8 available on our TikTok Shop."),
    ]


async def test_an_unsellable_listing_or_an_inactive_product_is_never_available(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = await store_for(migrated_dsn, pool)
    client = binding.client_id
    FakeTikTok(binding.shop_id).install(monkeypatch)
    draft = tiktok_mug("SYNTH-P-DRAFT")
    draft["status"] = "DRAFT"
    await seed_listings(
        pool, client, binding.record_id, draft, synced_at=now() - timedelta(minutes=30)
    )
    await add_product(migrated_dsn, client, "OLD-LAMP", "Retired lamp", stock=9, active=False)
    await add_product(migrated_dsn, client, "NEW-LAMP", "Current lamp", stock=2)
    on = Switch(True)
    # Both checked, the only matches unsellable: the existing both-checked sentence, once.
    result = await ask(pool, make_ctx(pool, client), "mug-blk-m", capability=on)
    assert pairs(result) == [("tiktok_stock_not_found", NOT_FOUND_EVERYWHERE)]
    result = await ask(pool, make_ctx(pool, client), "retired lamp", capability=on)
    assert pairs(result) == [("tiktok_stock_not_found", NOT_FOUND_EVERYWHERE)]
    # An unsellable TikTok listing next to a sellable shop row adds nothing.
    await add_product(migrated_dsn, client, "MUG-BLK-M", "Synthetic mug", stock=4)
    result = await ask(pool, make_ctx(pool, client), "mug-blk-m", capability=on)
    assert pairs(result) == [
        ("shop_product_0", "Synthetic mug (MUG-BLK-M): MYR 12.00, 4 available in our shop.")
    ]
    # Only our shop checked: the inactive product is simply not there.
    result = await ask(pool, make_ctx(pool, client), "retired lamp", capability=Switch(False))
    assert pairs(result) == [("shop_not_found", SHOP_NOT_FOUND_TEXT)]
    for _, text in pairs(result):
        assert "available" not in text


async def test_out_of_stock_in_our_shop_says_out_of_stock(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    client, _, _ = await plain_store(migrated_dsn)
    await add_product(migrated_dsn, client, "SOLD-1", "Sold out lamp", stock=3, reserved=3)
    await add_product(migrated_dsn, client, "NONE-1", "Empty lamp", stock=0, price="4.5")
    result = await ask(pool, make_ctx(pool, client), "lamp", capability=Switch(False))
    assert pairs(result) == [
        ("shop_product_0", "Empty lamp (NONE-1): MYR 4.50, out of stock in our shop."),
        ("shop_product_1", "Sold out lamp (SOLD-1): MYR 12.00, out of stock in our shop."),
    ]


@pytest.mark.parametrize(
    ("age", "text"),
    [
        (
            timedelta(hours=2, minutes=1),
            "Synthetic mug (Black / M), MYR 19.90 on our TikTok Shop: "
            "10 were available as of 2 hours ago.",
        ),
        (
            timedelta(hours=7),
            f"Synthetic mug (Black / M) on our TikTok Shop: {STOCK_UNCHECKED_TEXT}",
        ),
    ],
)
async def test_a_failed_live_read_quotes_the_stored_listing_with_its_age_or_cannot_check(
    pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
    age: timedelta,
    text: str,
) -> None:
    binding = await store_for(migrated_dsn, pool)
    client = binding.client_id
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    await seed_listings(pool, client, binding.record_id, tiktok_mug(), synced_at=now() - age)
    tiktok.fail[("product_detail", "SYNTH-P-MUG")] = server_error
    result = await ask(
        pool, make_ctx(pool, client), "mug-blk-m", capability=Switch(True), live=live_access(pool)
    )
    assert pairs(result) == [("tiktok_stock", text)]
    # One attempt, never retried.
    assert [k for k, _, _ in tiktok.requests if k == "product_detail"] == ["product_detail"]


async def test_tiktok_choices_only_when_our_shop_has_no_match(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = await store_for(migrated_dsn, pool)
    client = binding.client_id
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    payload = product_payload("SYNTH-P-MANY")
    payload["skus"] = [
        sku_payload(f"SYNTH-SKU-{i}", attributes=(f"Colour {i}",), seller_sku=f"MUG-{i}")
        for i in range(1, 4)
    ]
    await seed_listings(pool, client, binding.record_id, payload, synced_at=now())
    on = Switch(True)
    result = await ask(
        pool, make_ctx(pool, client), "synthetic mug", capability=on, live=live_access(pool)
    )
    ((fact_id, text),) = pairs(result)
    assert fact_id == "tiktok_stock_choices"
    assert text.startswith("Several items on our TikTok Shop match. Which one do you mean?")
    assert tiktok.requests == []
    # With a row in our shop the customer gets that answer, and no question about TikTok.
    await add_product(migrated_dsn, client, "SYN-MUG", "Synthetic mug", stock=6)
    result = await ask(
        pool, make_ctx(pool, client), "synthetic mug", capability=on, live=live_access(pool)
    )
    assert pairs(result) == [
        ("shop_product_0", "Synthetic mug (SYN-MUG): MYR 12.00, 6 available in our shop.")
    ]


async def test_no_match_anywhere_says_so_once(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = await store_for(migrated_dsn, pool)
    client = binding.client_id
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    await seed_listings(pool, client, binding.record_id, tiktok_mug(), synced_at=now())
    result = await ask(
        pool,
        make_ctx(pool, client),
        "nothing like it",
        capability=Switch(True),
        live=live_access(pool),
    )
    assert pairs(result) == [("tiktok_stock_not_found", NOT_FOUND_EVERYWHERE)]
    result = await ask(pool, make_ctx(pool, client), "nothing like it", capability=Switch(False))
    assert pairs(result) == [("shop_not_found", "I couldn't find that item in our shop.")]
    assert SHOP_NOT_FOUND_TEXT == "I couldn't find that item in our shop."
    assert tiktok.requests == []


async def test_the_web_sandbox_reads_only_the_catalogue(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = await store_for(migrated_dsn, pool)
    client = binding.client_id
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    await seed_listings(pool, client, binding.record_id, tiktok_mug(), synced_at=now())
    await add_product(migrated_dsn, client, "LOCAL-LAMP", "Synthetic lamp", stock=7)
    web = replace(make_ctx(pool, client), channel="web")
    on = Switch(True)
    result = await ask(pool, web, "lamp", capability=on, live=live_access(pool))
    assert pairs(result) == [
        ("shop_product_0", "Synthetic lamp (LOCAL-LAMP): MYR 12.00, 7 available in our shop.")
    ]
    result = await ask(pool, web, "mug", capability=on, live=live_access(pool))
    assert pairs(result) == [("shop_not_found", SHOP_NOT_FOUND_TEXT)]
    # Not even the capability was asked, and TikTok was never reached.
    assert on.asked == []
    assert tiktok.requests == []


async def test_another_companys_listing_is_never_read(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = await store_for(migrated_dsn, pool)
    owner = binding.client_id
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    await seed_listings(pool, owner, binding.record_id, tiktok_mug(), synced_at=now())
    await add_product(migrated_dsn, owner, "MUG-BLK-M", "Synthetic mug", stock=5)
    other, _, _ = await plain_store(migrated_dsn)
    on = Switch(True)
    result = await ask(
        pool, make_ctx(pool, other), "mug-blk-m", capability=on, live=live_access(pool)
    )
    assert pairs(result) == [("tiktok_stock_not_found", NOT_FOUND_EVERYWHERE)]
    assert on.asked == [other]
    # Neither the listing nor the live shop of the other company was touched.
    assert tiktok.requests == []
    result = await ask(pool, make_ctx(pool, other), "mug-blk-m", capability=Switch(False))
    assert pairs(result) == [("shop_not_found", SHOP_NOT_FOUND_TEXT)]
    # The owner still gets its own rows.
    result = await ask(
        pool, make_ctx(pool, owner), "mug-blk-m", capability=Switch(False), live=None
    )
    assert pairs(result) == [
        ("shop_product_0", "Synthetic mug (MUG-BLK-M): MYR 12.00, 5 available in our shop.")
    ]


@pytest.mark.parametrize("query", ["   ", "%", "_%_"])
async def test_a_query_with_no_words_never_lists_the_catalogue(
    pool: AsyncConnectionPool, migrated_dsn: str, query: str
) -> None:
    client, _, _ = await plain_store(migrated_dsn)
    await add_product(migrated_dsn, client, "LOCAL-LAMP", "Synthetic lamp", stock=7)
    result = await ask(pool, make_ctx(pool, client), query, capability=Switch(False))
    assert pairs(result) == [("shop_not_found", SHOP_NOT_FOUND_TEXT)]
