"""Operational runbook procedures for CSA runtime management (LG09).

Each procedure executes with tenant isolation, auditability, and parameterized SQL.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from csa.core import db


async def requeue_turn(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    ingress_event_id: UUID,
) -> dict[str, Any] | None:
    """Runbook 1: Re-queue a failed or stuck turn in ingress_events."""
    row = await db.q.fetch_one(
        "ops/requeue_turn.sql",
        (ingress_event_id, client_id),
        conn=conn,
    )
    return dict(row) if row is not None else None


async def inspect_dlq(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Runbook 2: Inspect open dead-letter queue items."""
    rows = await db.q.fetch_all(
        "ops/inspect_dlq.sql",
        (client_id, limit),
        conn=conn,
    )
    return [dict(r) for r in rows]


async def redrive_dlq(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    dlq_id: int,
) -> dict[str, Any] | None:
    """Runbook 2: Re-drive an eligible dead-letter queue item back into ingress_events."""
    row = await db.q.fetch_one(
        "ops/redrive_dlq.sql",
        (dlq_id, client_id),
        conn=conn,
    )
    if row is None:
        return None

    raw_envelope = row["envelope"]
    new_trace_id = uuid4()
    if row["stage"] == "turn" and isinstance(raw_envelope, dict):
        # Insert back into ingress_events if not already present
        raw = {
            "envelope": raw_envelope,
            "source": {"channel": "redrive_dlq", "dlq_id": dlq_id},
        }
        await db.q.fetch_one(
            "ingress/insert_event.sql",
            (
                client_id,
                raw_envelope.get("meta", {}).get("endpoint_id") or client_id,
                raw_envelope.get("contact", {}).get("id") or client_id,
                f"redrive:{dlq_id}:{new_trace_id}",
                "redrive_hash",
                new_trace_id,
                Jsonb(raw),
            ),
            conn=conn,
        )
    return dict(row)


async def get_circuit_states(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
) -> list[dict[str, Any]]:
    """Runbook 3: Inspect circuit breaker status for client capabilities."""
    rows = await db.q.fetch_all(
        "ops/get_circuit_states.sql",
        (client_id,),
        conn=conn,
    )
    return [dict(r) for r in rows]


async def reset_circuit(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    agent_name: str,
) -> dict[str, Any] | None:
    """Runbook 3: Close an open circuit breaker manually."""
    row = await db.q.fetch_one(
        "ops/reset_circuit.sql",
        (client_id, agent_name),
        conn=conn,
    )
    return dict(row) if row is not None else None


async def force_decide_mode(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    decide_mode: str,
) -> dict[str, Any] | None:
    """Runbook 4: Force client decision provider (llm / shadow / cascade / jev)."""
    if decide_mode not in ("llm", "shadow", "cascade", "jev"):
        raise ValueError(f"invalid decide_mode: {decide_mode}")
    row = await db.q.fetch_one(
        "ops/update_client_decide_mode.sql",
        (decide_mode, client_id),
        conn=conn,
    )
    return dict(row) if row is not None else None


async def set_deployment_mode(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    deployment_mode: str,
) -> dict[str, Any] | None:
    """Runbook 6: Force client deployment mode (shadow / suggest / auto)."""
    if deployment_mode not in ("shadow", "suggest", "auto"):
        raise ValueError(f"invalid deployment_mode: {deployment_mode}")
    row = await db.q.fetch_one(
        "ops/update_client_policy_mode.sql",
        (deployment_mode, deployment_mode, client_id),
        conn=conn,
    )
    return dict(row) if row is not None else None


async def set_runtime(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    runtime: str,
) -> dict[str, Any] | None:
    """Strangler cutover / rollback: flip runtime between n8n and langgraph."""
    if runtime not in ("n8n", "langgraph"):
        raise ValueError(f"invalid runtime: {runtime}")
    row = await db.q.fetch_one(
        "ops/update_client_runtime.sql",
        (runtime, client_id),
        conn=conn,
    )
    return dict(row) if row is not None else None


async def expire_stuck_approvals(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
) -> int:
    """Runbook 8: Expire overdue pending staff approvals and write audit."""
    row = await db.q.fetch_one(
        "ops/expire_stuck_approvals.sql",
        (client_id,),
        conn=conn,
    )
    return int(row["count"]) if row is not None else 0
