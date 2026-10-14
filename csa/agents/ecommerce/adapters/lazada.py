"""Lazada Open Platform platform adapter (draft)."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any
from uuid import UUID

from csa.agents.ecommerce.adapters.base import EcommercePlatformAdapter
from csa.agents.ecommerce.models import EcommerceOrder, InventorySyncItem


class LazadaAdapter(EcommercePlatformAdapter):
    """Draft adapter for Lazada Open Platform API."""

    def __init__(self, app_key: str, app_secret: str, access_token: str) -> None:
        self.app_key = app_key
        self.app_secret = app_secret
        self.access_token = access_token

    @property
    def platform_name(self) -> str:
        return "lazada"

    def sign_request(self, api_name: str, params: dict[str, Any]) -> str:
        """Calculates Lazada SHA256 signature."""
        sorted_pairs = "".join(f"{k}{params[k]}" for k in sorted(params.keys()))
        sign_string = f"{api_name}{sorted_pairs}"
        return (
            hmac.new(self.app_secret.encode("utf-8"), sign_string.encode("utf-8"), hashlib.sha256)
            .hexdigest()
            .upper()
        )

    async def get_order(self, client_id: UUID, order_id: str) -> EcommerceOrder:
        raise NotImplementedError(
            "Lazada adapter is in draft phase. Awaiting merchant credentials."
        )

    async def search_orders(
        self, client_id: UUID, status: str | None = None, limit: int = 10
    ) -> list[EcommerceOrder]:
        raise NotImplementedError("Lazada adapter is in draft phase.")

    async def get_product(self, client_id: UUID, sku: str) -> dict[str, Any]:
        raise NotImplementedError("Lazada adapter is in draft phase.")

    async def sync_inventory(
        self, client_id: UUID, items: list[InventorySyncItem]
    ) -> dict[str, Any]:
        raise NotImplementedError("Lazada adapter is in draft phase.")

    async def fulfill_order(
        self, client_id: UUID, order_id: str, tracking_number: str, carrier: str | None = None
    ) -> bool:
        raise NotImplementedError("Lazada adapter is in draft phase.")

    def verify_webhook_signature(self, raw_body: bytes, signature: str, app_secret: str) -> bool:
        expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
