"""Tests for E-Commerce Operations agent spoke and TikTok Shop adapter."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest
from csa.agents.base import AgentContext
from csa.agents.ecommerce import (
    EcommerceAgent,
    EcommerceInput,
    InventorySyncItem,
    TikTokShopAdapter,
    TikTokShopCredentials,
)
from csa.contracts.callbacks import PendingActionStore


def _sample_creds() -> TikTokShopCredentials:
    return TikTokShopCredentials(
        app_key="test_app_key",
        app_secret="test_app_secret_123456",
        access_token="test_access_token_xyz",
        shop_cipher="test_shop_cipher_my",
    )


def test_tiktok_shop_signing_and_webhook_verification() -> None:
    """Verifies HMAC-SHA256 signature calculation and webhook verification."""
    creds = _sample_creds()
    adapter = TikTokShopAdapter(creds)

    # 1. Request signature calculation
    path = "/order/202309/orders/search"
    query_params = {
        "timestamp": "1700000000",
        "app_key": creds.app_key,
        "shop_cipher": creds.shop_cipher,
        "sign": "ignore_me",
        "access_token": "ignore_me_too",
    }
    body = '{"page_size":10}'
    sign1 = adapter.sign_request(path, query_params, body)
    assert isinstance(sign1, str)
    assert len(sign1) == 64  # SHA256 hex length

    # Signature must be deterministic
    sign2 = adapter.sign_request(path, query_params, body)
    assert sign1 == sign2

    # Different body produces different signature
    sign3 = adapter.sign_request(path, query_params, '{"page_size":20}')
    assert sign1 != sign3

    # 2. Webhook signature verification
    raw_payload = b'{"event":"ORDER_STATUS_CHANGE","order_id":"12345"}'
    # Custom test of verify_webhook_signature
    import hashlib
    import hmac

    expected_sig = hmac.new(
        creds.app_secret.encode("utf-8"), raw_payload, hashlib.sha256
    ).hexdigest()
    assert adapter.verify_webhook_signature(raw_payload, expected_sig, creds.app_secret) is True
    assert (
        adapter.verify_webhook_signature(raw_payload, "invalid_sig_hex", creds.app_secret) is False
    )


def test_ecommerce_agent_risk_classification() -> None:
    """Verifies that all e-commerce actions have declared risk classes."""
    agent = EcommerceAgent()

    # Read operations
    assert agent.risk(EcommerceInput(action="get_order", order_id="TK1001")) == "read"
    assert (
        agent.risk(EcommerceInput(action="search_orders", status_filter="AWAITING_SHIPMENT"))
        == "read"
    )
    assert agent.risk(EcommerceInput(action="get_product", sku="SKU-TEST-1")) == "read"

    # Write / irreversible operations
    assert (
        agent.risk(
            EcommerceInput(
                action="sync_inventory",
                inventory_items=[InventorySyncItem(sku="SKU-1", available_qty=10)],
            )
        )
        == "commit"
    )
    assert (
        agent.risk(
            EcommerceInput(action="fulfill_order", order_id="TK1001", tracking_number="TRACK-999")
        )
        == "commit"
    )
    assert agent.risk(EcommerceInput(action="cancel_order", order_id="TK1001")) == "commit"
    assert agent.risk(EcommerceInput(action="approve_refund", order_id="TK1001")) == "commit"


@pytest.mark.asyncio
async def test_ecommerce_agent_get_order_and_search_mocked() -> None:
    """Verifies EcommerceAgent dispatches to TikTokShopAdapter and normalizes orders."""
    creds = _sample_creds()

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "/order/202309/orders/search" in url_str:
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "message": "Success",
                    "data": {
                        "orders": [
                            {
                                "id": "TK-ORD-001",
                                "status": "AWAITING_SHIPMENT",
                                "buyer_uid": "user_my_123",
                                "payment": {"total_amount": "89.90", "currency": "MYR"},
                                "line_items": [
                                    {
                                        "seller_sku": "SKU-SHIRT-BLK-L",
                                        "product_name": "Cotton T-Shirt",
                                        "quantity": 2,
                                        "sku_sale_price": "44.95",
                                    }
                                ],
                            }
                        ]
                    },
                },
            )
        if request.url.path == "/order/202309/orders":
            assert request.url.params["ids"] == "TK-ORD-001"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "orders": [
                            {
                                "id": "TK-ORD-001",
                                "status": "AWAITING_SHIPMENT",
                                "buyer_uid": "user_my_123",
                                "payment": {"total_amount": "89.90", "currency": "MYR"},
                                "line_items": [
                                    {
                                        "seller_sku": "SKU-SHIRT-BLK-L",
                                        "product_name": "Cotton T-Shirt",
                                        "quantity": 2,
                                        "sku_sale_price": "44.95",
                                    }
                                ],
                            }
                        ]
                    },
                },
            )
        return httpx.Response(404, json={"code": 404, "message": "Not Found"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = TikTokShopAdapter(creds, http_client=client)
    agent = EcommerceAgent()
    agent.register_adapter(adapter)

    ctx = AgentContext(
        client_id=uuid4(),
        contact_id=uuid4(),
        trace_id=uuid4(),
        conversation_id=uuid4(),
        idempotency_key="idem-ecom-1",
        channel="tiktok_shop",
        pool=MagicMock(),
        pending_actions=MagicMock(spec=PendingActionStore),
    )

    # 1. get_order
    res_get = await agent.run(ctx, EcommerceInput(action="get_order", order_id="TK-ORD-001"))
    assert res_get.ok is True
    assert res_get.facts is not None
    assert len(res_get.facts) == 1
    fact = res_get.facts[0]
    assert fact.id == "ecom_order_TK-ORD-001"
    assert "TK-ORD-001" in fact.text
    assert "89.90" in fact.text
    assert "SKU-SHIRT-BLK-L" in fact.text

    # 2. search_orders
    res_search = await agent.run(
        ctx, EcommerceInput(action="search_orders", status_filter="AWAITING_SHIPMENT")
    )
    assert res_search.ok is True
    assert res_search.facts is not None
    assert len(res_search.facts) == 1
    assert "TK-ORD-001" in res_search.facts[0].text

    await client.aclose()


@pytest.mark.asyncio
async def test_ecommerce_agent_inventory_sync_and_fulfillment() -> None:
    """Verifies inventory synchronization and order shipment fulfillment."""
    creds = _sample_creds()

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "/product/202309/products/stocks/update" in url_str:
            return httpx.Response(
                200, json={"code": 0, "data": {"updated_skus": ["SKU-A", "SKU-B"]}}
            )
        if "/fulfillment/202309/orders/TK-ORD-002/ship" in url_str:
            return httpx.Response(200, json={"code": 0, "message": "Success"})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = TikTokShopAdapter(creds, http_client=client)
    agent = EcommerceAgent()
    agent.register_adapter(adapter)

    ctx = AgentContext(
        client_id=uuid4(),
        contact_id=uuid4(),
        trace_id=uuid4(),
        conversation_id=uuid4(),
        idempotency_key="idem-ecom-2",
        channel="tiktok_shop",
        pool=MagicMock(),
        pending_actions=MagicMock(spec=PendingActionStore),
    )

    # 1. sync_inventory
    items = [
        InventorySyncItem(sku="SKU-A", available_qty=25),
        InventorySyncItem(sku="SKU-B", available_qty=10),
    ]
    res_sync = await agent.run(ctx, EcommerceInput(action="sync_inventory", inventory_items=items))
    assert res_sync.ok is True
    assert res_sync.facts is not None
    assert res_sync.facts[0].id == "ecom_sync_tiktok_shop"
    assert "Synced 2 items" in res_sync.facts[0].text

    # 2. fulfill_order
    res_ship = await agent.run(
        ctx,
        EcommerceInput(
            action="fulfill_order",
            order_id="TK-ORD-002",
            tracking_number="MY-JNT-987654",
            shipping_carrier="J&T Express",
        ),
    )
    assert res_ship.ok is True
    assert res_ship.facts is not None
    assert res_ship.facts[0].id == "ecom_ship_TK-ORD-002"
    assert "fulfilled with tracking MY-JNT-987654" in res_ship.facts[0].text

    # 3. High risk actions require human approval
    res_cancel = await agent.run(ctx, EcommerceInput(action="cancel_order", order_id="TK-ORD-002"))
    assert res_cancel.ok is False
    assert res_cancel.error is not None
    assert res_cancel.error.code == "action_requires_approval"
    assert res_cancel.requires_human is True

    await client.aclose()
