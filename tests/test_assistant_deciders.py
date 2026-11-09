"""The assistant decider layer: Jev beside an LLM or rule fallback, per area."""

from __future__ import annotations

import json
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest
from csa.decide import deciders
from csa.decide.deciders import Decided, Decider, build_deciders
from csa.decide.jev import JevError, JevResponse, redact_for_jev

QUESTIONS: dict[str, dict[str, Any]] = {
    "mode": {
        "type": "choice",
        "instructions": "Shared or personalised?",
        "criteria": {"shared": "same text", "personalised": "per person"},
    },
    "needs_kb": {"type": "noul", "instructions": "Does `message` need the knowledge base?"},
}
FLOORS = {"noul": 0.7, "choice": 0.7}


def _jev(p_kb: float = 0.95, mode: str = "shared", mode_conf: float = 0.9) -> JevResponse:
    return JevResponse(
        model="jev-test",
        answers={
            "mode": {"choice": mode, "confidence": mode_conf},
            "needs_kb": {"noul": p_kb},
        },
    )


class FakeJev:
    model = "jev-test"

    def __init__(self, response: JevResponse | None = None, error: Exception | None = None) -> None:
        self.response = response if response is not None else _jev()
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def ask(self, state: object, questions: Any, *, timeout: float) -> JevResponse:
        self.calls.append({"state": state, "timeout": timeout})
        if self.error is not None:
            raise self.error
        return self.response


class Fallback:
    def __init__(self, answers: dict[str, object] | None = None) -> None:
        self.answers = (
            answers if answers is not None else {"mode": "personalised", "needs_kb": False}
        )
        self.calls = 0

    async def __call__(self) -> dict[str, object]:
        self.calls += 1
        return dict(self.answers)


