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
    EcommerceOrderPage,
    InventorySyncItem,
    OrderItemSnapshot,
    TikTokShopCredentials,
)
from csa.agents.ecommerce.tiktok_models import TikTokOrderSnapshot, TikTokOrderSnapshotPage
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

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


class _OrderPage[OrderT: (_Order, TikTokOrderSnapshot)](_ReadContract):
    orders: list[OrderT]
    next_page_token: str | None = Field(default=None, max_length=2048)


class _SearchQuery(_ReadContract):
    page_size: int = Field(ge=1, le=50, strict=True)
    order_status: str | None = Field(default=None, min_length=1, max_length=100)
    page_token: str | None = Field(default=None, min_length=1, max_length=2048)


class _AuthorizedShop(_ReadContract):
    id: str = Field(min_length=1, max_length=100)
    cipher: SecretStr = Field(min_length=1)


class _AuthorizedShops(_ReadContract):
    shops: list[_AuthorizedShop]

    @model_validator(mode="after")
    def require_distinct_shops(self) -> _AuthorizedShops:
        ids = [shop.id for shop in self.shops]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate authorized shop identity")
        return self


class TikTokShopAdapter(EcommercePlatformAdapter):
    """Adapter for TikTok Shop Open API v202309."""

    def __init__(
        self,
        credentials: TikTokShopCredentials,
        http_client: httpx.AsyncClient | None = None,
        base_url: str = TIKTOK_SHOP_BASE_URL,
        *,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if http_client is not None and http_transport is not None:
            raise ValueError("provide either an HTTP client or transport")
        self._creds = credentials
        self._client = (
            http_client
            if http_client is not None
            else httpx.AsyncClient(timeout=15.0)
            if http_transport is None
            else None
        )
        self._transport = http_transport
        self._owns_client = http_client is None and http_transport is None
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

    def _build_url_and_params(
        self,
        path: str,
        body_str: str,
        query: dict[str, str] | None = None,
        *,
        include_shop_cipher: bool = True,
    ) -> tuple[str, dict[str, str]]:
        timestamp = str(int(time.time()))
        params: dict[str, str] = {
            "app_key": self._creds.app_key,
            "timestamp": timestamp,
        }
        if include_shop_cipher:
            if not self._creds.shop_cipher:
                raise TikTokReadError("shop_binding_mismatch")
            params["shop_cipher"] = self._creds.shop_cipher
        if query is not None:
            params.update(query)
        sign = self.sign_request(path, params, body_str)
        params["sign"] = sign
        params["access_token"] = self._creds.access_token
        url = f"{self._base_url}{path}"
        return url, params

    async def _read(
        self,
        path: str,
        body: str | None = None,
        *,
        query: dict[str, str] | None = None,
        include_shop_cipher: bool = True,
    ) -> dict[str, Any]:
        url, params = self._build_url_and_params(
            path, body or "", query, include_shop_cipher=include_shop_cipher
        )
        params.pop("access_token")
        headers = {
            "x-tts-access-token": self._creds.access_token,
            "Content-Type": "application/json",
        }
        try:
            if self._transport is not None:
                request = httpx.Request(
                    "GET" if body is None else "POST",
                    url,
                    params=params,
                    headers=headers,
                    content=body,
                    extensions={"timeout": httpx.Timeout(15.0).as_dict()},
                )
                response = await self._transport.handle_async_request(request)
                response.request = request
                try:
                    response.raise_for_status()
                    content = await self._read_bounded_response(response)
                finally:
                    await response.aclose()
            else:
                assert self._client is not None
                response = await self._client.request(
                    "GET" if body is None else "POST",
                    url,
                    params=params,
                    headers=headers,
                    content=body,
                    follow_redirects=False,
                )
                response.raise_for_status()
                content = response.content
        except httpx.HTTPStatusError:
            raise TikTokReadError("http_error") from None
        except httpx.RequestError:
            raise TikTokReadError("transport_error") from None
        try:
            envelope = _Envelope.model_validate_json(content)
        except ValidationError:
            raise TikTokReadError("invalid_response") from None
        if envelope.code != 0:
            raise TikTokReadError("provider_rejected")
        if envelope.data is None:
            raise TikTokReadError("invalid_response")
        return envelope.data

    @staticmethod
    async def _read_bounded_response(response: httpx.Response) -> bytes:
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                length = int(content_length)
            except ValueError:
                raise TikTokReadError("invalid_response") from None
            if length < 0:
                raise TikTokReadError("invalid_response")
            if length > 256 * 1024:
                raise TikTokReadError("invalid_response")
        content = bytearray()
        async for chunk in response.aiter_raw(chunk_size=8192):
            content.extend(chunk)
            if len(content) > 256 * 1024:
                raise TikTokReadError("invalid_response")
        return bytes(content)

    async def authorized_shop_cipher(self, shop_id: str) -> SecretStr:
        """Return a fresh cipher only for the exact server-selected shop ID."""
        if not shop_id or len(shop_id) > 100 or self._creds.shop_id != shop_id:
            raise TikTokReadError("shop_binding_mismatch")
        data = await self._read("/authorization/202309/shops", include_shop_cipher=False)
        try:
            shops = _AuthorizedShops.model_validate(data)
        except ValidationError:
            raise TikTokReadError("invalid_response") from None
        matches = [shop for shop in shops.shops if shop.id == shop_id]
        if len(matches) != 1:
            raise TikTokReadError("shop_not_authorized")
        return matches[0].cipher

    async def require_authorized_shop(self, shop_id: str) -> None:
        """Fresh exact ID/cipher match only, not fresh order scopes or tenant authority.

        The bound reader uses this existing-cipher check. Initial authorization must
        use the separate verifier and persist its freshly selected exact-shop cipher.
        """
        if (
            not shop_id
            or len(shop_id) > 100
            or self._creds.shop_id != shop_id
            or not self._creds.shop_cipher
        ):
            raise TikTokReadError("shop_binding_mismatch")
        cipher = await self.authorized_shop_cipher(shop_id)
        if not hmac.compare_digest(
            cipher.get_secret_value().encode(), self._creds.shop_cipher.encode()
        ):
            raise TikTokReadError("shop_binding_mismatch")

    async def get_order(self, client_id: UUID, order_id: str) -> EcommerceOrder:
        """Read one order; tenant/store credential binding remains the caller's responsibility."""
        return (await self._get_order(order_id, _OrderPage[_Order])).normalize()

    async def get_order_snapshot(self, client_id: UUID, order_id: str) -> TikTokOrderSnapshot:
        """Read selected provider fields without normalized quantity/price assumptions.

        This is not tenant/store authorization; credential binding remains a caller gate.
        """
        return await self._get_order(order_id, _OrderPage[TikTokOrderSnapshot])

    async def _get_order[OrderT: (_Order, TikTokOrderSnapshot)](
        self, order_id: str, page_type: type[_OrderPage[OrderT]]
    ) -> OrderT:
        if not order_id or len(order_id) > 100 or "," in order_id:
            raise TikTokReadError("invalid_query")
        data = await self._read("/order/202309/orders", query={"ids": order_id})
        try:
            page = page_type.model_validate(data)
        except ValidationError:
            raise TikTokReadError("invalid_response") from None
        if len(page.orders) != 1:
            raise TikTokReadError("order_mismatch")
        order = page.orders[0]
        if order.id != order_id:
            raise TikTokReadError("order_mismatch")
        return order

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
        page = await self._search_order_page(_OrderPage[_Order], status, limit, page_token)
        return EcommerceOrderPage(
            items=[order.normalize() for order in page.orders],
            next_page_token=page.next_page_token or None,
        )

    async def search_order_snapshot_page(
        self,
        client_id: UUID,
        status: str | None = None,
        limit: int = 10,
        page_token: str | None = None,
    ) -> TikTokOrderSnapshotPage:
        """Read provider fields; no normalized order, sync or authorization claim."""
        page = await self._search_order_page(
            _OrderPage[TikTokOrderSnapshot], status, limit, page_token
        )
        return TikTokOrderSnapshotPage(
            items=tuple(page.orders), next_page_token=page.next_page_token or None
        )

    async def _search_order_page[OrderT: (_Order, TikTokOrderSnapshot)](
        self,
        page_type: type[_OrderPage[OrderT]],
        status: str | None,
        limit: int,
        page_token: str | None,
    ) -> _OrderPage[OrderT]:
        try:
            query = _SearchQuery(page_size=limit, order_status=status, page_token=page_token)
        except ValidationError:
            raise TikTokReadError("invalid_query") from None
        params = {"page_size": str(query.page_size)}
        if query.page_token is not None:
            params["page_token"] = query.page_token
        body = json.dumps(
            {"order_status": query.order_status} if query.order_status is not None else {}
        )
        data = await self._read("/order/202309/orders/search", body, query=params)
        try:
            page = page_type.model_validate(data)
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
        return page

    async def get_product(self, client_id: UUID, sku: str) -> dict[str, Any]:
        """Queries product details by seller SKU."""
        path = "/product/202309/products/search"
        body_str = json.dumps({"seller_sku_list": [sku]})
        url, params = self._build_url_and_params(path, body_str)
        headers = {
            "x-tts-access-token": self._creds.access_token,
            "Content-Type": "application/json",
        }
        resp = await self._require_http_client().post(
            url, params=params, headers=headers, content=body_str
        )
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
        resp = await self._require_http_client().post(
            url, params=params, headers=headers, content=body_str
        )
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
        resp = await self._require_http_client().post(
            url, params=params, headers=headers, content=body_str
        )
        resp.raise_for_status()
        return bool(resp.json().get("code") == 0)

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    def _require_http_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise TikTokReadError("http_client_unavailable")
        return self._client
