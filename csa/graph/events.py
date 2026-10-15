"""Redacted timeline events for graph steps (console visibility).

``summarize`` maps a node's state update to a small dict of codes, counts and names. It never
reads customer text, so persisted events are safe to show in the console and to log.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol
from uuid import UUID

import structlog
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from csa.core import db
from csa.core.tenancy import tx

logger = structlog.get_logger(__name__)


class EventSink(Protocol):
    async def record(
        self,
        *,
        client_id: UUID,
        trace_id: UUID,
        conversation_id: UUID | None,
        node: str,
        agent: str | None,
        status: str,
        duration_ms: int,
        summary: dict[str, Any],
    ) -> None: ...


class PostgresEventSink:
    def __init__(
        self,
        pool: AsyncConnectionPool,
        *,
        langfuse_sink: Any | None = None,
    ) -> None:
        self._pool = pool
        self._langfuse_sink = langfuse_sink

    async def record(
        self,
        *,
        client_id: UUID,
        trace_id: UUID,
        conversation_id: UUID | None,
        node: str,
        agent: str | None,
        status: str,
        duration_ms: int,
        summary: dict[str, Any],
    ) -> None:
        async with tx(self._pool, str(client_id)) as conn:
            await db.q.execute(
                "console/insert_event.sql",
                (
                    client_id,
                    trace_id,
                    conversation_id,
                    node,
                    agent,
                    status,
                    duration_ms,
                    Jsonb(summary),
                ),
                conn=conn,
            )

        if self._langfuse_sink is not None:
            try:
                await self._langfuse_sink.send_event(
                    client_id=client_id,
                    trace_id=trace_id,
                    conversation_id=conversation_id,
                    node=node,
                    agent=agent,
                    status=status,
                    duration_ms=duration_ms,
                    summary=summary,
                )
            except Exception as exc:  # Observability must never fail a customer turn
                logger.warning("langfuse_record_failed", error_type=type(exc).__name__)


def _get(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, Mapping) else getattr(obj, name, None)


def summarize(node: str, update: Mapping[str, Any]) -> tuple[str | None, dict[str, Any]]:
    """Return ``(agent, summary)`` for one node's state update."""
    agent: str | None = None
    out: dict[str, Any] = {}
    if error := update.get("node_error"):
        out["error"] = str(error)[:200]
    if signals := update.get("signals"):
        out["intent"] = str(_get(signals, "intent") or "") or None
        out["provider"] = _get(signals, "provider")
    if route := update.get("route"):
        out["route"] = route
    if plan := update.get("plan"):
        out["intent"] = str(_get(plan, "intent"))
        out["strategy"] = _get(plan, "reply_strategy")
        out["confidence"] = _get(plan, "confidence")
        out["tasks"] = [str(_get(t, "agent")) for t in _get(plan, "tasks") or []]
    for result in update.get("results") or []:
        task_id = _get(result, "task_id")
        err = _get(result, "error")
        agent = str(_get(result, "agent") or "") or None
        out.update(
            {
                "task_id": task_id,
                "ok": _get(result, "ok"),
                "agent_status": _get(result, "status"),
                "error_code": _get(err, "code") if err else None,
                "requires_human": _get(result, "requires_human"),
            }
        )
    if handoff := update.get("handoff"):
        out["handoff_required"] = _get(handoff, "required")
        out["handoff_reason"] = _get(handoff, "reason")
    if draft := update.get("draft"):
        out["language"] = _get(draft, "language")
        out["fact_count"] = len(_get(draft, "fact_ids") or [])
    if "verified" in update and update["verified"] is not None:
        out["verified"] = update["verified"]
    if update.get("verify_issues"):
        out["verify_issue_count"] = len(update["verify_issues"])
    if verdict := update.get("verdict"):
        out["decision"] = _get(verdict, "decision")
        out["reason"] = _get(verdict, "reason")
        out["risk"] = _get(verdict, "risk_score")
    if outbound := update.get("outbound"):
        out["messages"] = len(outbound)
    if "pending_tasks" in update:
        out["next_tasks"] = [str(_get(t, "agent")) for t in update["pending_tasks"] or []]
    if step := update.get("step"):
        out["step"] = step
    if loop_stop := update.get("loop_stop"):
        out["loop_stop"] = loop_stop
    return agent, {k: v for k, v in out.items() if v is not None}


def traced(
    node: str,
    fn: Callable[[Any], Any],
    sink: EventSink,
) -> Callable[[Any], Awaitable[dict[str, object]]]:
    """Wrap a graph node so each execution appends a timeline event."""

    async def wrapper(state: Any) -> dict[str, object]:
        started = time.perf_counter()
        update = fn(state)
        if hasattr(update, "__await__"):
            update = await update
        envelope = _get(state, "envelope")
        task = _get(state, "current_task")
        agent, summary = summarize(node, update)
        if agent is None and task is not None:
            agent = str(_get(task, "agent"))
        try:
            await sink.record(
                client_id=_get(envelope, "client_id"),
                trace_id=_get(envelope, "trace_id"),
                conversation_id=_get(_get(envelope, "conversation"), "id"),
                node=node,
                agent=agent,
                status="error" if update.get("node_error") else "ok",
                duration_ms=int((time.perf_counter() - started) * 1000),
                summary=summary,
            )
        except Exception as exc:
            # Observability must never fail a customer turn, but a gap must be loud.
            logger.error("timeline_event_failed", node=node, error_type=type(exc).__name__)
        return update

    return wrapper
