"""The cycle usage summary: a company's ledger rows for one billing cycle, priced and billed.

``cycle_usage`` is what the Credit tab (Task 9) and the platform-admin overview (Task 10) read.
It runs on the caller's connection, already scoped to the company (``tx(pool, str(client_id))``),
and opens no transaction of its own. The internal types here carry raw cost, tokens and markup
for the admin view; the client API strips them.

The rules (spec "The pricing rules"):

- An execution is one distinct ``trace_id``. It belongs to the cycle whose billed window holds its
  first ledger row's Kuala Lumpur time, ``[window start 00:00, window end 00:00)``; its job is that
  first row's job, and every one of its rows counts, also rows after the window end.
- Its raw cost is the sum of its rows' raw costs, each priced at its own ``occurred_at``. A row
  with no price makes the whole execution unpriced: it is listed, counts toward the tier, and is
  left out of the AI charge (never priced at zero); the cycle is then ``pending_prices``.
- The stage and bundle come from ``billed_plan`` (in a termination's cycle, the plan before it).
  The markup is the tier the cycle's execution count reaches, and the fee the stage's fee
  prorated by the window's days; both are looked up at the cycle start, 00:00 Kuala Lumpur.
- Displayed prices are the AI charge allocated over the priced executions in RM0.0001, so the
  listed, per-agent and per-day prices all add up to it exactly.

Closed cycles never change because every config and plan table is effective-dated and
append-only; nothing here caches or freezes a figure.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Context, Decimal, Inexact
from typing import Any, Literal, cast
from uuid import UUID

import psycopg

from csa.billing.agents import AGENT_TITLES, agent_for
from csa.billing.config import BillingConfig, fee_for, load_config
from csa.billing.cycles import KL, Cycle, cycle_containing, cycle_of, cycle_range, days_in
from csa.billing.plans import Bundle, PlanRow, Stage, billed_plan, billed_window
from csa.billing.pricing import (
    Bill,
    allocate_prices,
    bill,
    execution_price,
    markup_pct,
    prorated_fee,
    raw_cost_rm,
)
from csa.core import db
from csa.metering.context import Job

__all__ = [
    "AgentTotal",
    "AllocatedExecution",
    "CyclePick",
    "CycleUsage",
    "DailyTotal",
    "ExecutionRow",
    "UsageStatus",
    "cycle_usage",
    "list_cycles",
]

UsageStatus = Literal["ok", "pending_prices", "not_set_up"]

# Sums of raw costs are exact: a rounding the context would make raises ``Inexact`` instead.
_CTX = Context(prec=60, traps=[Inexact])
_ZERO = Decimal(0)


@dataclass(frozen=True)
class ExecutionRow:
    """One execution (a distinct ``trace_id``): its first call's time and job, and its totals.

    ``raw_rm`` is the summed raw cost in RM, unrounded, or ``None`` when ``unpriced_calls`` of its
    rows had no price in force. Tokens are summed over every row, priced or not.
    """

    trace_id: UUID
    first_at: datetime
    job: Job
    agent_id: str
    raw_rm: Decimal | None
    prompt_tokens: int
    completion_tokens: int
    unpriced_calls: int


@dataclass(frozen=True)
class AllocatedExecution:
    """A priced execution: its markup, exact price, and allocated display price (RM0.0001)."""

    execution: ExecutionRow
    markup: Decimal
    exact_price: Decimal
    price: Decimal


@dataclass(frozen=True)
class AgentTotal:
    """One AI agent's executions in the cycle (priced or not) and their allocated prices' sum."""

    agent_id: str
    title: str
    executions: int
    price: Decimal


@dataclass(frozen=True)
class DailyTotal:
    """The allocated prices of the executions whose first call was on ``day`` (KL date)."""

    day: date
    price: Decimal


@dataclass(frozen=True)
class CyclePick:
    """A cycle for the picker: its bill total (``None`` when not set up) and whether it is final."""

    cycle: Cycle
    total: Decimal | None
    final: bool


@dataclass(frozen=True)
class CycleUsage:
    """One company's usage and bill for one cycle.

    ``status``: ``not_set_up`` when no plan row is dated before the cycle's end (no window, no
    bill); otherwise ``ok``, or ``pending_prices`` when an execution in the window is unpriced. A
    cycle at or after a termination (``window`` ``None``) is ``ok`` with a zero bill.

    ``executions`` (every execution in the window, priced or not) and ``priced`` (those with a raw
    cost) are newest first by ``(first_at, trace_id)``. ``price_by_trace`` maps each priced
    execution to its allocated price; an unpriced one is absent (its price is ``None``).
    ``by_agent`` lists every agent with an execution in the window and counts all of them, priced
    or not (an agent whose executions are all unpriced has price zero), highest price first then
    agent id; ``daily`` has one entry per KL date of the window, zero where nothing ran. Both are
    sums of the allocated prices, so each adds up to ``bill.ai_charge`` exactly.

    Admin warnings: ``usage_without_plan`` counts executions in the cycle before the plan starts
    (the whole cycle when not set up), ``usage_after_termination`` those at or after the termination
    date. Neither is billed. ``raw_cost_rm`` sums the priced executions' raw cost; tokens sum every
    execution in the window. ``markup`` is the cycle's tier markup percent (``None`` when nothing is
    billed); like raw cost and tokens it never reaches a client.
    """

    cycle: Cycle
    window: tuple[date, date] | None
    status: UsageStatus
    final: bool
    stage: Stage | None
    bundle: Bundle | None
    executions: list[ExecutionRow]
    priced: list[AllocatedExecution]
    price_by_trace: dict[UUID, Decimal]
    bill: Bill | None
    markup: Decimal | None
    by_agent: list[AgentTotal]
    daily: list[DailyTotal]
    unpriced_executions: int
    usage_without_plan: int
    usage_after_termination: int
    raw_cost_rm: Decimal
    prompt_tokens: int
    completion_tokens: int


def _midnight(d: date) -> datetime:
    """00:00 Kuala Lumpur on ``d``, as an aware datetime."""
    return datetime.combine(d, time(0), tzinfo=KL)


def _exact_sum(values: Sequence[Decimal]) -> Decimal:
    total = _ZERO
    for value in values:
        total = _CTX.add(total, value)
    return total


async def _check_scope(conn: psycopg.AsyncConnection[Any], client_id: UUID) -> None:
    """Refuse a connection scoped to another company (or none): it would read as no usage."""
    tenant = await db.q.fetch_one("billing/plans_tenant.sql", conn=conn)
    if tenant is None or tenant["client_id"] != client_id:
        raise RuntimeError(
            "billing usage: app.client_id is not this company; open tx(pool, client_id) first"
        )


async def _history(conn: psycopg.AsyncConnection[Any], client_id: UUID) -> list[PlanRow]:
    rows = await db.q.fetch_all("billing/plans_history.sql", (client_id,), conn=conn)
    return [PlanRow(**r) for r in rows]


async def _count(
    conn: psycopg.AsyncConnection[Any], client_id: UUID, start: date, end: date
) -> int:
    """Executions whose first row lies in ``[start, end)`` (KL dates); 0 for an empty range."""
    if start >= end:
        return 0
    row = await db.q.fetch_one(
        "billing/usage_trace_count.sql",
        {"client_id": client_id, "start": _midnight(start), "end": _midnight(end)},
        conn=conn,
    )
    if row is None:  # an aggregate always returns one row
        raise RuntimeError("usage_trace_count.sql returned no row")
    return int(row["n"])


def _executions(config: BillingConfig, rows: Sequence[dict[str, Any]]) -> list[ExecutionRow]:
    """Group ledger rows (each trace's rows in call order) into executions, newest first."""
    groups: dict[UUID, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row["trace_id"], []).append(row)
    executions = []
    for trace_id, calls in groups.items():
        first = calls[0]
        costs = [
            raw_cost_rm(
                config,
                c["provider"],
                c["model"],
                c["prompt_tokens"],
                c["completion_tokens"],
                c["occurred_at"],
            )
            for c in calls
        ]
        priced = [cost for cost in costs if cost is not None]
        unpriced = len(costs) - len(priced)
        job = cast(Job, first["job"])
        executions.append(
            ExecutionRow(
                trace_id=trace_id,
                first_at=first["occurred_at"],
                job=job,
                agent_id=agent_for(job)[0],
                raw_rm=None if unpriced else _exact_sum(priced),
                prompt_tokens=sum(c["prompt_tokens"] for c in calls),
                completion_tokens=sum(c["completion_tokens"] for c in calls),
                unpriced_calls=unpriced,
            )
        )
    executions.sort(key=lambda e: (e.first_at, e.trace_id), reverse=True)
    return executions


def _summarise(
    config: BillingConfig,
    cycle: Cycle,
    window: tuple[date, date],
    plan: PlanRow,
    rows: Sequence[dict[str, Any]],
    *,
    final: bool,
    usage_without_plan: int,
    usage_after_termination: int,
) -> CycleUsage:
    """Price and bill a billed window's ledger rows. Pure and CPU-bound: run it off the loop."""
    if plan.stage == "terminated":  # billed_plan never returns one; markup_pct would refuse it
        raise RuntimeError("a terminated plan row cannot price a billed window")
    at = _midnight(cycle.start)
    executions = _executions(config, rows)
    markup = markup_pct(config, plan.stage, plan.bundle, len(executions), at)
    fee = prorated_fee(
        fee_for(config, plan.stage, at), (window[1] - window[0]).days, days_in(cycle)
    )
    priced_rows = [e for e in executions if e.raw_rm is not None]
    exact = [execution_price(cast(Decimal, e.raw_rm), markup) for e in priced_rows]
    the_bill = bill(fee=fee, exact_prices=exact)
    # A cycle with nothing priced has a zero charge and nothing to allocate over.
    shares = allocate_prices(exact, the_bill.ai_charge) if priced_rows else []
    priced = [
        AllocatedExecution(execution=e, markup=markup, exact_price=x, price=p)
        for e, x, p in zip(priced_rows, exact, shares, strict=True)
    ]

    # Every execution counts toward its agent, priced or not; only priced ones add to its price,
    # so an agent whose executions are all unpriced is listed at zero.
    agent_prices: dict[str, Decimal] = {}
    agent_counts: dict[str, int] = {}
    for e in executions:
        agent_prices.setdefault(e.agent_id, _ZERO)
        agent_counts[e.agent_id] = agent_counts.get(e.agent_id, 0) + 1
    days = (window[1] - window[0]).days
    daily = {window[0] + timedelta(days=i): _ZERO for i in range(days)}
    for p in priced:
        agent = p.execution.agent_id
        agent_prices[agent] = _CTX.add(agent_prices[agent], p.price)
        day = p.execution.first_at.astimezone(KL).date()
        if day not in daily:
            raise RuntimeError(f"execution {p.execution.trace_id} lies outside the billed window")
        daily[day] = _CTX.add(daily[day], p.price)
    totals = [
        AgentTotal(a, AGENT_TITLES[a], agent_counts[a], price) for a, price in agent_prices.items()
    ]
    # Highest price first, ties by agent id (two stable sorts: no negated Decimal needed).
    by_agent = sorted(sorted(totals, key=lambda t: t.agent_id), key=lambda t: t.price, reverse=True)
    unpriced = len(executions) - len(priced_rows)
    return CycleUsage(
        cycle=cycle,
        window=window,
        status="pending_prices" if unpriced else "ok",
        final=final,
        stage=plan.stage,
        bundle=plan.bundle,
        executions=executions,
        priced=priced,
        price_by_trace={p.execution.trace_id: p.price for p in priced},
        bill=the_bill,
        markup=markup,
        by_agent=by_agent,
        daily=[DailyTotal(d, price) for d, price in daily.items()],
        unpriced_executions=unpriced,
        usage_without_plan=usage_without_plan,
        usage_after_termination=usage_after_termination,
        raw_cost_rm=_exact_sum([cast(Decimal, e.raw_rm) for e in priced_rows]),
        prompt_tokens=sum(e.prompt_tokens for e in executions),
        completion_tokens=sum(e.completion_tokens for e in executions),
    )


def _unbilled(
    cycle: Cycle,
    status: UsageStatus,
    *,
    final: bool,
    the_bill: Bill | None,
    usage_without_plan: int,
    usage_after_termination: int,
) -> CycleUsage:
    """A cycle with no billed window: not set up, or at or after a termination."""
    return CycleUsage(
        cycle=cycle,
        window=None,
        status=status,
        final=final,
        stage=None,
        bundle=None,
        executions=[],
        priced=[],
        price_by_trace={},
        bill=the_bill,
        markup=None,
        by_agent=[],
        daily=[],
        unpriced_executions=0,
        usage_without_plan=usage_without_plan,
        usage_after_termination=usage_after_termination,
        raw_cost_rm=_ZERO,
        prompt_tokens=0,
        completion_tokens=0,
    )


async def _cycle_usage(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    cycle: Cycle,
    now: datetime,
    config: BillingConfig,
    history: Sequence[PlanRow],
) -> CycleUsage:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must carry a timezone")
    final = now >= _midnight(cycle.end)
    join = min((r.effective_from for r in history), default=None)
    terminated = min((r.effective_from for r in history if r.stage == "terminated"), default=None)
    # Usage outside the billed window, by first call: before the plan starts, or from the
    # termination date. The two ranges never overlap (a termination is not before the join).
    before_plan_end = cycle.end if join is None else min(join, cycle.end)
    usage_without_plan = await _count(conn, client_id, cycle.start, before_plan_end)
    usage_after_termination = 0
    if terminated is not None and terminated < cycle.end:
        usage_after_termination = await _count(
            conn, client_id, max(cycle.start, terminated), cycle.end
        )

    window = billed_window(history, cycle)
    if window is None:
        if terminated is not None and terminated < cycle.end:
            return _unbilled(
                cycle,
                "ok",
                final=final,
                the_bill=bill(fee=_ZERO, exact_prices=[]),
                usage_without_plan=usage_without_plan,
                usage_after_termination=usage_after_termination,
            )
        return _unbilled(
            cycle,
            "not_set_up",
            final=final,
            the_bill=None,
            usage_without_plan=usage_without_plan,
            usage_after_termination=usage_after_termination,
        )
    plan = billed_plan(history, cycle)
    if plan is None:  # billed_plan is None only when billed_window is
        raise RuntimeError(f"cycle {cycle.start.isoformat()} has a billed window but no plan")
    rows = await db.q.fetch_all(
        "billing/usage_executions.sql",
        {"client_id": client_id, "start": _midnight(window[0]), "end": _midnight(window[1])},
        conn=conn,
    )
    return await asyncio.to_thread(
        _summarise,
        config,
        cycle,
        window,
        plan,
        rows,
        final=final,
        usage_without_plan=usage_without_plan,
        usage_after_termination=usage_after_termination,
    )


async def cycle_usage(
    conn: psycopg.AsyncConnection[Any], client_id: UUID, cycle: Cycle, *, now: datetime
) -> CycleUsage:
    """The usage summary and bill of ``client_id`` for ``cycle``, as of ``now`` (aware).

    ``conn`` must be scoped to ``client_id`` (``tx(pool, str(client_id))``); another scope raises
    ``RuntimeError``. A missing FX rate, fee or markup tier raises the config error.
    """
    await _check_scope(conn, client_id)
    config = await load_config(conn)
    history = await _history(conn, client_id)
    return await _cycle_usage(conn, client_id, cycle, now, config, history)


async def list_cycles(
    conn: psycopg.AsyncConnection[Any], client_id: UUID, now: datetime
) -> list[CyclePick]:
    """Every cycle from the first plan row's or first ledger row's cycle to the current one.

    Newest first, each with its bill total (``None`` when not set up) and ``final`` flag. With no
    plan and no usage, just the current cycle. ``conn`` is scoped to ``client_id``, as for
    ``cycle_usage``.
    """
    await _check_scope(conn, client_id)
    config = await load_config(conn)
    history = await _history(conn, client_id)
    current = cycle_of(now)
    starts = [current]
    if history:
        starts.append(cycle_containing(min(r.effective_from for r in history)))
    first = await db.q.fetch_one("billing/usage_first.sql", (client_id,), conn=conn)
    if first is not None and first["first_at"] is not None:
        starts.append(cycle_of(first["first_at"]))
    earliest = min(starts, key=lambda c: c.start)
    picks = []
    for cycle in reversed(cycle_range(earliest, current)):
        usage = await _cycle_usage(conn, client_id, cycle, now, config, history)
        total = None if usage.bill is None else usage.bill.total
        picks.append(CyclePick(cycle=cycle, total=total, final=usage.final))
    return picks
