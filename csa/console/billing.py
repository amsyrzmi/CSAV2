"""The Credit tab's API under ``/console/v1/billing``: a company's usage and bill, prices only.

Client routes (this module, Task 9): ``GET /usage`` and ``GET /usage/cycles``, for the company's
Admin or a platform admin who picks the company with ``client_id``. Everyone else, staff,
read-only members and the machine principal included, gets 403: a non-Admin member seeing billing
is an anti-goal. Task 10 adds the platform-admin routes to the router ``build_billing_router``
returns.

What leaves here is prices only (spec "What the client sees"): the bill, the per-agent and per-day
charges and the execution counts. Owner decision 2026-11-13: there is no per-execution list, so
the client never sees single executions or their jobs. The service's ``CycleUsage`` also carries
raw cost, tokens and the markup for the admin view, so every response model is built by copying
named fields, never by dumping a dataclass or dict, and every model forbids extra fields. Nothing
here logs. Money is a fixed-point decimal string: 2 places for the fee, AI usage price and total,
4 for the per-agent and per-day sums of the allocated prices, which add up to the AI usage price
exactly. A value that would need rounding to fit is an error, never rounded here.

The platform-admin routes (Task 10: raw cost, tokens and margin) are in
``csa.console.billing_admin`` under ``/billing/admin``, with models of their own; nothing here
imports them.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Context, Decimal, Inexact
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import AfterValidator, BaseModel, ConfigDict

from csa.billing.cycles import Cycle, cycle_containing, cycle_of
from csa.billing.memo import USAGE_MEMO
from csa.billing.service import CycleUsage, cycle_usage, list_cycles
from csa.console.access import tenant
from csa.console.contracts import ConsoleActor, Role
from csa.core.tenancy import tx

__all__ = [
    "AgentUsageOut",
    "CyclePickOut",
    "DailyUsageOut",
    "UsageOut",
    "billing_client",
    "billing_now",
    "build_billing_router",
    "pick_cycle",
]

_CYCLE_START_DAY = 5
_ADMIN_REQUIRED = "billing_admin_required"
# Quantizing under this context raises instead of rounding a value that does not fit.
_EXACT = Context(prec=60, traps=[Inexact])


def _fixed(places: int) -> Callable[[Decimal], Decimal]:
    unit = Decimal(1).scaleb(-places)

    def exactly(value: Decimal) -> Decimal:
        try:
            return value.quantize(unit, context=_EXACT)
        except Inexact:
            raise ValueError(f"{value} does not fit {places} decimal places") from None

    return exactly


# A bill figure (RM0.01) and an allocated price (RM0.0001), each serialised as a string.
Money2 = Annotated[Decimal, AfterValidator(_fixed(2))]
Money4 = Annotated[Decimal, AfterValidator(_fixed(4))]


class AgentUsageOut(BaseModel):
    """One AI agent's executions in the cycle (priced or not) and their allocated price."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    title: str
    executions: int
    price: Money4


class DailyUsageOut(BaseModel):
    """The allocated price of the executions that started on ``day`` (Kuala Lumpur date)."""

    model_config = ConfigDict(extra="forbid")

    day: date
    price: Money4


class UsageOut(BaseModel):
    """One cycle's usage and bill for the Credit tab.

    ``billed_from`` and ``billed_to`` (exclusive) are the billed window, ``None`` when billing is
    not set up or the company was terminated before the cycle. ``executions`` counts every
    execution in the window, priced or not. ``final`` is false for the open cycle (the bill so
    far). ``status`` ``pending_prices`` means some usage is still being priced and is left out of
    ``ai_usage_price``; ``not_set_up`` has every figure zero. ``terminated_on`` is the date the
    company's plan ended when that is before ``cycle_end`` (this cycle holds the final period, or
    the plan ended before it), else ``None``: a date only, no reason.
    """

    model_config = ConfigDict(extra="forbid")

    cycle_start: date
    cycle_end: date
    billed_from: date | None
    billed_to: date | None
    executions: int
    ai_usage_price: Money2
    platform_fee: Money2
    total: Money2
    final: bool
    status: Literal["ok", "pending_prices", "not_set_up"]
    by_agent: list[AgentUsageOut]
    daily: list[DailyUsageOut]
    terminated_on: date | None


