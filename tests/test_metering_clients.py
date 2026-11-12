"""The provider clients book token usage to the bound metering context, per HTTP response."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from csa.decide.completion import CompletionError
from csa.decide.gemini import GeminiClient
from csa.decide.groq import GroqClient
from csa.metering.context import (
    MeterContext,
    MeteringContextMissing,
    current_context,
    meter_context,
)
from csa.metering.meter import Usage
from pydantic import BaseModel
from structlog.testing import capture_logs


class Answer(BaseModel):
    text: str


class RecordingMeter:
    """A ``UsageMeter`` double: requires a real bound context and keeps what it is given."""

    def __init__(self) -> None:
        self.records: list[tuple[MeterContext, Usage]] = []

    def require_context(self) -> None:
        current_context()

    async def record(self, usage: Usage) -> None:
        self.records.append((current_context(), usage))


def _ctx() -> MeterContext:
    return MeterContext(client_id=uuid.uuid4(), trace_id=uuid.uuid4(), job="customer_turn")


def _groq_ok(usage: dict[str, Any] | None, content: str = '{"text": "ok"}') -> httpx.Response:
    body: dict[str, Any] = {"choices": [{"message": {"content": content}}]}
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body)


def _gemini_ok(usage: dict[str, Any] | None, text: str = '{"text": "ok"}') -> httpx.Response:
    body: dict[str, Any] = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
    if usage is not None:
        body["usageMetadata"] = usage
    return httpx.Response(200, json=body)


def _http(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _groq(handler: Callable[[httpx.Request], httpx.Response], meter: RecordingMeter) -> GroqClient:
    return GroqClient("k", "groq-model", _http(handler), meter=meter)


def _gemini(
    handler: Callable[[httpx.Request], httpx.Response], meter: RecordingMeter
) -> GeminiClient:
    return GeminiClient("k", "gemini-model", _http(handler), meter=meter)


def _sequence(*responses: httpx.Response) -> Callable[[httpx.Request], httpx.Response]:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        return queue.pop(0)

    return handler


async def test_groq_records_usage_on_success() -> None:
    meter = RecordingMeter()
    ctx = _ctx()
    client = _groq(lambda r: _groq_ok({"prompt_tokens": 120, "completion_tokens": 30}), meter)
    with meter_context(ctx):
        result = await client.json_completion(Answer, system="s", user="u", timeout=5)
    assert result.text == "ok"
    assert meter.records == [
        (
            ctx,
            Usage(
                provider="groq",
                model="groq-model",
                prompt_tokens=120,
                completion_tokens=30,
                schema_name="Answer",
            ),
        )
    ]


async def test_groq_records_each_attempt_that_carries_usage() -> None:
    meter = RecordingMeter()
    client = _groq(
        _sequence(
            httpx.Response(429, json={"usage": {"prompt_tokens": 50, "completion_tokens": 0}}),
            _groq_ok({"prompt_tokens": 50, "completion_tokens": 9}),
        ),
        meter,
    )
    with meter_context(_ctx()):
        await client.json_completion(Answer, system="s", user="u", timeout=5)
    assert [(u.prompt_tokens, u.completion_tokens) for _, u in meter.records] == [(50, 0), (50, 9)]


async def test_groq_a_5xx_without_usage_then_success_records_once() -> None:
    meter = RecordingMeter()
    client = _groq(
        _sequence(
            httpx.Response(500, json={"error": {}}),
            _groq_ok({"prompt_tokens": 7, "completion_tokens": 3}),
        ),
        meter,
    )
    with meter_context(_ctx()):
        await client.json_completion(Answer, system="s", user="u", timeout=5)
    assert [(u.prompt_tokens, u.completion_tokens) for _, u in meter.records] == [(7, 3)]


async def test_groq_records_usage_when_output_fails_validation() -> None:
    meter = RecordingMeter()
    client = _groq(
        lambda r: _groq_ok({"prompt_tokens": 40, "completion_tokens": 12}, content="not json"),
        meter,
    )
    with meter_context(_ctx()), pytest.raises(CompletionError) as err:
        await client.json_completion(Answer, system="s", user="u", timeout=5)
    assert err.value.code == "groq_invalid_output"
    assert [(u.prompt_tokens, u.completion_tokens) for _, u in meter.records] == [(40, 12)]


async def test_groq_no_usage_block_records_nothing() -> None:
    meter = RecordingMeter()
    client = _groq(lambda r: _groq_ok(None), meter)
    with meter_context(_ctx()):
        await client.json_completion(Answer, system="s", user="u", timeout=5)
    assert meter.records == []


@pytest.mark.parametrize(
    "usage",
    [
        {"prompt_tokens": "many", "completion_tokens": 3},
        {"prompt_tokens": 3},
        {"prompt_tokens": -1, "completion_tokens": 3},
        {"prompt_tokens": True, "completion_tokens": 3},
        "garbage",
    ],
)
async def test_groq_a_malformed_usage_block_records_nothing_and_does_not_break_the_call(
    usage: object,
) -> None:
    meter = RecordingMeter()
    client = _groq(lambda r: _groq_ok(usage), meter)  # type: ignore[arg-type]
    with meter_context(_ctx()), capture_logs() as logs:
        result = await client.json_completion(Answer, system="s", user="u", timeout=5)
    assert result.text == "ok"
    assert meter.records == []
    warnings = [e for e in logs if e["log_level"] == "warning"]
    assert [e["event"] for e in warnings] == ["usage_block_unusable"]
    assert "garbage" not in str(warnings) and "many" not in str(warnings)


async def test_gemini_records_usage_with_thinking_tokens() -> None:
    meter = RecordingMeter()
    ctx = _ctx()
    usage = {"promptTokenCount": 200, "candidatesTokenCount": 25, "thoughtsTokenCount": 75}
    client = _gemini(lambda r: _gemini_ok(usage), meter)
    with meter_context(ctx):
        await client.json_completion(Answer, system="s", user="u", timeout=5)
    assert meter.records == [
        (
            ctx,
            Usage(
                provider="gemini",
                model="gemini-model",
                prompt_tokens=200,
                completion_tokens=100,
                schema_name="Answer",
            ),
        )
    ]


async def test_gemini_thoughts_default_to_zero() -> None:
    meter = RecordingMeter()
    client = _gemini(
        lambda r: _gemini_ok({"promptTokenCount": 5, "candidatesTokenCount": 6}), meter
    )
    with meter_context(_ctx()):
        await client.json_completion(Answer, system="s", user="u", timeout=5)
    assert [(u.prompt_tokens, u.completion_tokens) for _, u in meter.records] == [(5, 6)]


async def test_gemini_candidates_default_to_zero_when_thinking_used_the_budget() -> None:
    # Gemini leaves candidatesTokenCount out when the thinking budget used every output token.
    meter = RecordingMeter()
    usage = {"promptTokenCount": 14, "thoughtsTokenCount": 90}
    client = _gemini(
        _sequence(httpx.Response(200, json={"usageMetadata": usage, "candidates": []})), meter
    )
    with meter_context(_ctx()), pytest.raises(CompletionError):
        await client.json_completion(Answer, system="s", user="u", timeout=5)
    assert [(u.prompt_tokens, u.completion_tokens) for _, u in meter.records] == [(14, 90)]


@pytest.mark.parametrize(
    "usage",
    [
        {"candidatesTokenCount": 4},
        {"promptTokenCount": "many", "candidatesTokenCount": 4},
        {"promptTokenCount": 3, "candidatesTokenCount": 4, "thoughtsTokenCount": "x"},
        {"promptTokenCount": -3, "candidatesTokenCount": 4},
        "garbage",
    ],
)
async def test_gemini_a_malformed_usage_block_records_nothing_and_does_not_break_the_call(
    usage: object,
) -> None:
    meter = RecordingMeter()
    client = _gemini(lambda r: _gemini_ok(usage), meter)  # type: ignore[arg-type]
    with meter_context(_ctx()), capture_logs() as logs:
        result = await client.json_completion(Answer, system="s", user="u", timeout=5)
    assert result.text == "ok"
    assert meter.records == []
    warnings = [e for e in logs if e["log_level"] == "warning"]
    assert [e["event"] for e in warnings] == ["usage_block_unusable"]


async def test_gemini_records_both_retried_responses_that_carry_usage() -> None:
    meter = RecordingMeter()
    full = {"promptTokenCount": 11, "candidatesTokenCount": 0}
    client = _gemini(
        _sequence(
            httpx.Response(500, json={"usageMetadata": full}),
            _gemini_ok({"promptTokenCount": 11, "candidatesTokenCount": 4}),
        ),
        meter,
    )
    with meter_context(_ctx()):
        await client.json_completion(Answer, system="s", user="u", timeout=5)
    assert [(u.prompt_tokens, u.completion_tokens) for _, u in meter.records] == [(11, 0), (11, 4)]


async def test_gemini_records_usage_when_output_fails_validation() -> None:
    meter = RecordingMeter()
    usage = {"promptTokenCount": 9, "candidatesTokenCount": 2}
    client = _gemini(lambda r: _gemini_ok(usage, text="nope"), meter)
    with meter_context(_ctx()), pytest.raises(CompletionError) as err:
        await client.json_completion(Answer, system="s", user="u", timeout=5)
    assert err.value.code == "gemini_invalid_output"
    assert len(meter.records) == 1


async def test_gemini_no_usage_metadata_records_nothing() -> None:
    meter = RecordingMeter()
    client = _gemini(lambda r: _gemini_ok(None), meter)
    with meter_context(_ctx()):
        await client.json_completion(Answer, system="s", user="u", timeout=5)
    assert meter.records == []


async def test_both_require_context_before_http() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _groq_ok({"prompt_tokens": 1, "completion_tokens": 1})

    meter = RecordingMeter()
    for client in (_groq(handler, meter), _gemini(handler, meter)):
        with pytest.raises(MeteringContextMissing):
            await client.json_completion(Answer, system="s", user="u", timeout=5)
    assert calls == []
    assert meter.records == []


async def test_fallback_inside_one_job_records_two_rows_one_trace() -> None:
    meter = RecordingMeter()
    groq = _groq(
        _sequence(
            httpx.Response(500, json={}),
            httpx.Response(500, json={"usage": {"prompt_tokens": 60, "completion_tokens": 0}}),
        ),
        meter,
    )
    gemini = _gemini(
        lambda r: _gemini_ok({"promptTokenCount": 61, "candidatesTokenCount": 8}), meter
    )
    ctx = _ctx()
    with meter_context(ctx):
        with pytest.raises(CompletionError):
            await groq.json_completion(Answer, system="s", user="u", timeout=5)
        await gemini.json_completion(Answer, system="s", user="u", timeout=5)
    assert [(c, u.provider) for c, u in meter.records] == [(ctx, "groq"), (ctx, "gemini")]
    assert {c.trace_id for c, _ in meter.records} == {ctx.trace_id}
