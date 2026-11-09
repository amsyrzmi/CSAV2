"""The staff assistant's decider layer: Jev for quick typed decisions, a fallback for the rest.

A ``Decider`` asks Jev a set of typed questions and answers each one itself when Jev is confident
enough (the floor per question type), and otherwise takes the LLM's or the server rule's answer
through the ``fallback`` the caller passes. Modes, per area:

``llm``      the fallback decides; Jev is not called and nothing is logged.
``shadow``   the fallback decides; Jev runs beside it and both answers are logged.
``cascade``  Jev decides each question at or above its floor, the fallback the others; a sampled
             share of requests also runs the fallback beside a confident Jev to log drift.

Jev never writes text, never extracts values and never decides a send: a decider answers typed
questions only, and its caller owns what the answer may do. Only redacted text reaches Jev and
``decision_log`` rows hold typed answers, never message text. Any Jev failure (error, timeout,
malformed answer) falls back; it never reaches the caller as an error.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

import structlog
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from csa.core import db
from csa.core.tenancy import tx
from csa.decide.jev import CLASSIFY_TIMEOUT_S, JevClient, JevError, JevResponse, redact_for_jev

logger = structlog.get_logger(__name__)

AreaMode = Literal["auto", "llm", "shadow", "cascade"]
LOW_CONFIDENCE = "low_confidence"
Answer = bool | str


class Decided(BaseModel):
    """What the caller acts on. ``provider`` is ``jev`` only when Jev answered every question."""

    answers: dict[str, Answer]
    provider: Literal["jev", "llm"]
    fallback_reason: str | None = Field(default=None, max_length=64)


class _Judged(BaseModel):
    answer: Answer
    confidence: float


def _judge(question: Mapping[str, Any], raw: Mapping[str, Any]) -> _Judged:
    """One Jev answer as an answer and a confidence: noul -> p >= 0.5 and max(p, 1 - p)."""
    kind = question.get("type")
    if kind == "noul":
        p = float(raw["noul"])
        if not 0.0 <= p <= 1.0:
            raise ValueError("noul out of range")
        return _Judged(answer=p >= 0.5, confidence=max(p, 1.0 - p))
    if kind == "choice":
        choice = str(raw["choice"])
        criteria = question.get("criteria")
        if isinstance(criteria, Mapping) and choice not in criteria:
            raise ValueError("choice is not an option")
        return _Judged(answer=choice, confidence=float(raw.get("confidence", 0.0)))
    raise ValueError(f"unsupported question type {kind!r}")


def _redact_state(value: object, names: Sequence[str]) -> object:
    if isinstance(value, str):
        return redact_for_jev(value, names=names)
    if isinstance(value, Mapping):
        return {key: _redact_state(item, names) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_redact_state(item, names) for item in value]
    return value


def _code(exc: BaseException) -> str:
    return str(getattr(exc, "code", type(exc).__name__))[:64]


def resolve_mode(mode: AreaMode, *, has_client: bool) -> Literal["llm", "shadow", "cascade"]:
    if mode == "auto":
        return "cascade" if has_client else "llm"
    return mode


class Decider:
    """One area's decider (``email``, ``routing``, ``inbound_injection``)."""

    def __init__(
        self,
        area: str,
        mode: str,
        client: JevClient | None,
        pool: Any,
        *,
        floors: Mapping[str, float],
        audit_sample: float,
        rng: random.Random | None = None,
    ) -> None:
        resolved = resolve_mode(mode, has_client=client is not None)  # type: ignore[arg-type]
        if resolved != "llm" and client is None:
            raise ValueError(f"decider mode {resolved} needs a Jev client")
        self.area = area
        self.mode = resolved
        self._client = client
        self._pool = pool
        self._floors = dict(floors)
        self._audit_sample = audit_sample
        self._rng = rng or random.Random()

    async def decide(
        self,
        state: Mapping[str, object],
        questions: Mapping[str, dict[str, Any]],
        *,
        fallback: Callable[[], Awaitable[Mapping[str, object]]],
        trace_id: UUID,
        client_id: UUID,
        names: Sequence[str] = (),
    ) -> Decided:
        if self.mode == "llm" or self._client is None:
            return Decided(answers=_typed(await fallback()), provider="llm")

        sent = _redact_state(state, names)
        if self.mode == "shadow":
            jev_out, fb_out = await asyncio.gather(
                self._ask(sent, questions), fallback(), return_exceptions=True
            )
            if isinstance(fb_out, BaseException):
                raise fb_out
            jev, error, latency = _split(jev_out)
            answers = _typed(fb_out)
            await self._log(client_id, trace_id, latency, jev, answers, answers, questions, error)
            return Decided(answers=answers, provider="llm", fallback_reason=error)

        try:
            jev, latency = await self._ask(sent, questions)
            error = None
        except Exception as exc:
            jev, latency, error = None, 0, _code(exc)

        weak = [
            q
            for q in questions
            if jev is None or jev[q].confidence < self._floors[str(questions[q]["type"])]
        ]
        audited = jev is not None and not weak and self._rng.random() < self._audit_sample
        fb: dict[str, Answer] | None = None
        if weak or audited:
            try:
                fb = _typed(await fallback())
            except Exception:
                if weak:
                    raise
                logger.warning("assistant_audit_fallback_failed", area=self.area)
        final: dict[str, Answer] = {}
        for q in questions:
            if q not in weak and jev is not None:
                final[q] = jev[q].answer
            elif fb is not None and q in fb:
                final[q] = fb[q]
            elif jev is not None:
                final[q] = jev[q].answer
        reason = error or (LOW_CONFIDENCE if weak else None)
        await self._log(client_id, trace_id, latency, jev, fb, final, questions, reason)
        return Decided(answers=final, provider="llm" if reason else "jev", fallback_reason=reason)

    async def _ask(
        self, state: object, questions: Mapping[str, dict[str, Any]]
    ) -> tuple[dict[str, _Judged], int]:
        assert self._client is not None
        started = time.monotonic()
        response: JevResponse = await self._client.ask(state, questions, timeout=CLASSIFY_TIMEOUT_S)
        latency = round((time.monotonic() - started) * 1000)
        try:
            judged = {q: _judge(questions[q], response.answers[q]) for q in questions}
        except (KeyError, TypeError, ValueError) as exc:
            raise JevError("jev_invalid_answer") from exc
        return judged, latency

    async def _log(
        self,
        client_id: UUID,
        trace_id: UUID,
        latency: int,
        jev: dict[str, _Judged] | None,
        other: Mapping[str, Answer] | None,
        final: Mapping[str, Answer],
        questions: Mapping[str, dict[str, Any]],
        reason: str | None,
    ) -> None:
        """One ``decision_log`` row: question names, typed answers, confidences. No text."""
        assert self._client is not None
        disagree = (
            [q for q in questions if q in other and other[q] != jev[q].answer]
            if jev is not None and other is not None
            else []
        )
        provider = "jev" if reason is None and self.mode == "cascade" else "llm"
        answers = {
            "jev": (
                {
                    q: {"answer": j.answer, "confidence": round(j.confidence, 3)}
                    for q, j in jev.items()
                }
                if jev is not None
                else None
            ),
            "llm": dict(other) if other is not None else None,
        }
        try:
            async with tx(self._pool, str(client_id)) as conn:
                await db.q.execute(
                    "decide/insert_decision.sql",
                    (
                        client_id,
                        trace_id,
                        f"assistant_{self.area}",
                        self.mode,
                        provider,
                        f"jev:{self._client.model}",
                        latency,
                        Jsonb(answers),
                        Jsonb(dict(final)),
                        disagree,
                        reason,
                    ),
                    conn=conn,
                )
        except Exception as exc:  # a lost log row must never fail the decision it describes
            logger.warning("assistant_decision_log_failed", area=self.area, error=_code(exc))
            return
        if disagree or reason:
            logger.info(
                "assistant_decision_compare",
                area=self.area,
                trace_id=str(trace_id),
                mode=self.mode,
                disagree=disagree,
                fallback=reason,
            )


