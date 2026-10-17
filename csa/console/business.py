"""Read-only local seller records; no marketplace transport or credential access."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from csa.console.access import tenant
from csa.console.contracts import ConsoleActor, Contract, Role
from csa.core import db
from csa.core.tenancy import tx


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


def business_actor(request: Request) -> ConsoleActor:
    actor = request.state.console_actor
    if not isinstance(actor, ConsoleActor) or actor.role not in {
        Role.PLATFORM_ADMIN,
        Role.TENANT_ADMIN,
        Role.TENANT_STAFF,
    }:
        raise HTTPException(403, "business records require an administrator or staff role")
    return actor


def build_business_router() -> APIRouter:
    router = APIRouter(prefix="/business", dependencies=[Depends(business_actor)])

    @router.get("/orders", response_model=OrderPage)
    async def orders(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        before: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> OrderPage:
        async with tx(request.app.state.pool, str(client_id)) as conn:
            if before is not None and not await db.q.fetch_one(
                "business/get_order.sql", (client_id, before), conn=conn
            ):
                raise HTTPException(404, "order cursor not found")
            rows = await db.q.fetch_all(
                "business/list_orders.sql",
                (client_id, before, client_id, before, limit + 1),
                conn=conn,
            )
        items = [BusinessOrder.model_validate(row) for row in rows[:limit]]
        return OrderPage(items=items, next_cursor=items[-1].id if len(rows) > limit else None)

    @router.get("/orders/{order_id}", response_model=BusinessOrder)
    async def order(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        order_id: Annotated[str, Path(min_length=1, max_length=100)],
    ) -> BusinessOrder:
        async with tx(request.app.state.pool, str(client_id)) as conn:
            row = await db.q.fetch_one("business/get_order.sql", (client_id, order_id), conn=conn)
        if row is None:
            raise HTTPException(404, "order not found")
        return BusinessOrder.model_validate(row)

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
