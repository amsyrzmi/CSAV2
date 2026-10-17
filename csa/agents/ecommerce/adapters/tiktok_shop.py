"""TikTok Shop Open API (v202309) platform adapter."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from decimal import Decimal
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx
from csa.agents.ecommerce.adapters.base import EcommercePlatformAdapter
from csa.agents.ecommerce.models import (
    EcommerceOrder,
    EcommerceOrderPage,
    InventorySyncItem,
    OrderItemSnapshot,
    TikTokShopCredentials,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

TIKTOK_SHOP_BASE_URL = "https://open-api.tiktokglobalshop.com"


class TikTokReadError(RuntimeError):
    """Safe failure code; never include provider bodies, signed URLs or credentials."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"TikTok order read failed: {code}")


class _ReadContract(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)


class _Envelope(_ReadContract):
    code: int = Field(strict=True)
    data: dict[str, Any] | None = None


class _Payment(_ReadContract):
    total_amount: Decimal = Field(ge=0, allow_inf_nan=False)
    currency: str = Field(min_length=3, max_length=3)


class _LineItem(_ReadContract):
    seller_sku: str | None = Field(default=None, min_length=1, max_length=64)
    sku_id: str | None = Field(default=None, min_length=1, max_length=64)
    product_name: str = Field(min_length=1, max_length=255)
    quantity: int = Field(ge=1, strict=True)
    sku_sale_price: Decimal = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def require_sku(self) -> _LineItem:
        if self.seller_sku is None and self.sku_id is None:
            raise ValueError("line item requires a SKU identifier")
        return self


class _Order(_ReadContract):
    id: str = Field(min_length=1, max_length=100)
    status: str = Field(min_length=1, max_length=100)
    payment: _Payment
    line_items: list[_LineItem]
    buyer_uid: str | None = None
    tracking_number: str | None = None
    shipping_provider: str | None = None

    def normalize(self) -> EcommerceOrder:
        items: list[OrderItemSnapshot] = []
        for item in self.line_items:
            sku = item.seller_sku or item.sku_id
            assert sku is not None
            items.append(
                OrderItemSnapshot(
                    sku=sku,
                    item_name=item.product_name,
                    quantity=item.quantity,
                    unit_price=item.sku_sale_price,
                )
            )
        return EcommerceOrder(
            platform="tiktok_shop",
            platform_order_id=self.id,
            order_status=self.status,
            buyer_identifier=self.buyer_uid,
            total_amount=self.payment.total_amount,
            currency=self.payment.currency,
            items=items,
            tracking_number=self.tracking_number,
            shipping_carrier=self.shipping_provider,
        )


class _OrderPage(_ReadContract):
    orders: list[_Order]
    next_page_token: str | None = Field(default=None, max_length=2048)


class _SearchQuery(_ReadContract):
    page_size: int = Field(ge=1, le=50, strict=True)
    order_status: str | None = Field(default=None, min_length=1, max_length=100)
    page_token: str | None = Field(default=None, min_length=1, max_length=2048)


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

    async def _read(self, path: str, body: str | None = None) -> dict[str, Any]:
        url, params = self._build_url_and_params(path, body or "")
        params.pop("access_token")
        headers = {"x-tts-access-token": self._creds.access_token}
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            response = await self._client.request(
                "GET" if body is None else "POST",
                url,
                params=params,
                headers=headers,
                content=body,
                follow_redirects=False,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError:
            raise TikTokReadError("http_error") from None
        except httpx.RequestError:
            raise TikTokReadError("transport_error") from None
        try:
            envelope = _Envelope.model_validate_json(response.content)
        except ValidationError:
            raise TikTokReadError("invalid_response") from None
        if envelope.code != 0:
            raise TikTokReadError("provider_rejected")
        if envelope.data is None:
            raise TikTokReadError("invalid_response")
        return envelope.data

    async def get_order(self, client_id: UUID, order_id: str) -> EcommerceOrder:
        """Read one order; tenant/store credential binding remains the caller's responsibility."""
        if not order_id or len(order_id) > 100:
            raise TikTokReadError("invalid_query")
        data = await self._read(f"/order/202309/orders/{quote(order_id, safe='')}")
        try:
            order = _Order.model_validate(data)
        except ValidationError:
            raise TikTokReadError("invalid_response") from None
        if order.id != order_id:
            raise TikTokReadError("order_mismatch")
        return order.normalize()

    async def search_orders(
        self, client_id: UUID, status: str | None = None, limit: int = 10
    ) -> list[EcommerceOrder]:
        """Legacy bounded first-page read; seller callers must retain page metadata."""
        return (await self.search_order_page(client_id, status=status, limit=limit)).items

    async def search_order_page(
        self,
        client_id: UUID,
        status: str | None = None,
        limit: int = 10,
        page_token: str | None = None,
    ) -> EcommerceOrderPage:
        """Read one explicit provider page without implying synchronization or authorization."""
        try:
            query = _SearchQuery(page_size=limit, order_status=status, page_token=page_token)
        except ValidationError:
            raise TikTokReadError("invalid_query") from None
        body = json.dumps(query.model_dump(exclude_none=True))
        data = await self._read("/order/202309/orders/search", body)
        try:
            page = _OrderPage.model_validate(data)
        except ValidationError:
            raise TikTokReadError("invalid_response") from None
        ids = [order.id for order in page.orders]
        token = page.next_page_token or None
        if (
            len(ids) > limit
            or len(set(ids)) != len(ids)
            or (token is not None and token == page_token)
        ):
            raise TikTokReadError("invalid_page")
        return EcommerceOrderPage(
            items=[order.normalize() for order in page.orders], next_page_token=token
        )

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
