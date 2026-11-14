"""The Langfuse sink: node events and generations as OTel spans over OTLP/HTTP (Langfuse v4).

Spans go to an ``InMemorySpanExporter`` injected through the constructor, so nothing leaves the
process. All identifiers and keys are synthetic.
"""

from __future__ import annotations

import base64
import inspect
import json
import threading
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from csa.core import langfuse as langfuse_module
from csa.core.langfuse import SHUTDOWN_TIMEOUT_MILLIS, LangfuseSink, build_otlp_exporter
from csa.graph.events import PostgresEventSink, summarize
from csa.metering.context import JOB_AGENT, JOB_LABELS, MeterContext
from csa.metering.meter import GenerationMirror, Usage
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from structlog.testing import capture_logs

PUBLIC_KEY = "pk-lf-synthetic"
SECRET_KEY = "sk-lf-synthetic"
BASE_URL = "https://langfuse.example.test"

USAGE = Usage(
    provider="groq",
    model="synthetic-model-8b",
    prompt_tokens=1200,
    completion_tokens=340,
    schema_name="SyntheticPlan",
)


def _sink(exporter: SpanExporter, **kwargs: Any) -> LangfuseSink:
    return LangfuseSink(PUBLIC_KEY, SECRET_KEY, BASE_URL, exporter=exporter, **kwargs)


@pytest.fixture
async def memory() -> AsyncIterator[tuple[LangfuseSink, InMemorySpanExporter]]:
    exporter = InMemorySpanExporter()
    sink = _sink(exporter)
    yield sink, exporter
    await sink.aclose()


def _spans(sink: LangfuseSink, exporter: InMemorySpanExporter) -> Sequence[ReadableSpan]:
    assert sink.force_flush() is True
    return exporter.get_finished_spans()


def _attrs(span: ReadableSpan) -> dict[str, Any]:
    return dict(span.attributes or {})


def _ctx(job: Any = "customer_turn", *, conversation: bool = True) -> MeterContext:
    return MeterContext(
        client_id=uuid4(),
        trace_id=uuid4(),
        job=job,
        conversation_id=uuid4() if conversation else None,
    )


async def _send_event(sink: LangfuseSink, **overrides: Any) -> bool:
    kwargs: dict[str, Any] = {
        "client_id": uuid4(),
        "trace_id": uuid4(),
        "conversation_id": uuid4(),
        "node": "plan_args",
        "agent": "order_agent",
        "status": "ok",
        "duration_ms": 45,
        "summary": {"intent": "order_status", "confidence": 0.95},
    }
    kwargs.update(overrides)
    return await sink.send_event(**kwargs)


# --- generations -------------------------------------------------------------------------------


def test_the_sink_is_a_generation_mirror(
    memory: tuple[LangfuseSink, InMemorySpanExporter],
) -> None:
    sink, _ = memory
    mirror: GenerationMirror = sink
    assert callable(mirror.submit)


def test_generation_span_carries_model_usage_user_session_and_job(
    memory: tuple[LangfuseSink, InMemorySpanExporter],
) -> None:
    sink, exporter = memory
    ctx = _ctx("customer_turn")

    sink.submit(ctx, USAGE)

    [span] = _spans(sink, exporter)
    attrs = _attrs(span)
    assert span.name == "customer_turn"
    assert span.parent is None
    assert span.context is not None
    assert span.context.trace_id == ctx.trace_id.int
    assert attrs["langfuse.observation.type"] == "generation"
    assert attrs["langfuse.observation.model.name"] == "synthetic-model-8b"
    assert json.loads(attrs["langfuse.observation.usage_details"]) == {
        "input": 1200,
        "output": 340,
    }
    assert attrs["user.id"] == str(ctx.client_id)
    assert attrs["session.id"] == str(ctx.conversation_id)
    assert attrs["langfuse.trace.name"] == "customer_turn"
    assert tuple(attrs["langfuse.trace.tags"]) == ("support", "customer_turn")
    assert attrs["langfuse.observation.metadata.agent"] == "support"
    assert attrs["langfuse.observation.metadata.job"] == "customer_turn"
    assert attrs["langfuse.observation.metadata.job_label"] == "Customer reply"
    assert attrs["langfuse.observation.metadata.provider"] == "groq"
    assert attrs["langfuse.observation.metadata.schema_name"] == "SyntheticPlan"


