"""The Jev evaluation gate: labels, agreement on confident answers, content-free report."""

from __future__ import annotations

import asyncio
import io
import json
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from csa.decide.jev import JevError, JevResponse
from pydantic import ValidationError
from scripts import assistant_jev_eval as ev

SMOKE_PATH = Path(__file__).resolve().parents[1] / "csa/workspace/smoke_cases.json"

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
        # Keyed by what Jev receives: the redacted state (a smoke message's address is a
        # placeholder by then), whole, since a routing and an email case can share a message.
        self._by_state = {_sent_key(ev._redact_state(case.state, ())): case for case in cases}
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
        case = self._by_state[_sent_key(state)]
        answers: dict[str, dict[str, Any]] = {}
        for name, question in questions.items():
            label = case.expect[name]
            if name in self._flip:
                label = (not label) if isinstance(label, bool) else _other_mode(label)
                answers[name] = _answer(question, label, confidence=self._flip[name])
            else:
                answers[name] = _answer(question, label, confidence=self._confidence)
        return JevResponse(model="fake", answers=answers)


def _sent_key(state: object) -> str:
    return json.dumps(state, sort_keys=True)


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
    # Smoke messages carry addresses; the evaluation redacts them before anything is sent (see
    # test_only_redacted_text_is_sent_to_jev), so the file may hold reserved example ones only.
    text = ev.LABELS_PATH.read_text(encoding="utf-8")
    domains = set(re.findall(r"@([A-Za-z0-9.-]+)", text))
    assert domains <= {"example.com", "example.test"}, domains
    assert "http" not in text


SMOKE = {case["id"]: case for case in json.loads(SMOKE_PATH.read_text(encoding="utf-8"))}
# Smoke cases with no labelled counterpart, and why: none of them needs a fact group or an email
# decision, or what the question would answer is not clear from its wording.
SMOKE_LEFT_OUT = {
    # No fact group, no email: navigation, help, bookings.
    "navigate-en-1", "navigate-ms-1", "navigate-en-2", "navigate-ms-2", "navigate-ms-3",
    "answer-en-1", "answer-ms-1",
    "booking_book-en-1", "booking_book-ms-1", "booking_book-en-2",
    "booking_move-en-1", "booking_move-ms-1", "booking_move-ms-2",
    "booking_cancel-en-1", "booking_cancel-ms-1",
    "booking_free-en-1", "booking_free-ms-1", "booking_free-en-2",
    "plan_bookings-en-1", "plan_bookings-ms-1",
    # Finding or opening one person: "people records" in the question names adding, changing,
    # removing, reporting lines and leaving; a lookup is not clearly one of them.
    "directory-en-1", "directory-ms-1", "hr_open-en-1", "hr_open-ms-1",
}  # fmt: skip


def test_the_smoke_derived_labels_are_the_smoke_messages_jev_would_see() -> None:
    sourced = [case for case in CASES if case.source is not None]
    assert sourced, "no labels derived from the smoke cases"
    for case in sourced:
        smoke = SMOKE[case.source]
        assert case.language == smoke["language"], case.id
        # Identical once redacted: an address may be swapped for an example one.
        assert ev._redact_state(case.state["message"], ()) == ev._redact_state(
            smoke["message"], ()
        ), case.id
    # Every smoke case is either labelled or left out on purpose (a new one needs a decision).
    assert {case.source for case in sourced} | SMOKE_LEFT_OUT == set(SMOKE)
    assert not {case.source for case in sourced} & SMOKE_LEFT_OUT
    messages = {case.state["message"] for case in sourced}
    for phrase in (
        "Email my manager that I will be late tomorrow morning",
        "Who reports to Razak?",
    ):
        assert phrase in messages
    assert any("each on their own" in message for message in messages)
    assert {case.area for case in sourced} == {"routing", "email"}


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
    assert "@" not in json.dumps(client.sent)  # the smoke messages' addresses were redacted
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


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def _capture_run(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    keys: list[str] = []

    async def fake_run(args: object, api_key: str, cases: object) -> ev.Report:
        keys.append(api_key)
        return await _report(OracleClient(CASES))

    monkeypatch.setattr(ev, "_run", fake_run)
    return keys


def test_with_no_key_in_the_shell_a_terminal_gets_a_hidden_prompt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(ev.sys, "stdin", _Terminal())
    prompts: list[str] = []

    def hidden(prompt: str = "") -> str:
        prompts.append(prompt)
        return "  synthetic-key-never-printed\n"

    monkeypatch.setattr(ev.getpass, "getpass", hidden)
    keys = _capture_run(monkeypatch)
    assert ev.main([]) == 0
    assert len(prompts) == 1
    assert "synthetic-key-never-printed" not in prompts[0]
    assert keys == ["synthetic-key-never-printed"]
    out = capsys.readouterr()
    assert "synthetic-key-never-printed" not in out.out + out.err


def test_a_key_in_the_shell_is_used_without_a_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-env-key")
    monkeypatch.setattr(ev.sys, "stdin", _Terminal())

    def never(prompt: str = "") -> str:
        raise AssertionError("prompted although the shell had a key")

    monkeypatch.setattr(ev.getpass, "getpass", never)
    keys = _capture_run(monkeypatch)
    assert ev.main([]) == 0
    assert keys == ["synthetic-env-key"]


def test_with_no_key_and_no_terminal_the_script_refuses_without_prompting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(ev.sys, "stdin", io.StringIO("piped-synthetic-key\n"))

    def never(prompt: str = "") -> str:
        raise AssertionError("prompted without a terminal")

    monkeypatch.setattr(ev.getpass, "getpass", never)
    keys = _capture_run(monkeypatch)
    assert ev.main([]) == 2
    assert keys == []
    err = capsys.readouterr().err
    assert "TYPESAFE_API_KEY" in err
    assert "piped-synthetic-key" not in err


@pytest.mark.parametrize("entered", ["", "   ", EOFError(), KeyboardInterrupt()])
def test_an_empty_or_abandoned_prompt_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], entered: object
) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(ev.sys, "stdin", _Terminal())

    def hidden(prompt: str = "") -> str:
        if isinstance(entered, BaseException):
            raise entered
        return str(entered)

    monkeypatch.setattr(ev.getpass, "getpass", hidden)
    keys = _capture_run(monkeypatch)
    assert ev.main([]) == 2
    assert keys == []
    assert "no key" in capsys.readouterr().err