def _typed(answers: Mapping[str, object]) -> dict[str, Answer]:
    return {q: a for q, a in answers.items() if isinstance(a, bool | str)}


def _split(out: object) -> tuple[dict[str, _Judged] | None, str | None, int]:
    if isinstance(out, BaseException):
        return None, _code(out), 0
    jev, latency = out  # type: ignore[misc]
    return jev, None, latency


@dataclass(frozen=True)
class Deciders:
    email: Decider | None
    routing: Decider | None
    inbound: Decider | None


def build_deciders(settings: Any, client: JevClient | None, pool: Any) -> Deciders:
    """One decider per area; None where the area runs on the LLM or rule alone."""
    floors = {
        "noul": settings.ASSISTANT_JEV_FLOOR_NOUL,
        "choice": settings.ASSISTANT_JEV_FLOOR_CHOICE,
    }

    def build(area: str, mode: AreaMode) -> Decider | None:
        if resolve_mode(mode, has_client=client is not None) == "llm":
            return None
        return Decider(
            area,
            mode,
            client,
            pool,
            floors=floors,
            audit_sample=settings.ASSISTANT_DECIDER_AUDIT_SAMPLE,
        )

    return Deciders(
        email=build("email", settings.ASSISTANT_DECIDER_EMAIL),
        routing=build("routing", settings.ASSISTANT_DECIDER_ROUTING),
        inbound=build("inbound_injection", settings.ASSISTANT_DECIDER_INBOUND_INJECTION),
    )
