"""Data contracts and schemas for the E-Commerce Operations agent."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from csa.core.pii import NUMBER_PLACEHOLDER

MarketplacePlatform = Literal["tiktok_shop", "shopee", "lazada"]

# Spec 6.7 [4 Nov]: the customer-facing spoke is read-only. These are the only two actions the
# planner and the spoke accept, so a customer message can never plan a marketplace write. The
# adapters' write methods (and the Shopee/Lazada adapters) stay in the code but unreachable.
EcommerceAction = Literal["order_status", "stock_check"]
# A TikTok order number as the customer types it: a plain token, never a free-text query.
PLAIN_ORDER_NUMBER_PATTERN = r"^[A-Za-z0-9_-]{1,100}$"
# The planner only sees redacted text: a number that looks like a card or a phone number reaches
# it as "[CARD_0]" / "[PHONE_0]" (csa.core.pii). order_status accepts that placeholder; the turn
# dependencies resolve it from the raw message before the lookup (never shown to the model).
ORDER_NUMBER_PLACEHOLDER_PATTERN = rf"^{NUMBER_PLACEHOLDER}$"
ORDER_NUMBER_PATTERN = rf"^(?:[A-Za-z0-9_-]{{1,100}}|{NUMBER_PLACEHOLDER})$"


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


class EcommerceOrderPage(BaseModel):
    """One provider page, not evidence of a complete or synchronized order history."""

    model_config = ConfigDict(extra="forbid")
    items: list[EcommerceOrder]
    next_page_token: str | None = Field(default=None, min_length=1, max_length=2048)


class TikTokShopCredentials(BaseModel):
    """Credentials for TikTok Shop Open API integration."""

    model_config = ConfigDict(extra="ignore")
    app_key: str
    app_secret: str
    access_token: str
    refresh_token: str | None = None
    shop_cipher: str | None = None
    shop_id: str | None = None


class EcommerceInput(BaseModel):
    """What the planner may ask of the customer-facing E-commerce spoke (TikTok Shop only)."""

    model_config = ConfigDict(extra="forbid")
    action: EcommerceAction
    order_number: str | None = Field(
        default=None, min_length=1, max_length=100, pattern=ORDER_NUMBER_PATTERN
    )
    query: str | None = Field(default=None, max_length=200)

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not 1 <= len(value) <= 100:
            raise ValueError("query must be 1 to 100 characters")
        return value

    @model_validator(mode="after")
    def validate_action_fields(self) -> EcommerceInput:
        if self.action == "order_status" and (self.order_number is None or self.query):
            raise ValueError("order_status needs order_number only")
        if self.action == "stock_check" and (self.query is None or self.order_number):
            raise ValueError("stock_check needs query only")
        return self


class OrderStatusFact(BaseModel):
    """All a customer learns from a TikTok order number (owner decision, spec 6.7).

    Never the buyer's name, phone or address, the items, the totals or the ship-to.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    status_text: str = Field(min_length=1)
    tracking_no: str | None = None
    carrier: str | None = None
    shipped_or_delivered_on: date | None = None


class OrderLookup(BaseModel):
    """found carries the fact; not_found and limited carry nothing."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    outcome: Literal["found", "not_found", "limited"]
    fact: OrderStatusFact | None = None

    @model_validator(mode="after")
    def fact_only_when_found(self) -> OrderLookup:
        if (self.outcome == "found") != (self.fact is not None):
            raise ValueError("a fact comes with found only")
        return self


class SkuStock(BaseModel):
    """One SKU of a TikTok product: the count and price, live or from our copy."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    listing_id: UUID | None
    external_sku_id: str
    variant_name: str
    seller_sku: str | None
    stock_qty: int | None
    price: Decimal | None
    currency: str


class LiveStock(BaseModel):
    """Stock of the product a listing belongs to, without any customer wording.

    ``checked_live`` is True when TikTok answered just now (``as_of`` is that time); otherwise
    the counts are our stored copy and ``as_of`` is when the asked listing was last synced.
    ``sellable`` is whether the product's status (live, or in the copy) is one we may offer.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    listing_id: UUID
    product_name: str
    skus: tuple[SkuStock, ...]
    checked_live: bool
    as_of: datetime
    sellable: bool

    def asked(self) -> SkuStock | None:
        """The SKU of the asked listing, or None when TikTok no longer lists it."""
        return next((sku for sku in self.skus if sku.listing_id == self.listing_id), None)


class ListingChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    listing_id: UUID
    product_name: str
    variant_name: str
    seller_sku: str | None


class StockLookup(BaseModel):
    """match carries the stock, choices up to five listings, not_found nothing."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    outcome: Literal["match", "choices", "not_found"]
    stock: LiveStock | None = None
    choices: tuple[ListingChoice, ...] = Field(default=(), max_length=5)

    @model_validator(mode="after")
    def shape_matches_outcome(self) -> StockLookup:
        if (self.outcome == "match") != (self.stock is not None):
            raise ValueError("stock comes with match only")
        if (self.outcome == "choices") != bool(self.choices):
            raise ValueError("choices come with choices only")
        return self
