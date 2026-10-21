"""Read-only local seller records; no marketplace transport or credential access."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from psycopg import AsyncConnection
from pydantic import Field

from csa.agents.ecommerce.models import MarketplacePlatform
from csa.agents.ecommerce.stores import StoreBinding, StoreConnection, StoreRecord, expiry_state
from csa.console.access import tenant
from csa.console.contracts import ConsoleActor, Contract, Role
from csa.core import db
from csa.core.tenancy import tx

OrderChannel = Literal["telegram", "web", "tiktok_shop", "shopee", "manual"]


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


class OrderPage(Contract):
    source: str = "local_records"
    marketplace_sync: str = "unavailable"
    items: list[BusinessOrder]
    next_cursor: str | None


class ProductPage(Contract):
    source: str = "local_catalogue"
    marketplace_sync: str = "unavailable"
    items: list[BusinessProduct]
    next_cursor: UUID | None


class StorePage(Contract):
    source: Literal["local_credential_records"] = "local_credential_records"
    marketplace_sync: Literal["unavailable"] = "unavailable"
    items: list[StoreRecord]
    next_cursor: UUID | None


async def store_records(
    conn: AsyncConnection[Any], client_id: UUID, rows: list[dict]
) -> list[StoreRecord]:
    if not rows:
        return []
    metadata = await db.q.fetch_all(
        "business/store_connections.sql", (client_id, [row["id"] for row in rows]), conn=conn
    )
    connections = {
        row["id"]: StoreConnection.model_validate({"status": row["status"]}) for row in metadata
    }
    now = datetime.now(UTC)
    return [
        StoreRecord.model_validate(
            {
                **row,
                "expiry": expiry_state(row["expires_at"], now=now),
                "connection": connections[row["id"]],
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
        return (await store_records(conn, binding.client_id, [row]))[0]


def business_actor(request: Request) -> ConsoleActor:
    actor = request.state.console_actor
    if not isinstance(actor, ConsoleActor):
        raise HTTPException(403, "business records require an administrator or staff role")
    if actor.role not in {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN, Role.TENANT_STAFF}:
        raise HTTPException(403, "business records require an administrator or staff role")
    return actor


def build_business_router() -> APIRouter:
    router = APIRouter(prefix="/business", dependencies=[Depends(business_actor)])

    @router.get("/stores", response_model=StorePage)
    async def stores(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        platform: MarketplacePlatform | None = None,
        before: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> StorePage:
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
            items = await store_records(conn, client_id, rows[:limit])
        return StorePage(items=items, next_cursor=items[-1].id if len(rows) > limit else None)

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
            return (await store_records(conn, client_id, [row]))[0]

    @router.get("/orders", response_model=OrderPage)
    async def orders(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        filters: Annotated[OrderFilters, Depends(order_filters)],
        before: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> OrderPage:
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
                    before,
                    client_id,
                    before,
                    limit + 1,
                ),
                conn=conn,
            )
        items = [BusinessOrder.model_validate(row) for row in rows[:limit]]
        return OrderPage(items=items, next_cursor=items[-1].id if len(rows) > limit else None)

    @router.get("/orders/{order_id}", response_model=BusinessOrder)
    async def order(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        order_id: Annotated[str, Path(min_length=1, max_length=100)],
        filters: Annotated[OrderFilters, Depends(order_filters)],
    ) -> BusinessOrder:
        actor: ConsoleActor = request.state.console_actor
        require_business_read(actor, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            result = await get_local_order(conn, client_id, order_id, filters)
        if result is None:
            raise HTTPException(404, "order not found")
        return result

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
        items = [BusinessProduct.model_validate(row) for row in rows[:limit]]
        return ProductPage(items=items, next_cursor=items[-1].id if len(rows) > limit else None)

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

    return router
