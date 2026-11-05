"""Pure mapping of TikTok snapshots into local order and listing rows; synthetic data only."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from csa.agents.ecommerce import TikTokOrderSnapshot, TikTokProductSnapshot
from csa.marketplace.mapping import (
    MappingError,
    map_listings,
    map_order,
    map_status,
)

from tests.test_tiktok_snapshots import extended_order

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
CLIENT = uuid4()
STORE = uuid4()


def snapshot(**changes: Any) -> TikTokOrderSnapshot:
    payload = extended_order("SYNTH-ORDER-1")
    for key, value in changes.items():
        payload[key] = value
    return TikTokOrderSnapshot.model_validate(payload)


def mapped(snap: TikTokOrderSnapshot | None = None):  # type: ignore[no-untyped-def]
    return map_order(CLIENT, STORE, "SYNTH-SHOP", snap or snapshot(), NOW)


@pytest.mark.parametrize(
    ("provider", "local"),
    [
        ("UNPAID", "pending_confirmation"),
        ("ON_HOLD", "processing"),
        ("AWAITING_SHIPMENT", "processing"),
        ("AWAITING_COLLECTION", "processing"),
        ("PARTIALLY_SHIPPING", "shipped"),
        ("IN_TRANSIT", "shipped"),
        ("DELIVERED", "delivered"),
        ("COMPLETED", "delivered"),
        ("CANCELLED", "cancelled"),
        ("SOMETHING_NEW", "processing"),
        ("", "processing"),
    ],
)
def test_status_table(provider: str, local: str) -> None:
    assert map_status(provider, paid=False) == (local, "unpaid")
    assert map_status(provider, paid=True) == (local, "paid")


def test_mapped_order_carries_provider_status_and_payment_state() -> None:
    order = mapped(snapshot(status="SOMETHING_NEW"))
    assert (order.status, order.provider_status) == ("processing", "SOMETHING_NEW")
    assert order.payment_status == "paid"  # extended_order carries a paid time
    unpaid = mapped(snapshot(status="UNPAID", paid_time=None))
    assert (unpaid.status, unpaid.payment_status) == ("pending_confirmation", "unpaid")
    # Statuses past payment imply paid even when the provider sent no paid time.
    shipped = mapped(snapshot(status="IN_TRANSIT", paid_time=None))
    assert shipped.payment_status == "paid"
    cancelled = mapped(snapshot(status="CANCELLED", paid_time=None))
    assert cancelled.payment_status == "unpaid"


def test_order_columns_ids_and_money() -> None:
    order = mapped()
    assert order.id == f"tt_{CLIENT.hex}_SYNTH-ORDER-1"
    assert order.external_order_id == "SYNTH-ORDER-1"
    assert (order.client_id, order.store_id, order.external_shop_id) == (
        CLIENT,
        STORE,
        "SYNTH-SHOP",
    )
    assert order.channel == "tiktok_shop"
    assert order.total == Decimal("12.34")
    assert order.currency == "MYR"
    assert (order.subtotal, order.shipping_fee, order.discount) == (
        Decimal("10.00"),
        Decimal("3.00"),
        Decimal("0.66"),
    )
    assert isinstance(order.total, Decimal)
    assert order.placed_at == datetime.fromtimestamp(1_762_000_000, UTC)
    assert order.provider_updated_at == datetime.fromtimestamp(1_762_003_600, UTC)
    assert order.synced_at == NOW
    assert (order.tracking_no, order.carrier) == ("SYNTH-TRACK-1", "Synthetic Courier")
    # The snapshot gives no shipped time, so none is invented.
    assert order.shipped_at is None
    assert not hasattr(order, "contact_id")


def test_missing_optional_money_maps_to_derived_subtotal_and_zero_fees() -> None:
    payload = extended_order("SYNTH-ORDER-2")
    payload["payment"] = {"total_amount": "5", "currency": "MYR"}
    order = mapped(TikTokOrderSnapshot.model_validate(payload))
    # No subtotal from TikTok: the sum of the folded line totals (2 x 6.17).
    assert order.subtotal == Decimal("12.34")
    assert (order.shipping_fee, order.discount) == (Decimal("0.00"), Decimal("0.00"))
    payload["line_items"] = []
    assert mapped(TikTokOrderSnapshot.model_validate(payload)).subtotal == Decimal("0.00")


def test_order_id_is_scoped_to_the_tenant() -> None:
    snap = snapshot()
    other = uuid4()
    first = map_order(CLIENT, STORE, "SYNTH-SHOP", snap, NOW)
    second = map_order(other, STORE, "SYNTH-SHOP", snap, NOW)
    assert first.id != second.id
    assert second.id == f"tt_{other.hex}_SYNTH-ORDER-1"
    assert first.external_order_id == second.external_order_id == "SYNTH-ORDER-1"


def test_lines_fold_on_sku_and_price_never_merging_different_prices() -> None:
    payload = extended_order("SYNTH-ORDER-7")
    payload["line_items"] = [
        {"id": "L1", "sku_id": "A", "seller_sku": "", "product_name": "A", "sale_price": "5"},
        {"id": "L2", "sku_id": "A", "seller_sku": "", "product_name": "A", "sale_price": "4"},
        {"id": "L3", "sku_id": "A", "seller_sku": "", "product_name": "A", "sale_price": "5.00"},
    ]
    payload["payment"] = {"total_amount": "20", "currency": "MYR"}
    lines = mapped(TikTokOrderSnapshot.model_validate(payload)).lines
    assert [(ln.sku, ln.qty, ln.unit_price, ln.external_line_id) for ln in lines] == [
        ("A", 2, Decimal("5.00"), "L1"),
        ("A", 1, Decimal("4.00"), "L2"),
    ]
    assert mapped(TikTokOrderSnapshot.model_validate(payload)).subtotal == Decimal("14.00")


def test_line_total_or_summed_subtotal_that_cannot_fit_is_rejected_safely() -> None:
    payload = extended_order("SYNTH-ORDER-8")
    # 3 units x 4,000,000,000.00 each fit as unit prices but not as a line total.
    payload["payment"] = {"total_amount": "1", "currency": "MYR"}
    payload["line_items"] = [
        {"id": f"L{n}", "sku_id": "A", "seller_sku": "", "product_name": "A", "sale_price": "4e9"}
        for n in range(3)
    ]
    with pytest.raises(MappingError) as caught:
        mapped(TikTokOrderSnapshot.model_validate(payload))
    assert caught.value.code == "amount_out_of_range"
    # Two lines that each fit but sum past the column, with no subtotal from TikTok.
    payload["line_items"] = [
        {"id": "L1", "sku_id": "A", "seller_sku": "", "product_name": "A", "sale_price": "6e9"},
        {"id": "L2", "sku_id": "B", "seller_sku": "", "product_name": "B", "sale_price": "6e9"},
    ]
    with pytest.raises(MappingError) as caught:
        mapped(TikTokOrderSnapshot.model_validate(payload))
    assert caught.value.code == "amount_out_of_range"


def test_same_sku_lines_fold_into_one_qty() -> None:
    order = mapped()
    # order_snapshot lists two lines of one SKU, one per unit.
    assert len(order.lines) == 1
    line = order.lines[0]
    assert line.qty == 2
    assert line.unit_price == Decimal("6.17")
    assert line.sku == "SYNTHETIC-SKU"
    assert line.name_snapshot == "Synthetic product"
    assert line.currency == "MYR"
    assert line.external_line_id == "SYNTH-LINE-1"


def test_different_skus_stay_separate_in_first_seen_order() -> None:
    payload = extended_order("SYNTH-ORDER-3")
    payload["line_items"] = [
        {"id": "L1", "sku_id": "B", "seller_sku": "SELL-B", "product_name": "B", "sale_price": "2"},
        {"id": "L2", "sku_id": "A", "seller_sku": "", "product_name": "A", "sale_price": "1"},
        {"id": "L3", "sku_id": "B", "seller_sku": "SELL-B", "product_name": "B", "sale_price": "2"},
    ]
    lines = mapped(TikTokOrderSnapshot.model_validate(payload)).lines
    assert [(ln.sku, ln.qty, ln.unit_price) for ln in lines] == [
        ("SELL-B", 2, Decimal("2.00")),
        ("A", 1, Decimal("1.00")),
    ]


def test_zero_lines_maps_without_error() -> None:
    order = mapped(snapshot(line_items=[]))
    assert order.lines == ()


def test_masked_address_maps_to_ship_to_as_given() -> None:
    order = mapped(
        snapshot(
            recipient_address={
                "name": "J***",
                "phone_number": "(+60)*****12",
                "full_address": "****",
                "region_code": "MY",
            }
        )
    )
    assert order.ship_to == {
        "recipient_name": "J***",
        "phone": "(+60)*****12",
        "address_text": "****",
        "region": "MY",
    }


def test_absent_address_maps_to_no_ship_to() -> None:
    assert mapped(snapshot(recipient_address=None)).ship_to is None
    assert mapped(snapshot(recipient_address="PRIVATE-ADDRESS")).ship_to is None
    assert mapped(snapshot(recipient_address={})).ship_to is None


def test_buyer_details_live_only_in_ship_to() -> None:
    order = mapped()
    assert order.ship_to is not None
    dump = order.model_dump(exclude={"ship_to"})
    assert "Synthetic Recipient" not in repr(dump)
    assert "+60000000000" not in repr(dump)


@pytest.mark.parametrize("amount", ["10000000000", "99999999999.99", "1e12"])
def test_amount_that_does_not_fit_the_column_is_rejected_safely(amount: str) -> None:
    payload = extended_order("SYNTH-ORDER-4")
    payload["payment"]["total_amount"] = amount
    snap = TikTokOrderSnapshot.model_validate(payload)
    with pytest.raises(MappingError) as caught:
        mapped(snap)
    assert caught.value.code == "amount_out_of_range"
    assert amount not in str(caught.value)


def test_line_price_out_of_range_is_rejected_safely() -> None:
    payload = extended_order("SYNTH-ORDER-5")
    payload["line_items"][0]["sale_price"] = "10000000000"
    with pytest.raises(MappingError) as caught:
        mapped(TikTokOrderSnapshot.model_validate(payload))
    assert caught.value.code == "amount_out_of_range"


def test_sub_cent_amounts_round_to_the_column_scale() -> None:
    payload = extended_order("SYNTH-ORDER-6")
    payload["payment"]["total_amount"] = "12.345"
    assert mapped(TikTokOrderSnapshot.model_validate(payload)).total == Decimal("12.35")


def product(**changes: Any) -> TikTokProductSnapshot:
    payload: dict[str, Any] = {
        "id": "SYNTH-PROD-1",
        "title": "Synthetic shirt",
        "status": "ACTIVATE",
        "update_time": 1_762_003_600,
        "main_images": [{"urls": ["https://img.example.test/a.jpg"]}],
        "skus": [
            {
                "id": "SKU-1",
                "seller_sku": "SELL-1",
                "price": {"sale_price": "19.9", "currency": "MYR"},
                "sales_attributes": [{"value_name": "Black"}, {"value_name": "M"}],
                "inventory": [{"quantity": 3}, {"quantity": 4}],
            },
            {
                "id": "SKU-2",
                "seller_sku": "",
                "price": {"sale_price": "21", "currency": "MYR"},
                "inventory": [],
            },
        ],
    }
    payload.update(changes)
    return TikTokProductSnapshot.model_validate(payload)


def test_one_listing_per_sku() -> None:
    rows = map_listings(CLIENT, STORE, product(), NOW)
    assert [(r.external_sku_id, r.variant_name, r.seller_sku, r.stock_qty) for r in rows] == [
        ("SKU-1", "Black / M", "SELL-1", 7),
        ("SKU-2", "", None, 0),
    ]
    first = rows[0]
    assert (first.client_id, first.store_id, first.channel) == (CLIENT, STORE, "tiktok_shop")
    assert (first.external_product_id, first.product_name) == ("SYNTH-PROD-1", "Synthetic shirt")
    assert first.price == Decimal("19.90")
    assert first.currency == "MYR"
    assert first.product_status == "ACTIVATE"
    assert first.provider_updated_at == datetime.fromtimestamp(1_762_003_600, UTC)
    assert first.synced_at == NOW


def test_product_without_skus_maps_to_no_listings() -> None:
    assert map_listings(CLIENT, STORE, product(skus=[]), NOW) == []


@pytest.mark.parametrize(
    ("url", "kept"),
    [
        ("https://img.example.test/a.jpg", True),
        ("http://img.example.test/a.jpg", False),
        ("javascript:alert(1)", False),
        ("data:image/png;base64,AAAA", False),
        ("//img.example.test/a.jpg", False),
        ("https:///no-host", False),
        ("https://img.example.test/a b.jpg", False),
        ("HTTPS://img.example.test/a.jpg", True),
    ],
)
def test_image_url_is_kept_only_when_https(url: str, kept: bool) -> None:
    rows = map_listings(CLIENT, STORE, product(main_images=[{"urls": [url]}]), NOW)
    assert rows[0].image_url == (url if kept else None)


def test_missing_image_is_none() -> None:
    assert map_listings(CLIENT, STORE, product(main_images=None), NOW)[0].image_url is None


def test_listing_price_out_of_range_is_rejected_safely() -> None:
    payload_skus = [
        {
            "id": "SKU-9",
            "price": {"sale_price": "10000000000", "currency": "MYR"},
            "inventory": [],
        }
    ]
    with pytest.raises(MappingError) as caught:
        map_listings(CLIENT, STORE, product(skus=payload_skus), NOW)
    assert caught.value.code == "amount_out_of_range"
