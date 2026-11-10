"""Evaluation gate for the staff assistant's Jev deciders (the owner runs it with their own key).

    uv run python -m scripts.assistant_jev_eval

It sends each labelled synthetic message (docs/superpowers/evals/assistant_jev_labels.json, English
and Malay) to Jev with the same questions, wording and redaction the live deciders use, then prints,
per area and question, how often Jev agrees with the label and whether that meets the target. What
counts is the agreement on the answers the cascade would actually use: those at or above the
confidence floor. Answers below the floor fall back to the LLM or rule in production, so they are
reported as coverage, not judged. An error or timeout is the same: a fallback, counted.

The key is typed at a hidden prompt (nothing echoed, nothing in shell history); a key already in the
shell's TYPESAFE_API_KEY is used instead. Never from a repo file. With no key and no terminal the run
stops (exit 2) rather than read one from a pipe. The output is content-free: area, question, counts, percentages and the ids of mislabelled cases (synthetic ids
such as ``routing-ms-04``), never message text. Exit codes: 0 every question meets its target,
1 at least one is below it, 2 the run could not start (no key, bad labels).

The customer classifier has its own evaluation, ``csa.evals.decisions`` (docs/RUNBOOK.md, Jev).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
from csa.core.settings import Settings
from csa.decide.deciders import _judge, _redact_state
from csa.decide.jev import CLASSIFY_TIMEOUT_S, INJECTION_QUESTION, JevClient, JevError, JevResponse
from csa.email.inbound import QUESTION as INJECTION_KEY
from csa.workspace.email_batch import email_questions
from csa.workspace.routing import routing_questions
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

LABELS_PATH = (
    Path(__file__).resolve().parents[1] / "docs/superpowers/evals/assistant_jev_labels.json"
)
Area = Literal["email", "routing", "inbound_injection"]
AREAS: tuple[Area, ...] = ("email", "routing", "inbound_injection")

# Agreement on confident answers each question must reach. The injection question is the only
# safety decision (a yes blocks a reply), so its bar is higher.
DEFAULT_TARGET = 0.90
TARGETS = {"injection": 0.95}
# Fewer confident answers than this is no evidence: the question fails rather than passes empty.
MIN_CONFIDENT = 5
ERROR_SAMPLE = 5


def area_questions(area: Area) -> dict[str, dict[str, Any]]:
    """The live deciders' questions, so the evaluation judges the wording that ships."""
    if area == "email":
        return email_questions()
    if area == "routing":
        return routing_questions()
    return {INJECTION_KEY: INJECTION_QUESTION}


class LabelledCase(BaseModel):
    """One synthetic message and what Jev should answer to each of its area's questions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    area: Area
    language: Literal["en", "ms"]
    state: dict[str, str]
    expect: dict[str, bool | str]
    # The csa/workspace/smoke_cases.json id the message comes from, for cases derived from the
    # smoke set; None for the cases written for the evaluation.
    source: str | None = None

    @model_validator(mode="after")
    def _expect_matches_the_questions(self) -> LabelledCase:
        questions = area_questions(self.area)
        if set(self.expect) != set(questions):
            raise ValueError(f"{self.id}: expect must label exactly {sorted(questions)}")
        for name, label in self.expect.items():
            question = questions[name]
            if question["type"] == "noul" and not isinstance(label, bool):
                raise ValueError(f"{self.id}: {name} needs true or false")
            if question["type"] == "choice" and label not in question["criteria"]:
                raise ValueError(f"{self.id}: {name} is not one of its options")
        return self


class LabelFile(BaseModel):
    """The labels file: a list of cases with unique ids."""

    model_config = ConfigDict(extra="ignore")

    cases: list[LabelledCase] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> LabelFile:
        ids = [case.id for case in self.cases]
        if len(set(ids)) != len(ids):
            raise ValueError("case ids must be unique")
        return self


def load_cases(path: Path = LABELS_PATH) -> list[LabelledCase]:
    """Read and validate the labelled cases; a bad file raises ValueError."""
    return LabelFile.model_validate(json.loads(path.read_text(encoding="utf-8"))).cases


class Asker(Protocol):
    """What the evaluation needs from a client: ``JevClient`` or a fake."""

    async def ask(
        self, state: object, questions: Mapping[str, dict[str, Any]], *, timeout: float
    ) -> JevResponse: ...


@dataclass
class Tally:
    """Counts for one question (over all cases, or one language)."""

    total: int = 0
    errors: int = 0
    agree_all: int = 0
    confident: int = 0
    agree_confident: int = 0
    false_yes: int = 0  # confident yes where the label is no (noul only)
    false_no: int = 0  # confident no where the label is yes (noul only)
    wrong: list[str] = field(default_factory=list)  # ids of confident disagreements


@dataclass(frozen=True)
class QuestionResult:
    """One question's tallies, with the floor and target it is judged by."""

    area: Area
    question: str
    floor: float
    target: float
    overall: Tally
    by_language: Mapping[str, Tally]

    @property
    def coverage(self) -> float:
        """The share of answers at or above the floor (the rest fall back)."""
        return self.overall.confident / self.overall.total if self.overall.total else 0.0

    @property
    def agreement(self) -> float | None:
        """Agreement with the label on the answers at or above the floor; None if there are none."""
        tally = self.overall
        return tally.agree_confident / tally.confident if tally.confident else None

    @property
    def meets_target(self) -> bool:
        """Enough confident answers, and their agreement reaches the target."""
        agreement = self.agreement
        return (
            self.overall.confident >= MIN_CONFIDENT
            and agreement is not None
            and agreement >= self.target
        )


