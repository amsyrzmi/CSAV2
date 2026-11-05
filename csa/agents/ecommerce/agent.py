"""The customer-facing E-commerce spoke: read-only TikTok Shop answers (spec 6.7).

Two questions only: the status of a TikTok order by its TikTok order number, and the stock of
an item that is not in the client's own catalogue but is listed on its TikTok Shop. Nothing
here writes to TikTok, and the planner cannot reach any write (``ACTION_RISK`` is read-only).

The agent words the answers; a ``MarketplaceReader`` (``csa.marketplace.reads``) does the
reads. An order number alone never reveals the buyer, the items, the totals or the ship-to.
Nothing here logs an order number, a tracking number or a query.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Protocol

from pydantic import BaseModel

from csa.agents.base import AgentContext, SideEffects
from csa.agents.ecommerce.models import (
    ORDER_NUMBER_PLACEHOLDER_PATTERN,
    EcommerceInput,
    ListingChoice,
    LiveStock,
    OrderLookup,
    OrderStatusFact,
    StockLookup,
)
from csa.agents.facts import money
from csa.contracts.facts import (
    AgentError,
    AgentResult,
    AgentResultData,
    Fact,
    ResultMeta,
)
from csa.contracts.plan import RiskClass

ACTION_RISK: dict[str, RiskClass] = {
    "order_status": "read",
    "stock_check": "read",
}

# Fact ids are constants: they are recorded in agent_runs, so they never carry an order number.
# ORDER_LOOKUP_FACT_IDS are the answers that count towards the per-conversation lookup limit.
ORDER_FOUND_FACT = "tiktok_order_status"
ORDER_NOT_FOUND_FACT = "tiktok_order_not_found"
ORDER_LIMIT_FACT = "tiktok_order_lookup_limit"
ORDER_LOOKUP_FACT_IDS = (ORDER_FOUND_FACT, ORDER_NOT_FOUND_FACT)
STOCK_FACT = "tiktok_stock"
STOCK_CHOICES_FACT = "tiktok_stock_choices"
STOCK_NOT_FOUND_FACT = "tiktok_stock_not_found"

ORDER_NOT_FOUND_TEXT = (
    "I couldn't find that TikTok order number. Please check it, or ask our staff."
)
ORDER_LIMIT_TEXT = "Please ask our staff for more help."
STOCK_NOT_FOUND_TEXT = (
    "I couldn't find that item in our shop or on our TikTok Shop. Our staff can help you."
)
STOCK_UNCHECKED_TEXT = "I can't check right now. Our staff will confirm."
# A placeholder the turn could not resolve from the raw message: ask for the number again.
ORDER_NUMBER_UNREADABLE_HINT = (
    "I couldn't read that TikTok order number. Could you send it again, digits only?"
)
_PLACEHOLDER = re.compile(ORDER_NUMBER_PLACEHOLDER_PATTERN)
# A copy younger than this may be quoted, with its age, when the live check fails.
COPY_QUOTABLE_FOR = timedelta(hours=6)


class MarketplaceReader(Protocol):
    """The reads behind the agent; ``csa.marketplace.reads.PostgresMarketplaceReader``."""

    async def order_status(self, ctx: AgentContext, order_number: str) -> OrderLookup: ...

    async def stock_check(self, ctx: AgentContext, query: str) -> StockLookup: ...


class EcommerceAgent:
    """Read-only TikTok Shop answers for customers; there is no default reader."""

    name = "ecommerce_agent"
    Input = EcommerceInput
    side_effects: SideEffects = "none"
    # The live stock read bounds itself (csa.marketplace.reads.LIVE_READ_TIMEOUT_SECONDS) and
    # falls back to the copy, inside the 20 s spoke budget.
    timeout_ms = 15000
    # A signed read is never retried, and a retry would count a lookup twice.
    max_retries = 0
    ACTION_RISK = ACTION_RISK

    def __init__(self, reader: MarketplaceReader) -> None:
        self._reader = reader

    def risk(self, inp: BaseModel) -> RiskClass:
        assert isinstance(inp, EcommerceInput)
        return ACTION_RISK[inp.action]

    async def run(self, ctx: AgentContext, inp: BaseModel) -> AgentResult:
        if not isinstance(inp, EcommerceInput):
            return self._error(ctx, "invalid_input", "validation", "Expected EcommerceInput")
        if inp.action == "order_status":
            assert inp.order_number is not None
            if _PLACEHOLDER.match(inp.order_number):
                # Never looked up: only the resolved digits are an order number.
                return self._error(
                    ctx,
                    "order_number_unreadable",
                    "validation",
                    "order number placeholder not resolved",
                    hint=ORDER_NUMBER_UNREADABLE_HINT,
                )
            lookup = await self._reader.order_status(ctx, inp.order_number)
            return self._ok(ctx, [self._order_fact(lookup)])
        assert inp.query is not None
        stock = await self._reader.stock_check(ctx, inp.query)
        return self._ok(ctx, [self._stock_fact(stock, ctx.now())])

    # -- wording ----------------------------------------------------------------------------

    def _order_fact(self, lookup: OrderLookup) -> Fact:
        if lookup.outcome == "limited":
            return self._fact(ORDER_LIMIT_FACT, ORDER_LIMIT_TEXT)
        if lookup.fact is None:
            return self._fact(ORDER_NOT_FOUND_FACT, ORDER_NOT_FOUND_TEXT)
        return self._fact(ORDER_FOUND_FACT, order_status_text(lookup.fact))

    def _stock_fact(self, lookup: StockLookup, now: datetime) -> Fact:
        if lookup.outcome == "choices":
            return self._fact(STOCK_CHOICES_FACT, choices_text(lookup.choices))
        stock = lookup.stock
        sku = stock.asked() if stock is not None else None
        # A listing that is not selling is never described as available (fail closed).
        if stock is None or sku is None or not stock.sellable:
            return self._fact(STOCK_NOT_FOUND_FACT, STOCK_NOT_FOUND_TEXT)
        label = _label(stock.product_name, sku.variant_name)
        price = f", {money(sku.price, sku.currency)}" if sku.price is not None else ""
        qty = sku.stock_qty
        if stock.checked_live and qty is not None:
            text = f"{label}{price}: {qty} available on our TikTok Shop."
        elif qty is not None and now - stock.as_of < COPY_QUOTABLE_FOR:
            age = _age(now - stock.as_of)
            text = f"{label}{price} on our TikTok Shop: {qty} were available as of {age} ago."
        else:
            text = f"{label} on our TikTok Shop: {STOCK_UNCHECKED_TEXT}"
        return Fact(id=STOCK_FACT, text=text, source=self.name, as_of=stock.as_of)

    # -- results ----------------------------------------------------------------------------

    def _fact(self, fact_id: str, text: str) -> Fact:
        return Fact(id=fact_id, text=text, source=self.name)

    def _ok(self, ctx: AgentContext, facts: list[Fact]) -> AgentResult:
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


def order_status_text(fact: OrderStatusFact) -> str:
    """Plain words from the fact's own fields only; nothing else can reach the reply."""
    text = f"Your TikTok order is {fact.status_text}."
    if fact.shipped_or_delivered_on is not None:
        day = fact.shipped_or_delivered_on.strftime("%d %b %Y")
        text += f" Last update: {day}."
    if fact.tracking_no:
        text += f" Tracking number: {fact.tracking_no}"
        text += f" ({fact.carrier})." if fact.carrier else "."
    return text


def choices_text(choices: tuple[ListingChoice, ...]) -> str:
    listed = "; ".join(
        f"{index}. {_label(choice.product_name, choice.variant_name)}"
        + (f" (SKU {choice.seller_sku})" if choice.seller_sku else "")
        for index, choice in enumerate(choices, start=1)
    )
    return f"Several items on our TikTok Shop match. Which one do you mean? {listed}"


def _label(product_name: str, variant_name: str) -> str:
    return f"{product_name} ({variant_name})" if variant_name else product_name


def _age(age: timedelta) -> str:
    minutes = max(int(age.total_seconds() // 60), 0)
    if minutes < 1:
        return "less than a minute"
    if minutes < 60:
        return "1 minute" if minutes <= 1 else f"{minutes} minutes"
    hours = minutes // 60
    return "1 hour" if hours == 1 else f"{hours} hours"


__all__ = [
    "ACTION_RISK",
    "COPY_QUOTABLE_FOR",
    "ORDER_LIMIT_TEXT",
    "ORDER_LOOKUP_FACT_IDS",
    "ORDER_NOT_FOUND_TEXT",
    "STOCK_UNCHECKED_TEXT",
    "EcommerceAgent",
    "LiveStock",
    "MarketplaceReader",
]
