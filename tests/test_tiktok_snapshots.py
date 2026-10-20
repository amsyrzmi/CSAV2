"""Synthetic provider-native projections; no live authorization or seller data."""

from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx
import pytest
from csa.agents.ecommerce import TikTokOrderSnapshot, TikTokReadError, TikTokShopAdapter
from pydantic import ValidationError

from tests.test_ecommerce_agent import _sample_creds


def order_snapshot(order_id: str = "SYNTHETIC-1") -> dict[str, Any]:
    return {
        "id": order_id,
        "status": "AWAITING_SHIPMENT",
        "payment": {"total_amount": "12.3400", "currency": "MYR"},
        "line_items": [
            {
                "id": line_id,
                "sku_id": "SYNTHETIC-SKU",
                "seller_sku": "",
                "product_name": "Synthetic product",
                "sale_price": "6.1700",
                "recipient_address": "PRIVATE-LINE",
            }
            for line_id in ("SYNTH-LINE-1", "SYNTH-LINE-2")
        ],
        "user_id": "PRIVATE-BUYER",
        "buyer_uid": "PRIVATE-LEGACY-BUYER",
        "recipient_address": "PRIVATE-ADDRESS",
        "arbitrary": "PRIVATE-UNKNOWN",
    }


@pytest.mark.asyncio
async def test_native_detail_preserves_lines_money_and_excludes_private_fields() -> None:
    payload = order_snapshot()
    creds = _sample_creds()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/order/202309/orders"
        assert request.url.params["ids"] == payload["id"]
        assert "access_token" not in request.url.params
        assert request.headers["x-tts-access-token"] == creds.access_token
        assert request.url.params["sign"] == adapter.sign_request(
            request.url.path, dict(request.url.params), ""
        )
        return httpx.Response(200, json={"code": 0, "data": {"orders": [payload]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = TikTokShopAdapter(creds, http_client=client)
        snapshot = await adapter.get_order_snapshot(uuid4(), payload["id"])
        assert isinstance(snapshot, TikTokOrderSnapshot)
        assert [line.id for line in snapshot.line_items] == ["SYNTH-LINE-1", "SYNTH-LINE-2"]
        assert snapshot.line_items[0].sku_id == snapshot.line_items[1].sku_id
        assert snapshot.line_items[0].sale_price == Decimal("6.1700")
        serialized = snapshot.model_dump(mode="json")
        assert serialized["payment"] == {"total_amount": "12.3400", "currency": "MYR"}
        assert serialized["line_items"][0]["sale_price"] == "6.1700"
        assert "quantity" not in serialized["line_items"][0]
        assert "unit_price" not in serialized["line_items"][0]
        assert "PRIVATE" not in snapshot.model_dump_json()
        with pytest.raises(ValidationError, match="frozen_instance"):
            snapshot.line_items[0].id = "CHANGED"
        with pytest.raises(ValidationError, match="frozen_instance"):
            snapshot.status = "CHANGED"
        with pytest.raises(TikTokReadError, match="invalid_response"):
            await adapter.get_order(uuid4(), payload["id"])


@pytest.mark.asyncio
async def test_native_search_preserves_opaque_pages_and_explicit_empty_page() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/order/202309/orders/search"
        assert request.method == "POST"
        assert request.content == b'{"order_status": "AWAITING_SHIPMENT"}'
        assert request.url.params["page_size"] == "1"
        assert request.url.params["sign"] == adapter.sign_request(
            request.url.path, dict(request.url.params), request.content.decode()
        )
        if calls == 1:
            assert "page_token" not in request.url.params
            data = {"orders": [order_snapshot()], "next_page_token": "opaque/+?&=%"}
        else:
            assert request.url.params["page_token"] == "opaque/+?&=%"
            data = {"orders": [], "next_page_token": ""}
        return httpx.Response(200, json={"code": 0, "data": data})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = TikTokShopAdapter(_sample_creds(), http_client=client)
        page = await adapter.search_order_snapshot_page(
            uuid4(), status="AWAITING_SHIPMENT", limit=1
        )
        assert len(page.items) == 1
        assert page.next_page_token == "opaque/+?&=%"
        last = await adapter.search_order_snapshot_page(
            uuid4(), status="AWAITING_SHIPMENT", limit=1, page_token=page.next_page_token
        )
        assert last.items == () and last.next_page_token is None
    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("read", ["detail", "search"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", ""),
        ("id", None),
        ("sku_id", ""),
        ("sale_price", None),
        ("sale_price", "-0.01"),
        ("sale_price", "NaN"),
        ("sale_price", "Infinity"),
        ("product_name", ""),
        ("product_name", None),
    ],
)
async def test_native_invalid_lines_fail_whole_read(read: str, field: str, value: Any) -> None:
    payload = order_snapshot()
    payload["line_items"][1][field] = value
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"code": 0, "data": {"orders": [payload]}})
        )
    ) as client:
        adapter = TikTokShopAdapter(_sample_creds(), http_client=client)
        with pytest.raises(TikTokReadError, match="invalid_response") as caught:
            if read == "detail":
                await adapter.get_order_snapshot(uuid4(), "SYNTHETIC-1")
            else:
                await adapter.search_order_snapshot_page(uuid4())
        assert caught.value.__suppress_context__
        assert "PRIVATE" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("read", ["detail", "search"])
