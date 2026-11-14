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
``csa.graph.events.summarize``, scrubbed again here to code-shaped values. The ledger, not
Langfuse, is the billing authority.

Owner decision 2026-11-13: client and conversation UUIDs may be sent to Langfuse Cloud
(``user.id``, ``session.id``); free text is still scrubbed from node summaries.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
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

__all__ = ["SHUTDOWN_TIMEOUT_MILLIS", "LangfuseSink", "build_otlp_exporter"]

logger = structlog.get_logger(__name__)

OTLP_TRACES_PATH = "/api/public/otel/v1/traces"
INGESTION_VERSION = "4"
EXPORT_TIMEOUT_SECONDS = 10.0
# How long close waits for queued spans before giving up on them (inside Docker's stop grace).
SHUTDOWN_TIMEOUT_MILLIS = 3000

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

# A summary value Langfuse may see: a short code such as ``order_status`` (starts with a letter,
# no spaces, no ``@`` or ``+``), and no UUID or 32-hex run inside it.
_CODE_VALUE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,63}")
_UUID_LIKE = re.compile(
    r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", re.IGNORECASE
)
_REDACTED = "[redacted]"
_SUMMARY_MAX_DEPTH = 4

# The probe's fixed user: no caller-supplied identifier reaches Langfuse.
_PROBE_USER = "admin"
_PROBE_NAME = "csa-test-verification"


def _code(value: str) -> bool:
    return bool(_CODE_VALUE.fullmatch(value)) and _UUID_LIKE.search(value) is None


def _scrub(value: Any, depth: int = 0) -> Any:
    """A summary reduced to what Langfuse may see: numbers, booleans and short codes.

    Anything else (free text, a UUID, an email or phone number) becomes ``[redacted]``; keys that
    are not codes are dropped.
    """
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value if _code(value) else _REDACTED
    if depth >= _SUMMARY_MAX_DEPTH:
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            str(k): _scrub(v, depth + 1)
            for k, v in value.items()
            if isinstance(k, str) and _code(k)
        }
    if isinstance(value, list | tuple):
        return [_scrub(item, depth + 1) for item in value]
    return _REDACTED


# The trace id the next root span takes; set only around ``start_span`` on the caller's thread.
_PENDING_TRACE_ID: ContextVar[int | None] = ContextVar("csa_langfuse_trace_id", default=None)


# The OTLP exporter merges these environment variables into its request headers. They belong to
# the global collector (csa/core/tracing.py), so their headers must never be sent to Langfuse.
_ENV_HEADER_VARIABLES = ("OTEL_EXPORTER_OTLP_TRACES_HEADERS", "OTEL_EXPORTER_OTLP_HEADERS")


