"""Langfuse observability sink (LG01 §8 / LG09): node events and generations over OTLP/HTTP.

Langfuse v4 ingests traces as OpenTelemetry spans at ``<base>/api/public/otel/v1/traces`` with
Basic auth and the ``x-langfuse-ingestion-version: 4`` header; the legacy batch endpoint
(``/api/public/ingestion``) no longer accepts traces, spans or generations. Attribute names follow
Langfuse's OTel property mapping (https://langfuse.com/integrations/native/opentelemetry#property-mapping
and .../opentelemetry/migration-to-v4), checked 2026-11-13.

The sink owns a dedicated ``TracerProvider`` (never the global one, so ``csa/core/tracing.py`` and
its exporters are untouched) behind a ``BatchSpanProcessor`` with a bounded queue: ``submit`` and
``send_event`` only enqueue, and a full queue drops the span with a warning. Export runs on the
processor's thread, and a failed export is logged and never reaches the caller.

Privacy: no prompt, reply or customer text and no cost goes on a span. A generation carries the
model, token counts and identifiers only; a node event carries the redacted summary from
``csa.graph.events.summarize``. The ledger, not Langfuse, is the billing authority.
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import structlog
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.id_generator import RandomIdGenerator
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from pydantic import SecretStr

from csa.metering.context import JOB_AGENT, JOB_LABELS

if TYPE_CHECKING:
    from csa.metering.context import Job, MeterContext
    from csa.metering.meter import Usage

__all__ = ["LangfuseSink", "build_otlp_exporter"]

logger = structlog.get_logger(__name__)

OTLP_TRACES_PATH = "/api/public/otel/v1/traces"
INGESTION_VERSION = "4"
EXPORT_TIMEOUT_SECONDS = 10.0

# Langfuse OTel attribute names (property mapping, Langfuse v4).
_TRACE_NAME = "langfuse.trace.name"
_TRACE_TAGS = "langfuse.trace.tags"
_TRACE_METADATA = "langfuse.trace.metadata"
_USER_ID = "user.id"
_SESSION_ID = "session.id"
_OBS_TYPE = "langfuse.observation.type"
_OBS_LEVEL = "langfuse.observation.level"
_OBS_STATUS_MESSAGE = "langfuse.observation.status_message"
_OBS_OUTPUT = "langfuse.observation.output"
_OBS_METADATA = "langfuse.observation.metadata"
_OBS_MODEL = "langfuse.observation.model.name"
_OBS_USAGE = "langfuse.observation.usage_details"

# Node events come from the customer-turn graph only.
_TURN_JOB: Job = "customer_turn"

# The trace id the next root span takes; set only around ``start_span`` on the caller's thread.
_PENDING_TRACE_ID: ContextVar[int | None] = ContextVar("csa_langfuse_trace_id", default=None)


def build_otlp_exporter(public_key: str, secret_key: str, base_url: str) -> OTLPSpanExporter:
    """The OTLP/HTTP exporter aimed at Langfuse's v4 trace endpoint for one project's keys."""
    creds = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode("ascii")
    return OTLPSpanExporter(
        endpoint=f"{base_url.rstrip('/')}{OTLP_TRACES_PATH}",
        headers={
            "Authorization": f"Basic {creds}",
            "x-langfuse-ingestion-version": INGESTION_VERSION,
        },
        timeout=EXPORT_TIMEOUT_SECONDS,
    )


class _TraceIdFromContext(RandomIdGenerator):
    """Root spans take the CSA trace id (a UUID is 128 bits, like an OTel trace id)."""

    def generate_trace_id(self) -> int:
        chosen = _PENDING_TRACE_ID.get()
        return chosen if chosen else super().generate_trace_id()


class _LoggedExporter(SpanExporter):
    """Wraps the real exporter: a failed or raising export is logged and counted, never raised."""

    def __init__(self, inner: SpanExporter) -> None:
        self._inner = inner
        self._lock = threading.Lock()
        self.failures = 0

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            result = self._inner.export(spans)
        except Exception as exc:  # the exporter thread must survive any transport error
            logger.error("langfuse_export_failed", error_type=type(exc).__name__, spans=len(spans))
            result = SpanExportResult.FAILURE
        else:
            if result is not SpanExportResult.SUCCESS:
                logger.error("langfuse_export_rejected", spans=len(spans))
        if result is not SpanExportResult.SUCCESS:
            with self._lock:
                self.failures += 1
        return result

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._inner.force_flush(timeout_millis)


class _BoundedBatchProcessor(BatchSpanProcessor):
    """A ``BatchSpanProcessor`` that logs, through structlog, the spans a full queue drops."""

    _DROP_LOG_INTERVAL_SECONDS = 60.0

    def __init__(self, exporter: SpanExporter, *, max_queue_size: int, **kwargs: Any) -> None:
        super().__init__(exporter, max_queue_size=max_queue_size, **kwargs)
        self._queue_limit = max_queue_size
        self._dropped = 0
        self._last_drop_log = float("-inf")

    def on_end(self, span: ReadableSpan) -> None:
        # The SDK drops the oldest span when its deque is full; count it here so the loss is loud
        # but logged at most once a minute while Langfuse is unreachable.
        queue = getattr(self._batch_processor, "_queue", None)
        if queue is not None and len(queue) >= self._queue_limit:
            self._dropped += 1
            now = time.monotonic()
            if now - self._last_drop_log >= self._DROP_LOG_INTERVAL_SECONDS:
                self._last_drop_log = now
                logger.warning(
                    "langfuse_queue_full",
                    max_queue_size=self._queue_limit,
                    dropped_total=self._dropped,
                )
        super().on_end(span)


def _trace_attributes(
    client_id: UUID | str, conversation_id: UUID | None, job: Job, *, agent_id: str
) -> dict[str, Any]:
    """Trace-level attributes, copied onto every span (v4 filters observations, not traces)."""
    attrs: dict[str, Any] = {
        _TRACE_NAME: job,
        _TRACE_TAGS: [agent_id, job],
        _USER_ID: str(client_id),
    }
    if conversation_id is not None:
        attrs[_SESSION_ID] = str(conversation_id)
    return attrs


class LangfuseSink:
    """Exports redacted node events and model generations to Langfuse as OTel spans.

    Implements ``GenerationMirror`` (``submit``) for ``LedgerMeter``.
    """

    def __init__(
        self,
        public_key: str,
        secret_key: SecretStr | str,
        base_url: str = "https://cloud.langfuse.com",
        *,
        exporter: SpanExporter | None = None,
        max_queue_size: int = 2048,
        max_export_batch_size: int = 256,
        schedule_delay_millis: int = 2000,
    ) -> None:
        """Build the dedicated provider; ``exporter`` replaces the OTLP exporter (tests)."""
        secret = secret_key.get_secret_value() if isinstance(secret_key, SecretStr) else secret_key
        inner = (
            exporter
            if exporter is not None
            else build_otlp_exporter(public_key, secret, base_url.rstrip("/"))
        )
        self._exporter = _LoggedExporter(inner)
        self._provider = TracerProvider(
            sampler=ALWAYS_ON,
            resource=Resource.create({"service.name": "csa"}),
            id_generator=_TraceIdFromContext(),
            shutdown_on_exit=False,
        )
        self._provider.add_span_processor(
            _BoundedBatchProcessor(
                self._exporter,
                max_queue_size=max_queue_size,
                max_export_batch_size=max_export_batch_size,
                schedule_delay_millis=schedule_delay_millis,
            )
        )
        self._tracer = self._provider.get_tracer("csa.langfuse")
        self._closed = False

    # --- GenerationMirror ----------------------------------------------------------------------

    def submit(self, ctx: MeterContext, usage: Usage) -> None:
        """Enqueue one generation: model and token counts, no prompt, reply or cost."""
        try:
            agent_id = JOB_AGENT[ctx.job]
            attrs = _trace_attributes(
                ctx.client_id, ctx.conversation_id, ctx.job, agent_id=agent_id
            )
            attrs.update(
                {
                    _OBS_TYPE: "generation",
                    _OBS_MODEL: usage.model,
                    _OBS_USAGE: json.dumps(
                        {"input": usage.prompt_tokens, "output": usage.completion_tokens}
                    ),
                    f"{_OBS_METADATA}.agent": agent_id,
                    f"{_OBS_METADATA}.job": ctx.job,
                    f"{_OBS_METADATA}.job_label": JOB_LABELS[ctx.job],
                    f"{_OBS_METADATA}.provider": usage.provider,
                    f"{_OBS_METADATA}.schema_name": usage.schema_name,
                }
            )
            now = time.time_ns()
            self._emit(ctx.trace_id, ctx.job, attrs, start_ns=now, end_ns=now)
        except Exception as exc:  # the mirror is best-effort; it must never fail a model call
            logger.warning(
                "langfuse_generation_failed",
                client_id=str(ctx.client_id),
                trace_id=str(ctx.trace_id),
                error_type=type(exc).__name__,
            )

    # --- graph node events ---------------------------------------------------------------------

    async def send_event(
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
    ) -> bool:
        """Enqueue one node execution as a span on the turn's trace; ``False`` if it could not."""
        try:
            attrs = _trace_attributes(
                client_id, conversation_id, _TURN_JOB, agent_id=JOB_AGENT[_TURN_JOB]
            )
            attrs.update(
                {
                    _OBS_TYPE: "span",
                    _OBS_LEVEL: "ERROR" if status == "error" else "DEFAULT",
                    _OBS_STATUS_MESSAGE: status,
                    _OBS_OUTPUT: json.dumps(summary, default=str),
                    f"{_OBS_METADATA}.node": node,
                    f"{_OBS_METADATA}.duration_ms": duration_ms,
                }
            )
            if agent:
                attrs[f"{_OBS_METADATA}.agent"] = agent
            end = time.time_ns()
            start = end - max(duration_ms, 0) * 1_000_000
            name = f"{agent}:{node}" if agent else node
            self._emit(trace_id, name, attrs, start_ns=start, end_ns=end)
            return True
        except Exception as exc:  # observability must never fail a customer turn
            logger.warning(
                "langfuse_event_failed",
                client_id=str(client_id),
                trace_id=str(trace_id),
                error_type=type(exc).__name__,
            )
            return False

    # --- connectivity probe --------------------------------------------------------------------

    async def send_test_trace(
        self,
        *,
        name: str = "csa-test-verification",
        user_id: str = "admin",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Send one probe span and flush; ``(ok, langfuse_trace_id)`` from the export result."""
        trace_id = uuid4()
        attrs: dict[str, Any] = {
            _TRACE_NAME: name,
            _TRACE_TAGS: ["verification", "csa-rebuild"],
            _USER_ID: user_id,
            _OBS_TYPE: "span",
            _OBS_STATUS_MESSAGE: "ok",
            _OBS_OUTPUT: json.dumps({"status": "ready", "csa_runtime": "active"}),
        }
        for key, value in _metadata(metadata or {"source": "csa_verification"}).items():
            attrs[f"{_TRACE_METADATA}.{key}"] = value
        failures_before = self._exporter.failures
        try:
            now = time.time_ns()
            self._emit(trace_id, "system-readiness-probe", attrs, start_ns=now, end_ns=now)
            flushed = await asyncio.to_thread(self._provider.force_flush, 15_000)
        except Exception as exc:
            logger.error("langfuse_test_trace_failed", error_type=type(exc).__name__)
            return False, trace_id.hex
        ok = bool(flushed) and self._exporter.failures == failures_before
        if ok:
            logger.info("langfuse_test_trace_sent", trace_id=trace_id.hex)
        return ok, trace_id.hex

    # --- lifecycle -----------------------------------------------------------------------------

    def force_flush(self, timeout_millis: int = 15_000) -> bool:
        """Export everything queued now (blocking; for tests and shutdown)."""
        return self._provider.force_flush(timeout_millis)

    async def aclose(self) -> None:
        """Flush what is queued and stop the exporter thread, off the event loop."""
        if self._closed:
            return
        self._closed = True
        try:
            await asyncio.to_thread(self._provider.shutdown)
        except Exception as exc:
            logger.warning("langfuse_shutdown_failed", error_type=type(exc).__name__)

    def _emit(
        self,
        trace_id: UUID,
        name: str,
        attributes: Mapping[str, Any],
        *,
        start_ns: int,
        end_ns: int,
    ) -> None:
        # An empty parent context: the span is a root on the CSA trace, never a child of whatever
        # span (from the global provider) happens to be active on this task.
        token = _PENDING_TRACE_ID.set(trace_id.int)
        try:
            span = self._tracer.start_span(
                name, context=Context(), attributes=attributes, start_time=start_ns
            )
        finally:
            _PENDING_TRACE_ID.reset(token)
        span.end(end_time=end_ns)


def _metadata(metadata: Mapping[str, Any]) -> dict[str, str | int | float | bool]:
    """Flatten metadata to OTel scalar attribute values (anything else becomes JSON text)."""
    out: dict[str, str | int | float | bool] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, str | int | float | bool):
            out[str(key)] = value
        else:
            out[str(key)] = json.dumps(value, default=str)
    return out
