"""Model smoke run (scripts/assistant_smoke.py) with a scripted model: no network, no database.

The real run is owner-triggered inside the staging gateway container; these tests only prove the
case file, the checklist link, the content-free output and the exit codes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from csa.decide.completion import CompletionError
from csa.email.models import DraftProposal
from csa.workspace.planner import PlannerOutput, detect_language
from csa.workspace.rules import ADDRESS
from csa.workspace.service import REST_WORDS
from pydantic import SecretStr, TypeAdapter, ValidationError
from scripts import assistant_smoke as smoke

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKLIST = REPO_ROOT / "docs" / "ASSISTANT_RETEST.md"
FEATURES = (
    "navigate",
    "answer",
    "directory",
    "compose",
    "draft",
    "reply",
    "team",
    "attention",
    "training",
    "training_ask",
    "dont_send",
    "hr_add",
    "hr_update",
    "hr_leave",
    "people_query",
    "hr_open",
    "booking_book",
    "booking_move",
    "booking_cancel",
    "booking_free",
    "email_batch",
    "email_batch_group",
    "email_batch_rest",
    "plan_bookings",
)
PEOPLE_ACTIONS = ("hr_person_add", "hr_person_update", "hr_person_leave", "people_query")
SCRIPTED_SUBJECT = "SCRIPTED-SUBJECT-7f3a"
SCRIPTED_BODY = "SCRIPTED-BODY-91c2 the scripted wording"
SCRIPTED_TEXT = "SCRIPTED-TEXT-55d0"
UNREQUESTED_ADDRESS = "scripted.person@example.com"  # in no case message
BATCH_NAMES = ("Aina Rahman", "Danial Hakim", "Mei Ling Tan")  # synthetic, in the batch cases


def _feature(case_id: str) -> str:
    return case_id.rsplit("-", 2)[0]


def test_smoke_cases_parse_and_cover_features() -> None:
    cases = smoke.load_cases()
    ids = [case.id for case in cases]
    assert len(ids) == len(set(ids))
    messages = [case.message for case in cases]
    assert len(messages) == len(set(messages))
    for case in cases:
        assert re.fullmatch(r"[a-z_]+-(en|ms)-\d+", case.id), case.id
        assert _feature(case.id) in FEATURES, case.id
        assert case.id.split("-")[-2] == case.language, case.id
        # The facts' language is the one the turn path would detect from these words.
        assert detect_language(case.message) == case.language, case.id
        smoke.build_facts(case)  # every preset and override builds valid PlannerFacts
    for feature in FEATURES:
        languages = [case.language for case in cases if _feature(case.id) == feature]
        assert len(languages) >= 2, feature
        assert {"en", "ms"} <= set(languages), feature
    assert {case.language for case in cases} == {"en", "ms"}


def test_smoke_cases_and_checklist_hold_only_synthetic_addresses() -> None:
    """The repository is public: every address in the cases and the checklist is invented, on
    example.com or a .test domain, never a real mailbox."""
    for path in (smoke.CASES_FILE, CHECKLIST):
        for address in ADDRESS.findall(path.read_text(encoding="utf-8")):
            domain = address.rsplit("@", 1)[1].lower()
            assert domain == "example.com" or domain.endswith(".test"), (path.name, address)


def test_unknown_preset_or_override_is_refused() -> None:
    adapter = TypeAdapter(smoke.SmokeCase)
    base = {
        "id": "navigate-en-9",
        "language": "en",
        "message": "Open the HR tab",
        "expect": {"actions": ["navigate"]},
    }
    with pytest.raises(ValidationError):
        adapter.validate_python({**base, "facts": {"preset": "staff_nobody"}})
    with pytest.raises(ValidationError):
        adapter.validate_python({**base, "facts": {"preset": "staff_basic", "colour": "red"}})
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {**base, "facts": {"preset": "staff_basic"}, "expect": {"actions": ["fly"]}}
        )


def test_every_smoke_case_is_in_the_checklist() -> None:
    checklist = CHECKLIST.read_text(encoding="utf-8")
    missing = [case.id for case in smoke.load_cases() if f"`{case.id}`" not in checklist]
    assert missing == []
    # And the reverse: every smoke id the checklist cites is a real case.
    known = {case.id for case in smoke.load_cases()}
    cited = set(re.findall(r"`([a-z_]+-(?:en|ms)-\d+)`", checklist))
    assert cited - known == set()


class ScriptedModel:
    """Answers each planner call with the case's first allowed action, built from synthetic
    values, except for the case ids in ``wrong``, which get an answer instead."""

    model = "scripted"

    def __init__(self, wrong: frozenset[str] = frozenset()) -> None:
        self.by_message = {case.message: case for case in smoke.load_cases()}
        self.wrong = wrong
        self.calls: list[str] = []

    def _proposal(self, case: smoke.SmokeCase) -> dict[str, Any]:
        action = "answer" if case.id in self.wrong else case.expect.actions[0]
        send = bool(case.expect.send)
        facts = smoke.build_facts(case)
        if action == "answer" and case.expect.reads and case.id not in self.wrong:
            ask = {"person": "Aina", "day": "2026-11-06"}
            return {
                "action": "answer",
                "markdown": SCRIPTED_TEXT,
                "reads": case.expect.reads,
                "availability": ask,
            }
        if action == "answer":
            return {"action": "answer", "markdown": SCRIPTED_TEXT}
        if action == "booking_action":
            return {"action": "booking_action", **case.expect.fields}
        if action == "navigate":
            # Subscriptions is active in no preset, so it is locked; HR is open in staff_basic.
            return {"action": "navigate", "tab": "subscriptions" if case.expect.locked else "hr"}
        if action == "directory_lookup":
            query = " ".join(case.expect.query_words) or SCRIPTED_TEXT
            return {"action": "directory_lookup", "query": query}
        if action == "email_compose":
            # The recipient the user asked for: the address in the message, else my_manager.
            found = ADDRESS.search(case.message)
            if found is not None:
                recipient: dict[str, Any] = {"recipient_address": found.group(0)}
            else:
                assert facts.my_manager is not None and facts.my_manager.employee_id is not None
                recipient = {"recipient_employee_id": str(facts.my_manager.employee_id)}
            return {
                "action": "email_compose",
                **recipient,
                "subject": SCRIPTED_SUBJECT,
                "instructions": SCRIPTED_TEXT,
                "send": send,
            }
        if action == "email_reply":
            assert facts.mailbox_threads
            thread = facts.mailbox_threads[0].thread_id
            return {"action": "email_reply", "thread_id": thread, "instructions": SCRIPTED_TEXT}
        if action == "email_team":
            return {
                "action": "email_team",
                "subject": SCRIPTED_SUBJECT,
                "instructions": SCRIPTED_TEXT,
                "send": send,
            }
        if action == "inbox_summary":
            return {"action": "inbox_summary"}
        if action == "email_batch":
            group = case.expect.group
            if group is not None:
                days = {"days": 30} if group == "renewing_soon" else {}
                recipients = [{"kind": "group", "group": group, **days}]
            else:
                # The names as the message writes them: a name the user never wrote is refused.
                count = case.expect.recipients or 2
                names = [name for name in BATCH_NAMES if name in case.message][:count]
                assert len(names) == count, case.id
                recipients = [{"kind": "employee", "value": name} for name in names]
            return {
                "action": "email_batch",
                "recipients": recipients,
                "subject": SCRIPTED_SUBJECT,
                "instructions": SCRIPTED_TEXT,
                "send": send,
                **case.expect.fields,
            }
        if action == "plan":
            steps = [{"action": step, **case.expect.step_fields} for step in case.expect.steps]
            return {"action": "plan", "steps": steps}
        if action == "training_ask":
            return {"action": "training_ask", "question": SCRIPTED_TEXT}
        if action == "training_submit":
            return {
                "action": "training_submit",
                "title": SCRIPTED_TEXT,
                "learning_objective": SCRIPTED_TEXT,
            }
        if action in (*PEOPLE_ACTIONS, "hr_person_open"):
            # The expected fields, as the model would write them from the message.
            return {"action": action, **case.expect.fields}
        return {"action": "clarification", "question": SCRIPTED_TEXT, "options": []}

    async def json_completion(
        self, schema: type[Any], *, system: str, user: str, timeout: float, max_tokens: int = 1500
    ) -> Any:
        self.calls.append(schema.__name__)
        if schema is DraftProposal:
            return DraftProposal(subject=SCRIPTED_SUBJECT, body=SCRIPTED_BODY)
        assert schema is PlannerOutput
        message = user.split("<message>\n", 1)[1].rsplit("\n</message>", 1)[0]
        case = self.by_message[message]
        # Parsed from JSON text, as GroqClient parses the provider's reply.
        return PlannerOutput.model_validate_json(json.dumps({"proposal": self._proposal(case)}))


def _assert_content_free(out: str) -> None:
    for case in smoke.load_cases():
        assert case.message not in out
    for secret in (SCRIPTED_SUBJECT, SCRIPTED_BODY, SCRIPTED_TEXT, "SCRIPTED"):
        assert secret not in out
    assert "@" not in out


def test_smoke_output_is_content_free_and_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    cases = smoke.load_cases()
    failing = next(case for case in cases if case.expect.actions == ["email_compose"])

    assert smoke.main([], client=ScriptedModel(wrong=frozenset({failing.id}))) == 1
    out = capsys.readouterr().out
    _assert_content_free(out)
    lines = out.strip().splitlines()
    assert f"FAIL {failing.id} action=answer actions" in lines
    passed = [line for line in lines if line.startswith("PASS ")]
    assert len(passed) == len(cases) - 1
    for line in passed:
        assert re.fullmatch(
            r"PASS [a-z_]+-(en|ms)-\d+ action=[a-z_]+( replanned)?( mode_inferred)?", line
        ), line
    assert lines[-1] == f"{len(cases) - 1} passed, 1 failed"

    # Every case passing exits 0; drafting was called for the draft checks.
    model = ScriptedModel()
    assert smoke.main([], client=model) == 0
    out = capsys.readouterr().out
    _assert_content_free(out)
    assert out.strip().splitlines()[-1] == f"{len(cases)} passed, 0 failed"
    assert "DraftProposal" in model.calls


def test_smoke_case_filter_and_repeat(capsys: pytest.CaptureFixture[str]) -> None:
    case = smoke.load_cases()[0]
    model = ScriptedModel()
    assert smoke.main(["--case", case.id, "--repeat", "3"], client=model) == 0
    out = capsys.readouterr().out
    _assert_content_free(out)
    lines = out.strip().splitlines()
    assert lines.count(f"PASS {case.id} action={case.expect.actions[0]}") == 3
    assert f"RATE {case.id} 3/3" in lines
    assert lines[-1] == "3 passed, 0 failed"
    assert model.calls.count("PlannerOutput") == 3

    assert smoke.main(["--case", "nowhere-en-1"], client=model) == 2


def test_smoke_send_rule_and_placeholder_checks(capsys: pytest.CaptureFixture[str]) -> None:
    """The server's pure rules decide: a placeholder option and a send against the words fail."""
    dont = next(case for case in smoke.load_cases() if case.id.startswith("dont_send-en"))

    class SendingModel(ScriptedModel):
        def _proposal(self, case: smoke.SmokeCase) -> dict[str, Any]:
            proposal = super()._proposal(case)
            return {**proposal, "send": True}

    # "don't send" is a draft card whatever the plan says: the send rule holds.
    assert smoke.main(["--case", dont.id], client=SendingModel()) == 0
    capsys.readouterr()

    class DraftingModel(ScriptedModel):
        def _proposal(self, case: smoke.SmokeCase) -> dict[str, Any]:
            return {**super()._proposal(case), "send": False}

    # "Email my manager that ..." has no send word: the plan's send decides, and false fails.
    assert smoke.main(["--case", "compose-en-2"], client=DraftingModel()) == 1
    out = capsys.readouterr().out
    assert "FAIL compose-en-2 action=email_compose send" in out

    clarifying = next(
        case
        for case in smoke.load_cases()
        if case.expect.no_placeholder_options and "clarification" in case.expect.actions
    )

    class PlaceholderModel(ScriptedModel):
        def _proposal(self, case: smoke.SmokeCase) -> dict[str, Any]:
            return {
                "action": "clarification",
                "question": "Who?",
                "options": [{"label": "Enter address", "value": "enter_address"}],
            }

    assert smoke.main(["--case", clarifying.id], client=PlaceholderModel()) == 1
    out = capsys.readouterr().out
    assert f"FAIL {clarifying.id} action=clarification placeholder_options" in out
    assert "Enter address" not in out


