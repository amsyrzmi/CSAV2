"""Choose and compare classify providers per tenant (LG06 shadow / cascade).

``llm``     the LLM decides; Jev is not called.
``shadow``  the LLM decides; Jev runs alongside and both answers are logged.
``cascade`` Jev decides; on a Jev error or low intent confidence the LLM decides.
``jev``     Jev decides; a Jev error fails the node (no fallback).
"""

from __future__ import annotations

import asyncio
import time
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from csa.contracts.envelope import Envelope
from csa.contracts.state import Context, Signals

DecideMode = Literal["llm", "shadow", "cascade", "jev"]
COMPARED = ("intent", "language", "wants_human", "injection", "urgent")


class Classifier(Protocol):
    @property
    def version(self) -> str: ...

    async def classify(self, envelope: Envelope, ctx: Context) -> Signals: ...


class DecisionRecord(BaseModel):
    """One ``decision_log`` row: typed answers only, never customer text."""

    mode: Literal["shadow", "cascade", "jev"]
    provider: Literal["jev", "llm"]
    model_version: str
    latency_ms: int = Field(ge=0)
    answers: dict[str, dict[str, object] | None]
    final: dict[str, object]
    disagree: list[str] = Field(default_factory=list)
    fallback_reason: str | None = None


def summarize(signals: Signals, latency_ms: int) -> dict[str, object]:
    answers = signals.answers
    return {
        "intent": signals.intent.value if signals.intent else None,
        **{key: answers.get(key) for key in COMPARED[1:]},
        "sentiment": answers.get("sentiment"),
        "intent_confidence": answers.get("intent_confidence"),
        "capabilities": sorted(signals.capability_scores),
        "version": signals.version,
        "latency_ms": latency_ms,
    }


def _disagree(a: dict[str, object] | None, b: dict[str, object] | None) -> list[str]:
    if a is None or b is None:
        return []
    return [key for key in COMPARED if a.get(key) != b.get(key)]


async def _timed(provider: Classifier, envelope: Envelope, ctx: Context) -> tuple[Signals, int]:
    started = time.monotonic()
    signals = await provider.classify(envelope, ctx)
    return signals, round((time.monotonic() - started) * 1000)


def _code(exc: BaseException) -> str:
    return str(getattr(exc, "code", type(exc).__name__))[:64]


async def classify_with(
    mode: DecideMode,
    envelope: Envelope,
    ctx: Context,
    *,
    llm: Classifier,
    jev: Classifier | None,
    min_confidence: float,
) -> tuple[Signals, DecisionRecord | None]:
    if mode == "llm":
        return await llm.classify(envelope, ctx), None
    if jev is None:
        raise RuntimeError("jev_not_configured")

    if mode == "shadow":
        llm_out, jev_out = await asyncio.gather(
            _timed(llm, envelope, ctx), _timed(jev, envelope, ctx), return_exceptions=True
        )
        if isinstance(llm_out, BaseException):
            raise llm_out
        signals, latency = llm_out
        llm_sum = summarize(signals, latency)
        jev_sum = None if isinstance(jev_out, BaseException) else summarize(*jev_out)
        return signals, DecisionRecord(
            mode="shadow",
            provider="llm",
            model_version=signals.version,
            latency_ms=latency,
            answers={"llm": llm_sum, "jev": jev_sum},
            final=llm_sum,
            disagree=_disagree(llm_sum, jev_sum),
            fallback_reason=_code(jev_out) if isinstance(jev_out, BaseException) else None,
        )

    try:
        signals, latency = await _timed(jev, envelope, ctx)
    except Exception as exc:
        if mode == "jev":
            raise
        reason = _code(exc)
        jev_sum = None
    else:
        jev_sum = summarize(signals, latency)
        confidence = signals.answers.get("intent_confidence")
        if mode == "jev" or (isinstance(confidence, float) and confidence >= min_confidence):
            return signals, DecisionRecord(
                mode=mode,
                provider="jev",
                model_version=signals.version,
                latency_ms=latency,
                answers={"jev": jev_sum, "llm": None},
                final=jev_sum,
            )
        reason = "low_intent_confidence"
    signals, latency = await _timed(llm, envelope, ctx)
    llm_sum = summarize(signals, latency)
    return signals, DecisionRecord(
        mode="cascade",
        provider="llm",
        model_version=signals.version,
        latency_ms=latency,
        answers={"jev": jev_sum, "llm": llm_sum},
        final=llm_sum,
        disagree=_disagree(jev_sum, llm_sum),
        fallback_reason=reason,
    )
