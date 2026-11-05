"""Product, variant and stock reads: synthetic provider payloads only, no live shop."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx
import pytest
from csa.agents.ecommerce import TikTokReadError, TikTokShopAdapter
from csa.agents.ecommerce.authorization import TikTokAuthorizationContext, TikTokAuthorizationError
from csa.agents.ecommerce.dispatch import TikTokReadRequest, begin_read
from csa.agents.ecommerce.grants import create_verified_grant
from csa.agents.ecommerce.reader import (
    BoundTikTokReader,
    read_verified,
    read_verified_order,
)
from csa.agents.ecommerce.tiktok_models import (
    TikTokOrderSnapshotPage,
    TikTokProductSnapshot,
    TikTokProductSnapshotPage,
    TikTokSkuSnapshot,
)
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

from tests.conftest import requires_docker
from tests.test_ecommerce_agent import _sample_creds
from tests.test_ecommerce_grants import pool, seed  # noqa: F401  (pool is a fixture)


def sku_payload(
    sku_id: str = "SYNTH-SKU-1",
    *,
    quantities: tuple[int, ...] = (4, 6),
    attributes: tuple[str, ...] = ("Black", "M"),
    seller_sku: str | None = "MUG-BLK-M",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": sku_id,
        "price": {"sale_price": "19.9000", "currency": "MYR"},
        "sales_attributes": [
            {"name": f"Attr{index}", "value_name": value} for index, value in enumerate(attributes)
        ],
        "inventory": [
            {"warehouse_id": f"SYNTH-WH-{index}", "quantity": quantity}
            for index, quantity in enumerate(quantities, start=1)
        ],
        "supplementary_private": "PRIVATE-SKU",
    }
    if seller_sku is not None:
        payload["seller_sku"] = seller_sku
    return payload


def product_payload(product_id: str = "SYNTH-PROD-1") -> dict[str, Any]:
    return {
        "id": product_id,
        "title": "Synthetic mug",
        "status": "ACTIVATE",
        "main_images": [{"urls": ["https://example.invalid/mug.jpg"]}],
        "update_time": 1793750400,
        "skus": [
            sku_payload("SYNTH-SKU-1"),
            sku_payload(
                "SYNTH-SKU-2", quantities=(0,), attributes=("White", "L"), seller_sku="MUG-WHT-L"
            ),
        ],
        "arbitrary": "PRIVATE-UNKNOWN",
    }


def assert_parsed(product: TikTokProductSnapshot, product_id: str = "SYNTH-PROD-1") -> None:
    assert product.id == product_id
    assert product.name == "Synthetic mug"
    assert product.status == "ACTIVATE"
    assert product.image_url == "https://example.invalid/mug.jpg"
    assert product.update_time == datetime.fromtimestamp(1793750400, UTC)
    first, second = product.skus
    assert isinstance(first, TikTokSkuSnapshot)
    assert (first.id, first.seller_sku, first.variant_name) == (
        "SYNTH-SKU-1",
        "MUG-BLK-M",
        "Black / M",
    )
    assert first.price == Decimal("19.9000") and first.currency == "MYR"
    assert first.stock_qty == 10  # 4 + 6 across two warehouses
    assert (second.id, second.variant_name, second.stock_qty) == ("SYNTH-SKU-2", "White / L", 0)
    assert "PRIVATE" not in product.model_dump_json()


@pytest.mark.asyncio
async def test_product_page_parses_variants_and_sums_stock() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.method == "POST"
        assert request.url.path == "/product/202309/products/search"
        assert request.url.params["page_size"] == "1"
        assert request.url.params["page_token"] == "opaque/+?&="
        assert "access_token" not in request.url.params
        assert request.headers["x-tts-access-token"] == creds.access_token
        assert request.content == b"{}"
        assert request.url.params["sign"] == adapter.sign_request(
            request.url.path, dict(request.url.params), request.content.decode()
        )
        data = {"products": [product_payload()], "next_page_token": "next-opaque", "total_count": 7}
        return httpx.Response(200, json={"code": 0, "data": data})

    creds = _sample_creds()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = TikTokShopAdapter(creds, http_client=client)
        page = await adapter.search_product_snapshot_page(
            uuid4(), limit=1, page_token="opaque/+?&="
        )
    assert isinstance(page, TikTokProductSnapshotPage)
    assert page.next_page_token == "next-opaque"
    assert len(page.items) == 1
    assert_parsed(page.items[0])
    assert calls == 1
    # A dump re-validates, so a stored snapshot can be rehydrated.
    again = TikTokProductSnapshot.model_validate(page.items[0].model_dump(mode="json"))
    assert again == page.items[0]


@pytest.mark.asyncio
async def test_product_detail_reads_one_product_with_live_stock() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/product/202309/products/SYNTH-PROD-1"
        assert request.url.params["shop_cipher"] == creds.shop_cipher
        assert request.url.params["sign"] == adapter.sign_request(
            request.url.path, dict(request.url.params), ""
        )
        return httpx.Response(200, json={"code": 0, "data": product_payload()})

    creds = _sample_creds()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = TikTokShopAdapter(creds, http_client=client)
        assert_parsed(await adapter.get_product_snapshot(uuid4(), "SYNTH-PROD-1"))


@pytest.mark.asyncio
async def test_product_search_window_sent_in_request_body() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        assert request.url.params["sign"] == adapter.sign_request(
            request.url.path, dict(request.url.params), request.content.decode()
        )
        assert set(request.url.params) <= {
            "app_key",
            "timestamp",
            "shop_cipher",
            "sign",
            "page_size",
        }
        return httpx.Response(200, json={"code": 0, "data": {"products": []}})

    start = datetime(2026, 11, 1, tzinfo=UTC)
    end = datetime(2026, 11, 2, tzinfo=UTC)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = TikTokShopAdapter(_sample_creds(), http_client=client)
        windows: list[dict[str, datetime]] = [
            {"updated_from": start, "updated_to": end},
            {"updated_from": start},
            {"updated_to": end},
            {},
        ]
        for window in windows:
            page = await adapter.search_product_snapshot_page(uuid4(), limit=5, **window)  # type: ignore[arg-type]
            assert page.items == () and page.next_page_token is None
    from_ts, to_ts = int(start.timestamp()), int(end.timestamp())
    # The end of the window is exclusive; the provider bound is inclusive whole seconds.
    assert bodies == [
        {"update_time_ge": from_ts, "update_time_le": to_ts - 1},
        {"update_time_ge": from_ts},
        {"update_time_le": to_ts - 1},
        {},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "product_id", ["", " ", "a,b", "a/b", "../x", "a?b", "a b", "x" * 101, "café"]
)
async def test_product_detail_requires_product_id(product_id: str) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"code": 0, "data": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = TikTokShopAdapter(_sample_creds(), http_client=client)
        with pytest.raises(TikTokReadError, match="invalid_query"):
            await adapter.get_product_snapshot(uuid4(), product_id)
    assert calls == 0


@pytest.mark.parametrize(
    "query",
    [
        {"kind": "product_detail"},
        {"kind": "product_detail", "product_id": ""},
        {"kind": "product_detail", "product_id": "A,B"},
        {"kind": "product_detail", "product_id": "A", "order_id": "O"},
        {"kind": "product_detail", "product_id": "A", "status": "X"},
        {"kind": "product_detail", "product_id": "A", "page_token": "t"},
        {"kind": "product_detail", "product_id": "A", "limit": 5},
        {"kind": "product_detail", "product_id": "A", "updated_from": "2026-11-01T00:00:00Z"},
        {"kind": "product_search", "product_id": "A"},
        {"kind": "product_search", "order_id": "O"},
        {"kind": "product_search", "status": "X"},
        {"kind": "product_search", "limit": 0},
        {
            "kind": "product_search",
            "updated_from": "2026-11-02T00:00:00Z",
            "updated_to": "2026-11-01T00:00:00Z",
        },
        {"kind": "detail", "order_id": "O", "product_id": "A"},
        {"kind": "search", "product_id": "A"},
    ],
)
def test_product_request_shapes_are_narrow(query: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        TikTokReadRequest.model_validate(query)


def test_product_request_shapes_round_trip() -> None:
    start = datetime(2026, 11, 1, tzinfo=UTC)
    end = datetime(2026, 11, 2, tzinfo=UTC)
    for request in (
        TikTokReadRequest(kind="product_detail", product_id="SYNTH-PROD-1"),
        TikTokReadRequest(kind="product_search", limit=20, page_token="opaque"),
        TikTokReadRequest(kind="product_search", updated_from=start, updated_to=end),
    ):
        assert TikTokReadRequest.model_validate(request.model_dump(mode="json")) == request


def _bad_negative_warehouse() -> dict[str, Any]:
    product = product_payload()
    product["skus"] = [sku_payload(quantities=(5, -1))]
    return product


def _bad_stock_above_int4() -> dict[str, Any]:
    product = product_payload()
    product["skus"] = [sku_payload(quantities=(2_147_483_647, 1))]
    return product


def _bad_single_warehouse_above_int4() -> dict[str, Any]:
    product = product_payload()
    product["skus"] = [sku_payload(quantities=(2_147_483_648,))]
    return product


def _bad_all_negative() -> dict[str, Any]:
    product = product_payload()
    product["skus"] = [sku_payload(quantities=(-3,))]
    return product


def _bad_missing_inventory() -> dict[str, Any]:
    product = product_payload()
    del product["skus"][0]["inventory"]
    return product


def _bad_fractional_quantity() -> dict[str, Any]:
    product = product_payload()
    product["skus"] = [sku_payload(quantities=(1.5,))]  # type: ignore[arg-type]
    return product


def _bad_string_quantity() -> dict[str, Any]:
    product = product_payload()
    product["skus"][0]["inventory"][0]["quantity"] = "4"
    return product


def _bad_duplicate_sku() -> dict[str, Any]:
    product = product_payload()
    product["skus"] = [sku_payload("SAME"), sku_payload("SAME")]
    return product


def _bad_negative_price() -> dict[str, Any]:
    product = product_payload()
    product["skus"][0]["price"]["sale_price"] = "-1.00"
    return product


def _bad_missing_price() -> dict[str, Any]:
    product = product_payload()
    del product["skus"][0]["price"]
    return product


def _bad_currency() -> dict[str, Any]:
    product = product_payload()
    product["skus"][0]["price"]["currency"] = "RINGGIT"
    return product


def _bad_no_name() -> dict[str, Any]:
    product = product_payload()
    product["title"] = ""
    return product


@pytest.mark.asyncio
@pytest.mark.parametrize("read", ["detail", "search"])
@pytest.mark.parametrize(
    "build",
    [
        _bad_negative_warehouse,
        _bad_all_negative,
        _bad_stock_above_int4,
        _bad_single_warehouse_above_int4,
        _bad_missing_inventory,
        _bad_fractional_quantity,
        _bad_string_quantity,
        _bad_duplicate_sku,
        _bad_negative_price,
        _bad_missing_price,
        _bad_currency,
        _bad_no_name,
    ],
)
async def test_negative_stock_rejected(read: str, build: Any) -> None:
    """Negative or malformed stock, price and identity fail the whole read, safely."""
    payload = build()

    def handler(request: httpx.Request) -> httpx.Response:
        data = payload if read == "detail" else {"products": [payload]}
        return httpx.Response(200, json={"code": 0, "data": data})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = TikTokShopAdapter(_sample_creds(), http_client=client)
        with pytest.raises(TikTokReadError, match="invalid_response") as caught:
            if read == "detail":
                await adapter.get_product_snapshot(uuid4(), "SYNTH-PROD-1")
            else:
                await adapter.search_product_snapshot_page(uuid4(), limit=5)
    assert "PRIVATE" not in str(caught.value) and "SYNTH" not in str(caught.value)


def test_snapshot_model_rejects_negative_stock_directly() -> None:
    with pytest.raises(ValidationError):
        TikTokSkuSnapshot(
            id="S", seller_sku=None, variant_name="", price=Decimal(1), currency="MYR", stock_qty=-1
        )
    sku = TikTokSkuSnapshot(
        id="S", seller_sku=None, variant_name="", price=Decimal(1), currency="MYR", stock_qty=0
    )
    assert sku.stock_qty == 0
    with pytest.raises(ValidationError, match="frozen_instance"):
        sku.stock_qty = 5  # type: ignore[misc]


@pytest.mark.asyncio
async def test_product_without_optional_fields_or_variants_validates() -> None:
    payload = {
        "id": "SYNTH-PROD-2",
        "title": "Plain thing",
        "status": "DRAFT",
        "skus": [
            {
                "id": "SYNTH-SKU-9",
                "price": {"sale_price": "3.00", "currency": "MYR"},
                "inventory": [],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "data": payload})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = TikTokShopAdapter(_sample_creds(), http_client=client)
        product = await adapter.get_product_snapshot(uuid4(), "SYNTH-PROD-2")
    assert product.image_url is None and product.update_time is None
    assert product.skus[0].seller_sku is None
    assert product.skus[0].variant_name == ""
    assert product.skus[0].stock_qty == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("read", ["detail", "search"])
@pytest.mark.parametrize("status", [500, 429, 503])
async def test_product_reads_never_retry(read: str, status: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, text="PRIVATE provider body")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = TikTokShopAdapter(_sample_creds(), http_client=client)
        with pytest.raises(TikTokReadError, match="http_error") as caught:
            if read == "detail":
                await adapter.get_product_snapshot(uuid4(), "SYNTH-PROD-1")
            else:
                await adapter.search_product_snapshot_page(uuid4(), limit=5)
    assert calls == 1
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.asyncio
async def test_product_detail_exact_identity_and_search_page_integrity() -> None:
    mode = "wrong_product"

    def handler(request: httpx.Request) -> httpx.Response:
        if mode == "wrong_product":
            return httpx.Response(200, json={"code": 0, "data": product_payload("SYNTH-OTHER")})
        products = {
            "oversized": [product_payload("A"), product_payload("B")],
            "duplicate": [product_payload("A"), product_payload("A")],
            "stuck": [product_payload("A")],
        }[mode]
        data = {"products": products, "next_page_token": "same"}
        return httpx.Response(200, json={"code": 0, "data": data})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = TikTokShopAdapter(_sample_creds(), http_client=client)
        with pytest.raises(TikTokReadError, match="product_mismatch"):
            await adapter.get_product_snapshot(uuid4(), "SYNTH-PROD-1")
        mode = "oversized"
        with pytest.raises(TikTokReadError, match="invalid_page"):
            await adapter.search_product_snapshot_page(uuid4(), limit=1)
        mode = "duplicate"
        with pytest.raises(TikTokReadError, match="invalid_page"):
            await adapter.search_product_snapshot_page(uuid4(), limit=5)
        mode = "stuck"
        with pytest.raises(TikTokReadError, match="invalid_page"):
            await adapter.search_product_snapshot_page(uuid4(), limit=5, page_token="same")


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"limit": 51}, {"page_token": ""}])
async def test_invalid_product_search_never_calls_provider(kwargs: dict[str, Any]) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"code": 0, "data": {"products": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = TikTokShopAdapter(_sample_creds(), http_client=client)
        with pytest.raises(TikTokReadError, match="invalid_query"):
            await adapter.search_product_snapshot_page(uuid4(), **{"limit": 5, **kwargs})
    assert calls == 0


def test_order_reads_alias_the_generalized_verified_read() -> None:
    assert read_verified_order is read_verified


PRODUCT_SCOPE = "seller.product.basic"


@requires_docker
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["product_detail", "product_search"])
@pytest.mark.parametrize("has_scope", [False, True])
async def test_product_read_requires_product_scope(
    pool: AsyncConnectionPool,  # noqa: F811
    migrated_dsn: str,
    kind: str,
    has_scope: bool,
) -> None:
    """A grant without the product scope fails closed before the shop check or any HTTP."""
    seeded = await seed(migrated_dsn)
    grant = (
        seeded.model_copy(update={"scopes": seeded.scopes | {PRODUCT_SCOPE}})
        if has_scope
        else seeded
    )
    await create_verified_grant(pool, grant)
    tenant = grant.context.binding.client_id
    request = (
        TikTokReadRequest(kind="product_detail", product_id="SYNTH-PROD-1")
        if kind == "product_detail"
        else TikTokReadRequest(kind="product_search", limit=5)
    )
    operation = await begin_read(pool, tenant, grant.id, request)
    creds = _sample_creds().model_copy(update={"shop_id": grant.context.binding.shop_id})
    calls: list[str] = []
    resolves = 0

    def handler(http: httpx.Request) -> httpx.Response:
        calls.append(http.url.path)
        if http.url.path == "/authorization/202309/shops":
            data: dict[str, Any] = {"shops": [{"id": creds.shop_id, "cipher": creds.shop_cipher}]}
        elif kind == "product_detail":
            assert http.url.path == "/product/202309/products/SYNTH-PROD-1"
            data = product_payload()
        else:
            assert http.url.path == "/product/202309/products/search"
            data = {"products": [product_payload()]}
        return httpx.Response(200, json={"code": 0, "data": data})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:

        async def resolve(context: TikTokAuthorizationContext, conn: Any) -> BoundTikTokReader:
            nonlocal resolves
            resolves += 1
            return BoundTikTokReader(
                context, creds, expected_app_key=creds.app_key, http_client=http
            )

        if not has_scope:
            with pytest.raises(TikTokAuthorizationError, match="insufficient_scope"):
                await read_verified(pool, tenant, operation.id, resolve)
            assert calls == [] and resolves == 0
            return
        result = await read_verified(pool, tenant, operation.id, resolve)
        assert calls == ["/authorization/202309/shops", calls[1]]
        assert len(calls) == 2 and resolves == 1
        if kind == "product_detail":
            assert isinstance(result, TikTokProductSnapshot)
            assert_parsed(result)
        else:
            assert isinstance(result, TikTokProductSnapshotPage)
            assert_parsed(result.items[0])
        with pytest.raises(TikTokAuthorizationError, match="operation_consumed"):
            await read_verified(pool, tenant, operation.id, resolve)
        assert len(calls) == 2


@requires_docker
@pytest.mark.asyncio
async def test_order_reads_do_not_need_the_product_scope(
    pool: AsyncConnectionPool,  # noqa: F811
    migrated_dsn: str,
) -> None:
    grant = await seed(migrated_dsn)
    assert PRODUCT_SCOPE not in grant.scopes
    await create_verified_grant(pool, grant)
    tenant = grant.context.binding.client_id
    operation = await begin_read(pool, tenant, grant.id, TikTokReadRequest(kind="search", limit=1))
    creds = _sample_creds().model_copy(update={"shop_id": grant.context.binding.shop_id})

    def handler(http: httpx.Request) -> httpx.Response:
        if http.url.path == "/authorization/202309/shops":
            data: dict[str, Any] = {"shops": [{"id": creds.shop_id, "cipher": creds.shop_cipher}]}
        else:
            data = {"orders": []}
        return httpx.Response(200, json={"code": 0, "data": data})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:

        async def resolve(context: TikTokAuthorizationContext, conn: Any) -> BoundTikTokReader:
            return BoundTikTokReader(
                context, creds, expected_app_key=creds.app_key, http_client=http
            )

        result = await read_verified_order(pool, tenant, operation.id, resolve)
    assert isinstance(result, TikTokOrderSnapshotPage) and result.items == ()
