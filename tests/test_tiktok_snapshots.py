"""Synthetic provider-native projections; no live authorization or seller data."""

from __future__ import annotations

import json
from datetime import UTC, datetime
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
        assert serialized["payment"] == {
            "total_amount": "12.3400",
            "currency": "MYR",
            "sub_total": None,
            "shipping_fee": None,
            "discount": None,
        }
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


def extended_order(order_id: str = "SYNTHETIC-1") -> dict[str, Any]:
    """Made-up payload shaped like the v202309 order fields the sync reads."""
    payload = order_snapshot(order_id)
    payload.update(
        create_time=1_762_000_000,
        update_time=1_762_003_600,
        paid_time=1_762_000_300,
        recipient_address={
            "name": "Synthetic Recipient",
            "phone_number": "+60000000000",
            "full_address": "1 Synthetic Lane, Synthetic Town",
            "region_code": "MY",
            "postal_code": "00000",
        },
        packages=[
            {
                "id": "SYNTH-PKG-1",
                "tracking_number": "SYNTH-TRACK-1",
                "shipping_provider_name": "Synthetic Courier",
            },
            {"id": "SYNTH-PKG-2"},
        ],
    )
    payload["payment"].update(
        sub_total="10.0000", shipping_fee="3.0000", seller_discount="0.6600", platform_discount="9"
    )
    return payload


@pytest.mark.asyncio
async def test_snapshot_parses_address_tracking_and_times_from_synthetic_payload() -> None:
    payload = extended_order()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"code": 0, "data": {"orders": [payload]}})
        )
    ) as client:
        adapter = TikTokShopAdapter(_sample_creds(), http_client=client)
        snapshot = await adapter.get_order_snapshot(uuid4(), payload["id"])
    assert snapshot.create_time == datetime.fromtimestamp(1_762_000_000, UTC)
    assert snapshot.update_time == datetime.fromtimestamp(1_762_003_600, UTC)
    assert snapshot.paid_at == datetime.fromtimestamp(1_762_000_300, UTC)
    assert snapshot.create_time is not None and snapshot.create_time.utcoffset() is not None
    assert snapshot.payment.sub_total == Decimal("10.0000")
    assert snapshot.payment.shipping_fee == Decimal("3.0000")
    assert snapshot.payment.discount == Decimal("0.6600")
    assert snapshot.shipping is not None
    assert snapshot.shipping.recipient_name == "Synthetic Recipient"
    assert snapshot.shipping.phone == "+60000000000"
    assert snapshot.shipping.address_text == "1 Synthetic Lane, Synthetic Town"
    assert snapshot.shipping.region == "MY"
    assert [(p.tracking_number, p.carrier) for p in snapshot.packages] == [
        ("SYNTH-TRACK-1", "Synthetic Courier"),
        (None, None),
    ]
    # Rehydrating our own serialized form works and unknown provider fields never leak in.
    again = TikTokOrderSnapshot.model_validate(snapshot.model_dump(mode="json"))
    assert again == snapshot
    assert "postal_code" not in snapshot.model_dump_json()
    with pytest.raises(ValidationError, match="frozen_instance"):
        snapshot.packages[0].tracking_number = "CHANGED"


@pytest.mark.parametrize(
    "recipient",
    [
        None,
        "PRIVATE-STRING-FORM",
        {},
        {
            "name": "A***",
            "phone_number": "(+60)****00",
            "full_address": "****",
            "region_code": "MY",
        },
        {"name": None, "phone_number": None, "full_address": None, "region_code": None},
    ],
)
def test_masked_or_absent_address_still_validates(recipient: Any) -> None:
    payload = order_snapshot()
    if recipient is None:
        payload.pop("recipient_address")
    else:
        payload["recipient_address"] = recipient
    snapshot = TikTokOrderSnapshot.model_validate(payload)
    assert snapshot.packages == ()
    assert snapshot.create_time is None and snapshot.update_time is None
    assert snapshot.paid_at is None
    assert snapshot.payment.sub_total is None
    if isinstance(recipient, dict) and recipient.get("region_code") == "MY":
        assert snapshot.shipping is not None
        assert snapshot.shipping.address_text == "****"
    elif isinstance(recipient, dict):
        assert snapshot.shipping is not None
        assert snapshot.shipping.address_text is None
    else:
        assert snapshot.shipping is None
    assert "PRIVATE" not in repr(snapshot)


