"""Customer TikTok reads against a disposable Postgres and a mocked TikTok (spec 6.7).

Synthetic data only. Every TikTok call goes through the processor's fenced read on its own
transport factory, replaced here with ``httpx.MockTransport`` (``FakeTikTok``). Each test makes
its own tenant with its own synthetic shop id.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import psycopg
import pytest
from csa.agents.base import AgentContext
from csa.agents.ecommerce import TikTokOrderSnapshot, TikTokProductSnapshot
from csa.agents.ecommerce.agent import STOCK_UNCHECKED_TEXT, EcommerceAgent
from csa.agents.ecommerce.models import EcommerceInput, OrderStatusFact
from csa.agents.orders.availability import availability_facts
from csa.agents.recorder import PostgresRunRecorder
from csa.agents.runner import SpokeRunner
from csa.contracts.callbacks import PendingActionStore
from csa.contracts.facts import Fact
from csa.contracts.plan import Task
from csa.core.tenancy import tx
from csa.marketplace.mapping import map_listings, map_order
from csa.marketplace.reads import (
    LiveReadAccess,
    PostgresMarketplaceReader,
    find_listings,
    live_stock,
    order_status_by_number,
)
from csa.marketplace.store import upsert_listings, upsert_order
from psycopg_pool import AsyncConnectionPool
from structlog.testing import capture_logs

from tests.conftest import requires_docker
from tests.test_coordinator import make_envelope
from tests.test_ecommerce_storage import decrypt
from tests.test_marketplace_processor import (
    PRIVATE_TEXT,
    FakeTikTok,
    any_app_loader,
    fetch,
    order,
    server_error,
    store_for,
)
from tests.test_tiktok_product_reads import product_payload, sku_payload

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_ecommerce_grants"]

ORDER_NUMBER = "576461413038785752"
# Seeded by extended_order(): every one of these must stay out of a customer's answer.
PRIVATE_STRINGS = (
    "Synthetic Recipient",
    "+60000000000",
    "60000000000",
    "1 Synthetic Lane",
    "Synthetic Town",
    "Synthetic product",
    "SYNTHETIC-SKU",
    "SYNTH-LINE",
    "12.34",
    "10.00",
    "3.00",
    "PRIVATE",
)


def now() -> datetime:
    return datetime.now(UTC)


async def plain_store(migrated_dsn: str) -> tuple[UUID, UUID, str]:
    """A tenant with one TikTok store row (no grant): enough for order lookups."""
    client, store = uuid4(), uuid4()
    shop = f"SYNTH-SHOP-{store}"
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        await conn.execute(
            "INSERT INTO clients(id,slug,name,platform_version) VALUES (%s,%s,'Synthetic','t')",
            (client, f"mkt-reads-{client}"),
        )
        await conn.execute(
            "INSERT INTO channel_credentials(id,client_id,channel,shop_id) "
            "VALUES (%s,%s,'tiktok_shop',%s)",
            (store, client, shop),
        )
    return client, store, shop


async def seed_order(
    pool: AsyncConnectionPool,
    client: UUID,
    store: UUID,
    shop: str,
    number: str,
    **changes: Any,
) -> str:
    snapshot = TikTokOrderSnapshot.model_validate(order(number, **changes))
    mapped = map_order(client, store, shop, snapshot, now())
    async with tx(pool, str(client)) as conn:
        await upsert_order(conn, mapped)
    return mapped.id


async def seed_listings(
    pool: AsyncConnectionPool,
    client: UUID,
    store: UUID,
    payload: dict,
    *,
    synced_at: datetime,
) -> TikTokProductSnapshot:
    product = TikTokProductSnapshot.model_validate(payload)
    async with tx(pool, str(client)) as conn:
        await upsert_listings(conn, map_listings(client, store, product, synced_at))
    return product


def make_ctx(
    pool: AsyncConnectionPool, client: UUID, conversation: UUID | None = None
) -> AgentContext:
    return AgentContext(
        client_id=client,
        contact_id=uuid4(),
        trace_id=uuid4(),
        conversation_id=conversation or uuid4(),
        idempotency_key=f"idem-{uuid4()}",
        channel="telegram",
        pool=pool,
        pending_actions=MagicMock(spec=PendingActionStore),
    )


def agent(pool: AsyncConnectionPool, live: LiveReadAccess | None = None) -> EcommerceAgent:
    return EcommerceAgent(PostgresMarketplaceReader(pool, live=live))


async def ask(agent_: EcommerceAgent, ctx: AgentContext, **inp: Any):  # type: ignore[no-untyped-def]
    return await agent_.run(ctx, EcommerceInput.model_validate(inp))


class Answer:
    """What the customer-facing read says about TikTok stock: the lookup, worded as in a turn."""

    def __init__(self, facts: list[Fact]) -> None:
        self.facts = facts


async def stock_answer(
    pool: AsyncConnectionPool, live: LiveReadAccess | None, ctx: AgentContext, query: str
) -> Answer:
    lookup = await PostgresMarketplaceReader(pool, live=live).stock_check(ctx, query)
    return Answer(availability_facts([], lookup, ctx.now()))


# -- order status by number -------------------------------------------------------------------


async def test_order_status_reply_contains_no_pii_fields(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    client, store, shop = await plain_store(migrated_dsn)
    internal_id = await seed_order(pool, client, store, shop, ORDER_NUMBER, status="IN_TRANSIT")
    # The stored row really holds the buyer data the answer must not show.
    rows = await fetch(
        migrated_dsn,
        "SELECT ship_to::text, total, items::text FROM orders WHERE id=%s",
        internal_id,
    )
    assert "Synthetic Recipient" in rows[0]["ship_to"]
    assert "Synthetic product" in rows[0]["items"]

    async with tx(pool, str(client)) as conn:
        fact = await order_status_by_number(conn, client, ORDER_NUMBER)
    assert fact == OrderStatusFact(
        status_text="shipped",
        tracking_no="SYNTH-TRACK-1",
        carrier="Synthetic Courier",
        shipped_or_delivered_on=datetime.fromtimestamp(1_762_003_600, UTC).date(),
    )
    assert fact is not None
    assert set(OrderStatusFact.model_fields) == {
        "status_text",
        "tracking_no",
        "carrier",
        "shipped_or_delivered_on",
    }

    result = await ask(
        agent(pool), make_ctx(pool, client), action="order_status", order_number=ORDER_NUMBER
    )
    assert result.ok
    assert "shipped" in result.facts[0].text and "SYNTH-TRACK-1" in result.facts[0].text
    dumped = [fact.model_dump_json(), result.model_dump_json()]
    for text in dumped:
        for private in (*PRIVATE_STRINGS, internal_id, client.hex, str(client)):
            assert private not in text, private


@pytest.mark.parametrize(
    ("provider_status", "words", "dated"),
    [
        ("UNPAID", "waiting for payment", False),
        ("AWAITING_SHIPMENT", "being prepared", False),
        ("IN_TRANSIT", "shipped", True),
        ("DELIVERED", "delivered", True),
        ("CANCELLED", "cancelled", False),
        ("SOMETHING_NEW", "being prepared", False),
    ],
)
async def test_status_words_and_day_only_for_shipped_or_delivered(
    pool: AsyncConnectionPool,
    migrated_dsn: str,
    provider_status: str,
    words: str,
    dated: bool,
) -> None:
    client, store, shop = await plain_store(migrated_dsn)
    changes: dict[str, Any] = {"status": provider_status}
    if provider_status == "UNPAID":
        changes["paid_time"] = None
    await seed_order(pool, client, store, shop, ORDER_NUMBER, **changes)
    async with tx(pool, str(client)) as conn:
        fact = await order_status_by_number(conn, client, ORDER_NUMBER)
    assert fact is not None
    assert fact.status_text == words
    assert (fact.shipped_or_delivered_on is not None) is dated


async def test_unknown_order_number_reply(pool: AsyncConnectionPool, migrated_dsn: str) -> None:
    client, store, shop = await plain_store(migrated_dsn)
    internal_id = await seed_order(pool, client, store, shop, ORDER_NUMBER)
    reader = agent(pool)
    for number in ("576461413038785753", internal_id[:100].replace("_", "-"), "SYNTH-PKG-1"):
        result = await ask(
            reader, make_ctx(pool, client), action="order_status", order_number=number
        )
        assert [(f.id, f.text) for f in result.facts] == [
            (
                "tiktok_order_not_found",
                "I couldn't find that TikTok order number. Please check it, or ask our staff.",
            )
        ]
    # Our own order id is not an order number either.
    async with tx(pool, str(client)) as conn:
        assert await order_status_by_number(conn, client, internal_id) is None


async def test_tenant_a_cannot_see_tenant_b_order_by_number(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    a_client, _, _ = await plain_store(migrated_dsn)
    b_client, b_store, b_shop = await plain_store(migrated_dsn)
    await seed_order(pool, b_client, b_store, b_shop, ORDER_NUMBER)
    result = await ask(
        agent(pool), make_ctx(pool, a_client), action="order_status", order_number=ORDER_NUMBER
    )
    assert result.facts[0].id == "tiktok_order_not_found"
    async with tx(pool, str(a_client)) as conn:
        # Even naming B's tenant id explicitly, RLS keeps A's connection out.
        assert await order_status_by_number(conn, b_client, ORDER_NUMBER) is None
    async with tx(pool, str(b_client)) as conn:
        assert await order_status_by_number(conn, b_client, ORDER_NUMBER) is not None


async def test_a_local_order_with_the_same_external_id_is_not_a_tiktok_order(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    client, _, _ = await plain_store(migrated_dsn)
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        await conn.execute(
            "INSERT INTO orders(id, client_id, channel, external_order_id, status, total) "
            "VALUES (%s, %s, 'telegram', %s, 'processing', 1)",
            (f"LOC{uuid4().hex[:7]}", client, ORDER_NUMBER),
        )
    async with tx(pool, str(client)) as conn:
        assert await order_status_by_number(conn, client, ORDER_NUMBER) is None


async def test_disconnected_store_orders_still_answer(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    client, store, shop = await plain_store(migrated_dsn)
    await seed_order(pool, client, store, shop, ORDER_NUMBER, status="DELIVERED")
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        await conn.execute(
            "UPDATE channel_credentials SET disconnected_at = now() WHERE id = %s", (store,)
        )
    result = await ask(
        agent(pool), make_ctx(pool, client), action="order_status", order_number=ORDER_NUMBER
    )
    assert result.facts[0].id == "tiktok_order_status"
    assert "delivered" in result.facts[0].text


async def test_eleventh_lookup_in_an_hour_is_refused(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    """The count is durable (agent_runs), so it holds across worker processes."""
    client, store, shop = await plain_store(migrated_dsn)
    await seed_order(pool, client, store, shop, ORDER_NUMBER)
    conversation = uuid4()

    def runner() -> SpokeRunner:  # a fresh runner per call: nothing is kept in memory
        return SpokeRunner({"ecommerce_agent": agent(pool)}, recorder=PostgresRunRecorder(pool))

    async def lookup(number: str, conv: UUID = conversation) -> str:
        task = Task(
            task_id="t",
            agent="ecommerce_agent",
            input={"action": "order_status", "order_number": number},
        )
        result = await runner().execute(task, make_ctx(pool, client, conv))
        assert result.ok
        return result.facts[0].id

    with capture_logs() as logs:
        answers = [
            await lookup(ORDER_NUMBER if index % 2 else f"99999999999999{index}")
            for index in range(10)
        ]
        assert set(answers) == {"tiktok_order_status", "tiktok_order_not_found"}
        assert await lookup(ORDER_NUMBER) == "tiktok_order_lookup_limit"
        assert await lookup("123") == "tiktok_order_lookup_limit"
        # Another conversation has its own allowance.
        assert await lookup(ORDER_NUMBER, uuid4()) == "tiktok_order_status"
    # The oldest lookup leaves the hour: 9 lookups and 2 refusals remain, and refusals do not
    # count, so one more lookup is allowed; after it the limit holds again.
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        await conn.execute(
            "UPDATE agent_runs SET created_at = created_at - interval '61 minutes' "
            "WHERE id = (SELECT id FROM agent_runs WHERE conversation_id = %s "
            "AND output->'fact_ids' ?| array['tiktok_order_status','tiktok_order_not_found'] "
            "ORDER BY id LIMIT 1)",
            (conversation,),
        )
    assert await lookup(ORDER_NUMBER) == "tiktok_order_status"
    assert await lookup(ORDER_NUMBER) == "tiktok_order_lookup_limit"
    # An hour after the rest, the whole allowance is back.
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        await conn.execute(
            "UPDATE agent_runs SET created_at = created_at - interval '61 minutes' "
            "WHERE conversation_id = %s AND created_at > now() - interval '1 hour'",
            (conversation,),
        )
    assert await lookup(ORDER_NUMBER) == "tiktok_order_status"
    # Order numbers never reach the logs.
    assert ORDER_NUMBER not in repr(logs)
    assert "SYNTH-TRACK-1" not in repr(logs)


# -- TikTok stock: the reader's lookup, worded by availability_facts --------------------------


async def test_tiktok_only_item_checked_live_and_copy_updated(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = await store_for(migrated_dsn, pool)
    client, store = binding.client_id, binding.record_id
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    old = product_payload("SYNTH-P-LIVE")
    old["skus"][0] = sku_payload("SYNTH-SKU-1", quantities=(1, 2))
    old["update_time"] = 1_793_750_000
    await seed_listings(pool, client, store, old, synced_at=now() - timedelta(hours=2))
    fresh = product_payload("SYNTH-P-LIVE")
    fresh["skus"][0] = sku_payload("SYNTH-SKU-1", quantities=(5, 3))
    tiktok.product_details["SYNTH-P-LIVE"] = fresh

    live = LiveReadAccess(load_app=any_app_loader(pool), decrypt=decrypt)
    before = now()
    with capture_logs() as logs:
        result = await stock_answer(pool, live, make_ctx(pool, client), "mug-blk-m")
    (fact,) = result.facts
    assert fact.id == "tiktok_stock"
    assert fact.text == "Synthetic mug (Black / M): 8 available on our TikTok Shop."
    assert [kind for kind, _, _ in tiktok.requests] == ["shops", "product_detail"]
    rows = await fetch(
        migrated_dsn,
        "SELECT external_sku_id, stock_qty, synced_at FROM marketplace_listings "
        "WHERE client_id=%s ORDER BY external_sku_id",
        client,
    )
    assert [(r["external_sku_id"], r["stock_qty"]) for r in rows] == [
        ("SYNTH-SKU-1", 8),
        ("SYNTH-SKU-2", 0),
    ]
    assert all(r["synced_at"] >= before for r in rows)
    assert "mug-blk-m" not in repr(logs).lower()
    assert PRIVATE_TEXT not in repr(logs)


async def test_live_stock_gives_per_sku_counts_without_wording(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = await store_for(migrated_dsn, pool)
    client, store = binding.client_id, binding.record_id
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    payload = product_payload("SYNTH-P-WS")
    await seed_listings(pool, client, store, payload, synced_at=now() - timedelta(days=1))
    payload = product_payload("SYNTH-P-WS")
    payload["skus"].append(sku_payload("SYNTH-SKU-3", quantities=(2,), attributes=("Red", "S")))
    tiktok.product_details["SYNTH-P-WS"] = payload
    async with tx(pool, str(client)) as conn:
        (first, *_) = await find_listings(conn, client, "MUG-BLK-M")
    live = LiveReadAccess(load_app=any_app_loader(pool), decrypt=decrypt)
    stock = await live_stock(pool, client, first.id, live=live)
    assert stock is not None and stock.checked_live
    assert stock.listing_id == first.id
    assert {(s.external_sku_id, s.stock_qty) for s in stock.skus} == {
        ("SYNTH-SKU-1", 10),
        ("SYNTH-SKU-2", 0),
        ("SYNTH-SKU-3", 2),
    }
    assert all(s.listing_id is not None for s in stock.skus)  # the new SKU was stored too
    assert stock.asked() is not None and stock.asked().stock_qty == 10  # type: ignore[union-attr]
    assert "available" not in stock.model_dump_json()
    # Another tenant's listing id answers nothing.
    other, _, _ = await plain_store(migrated_dsn)
    assert await live_stock(pool, other, first.id, live=live) is None


async def test_ambiguous_variants_prompt_lists_up_to_five(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = await store_for(migrated_dsn, pool)
    client = binding.client_id
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    payload = product_payload("SYNTH-P-MANY")
    payload["skus"] = [
        sku_payload(f"SYNTH-SKU-{i}", attributes=(f"Colour {i}",), seller_sku=f"MUG-{i}")
        for i in range(1, 8)
    ]
    await seed_listings(pool, client, binding.record_id, payload, synced_at=now())
    live = LiveReadAccess(load_app=any_app_loader(pool), decrypt=decrypt)
    result = await stock_answer(pool, live, make_ctx(pool, client), "mug")
    (fact,) = result.facts
    assert fact.id == "tiktok_stock_choices"
    assert fact.text.count("Synthetic mug (Colour") == 5
    assert "5. Synthetic mug (Colour 5) (SKU MUG-5)" in fact.text
    assert "Colour 6" not in fact.text
    assert tiktok.requests == []
    # Naming the variant exactly picks it.
    tiktok.product_details["SYNTH-P-MANY"] = payload
    result = await stock_answer(pool, live, make_ctx(pool, client), "Synthetic mug Colour 6")
    assert result.facts[0].id == "tiktok_stock"
    assert "Colour 6" in result.facts[0].text


async def test_live_failure_with_fresh_copy_and_with_stale_copy(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = await store_for(migrated_dsn, pool)
    client, store = binding.client_id, binding.record_id
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    live = LiveReadAccess(load_app=any_app_loader(pool), decrypt=decrypt)
    fresh = product_payload("SYNTH-P-FRESH")
    fresh["title"] = "Fresh mug"
    fresh["skus"] = [sku_payload("SYNTH-SKU-F", quantities=(8,), seller_sku="FRESH-1")]
    stale = product_payload("SYNTH-P-STALE")
    stale["title"] = "Stale mug"
    stale["skus"] = [sku_payload("SYNTH-SKU-S", quantities=(8,), seller_sku="STALE-1")]
    await seed_listings(pool, client, store, fresh, synced_at=now() - timedelta(hours=2, minutes=1))
    await seed_listings(pool, client, store, stale, synced_at=now() - timedelta(hours=7))
    tiktok.fail[("product_detail", "SYNTH-P-FRESH")] = server_error
    tiktok.fail[("product_detail", "SYNTH-P-STALE")] = server_error

    with capture_logs() as logs:
        result = await stock_answer(pool, live, make_ctx(pool, client), "FRESH-1")
        assert result.facts[0].text == (
            "Fresh mug (Black / M) on our TikTok Shop: 8 were available as of 2 hours ago."
        )
        result = await stock_answer(pool, live, make_ctx(pool, client), "STALE-1")
        assert (
            result.facts[0].text
            == f"Stale mug (Black / M) on our TikTok Shop: {STOCK_UNCHECKED_TEXT}"
        )
    # One attempt each, never retried; the copy is untouched by a failed read.
    assert [k for k, _, _ in tiktok.requests if k == "product_detail"] == ["product_detail"] * 2
    rows = await fetch(
        migrated_dsn, "SELECT stock_qty FROM marketplace_listings WHERE client_id=%s", client
    )
    assert [r["stock_qty"] for r in rows] == [8, 8]
    assert PRIVATE_TEXT not in repr(logs)
    assert "FRESH-1" not in repr(logs)
    assert any(log["event"] == "marketplace_live_stock_unavailable" for log in logs)


async def test_no_live_read_for_a_disconnected_store_or_with_tiktok_off(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = await store_for(migrated_dsn, pool)
    client, store = binding.client_id, binding.record_id
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    await seed_listings(
        pool, client, store, product_payload("SYNTH-P-OFF"), synced_at=now() - timedelta(minutes=30)
    )
    # TikTok off here: no live access at all.
    result = await stock_answer(pool, None, make_ctx(pool, client), "MUG-BLK-M")
    assert "10 were available as of 30 minutes ago." in result.facts[0].text
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        await conn.execute(
            "UPDATE channel_credentials SET disconnected_at = now() WHERE id = %s", (store,)
        )
    live = LiveReadAccess(load_app=any_app_loader(pool), decrypt=decrypt)
    result = await stock_answer(pool, live, make_ctx(pool, client), "MUG-BLK-M")
    assert "10 were available as of 30 minutes ago." in result.facts[0].text
    assert tiktok.requests == []


async def test_find_listings_is_tenant_scoped_escaped_and_skips_removed(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    client, store, _ = await plain_store(migrated_dsn)
    other, other_store, _ = await plain_store(migrated_dsn)
    cotton = product_payload("SYNTH-P-COTTON")
    cotton["title"] = "100% cotton_tee"
    cotton["skus"] = [sku_payload("SYNTH-SKU-C", seller_sku="TEE-1")]
    removed = product_payload("SYNTH-P-GONE")
    removed["title"] = "Removed tee"
    removed["status"] = "DELETED"
    removed["skus"] = [sku_payload("SYNTH-SKU-G", seller_sku="TEE-2")]
    plain = product_payload("SYNTH-P-PLAIN")
    plain["title"] = "1000 cottonXtee"
    plain["skus"] = [sku_payload("SYNTH-SKU-P", seller_sku="TEE-3")]
    for payload in (cotton, removed, plain):
        await seed_listings(pool, client, store, payload, synced_at=now())
    elsewhere = product_payload("SYNTH-P-ELSE")
    elsewhere["title"] = "Other tenant tee"
    await seed_listings(pool, other, other_store, elsewhere, synced_at=now())

    async with tx(pool, str(client)) as conn:
        names = [m.product_name for m in await find_listings(conn, client, "tee")]
        assert set(names) == {"1000 cottonXtee", "100% cotton_tee"}
        assert [m.product_name for m in await find_listings(conn, client, "0% cotton_")] == [
            "100% cotton_tee"
        ]
        assert await find_listings(conn, client, "Removed") == []
        assert await find_listings(conn, client, "Other tenant") == []
        assert await find_listings(conn, client, "   ") == []
        (exact,) = [m for m in await find_listings(conn, client, "tee-3") if m.exact]
        assert exact.seller_sku == "TEE-3"
        assert len(await find_listings(conn, client, "Black", limit=1)) == 1


# -- fix round 1: masked order numbers, sellable statuses, fresh copy --------------------------

LUHN_ORDER = "576461413038785750"  # passes the card check, so redact() masks it as [CARD_0]


def turn_envelope(client: UUID, conversation: UUID, raw: str, redacted: str | None = None):  # type: ignore[no-untyped-def]
    from csa.core.pii import redact

    envelope = make_envelope(raw)
    return envelope.model_copy(
        update={
            "client_id": client,
            "contact": envelope.contact.model_copy(update={"id": uuid4()}),
            "conversation": envelope.conversation.model_copy(update={"id": conversation}),
            "message": envelope.message.model_copy(
                update={"body_redacted": redact(raw) if redacted is None else redacted}
            ),
        }
    )


async def test_a_card_like_order_number_reaches_the_lookup_through_the_turn(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    from csa.contracts.state import Context
    from csa.turn.deps import PostgresTurnDependencies

    client, store, shop = await plain_store(migrated_dsn)
    await seed_order(pool, client, store, shop, LUHN_ORDER, status="IN_TRANSIT")
    deps = PostgresTurnDependencies(pool)
    monkeypatch.setattr(
        deps._capabilities,  # noqa: SLF001
        "allows_execution",
        AsyncMock(return_value=True),
    )
    conversation = uuid4()
    envelope = turn_envelope(client, conversation, f"Where is my TikTok order {LUHN_ORDER}?")
    assert "[CARD_0]" in envelope.message.body_redacted
    assert LUHN_ORDER not in envelope.message.body_redacted
    task = Task(
        task_id="t0-ecommerce_agent",
        agent="ecommerce_agent",
        input={"action": "order_status", "order_number": "[CARD_0]"},
    )
    with capture_logs() as logs:
        result = await deps.execute_spoke(task, envelope, Context())
    assert result.ok, result.error
    assert result.facts[0].id == "tiktok_order_status"
    assert "shipped" in result.facts[0].text
    # The plan's task keeps the placeholder; the digits never reach the result, logs or the
    # recorded run.
    assert task.input["order_number"] == "[CARD_0]"
    assert LUHN_ORDER not in result.model_dump_json()
    assert LUHN_ORDER not in repr(logs)
    runs = await fetch(
        migrated_dsn,
        "SELECT input::text AS input, output::text AS output FROM agent_runs "
        "WHERE conversation_id=%s",
        conversation,
    )
    assert len(runs) == 1
    assert LUHN_ORDER not in runs[0]["input"] and LUHN_ORDER not in runs[0]["output"]
    assert "tiktok_order_status" in runs[0]["output"]  # it counts towards the limit

    # A placeholder that cannot be resolved from this message is never looked up.
    stale = turn_envelope(client, conversation, f"order {LUHN_ORDER}", redacted="order [CARD_0]!")
    result = await deps.execute_spoke(task, stale, Context())
    assert result.error is not None and result.error.code == "order_number_unreadable"


@pytest.mark.parametrize(
    ("status", "offered"),
    [
        ("ACTIVATE", True),
        ("DRAFT", False),
        ("SELLER_DEACTIVATED", False),
        ("PLATFORM_DEACTIVATED", False),
        ("FREEZE", False),
        ("DELETED", False),
        ("SOMETHING_NEW", False),
    ],
)
async def test_only_selling_listings_are_offered(
    pool: AsyncConnectionPool, migrated_dsn: str, status: str, offered: bool
) -> None:
    client, store, _ = await plain_store(migrated_dsn)
    payload = product_payload(f"SYNTH-P-{status}")
    payload["status"] = status
    await seed_listings(pool, client, store, payload, synced_at=now() - timedelta(minutes=30))
    async with tx(pool, str(client)) as conn:
        assert bool(await find_listings(conn, client, "MUG-BLK-M")) is offered
    result = await stock_answer(pool, None, make_ctx(pool, client), "MUG-BLK-M")
    (fact,) = result.facts
    if offered:
        assert fact.id == "tiktok_stock"
        assert "10 were available as of 30 minutes ago." in fact.text
    else:
        assert fact.id == "tiktok_stock_not_found"
        assert "available" not in fact.text


async def test_a_live_product_that_stopped_selling_is_not_offered(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = await store_for(migrated_dsn, pool)
    client, store = binding.client_id, binding.record_id
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    old = product_payload("SYNTH-P-STOP")
    old["update_time"] = 1_793_750_000
    await seed_listings(pool, client, store, old, synced_at=now() - timedelta(hours=2))
    stopped = product_payload("SYNTH-P-STOP")
    stopped["status"] = "SELLER_DEACTIVATED"
    tiktok.product_details["SYNTH-P-STOP"] = stopped
    live = LiveReadAccess(load_app=any_app_loader(pool), decrypt=decrypt)
    async with tx(pool, str(client)) as conn:
        (match,) = await find_listings(conn, client, "MUG-BLK-M")
    stock = await live_stock(pool, client, match.id, live=live)
    assert stock is not None and stock.checked_live and not stock.sellable
    result = await stock_answer(pool, live, make_ctx(pool, client), "MUG-BLK-M")
    assert result.facts[0].id == "tiktok_stock_not_found"
    rows = await fetch(
        migrated_dsn,
        "SELECT DISTINCT product_status FROM marketplace_listings WHERE client_id=%s",
        client,
    )
    assert [r["product_status"] for r in rows] == ["SELLER_DEACTIVATED"]


async def test_a_copy_under_a_minute_old_is_not_read_live(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = await store_for(migrated_dsn, pool)
    client, store = binding.client_id, binding.record_id
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    await seed_listings(
        pool, client, store, product_payload("SYNTH-P-NEW"), synced_at=now() - timedelta(seconds=10)
    )
    live = LiveReadAccess(load_app=any_app_loader(pool), decrypt=decrypt)
    result = await stock_answer(pool, live, make_ctx(pool, client), "MUG-BLK-M")
    assert result.facts[0].text == (
        "Synthetic mug (Black / M) on our TikTok Shop: "
        "10 were available as of less than a minute ago."
    )
    assert tiktok.requests == []
