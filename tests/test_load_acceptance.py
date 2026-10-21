"""Synthetic load evidence: real ingress SQL and durable graph, no external providers."""

from __future__ import annotations

import asyncio
import json
import math
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import wraps
from typing import Any

import httpx
import psycopg
import pytest
from apps.gateway import main as gateway
from apps.worker.main import IngressWorker
from csa.channels.ingress import TelegramIngress
from csa.core import db, settings
from csa.graph.coordinator import build_graph, build_runtime, run_turn
from csa.graph.events import PostgresEventSink
from csa.graph.saver import build_checkpoint_pool, build_saver
from csa.turn.deps import PostgresTurnDependencies
from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row

from tests.conftest import owner_dsn_for, requires_docker
from tests.test_coordinator import FakeDependencies, make_envelope
from tests.test_ingress import _message_update, ingress_context  # noqa: F401


def p95(samples: list[float]) -> float:
    return sorted(samples)[math.ceil(0.95 * len(samples)) - 1]


@asynccontextmanager
async def load_client(app: FastAPI, transport: str) -> AsyncIterator[httpx.AsyncClient]:
    if transport == "asgi":
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test"
            ) as client,
        ):
            yield client
        return
    assert transport == "tcp"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.setblocking(False)
        with tempfile.TemporaryFile(mode="w+") as errors:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    (
                        "import socket, sys, uvicorn\n"
                        # Uvicorn's --fd path labels inherited IPv4 sockets AF_UNIX.
                        "with socket.socket(fileno=int(sys.argv[1])) as listener:\n"
                        "    assert listener.family == socket.AF_INET\n"
                        "    listener.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)\n"
                        "    config = uvicorn.Config('apps.gateway.main:app', "
                        "access_log=False, log_level='warning')\n"
                        "    uvicorn.Server(config).run(sockets=[listener])\n"
                    ),
                    str(sock.fileno()),
                ],
                pass_fds=(sock.fileno(),),
                stdout=subprocess.DEVNULL,
                stderr=errors,
            )
            try:
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{port}", trust_env=False
                ) as client:
                    async with asyncio.timeout(30):
                        while True:
                            if process.poll() is not None:
                                errors.seek(0)
                                raise RuntimeError("test gateway startup failed: " + errors.read())
                            try:
                                response = await client.get("/readyz", timeout=1)
                            except httpx.ConnectError:
                                await asyncio.sleep(0.02)
                                continue
                            assert response.status_code == 200, response.text
                            break
                    yield client
            finally:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait, timeout=5)
                    raise


