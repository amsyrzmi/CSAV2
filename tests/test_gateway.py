"""Telegram webhook HTTP surface tests (LG03)."""

from __future__ import annotations

import asyncio
import math
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator
from time import perf_counter

import httpx
import pytest
from apps.gateway import main as gateway
from csa.channels.ingress import IngressReceipt
from csa.core import settings
from fastapi import FastAPI


class _FakeIngress:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, bytes]] = []

    async def receive(
        self,
        public_id: str,
        supplied_secret: str | None,
        body: bytes,
    ) -> IngressReceipt:
        self.calls.append((public_id, supplied_secret, body))
        return IngressReceipt(accepted=True, duplicate=False, trace_id=uuid.uuid4())


def test_actual_gateway_remains_live_when_stdout_is_not_drained(
    gateway_app: tuple[FastAPI, _FakeIngress],
) -> None:
    code = """
import sys
import structlog
import uvicorn
from apps.gateway.main import create_app
app = create_app()
@app.get('/_synthetic/log-pressure')
async def pressure():
    for _ in range(4096):
        structlog.get_logger().info('synthetic-pressure', payload='x' * 1024)
    return {'done': True}
uvicorn.run(app, fd=int(sys.argv[1]), lifespan='off', access_log=False, log_level='warning')
"""
    with socket.socket() as sock, tempfile.TemporaryFile() as errors:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, "-c", code, str(sock.fileno())],
            env={**os.environ, "ENV": "ci", "NO_LOCAL_ENV": "1"},
            pass_fds=(sock.fileno(),),
            stdout=subprocess.PIPE,
            stderr=errors,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:
                deadline = time.monotonic() + 30
                while True:
                    assert process.poll() is None, "synthetic gateway exited before readiness"
                    try:
                        assert client.get("/healthz", timeout=1).status_code == 200
                        break
                    except httpx.ConnectError:
                        assert time.monotonic() < deadline
                        time.sleep(0.02)
                assert client.get("/_synthetic/log-pressure", timeout=5).status_code == 200
                assert client.get("/healthz", timeout=1).status_code == 200
                response = client.get("/readyz", timeout=1)
                assert response.status_code == 503
                assert "queue_full" in response.json()["reason"]
                assert "dropped_events=0" not in response.json()["reason"]
        finally:
            process.kill()
            process.wait(timeout=5)
            assert process.stdout is not None
            process.stdout.close()


@pytest.fixture()
def gateway_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[FastAPI, _FakeIngress]]:
    for name, value in {
        "ENV": "ci",
        "DATABASE_URL": "postgresql://app:pass@localhost:5433/csa",
        "LG_DATABASE_URL": "postgresql://app:pass@localhost:5433/csa",
        "GROQ_API_KEY": "test-key",
        "PUBLIC_BASE_URL": "https://example.test",
        "CREDENTIALS_KEY": "base64:BAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ=",
    }.items():
        monkeypatch.setenv(name, value)
    settings.get_settings.cache_clear()
    monkeypatch.setattr(gateway, "configure_logging", lambda **_: None)
    monkeypatch.setattr(gateway, "configure_tracing", lambda **_: None)
    monkeypatch.setattr(gateway, "instrument_fastapi", lambda _app: None)
    app = gateway.create_app()
    ingress = _FakeIngress()
    app.state.telegram_ingress = ingress
    yield app, ingress
    settings.get_settings.cache_clear()


@pytest.mark.asyncio
async def test_logging_failure_rejects_readiness_but_not_liveness(
    gateway_app: tuple[FastAPI, _FakeIngress], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = gateway_app
    reason = "logging unavailable: queue_full; dropped_events=3"
    monkeypatch.setattr(gateway, "logging_health", lambda: (False, reason))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/readyz")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready", "reason": reason}
        assert (await client.get("/healthz")).status_code == 200


@pytest.mark.asyncio
async def test_telegram_webhook_passes_raw_body_and_secret_to_ingress(
    gateway_app: tuple[FastAPI, _FakeIngress],
) -> None:
    app, ingress = gateway_app
    body = b'{"update_id":1}'
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/hooks/local-endpoint",
            content=body,
            headers={
                "content-type": "application/json; charset=utf-8",
                "X-Telegram-Bot-Api-Secret-Token": "secret",
            },
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert ingress.calls == [("local-endpoint", "secret", body)]


@pytest.mark.asyncio
async def test_telegram_webhook_rejects_wrong_content_type_and_oversized_body(
    gateway_app: tuple[FastAPI, _FakeIngress],
) -> None:
    app, ingress = gateway_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        unsupported = await client.post(
            "/hooks/local-endpoint",
            content="{}",
            headers={
                "content-type": "text/plain",
            },
        )
        oversized = await client.post(
            "/hooks/local-endpoint",
            content=b"x" * 1_000_001,
            headers={"content-type": "application/json"},
        )

    assert unsupported.status_code == 415
    assert oversized.status_code == 413
    assert ingress.calls == []


@pytest.mark.asyncio
async def test_gateway_ack_p95_under_200ms_at_50_requests_per_second(
    gateway_app: tuple[FastAPI, _FakeIngress],
) -> None:
    app, ingress = gateway_app
    latencies: list[float] = []

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:

        async def send_one() -> float:
            started = perf_counter()
            response = await client.post(
                "/hooks/local-endpoint",
                content=b'{"update_id":1}',
                headers={"content-type": "application/json"},
            )
            assert response.status_code == 200
            return perf_counter() - started

        for batch in range(3):
            batch_started = perf_counter()
            latencies.extend(await asyncio.gather(*(send_one() for _ in range(50))))
            if batch < 2:
                await asyncio.sleep(max(0.0, 1.0 - (perf_counter() - batch_started)))

    p95 = sorted(latencies)[math.ceil(0.95 * len(latencies)) - 1]
    assert len(ingress.calls) == 150
    assert p95 < 0.2


@pytest.mark.parametrize("fail_startup", [False, True])
async def test_lifespan_preopens_capacity_and_closes_pool_even_on_failure(
    gateway_app: tuple[FastAPI, _FakeIngress],
    monkeypatch: pytest.MonkeyPatch,
    fail_startup: bool,
) -> None:
    class Pool:
        ready = False
        closed = False

        async def open(self, *, wait: bool) -> None:
            assert wait is True
            if fail_startup:
                raise RuntimeError("synthetic pool startup failure")
            self.ready = True

        async def close(self) -> None:
            self.closed = True

    pool = Pool()
    calls: list[tuple[int, int]] = []

    def factory(_dsn: str, *, min_size: int, max_size: int, autocommit: bool) -> Pool:
        assert autocommit is True
        calls.append((min_size, max_size))
        return pool

    monkeypatch.setattr(gateway, "build_pool", factory)
    app, _ = gateway_app
    if fail_startup:
        with pytest.raises(RuntimeError, match="synthetic pool startup failure"):
            async with app.router.lifespan_context(app):
                pytest.fail("startup must not yield after a pool error")
    else:
        async with app.router.lifespan_context(app):
            assert pool.ready and not pool.closed
            assert app.state.pool is pool
        assert pool.ready
    assert pool.closed
    assert calls == [(gateway.GATEWAY_WORKER_MAX_SIZE, gateway.GATEWAY_WORKER_MAX_SIZE)]
