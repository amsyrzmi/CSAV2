"""Job runner with distributed advisory locks, RLS tenancy scoping, and execution logging (LG10)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
import structlog
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from csa.core import db
from csa.core.tenancy import tx
from csa.jobs.clock import Clock, SystemClock
from csa.jobs.orders import run_order_draft_expiry, run_orphan_draft_expiry
from csa.jobs.reapers import run_mute_expiry, run_pending_actions_expiry
from csa.jobs.reconcile import run_reservation_reconcile
from csa.jobs.registry import Job, JobRegistry

logger = structlog.get_logger(__name__)


def build_default_registry() -> JobRegistry:
    """Build the standard production registry of scheduled jobs."""
    registry = JobRegistry()
    registry.register(
        Job(
            name="order_draft_expiry",
            every=timedelta(minutes=1),
            fn=run_order_draft_expiry,
            per_client=True,
        )
    )
    registry.register(
        Job(
            name="orphan_draft_expiry",
            every=timedelta(minutes=5),
            fn=run_orphan_draft_expiry,
            per_client=True,
        )
    )
    registry.register(
        Job(
            name="reservation_reconcile",
            every=timedelta(hours=24),
            fn=run_reservation_reconcile,
            per_client=True,
        )
    )
    registry.register(
        Job(
            name="pending_actions_expiry",
            every=timedelta(minutes=5),
            fn=run_pending_actions_expiry,
            per_client=True,
        )
    )
    registry.register(
        Job(
            name="mute_expiry",
            every=timedelta(minutes=5),
            fn=run_mute_expiry,
            per_client=True,
        )
    )
    return registry


class JobRunner:
    """Executes scheduled jobs across active tenants with single-instance advisory locking."""

    def __init__(
        self,
        owner_pool: AsyncConnectionPool,
        app_pool: AsyncConnectionPool,
        *,
        lock_conninfo: str,
        registry: JobRegistry | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._owner_pool = owner_pool
        self._app_pool = app_pool
        self._lock_conninfo = lock_conninfo
        self._registry = registry or build_default_registry()
        self._clock = clock or SystemClock()
        self._last_run: dict[str, datetime] = {}
        self._last_success: dict[str, datetime] = {}

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def registry(self) -> JobRegistry:
        return self._registry

    async def list_active_clients(self) -> list[dict[str, Any]]:
        """List all active/pilot tenants to run per-client jobs against."""
        async with self._owner_pool.connection() as conn:
            rows = await db.q.fetch_all("jobs/list_active_clients.sql", conn=conn)
        return [dict(r) for r in rows]

    def is_healthy(self, job_name: str) -> bool:
        """Check if job has succeeded within 3x of its interval."""
        job = self._registry.get(job_name)
        if job is None:
            raise ValueError(f"unknown job: {job_name}")
        last = self._last_success.get(job.name)
        if last is None:
            return True
        return (self._clock.now() - last) <= (3 * job.every)

    async def run_due_jobs(self) -> dict[str, bool]:
        """Check all registered jobs and run those that are due."""
        now = self._clock.now()
        results: dict[str, bool] = {}
        for job in self._registry.all_jobs():
            last = self._last_run.get(job.name)
            if last is None or (now - last) >= job.every:
                results[job.name] = await self.run_job_once(job.name)
        return results

    async def run_job_once(self, job_name: str) -> bool:
        """Run a single job if it can acquire the global advisory lock."""
        job = self._registry.get(job_name)
        if job is None:
            raise ValueError(f"unknown job: {job_name}")

        lock_conn = await psycopg.AsyncConnection.connect(
            self._lock_conninfo,
            autocommit=True,
        )
        try:
            async with lock_conn.cursor() as cur:
                await cur.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
                    (f"job:{job.name}",),
                )
                row = await cur.fetchone()
                acquired = bool(row and row[0])

            if not acquired:
                logger.debug("job_lock_busy", job=job.name)
                return False

            try:
                await self._execute_job(job)
                self._last_success[job.name] = self._clock.now()
                return True
            finally:
                async with lock_conn.cursor() as cur:
                    await cur.execute(
                        "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                        (f"job:{job.name}",),
                    )
        finally:
            await lock_conn.close()

    async def _execute_job(self, job: Job) -> None:
        """Execute the job either per-client or globally, recording job_runs."""
        now = self._clock.now()
        self._last_run[job.name] = now

        if not job.per_client:
            await self._run_global_job(job)
            return

        clients = await self.list_active_clients()
        for client in clients:
            client_id = client["id"]
            run_id = await self._start_job_run(job.name, client_id)
            ok = True
            stats: dict[str, Any] = {}
            error_msg: str | None = None
            try:
                async with tx(self._app_pool, str(client_id)) as conn:
                    stats = await asyncio.wait_for(
                        job.fn(conn, client_id, self._clock, batch_size=job.batch_size),
                        timeout=job.timeout,
                    )
            except Exception as exc:
                ok = False
                error_msg = f"{type(exc).__name__}: {exc}"
                logger.error(
                    "job_tenant_failed", job=job.name, client_id=str(client_id), error=error_msg
                )
            finally:
                await self._finish_job_run(run_id, ok=ok, stats=stats, error=error_msg)

    async def _run_global_job(self, job: Job) -> None:
        run_id = await self._start_job_run(job.name, client_id=None)
        ok = True
        stats: dict[str, Any] = {}
        error_msg: str | None = None
        try:
            async with self._owner_pool.connection() as conn:
                stats = await asyncio.wait_for(
                    job.fn(conn, self._clock, batch_size=job.batch_size),
                    timeout=job.timeout,
                )
        except Exception as exc:
            ok = False
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.error("job_global_failed", job=job.name, error=error_msg)
        finally:
            await self._finish_job_run(run_id, ok=ok, stats=stats, error=error_msg)

    async def _start_job_run(self, job_name: str, client_id: UUID | None) -> int:
        now = self._clock.now()
        async with self._owner_pool.connection() as conn:
            row = await db.q.fetch_one(
                "jobs/insert_run.sql",
                (job_name, client_id, now),
                conn=conn,
            )
        if row is None:
            raise RuntimeError("failed to insert job_run")
        return int(row["id"])

    async def _finish_job_run(
        self,
        run_id: int,
        *,
        ok: bool,
        stats: dict[str, Any],
        error: str | None,
    ) -> None:
        now = self._clock.now()
        async with self._owner_pool.connection() as conn:
            await db.q.execute(
                "jobs/finish_run.sql",
                (now, ok, Jsonb(stats), error, run_id),
                conn=conn,
            )


async def jobs_loop(
    runner: JobRunner,
    *,
    poll_interval: float = 10.0,
    stop: asyncio.Event | None = None,
) -> None:
    """Continuous loop running due scheduled jobs at periodic ticks."""
    logger.info("jobs_loop_started", poll_interval=poll_interval)
    while stop is None or not stop.is_set():
        try:
            results = await runner.run_due_jobs()
            if any(results.values()):
                logger.info("jobs_executed", results=results)
        except asyncio.CancelledError:
            logger.info("jobs_loop_cancelled")
            raise
        except Exception as exc:
            logger.error("jobs_loop_error", error=f"{type(exc).__name__}: {exc}")

        try:
            await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            break
