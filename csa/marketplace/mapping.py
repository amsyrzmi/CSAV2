"""Pure mapping of TikTok snapshots into local order and listing rows (spec 6.3).

No I/O. Buyer details (recipient name, phone, address) go only into ``ship_to``; nothing else
carries them. Amounts that do not fit ``numeric(12,2)`` raise :class:`MappingError` with a fixed
code and message, never the value.
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from csa.agents.ecommerce import TikTokOrderSnapshot, TikTokProductSnapshot

CHANNEL = "tiktok_shop"

# Spec 6.3. Anything not listed (including a status TikTok adds later) is `processing`, and the
# raw word stays visible in `provider_status`.
_STATUS: dict[str, str] = {
    "UNPAID": "pending_confirmation",
    "ON_HOLD": "processing",
    "AWAITING_SHIPMENT": "processing",
    "AWAITING_COLLECTION": "processing",
    "PARTIALLY_SHIPPING": "shipped",
    "IN_TRANSIT": "shipped",
    "DELIVERED": "delivered",
    "COMPLETED": "delivered",
    "CANCELLED": "cancelled",
}
_UNKNOWN_STATUS = "processing"
# Statuses that only exist after payment, so the order is paid even if no paid time arrived.
_PAID_STATUSES = frozenset(
    {
        "ON_HOLD",
        "AWAITING_SHIPMENT",
        "AWAITING_COLLECTION",
        "PARTIALLY_SHIPPING",
        "IN_TRANSIT",
        "DELIVERED",
        "COMPLETED",
    }
)

_MONEY_CENT = Decimal("0.01")
_MONEY_LIMIT = Decimal(10) ** 10  # numeric(12,2) holds up to 9999999999.99


class MappingError(ValueError):
    """A snapshot cannot be stored as given. The message is the code: no provider values."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def map_status(provider_status: str, *, paid: bool) -> tuple[str, str]:
    """Return ``(local_status, payment_status)`` for a TikTok order status."""
    local = _STATUS.get(provider_status.strip().upper(), _UNKNOWN_STATUS)
    return local, "paid" if paid else "unpaid"


def _money(value: Decimal) -> Decimal:
    """Round half-up to the column's cents (sub-cent is rounded, not rejected); refuse (never
    clip) what cannot fit the column."""
    if abs(value) >= _MONEY_LIMIT:
        raise MappingError("amount_out_of_range")
    quantized = value.quantize(_MONEY_CENT, rounding=ROUND_HALF_UP)
    if abs(quantized) >= _MONEY_LIMIT:
        raise MappingError("amount_out_of_range")
    return quantized


class MappedLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    sku: str
    name_snapshot: str
    unit_price: Decimal
    qty: int
    currency: str
    external_line_id: str


class MappedOrder(BaseModel):
    """Columns of one ``orders`` row plus its folded lines. ``contact_id`` is always NULL."""

    model_config = ConfigDict(frozen=True)

    id: str
    client_id: UUID
    store_id: UUID
    channel: str
    external_order_id: str
    external_shop_id: str
    status: str
    payment_status: str
    provider_status: str
    total: Decimal
    currency: str
    subtotal: Decimal
    shipping_fee: Decimal
    discount: Decimal
    placed_at: datetime | None
    shipped_at: datetime | None
    tracking_no: str | None
    carrier: str | None
    # The only place recipient name, phone and address are stored.
    ship_to: dict[str, str] | None
    synced_at: datetime
    provider_updated_at: datetime | None
    lines: tuple[MappedLine, ...]


class MappedListing(BaseModel):
    model_config = ConfigDict(frozen=True)

    client_id: UUID
    store_id: UUID
    channel: str
    external_product_id: str
    external_sku_id: str
    product_name: str
    variant_name: str
    seller_sku: str | None
    price: Decimal
    currency: str
    stock_qty: int
    product_status: str
    image_url: str | None
    provider_updated_at: datetime | None
    synced_at: datetime


