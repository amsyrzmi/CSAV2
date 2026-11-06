"""Idempotent order and listing upserts against a disposable migrated Postgres; synthetic data."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from csa.agents.ecommerce import TikTokOrderSnapshot, TikTokProductSnapshot
from csa.core import db
from csa.core.tenancy import tx
from csa.marketplace.mapping import map_listings, map_order
from csa.marketplace.store import upsert_listings, upsert_order
from psycopg import errors
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from tests.conftest import owner_dsn_for, requires_docker
from tests.test_marketplace_mapping import product as product_snapshot
from tests.test_tiktok_snapshots import extended_order

pytestmark = [requires_docker, pytest.mark.asyncio, pytest.mark.usefixtures("migrated_dsn")]

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
FIRST_UPDATE = datetime.fromtimestamp(1_762_003_600, UTC)


@pytest_asyncio.fixture
async def pool(app_dsn: str) -> AsyncIterator[AsyncConnectionPool]:
    pool = db.build_pool(app_dsn, max_size=3)
    await pool.open(wait=True)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
def owner(pg_container: object) -> Any:
    with psycopg.connect(
        owner_dsn_for(pg_container), autocommit=True, row_factory=cast(Any, dict_row)
    ) as conn:
        yield conn


def make_store(owner: psycopg.Connection[Any]) -> tuple[UUID, UUID, str]:
    """A tenant with one TikTok store; the shop id is unique per call (migration 0491)."""
    client, store = uuid4(), uuid4()
    shop = f"SYNTH-SHOP-{store}"
    owner.execute(
        "INSERT INTO clients(id,slug,name,platform_version) VALUES (%s,%s,'Synthetic','t')",
        (client, f"mkt-store-{client}"),
    )
    owner.execute(
        "INSERT INTO channel_credentials(id,client_id,channel,shop_id) "
        "VALUES (%s,%s,'tiktok_shop',%s)",
        (store, client, shop),
    )
    return client, store, shop


def order_for(
    client: UUID,
    store: UUID,
    shop: str,
    order_id: str,
    *,
    update_time: datetime | None = FIRST_UPDATE,
    **changes: Any,
):  # type: ignore[no-untyped-def]
    payload = extended_order(order_id)
    payload["update_time"] = None if update_time is None else int(update_time.timestamp())
    payload.update(changes)
    return map_order(client, store, shop, TikTokOrderSnapshot.model_validate(payload), NOW)


def read_order(owner: psycopg.Connection[Any], order_id: str) -> dict[str, Any]:
    # Order ids in these tests are unique per run, so the provider id finds exactly one row.
    row = owner.execute("SELECT * FROM orders WHERE external_order_id = %s", (order_id,)).fetchone()
    assert row is not None
    return row


def read_items(owner: psycopg.Connection[Any], order_id: str) -> list[dict[str, Any]]:
    return owner.execute(
        "SELECT sku, name_snapshot, unit_price, qty, line_total, currency, external_line_id, "
        "product_id FROM order_items WHERE order_id = "
        "(SELECT id FROM orders WHERE external_order_id = %s) ORDER BY sku",
        (order_id,),
    ).fetchall()


async def test_order_lands_in_the_local_tables(
    pool: AsyncConnectionPool, owner: psycopg.Connection[Any]
) -> None:
    client, store, shop = make_store(owner)
    order_id = f"SYNTH-{uuid4()}"
    order = order_for(client, store, shop, order_id)
    async with tx(pool, str(client)) as conn:
        assert await upsert_order(conn, order) == "inserted"

    row = read_order(owner, order_id)
    assert row["client_id"] == client
    assert row["store_id"] == store
    assert (row["channel"], row["external_order_id"], row["external_shop_id"]) == (
        "tiktok_shop",
        order_id,
        shop,
    )
    assert (row["status"], row["payment_status"], row["provider_status"]) == (
        "processing",
        "paid",
        "AWAITING_SHIPMENT",
    )
    assert row["contact_id"] is None
    assert row["total"] == Decimal("12.34")
    # TikTok's own subtotal survives the order_items trigger, which would say 12.34.
    assert row["subtotal"] == Decimal("10.00")
    assert (row["shipping_fee"], row["discount"]) == (Decimal("3.00"), Decimal("0.66"))
    assert (row["tracking_no"], row["carrier"]) == ("SYNTH-TRACK-1", "Synthetic Courier")
    assert row["provider_updated_at"] == FIRST_UPDATE
    assert row["synced_at"] == NOW
    assert row["buyer_cleared_at"] is None
    assert row["ship_to"] == {
        "recipient_name": "Synthetic Recipient",
        "phone": "+60000000000",
        "address_text": "1 Synthetic Lane, Synthetic Town",
        "region": "MY",
    }
    assert [
        (i["sku"], i["qty"], i["unit_price"], i["line_total"]) for i in read_items(owner, order_id)
    ] == [("SYNTHETIC-SKU", 2, Decimal("6.17"), Decimal("12.34"))]
    assert read_items(owner, order_id)[0]["product_id"] is None
    # Buyer details sit only in ship_to.
    others = {k: v for k, v in row.items() if k != "ship_to"}
    assert "Synthetic Recipient" not in repr(others)
    assert "+60000000000" not in repr(others)


async def test_upsert_is_idempotent_and_newer_wins(
    pool: AsyncConnectionPool, owner: psycopg.Connection[Any]
) -> None:
    client, store, shop = make_store(owner)
    order_id = f"SYNTH-{uuid4()}"
    async with tx(pool, str(client)) as conn:
        assert await upsert_order(conn, order_for(client, store, shop, order_id)) == "inserted"
    # The same snapshot again is accepted and changes nothing visible.
    async with tx(pool, str(client)) as conn:
        assert await upsert_order(conn, order_for(client, store, shop, order_id)) == "updated"
    count = owner.execute(
        "SELECT count(*) AS n FROM orders WHERE external_order_id = %s", (order_id,)
    ).fetchone()
    assert count is not None and count["n"] == 1
    assert len(read_items(owner, order_id)) == 1

    # A newer snapshot wins, including a changed line set.
    newer = order_for(
        client,
        store,
        shop,
        order_id,
        update_time=FIRST_UPDATE + timedelta(hours=1),
        status="DELIVERED",
        line_items=[
            {"id": "L1", "sku_id": "S-A", "seller_sku": "", "product_name": "A", "sale_price": "4"}
        ],
    )
    async with tx(pool, str(client)) as conn:
        assert await upsert_order(conn, newer) == "updated"
    row = read_order(owner, order_id)
    assert (row["status"], row["provider_status"]) == ("delivered", "DELIVERED")
    assert row["provider_updated_at"] == FIRST_UPDATE + timedelta(hours=1)
    assert [(i["sku"], i["qty"]) for i in read_items(owner, order_id)] == [("S-A", 1)]
    assert row["items"] == [{"sku": "S-A", "name": "A", "qty": 1, "price": 4.0}]

    # An older snapshot is stale: nothing changes, lines included.
    before = read_order(owner, order_id)
    older = order_for(
        client,
        store,
        shop,
        order_id,
        update_time=FIRST_UPDATE - timedelta(hours=1),
        status="UNPAID",
    )
    async with tx(pool, str(client)) as conn:
        assert await upsert_order(conn, older) == "stale"
    assert read_order(owner, order_id) == before
    assert [(i["sku"], i["qty"]) for i in read_items(owner, order_id)] == [("S-A", 1)]


async def test_snapshot_without_a_time_writes_and_keeps_the_stored_time(
    pool: AsyncConnectionPool, owner: psycopg.Connection[Any]
) -> None:
    client, store, shop = make_store(owner)
    order_id = f"SYNTH-{uuid4()}"
    async with tx(pool, str(client)) as conn:
        await upsert_order(conn, order_for(client, store, shop, order_id))
        untimed = order_for(client, store, shop, order_id, update_time=None, status="DELIVERED")
        assert await upsert_order(conn, untimed) == "updated"
    row = read_order(owner, order_id)
    assert row["status"] == "delivered"
    assert row["provider_updated_at"] == FIRST_UPDATE


async def test_zero_line_order_stores_and_clears_old_lines(
    pool: AsyncConnectionPool, owner: psycopg.Connection[Any]
) -> None:
    client, store, shop = make_store(owner)
    order_id = f"SYNTH-{uuid4()}"
    async with tx(pool, str(client)) as conn:
        await upsert_order(conn, order_for(client, store, shop, order_id))
        empty = order_for(
            client,
            store,
            shop,
            order_id,
            update_time=FIRST_UPDATE + timedelta(minutes=1),
            line_items=[],
        )
        assert await upsert_order(conn, empty) == "updated"
    assert read_items(owner, order_id) == []
    assert read_order(owner, order_id)["subtotal"] == Decimal("10.00")


async def test_cleared_order_never_gets_buyer_data_back(
    pool: AsyncConnectionPool, owner: psycopg.Connection[Any]
) -> None:
    client, store, shop = make_store(owner)
    order_id = f"SYNTH-{uuid4()}"
    async with tx(pool, str(client)) as conn:
        await upsert_order(conn, order_for(client, store, shop, order_id))
    cleared_at = NOW + timedelta(days=100)
    owner.execute(
        "UPDATE orders SET ship_to = NULL, buyer_cleared_at = %s WHERE external_order_id = %s",
        (cleared_at, order_id),
    )
    # Equal-time and newer syncs both carry a full address; neither restores it.
    for offset, status in ((0, "AWAITING_SHIPMENT"), (1, "DELIVERED"), (2, "COMPLETED")):
        refreshed = order_for(
            client,
            store,
            shop,
            order_id,
            update_time=FIRST_UPDATE + timedelta(hours=offset),
            status=status,
        )
        assert refreshed.ship_to is not None
        async with tx(pool, str(client)) as conn:
            assert await upsert_order(conn, refreshed) == "updated"
        row = read_order(owner, order_id)
        assert row["ship_to"] is None
        assert row["buyer_cleared_at"] == cleared_at
    assert row["status"] == "delivered"
    assert row["provider_status"] == "COMPLETED"


async def test_masked_ship_to_is_stored_as_given(
    pool: AsyncConnectionPool, owner: psycopg.Connection[Any]
) -> None:
    client, store, shop = make_store(owner)
    order_id = f"SYNTH-{uuid4()}"
    masked = order_for(
        client,
        store,
        shop,
        order_id,
        recipient_address={"name": "J***", "full_address": "****", "region_code": "MY"},
    )
    async with tx(pool, str(client)) as conn:
        await upsert_order(conn, masked)
    assert read_order(owner, order_id)["ship_to"] == {
        "recipient_name": "J***",
        "address_text": "****",
        "region": "MY",
    }


async def test_order_without_address_stores_null_ship_to(
    pool: AsyncConnectionPool, owner: psycopg.Connection[Any]
) -> None:
    client, store, shop = make_store(owner)
    order_id = f"SYNTH-{uuid4()}"
    async with tx(pool, str(client)) as conn:
        await upsert_order(conn, order_for(client, store, shop, order_id, recipient_address=None))
    assert read_order(owner, order_id)["ship_to"] is None


async def test_tenant_scope_is_enforced_on_writes(
    pool: AsyncConnectionPool, owner: psycopg.Connection[Any]
) -> None:
    client, store, shop = make_store(owner)
    other, _, _ = make_store(owner)
    order = order_for(client, store, shop, f"SYNTH-{uuid4()}")
    with pytest.raises(errors.InsufficientPrivilege):
        async with tx(pool, str(other)) as conn:
            await upsert_order(conn, order)


def local_product(owner: psycopg.Connection[Any], client: UUID, sku: str) -> UUID:
    product = uuid4()
    owner.execute(
        "INSERT INTO products(id,client_id,sku,name,price,stock_qty,reserved_qty) "
        "VALUES (%s,%s,%s,'Local product',9.90,11,2)",
        (product, client, sku),
    )
    return product


async def test_listing_upsert_never_changes_local_products(
    pool: AsyncConnectionPool, owner: psycopg.Connection[Any]
) -> None:
    client, store, _ = make_store(owner)
    # A local product with the same seller SKU as the listing: still never touched.
    local_product(owner, client, "SELL-1")
    local_product(owner, client, "OTHER")
    products_before = owner.execute(
        "SELECT * FROM products WHERE client_id = %s ORDER BY sku", (client,)
    ).fetchall()
    order_items_before = owner.execute("SELECT count(*) AS n FROM order_items").fetchone()

    rows = map_listings(client, store, product_snapshot(), NOW)
    async with tx(pool, str(client)) as conn:
        assert await upsert_listings(conn, rows) == 2
    async with tx(pool, str(client)) as conn:
        assert await upsert_listings(conn, rows) == 2

    products_after = owner.execute(
        "SELECT * FROM products WHERE client_id = %s ORDER BY sku", (client,)
    ).fetchall()
    assert products_after == products_before
    assert owner.execute("SELECT count(*) AS n FROM order_items").fetchone() == (order_items_before)
    stored = owner.execute(
        "SELECT external_sku_id, variant_name, seller_sku, price, stock_qty, image_url, channel "
        "FROM marketplace_listings WHERE client_id = %s ORDER BY external_sku_id",
        (client,),
    ).fetchall()
    assert stored == [
        {
            "external_sku_id": "SKU-1",
            "variant_name": "Black / M",
            "seller_sku": "SELL-1",
            "price": Decimal("19.90"),
            "stock_qty": 7,
            "image_url": "https://img.example.test/a.jpg",
            "channel": "tiktok_shop",
        },
        {
            "external_sku_id": "SKU-2",
            "variant_name": "",
            "seller_sku": None,
            "price": Decimal("21.00"),
            "stock_qty": 0,
            "image_url": "https://img.example.test/a.jpg",
            "channel": "tiktok_shop",
        },
    ]


async def test_listing_newer_wins_and_untimed_product_refreshes(
    pool: AsyncConnectionPool, owner: psycopg.Connection[Any]
) -> None:
    client, store, _ = make_store(owner)
    first = map_listings(client, store, product_snapshot(), NOW)
    older = map_listings(client, store, product_snapshot(update_time=1_762_000_000), NOW)
    newer = map_listings(client, store, product_snapshot(update_time=1_762_007_200), NOW)
    untimed = map_listings(client, store, product_snapshot(update_time=None), NOW)
    async with tx(pool, str(client)) as conn:
        assert await upsert_listings(conn, first) == 2
        assert await upsert_listings(conn, older) == 0  # stale
        assert await upsert_listings(conn, newer) == 2
        assert await upsert_listings(conn, untimed) == 2  # cannot be proven older
    stored = owner.execute(
        "SELECT provider_updated_at FROM marketplace_listings WHERE client_id = %s",
        (client,),
    ).fetchall()
    assert {r["provider_updated_at"] for r in stored} == {
        datetime.fromtimestamp(1_762_007_200, UTC)
    }


async def test_product_without_skus_writes_no_rows(
    pool: AsyncConnectionPool, owner: psycopg.Connection[Any]
) -> None:
    client, store, _ = make_store(owner)
    rows = map_listings(
        client,
        store,
        TikTokProductSnapshot.model_validate(
            {"id": "SYNTH-EMPTY", "title": "No variants", "status": "DRAFT", "skus": []}
        ),
        NOW,
    )
    assert rows == []
    async with tx(pool, str(client)) as conn:
        assert await upsert_listings(conn, rows) == 0
    count = owner.execute(
        "SELECT count(*) AS n FROM marketplace_listings WHERE client_id = %s", (client,)
    ).fetchone()
    assert count is not None and count["n"] == 0


async def test_non_https_image_is_stored_as_null(
    pool: AsyncConnectionPool, owner: psycopg.Connection[Any]
) -> None:
    client, store, _ = make_store(owner)
    rows = map_listings(
        client, store, product_snapshot(main_images=[{"urls": ["javascript:alert(1)"]}]), NOW
    )
    async with tx(pool, str(client)) as conn:
        await upsert_listings(conn, rows)
    images = owner.execute(
        "SELECT image_url FROM marketplace_listings WHERE client_id = %s", (client,)
    ).fetchall()
    assert [r["image_url"] for r in images] == [None, None]


async def test_two_tenants_can_sync_the_same_provider_order_id_in_isolation(
    pool: AsyncConnectionPool, owner: psycopg.Connection[Any]
) -> None:
    # A shop that moved tenants: the old tenant keeps its orders, the new one syncs the same ids.
    first, first_store, first_shop = make_store(owner)
    second, second_store, second_shop = make_store(owner)
    order_id = f"SYNTH-{uuid4()}"
    async with tx(pool, str(first)) as conn:
        assert (
            await upsert_order(
                conn, order_for(first, first_store, first_shop, order_id, status="UNPAID")
            )
            == "inserted"
        )
    async with tx(pool, str(second)) as conn:
        assert (
            await upsert_order(
                conn, order_for(second, second_store, second_shop, order_id, status="DELIVERED")
            )
            == "inserted"
        )
    # And again: no UniqueViolation on the second pass either.
    async with tx(pool, str(second)) as conn:
        assert (
            await upsert_order(
                conn, order_for(second, second_store, second_shop, order_id, status="DELIVERED")
            )
            == "updated"
        )
    rows = owner.execute(
        "SELECT client_id, id, provider_status FROM orders WHERE external_order_id = %s",
        (order_id,),
    ).fetchall()
    assert {(r["client_id"], r["provider_status"]) for r in rows} == {
        (first, "UNPAID"),
        (second, "DELIVERED"),
    }
    assert len({r["id"] for r in rows}) == 2
    # Each tenant sees only its own row.
    for client, expected in ((first, "UNPAID"), (second, "DELIVERED")):
        async with tx(pool, str(client)) as conn:
            cur = await conn.execute(
                "SELECT provider_status FROM orders WHERE external_order_id = %s", (order_id,)
            )
            assert [r["provider_status"] for r in await cur.fetchall()] == [expected]


async def test_subtotal_is_deterministic_on_insert_update_and_dropped_lines(
    pool: AsyncConnectionPool, owner: psycopg.Connection[Any]
) -> None:
    client, store, shop = make_store(owner)
    no_subtotal: dict[str, Any] = {"payment": {"total_amount": "20", "currency": "MYR"}}
    one_line = [
        {"id": "L1", "sku_id": "A", "seller_sku": "", "product_name": "A", "sale_price": "4"}
    ]
    # No subtotal from TikTok, with lines: the sum of the line totals (2 x 6.17).
    with_lines = f"SYNTH-{uuid4()}"
    async with tx(pool, str(client)) as conn:
        await upsert_order(conn, order_for(client, store, shop, with_lines, **no_subtotal))
    assert read_order(owner, with_lines)["subtotal"] == Decimal("12.34")
    # No subtotal, no lines: zero, not NULL.
    empty = f"SYNTH-{uuid4()}"
    async with tx(pool, str(client)) as conn:
        await upsert_order(
            conn, order_for(client, store, shop, empty, line_items=[], **no_subtotal)
        )
    assert read_order(owner, empty)["subtotal"] == Decimal("0.00")
    # An update that drops every line keeps a deterministic subtotal (TikTok's, when given).
    dropped = f"SYNTH-{uuid4()}"
    later = FIRST_UPDATE + timedelta(hours=1)
    async with tx(pool, str(client)) as conn:
        await upsert_order(conn, order_for(client, store, shop, dropped, line_items=one_line))
    assert read_order(owner, dropped)["subtotal"] == Decimal("10.00")
    async with tx(pool, str(client)) as conn:
        await upsert_order(
            conn, order_for(client, store, shop, dropped, update_time=later, line_items=[])
        )
    assert read_order(owner, dropped)["subtotal"] == Decimal("10.00")
    # ... and without TikTok's figure it becomes zero.
    bare = f"SYNTH-{uuid4()}"
    async with tx(pool, str(client)) as conn:
        await upsert_order(
            conn, order_for(client, store, shop, bare, line_items=one_line, **no_subtotal)
        )
    assert read_order(owner, bare)["subtotal"] == Decimal("4.00")
    async with tx(pool, str(client)) as conn:
        await upsert_order(
            conn,
            order_for(client, store, shop, bare, update_time=later, line_items=[], **no_subtotal),
        )
    assert read_order(owner, bare)["subtotal"] == Decimal("0.00")