def test_order_level_tracking_is_the_fallback_when_packages_carry_none() -> None:
    payload = order_snapshot()
    payload.update(tracking_number="SYNTH-TRACK-9", shipping_provider="Synthetic Courier")
    snapshot = TikTokOrderSnapshot.model_validate(payload)
    assert [(p.tracking_number, p.carrier) for p in snapshot.packages] == [
        ("SYNTH-TRACK-9", "Synthetic Courier")
    ]
    payload["packages"] = [{"tracking_number": "SYNTH-TRACK-1"}]
    assert TikTokOrderSnapshot.model_validate(payload).packages[0].tracking_number == (
        "SYNTH-TRACK-1"
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("payment", "sub_total"), "-1"),
        (("payment", "shipping_fee"), "NaN"),
        (("payment", "seller_discount"), "Infinity"),
        (("create_time",), "not-a-time"),
        (("paid_time",), "tomorrow"),
        (("packages",), "oops"),
    ],
)
def test_malformed_new_fields_fail_validation_without_echoing_input(
    path: tuple[str, ...], value: Any
) -> None:
    payload = extended_order()
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError) as caught:
        TikTokOrderSnapshot.model_validate(payload)
    assert "Synthetic Recipient" not in str(caught.value)
    assert "input_value" not in str(caught.value)


def test_odd_ship_to_and_package_text_is_truncated_or_ignored_never_fatal() -> None:
    payload = extended_order()
    payload["recipient_address"].update(
        name="N" * 400,
        phone_number="9" * 150,
        full_address="A" * 5000,
        region_code=12345,
    )
    payload["packages"] = [
        {"tracking_number": "T" * 400, "shipping_provider_name": {"odd": "shape"}},
        {"tracking_number": 77},
    ]
    snapshot = TikTokOrderSnapshot.model_validate(payload)
    assert snapshot.shipping is not None
    assert snapshot.shipping.recipient_name == "N" * 255
    assert snapshot.shipping.phone == "9" * 100
    assert snapshot.shipping.address_text == "A" * 2000
    assert snapshot.shipping.region is None
    assert snapshot.packages[0].tracking_number == "T" * 255
    assert snapshot.packages[0].carrier is None
    assert snapshot.packages[1].tracking_number is None


def test_overlong_line_name_and_seller_sku_truncate_but_ids_stay_strict() -> None:
    payload = extended_order()
    payload["line_items"][0].update(product_name="P" * 400, seller_sku="S" * 400)
    snapshot = TikTokOrderSnapshot.model_validate(payload)
    assert snapshot.line_items[0].product_name == "P" * 255
    assert snapshot.line_items[0].seller_sku == "S" * 255
    for field in ("id", "sku_id"):
        for bad in ("", "x" * 101):
            broken = extended_order()
            broken["line_items"][0][field] = bad
            with pytest.raises(ValidationError):
                TikTokOrderSnapshot.model_validate(broken)
    empty_name = extended_order()
    empty_name["line_items"][0]["product_name"] = ""
    with pytest.raises(ValidationError):
        TikTokOrderSnapshot.model_validate(empty_name)


@pytest.mark.asyncio
async def test_search_window_sent_in_request_body() -> None:
    start = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)
    end = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"code": 0, "data": {"orders": [], "next_page_token": ""}})

    creds = _sample_creds()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = TikTokShopAdapter(creds, http_client=client)
        await adapter.search_order_snapshot_page(
            uuid4(), status="AWAITING_SHIPMENT", limit=5, updated_from=start, updated_to=end
        )
        await adapter.search_order_snapshot_page(uuid4(), updated_from=start)
        await adapter.search_order_snapshot_page(uuid4(), updated_to=end, page_token="tok")
        await adapter.search_order_snapshot_page(uuid4())
    assert len(seen) == 4  # one attempt per call, never retried
    both, only_from, only_to, none = seen
    assert both.method == "POST" and both.url.path == "/order/202309/orders/search"
    assert json.loads(both.content) == {
        "order_status": "AWAITING_SHIPMENT",
        "update_time_ge": int(start.timestamp()),
        "update_time_lt": int(end.timestamp()),
    }
    assert json.loads(only_from.content) == {"update_time_ge": int(start.timestamp())}
    assert json.loads(only_to.content) == {"update_time_lt": int(end.timestamp())}
    assert only_to.url.params["page_token"] == "tok"
    assert none.content == b"{}"
    for request in seen:
        # Window stays in the signed body; the query keeps paging only.
        assert "update_time_ge" not in request.url.params
        assert "access_token" not in request.url.params
        assert request.headers["x-tts-access-token"] == creds.access_token
        assert request.url.params["sign"] == adapter.sign_request(
            request.url.path, dict(request.url.params), request.content.decode()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2026, 11, 2, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC)),
        (datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC)),
        (datetime(2026, 11, 1), datetime(2026, 11, 2, tzinfo=UTC)),
    ],
)
async def test_invalid_search_window_never_calls_provider(start: datetime, end: datetime) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        pytest.fail("invalid window must not perform HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = TikTokShopAdapter(_sample_creds(), http_client=client)
        with pytest.raises(TikTokReadError, match="invalid_query"):
            await adapter.search_order_snapshot_page(uuid4(), updated_from=start, updated_to=end)
