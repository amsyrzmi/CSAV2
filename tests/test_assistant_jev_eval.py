"""The Jev evaluation gate: labels, agreement on confident answers, content-free report."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from csa.decide.jev import JevError, JevResponse
from pydantic import ValidationError
from scripts import assistant_jev_eval as ev

FLOORS = {"noul": 0.7, "choice": 0.7}
CASES = ev.load_cases()


def _answer(question: Mapping[str, Any], label: object, *, confidence: float) -> dict[str, Any]:
    if question["type"] == "noul":
        return {"noul": confidence if label else 1 - confidence}
    return {"choice": label, "confidence": confidence}


class OracleClient:
    """Answers each case as its label says, at the confidence the test sets per question."""

    def __init__(
        self,
        cases: list[ev.LabelledCase],
        *,
        confidence: float = 0.95,
        flip: Mapping[str, float] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._by_message = {case.state["message"]: case for case in cases}
        self._confidence = confidence
        # question -> confidence at which this client answers WRONG (inverted label).
        self._flip = dict(flip or {})
        self._error = error
        self.sent: list[object] = []

    async def ask(
        self, state: object, questions: Mapping[str, dict[str, Any]], *, timeout: float
    ) -> JevResponse:
        self.sent.append(state)
        if self._error is not None:
            raise self._error
        case = self._by_message[state["message"]]  # type: ignore[index]
        answers: dict[str, dict[str, Any]] = {}
        for name, question in questions.items():
            label = case.expect[name]
            if name in self._flip:
                label = (not label) if isinstance(label, bool) else _other_mode(label)
                answers[name] = _answer(question, label, confidence=self._flip[name])
            else:
                answers[name] = _answer(question, label, confidence=self._confidence)
        return JevResponse(model="fake", answers=answers)


def _other_mode(mode: object) -> str:
    return "personalised" if mode == "shared" else "shared"


def _result(report: ev.Report, area: str, question: str) -> ev.QuestionResult:
    return next(r for r in report.results if r.area == area and r.question == question)


# ------------------------------------------------------------------------------- labels


def test_the_labels_cover_every_area_and_question_in_english_and_malay() -> None:
    for area in ev.AREAS:
        in_area = [case for case in CASES if case.area == area]
        assert {case.language for case in in_area} == {"en", "ms"}
        for question, spec in ev.area_questions(area).items():
            labels = Counter(case.expect[question] for case in in_area)
            expected = {True, False} if spec["type"] == "noul" else set(spec["criteria"])
            assert set(labels) == expected, (area, question)
            # Enough of each answer for the agreement to mean something.
            assert min(labels.values()) >= ev.MIN_CONFIDENT, (area, question, labels)


def test_the_evaluation_asks_the_questions_the_live_deciders_ask() -> None:
    from csa.decide.jev import INJECTION_QUESTION
    from csa.workspace.email_batch import email_questions
    from csa.workspace.routing import routing_questions

    assert ev.area_questions("email") == email_questions()
    assert ev.area_questions("routing") == routing_questions()
    assert ev.area_questions("inbound_injection") == {"injection": INJECTION_QUESTION}


def test_the_label_file_holds_no_real_addresses() -> None:
    text = ev.LABELS_PATH.read_text(encoding="utf-8")
    assert "@" not in text
    assert "http" not in text


def _case(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "x-1",
        "area": "inbound_injection",
        "language": "en",
        "state": {"message": "hello"},
        "expect": {"injection": False},
    }
    return {**base, **overrides}


@pytest.mark.parametrize(
    "bad",
    [
        {"expect": {}},  # unlabelled question
        {"expect": {"injection": False, "extra": True}},  # a question that is not asked
        {"expect": {"injection": "no"}},  # noul needs a bool
        {"area": "email", "expect": {"mode": "both"}},  # not an option (and others missing)
        {"language": "fr"},
        {"id": ""},
    ],
)
def test_bad_labels_are_refused(bad: dict[str, object], tmp_path: Path) -> None:
    path = tmp_path / "labels.json"
    path.write_text(json.dumps({"cases": [_case(**bad)]}), encoding="utf-8")
    with pytest.raises(ValidationError):
        ev.load_cases(path)


def test_duplicate_ids_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "labels.json"
    path.write_text(json.dumps({"cases": [_case(), _case()]}), encoding="utf-8")
    with pytest.raises(ValidationError, match="unique"):
        ev.load_cases(path)


# ---------------------------------------------------------------------------- agreement


async def test_a_jev_that_matches_every_label_meets_every_target() -> None:
    client = OracleClient(CASES)
    report = await ev.evaluate(client, CASES, floors=FLOORS)
    assert report.passed
    assert report.cases == len(CASES) == len(client.sent)
    assert not report.errors
    questions = {(r.area, r.question) for r in report.results}
    assert questions == {
        (area, question) for area in ev.AREAS for question in ev.area_questions(area)
    }
    for result in report.results:
        assert result.agreement == 1.0
        assert result.coverage == 1.0
        assert set(result.by_language) == {"en", "ms"}


async def test_only_confident_answers_are_judged_and_the_rest_count_as_coverage() -> None:
    # Wrong on every routing:manager case, but below the 0.7 floor: the cascade would not use it.
    client = OracleClient(CASES, flip={"needs:manager": 0.6})
    report = await ev.evaluate(client, CASES, floors=FLOORS)
    manager = _result(report, "routing", "needs:manager")
    assert manager.overall.confident == 0
    assert manager.agreement is None
    assert manager.coverage == 0.0
    assert not manager.meets_target  # no evidence is not a pass
    assert _result(report, "routing", "needs:mail").meets_target


async def test_a_confident_wrong_answer_is_counted_against_the_question() -> None:
    client = OracleClient(CASES, flip={"injection": 0.95})
    report = await ev.evaluate(client, CASES, floors=FLOORS)
    injection = _result(report, "inbound_injection", "injection")
    assert injection.agreement == 0.0
    assert not injection.meets_target
    assert not report.passed
    assert injection.overall.false_yes == 20
    assert injection.overall.false_no == 20
    assert len(injection.overall.wrong) == 40
    # Other questions are judged on their own.
    assert _result(report, "routing", "needs:team").meets_target


async def test_a_choice_question_is_judged_by_its_option_and_confidence() -> None:
    client = OracleClient(CASES, flip={"mode": 0.9})
    report = await ev.evaluate(client, CASES, floors=FLOORS)
    mode = _result(report, "email", "mode")
    assert mode.agreement == 0.0
    assert mode.overall.false_yes == 0  # false yes and no are for yes/no questions only
    # Raising the floor above its confidence turns the same answers into fallbacks.
    report = await ev.evaluate(client, CASES, floors={"noul": 0.7, "choice": 0.95})
    assert _result(report, "email", "mode").overall.confident == 0


async def test_the_injection_target_is_stricter_than_the_others() -> None:
    assert ev.TARGETS["injection"] > ev.DEFAULT_TARGET
    # 4 wrong of 40 confident is 90 percent: meets the default bar, not the injection bar.
    inbound = [case for case in CASES if case.area == "inbound_injection"]
    wrong_ids = {case.state["message"] for case in inbound[:2] + inbound[-2:]}

    class Partly(OracleClient):
        async def ask(self, state: object, questions: Any, *, timeout: float) -> JevResponse:
            response = await super().ask(state, questions, timeout=timeout)
            if state["message"] in wrong_ids:  # type: ignore[index]
                answers = {"injection": {"noul": 1 - response.answers["injection"]["noul"]}}
                return JevResponse(model="fake", answers=answers)
            return response

    report = await ev.evaluate(Partly(CASES), inbound, floors=FLOORS)
    injection = _result(report, "inbound_injection", "injection")
    assert injection.agreement == pytest.approx(36 / 40)
    assert not injection.meets_target


# ------------------------------------------------------------------------------ failure


async def test_jev_errors_fall_back_and_are_counted_not_raised() -> None:
    client = OracleClient(CASES, error=JevError("jev_http_503"))
    report = await ev.evaluate(client, CASES, floors=FLOORS)
    assert not report.passed
    assert report.errors == Counter({"jev_http_503": len(CASES)})
    for result in report.results:
        assert result.overall.errors == result.overall.total
        assert result.overall.confident == 0


async def test_an_unexpected_client_failure_is_counted_by_type_only() -> None:
    client = OracleClient(CASES, error=RuntimeError("secret detail in the message"))
    report = await ev.evaluate(client, CASES, floors=FLOORS)
    assert report.errors == Counter({"RuntimeError": len(CASES)})
    assert "secret detail" not in ev.render(report)


async def test_a_hanging_jev_times_out_as_a_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ev, "CLASSIFY_TIMEOUT_S", 0.05)

    class Hangs:
        async def ask(self, state: object, questions: Any, *, timeout: float) -> JevResponse:
            await asyncio.sleep(10)
            raise AssertionError("unreachable")

    few = CASES[:3]
    report = await ev.evaluate(Hangs(), few, floors=FLOORS)
    assert report.errors == Counter({"jev_timeout": 3})


async def test_a_malformed_answer_is_an_error_for_that_question_only() -> None:
    class Malformed(OracleClient):
        async def ask(self, state: object, questions: Any, *, timeout: float) -> JevResponse:
            response = await super().ask(state, questions, timeout=timeout)
            response.answers["needs:team"] = {"noul": 7}  # not a probability
            return response

    routing = [case for case in CASES if case.area == "routing"]
    report = await ev.evaluate(Malformed(routing), routing, floors=FLOORS)
    assert report.errors == Counter({"jev_invalid_answer": len(routing)})
    assert _result(report, "routing", "needs:team").overall.errors == len(routing)
    assert _result(report, "routing", "needs:mail").meets_target


# ----------------------------------------------------------------------- what is sent


async def test_only_redacted_text_is_sent_to_jev() -> None:
    case = ev.LabelledCase(
        id="redact-1",
        area="inbound_injection",
        language="en",
        state={"message": "call 012-3456789 or write to someone@example.com"},
        expect={"injection": False},
    )
    sent: list[object] = []

    class Spy:
        async def ask(self, state: object, questions: Any, *, timeout: float) -> JevResponse:
            sent.append(state)
            return JevResponse(model="fake", answers={"injection": {"noul": 0.05}})

    await ev.evaluate(Spy(), [case], floors=FLOORS)
    (state,) = sent
    assert "someone@example.com" not in json.dumps(state)
    assert "3456789" not in json.dumps(state)


# ------------------------------------------------------------------------------ report


async def test_the_report_is_content_free() -> None:
    client = OracleClient(CASES, flip={"injection": 0.95, "needs:mail": 0.9})
    text = ev.render(await ev.evaluate(client, CASES, floors=FLOORS))
    for case in CASES:
        for value in case.state.values():
            assert value not in text
    assert "[inbound_injection] injection: BELOW TARGET" in text
    assert "[routing] needs:team: PASS" in text
    assert "confident yes where label is no" in text
    assert "mislabelled: inbound-en-01" in text
    assert "RESULT: below target" in text
    # Ids of cases are synthetic; the first few are named and the rest counted.
    assert "more)" in text


async def test_the_report_names_the_pass_and_each_language() -> None:
    text = ev.render(await ev.evaluate(OracleClient(CASES), CASES, floors=FLOORS))
    assert "RESULT: every question meets its target" in text
    assert "    en: agreement 100%" in text
    assert "    ms: agreement 100%" in text
    assert "floor 0.7" in text


# ------------------------------------------------------------------------------- script


def _stub_evaluate(monkeypatch: pytest.MonkeyPatch, report: ev.Report) -> list[Mapping[str, float]]:
    seen: list[Mapping[str, float]] = []

    async def fake(client: object, cases: object, *, floors: Mapping[str, float], **_: object):
        seen.append(floors)
        return report

    monkeypatch.setattr(ev, "evaluate", fake)
    return seen


async def _report(client: OracleClient) -> ev.Report:
    return await ev.evaluate(client, CASES, floors=FLOORS)


def test_the_script_needs_a_key_from_the_shell(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert ev.main([]) == 2
    assert "TYPESAFE_API_KEY" in capsys.readouterr().err
    monkeypatch.setenv("TYPESAFE_API_KEY", "   ")
    assert ev.main([]) == 2


def test_the_script_exits_0_on_a_pass_and_1_below_target(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-key-never-printed")
    good = asyncio.run(_report(OracleClient(CASES)))
    bad = asyncio.run(_report(OracleClient(CASES, flip={"injection": 0.95})))
    seen = _stub_evaluate(monkeypatch, good)
    assert ev.main([]) == 0
    assert seen == [{"noul": 0.7, "choice": 0.7}]  # the settings' default floors
    _stub_evaluate(monkeypatch, bad)
    assert ev.main(["--floor-noul", "0.8", "--floor-choice", "0.9"]) == 1
    out = capsys.readouterr()
    assert "RESULT: every question meets its target" in out.out
    assert "RESULT: below target" in out.out
    assert "synthetic-key-never-printed" not in out.out + out.err


def test_the_script_floors_default_to_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    good = asyncio.run(_report(OracleClient(CASES)))
    seen = _stub_evaluate(monkeypatch, good)
    ev.main(["--floor-choice", "0.9"])
    assert seen == [{"noul": 0.7, "choice": 0.9}]


def test_the_script_refuses_unusable_labels_without_echoing_them(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    path = tmp_path / "labels.json"
    path.write_text(json.dumps({"cases": [_case(expect={"injection": "SECRET TEXT"})]}))
    assert ev.main(["--labels", str(path)]) == 2
    err = capsys.readouterr().err
    assert "labels are unusable" in err
    assert "SECRET TEXT" not in err
    assert ev.main(["--labels", str(tmp_path / "missing.json")]) == 2
