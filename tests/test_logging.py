"""Structured logging context-injection tests (LG01 §8.1)."""  # LG01

from __future__ import annotations

import json
import logging
from io import StringIO
from threading import Event

import pytest
import structlog
from csa.core import logging as clog


def _capture() -> structlog.testing.LogCapture:
    cap = structlog.testing.LogCapture()
    structlog.configure(
        processors=[structlog.contextvars.merge_contextvars, cap],
        wrapper_class=structlog.make_filtering_bound_logger(20),
        cache_logger_on_first_use=False,
    )
    return cap


def teardown_function() -> None:
    clog.shutdown_logging()
    clog.clear_context()
    structlog.reset_defaults()


def test_bound_context_keys_are_injected() -> None:
    cap = _capture()
    clog.bind_context(trace_id="t-1", client_id="c-1")
    clog.get_logger().info("hello")
    entry = cap.entries[0]
    assert entry["trace_id"] == "t-1"
    assert entry["client_id"] == "c-1"


def test_absent_context_keys_are_omitted() -> None:
    cap = _capture()
    clog.bind_context(trace_id="t-2")
    clog.get_logger().info("hello")
    entry = cap.entries[0]
    assert entry["trace_id"] == "t-2"
    assert "client_id" not in entry
    assert "conversation_id" not in entry
    assert "channel" not in entry


def test_none_values_are_not_bound() -> None:
    cap = _capture()
    clog.bind_context(trace_id="t-3", channel=None)
    clog.get_logger().info("hi")
    entry = cap.entries[0]
    assert "channel" not in entry


def test_json_renderer_produces_valid_json(capsys) -> None:
    clog.configure_logging(env="prod")
    clog.bind_context(trace_id="t-4")
    clog.get_logger().info("event-happened", answer=42)
    clog.flush_logging()
    out = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(out)
    assert payload["trace_id"] == "t-4"
    assert payload["event"] == "event-happened"
    assert payload["answer"] == 42


def test_stdlib_context_is_rendered_before_crossing_thread_boundary(capsys) -> None:
    clog.configure_logging(env="prod")
    clog.bind_context(trace_id="producer-context")
    logging.getLogger("synthetic").warning("stdlib-event")
    clog.clear_context()
    clog.flush_logging()
    entry = json.loads(capsys.readouterr().err.strip())
    assert entry["trace_id"] == "producer-context"
    assert entry["event"] == "stdlib-event"


def test_blocked_sink_is_bounded_observable_and_does_not_block_producer(monkeypatch) -> None:
    entered, release = Event(), Event()

    class Blocked(StringIO):
        def write(self, text: str) -> int:
            entered.set()
            if not release.wait(5):
                raise RuntimeError("synthetic sink deadline")
            return super().write(text)

    sink = Blocked()
    output = clog.LogOutput(capacity=2)
    try:
        output.write(sink, "first\n")
        assert entered.wait(1)
        monkeypatch.setattr(clog, "_OUTPUT_STALL_SECONDS", -1)
        assert output.health() == (False, "logging unavailable: sink_stalled; dropped_events=0")
        output.write(sink, "second\n")
        output.write(sink, "third\n")
        output.write(sink, "fourth\n")
        assert output.health() == (False, "logging unavailable: queue_full; dropped_events=1")
        with pytest.raises(RuntimeError, match="deadline"):
            output.flush(timeout=0.01)
        with pytest.raises(RuntimeError, match="shutdown timed out"):
            output.close(timeout=0.01)
    finally:
        release.set()
        with pytest.raises(RuntimeError, match="queue_full; dropped_events=1"):
            output.close()
    assert sink.getvalue() == "first\nsecond\nthird\n"


def test_failed_sink_has_secret_free_latched_health_error() -> None:
    class Broken(StringIO):
        def write(self, text: str) -> int:
            raise OSError("synthetic-private-output-error")

    output = clog.LogOutput()
    output.write(Broken(), "event\n")
    with pytest.raises(RuntimeError, match="sink_error:OSError; dropped_events=1"):
        output.flush()
    assert "synthetic-private" not in output.health()[1]
    with pytest.raises(RuntimeError, match="sink_error:OSError"):
        output.close()


def test_stall_threshold_and_recovery_without_event_loss(monkeypatch) -> None:
    entered, release = Event(), Event()
    now = [100.0]
    monkeypatch.setattr(clog.time, "monotonic", lambda: now[0])

    class Paused(StringIO):
        def write(self, text: str) -> int:
            entered.set()
            if not release.wait(5):
                raise RuntimeError("synthetic sink deadline")
            return super().write(text)

    sink = Paused()
    output = clog.LogOutput()
    try:
        output.write(sink, "retained\n")
        assert entered.wait(1)
        now[0] = 104.999
        assert output.health() == (True, "ok")
        now[0] = 105.001
        assert output.health() == (False, "logging unavailable: sink_stalled; dropped_events=0")
        release.set()
        output.flush()
        assert output.health() == (True, "ok")
        assert sink.getvalue() == "retained\n"
    finally:
        release.set()
        output.close()


def test_normal_shutdown_drains_queued_output() -> None:
    output = clog.LogOutput()
    sink = StringIO()
    for index in range(10):
        output.write(sink, f"event-{index}\n")
    output.close()
    assert len(sink.getvalue().splitlines()) == 10


def test_telegram_api_and_download_urls_redact_bot_tokens() -> None:
    from csa.core.logging import _redact_telegram_urls

    event = _redact_telegram_urls(
        None,
        "info",
        {
            "event": 'HTTP POST https://api.telegram.org/bot123:synthetic/getFile "200 OK"',
            "exception": "GET https://api.telegram.org/file/bot123:synthetic/voice/a.oga",
        },
    )
    assert "123:synthetic" not in str(event)
    assert "/getFile" in event["event"]
    assert "/voice/a.oga" in event["exception"]
