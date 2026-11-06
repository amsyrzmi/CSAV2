"""Staff stock reads in the workspace assistant against a disposable Postgres (spec 6.8).

Synthetic data only. TikTok is ``FakeTikTok`` on the processor's own read transport
(``httpx.MockTransport``), so the read really goes through the fenced reader of Task 19. Each
test makes its own tenant with its own synthetic shop id.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from csa.marketplace import processor as processor_module
from csa.marketplace.reads import LiveReadAccess, live_stock
from csa.workspace.contracts import (
    AnswerResult,
    ProductStockReadProposal,
    ProductStockResult,
    RecordRef,
)
from csa.workspace.service import _TEXT
from csa.workspace.stock import product_summary, stock_read
from psycopg_pool import AsyncConnectionPool
from structlog.testing import capture_logs

from tests.conftest import requires_docker
from tests.test_ecommerce_storage import decrypt
from tests.test_marketplace_processor import (
    PRIVATE_TEXT,
    FakeTikTok,
    any_app_loader,
    fetch,
    server_error,
    store_for,
)
from tests.test_marketplace_reads import plain_store, seed_listings
from tests.test_tiktok_product_reads import product_payload, sku_payload

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_ecommerce_grants"]

BY_SKU = ProductStockReadProposal(action="product_stock_read", query="mug-blk-m")


def now() -> datetime:
    return datetime.now(UTC)


def watch_methods(tiktok: FakeTikTok, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every HTTP method sent to the fake TikTok, on top of its own request log."""
    methods: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return await tiktok.handler(request)

    monkeypatch.setattr(
        processor_module, "_new_read_transport", lambda: httpx.MockTransport(handler)
    )
    return methods