@pytest.mark.parametrize("mutation", ["duplicate_line", "missing_price", "legacy_price", "payment"])
async def test_native_no_inference_or_duplicate_line_identity(read: str, mutation: str) -> None:
    payload = order_snapshot()
    if mutation == "duplicate_line":
        payload["line_items"][1]["id"] = payload["line_items"][0]["id"]
    elif mutation in ("missing_price", "legacy_price"):
        del payload["line_items"][0]["sale_price"]
        if mutation == "legacy_price":
            payload["line_items"][0].update(quantity=2, sku_sale_price="6.17")
    else:
        del payload["payment"]["currency"]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"code": 0, "data": {"orders": [payload]}})
        )
    ) as client:
        adapter = TikTokShopAdapter(_sample_creds(), http_client=client)
        with pytest.raises(TikTokReadError, match="invalid_response"):
            if read == "detail":
                await adapter.get_order_snapshot(uuid4(), "SYNTHETIC-1")
            else:
                await adapter.search_order_snapshot_page(uuid4())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "orders", [[], [order_snapshot(), order_snapshot()], [order_snapshot("OTHER")]]
)
async def test_native_detail_exact_identity_and_cardinality(orders: list[dict[str, Any]]) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"code": 0, "data": {"orders": orders}})
        )
    ) as client:
        adapter = TikTokShopAdapter(_sample_creds(), http_client=client)
        with pytest.raises(TikTokReadError, match="order_mismatch"):
            await adapter.get_order_snapshot(uuid4(), "SYNTHETIC-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        {"orders": [order_snapshot(), order_snapshot()]},
        {"orders": [order_snapshot(), order_snapshot("OTHER")]},
        {"orders": [], "next_page_token": "same"},
    ],
)
async def test_native_search_rejects_duplicate_oversized_or_stuck_pages(
    data: dict[str, Any],
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"code": 0, "data": data}))
    ) as client:
        adapter = TikTokShopAdapter(_sample_creds(), http_client=client)
        with pytest.raises(TikTokReadError, match="invalid_page"):
            await adapter.search_order_snapshot_page(uuid4(), limit=1, page_token="same")


@pytest.mark.asyncio
async def test_native_invalid_queries_never_call_provider() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        pytest.fail("invalid query must not perform HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = TikTokShopAdapter(_sample_creds(), http_client=client)
        for order_id in ("", "x" * 101, "ONE,TWO"):
            with pytest.raises(TikTokReadError, match="invalid_query"):
                await adapter.get_order_snapshot(uuid4(), order_id)
        with pytest.raises(TikTokReadError, match="invalid_query"):
            await adapter.search_order_snapshot_page(uuid4(), limit=51)
        with pytest.raises(TikTokReadError, match="invalid_query"):
            await adapter.search_order_snapshot_page(uuid4(), page_token="")