@pytest.fixture
def rows(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """Stands in for the tenant transaction and the insert: every decision_log row written."""
    written: list[tuple[Any, ...]] = []

    @asynccontextmanager
    async def fake_tx(pool: object, client_id: str) -> AsyncIterator[object]:
        yield object()

    async def fake_execute(path: str, params: tuple[Any, ...], **_: object) -> None:
        assert path == "decide/insert_decision.sql"
        written.append(params)

    monkeypatch.setattr(deciders, "tx", fake_tx)
    monkeypatch.setattr(deciders.db.q, "execute", fake_execute)
    return written


def _decider(
    mode: str,
    client: FakeJev | None,
    *,
    audit: float = 0.0,
    area: str = "email",
) -> Decider:
    return Decider(
        area,
        mode,
        client,  # type: ignore[arg-type]
        object(),
        floors=FLOORS,
        audit_sample=audit,
        rng=random.Random(1),
    )


async def _decide(decider: Decider, fallback: Fallback, state: object | None = None) -> Decided:
    return await decider.decide(
        state if state is not None else {"message": "hi"},  # type: ignore[arg-type]
        QUESTIONS,
        fallback=fallback,
        trace_id=uuid4(),
        client_id=uuid4(),
    )


async def test_llm_mode_never_calls_jev(rows: list[Any]) -> None:
    jev = FakeJev()
    fallback = Fallback()
    out = await _decide(_decider("llm", jev), fallback)
    assert out.provider == "llm"
    assert out.answers == {"mode": "personalised", "needs_kb": False}
    assert jev.calls == [] and fallback.calls == 1
    assert rows == []


async def test_cascade_uses_jev_at_or_above_the_floor_and_skips_the_fallback(
    rows: list[Any],
) -> None:
    # noul 0.7 gives confidence exactly 0.7 (the floor); 0.3 is the same confidence, answer False.
    jev = FakeJev(_jev(p_kb=0.7, mode_conf=0.7))
    fallback = Fallback()
    out = await _decide(_decider("cascade", jev), fallback)
    assert out.provider == "jev" and out.fallback_reason is None
    assert out.answers == {"mode": "shared", "needs_kb": True}
    assert fallback.calls == 0
    assert jev.calls[0]["timeout"] == 2.0
    assert len(rows) == 1


async def test_noul_midpoint_and_confidence_are_pinned(rows: list[Any]) -> None:
    # p=0.5 is True with confidence 0.5 (below the floor): the fallback answers it.
    out = await _decide(
        _decider("cascade", FakeJev(_jev(p_kb=0.5))), Fallback({"mode": "x", "needs_kb": False})
    )
    assert out.answers["needs_kb"] is False
    # p=0.2 is False with confidence 0.8 (above the floor): Jev answers it, no fallback needed.
    fallback = Fallback()
    out = await _decide(_decider("cascade", FakeJev(_jev(p_kb=0.2))), fallback)
    assert out.answers["needs_kb"] is False and out.provider == "jev"
    assert fallback.calls == 0


async def test_cascade_below_floor_uses_the_fallback_and_logs_reason(rows: list[Any]) -> None:
    jev = FakeJev(_jev(p_kb=0.95, mode_conf=0.4))
    fallback = Fallback()
    out = await _decide(_decider("cascade", jev), fallback)
    # Judged per question: the confident noul stays Jev's, the weak choice goes to the fallback.
    assert out.answers == {"mode": "personalised", "needs_kb": True}
    assert out.provider == "llm" and out.fallback_reason == "low_confidence"
    assert fallback.calls == 1
    assert rows[0][3] == "cascade" and rows[0][4] == "llm" and rows[0][10] == "low_confidence"
    assert rows[0][9] == ["mode", "needs_kb"]  # Jev and the fallback differ on both


@pytest.mark.parametrize(
    "error", [JevError("jev_timeout"), JevError("jev_http_500"), RuntimeError("boom")]
)
async def test_jev_error_or_timeout_falls_back_without_raising(
    rows: list[Any], error: Exception
) -> None:
    fallback = Fallback()
    out = await _decide(_decider("cascade", FakeJev(error=error)), fallback)
    assert out.provider == "llm" and out.answers == fallback.answers
    assert out.fallback_reason == getattr(error, "code", type(error).__name__)
    assert fallback.calls == 1 and len(rows) == 1


async def test_a_malformed_jev_answer_falls_back(rows: list[Any]) -> None:
    bad = _jev()
    bad.answers["mode"] = {"choice": "not-an-option", "confidence": 0.99}
    out = await _decide(_decider("cascade", FakeJev(bad)), Fallback())
    assert out.provider == "llm" and out.fallback_reason == "jev_invalid_answer"


async def test_a_failing_log_write_does_not_break_the_decision(
    rows: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_: object, **__: object) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(deciders.db.q, "execute", broken)
    out = await _decide(_decider("cascade", FakeJev()), Fallback())
    assert out.provider == "jev"


async def test_shadow_uses_the_fallback_and_logs_both_answers_and_disagreement(
    rows: list[Any],
) -> None:
    jev = FakeJev(_jev(p_kb=0.95, mode="shared"))
    fallback = Fallback({"mode": "personalised", "needs_kb": True})
    out = await _decide(_decider("shadow", jev), fallback)
    assert out.provider == "llm" and out.answers == fallback.answers
    assert len(jev.calls) == 1 and fallback.calls == 1
    (row,) = rows
    assert row[3] == "shadow" and row[2] == "assistant_email"
    answers = row[7].obj
    assert set(answers) == {"jev", "llm"}
    assert answers["llm"] == {"mode": "personalised", "needs_kb": True}
    assert answers["jev"]["mode"]["answer"] == "shared"
    assert row[9] == ["mode"]


async def test_shadow_with_a_jev_error_still_answers_from_the_fallback(rows: list[Any]) -> None:
    out = await _decide(_decider("shadow", FakeJev(error=JevError("jev_timeout"))), Fallback())
    assert out.provider == "llm" and out.fallback_reason == "jev_timeout"
    assert rows[0][10] == "jev_timeout"


async def test_audit_sample_runs_the_fallback_beside_jev_and_logs_a_disagreement(
    rows: list[Any],
) -> None:
    jev = FakeJev(_jev(p_kb=0.95, mode="shared", mode_conf=0.95))
    fallback = Fallback({"mode": "personalised", "needs_kb": True})
    out = await _decide(_decider("cascade", jev, audit=1.0), fallback)
    assert out.provider == "jev" and out.answers["mode"] == "shared"  # Jev still decides
    assert fallback.calls == 1
    assert rows[0][4] == "jev" and rows[0][9] == ["mode"]
    assert rows[0][7].obj["llm"] == {"mode": "personalised", "needs_kb": True}


async def test_audit_zero_never_runs_the_fallback_when_jev_is_confident(rows: list[Any]) -> None:
    fallback = Fallback()
    await _decide(_decider("cascade", FakeJev(), audit=0.0), fallback)
    assert fallback.calls == 0


async def test_a_failing_audit_fallback_does_not_break_a_jev_decision(rows: list[Any]) -> None:
    async def failing() -> dict[str, object]:
        raise RuntimeError("llm down")

    out = await _decider("cascade", FakeJev(), audit=1.0).decide(
        {"message": "hi"}, QUESTIONS, fallback=failing, trace_id=uuid4(), client_id=uuid4()
    )
    assert out.provider == "jev"


async def test_decision_log_rows_carry_no_message_text(rows: list[Any]) -> None:
    secret = "please send the renewal notice to Zed about the blue plan"
    fallback = Fallback({"mode": "personalised", "needs_kb": True})
    await _decide(
        _decider("cascade", FakeJev(_jev(mode_conf=0.1)), audit=1.0),
        fallback,
        {"message": secret, "nested": {"note": secret}},
    )
    await _decide(_decider("shadow", FakeJev()), fallback, {"message": secret})
    assert len(rows) == 2
    for row in rows:
        dumped = json.dumps([row[7].obj, row[8].obj, row[9], row[10], row[5]], default=str)
        assert "renewal" not in dumped and "Zed" not in dumped and "blue plan" not in dumped
        for answer in row[8].obj.values():
            assert isinstance(answer, bool | str)


async def test_only_redacted_text_reaches_jev(rows: list[Any]) -> None:
    jev = FakeJev()
    state = {
        "message": "Mail Aisyah Rahman at aisyah@example.test or call +60 12-345 6789",
        "people": ["Aisyah Rahman", {"label": "aisyah rahman, Cik Mawar"}],
        "flags": {"first_contact": True, "count": 3},
    }
    names = ["Aisyah Rahman", "Cik Mawar", "  "]
    await _decider("cascade", jev).decide(
        state, QUESTIONS, fallback=Fallback(), trace_id=uuid4(), client_id=uuid4(), names=names
    )
    sent = jev.calls[0]["state"]
    dumped = json.dumps(sent)
    assert "aisyah@example.test" not in dumped and "12-345 6789" not in dumped
    assert "Aisyah" not in dumped and "Rahman" not in dumped and "Mawar" not in dumped
    assert sent["message"] == "Mail [NAME_0] at [EMAIL_0] or call [PHONE_0]"
    assert sent["people"][0] == "[NAME_0]"  # same number in every string of one call
    assert sent["people"][1] == {"label": "[NAME_0], [NAME_1]"}
    assert sent["flags"] == {"first_contact": True, "count": 3}
    # The caller's own state is not changed.
    assert "aisyah@example.test" in state["message"]


def test_redact_for_jev_is_whole_word_and_longest_first() -> None:
    assert redact_for_jev("Ann met Anna", names=["Ann", "Anna"]) == "[NAME_0] met [NAME_1]"
    assert redact_for_jev("Joanna", names=["Ann", "Jo"]) == "Joanna"
    assert redact_for_jev("a@b.test", names=["EMAIL"]) == "[EMAIL_0]"
    assert redact_for_jev("no names here") == "no names here"


async def test_auto_with_no_key_is_llm_and_never_errors(rows: list[Any]) -> None:
    decider = _decider("auto", None)
    out = await _decide(decider, Fallback())
    assert out.provider == "llm" and rows == []


def test_build_deciders_resolves_each_area(monkeypatch: pytest.MonkeyPatch) -> None:
    class S:
        ASSISTANT_DECIDER_EMAIL = "auto"
        ASSISTANT_DECIDER_ROUTING = "llm"
        ASSISTANT_DECIDER_INBOUND_INJECTION = "auto"
        ASSISTANT_JEV_FLOOR_NOUL = 0.8
        ASSISTANT_JEV_FLOOR_CHOICE = 0.6
        ASSISTANT_DECIDER_AUDIT_SAMPLE = 0.1

    keyless = build_deciders(S(), None, object())  # type: ignore[arg-type]
    assert keyless.email is None and keyless.routing is None

    class T(S):
        ASSISTANT_DECIDER_INBOUND_INJECTION = "cascade"

    built = build_deciders(T(), FakeJev(), object())  # type: ignore[arg-type]
    assert built.email is not None and built.email.mode == "cascade"  # auto with a key
    assert built.routing is None  # llm is the kill switch: no decider at all
    assert built.inbound is not None and built.inbound.mode == "cascade"
