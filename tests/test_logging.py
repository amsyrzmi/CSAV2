"""Structured logging context-injection tests (LG01 §8.1)."""  # LG01

from __future__ import annotations

import json

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
    out = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(out)
    assert payload["trace_id"] == "t-4"
    assert payload["event"] == "event-happened"
    assert payload["answer"] == 42
