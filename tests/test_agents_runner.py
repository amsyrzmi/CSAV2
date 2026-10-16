"""Spoke wrapper behaviour without Docker (LG05)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from csa.agents.base import AgentContext, SideEffects
from csa.agents.breaker import CircuitBreaker
from csa.agents.runner import SpokeRunner
from csa.contracts.facts import AgentError, AgentResult, AgentResultData, ResultMeta
from csa.contracts.plan import RiskClass, Task
from csa.contracts.registry import CapabilityRegistry
from pydantic import BaseModel


class _In(BaseModel):
    n: int


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class _Agent:
    name = "t_agent"
    Input = _In
    side_effects: SideEffects = "none"
    timeout_ms = 50
    max_retries = 1

    def __init__(self, behaviours: list[str]) -> None:
        self.behaviours = behaviours
        self.calls = 0

    def risk(self, inp: BaseModel) -> RiskClass:
        return "read"

    async def run(self, ctx: AgentContext, inp: BaseModel) -> AgentResult:
        behaviour = self.behaviours[min(self.calls, len(self.behaviours) - 1)]
        self.calls += 1
        if behaviour == "hang":
            await asyncio.sleep(5)
        if behaviour == "boom":
            raise RuntimeError("boom")
        if behaviour == "retryable":
            return _result(ctx, ok=False, retryable=True)
        return _result(ctx, ok=True)


def _result(ctx: AgentContext, *, ok: bool, retryable: bool = False) -> AgentResult:
    return AgentResult(
        ok=ok,
        status="ok" if ok else "error",
        task_id="t1",
        agent="t_agent",
        data=AgentResultData(),
        error=None
        if ok
        else AgentError(code="x", category="upstream", message="x", retryable=retryable),
        meta=ResultMeta(trace_id=ctx.trace_id, latency_ms=0),
    )


def _ctx() -> AgentContext:
    return AgentContext(
        client_id=uuid4(),
        contact_id=uuid4(),
        trace_id=uuid4(),
        conversation_id=uuid4(),
        idempotency_key="k",
        channel="telegram",
        pool=None,  # type: ignore[arg-type]
        pending_actions=None,  # type: ignore[arg-type]
    )


def _runner(agent: Any, breaker: CircuitBreaker | None = None) -> SpokeRunner:
    return SpokeRunner({agent.name: agent}, breaker=breaker, backoff_s=0)


def _task(agent: str = "t_agent", **inp: object) -> Task:
    return Task(task_id="t1", agent=agent, input=inp or {"n": 1})


async def test_ok() -> None:
    result = await _runner(_Agent(["ok"])).execute(_task(), _ctx())
    assert result.ok


@pytest.mark.parametrize(
    "name,action",
    [("kb_agent", "query"), ("order_agent", "search_products"), ("order_agent", "product_detail")],
)
async def test_web_sandbox_allows_only_knowledge_and_product_reads(name: str, action: str) -> None:
    agent = _Agent(["ok"])
    agent.name = name
    result = await _runner(agent).execute(
        _task(name, n=1, action=action), replace(_ctx(), channel="web")
    )
    assert result.ok and agent.calls == 1


@pytest.mark.parametrize("action", ["create_draft", "status", ["search_products"], None])
async def test_web_denial_is_recorded_without_agent_execution(action: object) -> None:
    agent = _Agent(["ok"])
    agent.name = "order_agent"
    recorder = AsyncMock()
    runner = SpokeRunner({agent.name: agent}, recorder=recorder)
    result = await runner.execute(
        _task(agent.name, n=1, action=action), replace(_ctx(), channel="web")
    )
    assert result.error is not None and result.error.code == "sandbox_action_forbidden"
    assert agent.calls == 0
    recorder.record.assert_awaited_once()


async def test_timeout_is_retryable_then_error() -> None:
    agent = _Agent(["hang"])
    result = await _runner(agent).execute(_task(), _ctx())
    assert result.error is not None and result.error.code == "timeout"
    assert agent.calls == 2


async def test_retry_then_success() -> None:
    agent = _Agent(["retryable", "ok"])
    result = await _runner(agent).execute(_task(), _ctx())
    assert result.ok and agent.calls == 2


async def test_disabled_capability_never_runs_and_denial_is_recorded() -> None:
    agent = _Agent(["ok"])
    registry = AsyncMock(spec=CapabilityRegistry)
    registry.allows_execution.return_value = False
    recorder = AsyncMock()
    runner = SpokeRunner({agent.name: agent}, capabilities=registry, recorder=recorder)
    result = await runner.execute(_task(), _ctx())
    assert result.error is not None and result.error.code == "capability_unavailable"
    assert agent.calls == 0
    recorder.record.assert_awaited_once()


async def test_disabling_during_backoff_prevents_retry() -> None:
    agent = _Agent(["retryable", "ok"])
    registry = AsyncMock(spec=CapabilityRegistry)
    registry.allows_execution.side_effect = [True, False]
    runner = SpokeRunner({agent.name: agent}, capabilities=registry, backoff_s=0)
    result = await runner.execute(_task(), _ctx())
    assert not result.ok and agent.calls == 1
    assert registry.allows_execution.await_count == 2


async def test_capability_query_failure_is_not_an_allow_or_disabled_fallback() -> None:
    agent = _Agent(["ok"])
    registry = AsyncMock(spec=CapabilityRegistry)
    registry.allows_execution.side_effect = RuntimeError("database unavailable")
    runner = SpokeRunner({agent.name: agent}, capabilities=registry)
    with pytest.raises(RuntimeError, match="database unavailable"):
        await runner.execute(_task(), _ctx())
    assert agent.calls == 0


async def test_validation_error_is_result_not_exception() -> None:
    result = await _runner(_Agent(["ok"])).execute(_task(n="x"), _ctx())
    assert result.error is not None and result.error.category == "validation"


async def test_catch_all_returns_internal_error() -> None:
    result = await _runner(_Agent(["boom"])).execute(_task(), _ctx())
    assert result.error is not None and result.error.category == "internal"


async def test_unknown_agent() -> None:
    result = await _runner(_Agent(["ok"])).execute(_task("nope"), _ctx())
    assert result.error is not None and result.error.code == "agent_not_registered"


async def test_irreversible_not_retried_on_exception() -> None:
    agent = _Agent(["boom"])
    agent.side_effects = "irreversible"
    await _runner(agent).execute(_task(), _ctx())
    assert agent.calls == 1


async def test_circuit_opens_then_half_open_closes() -> None:
    clock = _Clock()
    breaker = CircuitBreaker(clock=clock)
    agent = _Agent(["boom"])
    agent.max_retries = 0
    runner = _runner(agent, breaker)
    ctx = _ctx()
    for _ in range(5):
        await runner.execute(_task(), ctx)
    assert breaker.state(ctx.client_id, "t_agent") == "open"
    calls = agent.calls
    result = await runner.execute(_task(), ctx)
    assert result.error is not None and result.error.code == "circuit_open"
    assert agent.calls == calls
    clock.t = 61
    agent.behaviours = ["ok"]
    agent.calls = 0
    assert (await runner.execute(_task(), ctx)).ok
    assert breaker.state(ctx.client_id, "t_agent") == "closed"


async def test_failed_probe_reopens() -> None:
    clock = _Clock()
    breaker = CircuitBreaker(clock=clock)
    agent = _Agent(["boom"])
    agent.max_retries = 0
    runner = _runner(agent, breaker)
    ctx = _ctx()
    for _ in range(5):
        await runner.execute(_task(), ctx)
    clock.t = 61
    await runner.execute(_task(), ctx)
    assert breaker.state(ctx.client_id, "t_agent") == "open"


async def test_failures_outside_window_do_not_open() -> None:
    clock = _Clock()
    breaker = CircuitBreaker(clock=clock)
    agent = _Agent(["boom"])
    agent.max_retries = 0
    runner = _runner(agent, breaker)
    ctx = _ctx()
    for _ in range(8):
        await runner.execute(_task(), ctx)
        clock.t += 40
    assert breaker.state(ctx.client_id, "t_agent") == "closed"


@pytest.mark.parametrize("n", [1])
async def test_latency_recorded(n: int) -> None:
    result = await _runner(_Agent(["ok"])).execute(_task(), _ctx())
    assert result.meta.latency_ms >= 0
