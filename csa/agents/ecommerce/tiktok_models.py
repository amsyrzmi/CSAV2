"""Selected v202309 provider fields, not normalized commerce or browser contracts."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from pydantic import (
    AliasChoices,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# Provider field mapping for the v202309 order payload, in one place.
# VERIFY IN PARTNER CENTER (ledger Task 21): every name below is from the v202309 order
# docs as remembered and from synthetic fixtures, not from a live shop.
#
#   order.create_time / update_time / paid_time   unix seconds -> create_time /
#                                                    update_time / paid_at
#   order.payment.sub_total                       -> payment.sub_total
#   order.payment.shipping_fee                    -> payment.shipping_fee
#   order.payment.seller_discount                 -> payment.discount (seller-funded only;
#                                                    platform_discount is not folded in)
#   order.recipient_address (object; may be masked, may arrive as a non-object)
#       .name / .phone_number / .full_address / .region_code
#                                                 -> shipping.recipient_name / phone /
#                                                    address_text / region
#   order.packages[].tracking_number              -> packages[].tracking_number
#   order.packages[].shipping_provider_name       -> packages[].carrier
#   order.tracking_number / order.shipping_provider
#                                                 -> one package, only when no package carries
#                                                    a tracking number (fallback)
#
# Our own serialized names are accepted too, so a snapshot can be rehydrated from its dump.

# Provider field mapping for the v202309 product payload (search results and detail), in one
# place. VERIFY IN PARTNER CENTER (ledger Task 21): every name below is from the v202309
# product docs as remembered and from synthetic fixtures, not from a live shop.
#
#   product.id / title / status                    -> id / name / status
#   product.main_images[0].urls[0]                 -> image_url (first image only; none -> None)
#   product.update_time                            unix seconds -> update_time
#   product.skus[].id / seller_sku                 -> skus[].id / seller_sku ("" -> None)
#   product.skus[].price.sale_price / .currency    -> skus[].price / currency
#   product.skus[].sales_attributes[].value_name   -> skus[].variant_name, joined " / "
#                                                     ("Black / M"; no attributes -> "")
#   product.skus[].inventory[].quantity            -> skus[].stock_qty, the SUM across
#                                                     warehouses (whole numbers, each >= 0;
#                                                     an absent inventory list is rejected,
#                                                     an empty one is zero stock)
#
# Our own serialized names are accepted here too, so a dump re-validates.

# A provider product ID goes into the signed request path, so it is a plain token, never a path.
PRODUCT_ID_PATTERN = r"[A-Za-z0-9_-]{1,100}"
_PRODUCT_ID = re.compile(PRODUCT_ID_PATTERN)


def is_safe_product_id(value: str) -> bool:
    return _PRODUCT_ID.fullmatch(value) is not None


class _TikTokSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)


class TikTokPaymentSnapshot(_TikTokSnapshot):
    total_amount: Decimal = Field(ge=0, allow_inf_nan=False)
    currency: str = Field(min_length=3, max_length=3)
    sub_total: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    shipping_fee: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    discount: Decimal | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
        validation_alias=AliasChoices("seller_discount", "discount"),
    )


def _cut(value: Any, limit: int) -> str | None:
    """Free text from the provider is never worth failing a whole page: cut it or drop it."""
    return value[:limit] if isinstance(value, str) else None


class TikTokShippingSnapshot(_TikTokSnapshot):
    """Ship-to as TikTok gives it; TikTok often masks or omits every field.

    Text fields are truncated to the cap (non-text is ignored) instead of failing the order.
    """

    recipient_name: str | None = Field(
        default=None, max_length=255, validation_alias=AliasChoices("name", "recipient_name")
    )
    phone: str | None = Field(
        default=None, max_length=100, validation_alias=AliasChoices("phone_number", "phone")
    )
    address_text: str | None = Field(
        default=None, max_length=2000, validation_alias=AliasChoices("full_address", "address_text")
    )
    region: str | None = Field(
        default=None, max_length=100, validation_alias=AliasChoices("region_code", "region")
    )

    @field_validator("recipient_name", mode="before")
    @classmethod
    def _cap_name(cls, value: Any) -> str | None:
        return _cut(value, 255)

    @field_validator("phone", "region", mode="before")
    @classmethod
    def _cap_short_text(cls, value: Any) -> str | None:
        return _cut(value, 100)

    @field_validator("address_text", mode="before")
    @classmethod
    def _cap_address(cls, value: Any) -> str | None:
        return _cut(value, 2000)


class TikTokPackageSnapshot(_TikTokSnapshot):
    """Tracking text is truncated to the cap (non-text is ignored) instead of failing the order."""

    tracking_number: str | None = Field(default=None, max_length=255)
    carrier: str | None = Field(
        default=None,
        max_length=255,
        validation_alias=AliasChoices("shipping_provider_name", "carrier"),
    )

    @field_validator("tracking_number", "carrier", mode="before")
    @classmethod
    def _cap_text(cls, value: Any) -> str | None:
        return _cut(value, 255)


class TikTokLineItemSnapshot(_TikTokSnapshot):
    """A distinct provider line; sale_price does not assert a quantity or unit price."""

    id: str = Field(min_length=1, max_length=100)
    sku_id: str = Field(min_length=1, max_length=100)
    seller_sku: str | None = Field(default=None, max_length=255)
    product_name: str = Field(min_length=1, max_length=255)
    sale_price: Decimal = Field(ge=0, allow_inf_nan=False)

    @field_validator("product_name", mode="before")
    @classmethod
    def _cap_name(cls, value: Any) -> Any:
        # Too long is cut; empty or non-text still fails (a line needs a name).
        return value[:255] if isinstance(value, str) else value

    @field_validator("seller_sku", mode="before")
    @classmethod
    def _cap_seller_sku(cls, value: Any) -> str | None:
        return _cut(value, 255)


class TikTokOrderSnapshot(_TikTokSnapshot):
    id: str = Field(min_length=1, max_length=100)
    status: str = Field(min_length=1, max_length=100)
    payment: TikTokPaymentSnapshot
    line_items: tuple[TikTokLineItemSnapshot, ...]
    create_time: AwareDatetime | None = None
    update_time: AwareDatetime | None = None
    paid_at: AwareDatetime | None = Field(
        default=None, validation_alias=AliasChoices("paid_time", "paid_at")
    )
    shipping: TikTokShippingSnapshot | None = Field(
        default=None, validation_alias=AliasChoices("recipient_address", "shipping")
    )
    packages: tuple[TikTokPackageSnapshot, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def fall_back_to_order_level_tracking(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        packages = data.get("packages")
        if packages is None:
            packages = []
        if not isinstance(packages, list | tuple):
            return data  # field validation rejects it
        has_tracking = any(isinstance(p, dict) and p.get("tracking_number") for p in packages)
        tracking = data.get("tracking_number")
        if has_tracking or not isinstance(tracking, str) or not tracking:
            return data
        carrier = data.get("shipping_provider")
        return {
            **data,
            "packages": [
                *packages,
                {
                    "tracking_number": tracking,
                    "shipping_provider_name": carrier if isinstance(carrier, str) else None,
                },
            ],
        }

    @field_validator("shipping", mode="before")
    @classmethod
    def ignore_non_object_address(cls, value: Any) -> Any:
        return value if isinstance(value, dict | TikTokShippingSnapshot) else None

    @model_validator(mode="after")
    def require_distinct_lines(self) -> TikTokOrderSnapshot:
        ids = [line.id for line in self.line_items]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate provider line identity")
        return self


class TikTokOrderSnapshotPage(_TikTokSnapshot):
    items: tuple[TikTokOrderSnapshot, ...]
    next_page_token: str | None = Field(default=None, min_length=1, max_length=2048)


# listings.stock_qty is an int4 column; a larger total is invalid data, not a database error.
_INT4_MAX = 2_147_483_647


def _sum_warehouse_stock(inventory: Any) -> int:
    if not isinstance(inventory, list | tuple):
        raise ValueError("inventory must be a list")
    total = 0
    for entry in inventory:
        quantity = entry.get("quantity") if isinstance(entry, dict) else None
        if type(quantity) is not int or quantity < 0:
            raise ValueError("warehouse quantity must be a whole number, not below zero")
        total += quantity
    return total


def _variant_label(attributes: Any) -> str:
    if not isinstance(attributes, list | tuple):
        raise ValueError("sales attributes must be a list")
    names = [a.get("value_name") for a in attributes if isinstance(a, dict)]
    return " / ".join(name for name in names if isinstance(name, str) and name)


def _first_image_url(images: Any) -> str | None:
    """Images are cosmetic: anything unexpected is no image, not a failed read."""
    if not isinstance(images, list | tuple) or not images or not isinstance(images[0], dict):
        return None
    urls = images[0].get("urls")
    if not isinstance(urls, list | tuple) or not urls or not isinstance(urls[0], str):
        return None
    return urls[0] or None


class TikTokSkuSnapshot(_TikTokSnapshot):
    """One variant; stock_qty is the live total across warehouses, never below zero."""

    id: str = Field(min_length=1, max_length=100)
    seller_sku: str | None = Field(default=None, max_length=255)
    variant_name: str = Field(default="", max_length=255)
    price: Decimal = Field(ge=0, allow_inf_nan=False)
    currency: str = Field(min_length=3, max_length=3)
    stock_qty: int = Field(strict=True, ge=0, le=_INT4_MAX)

    @model_validator(mode="before")
    @classmethod
    def map_provider_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        mapped = dict(data)
        price = mapped.get("price")
        if isinstance(price, dict):
            mapped["price"] = price.get("sale_price")
            mapped.setdefault("currency", price.get("currency"))
        if "variant_name" not in mapped and "sales_attributes" in mapped:
            mapped["variant_name"] = _variant_label(mapped["sales_attributes"])
        if "stock_qty" not in mapped and "inventory" in mapped:
            mapped["stock_qty"] = _sum_warehouse_stock(mapped["inventory"])
        return mapped

    @field_validator("seller_sku", mode="after")
    @classmethod
    def empty_seller_sku_is_none(cls, value: str | None) -> str | None:
        return value or None


class TikTokProductSnapshot(_TikTokSnapshot):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=255, validation_alias=AliasChoices("title", "name"))
    status: str = Field(min_length=1, max_length=100)
    image_url: str | None = Field(default=None, max_length=2048)
    skus: tuple[TikTokSkuSnapshot, ...]
    update_time: AwareDatetime | None = None

    @model_validator(mode="before")
    @classmethod
    def map_provider_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "image_url" in data:
            return data
        return {**data, "image_url": _first_image_url(data.get("main_images"))}

    @model_validator(mode="after")
    def require_distinct_skus(self) -> TikTokProductSnapshot:
        ids = [sku.id for sku in self.skus]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate provider SKU identity")
        return self


class TikTokProductSnapshotPage(_TikTokSnapshot):
    items: tuple[TikTokProductSnapshot, ...]
    next_page_token: str | None = Field(default=None, min_length=1, max_length=2048)
