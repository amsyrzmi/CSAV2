"""Uniform spoke wrapper: validate, time out, retry, break, record (LG05)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from typing import Any, Protocol

import structlog
from pydantic import ValidationError

from csa.agents.base import Agent, AgentContext
from csa.agents.breaker import CircuitBreaker
from csa.contracts.facts import AgentError, AgentResult, AgentResultData, ResultMeta
from csa.contracts.plan import RiskClass, Task
from csa.contracts.registry import CapabilityRegistry
from csa.core.pii import redact

logger = structlog.get_logger(__name__)

_BACKOFF_S = 0.2


class RunRecorder(Protocol):
    async def record(
        self,
        ctx: AgentContext,
        task: Task,
        result: AgentResult,
        latency_ms: int,
    ) -> None: ...


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Mapping):
        return {key: _scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def scrubbed_input(task: Task) -> dict[str, Any]:
    return {key: _scrub(value) for key, value in task.input.items()}


def _error_result(
    task: Task,
    ctx: AgentContext,
    latency_ms: int,
    code: str,
    category: str,
    message: str,
    *,
    retryable: bool = False,
    hint: str | None = None,
) -> AgentResult:
    return AgentResult(
        ok=False,
        status="error",
        task_id=task.task_id,
        agent=task.agent,
        data=AgentResultData(),
        error=AgentError.model_validate(
            {
                "code": code,
                "category": category,
                "message": message,
                "retryable": retryable,
                "customer_safe_hint": hint,
            }
        ),
        meta=ResultMeta(trace_id=ctx.trace_id, latency_ms=latency_ms),
    )


class SpokeRunner:
    def __init__(
        self,
        agents: Mapping[str, Agent],
        *,
        breaker: CircuitBreaker | None = None,
        recorder: RunRecorder | None = None,
        backoff_s: float = _BACKOFF_S,
        capabilities: CapabilityRegistry | None = None,
    ) -> None:
        self._agents = dict(agents)
        self._breaker = breaker or CircuitBreaker()
        self._recorder = recorder
        self._backoff_s = backoff_s
        self._capabilities = capabilities

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._agents)

    def risk(self, task: Task) -> RiskClass | None:
        """Risk class of a task's action, or ``None`` if the agent or input is unknown."""
        agent = self._agents.get(task.agent)
        if agent is None:
            return None
        try:
            return agent.risk(agent.Input.model_validate(task.input))
        except ValidationError:
            return None

    async def execute(self, task: Task, ctx: AgentContext) -> AgentResult:
        started = time.perf_counter()

        def elapsed() -> int:
            return round((time.perf_counter() - started) * 1000)

        agent = self._agents.get(task.agent)
        if agent is None:
            result = _error_result(
                task, ctx, elapsed(), "agent_not_registered", "not_found", task.agent
            )
            return await self._finish(ctx, task, result, elapsed())
        try:
            inp = agent.Input.model_validate(task.input)
        except ValidationError as exc:
            result = _error_result(
                task,
                ctx,
                elapsed(),
                "invalid_input",
                "validation",
                f"{len(exc.errors())} invalid field(s)",
                hint="I need a bit more detail to do that.",
            )
            return await self._finish(ctx, task, result, elapsed())
        attempts = agent.max_retries + 1
        result: AgentResult | None = None
        for attempt in range(attempts):
            if self._capabilities is not None and not await self._capabilities.allows_execution(
                ctx.client_id, agent.name
            ):
                if attempt > 0:
                    break
                result = _error_result(
                    task,
                    ctx,
                    elapsed(),
                    "capability_unavailable",
                    "validation",
                    "Capability is missing or disabled.",
                )
                return await self._finish(ctx, task, result, elapsed())
            if attempt == 0 and not await self._breaker.allow(ctx.client_id, agent.name):
                result = _error_result(
                    task,
                    ctx,
                    elapsed(),
                    "circuit_open",
                    "upstream",
                    f"{agent.name} circuit is open",
                    hint="That service is temporarily unavailable.",
                )
                return await self._finish(ctx, task, result, elapsed())
            result = await self._attempt(agent, ctx, inp, task, elapsed)
            if result.ok or result.error is None or not result.error.retryable:
                break
            # Irreversible work is only retried when idempotent via the context key.
            if agent.side_effects == "irreversible" and not ctx.idempotency_key:
                break
            if attempt + 1 < attempts:
                await asyncio.sleep(self._backoff_s * (2**attempt))
        assert result is not None
        infrastructure_failure = (
            not result.ok
            and result.error is not None
            and result.error.category in {"upstream", "internal"}
        )
        if infrastructure_failure:
            await self._breaker.record_failure(ctx.client_id, agent.name)
        else:
            await self._breaker.record_success(ctx.client_id, agent.name)
        return await self._finish(ctx, task, result, elapsed())

    async def _attempt(
        self,
        agent: Agent,
        ctx: AgentContext,
        inp: Any,
        task: Task,
        elapsed: Any,
    ) -> AgentResult:
        try:
            async with asyncio.timeout(agent.timeout_ms / 1000):
                result = await agent.run(ctx, inp)
        except TimeoutError:
            return _error_result(
                task,
                ctx,
                elapsed(),
                "timeout",
                "upstream",
                f"{agent.name} exceeded {agent.timeout_ms}ms",
                retryable=True,
            )
        except Exception as exc:
            logger.exception("agent_failed", agent=agent.name, trace_id=str(ctx.trace_id))
            return _error_result(
                task,
                ctx,
                elapsed(),
                "agent_exception",
                "internal",
                type(exc).__name__,
                retryable=agent.side_effects != "irreversible",
            )
        return result

    async def _finish(
        self,
        ctx: AgentContext,
        task: Task,
        result: AgentResult,
        latency_ms: int,
    ) -> AgentResult:
        result = result.model_copy(
            update={
                "task_id": task.task_id,
                "meta": result.meta.model_copy(update={"latency_ms": latency_ms}),
            }
        )
        if self._recorder is not None:
            await self._recorder.record(ctx, task, result, latency_ms)
        return result