@pytest.mark.parametrize("job", sorted(JOB_AGENT))
def test_every_job_is_tagged_with_its_agent(
    memory: tuple[LangfuseSink, InMemorySpanExporter], job: Any
) -> None:
    sink, exporter = memory

    sink.submit(_ctx(job, conversation=False), USAGE)

    [span] = _spans(sink, exporter)
    attrs = _attrs(span)
    assert span.name == job
    assert attrs["langfuse.trace.name"] == job
    assert tuple(attrs["langfuse.trace.tags"]) == (JOB_AGENT[job], job)
    assert attrs["langfuse.observation.metadata.agent"] == JOB_AGENT[job]
    assert attrs["langfuse.observation.metadata.job_label"] == JOB_LABELS[job]
    assert "session.id" not in attrs


def test_generation_ignores_the_ambient_span(
    memory: tuple[LangfuseSink, InMemorySpanExporter],
) -> None:
    """A request span from another provider (FastAPI instrumentation) must not adopt the call."""
    sink, exporter = memory
    other = TracerProvider()
    ctx = _ctx("workspace_assistant")

    with other.get_tracer("ambient").start_as_current_span("http-request"):
        sink.submit(ctx, USAGE)

    [span] = _spans(sink, exporter)
    assert span.parent is None
    assert span.context is not None
    assert span.context.trace_id == ctx.trace_id.int
    other.shutdown()


# Synthetic customer text standing in for a real prompt and reply; it must never reach a span.
PROMPT_TEXT = "synthetic-customer-prompt where is my parcel 7781"
REPLY_TEXT = "synthetic-bot-reply your parcel ships tomorrow"


async def test_no_span_carries_prompt_reply_or_cost(
    memory: tuple[LangfuseSink, InMemorySpanExporter],
) -> None:
    sink, exporter = memory
    ctx = _ctx()
    # The node summary comes from the graph's own redaction over an update holding the texts.
    agent, summary = summarize(
        "compose",
        {
            "messages": [{"role": "user", "text": PROMPT_TEXT}],
            "draft": {"text": REPLY_TEXT, "language": "ms", "fact_ids": ["f1"]},
            "signals": {"intent": "order_status", "provider": "groq"},
        },
    )
    sink.submit(ctx, USAGE)
    await _send_event(
        sink, client_id=ctx.client_id, trace_id=ctx.trace_id, agent=agent, summary=summary
    )
    await sink.send_test_trace()

    spans = _spans(sink, exporter)
    assert len(spans) == 3
    for span in spans:
        attrs = _attrs(span)
        is_generation = attrs["langfuse.observation.type"] == "generation"
        banned_keys = ["prompt", "completion", "reply", "cost", "langfuse.observation.input"]
        if is_generation:
            banned_keys += ["input", "output"]
        for key, value in attrs.items():
            for banned in banned_keys:
                assert banned not in key.lower(), (span.name, key)
            text = json.dumps(list(value)) if isinstance(value, tuple) else str(value)
            assert PROMPT_TEXT not in text, (span.name, key)
            assert REPLY_TEXT not in text, (span.name, key)
            assert "parcel" not in text, (span.name, key)


# --- privacy: ids only where approved, nothing else identifying ------------------------------


def _spellings(value: UUID) -> list[str]:
    plain = [str(value), value.hex]
    return [s.lower() for s in plain] + [s.upper() for s in plain]


def _walk(value: Any) -> list[str]:
    """Every string inside an attribute value, JSON text decoded and nested values included."""
    if isinstance(value, str):
        out = [value]
        try:
            decoded = json.loads(value)
        except ValueError:
            return out
        if not isinstance(decoded, str):
            out += _walk(decoded)
        return out
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in [str(k), *_walk(v)]]
    if isinstance(value, list | tuple):
        return [s for item in value for s in _walk(item)]
    return [str(value)]


# The only attributes allowed to carry the client and conversation ids.
_ID_ATTRIBUTES = {"user.id", "session.id"}


