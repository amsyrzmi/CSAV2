"""Read-only seller records and synced TikTok data; no marketplace transport or credentials."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from psycopg import AsyncConnection
from pydantic import AwareDatetime, Field, field_validator

from csa.agents.ecommerce.authorization import PRODUCT_SCOPES
from csa.agents.ecommerce.models import MarketplacePlatform
from csa.agents.ecommerce.stores import (
    StoreBinding,
    StoreConnection,
    StoreRecord,
    StoreSync,
    expiry_state,
)
from csa.console.access import tenant
from csa.console.contracts import ConsoleActor, Contract, Role
from csa.console.services import package_gate
from csa.console.tiktok_connect import build_tiktok_callback_router, build_tiktok_connect_router
from csa.core import db
from csa.core.tenancy import tx
from csa.marketplace.jobs import list_connected_stores

OrderChannel = Literal["telegram", "web", "tiktok_shop", "shopee", "manual"]
# Mirrors the orders_status_chk and orders_payment_chk constraints in db/schema_baseline.sql.
ORDER_STATUSES = (
    "draft",
    "pending_confirmation",
    "confirmed",
    "processing",
    "shipped",
    "delivered",
    "cancelled",
    "refunded",
)
PAYMENT_STATUSES = ("unpaid", "cod_pending", "paid", "refunded", "failed")


class OrderFilters(Contract):
    channel: OrderChannel | None = None
    shop_id: str | None = Field(default=None, min_length=1, max_length=100)


def order_filters(
    channel: Annotated[OrderChannel | None, Query()] = None,
    shop_id: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
) -> OrderFilters:
    if shop_id is not None and channel is None:
        raise HTTPException(400, "shop_id requires a channel")
    return OrderFilters(channel=channel, shop_id=shop_id)


class OrderWindow(Contract):
    """Optional created_at window (from inclusive, to exclusive) and payment status."""

    created_from: AwareDatetime | None = None
    created_to: AwareDatetime | None = None
    payment_status: str | None = None


def order_window(
    created_from: Annotated[AwareDatetime | None, Query(alias="from")] = None,
    created_to: Annotated[AwareDatetime | None, Query(alias="to")] = None,
    payment_status: Annotated[str | None, Query()] = None,
) -> OrderWindow:
    if payment_status is not None and payment_status not in PAYMENT_STATUSES:
        raise HTTPException(422, "invalid_payment_status")
    if created_from is not None and created_to is not None and created_from >= created_to:
        raise HTTPException(422, "invalid_range")
    return OrderWindow(
        created_from=created_from, created_to=created_to, payment_status=payment_status
    )


class OrderCounts(Contract):
    """Orders per status for the date, payment, channel and store filters; never status."""

    total: int
    by_status: dict[str, int]


class BusinessOrder(Contract):
    id: str
    status: str
    payment_status: str
    total: Decimal
    currency: str
    channel: str
    external_order_id: str | None
    external_shop_id: str | None
    tracking_no: str | None
    carrier: str | None
    placed_at: datetime | None
    shipped_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # Synced TikTok orders only; local orders read None, None, False.
    provider_status: str | None = None
    synced_at: datetime | None = None
    buyer_cleared: bool = False


class OrderLineOut(Contract):
    sku: str
    name: str
    qty: int
    unit_price: Decimal


class ShipTo(Contract):
    """The four fields sync stores, as TikTok gave them (possibly masked)."""

    recipient_name: str | None = None
    phone: str | None = None
    address_text: str | None = None
    region: str | None = None


class BusinessOrderDetail(BusinessOrder):
    """One order. The extra fields are filled only for a synced TikTok order; for a local order
    they are None, and ship_to is None as well once the buyer details were cleared."""

    lines: list[OrderLineOut] | None = None
    subtotal: Decimal | None = None
    shipping_fee: Decimal | None = None
    discount: Decimal | None = None
    ship_to: ShipTo | None = None


class BusinessListing(Contract):
    id: UUID
    product_name: str
    variant_name: str | None
    seller_sku: str | None
    price: Decimal | None
    currency: str
    stock_qty: int | None
    product_status: str | None
    image_url: str | None
    synced_at: datetime
    # The local product with an equal SKU, looked up at read time; never stored.
    same_sku_product: str | None

    @field_validator("image_url")
    @classmethod
    def _https_only(cls, value: str | None) -> str | None:
        """Provider data is untrusted: only an https URL is ever handed to a browser."""
        return value if value is not None and value.startswith("https://") else None


class BusinessProduct(Contract):
    id: UUID
    sku: str
    name: str
    price: Decimal
    currency: str
    stock_qty: int
    reserved_qty: int
    available_qty: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


# "connected" when the tenant has a TikTok store that can sync now, else "not_connected".
MarketplaceSync = Literal["connected", "not_connected"]


class OrderPage(Contract):
    source: str = "local_records"
    marketplace_sync: MarketplaceSync
    items: list[BusinessOrder]
    next_cursor: str | None


class ProductPage(Contract):
    source: str = "local_catalogue"
    marketplace_sync: MarketplaceSync
    items: list[BusinessProduct]
    next_cursor: UUID | None


class ListingPage(Contract):
    items: list[BusinessListing]
    next_cursor: UUID | None


class StorePage(Contract):
    source: Literal["local_credential_records"] = "local_credential_records"
    marketplace_sync: MarketplaceSync
    items: list[StoreRecord]
    next_cursor: UUID | None


async def marketplace_sync_state(conn: AsyncConnection[Any], client_id: UUID) -> MarketplaceSync:
    """Connected iff an active grant, bound shop and live tokens exist (the sync's own test)."""
    return "connected" if await list_connected_stores(conn, client_id) else "not_connected"


def can_manage_stores(actor: ConsoleActor) -> bool:
    """Platform admins and the tenant's Admin; staff only look."""
    return actor.platform_admin or actor.role == Role.TENANT_ADMIN


def escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def store_records(
    conn: AsyncConnection[Any], client_id: UUID, rows: list[dict], *, can_manage: bool
) -> list[StoreRecord]:
    if not rows:
        return []
    metadata = await db.q.fetch_all(
        "business/store_connections.sql",
        {
            "client_id": client_id,
            "ids": [row["id"] for row in rows],
            "product_scopes": sorted(PRODUCT_SCOPES),
        },
        conn=conn,
    )
    connections = {
        row["id"]: StoreConnection.model_validate({"status": row["status"]}) for row in metadata
    }
    syncs = {
        row["id"]: StoreSync(
            orders_synced_at=row["orders_synced_at"],
            products_synced_at=row["products_synced_at"],
            problem="sync_failing" if row["sync_failing"] else None,
        )
        for row in metadata
        if row["is_tiktok"]
    }
    now = datetime.now(UTC)
    return [
        StoreRecord.model_validate(
            {
                **row,
                "expiry": expiry_state(row["expires_at"], now=now),
                "connection": connections[row["id"]],
                "sync": syncs.get(row["id"]),
                "can_manage": can_manage,
            }
        )
        for row in rows
    ]


def require_business_read(actor: ConsoleActor, client_id: UUID) -> None:
    if actor.role not in {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN, Role.TENANT_STAFF}:
        raise HTTPException(403, "business records require an administrator or staff role")
    if not actor.platform_admin and actor.client_id != client_id:
        raise HTTPException(403, "business tenant is not authorized")


async def get_local_order(
    conn: AsyncConnection[Any],
    client_id: UUID,
    order_id: str,
    filters: OrderFilters | None = None,
) -> BusinessOrder | None:
    filters = filters or OrderFilters()
    row = await db.q.fetch_one(
        "business/get_order.sql",
        (
            client_id,
            order_id,
            filters.channel,
            filters.channel,
            filters.shop_id,
            filters.shop_id,
        ),
        conn=conn,
    )
    return BusinessOrder.model_validate(row) if row is not None else None


async def resolve_store_binding(request: Request, binding: StoreBinding) -> StoreRecord:
    """Match a caller's full local scope; does not load secrets or authorize an API call."""
    async with tx(request.app.state.pool, str(binding.client_id)) as conn:
        row = await db.q.fetch_one(
            "business/get_store.sql",
            (
                binding.client_id,
                binding.record_id,
                binding.platform,
                binding.platform,
                binding.shop_id,
                binding.shop_id,
            ),
            conn=conn,
        )
        if row is None:
            raise HTTPException(404, "local store record not found")
        can_manage = can_manage_stores(request.state.console_actor)
        return (await store_records(conn, binding.client_id, [row], can_manage=can_manage))[0]


def business_actor(request: Request) -> ConsoleActor:
    actor = request.state.console_actor
    if not isinstance(actor, ConsoleActor):
        raise HTTPException(403, "business records require an administrator or staff role")
    if actor.role not in {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN, Role.TENANT_STAFF}:
        raise HTTPException(403, "business records require an administrator or staff role")
    return actor


def build_business_router() -> APIRouter:
    router = APIRouter(
        prefix="/business",
        dependencies=[
            Depends(package_gate("orders_products", "marketplaces")),
            Depends(business_actor),
        ],
    )

    @router.get("/stores", response_model=StorePage)
    async def stores(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        platform: MarketplacePlatform | None = None,
        before: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> StorePage:
        actor = request.state.console_actor
        async with tx(request.app.state.pool, str(client_id)) as conn:
            if before is not None and not await db.q.fetch_one(
                "business/get_store.sql",
                (client_id, before, platform, platform, None, None),
                conn=conn,
            ):
                raise HTTPException(404, "local store cursor not found")
            rows = await db.q.fetch_all(
                "business/list_stores.sql",
                (client_id, platform, platform, before, before, limit + 1),
                conn=conn,
            )
            items = await store_records(
                conn, client_id, rows[:limit], can_manage=can_manage_stores(actor)
            )
            sync = await marketplace_sync_state(conn, client_id)
        return StorePage(
            marketplace_sync=sync,
            items=items,
            next_cursor=items[-1].id if len(rows) > limit else None,
        )

    @router.get("/stores/{record_id}", response_model=StoreRecord)
    async def store(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        record_id: UUID,
        platform: MarketplacePlatform | None = None,
        shop_id: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    ) -> StoreRecord:
        if shop_id is not None:
            if platform is None:
                raise HTTPException(400, "shop_id requires a platform")
            return await resolve_store_binding(
                request,
                StoreBinding(
                    client_id=client_id, record_id=record_id, platform=platform, shop_id=shop_id
                ),
            )
        async with tx(request.app.state.pool, str(client_id)) as conn:
            row = await db.q.fetch_one(
                "business/get_store.sql",
                (client_id, record_id, platform, platform, None, None),
                conn=conn,
            )
            if row is None:
                raise HTTPException(404, "local store record not found")
            can_manage = can_manage_stores(request.state.console_actor)
            return (await store_records(conn, client_id, [row], can_manage=can_manage))[0]

    @router.get("/orders", response_model=OrderPage)
    async def orders(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        filters: Annotated[OrderFilters, Depends(order_filters)],
        window: Annotated[OrderWindow, Depends(order_window)],
        status: Annotated[str | None, Query()] = None,
        before: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> OrderPage:
        if status is not None and status not in ORDER_STATUSES:
            raise HTTPException(422, "invalid_status")
        async with tx(request.app.state.pool, str(client_id)) as conn:
            if before is not None and not await db.q.fetch_one(
                "business/get_order.sql",
                (
                    client_id,
                    before,
                    filters.channel,
                    filters.channel,
                    filters.shop_id,
                    filters.shop_id,
                ),
                conn=conn,
            ):
                raise HTTPException(404, "order cursor not found")
            rows = await db.q.fetch_all(
                "business/list_orders.sql",
                (
                    client_id,
                    filters.channel,
                    filters.channel,
                    filters.shop_id,
                    filters.shop_id,
                    window.created_from,
                    window.created_from,
                    window.created_to,
                    window.created_to,
                    status,
                    status,
                    window.payment_status,
                    window.payment_status,
                    before,
                    client_id,
                    before,
                    limit + 1,
                ),
                conn=conn,
            )
            sync = await marketplace_sync_state(conn, client_id)
        items = [BusinessOrder.model_validate(row) for row in rows[:limit]]
        return OrderPage(
            marketplace_sync=sync,
            items=items,
            next_cursor=items[-1].id if len(rows) > limit else None,
        )

    @router.get("/orders/counts", response_model=OrderCounts)
    async def order_counts(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        filters: Annotated[OrderFilters, Depends(order_filters)],
        window: Annotated[OrderWindow, Depends(order_window)],
    ) -> OrderCounts:
        """Per-status totals; any `status` query parameter is deliberately not read."""
        async with tx(request.app.state.pool, str(client_id)) as conn:
            rows = await db.q.fetch_all(
                "business/order_counts.sql",
                (
                    client_id,
                    filters.channel,
                    filters.channel,
                    filters.shop_id,
                    filters.shop_id,
                    window.created_from,
                    window.created_from,
                    window.created_to,
                    window.created_to,
                    window.payment_status,
                    window.payment_status,
                ),
                conn=conn,
            )
        found = {row["status"]: int(row["n"]) for row in rows}
        by_status = {status: found.pop(status, 0) for status in ORDER_STATUSES}
        if found:
            raise RuntimeError(f"orders hold statuses outside the schema enum: {sorted(found)}")
        return OrderCounts(total=sum(by_status.values()), by_status=by_status)

    @router.get("/orders/{order_id}", response_model=BusinessOrderDetail)
    async def order(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        order_id: Annotated[str, Path(min_length=1, max_length=100)],
        filters: Annotated[OrderFilters, Depends(order_filters)],
    ) -> BusinessOrderDetail:
        actor: ConsoleActor = request.state.console_actor
        require_business_read(actor, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            row = await db.q.fetch_one(
                "business/get_order_detail.sql",
                (
                    client_id,
                    order_id,
                    filters.channel,
                    filters.channel,
                    filters.shop_id,
                    filters.shop_id,
                ),
                conn=conn,
            )
            if row is None:
                raise HTTPException(404, "order not found")
            synced = row.pop("synced")
            lines = None
            if synced:
                lines = await db.q.fetch_all(
                    "business/get_order_lines.sql", (client_id, order_id), conn=conn
                )
        ship_to = row["ship_to"]
        if row["buyer_cleared"] or not isinstance(ship_to, dict):
            ship_to = None
        else:
            ship_to = {
                key: value
                for key, value in ship_to.items()
                if key in ShipTo.model_fields and isinstance(value, str)
            }
        return BusinessOrderDetail.model_validate({**row, "ship_to": ship_to, "lines": lines})

    @router.get("/products", response_model=ProductPage)
    async def products(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        before: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> ProductPage:
        async with tx(request.app.state.pool, str(client_id)) as conn:
            if before is not None and not await db.q.fetch_one(
                "business/get_product.sql", (client_id, before), conn=conn
            ):
                raise HTTPException(404, "product cursor not found")
            rows = await db.q.fetch_all(
                "business/list_products.sql",
                (client_id, before, client_id, before, limit + 1),
                conn=conn,
            )
            sync = await marketplace_sync_state(conn, client_id)
        items = [BusinessProduct.model_validate(row) for row in rows[:limit]]
        return ProductPage(
            marketplace_sync=sync,
            items=items,
            next_cursor=items[-1].id if len(rows) > limit else None,
        )

    @router.get("/listings", response_model=ListingPage)
    async def listings(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        store_id: UUID | None = None,
        q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
        before: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> ListingPage:
        needle = q.strip() if q is not None else ""
        pattern = f"%{escape_like(needle)}%" if needle else None
        async with tx(request.app.state.pool, str(client_id)) as conn:
            if before is not None and not await db.q.fetch_one(
                "business/get_listing.sql",
                (client_id, before, store_id, store_id),
                conn=conn,
            ):
                raise HTTPException(404, "listing cursor not found")
            rows = await db.q.fetch_all(
                "business/list_listings.sql",
                {
                    "client_id": client_id,
                    "store_id": store_id,
                    "pattern": pattern,
                    "before": before,
                    "limit": limit + 1,
                },
                conn=conn,
            )
        items = [BusinessListing.model_validate(row) for row in rows[:limit]]
        return ListingPage(items=items, next_cursor=items[-1].id if len(rows) > limit else None)

    @router.get("/products/{product_id}", response_model=BusinessProduct)
    async def product(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        product_id: UUID,
    ) -> BusinessProduct:
        async with tx(request.app.state.pool, str(client_id)) as conn:
            row = await db.q.fetch_one(
                "business/get_product.sql", (client_id, product_id), conn=conn
            )
        if row is None:
            raise HTTPException(404, "product not found")
        return BusinessProduct.model_validate(row)

    # Connect and disconnect sit behind the package gate and business_actor, plus their own
    # Admin check. The callback's tenant comes from the state, not client_id, so it cannot use
    # the tenant-resolving gate: it keeps business_actor and checks the rest itself.
    router.include_router(build_tiktok_connect_router())
    business = APIRouter()
    business.include_router(router)
    business.include_router(
        build_tiktok_callback_router(), prefix="/business", dependencies=[Depends(business_actor)]
    )
    return business
