"""Data contracts and schemas for the E-Commerce Operations agent."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MarketplacePlatform = Literal["tiktok_shop", "shopee", "lazada"]

EcommerceAction = Literal[
    "get_order",
    "search_orders",
    "get_product",
    "sync_inventory",
    "fulfill_order",
    "cancel_order",
    "approve_refund",
]


class OrderItemSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str = Field(min_length=1, max_length=64)
    item_name: str = Field(min_length=1, max_length=255)
    quantity: int = Field(ge=1)
    unit_price: Decimal = Field(ge=0)


class EcommerceOrder(BaseModel):
    """Normalized cross-marketplace order structure."""

    model_config = ConfigDict(extra="ignore")
    platform: MarketplacePlatform
    platform_order_id: str
    order_status: str
    buyer_identifier: str | None = None
    total_amount: Decimal
    currency: str = "MYR"
    items: list[OrderItemSnapshot] = Field(default_factory=list)
    tracking_number: str | None = None
    shipping_carrier: str | None = None
    created_at: datetime | None = None


class InventorySyncItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str = Field(min_length=1, max_length=64)
    available_qty: int = Field(ge=0)


class TikTokShopCredentials(BaseModel):
    """Credentials for TikTok Shop Open API integration."""

    model_config = ConfigDict(extra="ignore")
    app_key: str
    app_secret: str
    access_token: str
    refresh_token: str | None = None
    shop_cipher: str
    shop_id: str | None = None


class EcommerceInput(BaseModel):
    """Input payload for the E-Commerce Operations agent spoke."""

    model_config = ConfigDict(extra="forbid")
    platform: MarketplacePlatform = "tiktok_shop"
    action: EcommerceAction
    order_id: str | None = Field(default=None, max_length=100)
    sku: str | None = Field(default=None, max_length=64)
    status_filter: str | None = None
    inventory_items: list[InventorySyncItem] = Field(default_factory=list, max_length=100)
    tracking_number: str | None = Field(default=None, max_length=100)
    shipping_carrier: str | None = Field(default=None, max_length=100)
    cancel_reason: str | None = Field(default=None, max_length=255)
    limit: int = Field(default=10, ge=1, le=50)

    @model_validator(mode="after")
    def validate_action_fields(self) -> EcommerceInput:
        if self.action in ("get_order", "cancel_order", "approve_refund") and not self.order_id:
            raise ValueError(f"Action '{self.action}' requires 'order_id'")
        if self.action == "get_product" and not self.sku:
            raise ValueError("Action 'get_product' requires 'sku'")
        if self.action == "sync_inventory" and not self.inventory_items and not self.sku:
            raise ValueError("Action 'sync_inventory' requires either 'inventory_items' or 'sku'")
        if self.action == "fulfill_order" and (not self.order_id or not self.tracking_number):
            raise ValueError(
                "Action 'fulfill_order' requires both 'order_id' and 'tracking_number'"
            )
        return self