async def test_no_free_text_contact_detail_or_key_material_reaches_a_span(
    memory: tuple[LangfuseSink, InMemorySpanExporter],
) -> None:
    """Owner decision 2026-11-13: the client and conversation UUIDs may go to Langfuse Cloud as
    ``user.id`` and ``session.id`` (exactly ``str(uuid)``). Nothing else identifying may: no free
    text, email, phone number, other UUID or key material anywhere on a span, and the two ids
    appear in no attribute but those two (summaries and probe metadata are scrubbed).
    """
    sink, exporter = memory
    client_id, conversation_id, employee_id = uuid4(), uuid4(), uuid4()
    ctx = MeterContext(
        client_id=client_id, trace_id=uuid4(), job="customer_turn", conversation_id=conversation_id
    )
    email, phone = "synthetic.person@example.test", "+60123456789"
    sink.submit(ctx, USAGE)
    await _send_event(
        sink,
        client_id=client_id,
        trace_id=ctx.trace_id,
        conversation_id=conversation_id,
        summary={
            "intent": "order_status",
            "confidence": 0.9,
            "task_id": str(employee_id),
            "reason": f"customer {email} asked about {client_id.hex}",
            "handoff_reason": phone,
            "nested": {"subject": str(conversation_id).upper(), "ids": [employee_id.hex]},
        },
    )
    await sink.send_test_trace(metadata={"client": str(client_id)})

    spans = _spans(sink, exporter)
    assert len(spans) == 3
    always_banned = [
        *_spellings(employee_id),
        email,
        phone,
        "asked about",
        SECRET_KEY,
        base64.b64encode(f"{PUBLIC_KEY}:{SECRET_KEY}".encode()).decode(),
    ]
    approved_ids = [*_spellings(client_id), *_spellings(conversation_id)]
    for span in spans:
        attrs = dict(span.attributes or {})
        texts = [span.name, *_walk(dict(span.resource.attributes))]
        texts += [t for k, v in attrs.items() if k not in _ID_ATTRIBUTES for t in _walk(v)]
        for text in texts:
            for needle in [*always_banned, *approved_ids]:
                assert needle.lower() not in text.lower(), (span.name, needle)
        for key in _ID_ATTRIBUTES & attrs.keys():
            for needle in always_banned:
                assert needle.lower() not in str(attrs[key]).lower(), (span.name, needle)
    probe, *turn = sorted(spans, key=lambda span: _attrs(span)["user.id"] != "admin")
    assert _attrs(probe)["user.id"] == "admin"
    assert "session.id" not in _attrs(probe)
    for span in turn:
        assert _attrs(span)["user.id"] == str(client_id)
        assert _attrs(span)["session.id"] == str(conversation_id)
    event = next(s for s in spans if s.name == "order_agent:plan_args")
    output = json.loads(_attrs(event)["langfuse.observation.output"])
    # Code-shaped values survive; identifiers, contact details and free text do not.
    assert output["intent"] == "order_status"
    assert output["confidence"] == 0.9
    assert output["task_id"] == output["reason"] == output["handoff_reason"] == "[redacted]"


# --- node events -------------------------------------------------------------------------------


def test_send_event_signature_is_unchanged() -> None:
    params = inspect.signature(LangfuseSink.send_event).parameters
    assert list(params) == [
        "self",
        "client_id",
        "trace_id",
        "conversation_id",
        "node",
        "agent",
        "status",
        "duration_ms",
        "summary",
    ]
    assert all(
        p.kind is inspect.Parameter.KEYWORD_ONLY for name, p in params.items() if name != "self"
    )
    assert inspect.iscoroutinefunction(LangfuseSink.send_event)


async def test_send_event_emits_a_span_on_the_turn_trace(
    memory: tuple[LangfuseSink, InMemorySpanExporter],
) -> None:
    sink, exporter = memory
    client_id, trace_id, conversation_id = uuid4(), uuid4(), uuid4()

    ok = await _send_event(
        sink, client_id=client_id, trace_id=trace_id, conversation_id=conversation_id
    )

    assert ok is True
    [span] = _spans(sink, exporter)
    attrs = _attrs(span)
    assert span.name == "order_agent:plan_args"
    assert span.context is not None
    assert span.context.trace_id == trace_id.int
    assert span.end_time is not None and span.start_time is not None
    assert span.end_time - span.start_time == 45 * 1_000_000
    assert attrs["langfuse.observation.type"] == "span"
    assert attrs["langfuse.observation.level"] == "DEFAULT"
    assert attrs["langfuse.observation.status_message"] == "ok"
    assert attrs["user.id"] == str(client_id)
    assert attrs["session.id"] == str(conversation_id)
    assert attrs["langfuse.trace.name"] == "customer_turn"
    assert tuple(attrs["langfuse.trace.tags"]) == ("support", "customer_turn")
    assert attrs["langfuse.observation.metadata.node"] == "plan_args"
    assert attrs["langfuse.observation.metadata.agent"] == "order_agent"
    assert attrs["langfuse.observation.metadata.duration_ms"] == "45"
    assert json.loads(attrs["langfuse.observation.output"]) == {
        "intent": "order_status",
        "confidence": 0.95,
    }


async def test_send_event_error_is_an_error_level_span(
    memory: tuple[LangfuseSink, InMemorySpanExporter],
) -> None:
    sink, exporter = memory

    await _send_event(sink, node="signals", agent=None, status="error", conversation_id=None)

    [span] = _spans(sink, exporter)
    attrs = _attrs(span)
    assert span.name == "signals"
    assert attrs["langfuse.observation.level"] == "ERROR"
    assert "session.id" not in attrs
    assert "langfuse.observation.metadata.agent" not in attrs


