"""OpenTelemetry tracing (LG01 §8.2).

No backend container is wired in LG01. The exporter is selected by
``OTEL_EXPORTER``: ``console`` (default, dev/ci) prints spans to stdout; ``otlp``
sends OTLP/gRPC to ``OTEL_EXPORTER_OTLP_ENDPOINT``. The OTel ``trace_id`` is
reused as the structlog ``trace_id`` so a request's spans and log lines share
one id.
"""  # LG01

from __future__ import annotations

from typing import Any, Literal

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SpanExporter,
)


def build_exporter(
    exporter: Literal["console", "otlp"] = "console",
    otlp_endpoint: str | None = None,
) -> SpanExporter:
    """Return the span exporter selected by ``exporter``.

    ``console`` yields a :class:`ConsoleSpanExporter`; ``otlp`` yields an
    OTLP/gRPC exporter aimed at ``otlp_endpoint``.
    """
    if exporter == "otlp":
        # Imported lazily so console-only deployments need no gRPC stack at import.
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )

        return OTLPSpanExporter(endpoint=otlp_endpoint) if otlp_endpoint else OTLPSpanExporter()
    return ConsoleSpanExporter(formatter=lambda span: span.to_json(indent=None) + "\n")


def configure_tracing(
    exporter: Literal["console", "otlp"] = "console",
    otlp_endpoint: str | None = None,
    service_name: str = "csa",
) -> TracerProvider:
    """Install a :class:`TracerProvider` with the selected exporter.

    Both exporters use a batching processor so export I/O does not block the
    request event loop. Call ``force_flush()`` when tests need exported spans
    immediately. Returns the provider so callers may inspect or shut it down.
    """
    provider = TracerProvider()
    span_exporter = build_exporter(exporter, otlp_endpoint)
    provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(provider)
    return provider


def current_trace_id_hex() -> str | None:
    """Return the active span's trace id as 32-char hex, or ``None``.

    Used to seed the structlog ``trace_id`` so spans and logs correlate.
    """
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if not ctx.is_valid:
        return None
    return trace.format_trace_id(ctx.trace_id)


def instrument_fastapi(app: Any) -> None:
    """Trace requests without per-chunk ASGI receive/send child spans."""
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, exclude_spans=["receive", "send"])
