"""Shopee Open Platform platform adapter (draft)."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any
from uuid import UUID

from csa.agents.ecommerce.adapters.base import EcommercePlatformAdapter
from csa.agents.ecommerce.models import EcommerceOrder, InventorySyncItem


class ShopeeAdapter(EcommercePlatformAdapter):
    """Draft adapter for Shopee Open Platform API v2."""

    def __init__(self, partner_id: str, partner_key: str, shop_id: str, access_token: str) -> None:
        self.partner_id = partner_id
        self.partner_key = partner_key
        self.shop_id = shop_id
        self.access_token = access_token

    @property
    def platform_name(self) -> str:
        return "shopee"

    def sign_request(self, path: str, timestamp: int) -> str:
        """Calculates Shopee SHA256 signature using HMAC-SHA256."""
        base = f"{self.partner_id}{path}{timestamp}{self.access_token}{self.shop_id}"
        return hmac.new(
            self.partner_key.encode("utf-8"), base.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    async def get_order(self, client_id: UUID, order_id: str) -> EcommerceOrder:
        raise NotImplementedError(
            "Shopee adapter is in draft phase. Awaiting merchant credentials."
        )

    async def search_orders(
        self, client_id: UUID, status: str | None = None, limit: int = 10
    ) -> list[EcommerceOrder]:
        raise NotImplementedError("Shopee adapter is in draft phase.")

    async def get_product(self, client_id: UUID, sku: str) -> dict[str, Any]:
        raise NotImplementedError("Shopee adapter is in draft phase.")

    async def sync_inventory(
        self, client_id: UUID, items: list[InventorySyncItem]
    ) -> dict[str, Any]:
        raise NotImplementedError("Shopee adapter is in draft phase.")

    async def fulfill_order(
        self, client_id: UUID, order_id: str, tracking_number: str, carrier: str | None = None
    ) -> bool:
        raise NotImplementedError("Shopee adapter is in draft phase.")

    def verify_webhook_signature(self, raw_body: bytes, signature: str, app_secret: str) -> bool:
        expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