def test_smoke_without_model_key_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "gsk_should_never_print"
    monkeypatch.setattr(
        smoke,
        "get_settings",
        lambda: SimpleNamespace(GROQ_API_KEY=SecretStr(""), GROQ_MODEL=secret),
    )
    assert smoke.main([]) == 2
    captured = capsys.readouterr()
    assert "no model key" in captured.err
    assert secret not in captured.out + captured.err

    def broken() -> Any:
        return TypeAdapter(int).validate_python(secret)

    monkeypatch.setattr(smoke, "get_settings", broken)
    assert smoke.main([]) == 2
    captured = capsys.readouterr()
    assert "no model key" in captured.err
    assert secret not in captured.out + captured.err


def test_smoke_replans_a_content_question_once(capsys: pytest.CaptureFixture[str]) -> None:
    """As on the turn path: a question for the wording is re-planned once with the server note;
    asked again, the case fails on ``content_question``."""
    case_id = "compose-en-1"

    class AskingModel(ScriptedModel):
        def __init__(self, asks: int) -> None:
            super().__init__()
            self.asks = asks
            self.notes: list[bool] = []

        async def json_completion(
            self,
            schema: type[Any],
            *,
            system: str,
            user: str,
            timeout: float,
            max_tokens: int = 1500,
        ) -> Any:
            if schema is PlannerOutput:
                self.notes.append("<correction>" in user)
                if self.asks > 0:
                    self.asks -= 1
                    question = {"action": "clarification", "question": "What subject?"}
                    return PlannerOutput.model_validate({"proposal": question})
            return await super().json_completion(
                schema, system=system, user=user, timeout=timeout, max_tokens=max_tokens
            )

    once = AskingModel(asks=1)
    assert smoke.main(["--case", case_id], client=once) == 0
    assert f"PASS {case_id} action=email_compose replanned" in capsys.readouterr().out
    assert once.notes == [False, True]

    twice = AskingModel(asks=2)
    assert smoke.main(["--case", case_id], client=twice) == 1
    out = capsys.readouterr().out
    assert f"FAIL {case_id} action=clarification content_question actions replanned" in out
    assert twice.notes == [False, True]


