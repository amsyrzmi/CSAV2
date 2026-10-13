"""Staff order approval: approve commits stock, reject releases it (LG05 staff path).

This agent is never planned from a customer turn; it is only reachable by a staff
path that supplies a ``staff_target_id`` belonging to the tenant.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Literal
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from csa.agents.base import AgentContext, SideEffects
from csa.agents.facts import money
from csa.contracts.facts import AgentError, AgentResult, AgentResultData, Fact, ResultMeta
from csa.contracts.plan import RiskClass
from csa.core import db
from csa.core.tenancy import tx


class ApprovalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["approve", "reject"]
    order_id: str = Field(min_length=1, max_length=64)
    staff_target_id: UUID
    reason: str | None = Field(default=None, max_length=500)


class OrderApprovalAgent:
    name = "order_approval"
    Input = ApprovalInput
    side_effects: SideEffects = "irreversible"
    timeout_ms = 15000
    max_retries = 0

    def risk(self, inp: BaseModel) -> RiskClass:
        return "commit"

    def _result(
        self, ctx: AgentContext, text: str, fact_id: str, *, dup: bool = False
    ) -> AgentResult:
        return AgentResult(
            ok=True,
            status="ok",
            task_id=ctx.idempotency_key,
            agent=self.name,
            data=AgentResultData(facts=[Fact(id=fact_id, text=text, source=self.name)]),
            meta=ResultMeta(trace_id=ctx.trace_id, latency_ms=0, was_duplicate=dup),
        )

    def _error(self, ctx: AgentContext, code: str, category: Any, message: str) -> AgentResult:
        return AgentResult(
            ok=False,
            status="error",
            task_id=ctx.idempotency_key,
            agent=self.name,
            data=AgentResultData(),
            error=AgentError(code=code, category=category, message=message),
            meta=ResultMeta(trace_id=ctx.trace_id, latency_ms=0),
        )

    async def run(self, ctx: AgentContext, inp: BaseModel) -> AgentResult:
        assert isinstance(inp, ApprovalInput)
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            target = await db.q.fetch_one(
                "notify/get_target.sql", (ctx.client_id, inp.staff_target_id), conn=conn
            )
            if target is None:
                return self._error(ctx, "staff_not_authorised", "validation", "unknown staff")
            order = await db.q.fetch_one(
                "orders/get_order_staff.sql", (ctx.client_id, inp.order_id), conn=conn
            )
            if order is None:
                return self._error(ctx, "order_not_found", "not_found", "order not found")
            fact_id = f"order_{order['id']}"
            target_state = "confirmed" if inp.action == "approve" else "cancelled"
            if order["status"] == target_state:
                return self._result(
                    ctx, f"Order {order['id']} is already {target_state}.", fact_id, dup=True
                )
            if order["status"] != "pending_confirmation":
                return self._error(
                    ctx, "order_not_pending", "conflict", f"order is {order['status']}"
                )
            lines = await db.q.fetch_all(
                "orders/list_items_for_approval.sql", (ctx.client_id, order["id"]), conn=conn
            )
            per_product: dict[Any, int] = defaultdict(int)
            for line in lines:
                if line["product_id"] is not None:
                    per_product[line["product_id"]] += line["qty"]
            for product_id in sorted(per_product, key=str):
                qty = per_product[product_id]
                if inp.action == "approve":
                    await db.q.fetch_one(
                        "orders/commit_stock.sql",
                        (qty, qty, product_id, ctx.client_id),
                        conn=conn,
                    )
                else:
                    await db.q.fetch_one(
                        "orders/release_stock.sql", (qty, product_id, ctx.client_id), conn=conn
                    )
            await db.q.fetch_one(
                "orders/set_status.sql",
                (
                    target_state,
                    order["payment_status"],
                    False,
                    inp.action == "approve",
                    inp.action == "reject",
                    ctx.client_id,
                    order["id"],
                ),
                conn=conn,
            )
            await db.q.fetch_one(
                "orders/insert_event.sql",
                (
                    ctx.client_id,
                    order["id"],
                    "staff_approved" if inp.action == "approve" else "staff_rejected",
                    "staff",
                    Jsonb({"target_id": str(inp.staff_target_id), "reason": inp.reason}),
                ),
                conn=conn,
            )
        verb = "approved" if inp.action == "approve" else "rejected"
        return self._result(
            ctx,
            f"Order {order['id']} {verb}, total {money(order['total'], order['currency'])}.",
            fact_id,
        )