def build_otlp_exporter(public_key: str, secret_key: str, base_url: str) -> OTLPSpanExporter:
    """The OTLP/HTTP exporter aimed at Langfuse's v4 trace endpoint for one project's keys.

    Only the protocol headers and our two are sent: headers from the OTLP environment variables
    are stripped (with a warning naming the variable, never its value).
    """
    for variable in _ENV_HEADER_VARIABLES:
        if os.environ.get(variable):
            logger.warning("langfuse_env_otlp_headers_ignored", variable=variable)
    creds = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode("ascii")
    ours = {
        "authorization": f"Basic {creds}",
        "x-langfuse-ingestion-version": INGESTION_VERSION,
    }
    exporter = OTLPSpanExporter(
        endpoint=f"{base_url.rstrip('/')}{OTLP_TRACES_PATH}",
        headers=ours,
        timeout=EXPORT_TIMEOUT_SECONDS,
    )
    # The SDK keeps the merged, lower-cased headers on its HTTP client (pinned 1.45.1; the
    # header test reads the same dict). Keep the SDK's own user agent, nothing from the env.
    client = exporter._client
    client._headers = {
        "content-type": "application/x-protobuf",
        "user-agent": client._headers.get("user-agent", "csa-langfuse"),
        **ours,
    }
    return exporter


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
    """A ``BatchSpanProcessor`` that logs the spans a full queue drops and shuts down in bounded
    time."""

    _DROP_LOG_INTERVAL_SECONDS = 60.0

    def __init__(
        self,
        exporter: SpanExporter,
        *,
        max_queue_size: int,
        shutdown_timeout_millis: int,
        **kwargs: Any,
    ) -> None:
        super().__init__(exporter, max_queue_size=max_queue_size, **kwargs)
        self._queue_limit = max_queue_size
        self._shutdown_timeout_millis = shutdown_timeout_millis
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

    def shutdown(self) -> None:
        """Drain within ``shutdown_timeout_millis``, then give up on the rest with a warning.

        ``BatchSpanProcessor.shutdown`` takes no timeout and joins its worker for 30 s, while the
        final drain retries each batch for up to the exporter deadline; during a Langfuse outage
        that outlasts Docker's stop grace. The inner processor (pinned SDK 1.45.1) takes one: it
        joins the worker for that long, stops further batches and shuts the exporter down. A
        batch already in export keeps running on the daemon worker thread and is abandoned.
        """
        inner = self._batch_processor
        inner.shutdown(timeout_millis=self._shutdown_timeout_millis)
        queue = getattr(inner, "_queue", None)
        worker = getattr(inner, "_worker_thread", None)
        unsent = len(queue) if queue is not None else 0
        in_progress = bool(worker is not None and worker.is_alive())
        if unsent or in_progress:
            logger.warning(
                "langfuse_shutdown_incomplete",
                unsent_spans=unsent,
                export_in_progress=in_progress,
                timeout_ms=self._shutdown_timeout_millis,
            )


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
        shutdown_timeout_millis: int = SHUTDOWN_TIMEOUT_MILLIS,
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
                shutdown_timeout_millis=shutdown_timeout_millis,
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
        """Enqueue one node execution as a span on the turn's trace; ``False`` if it could not.

        The summary is scrubbed to numbers, booleans and short codes before it leaves.
        """
        try:
            attrs = _trace_attributes(
                client_id, conversation_id, _TURN_JOB, agent_id=JOB_AGENT[_TURN_JOB]
            )
            attrs.update(
                {
                    _OBS_TYPE: "span",
                    _OBS_LEVEL: "ERROR" if status == "error" else "DEFAULT",
                    _OBS_STATUS_MESSAGE: status,
                    _OBS_OUTPUT: json.dumps(_scrub(summary)),
                    f"{_OBS_METADATA}.node": node,
                    f"{_OBS_METADATA}.duration_ms": str(duration_ms),
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
        name: str = _PROBE_NAME,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Send one probe span and flush; ``(ok, langfuse_trace_id)`` from the export result.

        ``ok`` is true when the flush ran and no export failed meanwhile. Caveats: a failing
        batch of other spans exported during the probe also makes ``ok`` false (a false
        negative, never a false positive from a failed export); the SDK ignores flush timeouts
        (pinned 1.45.1), so during an outage the flush lasts as long as the exporter retries
        each queued batch (up to its 10 s deadline per batch), off the event loop; and with
        ``OTEL_SDK_DISABLED=true`` no span is recorded or exported, so ``ok`` is true while
        nothing reached Langfuse.

        The user is a fixed ``admin``, a name that is not a short code becomes the default, and
        metadata is scrubbed like a node summary.
        """
        trace_id = uuid4()
        attrs: dict[str, Any] = {
            _TRACE_NAME: name if _code(name) else _PROBE_NAME,
            _TRACE_TAGS: ["verification", "csa-rebuild"],
            _USER_ID: _PROBE_USER,
            _OBS_TYPE: "span",
            _OBS_STATUS_MESSAGE: "ok",
            _OBS_OUTPUT: json.dumps({"status": "ready", "csa_runtime": "active"}),
        }
        scrubbed = _scrub(metadata or {"source": "csa_verification"})
        for field, value in _metadata(scrubbed).items():
            attrs[f"{_TRACE_METADATA}.{field}"] = value
        failures_before = self._exporter.failures
        try:
            now = time.time_ns()
            self._emit(trace_id, "system-readiness-probe", attrs, start_ns=now, end_ns=now)
            flushed = await asyncio.to_thread(self._provider.force_flush)
        except Exception as exc:
            logger.error("langfuse_test_trace_failed", error_type=type(exc).__name__)
            return False, trace_id.hex
        ok = bool(flushed) and self._exporter.failures == failures_before
        if ok:
            logger.info("langfuse_test_trace_sent", trace_id=trace_id.hex)
        return ok, trace_id.hex

    # --- lifecycle -----------------------------------------------------------------------------

    def force_flush(self) -> bool:
        """Export everything queued now (blocking, no timeout in SDK 1.45.1; for tests)."""
        return self._provider.force_flush()

    async def aclose(self) -> None:
        """Flush what is queued and stop, off the event loop, within ``shutdown_timeout_millis``.

        The bound holds inside the worker thread (see ``_BoundedBatchProcessor.shutdown``), so
        ``asyncio.run`` joining its default executor at exit cannot hang on it either.
        """
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
