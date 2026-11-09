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
malformed answer) falls back; it never reaches the caller as an error. The fallback's answers are
checked the same way: only the asked questions, a bool for ``noul`` and an option key for
``choice``; a question the fallback must answer and does not raises ``FallbackAnswerError``.
The inbound injection decider audits every request, so each disagreement on that question (the
only safety decision) is logged.
"""

from __future__ import annotations

import asyncio
import math
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
INJECTION_AUDIT_SAMPLE = 1.0
Answer = bool | str


class FallbackAnswerError(ValueError):
    """The fallback gave no valid answer to a question only it may answer (the caller's bug)."""

    code = "fallback_invalid_answer"


class Decided(BaseModel):
    """What the caller acts on. ``provider`` is ``jev`` only when Jev answered every question."""

    answers: dict[str, Answer]
    provider: Literal["jev", "llm"]
    fallback_reason: str | None = Field(default=None, max_length=64)


class _Judged(BaseModel):
    answer: Answer
    confidence: float


def _probability(value: object) -> float:
    """A finite number in [0, 1]; a bool, NaN or anything out of range is a malformed answer."""
    if isinstance(value, bool):
        raise ValueError("a bool is not a probability")
    p = float(value)  # type: ignore[arg-type]
    if not (math.isfinite(p) and 0.0 <= p <= 1.0):
        raise ValueError("probability out of range")
    return p


def _is_option(question: Mapping[str, Any], choice: object) -> bool:
    criteria = question.get("criteria")
    return isinstance(choice, str) and isinstance(criteria, Mapping) and choice in criteria


def _judge(question: Mapping[str, Any], raw: Mapping[str, Any]) -> _Judged:
    """One Jev answer as an answer and a confidence: noul -> p >= 0.5 and max(p, 1 - p)."""
    kind = question.get("type")
    if kind == "noul":
        p = _probability(raw["noul"])
        return _Judged(answer=p >= 0.5, confidence=max(p, 1.0 - p))
    if kind == "choice":
        choice = str(raw["choice"])
        if not _is_option(question, choice):
            raise ValueError("choice is not an option")
        return _Judged(answer=choice, confidence=_probability(raw.get("confidence", 0.0)))
    raise ValueError(f"unsupported question type {kind!r}")


def _valid_answer(question: Mapping[str, Any], answer: object) -> bool:
    kind = question.get("type")
    if kind == "noul":
        return isinstance(answer, bool)
    if kind == "choice":
        return _is_option(question, answer)
    return False


def _fallback_answers(
    raw: Mapping[str, object],
    questions: Mapping[str, dict[str, Any]],
    needed: Sequence[str],
) -> dict[str, Answer]:
    """The fallback's valid answers to the asked questions; every ``needed`` one must be there.

    Extra keys and wrong-typed or free-text values are dropped, so they never reach the caller's
    answers or ``decision_log``.
    """
    answers: dict[str, Answer] = {}
    for q, question in questions.items():
        answer = raw.get(q)
        if _valid_answer(question, answer):
            answers[q] = answer  # type: ignore[assignment]
    missing = [q for q in needed if q not in answers]
    if missing:
        raise FallbackAnswerError(f"no valid fallback answer for {sorted(missing)}")
    return answers


def _redact_state(value: object, names: Sequence[str]) -> object:
    if isinstance(value, str):
        return redact_for_jev(value, names=names)
    if isinstance(value, Mapping):
        # String keys too: the callers' keys are fixed identifiers, so for them this is a no-op.
        redacted: dict[object, object] = {}
        for key, item in value.items():
            safe_key = redact_for_jev(key, names=names) if isinstance(key, str) else key
            redacted[safe_key] = _redact_state(item, names)
        return redacted
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
        self.audit_sample = audit_sample
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
            answers = _fallback_answers(await fallback(), questions, list(questions))
            return Decided(answers=answers, provider="llm")

        sent = _redact_state(state, names)
        if self.mode == "shadow":
            jev_out, fb_out = await asyncio.gather(
                self._ask(sent, questions), fallback(), return_exceptions=True
            )
            if isinstance(fb_out, BaseException):
                raise fb_out
            jev, error, latency = _split(jev_out)
            answers = _fallback_answers(fb_out, questions, list(questions))
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
        audited = jev is not None and not weak and self._rng.random() < self.audit_sample
        fb: dict[str, Answer] | None = None
        if weak or audited:
            try:
                fb = _fallback_answers(await fallback(), questions, weak)
            except Exception:
                if weak:
                    raise
                logger.warning("assistant_audit_fallback_failed", area=self.area)
        final: dict[str, Answer] = {}
        for q in questions:
            if q in weak:
                assert fb is not None  # a weak question either has a fallback answer or raised
                final[q] = fb[q]
            else:
                assert jev is not None  # without Jev's answers every question is weak
                final[q] = jev[q].answer
        reason = error or (LOW_CONFIDENCE if weak else None)
        await self._log(client_id, trace_id, latency, jev, fb, final, questions, reason)
        return Decided(answers=final, provider="llm" if reason else "jev", fallback_reason=reason)

    async def _ask(
        self, state: object, questions: Mapping[str, dict[str, Any]]
    ) -> tuple[dict[str, _Judged], int]:
        assert self._client is not None
        started = time.monotonic()
        try:
            # httpx's timeout is per phase; this bounds the whole request. A cancel still
            # propagates: asyncio.timeout turns only its own expiry into TimeoutError.
            async with asyncio.timeout(CLASSIFY_TIMEOUT_S):
                response: JevResponse = await self._client.ask(
                    state, questions, timeout=CLASSIFY_TIMEOUT_S
                )
        except TimeoutError as exc:
            raise JevError("jev_timeout") from exc
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

    def build(area: str, mode: AreaMode, audit_sample: float) -> Decider | None:
        if resolve_mode(mode, has_client=client is not None) == "llm":
            return None
        return Decider(area, mode, client, pool, floors=floors, audit_sample=audit_sample)

    sample = settings.ASSISTANT_DECIDER_AUDIT_SAMPLE

    return Deciders(
        email=build("email", settings.ASSISTANT_DECIDER_EMAIL, sample),
        routing=build("routing", settings.ASSISTANT_DECIDER_ROUTING, sample),
        # The injection check is the only safety decision: the fallback always runs beside Jev
        # so every disagreement on it is logged.
        inbound=build(
            "inbound_injection",
            settings.ASSISTANT_DECIDER_INBOUND_INJECTION,
            INJECTION_AUDIT_SAMPLE,
        ),
    )
