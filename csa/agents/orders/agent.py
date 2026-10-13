"""Order agent: status, catalogue lookup, draft creation, confirm and cancel (LG05)."""

from __future__ import annotations

import secrets
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from csa.agents.base import AgentContext, SideEffects
from csa.agents.facts import day, items_text, money
from csa.contracts.facts import (
    AgentError,
    AgentResult,
    AgentResultData,
    Fact,
    ResultMeta,
    Ui,
    UiConfirmation,
    UiOption,
)
from csa.contracts.plan import RiskClass
from csa.core import db
from csa.core.tenancy import tx
from csa.notify.notifier import notify

DRAFT_TTL = timedelta(minutes=30)
ORDER_CHANNELS = {"telegram", "web", "tiktok_shop", "shopee", "manual"}
_CHANNEL_ALIASES = {"web_console": "web"}
_ACTIVE = ("draft", "pending_confirmation", "confirmed", "processing", "shipped")


class OrderLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str = Field(min_length=1, max_length=64)
    qty: int = Field(ge=1, le=999)


class OrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal[
        "status", "search_products", "product_detail", "create_draft", "confirm", "cancel"
    ]
    order_id: str | None = Field(default=None, max_length=64)
    query: str | None = Field(default=None, max_length=100)
    sku: str | None = Field(default=None, max_length=64)
    items: list[OrderLine] = Field(default_factory=list, max_length=20)
    callback_id: UUID | None = None

    @model_validator(mode="after")
    def check_action_fields(self) -> OrderInput:
        needs = {
            "search_products": bool(self.query),
            "product_detail": bool(self.sku),
            "create_draft": bool(self.items),
            "confirm": self.order_id is not None and self.callback_id is not None,
            "cancel": self.order_id is not None and self.callback_id is not None,
        }
        if not needs.get(self.action, True):
            raise ValueError(f"missing fields for {self.action}")
        return self