async def test_stock_read_reads_live_through_the_fenced_reader_and_never_writes_to_tiktok(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = await store_for(migrated_dsn, pool)
    client, store = binding.client_id, binding.record_id
    tiktok = FakeTikTok(binding.shop_id)
    methods = watch_methods(tiktok, monkeypatch)
    await seed_listings(
        pool, client, store, product_payload("SYNTH-P-WS"), synced_at=now() - timedelta(hours=2)
    )
    fresh = product_payload("SYNTH-P-WS")
    fresh["skus"][0] = sku_payload("SYNTH-SKU-1", quantities=(5, 3))
    tiktok.product_details["SYNTH-P-WS"] = fresh
    live = LiveReadAccess(load_app=any_app_loader(pool), decrypt=decrypt)

    with capture_logs() as logs:
        result = await stock_read(pool, client, BY_SKU, None, live=live, text=_TEXT["en"])
    assert isinstance(result, ProductStockResult)
    assert result.checked_live and result.on_sale
    assert result.note == "Checked live just now."
    assert result.product == "Synthetic mug"
    assert [(v.variant, v.seller_sku, v.stock) for v in result.variants] == [
        ("Black / M", "MUG-BLK-M", 8),
        ("White / L", "MUG-WHT-L", 0),
    ]
    assert result.total == 8
    # Reads only: the authorized shops, then the product. Never a write, never a retry.
    assert [kind for kind, _, _ in tiktok.requests] == ["shops", "product_detail"]
    assert set(methods) == {"GET"}
    assert "mug-blk-m" not in repr(logs).lower() and binding.shop_id not in repr(logs)
    for private in ("SYNTH-P-WS", "SYNTH-SKU-1", binding.shop_id, PRIVATE_TEXT):
        assert private not in result.model_dump_json()

    # The live read fails: the copy (refreshed just now by the read above, so set back),
    # with the staff wording.
    await fetch(
        migrated_dsn,
        "UPDATE marketplace_listings SET synced_at=now()-interval '3 hours' "
        "WHERE client_id=%s RETURNING id",
        client,
    )
    tiktok.fail[("product_detail", "SYNTH-P-WS")] = server_error
    failed = await stock_read(pool, client, BY_SKU, None, live=live, text=_TEXT["ms"])
    assert isinstance(failed, ProductStockResult) and not failed.checked_live
    assert failed.note == "Setakat 3 jam lalu (semakan langsung gagal)."
    assert [(v.variant, v.stock) for v in failed.variants] == [("Black / M", 8), ("White / L", 0)]
    assert [kind for kind, _, _ in tiktok.requests][2:] == ["shops", "product_detail"]
    assert set(methods) == {"GET"}


async def test_stock_read_is_tenant_scoped_and_needs_a_connected_shop(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = await store_for(migrated_dsn, pool)
    client, store = binding.client_id, binding.record_id
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    await seed_listings(pool, client, store, product_payload("SYNTH-P-OWN"), synced_at=now())
    rows = await fetch(
        migrated_dsn, "SELECT id FROM marketplace_listings WHERE client_id=%s", client
    )
    listing = rows[0]["id"]
    live = LiveReadAccess(load_app=any_app_loader(pool), decrypt=decrypt)
    # A copy under a minute old is answered without a live read.
    own = await stock_read(
        pool,
        client,
        ProductStockReadProposal(action="product_stock_read"),
        RecordRef(kind="product", id=str(listing)),
        live=live,
        text=_TEXT["en"],
    )
    assert isinstance(own, ProductStockResult) and not own.checked_live
    assert own.note == "Synced from TikTok just now."
    assert tiktok.requests == []

    other, _, _ = await plain_store(migrated_dsn)  # a store row with no grant: not connected
    for proposal, record in (
        (ProductStockReadProposal(action="product_stock_read", listing_id=listing), None),
        (
            ProductStockReadProposal(action="product_stock_read"),
            RecordRef(kind="product", id=str(listing)),
        ),
        (ProductStockReadProposal(action="product_stock_read", query="mug"), None),
    ):
        refused = await stock_read(pool, other, proposal, record, live=live, text=_TEXT["en"])
        assert refused == AnswerResult(action="answer", markdown=_TEXT["en"]["stock_no_store"])
    assert tiktok.requests == []


async def _off_sale_product(
    pool: AsyncConnectionPool, client: UUID, store: UUID, *, age: timedelta
) -> str:
    """A deactivated TikTok product with two SKUs in our copy, synced ``age`` ago."""
    payload = product_payload("SYNTH-P-OFF")
    payload["status"] = "SELLER_DEACTIVATED"
    payload["skus"] = [
        sku_payload("SYNTH-SKU-OFF-1", quantities=(4, 3), seller_sku="OFF-1"),
        sku_payload(
            "SYNTH-SKU-OFF-2", quantities=(5,), attributes=("White", "L"), seller_sku="OFF-2"
        ),
    ]
    await seed_listings(pool, client, store, payload, synced_at=now() - age)
    return "SYNTH-P-OFF"


@pytest.mark.parametrize("trigger", ["tiktok_off", "fresh_copy", "live_failure"])
async def test_off_sale_product_from_the_copy_shows_every_sku_and_not_on_sale(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch, trigger: str
) -> None:
    """Staff get exact counts of every SKU of an off-sale product, from the copy too."""
    binding = await store_for(migrated_dsn, pool)
    client, store = binding.client_id, binding.record_id
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    age = timedelta(seconds=5) if trigger == "fresh_copy" else timedelta(hours=2)
    product = await _off_sale_product(pool, client, store, age=age)
    tiktok.fail[("product_detail", product)] = server_error
    rows = await fetch(
        migrated_dsn,
        "SELECT id FROM marketplace_listings WHERE client_id=%s ORDER BY external_sku_id",
        client,
    )
    live = (
        None
        if trigger == "tiktok_off"
        else LiveReadAccess(load_app=any_app_loader(pool), decrypt=decrypt)
    )
    result = await stock_read(
        pool,
        client,
        ProductStockReadProposal(action="product_stock_read"),
        RecordRef(kind="product", id=str(rows[0]["id"])),
        live=live,
        text=_TEXT["en"],
    )
    assert isinstance(result, ProductStockResult)
    assert not result.checked_live and not result.on_sale
    assert [(v.variant, v.seller_sku, v.stock) for v in result.variants] == [
        ("Black / M", "OFF-1", 7),
        ("White / L", "OFF-2", 5),
    ]
    assert result.total == 12
    expected = {
        "tiktok_off": "As of 2 hours ago (live check failed).",
        "fresh_copy": "Synced from TikTok just now.",
        "live_failure": "As of 2 hours ago (live check failed).",
    }
    assert result.note == expected[trigger]
    kinds = [kind for kind, _, _ in tiktok.requests]
    assert kinds == (["shops", "product_detail"] if trigger == "live_failure" else [])
    # The customer path stays fail-closed: the same copy offers no SKU of this product.
    customer = await live_stock(pool, client, rows[0]["id"], live=None)
    assert customer is not None and customer.skus == () and not customer.sellable


async def _with_removed_sku(
    pool: AsyncConnectionPool, migrated_dsn: str, client: UUID, store: UUID, age: timedelta
) -> list[UUID]:
    """A selling product with two live SKUs (7, 5) and one TikTok removed (stale count 99)."""
    payload = product_payload("SYNTH-P-GONE")
    payload["skus"] = [
        sku_payload("SYNTH-SKU-GONE-1", quantities=(4, 3), seller_sku="LIVE-1"),
        sku_payload(
            "SYNTH-SKU-GONE-2", quantities=(5,), attributes=("White", "L"), seller_sku="LIVE-2"
        ),
        sku_payload(
            "SYNTH-SKU-GONE-3", quantities=(99,), attributes=("Red", "S"), seller_sku="GONE-3"
        ),
    ]
    await seed_listings(pool, client, store, payload, synced_at=now() - age)
    # What store_products does when TikTok stops listing a SKU: status only, count kept.
    await fetch(
        migrated_dsn,
        "UPDATE marketplace_listings SET product_status='DELETED' "
        "WHERE client_id=%s AND external_sku_id='SYNTH-SKU-GONE-3' RETURNING id",
        client,
    )
    rows = await fetch(
        migrated_dsn,
        "SELECT id FROM marketplace_listings WHERE client_id=%s ORDER BY external_sku_id",
        client,
    )
    return [row["id"] for row in rows]


@pytest.mark.parametrize("trigger", ["tiktok_off", "fresh_copy", "live_failure"])
async def test_a_sku_tiktok_removed_never_counts_on_the_staff_copy_path(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch, trigger: str
) -> None:
    binding = await store_for(migrated_dsn, pool)
    client, store = binding.client_id, binding.record_id
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    tiktok.fail[("product_detail", "SYNTH-P-GONE")] = server_error
    age = timedelta(seconds=5) if trigger == "fresh_copy" else timedelta(hours=2)
    ids = await _with_removed_sku(pool, migrated_dsn, client, store, age)
    live = (
        None
        if trigger == "tiktok_off"
        else LiveReadAccess(load_app=any_app_loader(pool), decrypt=decrypt)
    )
    result = await stock_read(
        pool,
        client,
        ProductStockReadProposal(action="product_stock_read"),
        RecordRef(kind="product", id=str(ids[0])),
        live=live,
        text=_TEXT["en"],
    )
    assert isinstance(result, ProductStockResult) and not result.checked_live
    assert [(v.seller_sku, v.stock) for v in result.variants] == [("LIVE-1", 7), ("LIVE-2", 5)]
    assert result.total == 12 and result.on_sale
    assert "No variants" not in result.note
    # The summary of the open product agrees: two variants, 12 in stock.
    summary = await product_summary(pool, client, ids[0], text=_TEXT["en"], now=now())
    assert summary is not None and ": 2 variants, 12 in stock in our copy" in summary
    # The customer path is unchanged: sellable SKUs only.
    customer = await live_stock(pool, client, ids[0], live=None)
    assert customer is not None
    assert [s.seller_sku for s in customer.skus] == ["LIVE-1", "LIVE-2"]


async def test_a_product_tiktok_removed_entirely_lists_no_variants(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = await store_for(migrated_dsn, pool)
    client, store = binding.client_id, binding.record_id
    FakeTikTok(binding.shop_id).install(monkeypatch)
    ids = await _with_removed_sku(pool, migrated_dsn, client, store, timedelta(hours=2))
    await fetch(
        migrated_dsn,
        "UPDATE marketplace_listings SET product_status='DELETED' WHERE client_id=%s RETURNING id",
        client,
    )
    for language, note in (
        (
            "en",
            "No variants of this product are listed on TikTok Shop. As of 2 hours ago "
            "(live check failed).",
        ),
        (
            "ms",
            "Tiada varian produk ini disenaraikan di TikTok Shop. Setakat 2 jam lalu "
            "(semakan langsung gagal).",
        ),
    ):
        result = await stock_read(
            pool,
            client,
            ProductStockReadProposal(action="product_stock_read", listing_id=ids[0]),
            None,
            live=None,
            text=_TEXT[language],
        )
        assert isinstance(result, ProductStockResult)
        assert result.variants == [] and result.total == 0 and not result.on_sale
        assert result.note == note
    customer = await live_stock(pool, client, ids[0], live=None)
    assert customer is not None and customer.skus == () and not customer.sellable