@requires_docker
@pytest.mark.parametrize("transport", ["asgi", "tcp"])
async def test_real_ingress_50rps_and_durable_queue(
    ingress_context: tuple[TelegramIngress, db.AsyncConnectionPool, str, str, str],  # noqa: F811
    pg_container: object,
    app_dsn: str,
    lg_setup_dsn: str,
    monkeypatch,
    transport: str,
) -> None:
    _, app_pool, client_id, public_id, secret = ingress_context
    owner_dsn = owner_dsn_for(pg_container)
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE webhook_endpoints SET rate_limit_rpm=6000 WHERE public_id=%s", (public_id,)
        )
    for name in ("configure_logging", "configure_tracing"):
        monkeypatch.setattr(gateway, name, lambda **_: None)
    monkeypatch.setattr(gateway, "instrument_fastapi", lambda _: None)
    monkeypatch.setenv("DATABASE_URL", app_dsn)
    monkeypatch.setenv("LG_DATABASE_URL", app_dsn)
    settings.get_settings.cache_clear()
    app = gateway.create_app()
    count, rate = 150, 50
    samples: list[float] = []
    scheduled_samples: list[float] = []
    start_lags: list[float] = []
    startup_started = time.perf_counter()
    async with load_client(app, transport) as http:
        startup_ms = (time.perf_counter() - startup_started) * 1000
        if transport == "asgi":
            assert app.state.pool.get_stats()["pool_size"] == db.GATEWAY_WORKER_MAX_SIZE
            async with app.state.pool.connection() as conn:
                assert conn.autocommit is True
        epoch = time.perf_counter()

        async def send(index: int) -> None:
            scheduled = epoch + index / rate
            await asyncio.sleep(max(0, scheduled - time.perf_counter()))
            started = time.perf_counter()
            response = await http.post(
                f"/hooks/{public_id}",
                json=_message_update(
                    update_id=index + 1, sender_id=900_000 + index, chat_id=900_000 + index
                ),
                headers={"X-Telegram-Bot-Api-Secret-Token": secret},
            )
            ended = time.perf_counter()
            assert response.status_code == 200, response.text
            assert response.json()["ok"] and not response.json()["duplicate"]
            samples.append((ended - started) * 1000)
            scheduled_samples.append((ended - scheduled) * 1000)
            start_lags.append((started - scheduled) * 1000)

        await asyncio.wait_for(asyncio.gather(*(send(i) for i in range(count))), timeout=15)
        ingress_seconds = time.perf_counter() - epoch
    if transport == "asgi":
        assert app.state.pool.closed

    owner_pool = db.build_pool(owner_dsn, max_size=4)
    checkpoint_pool = build_checkpoint_pool(app_dsn)
    await owner_pool.open(wait=True)
    await checkpoint_pool.open(wait=True)
    try:
        worker = IngressWorker(
            owner_pool,
            app_pool,
            lock_conninfo=owner_dsn,
            coordinator=build_runtime(
                PostgresTurnDependencies(app_pool),
                build_saver(checkpoint_pool),
                PostgresEventSink(app_pool),
            ),
        )

        async def drain() -> None:
            while await worker.process_once():
                pass

        started = time.perf_counter()
        await asyncio.wait_for(asyncio.gather(*(drain() for _ in range(4))), timeout=120)
        queue_seconds = time.perf_counter() - started
    finally:
        await checkpoint_pool.close()
        await owner_pool.close()

    with psycopg.connect(owner_dsn) as conn:
        status = dict(
            conn.execute(
                "SELECT status,count(*) FROM ingress_events WHERE client_id=%s GROUP BY status",
                (client_id,),
            ).fetchall()
        )
        outbound = conn.execute(
            "SELECT count(*),count(DISTINCT trace_id) FROM messages "
            "WHERE client_id=%s AND direction='outbound'",
            (client_id,),
        ).fetchone()
        deliveries = conn.execute(
            "SELECT count(*) FROM outbound_deliveries WHERE client_id=%s AND status='pending'",
            (client_id,),
        ).fetchone()
    assert status == {"completed": count}
    assert outbound == (count, count) and deliveries == (count,)
    metrics: dict[str, Any] = {
        "transport": transport,
        "requests": count,
        "offered_rps": rate,
        "ingress_seconds": round(ingress_seconds, 3),
        "ack_p95_ms": round(p95(samples), 3),
        "scheduled_ack_p95_ms": round(p95(scheduled_samples), 3),
        "start_lag_p95_ms": round(p95(start_lags), 3),
        "queue_seconds": round(queue_seconds, 3),
        "queue_turns_per_second": round(count / queue_seconds, 3),
        "queue_concurrency": 4,
        "app_pool_max_size": db.GATEWAY_WORKER_MAX_SIZE,
        "pool_preopened": True,
        "gateway_autocommit": True,
        "gateway_lifespan_used": True,
        "startup_ms": round(startup_ms, 3),
        "completed": count,
        "pending_deliveries": count,
    }
    print("LG08_LOAD " + json.dumps(metrics, sort_keys=True))
    assert p95(samples) < 200
    assert p95(scheduled_samples) < 200