class CyclePickOut(BaseModel):
    """A cycle for the picker: its bill total (``None`` when not set up) and whether it is final."""

    model_config = ConfigDict(extra="forbid")

    start: date
    end: date
    total: Money2 | None
    final: bool


def billing_now() -> datetime:
    """The clock the billing routes read (a dependency, so tests can pin it)."""
    return datetime.now(UTC)


def billing_client(request: Request, client_id: Annotated[UUID, Depends(tenant)]) -> UUID:
    """The company whose billing may be read: the caller's own as its Admin, or any company a
    platform admin selects. Staff, read-only members and the machine principal are refused."""
    actor = request.state.console_actor
    if not isinstance(actor, ConsoleActor):
        raise HTTPException(403, _ADMIN_REQUIRED)
    if actor.platform_admin:
        return client_id
    if actor.role != Role.TENANT_ADMIN or actor.client_id != client_id:
        raise HTTPException(403, _ADMIN_REQUIRED)
    return client_id


def pick_cycle(start: date | None, now: datetime) -> Cycle:
    """The cycle starting on ``start``, or the current one; 422 off a 5th, 404 in the future."""
    current = cycle_of(now)
    if start is None:
        return current
    if start.day != _CYCLE_START_DAY:
        raise HTTPException(422, "cycle_must_start_on_a_fifth")
    # A 5th is its own cycle's start, so a future cycle is refused before it is built: the
    # cycle starting 9999-12-05 would end in year 10000, which ``date`` cannot hold.
    if start > current.start:
        raise HTTPException(404, "cycle_not_found")
    return cycle_containing(start)


def usage_out(usage: CycleUsage) -> UsageOut:
    """The client view of a cycle: named price fields copied one by one, nothing else."""
    zero = Decimal("0.00")
    bill = usage.bill
    window = usage.window
    return UsageOut(
        cycle_start=usage.cycle.start,
        cycle_end=usage.cycle.end,
        billed_from=None if window is None else window[0],
        billed_to=None if window is None else window[1],
        executions=len(usage.executions),
        ai_usage_price=zero if bill is None else bill.ai_charge,
        platform_fee=zero if bill is None else bill.platform_fee,
        total=zero if bill is None else bill.total,
        final=usage.final,
        status=usage.status,
        by_agent=[
            AgentUsageOut(
                agent_id=agent.agent_id,
                title=agent.title,
                executions=agent.executions,
                price=agent.price,
            )
            for agent in usage.by_agent
        ],
        daily=[DailyUsageOut(day=day.day, price=day.price) for day in usage.daily],
        terminated_on=usage.terminated_on,
    )


def build_billing_router() -> APIRouter:
    """The ``/billing`` routes, mounted under ``/console/v1`` by ``csa.console.router``."""
    router = APIRouter(prefix="/billing")

    @router.get("/usage", response_model=UsageOut)
    async def usage(
        request: Request,
        client_id: Annotated[UUID, Depends(billing_client)],
        now: Annotated[datetime, Depends(billing_now)],
        cycle: Annotated[date | None, Query()] = None,
    ) -> UsageOut:
        picked = pick_cycle(cycle, now)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            summary = await cycle_usage(conn, client_id, picked, now=now, memo=USAGE_MEMO)
        return usage_out(summary)

    @router.get("/usage/cycles", response_model=list[CyclePickOut])
    async def cycles(
        request: Request,
        client_id: Annotated[UUID, Depends(billing_client)],
        now: Annotated[datetime, Depends(billing_now)],
    ) -> list[CyclePickOut]:
        async with tx(request.app.state.pool, str(client_id)) as conn:
            picks = await list_cycles(conn, client_id, now, memo=USAGE_MEMO)
        return [
            CyclePickOut(start=p.cycle.start, end=p.cycle.end, total=p.total, final=p.final)
            for p in picks
        ]

    return router
