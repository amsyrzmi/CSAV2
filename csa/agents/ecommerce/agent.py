"""E-Commerce Operations Agent spoke (LG05/LG13)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from csa.agents.base import AgentContext, SideEffects
from csa.agents.ecommerce.adapters.base import EcommercePlatformAdapter
from csa.agents.ecommerce.models import EcommerceInput, InventorySyncItem
from csa.contracts.facts import (
    AgentError,
    AgentResult,
    AgentResultData,
    Fact,
    ResultMeta,
)
from csa.contracts.plan import RiskClass

ACTION_RISK: dict[str, RiskClass] = {
    "get_order": "read",
    "search_orders": "read",
    "get_product": "read",
    "sync_inventory": "commit",
    "fulfill_order": "commit",
    "cancel_order": "commit",
    "approve_refund": "commit",
}


class EcommerceAgent:
    """Spoke agent automating cross-channel e-commerce operations (TikTok Shop, Shopee, Lazada)."""

    ACTION_RISK = ACTION_RISK

    def __init__(
        self,
        adapters: dict[str, EcommercePlatformAdapter] | None = None,
    ) -> None:
        self._adapters: dict[str, EcommercePlatformAdapter] = adapters or {}

    @property
    def name(self) -> str:
        return "ecommerce_agent"

    @property
    def Input(self) -> type[BaseModel]:  # noqa: N802
        return EcommerceInput

    @property
    def side_effects(self) -> SideEffects:
        # Some operations (inventory sync, fulfillment, cancellation) modify state
        return "reversible"

    @property
    def timeout_ms(self) -> int:
        return 15000

    @property
    def max_retries(self) -> int:
        return 1

    def risk(self, inp: BaseModel) -> RiskClass:
        if isinstance(inp, EcommerceInput):
            return ACTION_RISK.get(inp.action, "commit")
        return "commit"

    def register_adapter(self, adapter: EcommercePlatformAdapter) -> None:
        self._adapters[adapter.platform_name] = adapter

    def get_adapter(self, platform: str) -> EcommercePlatformAdapter:
        adapter = self._adapters.get(platform)
        if not adapter:
            raise ValueError(f"No configured e-commerce adapter for platform '{platform}'")
        return adapter

    def _fact(self, fact_id: str, text: str) -> Fact:
        return Fact(id=fact_id, text=text, source=self.name)

    def _ok(
        self,
        ctx: AgentContext,
        facts: list[Fact],
        *,
        raw: dict[str, Any] | None = None,
    ) -> AgentResult:
        return AgentResult(
            ok=True,
            status="ok",
            task_id=ctx.idempotency_key,
            agent=self.name,
            data=AgentResultData(facts=facts),
            meta=ResultMeta(trace_id=ctx.trace_id, latency_ms=0),
        )

    def _error(
        self,
        ctx: AgentContext,
        code: str,
        category: Any,
        message: str,
        *,
        hint: str | None = None,
        requires_human: bool = False,
    ) -> AgentResult:
        return AgentResult(
            ok=False,
            status="error",
            task_id=ctx.idempotency_key,
            agent=self.name,
            data=AgentResultData(),
            requires_human=requires_human,
            error=AgentError(
                code=code,
                category=category,
                message=message,
                customer_safe_hint=hint,
            ),
            meta=ResultMeta(trace_id=ctx.trace_id, latency_ms=0),
        )

    async def run(self, ctx: AgentContext, inp: BaseModel) -> AgentResult:
        if not isinstance(inp, EcommerceInput):
            return self._error(
                ctx,
                "invalid_input",
                "validation",
                "Expected EcommerceInput",
            )

        try:
            adapter = self.get_adapter(inp.platform)
        except ValueError as err:
            return self._error(
                ctx,
                "unsupported_platform",
                "validation",
                str(err),
            )

        try:
            if inp.action == "get_order":
                assert inp.order_id is not None
                order = await adapter.get_order(ctx.client_id, inp.order_id)
                items_desc = ", ".join(f"{it.quantity}x {it.sku}" for it in order.items)
                desc = (
                    f"Order {order.platform_order_id} ({order.platform}): {order.order_status}, "
                    f"total {order.total_amount} {order.currency}, items: [{items_desc}]"
                )
                if order.tracking_number:
                    desc += f", tracking: {order.tracking_number}"
                fact = self._fact(f"ecom_order_{order.platform_order_id}", desc)
                return self._ok(ctx, [fact])

            if inp.action == "search_orders":
                orders = await adapter.search_orders(
                    ctx.client_id, status=inp.status_filter, limit=inp.limit
                )
                facts = [
                    self._fact(
                        f"ecom_order_{order.platform_order_id}",
                        f"Order {order.platform_order_id}: {order.order_status}, "
                        f"total {order.total_amount} {order.currency}",
                    )
                    for order in orders
                ]
                return self._ok(ctx, facts)

            if inp.action == "get_product":
                assert inp.sku is not None
                product_data = await adapter.get_product(ctx.client_id, inp.sku)
                fact = self._fact(
                    f"ecom_prod_{inp.sku}",
                    f"Product {inp.sku}: {product_data.get('title', 'Unknown')}",
                )
                return self._ok(ctx, [fact], raw=product_data)

            if inp.action == "sync_inventory":
                items: list[InventorySyncItem] = inp.inventory_items
                if not items and inp.sku is not None:
                    items = [InventorySyncItem(sku=inp.sku, available_qty=0)]

                sync_res = await adapter.sync_inventory(ctx.client_id, items)
                fact = self._fact(
                    f"ecom_sync_{inp.platform}",
                    f"Synced {len(items)} items to {inp.platform}",
                )
                return self._ok(ctx, [fact], raw=sync_res)

            if inp.action == "fulfill_order":
                assert inp.order_id is not None
                assert inp.tracking_number is not None
                await adapter.fulfill_order(
                    ctx.client_id, inp.order_id, inp.tracking_number, inp.shipping_carrier
                )
                fact = self._fact(
                    f"ecom_ship_{inp.order_id}",
                    f"Order {inp.order_id} fulfilled with tracking {inp.tracking_number}",
                )
                return self._ok(ctx, [fact])

            # High-risk actions cancel_order and approve_refund:
            return self._error(
                ctx,
                "action_requires_approval",
                "validation",
                f"Action '{inp.action}' requires staff HITL approval.",
                requires_human=True,
            )

        except Exception as err:
            return self._error(
                ctx,
                "ecommerce_platform_error",
                "upstream",
                str(err),
            )
