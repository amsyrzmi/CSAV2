"""TikTok Shop Open API (v202309) platform adapter."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
from csa.agents.ecommerce.adapters.base import EcommercePlatformAdapter
from csa.agents.ecommerce.models import (
    EcommerceOrder,
    InventorySyncItem,
    OrderItemSnapshot,
    TikTokShopCredentials,
)

TIKTOK_SHOP_BASE_URL = "https://open-api.tiktokglobalshop.com"


class TikTokShopAdapter(EcommercePlatformAdapter):
    """Adapter for TikTok Shop Open API v202309."""

    def __init__(
        self,
        credentials: TikTokShopCredentials,
        http_client: httpx.AsyncClient | None = None,
        base_url: str = TIKTOK_SHOP_BASE_URL,
    ) -> None:
        self._creds = credentials
        self._client = http_client or httpx.AsyncClient(timeout=15.0)
        self._owns_client = http_client is None
        self._base_url = base_url.rstrip("/")

    @property
    def platform_name(self) -> str:
        return "tiktok_shop"

    def sign_request(self, path: str, query_params: dict[str, Any], body: str) -> str:
        """Calculates TikTok Shop HMAC-SHA256 request signature.

        Format: app_secret + path + sorted_params + body + app_secret
        """
        # Exclude 'sign' and 'access_token' from signature calculation
        filtered = {k: v for k, v in query_params.items() if k not in ("sign", "access_token")}
        sorted_pairs = "".join(f"{k}{filtered[k]}" for k in sorted(filtered.keys()))
        sign_string = f"{self._creds.app_secret}{path}{sorted_pairs}{body}{self._creds.app_secret}"
        return hmac.new(
            self._creds.app_secret.encode("utf-8"),
            sign_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def verify_webhook_signature(self, raw_body: bytes, signature: str, app_secret: str) -> bool:
        """Verifies HMAC-SHA256 signature on inbound webhook payloads."""
        expected = hmac.new(
            app_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def _build_url_and_params(self, path: str, body_str: str) -> tuple[str, dict[str, str]]:
        timestamp = str(int(time.time()))
        params: dict[str, str] = {
            "app_key": self._creds.app_key,
            "timestamp": timestamp,
            "shop_cipher": self._creds.shop_cipher,
        }
        sign = self.sign_request(path, params, body_str)
        params["sign"] = sign
        params["access_token"] = self._creds.access_token
        url = f"{self._base_url}{path}"
        return url, params

    async def get_order(self, client_id: UUID, order_id: str) -> EcommerceOrder:
        """Fetches order details by ID from TikTok Shop."""
        path = f"/order/202309/orders/{order_id}"
        url, params = self._build_url_and_params(path, "")
        headers = {"x-tts-access-token": self._creds.access_token}

        resp = await self._client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json().get("data", {})

        items = [
            OrderItemSnapshot(
                sku=item.get("seller_sku") or item.get("sku_id", "UNKNOWN"),
                item_name=item.get("product_name", "Product"),
                quantity=int(item.get("quantity", 1)),
                unit_price=Decimal(str(item.get("sku_sale_price", "0.00"))),
            )
            for item in data.get("line_items", [])
        ]

        payment = data.get("payment", {})
        total = Decimal(str(payment.get("total_amount", "0.00")))
        currency = payment.get("currency", "MYR")

        return EcommerceOrder(
            platform="tiktok_shop",
            platform_order_id=str(data.get("id") or order_id),
            order_status=data.get("status", "UNPAID"),
            buyer_identifier=data.get("buyer_uid"),
            total_amount=total,
            currency=currency,
            items=items,
            tracking_number=data.get("tracking_number"),
            shipping_carrier=data.get("shipping_provider"),
        )

    async def search_orders(
        self, client_id: UUID, status: str | None = None, limit: int = 10
    ) -> list[EcommerceOrder]:
        """Searches recent orders on TikTok Shop."""
        path = "/order/202309/orders/search"
        body_payload: dict[str, Any] = {"page_size": limit}
        if status:
            body_payload["order_status"] = status
        body_str = json.dumps(body_payload)

        url, params = self._build_url_and_params(path, body_str)
        headers = {
            "x-tts-access-token": self._creds.access_token,
            "Content-Type": "application/json",
        }

        resp = await self._client.post(url, params=params, headers=headers, content=body_str)
        resp.raise_for_status()
        orders_data = resp.json().get("data", {}).get("orders", [])

        orders: list[EcommerceOrder] = []
        for raw in orders_data:
            items = [
                OrderItemSnapshot(
                    sku=item.get("seller_sku") or item.get("sku_id", "UNKNOWN"),
                    item_name=item.get("product_name", "Product"),
                    quantity=int(item.get("quantity", 1)),
                    unit_price=Decimal(str(item.get("sku_sale_price", "0.00"))),
                )
                for item in raw.get("line_items", [])
            ]
            payment = raw.get("payment", {})
            orders.append(
                EcommerceOrder(
                    platform="tiktok_shop",
                    platform_order_id=str(raw.get("id", "")),
                    order_status=raw.get("status", "UNKNOWN"),
                    buyer_identifier=raw.get("buyer_uid"),
                    total_amount=Decimal(str(payment.get("total_amount", "0.00"))),
                    currency=payment.get("currency", "MYR"),
                    items=items,
                    tracking_number=raw.get("tracking_number"),
                )
            )
        return orders

    async def get_product(self, client_id: UUID, sku: str) -> dict[str, Any]:
        """Queries product details by seller SKU."""
        path = "/product/202309/products/search"
        body_str = json.dumps({"seller_sku_list": [sku]})
        url, params = self._build_url_and_params(path, body_str)
        headers = {
            "x-tts-access-token": self._creds.access_token,
            "Content-Type": "application/json",
        }
        resp = await self._client.post(url, params=params, headers=headers, content=body_str)
        resp.raise_for_status()
        return resp.json().get("data", {})  # type: ignore[no-any-return]

    async def sync_inventory(
        self, client_id: UUID, items: list[InventorySyncItem]
    ) -> dict[str, Any]:
        """Pushes stock quantity updates to TikTok Shop."""
        path = "/product/202309/products/stocks/update"
        skus_payload = [
            {"seller_sku": item.sku, "available_stock": item.available_qty} for item in items
        ]
        body_str = json.dumps({"skus": skus_payload})
        url, params = self._build_url_and_params(path, body_str)
        headers = {
            "x-tts-access-token": self._creds.access_token,
            "Content-Type": "application/json",
        }
        resp = await self._client.post(url, params=params, headers=headers, content=body_str)
        resp.raise_for_status()
        return resp.json().get("data", {})  # type: ignore[no-any-return]

    async def fulfill_order(
        self, client_id: UUID, order_id: str, tracking_number: str, carrier: str | None = None
    ) -> bool:
        """Confirms package shipment and dispatches carrier tracking code."""
        path = f"/fulfillment/202309/orders/{order_id}/ship"
        body_str = json.dumps(
            {
                "tracking_number": tracking_number,
                "shipping_provider": carrier or "Standard Delivery",
            }
        )
        url, params = self._build_url_and_params(path, body_str)
        headers = {
            "x-tts-access-token": self._creds.access_token,
            "Content-Type": "application/json",
        }
        resp = await self._client.post(url, params=params, headers=headers, content=body_str)
        resp.raise_for_status()
        return bool(resp.json().get("code") == 0)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