@dataclass(frozen=True)
class Report:
    """Every question's result, the Jev error codes seen, and the number of cases."""

    results: list[QuestionResult]
    errors: Counter[str]
    cases: int

    @property
    def passed(self) -> bool:
        """Every question meets its target."""
        return all(result.meets_target for result in self.results)


def _record(
    tally: Tally,
    *,
    case_id: str,
    answer: object,
    label: object,
    confidence: float | None,
    floor: float,
) -> None:
    tally.total += 1
    if confidence is None:
        tally.errors += 1
        return
    agrees = answer == label
    tally.agree_all += agrees
    if confidence < floor:
        return
    tally.confident += 1
    tally.agree_confident += agrees
    if not agrees:
        tally.wrong.append(case_id)
        if isinstance(label, bool):
            tally.false_yes += label is False
            tally.false_no += label is True


async def evaluate(
    client: Asker,
    cases: Sequence[LabelledCase],
    *,
    floors: Mapping[str, float],
    concurrency: int = 4,
) -> Report:
    """Ask Jev every case once (all of its area's questions in one request, as production does)."""
    semaphore = asyncio.Semaphore(concurrency)
    error_codes: Counter[str] = Counter()

    async def one(case: LabelledCase) -> dict[str, tuple[object, float] | None]:
        questions = area_questions(case.area)
        async with semaphore:
            try:
                async with asyncio.timeout(CLASSIFY_TIMEOUT_S):
                    response = await client.ask(
                        _redact_state(case.state, ()), questions, timeout=CLASSIFY_TIMEOUT_S
                    )
            except TimeoutError:
                error_codes["jev_timeout"] += 1
                return {q: None for q in questions}
            except JevError as exc:
                error_codes[exc.code] += 1
                return {q: None for q in questions}
            except Exception as exc:  # a failed call is a fallback, never a crash of the run
                error_codes[type(exc).__name__] += 1
                return {q: None for q in questions}
        out: dict[str, tuple[object, float] | None] = {}
        for name, question in questions.items():
            try:
                judged = _judge(question, response.answers[name])
            except (KeyError, TypeError, ValueError):
                error_codes["jev_invalid_answer"] += 1
                out[name] = None
            else:
                out[name] = (judged.answer, judged.confidence)
        return out

    answered = await asyncio.gather(*(one(case) for case in cases))
    tallies: dict[tuple[Area, str], tuple[Tally, dict[str, Tally]]] = {}
    for case, answers in zip(cases, answered, strict=True):
        for name, question in area_questions(case.area).items():
            overall, languages = tallies.setdefault((case.area, name), (Tally(), {}))
            result = answers[name]
            answer, confidence = (None, None) if result is None else result
            floor = floors[str(question["type"])]
            for tally in (overall, languages.setdefault(case.language, Tally())):
                _record(
                    tally,
                    case_id=case.id,
                    answer=answer,
                    label=case.expect[name],
                    confidence=confidence,
                    floor=floor,
                )
    results = [
        QuestionResult(
            area=area,
            question=name,
            floor=floors[str(area_questions(area)[name]["type"])],
            target=TARGETS.get(name, DEFAULT_TARGET),
            overall=overall,
            by_language=languages,
        )
        for area in AREAS
        for (a, name), (overall, languages) in tallies.items()
        if a == area
    ]
    return Report(results=results, errors=error_codes, cases=len(cases))


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def render(report: Report) -> str:
    """Counts and percentages only: no message text."""
    lines = [
        f"Jev evaluation: {report.cases} labelled messages "
        f"(timeout {CLASSIFY_TIMEOUT_S:g}s, floors as shown)",
        "",
    ]
    for result in report.results:
        tally = result.overall
        verdict = "PASS" if result.meets_target else "BELOW TARGET"
        lines.append(
            f"[{result.area}] {result.question}: {verdict}  "
            f"agreement {_pct(result.agreement)} on {tally.confident} confident answers "
            f"(target {_pct(result.target)}, floor {result.floor:g}); "
            f"coverage {_pct(result.coverage)} of {tally.total}; errors {tally.errors}"
        )
        if tally.confident < MIN_CONFIDENT:
            lines.append(f"    fewer than {MIN_CONFIDENT} confident answers: not enough evidence")
        if tally.false_yes or tally.false_no:
            lines.append(
                f"    confident yes where label is no: {tally.false_yes}; "
                f"confident no where label is yes: {tally.false_no}"
            )
        for language, part in sorted(result.by_language.items()):
            agreement = part.agree_confident / part.confident if part.confident else None
            lines.append(
                f"    {language}: agreement {_pct(agreement)} on {part.confident} confident "
                f"of {part.total}"
            )
        if tally.wrong:
            shown = ", ".join(tally.wrong[:ERROR_SAMPLE])
            more = len(tally.wrong) - ERROR_SAMPLE
            lines.append(f"    mislabelled: {shown}" + (f" (+{more} more)" if more > 0 else ""))
    if report.errors:
        codes = ", ".join(f"{code} x{count}" for code, count in sorted(report.errors.items()))
        lines += ["", f"Jev errors (each fell back): {codes}"]
    lines += [
        "",
        "RESULT: every question meets its target"
        if report.passed
        else "RESULT: below target. Run again with a higher --floor-noul or --floor-choice, "
        "or keep that area on llm (docs/RUNBOOK.md, Jev).",
    ]
    return "\n".join(lines)


