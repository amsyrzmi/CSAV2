"""Offline order-read contracts; fixtures do not verify the live TikTok API."""

import json
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx
import pytest
from csa.agents.ecommerce import TikTokReadError, TikTokShopAdapter

from tests.test_ecommerce_agent import _sample_creds


def order(order_id: str = "SYNTHETIC-1") -> dict[str, Any]:
    return {
        "id": order_id,
        "status": "AWAITING_SHIPMENT",
        "payment": {"total_amount": "12.34", "currency": "MYR"},
        "line_items": [
            {
                "sku_id": "SYNTHETIC-SKU",
                "product_name": "Synthetic product",
                "quantity": 2,
                "sku_sale_price": "6.17",
            }
        ],
        "tracking_number": "SYNTHETIC-TRACKING",
        "shipping_provider": "Synthetic carrier",
        "recipient_address": {"private": "not-for-projection"},
    }


@pytest.mark.asyncio
async def test_order_pages_preserve_cursor_signing_and_money() -> None:
    requests: list[httpx.Request] = []
    creds = _sample_creds()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        assert request.method == "POST"
        assert request.url.path == "/order/202309/orders/search"
        assert "access_token" not in request.url.params
        assert request.headers["x-tts-access-token"] == creds.access_token
        params = dict(request.url.params)
        assert params["sign"] == adapter.sign_request(
            request.url.path, params, request.content.decode()
        )
        assert body["page_size"] == 1 and body["order_status"] == "AWAITING_SHIPMENT"
        if len(requests) == 1:
            assert "page_token" not in body
            return httpx.Response(
                200,
                json={"code": 0, "data": {"orders": [order()], "next_page_token": "page+2/="}},
            )
        assert body["page_token"] == "page+2/="
        return httpx.Response(
            200,
            json={"code": 0, "data": {"orders": [order("SYNTHETIC-2")], "next_page_token": ""}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = TikTokShopAdapter(creds, http_client=client)
        tenant = uuid4()
        first = await adapter.search_order_page(tenant, status="AWAITING_SHIPMENT", limit=1)
        assert first.next_page_token == "page+2/="
        assert first.items[0].total_amount == Decimal("12.34")
        assert first.items[0].items[0].unit_price == Decimal("6.17")
        assert first.items[0].shipping_carrier == "Synthetic carrier"
        assert first.items[0].items[0].sku == "SYNTHETIC-SKU"
        assert "not-for-projection" not in first.model_dump_json()
        assert first.model_dump(mode="json")["items"][0]["total_amount"] == "12.34"
        last = await adapter.search_order_page(
            tenant, status="AWAITING_SHIPMENT", limit=1, page_token=first.next_page_token
        )
        assert last.next_page_token is None
        assert last.items[0].platform_order_id == "SYNTHETIC-2"
    assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"code": 105001, "message": "private-provider-detail"}, "provider_rejected"),
        ({"data": {"orders": []}}, "invalid_response"),
        ({"code": "0", "data": {"orders": []}}, "invalid_response"),
        ({"code": False, "data": {"orders": []}}, "invalid_response"),
        ({"code": 0}, "invalid_response"),
        ({"code": 0, "data": []}, "invalid_response"),
        ({"code": 0, "data": {}}, "invalid_response"),
        ({"code": 0, "data": {"orders": None}}, "invalid_response"),
        ({"code": 0, "data": {"orders": [{}]}}, "invalid_response"),
        ({"code": 0, "data": {"orders": [order(), order()]}}, "invalid_page"),
        (
            {"code": 0, "data": {"orders": [], "next_page_token": "same"}},
            "invalid_page",
        ),
        (
            {"code": 0, "data": {"orders": [], "next_page_token": "x" * 2049}},
            "invalid_response",
        ),
    ],
)
async def test_invalid_pages_never_become_empty_success(payload: dict[str, Any], code: str) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        adapter = TikTokShopAdapter(_sample_creds(), http_client=client)
        with pytest.raises(TikTokReadError) as caught:
            await adapter.search_order_page(uuid4(), page_token="same")
        assert caught.value.code == code
        assert "private-provider-detail" not in str(caught.value)
        assert caught.value.__cause__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", ""),
        ("status", None),
        ("payment", {}),
        ("payment", {"total_amount": "-0.01", "currency": "MYR"}),
        ("payment", {"total_amount": "NaN", "currency": "MYR"}),
        ("payment", {"total_amount": "Infinity", "currency": "MYR"}),
        ("payment", {"total_amount": "12.34"}),
        ("line_items", [{}]),
        (
            "line_items",
            [{"product_name": "Synthetic", "quantity": 1, "sku_sale_price": "1.00"}],
        ),
        (
            "line_items",
            [
                {
                    "seller_sku": "SYNTHETIC",
                    "product_name": "Synthetic",
                    "quantity": True,
                    "sku_sale_price": "1.00",
                }
            ],
        ),
    ],
)
async def test_required_order_fields_are_not_fabricated(field: str, value: Any) -> None:
    payload = order()
    payload[field] = value
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"code": 0, "data": payload})
        )
    ) as client:
        adapter = TikTokShopAdapter(_sample_creds(), http_client=client)
        with pytest.raises(TikTokReadError, match="invalid_response"):
            await adapter.get_order(uuid4(), "SYNTHETIC-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["http", "timeout", "invalid_json", "redirect"])
async def test_read_errors_do_not_expose_provider_or_request_secrets(failure: str) -> None:
    creds = _sample_creds()

    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "http":
            return httpx.Response(401, text=creds.access_token)
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://example.invalid/private"})
        if failure == "timeout":
            raise httpx.ReadTimeout(f"private transport {creds.access_token}", request=request)
        return httpx.Response(200, text=f"not JSON {creds.access_token}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        adapter = TikTokShopAdapter(creds, http_client=client)
        with pytest.raises(TikTokReadError) as caught:
            await adapter.get_order(uuid4(), "SYNTHETIC-1")
        message = str(caught.value)
        assert creds.access_token not in message and creds.app_secret not in message
        assert (
            caught.value.code
            == {
                "http": "http_error",
                "redirect": "http_error",
                "timeout": "transport_error",
                "invalid_json": "invalid_response",
            }[failure]
        )
        assert caught.value.__suppress_context__


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        {"limit": 0},
        {"limit": 51},
        {"limit": True},
        {"page_token": ""},
        {"page_token": "x" * 2049},
        {"status": ""},
    ],
)
async def test_invalid_search_query_never_calls_provider(query: dict[str, Any]) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        pytest.fail("invalid query must not perform HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = TikTokShopAdapter(_sample_creds(), http_client=client)
        with pytest.raises(TikTokReadError, match="invalid_query"):
            await adapter.search_order_page(uuid4(), **query)


@pytest.mark.asyncio
async def test_detail_identity_and_page_size_must_match_request() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"code": 0, "data": order("WRONG-ORDER")})
        )
    ) as client:
        adapter = TikTokShopAdapter(_sample_creds(), http_client=client)
        with pytest.raises(TikTokReadError, match="order_mismatch"):
            await adapter.get_order(uuid4(), "SYNTHETIC-1")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"code": 0, "data": {"orders": [order(), order("SYNTHETIC-2")]}}
            )
        )
    ) as client:
        adapter = TikTokShopAdapter(_sample_creds(), http_client=client)
        with pytest.raises(TikTokReadError, match="invalid_page"):
            await adapter.search_order_page(uuid4(), limit=1)


@pytest.mark.asyncio
async def test_empty_page_is_explicit_and_legacy_list_shape_preserved() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"code": 0, "data": {"orders": []}})
        )
    ) as client:
        adapter = TikTokShopAdapter(_sample_creds(), http_client=client)
        page = await adapter.search_order_page(uuid4())
        assert page.items == [] and page.next_page_token is None
        assert await adapter.search_orders(uuid4()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("order_id", ["", "x" * 101])
async def test_invalid_order_id_never_calls_provider(order_id: str) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        pytest.fail("invalid order ID must not perform HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = TikTokShopAdapter(_sample_creds(), http_client=client)
        with pytest.raises(TikTokReadError, match="invalid_query"):
            await adapter.get_order(uuid4(), order_id)
