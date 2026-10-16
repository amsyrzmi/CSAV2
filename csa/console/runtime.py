"""Tenant-scoped execution metadata; raw agent payloads are deliberately excluded."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict

from csa.core import db
from csa.core.tenancy import tx

MAX_RUN_ID = 2**63 - 1


class RunOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    trace_id: UUID
    conversation_id: UUID | None
    agent: str
    ok: bool
    status: str
    error_code: str | None
    error_category: str | None
    latency_ms: int | None
    created_at: datetime


class RunPage(BaseModel):
    items: list[RunOut]
    next_before_id: int | None


class OverviewOut(BaseModel):
    runs: int
    succeeded: int
    failed: int
    last_run_at: datetime | None
    window_hours: int = 24


async def list_runs(
    pool: AsyncConnectionPool,
    client_id: UUID,
    *,
    before_id: int | None,
    agent: str | None,
    limit: int,
) -> RunPage:
    async with tx(pool, str(client_id)) as conn:
        rows = await db.q.fetch_all(
            "console/list_runs.sql",
            (client_id, before_id, before_id, agent, agent, limit + 1),
            conn=conn,
        )
    items = [RunOut.model_validate(row) for row in rows[:limit]]
    return RunPage(
        items=items,
        next_before_id=items[-1].id if len(rows) > limit else None,
    )


async def get_run(pool: AsyncConnectionPool, client_id: UUID, run_id: int) -> RunOut | None:
    async with tx(pool, str(client_id)) as conn:
        row = await db.q.fetch_one("console/get_run.sql", (client_id, run_id), conn=conn)
    return RunOut.model_validate(row) if row is not None else None


async def overview(pool: AsyncConnectionPool, client_id: UUID) -> OverviewOut:
    async with tx(pool, str(client_id)) as conn:
        row = await db.q.fetch_one("console/runtime_overview.sql", (client_id,), conn=conn)
    if row is None:
        raise RuntimeError("Runtime overview aggregate missing")
    return OverviewOut.model_validate(row)