def _fold_lines(snap: TikTokOrderSnapshot) -> tuple[MappedLine, ...]:
    """TikTok 202309 lists one line per unit: lines of one SKU at one price fold into a single
    line with qty = count. A different price for the same SKU stays its own line."""
    folded: dict[tuple[str, Decimal], MappedLine] = {}
    for line in snap.line_items:
        unit_price = _money(line.sale_price)
        key = (line.sku_id, unit_price)
        existing = folded.get(key)
        if existing is not None:
            folded[key] = existing.model_copy(update={"qty": existing.qty + 1})
            continue
        folded[key] = MappedLine(
            sku=line.seller_sku or line.sku_id,
            name_snapshot=line.product_name,
            unit_price=unit_price,
            qty=1,
            currency=snap.payment.currency,
            external_line_id=line.id,
        )
    lines = tuple(folded.values())
    for line in lines:
        _money(line.unit_price * line.qty)  # a line total that cannot fit is refused here
    return lines


def _ship_to(snap: TikTokOrderSnapshot) -> dict[str, str] | None:
    shipping = snap.shipping
    if shipping is None:
        return None
    fields = {
        "recipient_name": shipping.recipient_name,
        "phone": shipping.phone,
        "address_text": shipping.address_text,
        "region": shipping.region,
    }
    present = {key: value for key, value in fields.items() if value is not None}
    return present or None


def map_order(
    client_id: UUID,
    store_id: UUID,
    shop_id: str,
    snap: TikTokOrderSnapshot,
    now: datetime,
) -> MappedOrder:
    paid = snap.paid_at is not None or snap.status.strip().upper() in _PAID_STATUSES
    status, payment_status = map_status(snap.status, paid=paid)
    payment = snap.payment
    lines = _fold_lines(snap)
    # TikTok's own subtotal when it sends one, else the sum of the line totals.
    subtotal = (
        _money(payment.sub_total)
        if payment.sub_total is not None
        else _money(sum((ln.unit_price * ln.qty for ln in lines), Decimal(0)))
    )
    package = next((p for p in snap.packages if p.tracking_number), None)
    return MappedOrder(
        # Tenant-scoped: orders.id is a global key, and a shop can move between tenants.
        id=f"tt_{client_id.hex}_{snap.id}",
        client_id=client_id,
        store_id=store_id,
        channel=CHANNEL,
        external_order_id=snap.id,
        external_shop_id=shop_id,
        status=status,
        payment_status=payment_status,
        provider_status=snap.status,
        total=_money(payment.total_amount),
        currency=payment.currency,
        subtotal=subtotal,
        shipping_fee=_money(payment.shipping_fee or Decimal(0)),
        discount=_money(payment.discount or Decimal(0)),
        placed_at=snap.create_time,
        # The snapshot carries no package time; one is never invented from the status.
        shipped_at=None,
        tracking_no=package.tracking_number if package else None,
        carrier=package.carrier if package else None,
        ship_to=_ship_to(snap),
        synced_at=now,
        provider_updated_at=snap.update_time,
        lines=lines,
    )


def _https_only(url: str | None) -> str | None:
    """Listing images are untrusted and rendered later: only a plain https URL is kept."""
    if not url or any(c.isspace() or c == "\\" or ord(c) < 32 or ord(c) == 127 for c in url):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return None
    return url if parts.scheme == "https" and host else None


def map_listings(
    client_id: UUID, store_id: UUID, product: TikTokProductSnapshot, now: datetime
) -> list[MappedListing]:
    """One listing per SKU; a product with no SKUs has no listing rows."""
    image_url = _https_only(product.image_url)
    return [
        MappedListing(
            client_id=client_id,
            store_id=store_id,
            channel=CHANNEL,
            external_product_id=product.id,
            external_sku_id=sku.id,
            product_name=product.name,
            variant_name=sku.variant_name,
            seller_sku=sku.seller_sku,
            price=_money(sku.price),
            currency=sku.currency,
            stock_qty=sku.stock_qty,
            product_status=product.status,
            image_url=image_url,
            provider_updated_at=product.update_time,
            synced_at=now,
        )
        for sku in product.skus
    ]


__all__ = [
    "MappedLine",
    "MappedListing",
    "MappedOrder",
    "MappingError",
    "map_listings",
    "map_order",
    "map_status",
]
