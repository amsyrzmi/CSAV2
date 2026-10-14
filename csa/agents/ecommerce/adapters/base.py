"""Base protocol for marketplace platform adapters."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from csa.agents.ecommerce.models import EcommerceOrder, InventorySyncItem


class EcommercePlatformAdapter(Protocol):
    """Protocol implemented by marketplace platform integrations (TikTok Shop, Shopee, Lazada)."""

    @property
    def platform_name(self) -> str: ...

    async def get_order(self, client_id: UUID, order_id: str) -> EcommerceOrder: ...

    async def search_orders(
        self, client_id: UUID, status: str | None = None, limit: int = 10
    ) -> list[EcommerceOrder]: ...

    async def get_product(self, client_id: UUID, sku: str) -> dict[str, Any]: ...

    async def sync_inventory(
        self, client_id: UUID, items: list[InventorySyncItem]
    ) -> dict[str, Any]: ...

    async def fulfill_order(
        self, client_id: UUID, order_id: str, tracking_number: str, carrier: str | None = None
    ) -> bool: ...

    def verify_webhook_signature(
        self, raw_body: bytes, signature: str, app_secret: str
    ) -> bool: ...