def _api_key() -> str | None:
    """The shell's key, else one typed at a hidden prompt; None when there is no terminal.

    An empty string means the prompt was left empty or abandoned. The key is never echoed.
    """
    from_shell = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if from_shell:
        return from_shell
    if not sys.stdin.isatty():
        return None
    try:
        return getpass.getpass("Jev key, TYPESAFE_API_KEY (hidden): ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def _setup_error(text: str) -> int:
    print(f"setup error: {text}", file=sys.stderr)
    return 2


async def _run(args: argparse.Namespace, api_key: str, cases: list[LabelledCase]) -> Report:
    async with httpx.AsyncClient() as http:
        client = JevClient(api_key, http)
        return await evaluate(
            client,
            cases,
            floors={"noul": args.floor_noul, "choice": args.floor_choice},
            concurrency=args.concurrency,
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the evaluation; the exit code is 0 pass, 1 below target, 2 could not start."""
    fields = Settings.model_fields
    parser = argparse.ArgumentParser(prog="python -m scripts.assistant_jev_eval")
    parser.add_argument("--labels", type=Path, default=LABELS_PATH)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--floor-noul", type=float, default=fields["ASSISTANT_JEV_FLOOR_NOUL"].default
    )
    parser.add_argument(
        "--floor-choice", type=float, default=fields["ASSISTANT_JEV_FLOOR_CHOICE"].default
    )
    args = parser.parse_args(argv)
    api_key = _api_key()
    if api_key is None:
        return _setup_error(
            "TYPESAFE_API_KEY is not set in this shell and there is no terminal to prompt on"
        )
    if not api_key:
        return _setup_error("no key entered at the prompt")
    try:
        cases = load_cases(args.labels)
    except (OSError, ValueError, ValidationError) as exc:
        return _setup_error(f"labels are unusable ({type(exc).__name__})")
    report = asyncio.run(_run(args, api_key, cases))
    print(render(report))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
