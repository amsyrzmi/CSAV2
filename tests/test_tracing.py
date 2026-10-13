"""Tracing exporter-selection tests (LG01 §8.2)."""  # LG01

from __future__ import annotations

import asyncio
import io
import json
import threading
from collections.abc import Iterator

import httpx
import pytest
from csa.core import tracing
from fastapi import FastAPI
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter


def test_console_exporter_default() -> None:
    exporter = tracing.build_exporter("console")
    assert isinstance(exporter, ConsoleSpanExporter)


def test_otlp_exporter_selected() -> None:
    exporter = tracing.build_exporter("otlp", "http://localhost:4317")
    assert isinstance(exporter, OTLPSpanExporter)


@pytest.fixture
def configured_provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[TracerProvider]:
    exporter = tracing.build_exporter("console")
    assert isinstance(exporter, ConsoleSpanExporter)
    exporter.out = io.StringIO()
    monkeypatch.setattr(tracing, "build_exporter", lambda *args: exporter)
    installed: list[TracerProvider] = []
    monkeypatch.setattr(tracing.trace, "set_tracer_provider", installed.append)
    provider = tracing.configure_tracing("console", service_name="csa-test")
    assert installed == [provider]
    monkeypatch.setattr(tracing.trace, "get_tracer_provider", lambda: provider)
    try:
        yield provider
    finally:
        provider.shutdown()


def test_configure_returns_provider_and_sets_global(configured_provider: TracerProvider) -> None:
    from opentelemetry import trace

    assert trace.get_tracer_provider() is configured_provider


def test_trace_id_shared_with_logging(configured_provider: TracerProvider) -> None:
    tracer = configured_provider.get_tracer("test")
    with tracer.start_as_current_span("root"):
        trace_id = tracing.current_trace_id_hex()
        assert trace_id is not None
        assert len(trace_id) == 32
        int(trace_id, 16)  # must be valid hex


def test_no_active_span_returns_none() -> None:
    # Outside any span, there may still be an invalid default span context.
    result = tracing.current_trace_id_hex()
    assert result is None or len(result) == 32


class RecordingOutput(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.writer_threads: list[int] = []
        self.written = threading.Event()

    def write(self, text: str) -> int:
        self.writer_threads.append(threading.get_ident())
        result = super().write(text)
        self.written.set()
        return result


async def test_batched_console_preserves_request_span_off_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = RecordingOutput()
    exporter = tracing.build_exporter("console")
    assert isinstance(exporter, ConsoleSpanExporter)
    exporter.out = output
    monkeypatch.setattr(tracing, "build_exporter", lambda *args: exporter)
    monkeypatch.setattr(tracing.trace, "set_tracer_provider", lambda provider: None)
    monkeypatch.setenv("OTEL_BSP_SCHEDULE_DELAY", "25")
    provider = tracing.configure_tracing("console")
    monkeypatch.setattr(tracing.trace, "get_tracer_provider", lambda: provider)
    app = FastAPI()

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    tracing.instrument_fastapi(app)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test"
        ) as client:
            response = await client.get("/healthz")
        assert response.status_code == 200
        assert await asyncio.to_thread(output.written.wait, 5)
        assert provider.force_flush()
        lines = output.getvalue().splitlines()
        assert len(lines) == 1
        span = json.loads(lines[0])
        assert span["name"] == "GET /healthz"
        assert span["kind"] == "SpanKind.SERVER"
        assert span["attributes"]["http.status_code"] == 200
        assert int(span["context"]["trace_id"], 16) != 0
        assert span["end_time"] >= span["start_time"]
        assert output.writer_threads
        assert threading.get_ident() not in output.writer_threads
    finally:
        provider.shutdown()