class UnrequestedModel(ScriptedModel):
    """Compose plans to an address the user never wrote, for the first ``bad`` calls."""

    def __init__(self, bad: int) -> None:
        super().__init__()
        self.bad = bad

    def _proposal(self, case: smoke.SmokeCase) -> dict[str, Any]:
        proposal = super()._proposal(case)
        if proposal["action"] == "email_compose" and self.bad > 0:
            self.bad -= 1
            return {**proposal, "recipient_address": UNREQUESTED_ADDRESS}
        return proposal


def test_smoke_refuses_an_unrequested_recipient(capsys: pytest.CaptureFixture[str]) -> None:
    """The turn path's recipient check runs: refused once, the case is re-planned with the note
    and passes; refused twice, the user gets the fallback and the case fails on ``recipient``."""
    once = UnrequestedModel(bad=1)
    assert smoke.main(["--case", "compose-en-1"], client=once) == 0
    out = capsys.readouterr().out
    assert "PASS compose-en-1 action=email_compose replanned" in out
    assert once.calls.count("PlannerOutput") == 2

    twice = UnrequestedModel(bad=2)
    assert smoke.main(["--case", "compose-en-1"], client=twice) == 1
    out = capsys.readouterr().out
    assert "FAIL compose-en-1 action=email_compose recipient replanned" in out
    assert twice.calls.count("PlannerOutput") == 2
    assert "DraftProposal" not in twice.calls  # nothing is drafted for a refused plan
    _assert_content_free(out)


