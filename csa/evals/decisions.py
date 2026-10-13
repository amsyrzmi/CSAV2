"""Decision eval: score a classifier (LLM or Jev) on labelled synthetic messages (LG08).

Live mode only: it calls real providers, so it never runs in CI. The scoring and
report functions are pure and covered by offline tests.

    ENV=dev uv run python -m csa.evals.decisions --provider both
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

import httpx
from pydantic import BaseModel, Field, field_validator

from csa.contracts.envelope import Channel, Contact, ConversationRef, Envelope, Message, Meta
from csa.contracts.state import Context, Signals

CASES_PATH = Path(__file__).parent / "data" / "decision_cases.json"
REPORT_PATH = Path("docs/evals/decision_report.md")
FIELDS = ("intent", "language", "wants_human", "injection", "urgent")
_EVAL_ID = UUID("00000000-0000-4000-8000-0000000000e1")


class Expect(BaseModel):
    intent: list[str] = Field(min_length=1)
    language: list[str] = Field(min_length=1)
    wants_human: bool = False
    injection: bool = False
    urgent: bool = False

    @field_validator("intent", "language", mode="before")
    @classmethod
    def _listify(cls, value: object) -> object:
        return [value] if isinstance(value, str) else value


class DecisionCase(BaseModel):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    expect: Expect


class Prediction(BaseModel):
    case_id: str
    provider: str
    intent: str | None = None
    language: str | None = None
    wants_human: bool | None = None
    injection: bool | None = None
    urgent: bool | None = None
    latency_ms: float
    error: str | None = None


class Classifier(Protocol):
    async def classify(self, envelope: Envelope, ctx: Context) -> Signals: ...


def load_cases(path: Path = CASES_PATH) -> list[DecisionCase]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = [DecisionCase.model_validate(item) for item in data["cases"]]
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate decision case id")
    return cases


def envelope_for(text: str) -> Envelope:
    return Envelope(
        trace_id=_EVAL_ID,
        received_at=datetime.now(UTC),
        channel=Channel.TELEGRAM,
        idempotency_key="eval:decision",
        client_id=_EVAL_ID,
        contact=Contact(
            id=_EVAL_ID, external_id="eval", channel=Channel.TELEGRAM, display_name=None
        ),
        conversation=ConversationRef(
            id=_EVAL_ID, channel_thread="eval", turn_count=1, status="open"
        ),
        message=Message(body_raw=text, body_redacted=text, language="und"),
        meta=Meta(),
    )


def to_prediction(case_id: str, provider: str, signals: Signals, latency_ms: float) -> Prediction:
    answers = signals.answers

    def flag(name: str) -> bool | None:
        value = answers.get(name)
        return value if isinstance(value, bool) else None

    language = answers.get("language")
    return Prediction(
        case_id=case_id,
        provider=provider,
        intent=signals.intent.value if signals.intent else None,
        language=language if isinstance(language, str) else None,
        wants_human=flag("wants_human"),
        injection=flag("injection"),
        urgent=flag("urgent"),
        latency_ms=latency_ms,
    )


async def run_provider(
    name: str, provider: Classifier, cases: Sequence[DecisionCase], *, concurrency: int = 4
) -> list[Prediction]:
    gate = asyncio.Semaphore(concurrency)

    async def one(case: DecisionCase) -> Prediction:
        async with gate:
            started = time.monotonic()
            try:
                signals = await provider.classify(envelope_for(case.text), Context())
            except Exception as exc:
                return Prediction(
                    case_id=case.id,
                    provider=name,
                    latency_ms=(time.monotonic() - started) * 1000,
                    error=getattr(exc, "code", type(exc).__name__),
                )
            return to_prediction(case.id, name, signals, (time.monotonic() - started) * 1000)

    return list(await asyncio.gather(*(one(case) for case in cases)))


def correct(case: DecisionCase, prediction: Prediction, field: str) -> bool:
    if prediction.error is not None:
        return False
    expected = getattr(case.expect, field)
    actual = getattr(prediction, field)
    return actual in expected if isinstance(expected, list) else actual == expected


class ProviderScore(BaseModel):
    provider: str
    cases: int
    errors: int
    accuracy: dict[str, float]
    injection_recall: float
    human_recall: float
    p50_ms: float
    p95_ms: float
    misses: list[tuple[str, str, str]]


def _recall(cases: Sequence[DecisionCase], preds: dict[str, Prediction], field: str) -> float:
    positives = [c for c in cases if getattr(c.expect, field)]
    if not positives:
        return 1.0
    return sum(correct(c, preds[c.id], field) for c in positives) / len(positives)


def score(cases: Sequence[DecisionCase], predictions: Sequence[Prediction]) -> ProviderScore:
    if not predictions:
        raise ValueError("no predictions")
    by_id = {p.case_id: p for p in predictions}
    missing = {c.id for c in cases} - set(by_id)
    if missing:
        raise ValueError(f"missing predictions: {sorted(missing)}")
    latencies = sorted(p.latency_ms for p in predictions if p.error is None) or [0.0]
    misses = [
        (case.id, field, by_id[case.id].error or str(getattr(by_id[case.id], field)))
        for case in cases
        for field in FIELDS
        if not correct(case, by_id[case.id], field)
    ]
    return ProviderScore(
        provider=predictions[0].provider,
        cases=len(cases),
        errors=sum(1 for p in predictions if p.error is not None),
        accuracy={
            field: round(sum(correct(c, by_id[c.id], field) for c in cases) / len(cases), 3)
            for field in FIELDS
        },
        injection_recall=round(_recall(cases, by_id, "injection"), 3),
        human_recall=round(_recall(cases, by_id, "wants_human"), 3),
        p50_ms=round(statistics.median(latencies), 1),
        p95_ms=round(latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))], 1),
        misses=misses,
    )


def agreement(a: Sequence[Prediction], b: Sequence[Prediction]) -> dict[str, float]:
    other = {p.case_id: p for p in b}
    pairs = [(p, other[p.case_id]) for p in a if p.case_id in other]
    if not pairs:
        return {}
    return {
        field: round(
            sum(
                x.error is None and y.error is None and getattr(x, field) == getattr(y, field)
                for x, y in pairs
            )
            / len(pairs),
            3,
        )
        for field in FIELDS
    }


def render_report(
    scores: Sequence[ProviderScore],
    versions: dict[str, str],
    agree: dict[str, float] | None,
    *,
    when: datetime,
) -> str:
    lines = [
        "# Decision eval report",
        "",
        f"Generated {when:%Y-%m-%d %H:%M} UTC by `python -m csa.evals.decisions` on "
        f"{scores[0].cases if scores else 0} synthetic labelled messages "
        "(`csa/evals/data/decision_cases.json`). Live providers; not run in CI.",
        "",
        "| Provider | Version | Errors | " + " | ".join(FIELDS) + " | injection recall "
        "| human recall | p50 ms | p95 ms |",
        "|---|---|---|" + "---|" * len(FIELDS) + "---|---|---|---|",
    ]
    for s in scores:
        lines.append(
            f"| {s.provider} | {versions.get(s.provider, '?')} | {s.errors} | "
            + " | ".join(f"{s.accuracy[f]:.0%}" for f in FIELDS)
            + f" | {s.injection_recall:.0%} | {s.human_recall:.0%} | {s.p50_ms} | {s.p95_ms} |"
        )
    if agree:
        lines += [
            "",
            "Agreement between providers: "
            + ", ".join(f"{f} {v:.0%}" for f, v in agree.items())
            + ".",
        ]
    for s in scores:
        lines += ["", f"## Misses: {s.provider}", ""]
        lines += [f"- `{cid}` {field}: got `{got}`" for cid, field, got in s.misses] or ["- none"]
    return "\n".join(lines) + "\n"


async def _main(provider: str, out: Path, concurrency: int) -> None:
    from csa.core.settings import get_settings
    from csa.decide.groq import GroqClient
    from csa.decide.jev import JevClient, JevProvider
    from csa.turn.deps import CAPABILITIES, build_llm_provider

    settings = get_settings()
    cases = load_cases()
    runners: list[tuple[str, Classifier, Callable[[], str]]] = []
    async with httpx.AsyncClient() as http:
        if provider in {"llm", "both"}:
            llm = build_llm_provider(
                GroqClient(settings.GROQ_API_KEY.get_secret_value(), settings.GROQ_MODEL, http)
            )
            runners.append(("llm", llm, lambda: llm.version))
        if provider in {"jev", "both"}:
            if settings.TYPESAFE_API_KEY is None:
                raise SystemExit("TYPESAFE_API_KEY is not set")
            jev = JevProvider(
                JevClient(settings.TYPESAFE_API_KEY.get_secret_value(), http),
                capabilities=CAPABILITIES,
            )
            runners.append(("jev", jev, lambda: jev.version))
        results: dict[str, list[Prediction]] = {}
        for name, classifier, _ in runners:
            results[name] = await run_provider(name, classifier, cases, concurrency=concurrency)
    scores = [score(cases, preds) for preds in results.values()]
    versions = {name: version() for name, _, version in runners}
    agree = agreement(results["llm"], results["jev"]) if len(results) == 2 else None
    report = render_report(scores, versions, agree, when=datetime.now(UTC))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["llm", "jev", "both"], default="both")
    parser.add_argument("--out", type=Path, default=REPORT_PATH)
    parser.add_argument("--concurrency", type=int, default=2, choices=range(1, 9))
    args = parser.parse_args(argv)
    asyncio.run(_main(args.provider, args.out, args.concurrency))


if __name__ == "__main__":
    main()