@requires_docker
async def test_simultaneous_tcp_ingress_and_worker_capacity(
    ingress_context: tuple[TelegramIngress, db.AsyncConnectionPool, str, str, str],  # noqa: F811
    pg_container: object,
    app_dsn: str,
    lg_setup_dsn: str,
    monkeypatch,
) -> None:
    _, _, client_id, public_id, secret = ingress_context
    owner_dsn = owner_dsn_for(pg_container)
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE webhook_endpoints SET rate_limit_rpm=6000 WHERE public_id=%s", (public_id,)
        )
    for name in ("configure_logging", "configure_tracing"):
        monkeypatch.setattr(gateway, name, lambda **_: None)
    monkeypatch.setattr(gateway, "instrument_fastapi", lambda _: None)
    monkeypatch.setenv("DATABASE_URL", app_dsn)
    monkeypatch.setenv("LG_DATABASE_URL", app_dsn)
    settings.get_settings.cache_clear()
    app = gateway.create_app()

    checkpoint_timings: dict[str, list[float]] = {"aget_tuple": [], "aput": [], "aput_writes": []}
    for method_name, samples_for_method in checkpoint_timings.items():
        original = getattr(AsyncPostgresSaver, method_name)

        @wraps(original)
        async def timed_checkpoint(
            *args, _original=original, _samples=samples_for_method, **kwargs
        ):
            started = time.perf_counter()
            try:
                return await _original(*args, **kwargs)
            finally:
                _samples.append((time.perf_counter() - started) * 1000)

        monkeypatch.setattr(AsyncPostgresSaver, method_name, timed_checkpoint)

    worker_count = 4
    owner_pool = db.build_pool(owner_dsn, max_size=worker_count)
    app_pool = db.build_pool(app_dsn, max_size=8)
    checkpoint_pool = build_checkpoint_pool(app_dsn, max_size=worker_count)
    for pool in (owner_pool, app_pool, checkpoint_pool):
        await pool.open(wait=True)
    workers = [
        IngressWorker(
            owner_pool,
            app_pool,
            lock_conninfo=owner_dsn,
            coordinator=build_runtime(
                PostgresTurnDependencies(app_pool),
                build_saver(checkpoint_pool),
                PostgresEventSink(app_pool),
            ),
        )
        for _ in range(worker_count)
    ]

    count, rate = 150, 50
    samples: list[float] = []
    scheduled_samples: list[float] = []
    stop = asyncio.Event()
    turn_samples: list[float] = []

    async def consume(worker: IngressWorker) -> None:
        while not stop.is_set():
            started = time.perf_counter()
            worked = await worker.process_once()
            if worked:
                turn_samples.append((time.perf_counter() - started) * 1000)
            else:
                await asyncio.sleep(0.01)

    consumers = [asyncio.create_task(consume(worker)) for worker in workers]
    try:
        async with load_client(app, "tcp") as http:
            epoch = time.perf_counter()

            async def send(index: int) -> None:
                scheduled = epoch + index / rate
                await asyncio.sleep(max(0, scheduled - time.perf_counter()))
                started = time.perf_counter()
                response = await http.post(
                    f"/hooks/{public_id}",
                    json=_message_update(
                        update_id=index + 1,
                        sender_id=910_000 + index,
                        chat_id=910_000 + index,
                    ),
                    headers={"X-Telegram-Bot-Api-Secret-Token": secret},
                )
                ended = time.perf_counter()
                assert response.status_code == 200, response.text
                assert response.json()["ok"] and not response.json()["duplicate"]
                samples.append((ended - started) * 1000)
                scheduled_samples.append((ended - scheduled) * 1000)

            await asyncio.wait_for(asyncio.gather(*(send(i) for i in range(count))), timeout=30)
            ingress_seconds = time.perf_counter() - epoch

        async def wait_until_drained() -> float:
            started = time.perf_counter()
            deadline = started + 180
            completed = 0
            while time.perf_counter() < deadline:
                async with owner_pool.connection() as conn:
                    async with conn.cursor(row_factory=dict_row) as cur:
                        await cur.execute(
                            "SELECT count(*) AS completed FROM ingress_events "
                            "WHERE client_id=%s AND status='completed'",
                            (client_id,),
                        )
                        row = await cur.fetchone()
                if row is not None:
                    completed = row["completed"]
                if completed == count:
                    return time.perf_counter() - started
                await asyncio.sleep(0.1)
            raise TimeoutError(f"only {completed} of {count} turns completed")

        drain_seconds = await wait_until_drained()
    finally:
        stop.set()
        await asyncio.gather(*consumers)
        await checkpoint_pool.close()
        await app_pool.close()
        await owner_pool.close()

    with psycopg.connect(owner_dsn) as conn:
        outbound = conn.execute(
            "SELECT count(*),count(DISTINCT trace_id) FROM messages "
            "WHERE client_id=%s AND direction='outbound'",
            (client_id,),
        ).fetchone()
        deliveries = conn.execute(
            "SELECT count(*) FROM outbound_deliveries WHERE client_id=%s AND status='pending'",
            (client_id,),
        ).fetchone()
        node_timings = conn.execute(
            "SELECT node, count(*), round(avg(duration_ms)::numeric, 1), "
            "percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) "
            "FROM agent_events WHERE client_id=%s GROUP BY node ORDER BY node",
            (client_id,),
        ).fetchall()
    assert outbound is not None and deliveries is not None
    assert outbound == (count, count) and deliveries == (count,)
    metrics = {
        "transport": "tcp",
        "simultaneous": True,
        "offered_rps": rate,
        "requests": count,
        "worker_count": worker_count,
        "ingress_seconds": round(ingress_seconds, 3),
        "ack_p95_ms": round(p95(samples), 3),
        "scheduled_ack_p95_ms": round(p95(scheduled_samples), 3),
        "drain_after_ingress_seconds": round(drain_seconds, 3),
        "worker_turn_p95_ms": round(p95(turn_samples), 3),
        "completed": count,
        "unique_replies": outbound[1],
        "pending_deliveries": deliveries[0],
    }
    print("LG08_SIMULTANEOUS " + json.dumps(metrics, sort_keys=True))
    print(
        "LG08_NODE_TIMINGS "
        + json.dumps(
            [
                {
                    "node": node,
                    "events": events,
                    "mean_ms": float(mean),
                    "p95_ms": round(p95, 1),
                }
                for node, events, mean, p95 in node_timings
            ],
            sort_keys=True,
        )
    )
    print(
        "LG08_CHECKPOINT_TIMINGS "
        + json.dumps(
            {
                name: {
                    "count": len(values),
                    "mean_ms": round(sum(values) / len(values), 3) if values else 0,
                    "p95_ms": round(p95(values), 3) if values else 0,
                    "max_ms": round(max(values), 3) if values else 0,
                }
                for name, values in checkpoint_timings.items()
            },
            sort_keys=True,
        )
    )


async def test_graph_only_overhead_report() -> None:
    deps = FakeDependencies()
    graph = build_graph(deps)
    samples: list[float] = []
    for _ in range(200):
        started = time.perf_counter()
        await run_turn(make_envelope("hello"), deps, graph=graph)
        samples.append((time.perf_counter() - started) * 1000)
    print(
        "LG08_OVERHEAD "
        + json.dumps({"turns": len(samples), "graph_only_p95_ms": round(p95(samples), 3)})
    )
    assert p95(samples) < 150