def test_smoke_expected_recipient_and_locked_tab(capsys: pytest.CaptureFixture[str]) -> None:
    """An employee id the user never offered fails ``recipient``; a navigate to an open tab
    where the case expects the locked answer fails ``locked``, and passes where it does not."""

    class ManagerModel(ScriptedModel):
        def _proposal(self, case: smoke.SmokeCase) -> dict[str, Any]:
            proposal = super()._proposal(case)
            proposal.pop("recipient_address", None)
            return {**proposal, "recipient_employee_id": str(smoke.MANAGER_ID)}

    # The message names no manager, so the id was never offered: refused twice, the fallback.
    assert smoke.main(["--case", "compose-en-3"], client=ManagerModel()) == 1
    assert "FAIL compose-en-3 action=email_compose recipient replanned" in (capsys.readouterr().out)

    class OpenTabModel(ScriptedModel):
        def _proposal(self, case: smoke.SmokeCase) -> dict[str, Any]:
            return {"action": "navigate", "tab": "overview"}

    assert smoke.main(["--case", "navigate-ms-2"], client=OpenTabModel()) == 1
    assert "FAIL navigate-ms-2 action=navigate locked" in capsys.readouterr().out
    assert smoke.main(["--case", "navigate-en-1"], client=OpenTabModel()) == 0
    capsys.readouterr()


