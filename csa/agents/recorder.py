"""Postgres-backed agent_runs recorder and circuit-state store."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import structlog
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from csa.agents.base import AgentContext
from csa.agents.breaker import CircuitState
from csa.agents.runner import scrubbed_input
from csa.contracts.facts import AgentResult
from csa.contracts.plan import Task
from csa.core import db
from csa.core.tenancy import tx
from csa.notify.notifier import notify

logger = structlog.get_logger(__name__)


class PostgresRunRecorder:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def record(
        self,
        ctx: AgentContext,
        task: Task,
        result: AgentResult,
        latency_ms: int,
    ) -> None:
        error = result.error
        output = {
            "fact_ids": [fact.id for fact in result.data.facts],
            "requires_human": result.requires_human,
        }
        async with tx(self._pool, str(ctx.client_id)) as conn:
            await db.q.fetch_one(
                "agents/insert_run.sql",
                (
                    ctx.client_id,
                    ctx.trace_id,
                    ctx.conversation_id,
                    task.agent,
                    task.task_id,
                    result.ok,
                    result.status,
                    error.code if error else None,
                    error.category if error else None,
                    latency_ms,
                    None,
                    0,
                    0,
                    Jsonb(scrubbed_input(task)),
                    Jsonb(output),
                ),
                conn=conn,
            )


class PostgresBreakerStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(
        self,
        client_id: UUID,
        agent: str,
        state: CircuitState,
        fail_count: int,
        opened_at_epoch: float | None,
    ) -> None:
        opened_at = datetime.now(UTC) if opened_at_epoch is not None else None
        async with tx(self._pool, str(client_id)) as conn:
            row = await db.q.fetch_one(
                "agents/set_circuit.sql",
                (state, fail_count, opened_at, client_id, agent),
                conn=conn,
            )
            if row is not None and state == "open" and opened_at_epoch is not None:
                await notify(
                    conn,
                    client_id=client_id,
                    event="circuit_opened",
                    priority="high",
                    payload={"agent": agent},
                    dedupe_key=f"{agent}:{opened_at_epoch:.6f}",
                )
        if row is None:
            logger.warning("circuit_not_persisted", client_id=str(client_id), agent=agent)
