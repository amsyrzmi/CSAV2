"""Process-level ingress worker ordering and crash recovery tests."""

from __future__ import annotations

import asyncio
import multiprocessing
from typing import Any

import psycopg
import pytest
from apps.worker.main import ClaimedIngress, IngressWorker
from csa.contracts.envelope import Envelope
from csa.core import db
from psycopg.rows import dict_row

from tests.conftest import owner_dsn_for, requires_docker
from tests.test_worker import _configure_test_settings, _seed_events


def _worker_process(
    owner_dsn: str,
    app_dsn: str,
    event_count: int,
    start: Any,
    results: Any,
    crash_started: Any = None,
) -> None:
    """Run a real worker loop with process-local pools and advisory-lock sessions."""

    async def run() -> None:
        owner_pool = db.build_pool(owner_dsn, max_size=2)
        app_pool = db.build_pool(app_dsn, max_size=2)
        await owner_pool.open(wait=True)
        await app_pool.open(wait=True)
        handled = 0

        class ProcessWorker(IngressWorker):
            async def _complete_turn(self, item: ClaimedIngress, envelope: Envelope) -> None:
                if crash_started is not None:
                    crash_started.set()
                    await asyncio.sleep(60)
                await asyncio.sleep(0.05)
                await super()._complete_turn(item, envelope)

        worker = ProcessWorker(owner_pool, app_pool, lock_conninfo=owner_dsn)
        try:
            start.wait(timeout=20)
            while True:
                worked = await worker.process_once()
                if worked:
                    handled += 1
                    await asyncio.sleep(0.02)
                    continue
                async with owner_pool.connection() as conn:
                    async with conn.cursor(row_factory=dict_row) as cur:
                        await cur.execute(
                            "SELECT count(*) FILTER (WHERE status='completed') AS completed, "
                            "count(*) FILTER (WHERE status IN ('received','dispatched','failed')) "
                            "AS active FROM ingress_events"
                        )
                        row = await cur.fetchone()
                if row is not None and row["completed"] >= event_count and row["active"] == 0:
                    break
                await asyncio.sleep(0.01)
        finally:
            await app_pool.close()
            await owner_pool.close()
        results.put((multiprocessing.current_process().pid, handled, None))

    try:
        asyncio.run(run())
    except BaseException as exc:
        results.put((multiprocessing.current_process().pid, 0, type(exc).__name__))


def _start_process(
    context: Any,
    owner_dsn: str,
    app_dsn: str,
    event_count: int,
    start: Any,
    results: Any,
    crash_started: Any = None,
) -> Any:
    process = context.Process(
        target=_worker_process,
        args=(owner_dsn, app_dsn, event_count, start, results, crash_started),
    )
    process.start()
    return process


async def _collect_process(
    process: Any, results: Any, *, timeout: float = 30
) -> tuple[int, int, str | None]:
    result = await asyncio.to_thread(results.get, True, timeout)
    await asyncio.to_thread(process.join, timeout)
    assert not process.is_alive()
    assert process.exitcode == 0
    return result