def test_smoke_checks_the_lookup_query_words(capsys: pytest.CaptureFixture[str]) -> None:
    """A Malay title searched in Malay fails ``query_words``; the English title passes. The
    query itself is never printed."""

    class QueryModel(ScriptedModel):
        def __init__(self, query: str) -> None:
            super().__init__()
            self.query = query

        def _proposal(self, case: smoke.SmokeCase) -> dict[str, Any]:
            return {"action": "directory_lookup", "query": self.query}

    assert smoke.main(["--case", "directory-ms-4"], client=QueryModel("pengurus kewangan")) == 1
    out = capsys.readouterr().out
    assert "FAIL directory-ms-4 action=directory_lookup query_words" in out
    assert "pengurus" not in out and "kewangan" not in out
    # Both titles in one query finds nobody (every word must match), so it fails too.
    mixed = QueryModel("pengurus kewangan finance manager")
    assert smoke.main(["--case", "directory-ms-4"], client=mixed) == 1
    assert "FAIL directory-ms-4 action=directory_lookup query_words" in capsys.readouterr().out
    assert smoke.main(["--case", "directory-ms-4"], client=QueryModel("Finance Manager")) == 0
    out = capsys.readouterr().out
    assert "PASS directory-ms-4" in out
    assert "Finance" not in out