async def test_generation_and_node_events_share_the_turn_trace(
    memory: tuple[LangfuseSink, InMemorySpanExporter],
) -> None:
    sink, exporter = memory
    ctx = _ctx("customer_turn")

    await _send_event(sink, client_id=ctx.client_id, trace_id=ctx.trace_id)
    sink.submit(ctx, USAGE)

    spans = _spans(sink, exporter)
    assert len(spans) == 2
    assert {s.context.trace_id for s in spans if s.context is not None} == {ctx.trace_id.int}


# --- failures never reach the caller ----------------------------------------------------------


class RaisingExporter(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        raise ConnectionError("synthetic: langfuse unreachable")

    def shutdown(self) -> None:
        return None


class RejectingExporter(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        return None


async def test_exporter_failure_is_logged_and_not_raised() -> None:
    sink = _sink(RaisingExporter())
    try:
        with capture_logs() as logs:
            sink.submit(_ctx(), USAGE)
            assert await _send_event(sink) is True
            sink.force_flush()
        failed = [e for e in logs if e["event"] == "langfuse_export_failed"]
        assert failed and failed[0]["log_level"] == "error"
        assert failed[0]["error_type"] == "ConnectionError"
    finally:
        await sink.aclose()


async def test_exporter_rejection_is_logged() -> None:
    sink = _sink(RejectingExporter())
    try:
        with capture_logs() as logs:
            sink.submit(_ctx(), USAGE)
            sink.force_flush()
        assert any(e["event"] == "langfuse_export_rejected" for e in logs)
    finally:
        await sink.aclose()


class BlockingExporter(SpanExporter):
    """Holds the first export open so the queue behind it fills."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.entered.set()
        self.release.wait(10)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self.release.set()


async def test_full_queue_drops_with_a_logged_warning() -> None:
    exporter = BlockingExporter()
    sink = _sink(exporter, max_queue_size=2, max_export_batch_size=1)
    try:
        with capture_logs() as logs:
            sink.submit(_ctx(), USAGE)  # taken by the worker, which then blocks in export
            assert exporter.entered.wait(5)
            sink.submit(_ctx(), USAGE)
            sink.submit(_ctx(), USAGE)  # the queue now holds its two
            sink.submit(_ctx(), USAGE)  # dropped
        full = [e for e in logs if e["event"] == "langfuse_queue_full"]
        assert len(full) == 1
        assert full[0]["log_level"] == "warning"
        assert full[0]["max_queue_size"] == 2
    finally:
        exporter.release.set()
        await sink.aclose()


async def test_submit_and_send_event_after_close_do_not_raise() -> None:
    sink = _sink(InMemorySpanExporter())
    await sink.aclose()

    sink.submit(_ctx(), USAGE)
    await _send_event(sink)


class StuckExporter(SpanExporter):
    """An export that hangs (Langfuse unreachable) and a shutdown that does not unstick it."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.unstick = threading.Event()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.entered.set()
        self.unstick.wait(30)
        return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        return None


async def test_close_is_bounded_while_the_export_hangs() -> None:
    assert SHUTDOWN_TIMEOUT_MILLIS <= 3000
    exporter = StuckExporter()
    sink = _sink(exporter, max_export_batch_size=1)
    try:
        sink.submit(_ctx(), USAGE)
        assert exporter.entered.wait(5)
        sink.submit(_ctx(), USAGE)
        sink.submit(_ctx(), USAGE)

        with capture_logs() as logs:
            started = time.monotonic()
            await sink.aclose()
            elapsed = time.monotonic() - started

        assert elapsed < SHUTDOWN_TIMEOUT_MILLIS / 1000 + 2.0
        [warning] = [e for e in logs if e["event"] == "langfuse_shutdown_incomplete"]
        assert warning["log_level"] == "warning"
        assert warning["unsent_spans"] >= 1
        assert warning["export_in_progress"] is True
        assert SECRET_KEY not in json.dumps(warning, default=str)
    finally:
        exporter.unstick.set()


async def test_spans_queued_before_close_are_still_exported() -> None:
    exporter = InMemorySpanExporter()
    sink = _sink(exporter, schedule_delay_millis=60_000)
    for _ in range(3):
        sink.submit(_ctx(), USAGE)
    assert exporter.get_finished_spans() == ()

    with capture_logs() as logs:
        await sink.aclose()

    assert len(exporter.get_finished_spans()) == 3
    assert not [e for e in logs if e["event"] == "langfuse_shutdown_incomplete"]


@pytest.mark.parametrize(
    "variable", ["OTEL_EXPORTER_OTLP_HEADERS", "OTEL_EXPORTER_OTLP_TRACES_HEADERS"]
)
def test_environment_otlp_headers_never_reach_langfuse(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    monkeypatch.setenv(variable, "x-collector-token=synthetic-env-secret")
    with capture_logs() as logs:
        exporter = build_otlp_exporter(PUBLIC_KEY, SECRET_KEY, BASE_URL)
    try:
        headers = exporter._client._headers
        assert "x-collector-token" not in headers
        assert "synthetic-env-secret" not in json.dumps(headers)
        assert headers["x-langfuse-ingestion-version"] == "4"
        assert headers["content-type"] == "application/x-protobuf"
        [warning] = [e for e in logs if e["event"] == "langfuse_env_otlp_headers_ignored"]
        assert warning["variable"] == variable
        assert "synthetic-env-secret" not in json.dumps(warning, default=str)
    finally:
        exporter.shutdown()


# --- transport configuration -------------------------------------------------------------------


def test_otlp_exporter_targets_langfuse_v4_endpoint_with_basic_auth() -> None:
    exporter = build_otlp_exporter(PUBLIC_KEY, SECRET_KEY, BASE_URL + "/")
    try:
        expected_auth = "Basic " + base64.b64encode(f"{PUBLIC_KEY}:{SECRET_KEY}".encode()).decode()
        assert exporter._endpoint == f"{BASE_URL}/api/public/otel/v1/traces"
        headers = exporter._client._headers
        assert headers["authorization"] == expected_auth
        assert headers["x-langfuse-ingestion-version"] == "4"
    finally:
        exporter.shutdown()


async def test_sink_builds_the_otlp_exporter_when_none_is_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    def fake_build(public_key: str, secret_key: str, base_url: str) -> SpanExporter:
        calls.append((public_key, secret_key, base_url))
        return InMemorySpanExporter()

    monkeypatch.setattr(langfuse_module, "build_otlp_exporter", fake_build)
    sink = LangfuseSink(PUBLIC_KEY, SECRET_KEY, BASE_URL)
    await sink.aclose()

    assert calls == [(PUBLIC_KEY, SECRET_KEY, BASE_URL)]


# --- connectivity probe ------------------------------------------------------------------------


async def test_send_test_trace_reports_success_and_the_trace_id(
    memory: tuple[LangfuseSink, InMemorySpanExporter],
) -> None:
    sink, exporter = memory

    ok, trace_id = await sink.send_test_trace(
        name="initialization-probe", metadata={"environment": "dev"}
    )

    assert ok is True
    [span] = exporter.get_finished_spans()
    attrs = _attrs(span)
    assert span.context is not None
    assert trace_id == f"{span.context.trace_id:032x}"
    UUID(hex=trace_id)
    assert attrs["langfuse.trace.name"] == "initialization-probe"
    assert attrs["user.id"] == "admin"
    assert attrs["langfuse.trace.metadata.environment"] == "dev"
    assert tuple(attrs["langfuse.trace.tags"]) == ("verification", "csa-rebuild")


async def test_send_test_trace_reports_an_export_failure() -> None:
    sink = _sink(RejectingExporter())
    try:
        ok, trace_id = await sink.send_test_trace()
        assert ok is False
        assert len(trace_id) == 32
    finally:
        await sink.aclose()


# --- PostgresEventSink still forwards ---------------------------------------------------------


@pytest.mark.asyncio
async def test_postgres_event_sink_forwards_to_langfuse() -> None:
    mock_pool = MagicMock()
    mock_langfuse = MagicMock(spec=LangfuseSink)
    mock_langfuse.send_event = AsyncMock()

    sink = PostgresEventSink(mock_pool, langfuse_sink=mock_langfuse)

    client_id = uuid4()
    trace_id = uuid4()
    mock_conn = AsyncMock()

    with pytest.MonkeyPatch.context() as m:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_conn
        mock_ctx.__aexit__.return_value = None
        m.setattr("csa.graph.events.tx", MagicMock(return_value=mock_ctx))
        m.setattr("csa.graph.events.db.q.execute", AsyncMock())

        await sink.record(
            client_id=client_id,
            trace_id=trace_id,
            conversation_id=None,
            node="guard",
            agent=None,
            status="ok",
            duration_ms=5,
            summary={"allowed": True},
        )

        mock_langfuse.send_event.assert_awaited_once_with(
            client_id=client_id,
            trace_id=trace_id,
            conversation_id=None,
            node="guard",
            agent=None,
            status="ok",
            duration_ms=5,
            summary={"allowed": True},
        )