@requires_docker
@pytest.mark.asyncio
async def test_two_worker_processes_preserve_conversation_order_and_idempotency(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    owner_dsn, client_id, event_ids = _seed_events(owner_dsn, count=8)
    context = multiprocessing.get_context("spawn")
    start, results = context.Event(), context.Queue()
    processes = [
        _start_process(context, owner_dsn, app_dsn, len(event_ids), start, results)
        for _ in range(2)
    ]
    try:
        start.set()
        observed = [await _collect_process(process, results, timeout=60) for process in processes]
        assert all(error is None for _, _, error in observed), observed
        assert all(handled > 0 for _, handled, _ in observed)
        assert len({pid for pid, _, _ in observed}) == 2
        assert sum(handled for _, handled, _ in observed) == len(event_ids)

        with psycopg.connect(owner_dsn) as conn:
            statuses = conn.execute(
                "SELECT status,count(*) FROM ingress_events WHERE id=ANY(%s) GROUP BY status",
                (event_ids,),
            ).fetchall()
            inbound = conn.execute(
                "SELECT body FROM messages WHERE client_id=%s AND direction='inbound' "
                "ORDER BY created_at,id",
                (client_id,),
            ).fetchall()
            outbound = conn.execute(
                "SELECT body,count(*),count(DISTINCT trace_id) FROM messages "
                "WHERE client_id=%s AND direction='outbound' GROUP BY body ORDER BY body",
                (client_id,),
            ).fetchall()
            deliveries = conn.execute(
                "SELECT count(*),count(DISTINCT trace_id) FROM outbound_deliveries "
                "WHERE client_id=%s AND status='pending'",
                (client_id,),
            ).fetchone()
        assert statuses == [("completed", len(event_ids))]
        assert inbound == [(f"message-{index}",) for index in range(1, 9)]
        assert outbound == [(f"Echo: message-{index}", 1, 1) for index in range(1, 9)]
        assert deliveries == (len(event_ids), len(event_ids))
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 10)
        results.close()
        results.join_thread()


@requires_docker
@pytest.mark.asyncio
async def test_process_death_releases_advisory_lock_and_reaper_recovers_turn(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    owner_dsn, client_id, event_ids = _seed_events(owner_dsn)
    context = multiprocessing.get_context("spawn")
    start, crash_started, results = context.Event(), context.Event(), context.Queue()
    process = _start_process(
        context, owner_dsn, app_dsn, 1, start, results, crash_started=crash_started
    )
    start.set()
    assert await asyncio.to_thread(crash_started.wait, 30)
    process.terminate()
    await asyncio.to_thread(process.join, 15)
    assert not process.is_alive()
    assert process.exitcode != 0
    results.close()
    results.join_thread()

    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE ingress_events SET dispatched_at=now()-interval '10 minutes' WHERE id=%s",
            (event_ids[0],),
        )
        row = conn.execute(
            "SELECT raw #>> '{envelope,channel}' AS channel, "
            "raw #>> '{envelope,conversation,channel_thread}' AS channel_thread "
            "FROM ingress_events WHERE id=%s",
            (event_ids[0],),
        ).fetchone()
        assert row is not None
        lock_key = f"{client_id}:{row[0]}:{row[1]}"
        locked = conn.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (lock_key,)
        ).fetchone()
        assert locked == (True,)
        conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (lock_key,))

    owner_pool = db.build_pool(owner_dsn, max_size=2)
    app_pool = db.build_pool(app_dsn, max_size=2)
    await owner_pool.open(wait=True)
    await app_pool.open(wait=True)
    try:
        reaper = IngressWorker(owner_pool, app_pool, lock_conninfo=owner_dsn)
        assert await reaper.reap() == 1
    finally:
        await app_pool.close()
        await owner_pool.close()

    start, results = context.Event(), context.Queue()
    recovery = _start_process(context, owner_dsn, app_dsn, 1, start, results)
    try:
        start.set()
        pid, handled, error = await _collect_process(recovery, results, timeout=30)
        assert pid != process.pid
        assert (handled, error) == (1, None), (handled, error)
        with psycopg.connect(owner_dsn) as conn:
            status = conn.execute(
                "SELECT status FROM ingress_events WHERE id=%s", (event_ids[0],)
            ).fetchone()
            messages = conn.execute(
                "SELECT direction,count(*),count(DISTINCT trace_id) FROM messages "
                "WHERE client_id=%s GROUP BY direction ORDER BY direction",
                (client_id,),
            ).fetchall()
            deliveries = conn.execute(
                "SELECT count(*),count(DISTINCT trace_id) FROM outbound_deliveries "
                "WHERE client_id=%s",
                (client_id,),
            ).fetchone()
        assert status == ("completed",)
        assert messages == [("inbound", 1, 1), ("outbound", 1, 1)]
        assert deliveries == (1, 1)
    finally:
        if recovery.is_alive():
            recovery.terminate()
            await asyncio.to_thread(recovery.join, 10)
        results.close()
        results.join_thread()