def test_smoke_stops_when_the_model_is_unavailable_first(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A provider failure on the first call is a setup error: exit 2, no further calls."""

    class DownModel(ScriptedModel):
        async def json_completion(
            self,
            schema: type[Any],
            *,
            system: str,
            user: str,
            timeout: float,
            max_tokens: int = 1500,
        ) -> Any:
            self.calls.append(schema.__name__)
            raise CompletionError("groq_timeout")

    down = DownModel()
    assert smoke.main([], client=down) == 2
    captured = capsys.readouterr()
    assert "model unavailable" in captured.err
    assert captured.out == ""
    assert down.calls == ["PlannerOutput"]


@pytest.mark.parametrize("broken", ["missing", "not_json", "bad_case"])
def test_smoke_cases_file_problem_is_a_setup_error(
    broken: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing or unreadable case file exits 2 with a fixed line, never the file's content."""
    marker = "SCRIPTED-FILE-CONTENT-3b8e"
    path = tmp_path / "smoke_cases.json"
    if broken == "not_json":
        path.write_text(f"{{{marker}", encoding="utf-8")
    elif broken == "bad_case":
        path.write_text(json.dumps([{"id": marker, "message": marker}]), encoding="utf-8")
    monkeypatch.setattr(smoke, "CASES_FILE", path)
    model = ScriptedModel()
    assert smoke.main([], client=model) == 2
    captured = capsys.readouterr()
    assert captured.err == "setup error: the smoke cases file is missing or invalid\n"
    assert captured.out == ""
    assert marker not in captured.err and str(tmp_path) not in captured.err
    assert model.calls == []


def test_owner_training_phrase_is_a_smoke_case() -> None:
    """The staging phrase that drew an email card: an unlinked login gets no email action."""
    case = next(case for case in smoke.load_cases() if case.id == "training-en-3")
    assert case.message == "i want to send a request for ComptiaA+ training"
    assert case.facts.preset == "staff_unlinked"
    assert {"email_compose", "email_reply", "email_team", "training_submit"} <= set(
        case.expect.not_actions
    )
    assert not set(case.expect.actions) & set(case.expect.not_actions)


def test_smoke_checks_the_expected_fields(capsys: pytest.CaptureFixture[str]) -> None:
    """A people action's fields are compared with the case's (case-insensitive); a wrong value
    fails ``fields`` and is never printed."""
    case = next(case for case in smoke.load_cases() if case.id == "hr_update-en-1")
    assert case.expect.fields == {"person": "Siti", "manager": "Razak"}

    class FieldsModel(ScriptedModel):
        def __init__(self, manager: str) -> None:
            super().__init__()
            self.manager = manager

        def _proposal(self, case: smoke.SmokeCase) -> dict[str, Any]:
            return {**super()._proposal(case), "manager": self.manager}

    assert smoke.main(["--case", case.id], client=FieldsModel("RAZAK")) == 0
    assert f"PASS {case.id} action=hr_person_update" in capsys.readouterr().out
    assert smoke.main(["--case", case.id], client=FieldsModel("Zulkifli")) == 1
    out = capsys.readouterr().out
    assert f"FAIL {case.id} action=hr_person_update fields" in out
    assert "Zulkifli" not in out and "Razak" not in out


def test_people_cases_use_the_people_presets() -> None:
    """Changing people is for HR managers; staff_basic only has the directory view."""
    cases = {case.id: case for case in smoke.load_cases()}
    for case_id, case in cases.items():
        if (
            case_id.split("-")[0] in ("hr_add", "hr_update", "hr_leave")
            and case_id != "hr_add-en-2"
        ):
            assert case.facts.preset == "hr_people_manager", case_id
            assert smoke.build_facts(case).people_access == "hr", case_id
    refused = cases["hr_add-en-2"]
    assert refused.facts.preset == "staff_basic" and refused.expect.actions == ["answer"]
    assert smoke.build_facts(refused).people_access == "directory"


def test_booking_cases_use_the_booking_presets() -> None:
    """Booking facts exist only for a booking message, and a viewer may only ask free times."""
    cases = {case.id: case for case in smoke.load_cases()}
    for case_id, case in cases.items():
        if case_id.startswith("booking_"):
            facts = smoke.build_facts(case)
            assert facts.bookings is not None, case_id
            assert facts.bookings.can_change is (case.facts.preset == "staff_bookings"), case_id
    viewer = cases["booking_book-en-2"]
    assert viewer.expect.actions == ["answer"] and viewer.expect.not_actions == ["booking_action"]
    # Whatever the words, a company with Bookings open gives the model its clock; one with the
    # tab closed (the other presets) gives none.
    other = cases["navigate-en-1"].model_copy(update={"facts": cases["booking_book-en-1"].facts})
    assert smoke.build_facts(other).bookings is not None
    for case_id in ("booking_move-ms-2", "booking_free-en-2"):
        assert smoke.build_facts(cases[case_id]).bookings is not None, case_id
    assert smoke.build_facts(cases["navigate-en-1"]).bookings is None


def test_smoke_checks_the_expected_reads(capsys: pytest.CaptureFixture[str]) -> None:
    """A free-times question must come back as an answer that carries the availability read."""
    case = next(case for case in smoke.load_cases() if case.id == "booking_free-en-1")
    assert case.expect.reads == ["booking_availability"]
    assert smoke.main(["--case", case.id], client=ScriptedModel()) == 0
    assert f"PASS {case.id} action=answer" in capsys.readouterr().out
    assert smoke.main(["--case", case.id], client=ScriptedModel(frozenset({case.id}))) == 1
    assert f"FAIL {case.id} action=answer reads" in capsys.readouterr().out


BATCH_CASES = ("email_batch-en-1", "email_batch-ms-1", "email_batch_group-en-1")
PLAN_CASES = ("plan_bookings-en-1", "plan_bookings-ms-1")


def test_batch_and_plan_cases_are_smoke_cases_with_their_expectations() -> None:
    """The batch and plan cases (English and Malay) name what a PASS checks."""
    cases = {case.id: case for case in smoke.load_cases()}
    for case_id in (*BATCH_CASES, "email_batch_group-ms-1"):
        case = cases[case_id]
        assert case.expect.actions == ["email_batch"], case_id
        assert "email_compose" in case.expect.not_actions, case_id
        assert case.expect.fields.get("mode") in ("shared", "personalised"), case_id
        assert case.expect.send is not None, case_id
    for case_id in ("email_batch-en-1", "email_batch-ms-1"):
        assert cases[case_id].expect.recipients == 3 and cases[case_id].expect.group is None
        assert cases[case_id].expect.send is True
    for case_id in ("email_batch_group-en-1", "email_batch_group-ms-1"):
        case = cases[case_id]
        assert case.expect.group == "active_subscribers" and case.expect.send is False
        # Groups are subscription customers: the preset has the Subscriptions service on.
        assert case.facts.preset == "staff_subscriptions"
        keys = {p.key for p in smoke.build_facts(case).packages if p.status == "active"}
        assert "subscriptions" in keys
    for case_id in PLAN_CASES:
        case = cases[case_id]
        assert case.expect.actions == ["plan"] and case.facts.preset == "staff_bookings"
        assert case.expect.steps == ["booking_action", "booking_action"]
        assert case.expect.step_fields == {"change": "book"}
    # Subscriptions stays off in every other preset (navigate-ms-2 expects it locked).
    for case in cases.values():
        if case.facts.preset != "staff_subscriptions":
            keys = {p.key for p in smoke.build_facts(case).packages}
            assert "subscriptions" not in keys, case.id


def test_smoke_checks_batch_recipients_group_send_and_plan_steps(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A wrong recipient count, group, send or plan step fails its field, never printed."""
    for case_id in (*BATCH_CASES, *PLAN_CASES):
        assert smoke.main(["--case", case_id], client=ScriptedModel()) == 0
        assert f"PASS {case_id}" in capsys.readouterr().out

    class Wrong(ScriptedModel):
        def __init__(self, change: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
            super().__init__()
            self.change = change

        def _proposal(self, case: smoke.SmokeCase) -> dict[str, Any]:
            return self.change(super()._proposal(case))

    def one_person(p: dict[str, Any]) -> dict[str, Any]:
        return {**p, "recipients": p["recipients"][:1]}

    def renewing(p: dict[str, Any]) -> dict[str, Any]:
        return {**p, "recipients": [{"kind": "group", "group": "renewing_soon", "days": 7}]}

    def one_step(p: dict[str, Any]) -> dict[str, Any]:
        return {**p, "steps": p["steps"][:1]}

    def moves(p: dict[str, Any]) -> dict[str, Any]:
        return {**p, "steps": [{**step, "change": "move"} for step in p["steps"]]}

    def no_send(p: dict[str, Any]) -> dict[str, Any]:
        return {**p, "send": False}

    for case_id, change, field in (
        ("email_batch-en-1", one_person, "recipients"),
        ("email_batch_group-en-1", renewing, "group"),
        ("plan_bookings-en-1", one_step, "steps"),
        ("plan_bookings-ms-1", moves, "step_fields"),
    ):
        assert smoke.main(["--case", case_id], client=Wrong(change)) == 1
        out = capsys.readouterr().out
        assert field in out.split(), (case_id, out)
        _assert_content_free(out)
    # "Send" is in the words: a plan send of false still gives a send (the server's rule).
    assert smoke.main(["--case", "email_batch-en-1"], client=Wrong(no_send)) == 0
    capsys.readouterr()


def test_smoke_refuses_an_unrequested_batch_address(capsys: pytest.CaptureFixture[str]) -> None:
    """A typed-address recipient the message never wrote is refused like on email_compose."""

    class Unrequested(ScriptedModel):
        def _proposal(self, case: smoke.SmokeCase) -> dict[str, Any]:
            proposal = super()._proposal(case)
            if proposal["action"] != "email_batch":
                return proposal
            extra = {"kind": "address", "value": UNREQUESTED_ADDRESS}
            return {**proposal, "recipients": [*proposal["recipients"], extra]}

    assert smoke.main(["--case", "email_batch-en-1"], client=Unrequested()) == 1
    out = capsys.readouterr().out
    assert "FAIL email_batch-en-1 action=email_batch recipient" in out
    assert UNREQUESTED_ADDRESS not in out


REST_CASES = ("email_batch_rest-en-1", "email_batch_rest-ms-1")


def test_the_rest_cases_carry_a_batch_turn_with_a_resume_count() -> None:
    """ "The rest" is a history case: the earlier batch turn holds the stored resume count."""
    cases = {case.id: case for case in smoke.load_cases()}
    for case_id in REST_CASES:
        case = cases[case_id]
        assert case.expect.actions == ["email_batch"] and case.expect.group == "active_subscribers"
        history = smoke.build_facts(case).history
        assert [(turn.action, turn.resume_offset) for turn in history] == [("email_batch", 20)]


def test_the_rest_words_match_the_rest_cases_and_not_everyday_more_or_lagi() -> None:
    """The server's rest-word rule continues a group only on words for "the rest": the smoke
    rest messages match, an everyday "more" or "lagi" in a fresh request does not."""
    cases = {case.id: case for case in smoke.load_cases()}
    for case_id in REST_CASES:
        assert REST_WORDS.search(cases[case_id].message), case_id
    for rest in ("the rest of them", "yang selebihnya", "the remaining ones", "baki pelanggan"):
        assert REST_WORDS.search(rest), rest
    for fresh in (
        "Send more details to all active subscribers",
        "Tolong draf emel kepada semua pelanggan aktif, jangan hantar lagi",
    ):
        assert not REST_WORDS.search(fresh), fresh


def test_smoke_resumes_the_rest_from_the_stored_count_and_fails_an_unknown_offset(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A model that forgets the offset still gets the next page (the server's rest-word rule);
    one that writes an offset the thread never stored fails ``offset``."""
    for case_id in REST_CASES:
        assert smoke.main(["--case", case_id], client=ScriptedModel()) == 0
        assert f"PASS {case_id} action=email_batch" in capsys.readouterr().out

    class Offset(ScriptedModel):
        def __init__(self, offset: int) -> None:
            super().__init__()
            self.offset = offset

        def _proposal(self, case: smoke.SmokeCase) -> dict[str, Any]:
            return {**super()._proposal(case), "offset": self.offset}

    assert smoke.main(["--case", REST_CASES[0]], client=Offset(20)) == 0
    assert f"PASS {REST_CASES[0]}" in capsys.readouterr().out
    assert smoke.main(["--case", REST_CASES[0]], client=Offset(7)) == 1
    out = capsys.readouterr().out
    assert f"FAIL {REST_CASES[0]} action=email_batch offset" in out
    _assert_content_free(out)


def test_smoke_refuses_a_group_or_name_the_message_never_wrote(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The planner alone cannot add a group or a name: refused twice, the recipient fallback."""

    class Added(ScriptedModel):
        def __init__(self, extra: dict[str, Any]) -> None:
            super().__init__()
            self.extra = extra

        def _proposal(self, case: smoke.SmokeCase) -> dict[str, Any]:
            proposal = super()._proposal(case)
            return {**proposal, "recipients": [*proposal["recipients"], self.extra]}

    for extra in (
        {"kind": "group", "group": "active_subscribers"},
        {"kind": "customer", "value": "Scripted Customer"},
    ):
        assert smoke.main(["--case", "email_batch-en-1"], client=Added(extra)) == 1
        out = capsys.readouterr().out
        # The recipient refusal itself ("recipient"), not a wrong count ("recipients").
        assert out.splitlines()[0].split()[:4] == [
            "FAIL",
            "email_batch-en-1",
            "action=email_batch",
            "recipient",
        ], out
        assert "Scripted Customer" not in out


class NoModeModel(ScriptedModel):
    """The staging failure: the planner's email_batch leaves ``mode`` out."""

    def _proposal(self, case: smoke.SmokeCase) -> dict[str, Any]:
        proposal = super()._proposal(case)
        proposal.pop("mode", None)
        return proposal


def test_smoke_checks_the_mode_the_server_drafts_with_and_marks_an_inferred_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A batch with no planner mode is checked on the mode the server fills in from the
    user's words ("the same email" is shared, a person's own details personalised), and the
    line says so: the prompt's own rate stays visible."""
    for case_id in (*BATCH_CASES, "email_batch_group-ms-1"):
        assert smoke.main(["--case", case_id], client=NoModeModel()) == 0, case_id
        out = capsys.readouterr().out
        assert f"PASS {case_id} action=email_batch mode_inferred" in out, (case_id, out)
        _assert_content_free(out)
        # A planner that sets the mode is not marked.
        assert smoke.main(["--case", case_id], client=ScriptedModel()) == 0
        assert "mode_inferred" not in capsys.readouterr().out

    class WrongMode(ScriptedModel):
        def _proposal(self, case: smoke.SmokeCase) -> dict[str, Any]:
            proposal = super()._proposal(case)
            if proposal["action"] == "email_batch":
                proposal["mode"] = "personalised"
            return proposal

    # The planner's own (wrong) choice is kept, so it still fails.
    assert smoke.main(["--case", "email_batch-en-1"], client=WrongMode()) == 1
    assert "FAIL email_batch-en-1 action=email_batch fields" in capsys.readouterr().out
