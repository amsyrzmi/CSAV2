"""Offline tests for the Jev provider and the decision eval harness (LG06/LG08)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from csa.contracts.plan import Intent
from csa.contracts.state import Context, Signals
from csa.decide.jev import JevClient, JevError, JevProvider, classify_questions
from csa.evals.decisions import (
    DecisionCase,
    Prediction,
    agreement,
    load_cases,
    render_report,
    run_provider,
    score,
    to_prediction,
)
from csa.turn.deps import CAPABILITIES

from tests.test_coordinator import make_envelope


def _answers(**over: Any) -> dict[str, Any]:
    answers: dict[str, Any] = {
        "intent": {"type": "choice", "choice": "order", "confidence": 0.81},
        "language": {"type": "choice", "choice": "ms", "confidence": 0.9},
        "sentiment": {"type": "score", "score": 1.4, "confidence": 0.7},
        "injection": {"type": "noul", "noul": 0.1},
        "wants_human": {"type": "noul", "noul": 0.65},
        "urgent": {"type": "noul", "noul": 0.59},
        "needs:order_agent": {"type": "noul", "noul": 0.92},
        "needs:ticket_agent": {"type": "noul", "noul": 0.2},
    }
    for agent in CAPABILITIES:
        answers.setdefault(f"needs:{agent}", {"type": "noul", "noul": 0.0})
    answers.update(over)
    return answers


def _jev(handler: Any) -> JevProvider:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return JevProvider(JevClient("jev-test-key", http), capabilities=CAPABILITIES)


async def test_jev_classify_maps_answers_to_signals() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"model": "jev-1.13.2", "answers": _answers()})

    provider = _jev(handler)
    signals = await provider.classify(make_envelope("aku nak order 2 A1"), Context())

    assert seen["auth"] == "Bearer jev-test-key"
    assert seen["body"]["model"] == "jev-latest"
    assert seen["body"]["state"]["message"] == "aku nak order 2 A1"
    assert set(seen["body"]["questions"]) == set(classify_questions(CAPABILITIES))
    assert signals.provider == "jev"
    assert signals.version == "jev:jev-1.13.2"
    assert signals.intent == Intent.ORDER
    assert signals.capability_scores == {"order_agent": 0.92}
    assert signals.answers["language"] == "ms"
    assert signals.answers["sentiment"] == "frustrated"
    assert signals.answers["wants_human"] is True
    assert signals.answers["urgent"] is False
    assert signals.answers["p_urgent"] == 0.59
    assert signals.answers["intent_confidence"] == 0.81


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(401), "jev_http_401"),
        (httpx.Response(200, json={"model": "m"}), "jev_invalid_output"),
        (httpx.Response(200, json={"model": "m", "answers": {}}), "jev_missing_answers"),
        (
            httpx.Response(
                200,
                json={
                    "model": "m",
                    "answers": _answers(intent={"type": "choice", "choice": "nonsense"}),
                },
            ),
            "jev_invalid_answer",
        ),
    ],
)
async def test_jev_errors_are_coded_and_secret_free(response: httpx.Response, code: str) -> None:
    with pytest.raises(JevError) as err:
        await _jev(lambda _: response).classify(make_envelope("hi"), Context())
    assert err.value.code == code
    assert "jev-test-key" not in str(err.value)


async def test_jev_timeout_is_coded() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(JevError) as err:
        await _jev(slow).classify(make_envelope("hi"), Context())
    assert err.value.code == "jev_timeout"


def test_decision_cases_load_and_cover_languages_and_safety() -> None:
    cases = load_cases()
    assert len(cases) >= 60
    languages = {lang for c in cases for lang in c.expect.language}
    assert {"en", "ms", "zh", "ta", "other"} <= languages
    assert sum(c.expect.injection for c in cases) >= 5
    assert sum(c.expect.wants_human for c in cases) >= 5
    valid = {i.value for i in Intent}
    assert all(set(c.expect.intent) <= valid for c in cases)


def _case(cid: str, intent: str, *, injection: bool = False) -> DecisionCase:
    return DecisionCase.model_validate(
        {
            "id": cid,
            "text": cid,
            "expect": {"intent": intent, "language": "en", "injection": injection},
        }
    )


def _pred(cid: str, intent: str | None, *, injection: bool = False, ms: float = 10) -> Prediction:
    return Prediction(
        case_id=cid,
        provider="fake",
        intent=intent,
        language="en",
        wants_human=False,
        injection=injection,
        urgent=False,
        latency_ms=ms,
    )


def test_score_counts_accuracy_recall_and_errors() -> None:
    cases = [_case("a", "order"), _case("b", "chitchat"), _case("c", "other", injection=True)]
    preds = [
        _pred("a", "order", ms=10),
        _pred("b", "order", ms=30),
        Prediction(case_id="c", provider="fake", latency_ms=5, error="jev_timeout"),
    ]
    result = score(cases, preds)
    assert result.errors == 1
    assert result.accuracy["intent"] == pytest.approx(0.333)
    assert result.injection_recall == 0.0
    assert result.p50_ms == 20.0
    assert ("b", "intent", "order") in result.misses
    assert ("c", "intent", "jev_timeout") in result.misses


def test_score_rejects_missing_predictions() -> None:
    with pytest.raises(ValueError, match="missing"):
        score([_case("a", "order"), _case("b", "order")], [_pred("a", "order")])


def test_agreement_and_report() -> None:
    a = [_pred("x", "order"), _pred("y", "chitchat")]
    b = [_pred("x", "order"), _pred("y", "other")]
    agree = agreement(a, b)
    assert agree["intent"] == 0.5
    assert agree["language"] == 1.0
    from datetime import UTC, datetime

    report = render_report(
        [score([_case("x", "order"), _case("y", "chitchat")], a)],
        {"fake": "fake:1"},
        agree,
        when=datetime(2026, 10, 12, tzinfo=UTC),
    )
    assert "| fake | fake:1 | 0 |" in report
    assert "intent 50%" in report


async def test_run_provider_records_errors_without_stopping() -> None:
    class Flaky:
        async def classify(self, envelope: Any, ctx: Context) -> Signals:
            if envelope.message.body_redacted == "boom":
                raise JevError("jev_http_500")
            return Signals(
                intent=Intent.ORDER, answers={"language": "en"}, provider="jev", version="v"
            )

    preds = await run_provider("jev", Flaky(), [_case("ok", "order"), _case("boom", "order")])
    by_id = {p.case_id: p for p in preds}
    assert by_id["ok"].intent == "order"
    assert by_id["boom"].error == "jev_http_500"
    assert to_prediction("z", "jev", Signals(provider="jev", version="v"), 1).intent is None