class OrderAgent:
    name = "order_agent"
    Input = OrderInput
    side_effects: SideEffects = "reversible"
    timeout_ms = 15000
    max_retries = 1
    ACTION_RISK: dict[str, RiskClass] = {
        "status": "read",
        "search_products": "read",
        "product_detail": "read",
        "create_draft": "draft",
        "confirm": "commit",
        "cancel": "commit",
    }

    def risk(self, inp: BaseModel) -> RiskClass:
        assert isinstance(inp, OrderInput)
        return self.ACTION_RISK[inp.action]

    async def run(self, ctx: AgentContext, inp: BaseModel) -> AgentResult:
        assert isinstance(inp, OrderInput)
        channel = _CHANNEL_ALIASES.get(ctx.channel, ctx.channel)
        if inp.action in {"create_draft", "confirm", "cancel"} and channel not in ORDER_CHANNELS:
            return self._error(
                ctx, "channel_unsupported", "validation", f"orders not supported on {channel}"
            )
        handler = {
            "status": self._status,
            "search_products": self._search,
            "product_detail": self._detail,
            "create_draft": self._create_draft,
            "confirm": self._confirm,
            "cancel": self._cancel,
        }[inp.action]
        return await handler(ctx, inp, channel)

    def _ok(
        self,
        ctx: AgentContext,
        facts: list[Fact],
        ui: Ui | None = None,
        *,
        duplicate: bool | None = None,
    ) -> AgentResult:
        return AgentResult(
            ok=True,
            status="ok",
            task_id=ctx.idempotency_key,
            agent=self.name,
            data=AgentResultData(facts=facts, ui=ui or Ui()),
            meta=ResultMeta(trace_id=ctx.trace_id, latency_ms=0, was_duplicate=duplicate),
        )

    def _error(
        self,
        ctx: AgentContext,
        code: str,
        category: Any,
        message: str,
        *,
        hint: str | None = None,
    ) -> AgentResult:
        return AgentResult(
            ok=False,
            status="error",
            task_id=ctx.idempotency_key,
            agent=self.name,
            data=AgentResultData(),
            error=AgentError(
                code=code, category=category, message=message, customer_safe_hint=hint
            ),
            meta=ResultMeta(trace_id=ctx.trace_id, latency_ms=0),
        )

    def _fact(self, fact_id: str, text: str) -> Fact:
        return Fact(id=fact_id, text=text, source=self.name)

    async def _status(self, ctx: AgentContext, inp: OrderInput, channel: str) -> AgentResult:
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            rows = await db.q.fetch_all(
                "orders/list_orders.sql",
                (ctx.client_id, ctx.contact_id, inp.order_id, inp.order_id, inp.order_id is None),
                conn=conn,
            )
        if not rows:
            return self._ok(ctx, [self._fact("no_orders", "No matching orders were found.")])
        facts = [
            self._fact(f"order_{row['id']}", self._describe(row, ctx.timezone)) for row in rows
        ]
        options: list[UiOption] = []
        if len(rows) > 1:
            for row in rows:
                action = await ctx.pending_actions.issue(
                    client_id=ctx.client_id,
                    conversation_id=_need_conversation(ctx),
                    contact_id=ctx.contact_id,
                    agent=self.name,
                    action="status",
                    payload={"order_id": row["id"]},
                    ttl=timedelta(hours=24),
                )
                options.append(UiOption(label=f"Track {row['id']}", action_id=action))
        return self._ok(ctx, facts, Ui(options=options))

    @staticmethod
    def _describe(row: dict[str, Any], timezone: str) -> str:
        text = (
            f"Order {row['id']}: {row['status']}, {money(row['total'], row['currency'])}, "
            f"items: {items_text(row['items'])}"
        )
        if row["placed_at"]:
            text += f", placed {day(row['placed_at'], timezone)}"
        if row["tracking_no"]:
            text += f", tracking {row['carrier'] or ''} {row['tracking_no']}".replace("  ", " ")
        return text

    async def _search(self, ctx: AgentContext, inp: OrderInput, channel: str) -> AgentResult:
        pattern = f"%{(inp.query or '').replace('%', '').replace('_', '')}%"
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            rows = await db.q.fetch_all(
                "orders/search_products.sql",
                (ctx.client_id, pattern, pattern, pattern),
                conn=conn,
            )
        if not rows:
            return self._ok(ctx, [self._fact("no_products", "No matching products were found.")])
        return self._ok(
            ctx,
            [
                self._fact(
                    f"product_{r['sku']}",
                    f"{r['name']} ({r['sku']}): {money(r['price'], r['currency'])}, "
                    f"{r['available_qty']} available",
                )
                for r in rows
            ],
        )

    async def _detail(self, ctx: AgentContext, inp: OrderInput, channel: str) -> AgentResult:
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            row = await db.q.fetch_one(
                "orders/get_product.sql", (ctx.client_id, inp.sku), conn=conn
            )
        if row is None:
            return self._error(ctx, "product_not_found", "not_found", f"{inp.sku} not found")
        text = (
            f"{row['name']} ({row['sku']}): {money(row['price'], row['currency'])}, "
            f"{row['available_qty']} {row['unit']} available"
        )
        if row["description"]:
            text += f". {row['description']}"
        return self._ok(ctx, [self._fact(f"product_{row['sku']}", text)])

    async def _create_draft(self, ctx: AgentContext, inp: OrderInput, channel: str) -> AgentResult:
        wanted: dict[str, int] = defaultdict(int)
        for line in inp.items:
            wanted[line.sku] += line.qty
        key = f"draft:{ctx.idempotency_key}"
        conversation_id = _need_conversation(ctx)
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            products = await db.q.fetch_all(
                "orders/lock_products.sql", (ctx.client_id, sorted(wanted)), conn=conn
            )
            existing = await db.q.fetch_one(
                "orders/find_by_idempotency.sql", (ctx.client_id, key), conn=conn
            )
            if existing is not None:
                order_id, replay = existing["id"], True
                lines = await db.q.fetch_all(
                    "orders/list_items.sql", (ctx.client_id, order_id), conn=conn
                )
                total, currency, status = (
                    existing["total"],
                    existing["currency"],
                    existing["status"],
                )
            else:
                replay = False
                by_sku = {p["sku"]: p for p in products if p["is_active"]}
                missing = sorted(set(wanted) - set(by_sku))
                if missing:
                    return self._error(
                        ctx, "product_not_found", "not_found", f"unknown sku: {', '.join(missing)}"
                    )
                short = [s for s, q in wanted.items() if by_sku[s]["available_qty"] < q]
                if short:
                    return self._error(
                        ctx,
                        "insufficient_stock",
                        "conflict",
                        f"insufficient stock: {', '.join(sorted(short))}",
                        hint="Some items don't have enough stock for that quantity.",
                    )
                currencies = {p["currency"] for p in by_sku.values()}
                if len(currencies) != 1:
                    return self._error(ctx, "mixed_currency", "validation", "mixed currencies")
                currency = currencies.pop()
                total = sum((by_sku[s]["price"] * q for s, q in wanted.items()), start=Decimal(0))
                order_id = await self._insert_order(conn, ctx, channel, key, total, currency)
                lines = []
                for sku in sorted(wanted):
                    product = by_sku[sku]
                    qty = wanted[sku]
                    await db.q.execute(
                        "orders/insert_item.sql",
                        (
                            ctx.client_id,
                            order_id,
                            product["id"],
                            sku,
                            product["name"],
                            product["price"],
                            qty,
                            currency,
                        ),
                        conn=conn,
                    )
                    await db.q.fetch_one(
                        "orders/reserve_stock.sql",
                        (qty, product["id"], ctx.client_id),
                        conn=conn,
                    )
                    lines.append({"sku": sku, "name": product["name"], "qty": qty})
                expires_at = ctx.now() + DRAFT_TTL
                await db.q.fetch_one(
                    "orders/insert_confirmation.sql",
                    (
                        ctx.client_id,
                        order_id,
                        ctx.contact_id,
                        secrets.token_urlsafe(16),
                        channel,
                        expires_at,
                    ),
                    conn=conn,
                )
                await self._event(
                    conn, ctx, order_id, "draft_created", {"lines": len(lines), "total": str(total)}
                )
                status = "draft"
        facts = [
            self._fact(
                f"order_{order_id}",
                f"Draft order {order_id}: {items_text(lines)}, total {money(total, currency)}. "
                "Not placed until the customer confirms.",
            )
        ]
        if status != "draft":
            return self._ok(ctx, facts, duplicate=replay)
        confirm_id = await ctx.pending_actions.issue(
            client_id=ctx.client_id,
            conversation_id=conversation_id,
            contact_id=ctx.contact_id,
            agent=self.name,
            action="confirm",
            payload={"order_id": order_id},
            ttl=DRAFT_TTL,
        )
        cancel_id = await ctx.pending_actions.issue(
            client_id=ctx.client_id,
            conversation_id=conversation_id,
            contact_id=ctx.contact_id,
            agent=self.name,
            action="cancel",
            payload={"order_id": order_id},
            ttl=DRAFT_TTL,
        )
        ui = Ui(
            confirmation=UiConfirmation(
                required=True,
                order_id=order_id,
                expires_at=ctx.now() + DRAFT_TTL,
                accept=UiOption(label="Confirm order", action_id=confirm_id),
                reject=UiOption(label="Cancel", action_id=cancel_id),
            )
        )
        return self._ok(ctx, facts, ui, duplicate=replay)

    async def _insert_order(
        self,
        conn: psycopg.AsyncConnection[Any],
        ctx: AgentContext,
        channel: str,
        key: str,
        total: Decimal,
        currency: str,
    ) -> str:
        for _ in range(3):
            order_id = secrets.token_hex(5).upper()
            row = await db.q.fetch_one(
                "orders/insert_order.sql",
                (order_id, ctx.client_id, ctx.contact_id, total, currency, channel, key),
                conn=conn,
            )
            if row is not None:
                return order_id
        raise RuntimeError("order_id_collision")

    async def _event(
        self,
        conn: psycopg.AsyncConnection[Any],
        ctx: AgentContext,
        order_id: str,
        event: str,
        payload: dict[str, object],
    ) -> None:
        await db.q.fetch_one(
            "orders/insert_event.sql",
            (ctx.client_id, order_id, event, "customer", Jsonb(payload)),
            conn=conn,
        )

    async def _authorised(
        self, ctx: AgentContext, inp: OrderInput, conn: psycopg.AsyncConnection[Any], action: str
    ) -> bool:
        if ctx.callback_action_id is None or ctx.callback_action_id != inp.callback_id:
            return False
        row = await db.q.fetch_one(
            "orders/get_used_action.sql",
            (ctx.callback_action_id, ctx.client_id, ctx.contact_id),
            conn=conn,
        )
        return (
            row is not None
            and row["action"] == action
            and row["payload"].get("order_id") == inp.order_id
        )

    async def _confirm(self, ctx: AgentContext, inp: OrderInput, channel: str) -> AgentResult:
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            if not await self._authorised(ctx, inp, conn, "confirm"):
                return self._error(ctx, "not_authorised", "validation", "callback mismatch")
            order = await db.q.fetch_one(
                "orders/get_order_for_update.sql",
                (ctx.client_id, inp.order_id, ctx.contact_id),
                conn=conn,
            )
            if order is None:
                return self._error(ctx, "order_not_found", "not_found", "order not found")
            if order["status"] in {"pending_confirmation", "confirmed", "processing"}:
                return self._ok(
                    ctx,
                    [
                        self._fact(
                            f"order_{order['id']}", f"Order {order['id']} is already confirmed."
                        )
                    ],
                    duplicate=True,
                )
            if order["status"] != "draft":
                return self._error(
                    ctx,
                    "order_not_confirmable",
                    "conflict",
                    f"order is {order['status']}",
                    hint="That order can no longer be confirmed.",
                )
            await db.q.fetch_one(
                "orders/set_status.sql",
                (
                    "pending_confirmation",
                    "cod_pending",
                    True,
                    True,
                    False,
                    ctx.client_id,
                    order["id"],
                ),
                conn=conn,
            )
            await db.q.fetch_one(
                "orders/update_confirmation.sql",
                ("confirmed", ctx.client_id, order["id"]),
                conn=conn,
            )
            await self._event(conn, ctx, order["id"], "customer_confirmed", {})
            await notify(
                conn,
                client_id=ctx.client_id,
                event="approval_needed",
                priority="high",
                payload={
                    "order_id": order["id"],
                    "text": f"Total {money(order['total'], order['currency'])}, cash on delivery.",
                },
                dedupe_key=str(order["id"]),
            )
        return self._ok(
            ctx,
            [
                self._fact(
                    f"order_{order['id']}",
                    f"Order {order['id']} confirmed, "
                    f"total {money(order['total'], order['currency'])}, "
                    "payment on delivery. Awaiting fulfilment.",
                )
            ],
        )

    async def _cancel(self, ctx: AgentContext, inp: OrderInput, channel: str) -> AgentResult:
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            if not await self._authorised(ctx, inp, conn, "cancel"):
                return self._error(ctx, "not_authorised", "validation", "callback mismatch")
            order = await db.q.fetch_one(
                "orders/get_order_for_update.sql",
                (ctx.client_id, inp.order_id, ctx.contact_id),
                conn=conn,
            )
            if order is None:
                return self._error(ctx, "order_not_found", "not_found", "order not found")
            if order["status"] == "cancelled":
                return self._ok(
                    ctx,
                    [
                        self._fact(
                            f"order_{order['id']}", f"Order {order['id']} is already cancelled."
                        )
                    ],
                    duplicate=True,
                )
            if order["status"] not in {"draft", "pending_confirmation"}:
                return self._error(
                    ctx,
                    "order_not_cancellable",
                    "conflict",
                    f"order is {order['status']}",
                    hint="This order has already progressed; support can help with a return.",
                )
            lines = await db.q.fetch_all(
                "orders/list_items.sql", (ctx.client_id, order["id"]), conn=conn
            )
            per_product: dict[Any, int] = defaultdict(int)
            for line in lines:
                if line["product_id"] is not None:
                    per_product[line["product_id"]] += line["qty"]
            for product_id in sorted(per_product, key=str):
                await db.q.fetch_one(
                    "orders/release_stock.sql",
                    (per_product[product_id], product_id, ctx.client_id),
                    conn=conn,
                )
            await db.q.fetch_one(
                "orders/set_status.sql",
                (
                    "cancelled",
                    order["payment_status"],
                    False,
                    False,
                    True,
                    ctx.client_id,
                    order["id"],
                ),
                conn=conn,
            )
            await db.q.fetch_one(
                "orders/update_confirmation.sql",
                ("cancelled", ctx.client_id, order["id"]),
                conn=conn,
            )
            await self._event(conn, ctx, order["id"], "customer_cancelled", {})
        return self._ok(
            ctx, [self._fact(f"order_{order['id']}", f"Order {order['id']} has been cancelled.")]
        )


def _need_conversation(ctx: AgentContext) -> UUID:
    if ctx.conversation_id is None:
        raise RuntimeError("conversation_id_missing")
    return ctx.conversation_id
