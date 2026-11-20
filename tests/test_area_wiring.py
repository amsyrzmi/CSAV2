"""The area switch's plumbing: scoped prompt per call, and the widening safety net.

A fake ``JsonCompletionClient`` records the system prompt of every call and returns scripted
planner output, so none of this needs a database or a model.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from csa.console.contracts import Role
from csa.decide.groq import GroqError
from csa.workspace.planner import (
    SYSTEM_PROMPT,
    PlannerFacts,
    PlannerInvalidOutput,
    PlannerOutput,
    WorkspacePlanner,
    planner_prompt,
    scoped_prompt,
)
from csa.workspace.prompt_areas import Area
from csa.workspace.rules import CONTENT_NOTE
from csa.workspace.service import _corrected_plan, _Planned
from pydantic import BaseModel, ValidationError

ANSWER = {"action": "answer", "markdown": "ok"}
OUT_OF_AREA = {
    "action": "email_compose",
    "recipient_address": "a@example.test",
    "subject": "Hello",
}
ASKS_SUBJECT = {"action": "clarification", "question": "What subject?", "options": []}


class FakeClient:
    """Records ``system`` and ``user`` of each call; ``!code`` entries raise a GroqError."""

    model = "fake"

    def __init__(self, *replies: dict[str, Any] | str) -> None:
        self.replies: list[dict[str, Any] | str] = list(replies)
        self.systems: list[str] = []
        self.users: list[str] = []

    async def json_completion(
        self, schema: type[Any], *, system: str, user: str, timeout: float, max_tokens: int = 1500
    ) -> Any:
        self.systems.append(system)
        self.users.append(user)
        reply = self.replies.pop(0)
        if isinstance(reply, str):
            raise GroqError(reply)
        assert issubclass(schema, BaseModel)
        try:
            return schema.model_validate_json(json.dumps({"proposal": reply}))
        except ValidationError as exc:
            raise GroqError("groq_invalid_output") from exc


def _facts(areas: frozenset[Area] | None) -> PlannerFacts:
    return PlannerFacts(
        role=Role.TENANT_STAFF,
        packages=[],
        page_context=None,
        record_summary=None,
        language="en",
        areas=areas,
    )


async def _keep(_: Any) -> _Planned:
    return _Planned()


HR: frozenset[Area] = frozenset({"hr"})


def test_areas_never_appear_in_the_model_facts_json() -> None:
    scopes: tuple[frozenset[Area] | None, ...] = (HR, None)
    for areas in scopes:
        prompt = planner_prompt("hello", _facts(areas))
        assert "areas" not in prompt
        assert '"areas"' not in _facts(areas).model_dump_json()


async def test_scoped_prompt_is_used_when_areas_are_set() -> None:
    client = FakeClient(ANSWER)
    await WorkspacePlanner(client).plan("hello", _facts(HR))
    assert client.systems == [scoped_prompt(HR)]
    assert client.systems[0] != SYSTEM_PROMPT


async def test_full_prompt_is_used_when_areas_is_none() -> None:
    client = FakeClient(ANSWER)
    await WorkspacePlanner(client).plan("hello", _facts(None))
    assert client.systems == [SYSTEM_PROMPT]


async def test_widen_uses_the_full_prompt_even_with_areas() -> None:
    client = FakeClient(ANSWER)
    await WorkspacePlanner(client).plan("hello", _facts(HR), widen=True)
    assert client.systems == [SYSTEM_PROMPT]


async def test_out_of_area_action_gets_one_full_prompt_replan() -> None:
    client = FakeClient(OUT_OF_AREA, ANSWER)
    proposal, _ = await _corrected_plan(WorkspacePlanner(client), "hello", _facts(HR), _keep)
    assert proposal.action == "answer"
    assert client.systems == [scoped_prompt(HR), SYSTEM_PROMPT]


async def test_in_area_action_is_not_widened() -> None:
    client = FakeClient(ANSWER)
    proposal, _ = await _corrected_plan(WorkspacePlanner(client), "hello", _facts(HR), _keep)
    assert proposal.action == "answer" and len(client.systems) == 1


async def test_invalid_output_with_no_named_reason_widens_once() -> None:
    client = FakeClient("groq_invalid_output", ANSWER)
    proposal, _ = await _corrected_plan(WorkspacePlanner(client), "hello", _facts(HR), _keep)
    assert proposal.action == "answer"
    assert client.systems == [scoped_prompt(HR), SYSTEM_PROMPT]


async def test_invalid_output_on_the_full_prompt_does_not_widen() -> None:
    client = FakeClient("groq_invalid_output", ANSWER)
    with pytest.raises(PlannerInvalidOutput):
        await _corrected_plan(WorkspacePlanner(client), "hello", _facts(None), _keep)
    assert len(client.systems) == 1


async def test_widening_is_final() -> None:
    client = FakeClient(OUT_OF_AREA, OUT_OF_AREA, ANSWER)
    proposal, _ = await _corrected_plan(WorkspacePlanner(client), "hello", _facts(HR), _keep)
    assert proposal.action == "email_compose"
    assert client.systems == [scoped_prompt(HR), SYSTEM_PROMPT]
    assert len(client.replies) == 1  # no third call


async def test_a_failed_widening_call_propagates() -> None:
    client = FakeClient("groq_invalid_output", "groq_invalid_output")
    with pytest.raises(PlannerInvalidOutput):
        await _corrected_plan(WorkspacePlanner(client), "hello", _facts(HR), _keep)
    assert len(client.systems) == 2


async def test_content_note_replan_keeps_the_scoped_prompt() -> None:
    email: frozenset[Area] = frozenset({"email"})
    client = FakeClient(ASKS_SUBJECT, ANSWER)
    planner = WorkspacePlanner(client)
    proposal, _ = await _corrected_plan(
        planner, "send email to danial@example.test", _facts(email), _keep
    )
    assert proposal.action == "answer"
    assert client.systems == [scoped_prompt(email), scoped_prompt(email)]
    assert client.users[1].endswith(f"<correction>\n{CONTENT_NOTE}\n</correction>")


async def test_widen_disabled_never_widens() -> None:
    client = FakeClient(OUT_OF_AREA, ANSWER)
    proposal, _ = await _corrected_plan(
        WorkspacePlanner(client), "hello", _facts(HR), _keep, widen_enabled=False
    )
    assert proposal.action == "email_compose"
    assert client.systems == [scoped_prompt(HR)]
    broken = FakeClient("groq_invalid_output", ANSWER)
    with pytest.raises(PlannerInvalidOutput):
        await _corrected_plan(
            WorkspacePlanner(broken), "hello", _facts(HR), _keep, widen_enabled=False
        )
    assert len(broken.systems) == 1


async def test_a_planner_without_the_widen_keyword_still_works_when_not_widening() -> None:
    """Only a widening call passes ``widen``: a fake planner written before it keeps working."""

    class OldPlanner:
        def __init__(self) -> None:
            self.calls = 0

        async def plan(self, message: str, facts: PlannerFacts, *, note: str | None = None) -> Any:
            self.calls += 1
            return PlannerOutput.model_validate({"proposal": ANSWER}).proposal

    old = OldPlanner()
    for areas in (None, HR):
        proposal, _ = await _corrected_plan(old, "hello", _facts(areas), _keep)  # type: ignore[arg-type]
        assert proposal.action == "answer"
    assert old.calls == 2


def test_scoped_facts_keep_only_the_selected_areas_payloads() -> None:
    from csa.workspace.planner import (
        BookingFacts,
        EmployeeFact,
        ManagerFact,
        TeamFact,
    )

    def loaded(areas: frozenset[Area] | None) -> dict[str, Any]:
        facts = _facts(areas).model_copy(
            update={
                "my_manager": ManagerFact(employee_id=uuid4(), reason=None),
                "my_team": TeamFact(reports=[], skipped=[], reason="no_reports"),
                "my_employee": EmployeeFact(linked=True, can_request_training=True, reason=None),
                "people_access": "hr",
                "bookings": BookingFacts(
                    now_local="2026-11-20 10:00", weekday="Friday", timezone="UTC", can_change=True
                ),
                "mailbox_threads": [],
                "page_hint": "hint",
                "record_summary": "summary",
            }
        )
        user = planner_prompt("hello", facts)
        return json.loads(user[user.index("<facts>") + 7 : user.index("</facts>")])

    present = lambda data: {  # noqa: E731
        key for key in ("my_manager", "my_team", "my_employee", "people_access", "bookings",
                        "mailbox_threads") if data.get(key) is not None
    }  # fmt: skip
    everything = {"my_manager", "my_team", "my_employee", "people_access", "bookings",
                  "mailbox_threads"}  # fmt: skip
    assert present(loaded(None)) == everything
    # The company's clock (bookings) is the only date the model has: it travels whenever any area
    # is selected, so "last day next Friday" on an HR turn still knows today. Only prose is scoped.
    assert present(loaded(frozenset({"hr"}))) == {"my_manager", "people_access", "bookings"}
    assert present(loaded(frozenset({"email"}))) == {
        "my_manager", "my_team", "mailbox_threads", "bookings"
    }  # fmt: skip
    assert present(loaded(frozenset({"training"}))) == {"my_employee", "bookings"}
    assert present(loaded(frozenset({"bookings"}))) == {"bookings"}
    assert present(loaded(frozenset({"inbox"}))) == {"bookings"}
    assert present(loaded(frozenset({"products", "hr"}))) == {
        "my_manager", "people_access", "bookings"
    }  # fmt: skip
    # An hr turn carries the clock itself, not only the key.
    hr_clock = loaded(frozenset({"hr"}))["bookings"]
    assert hr_clock["now_local"] == "2026-11-20 10:00" and hr_clock["weekday"] == "Friday"
    # The general facts always travel.
    data = loaded(frozenset({"inbox"}))
    assert data["page_hint"] == "hint" and data["record_summary"] == "summary"


def _scoped_turn_events(logs: list[Any]) -> list[dict[str, Any]]:
    return [entry for entry in logs if entry["event"] == "workspace_scoped_turn"]


async def test_a_scoped_turn_logs_its_areas_the_widen_cause_and_the_final_action() -> None:
    from structlog.testing import capture_logs

    secret = "SECRET-MESSAGE-TEXT-4711"
    hr_email: frozenset[Area] = frozenset({"hr", "email"})
    cases = (
        (FakeClient(ANSWER), hr_email, "none", "answer"),
        (FakeClient(OUT_OF_AREA, ANSWER), HR, "out_of_area", "answer"),
        (FakeClient("groq_invalid_output", ANSWER), HR, "invalid_output", "answer"),
    )
    for client, areas, cause, action in cases:
        with capture_logs() as logs:
            await _corrected_plan(WorkspacePlanner(client), secret, _facts(areas), _keep)
        events = _scoped_turn_events(logs)
        assert len(events) == 1, cause
        assert events[0]["areas"] == sorted(areas), cause
        assert events[0]["widen"] == cause
        assert events[0]["action"] == action
        # Nothing but the three fields: no message text, no ids.
        assert set(events[0]) == {"event", "log_level", "areas", "widen", "action"}
        assert secret not in repr(logs)


async def test_a_scoped_turn_with_widening_off_logs_the_out_of_area_action_as_final() -> None:
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        await _corrected_plan(
            WorkspacePlanner(FakeClient(OUT_OF_AREA)),
            "hello",
            _facts(HR),
            _keep,
            widen_enabled=False,
        )
    (event,) = _scoped_turn_events(logs)
    assert (event["widen"], event["action"]) == ("none", "email_compose")


async def test_a_full_prompt_turn_logs_nothing_about_areas() -> None:
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        await _corrected_plan(WorkspacePlanner(FakeClient(ANSWER)), "hello", _facts(None), _keep)
    assert _scoped_turn_events(logs) == []


async def test_a_scoped_turn_the_planner_refuses_logs_it_as_refused() -> None:
    from structlog.testing import capture_logs

    client = FakeClient("groq_invalid_output", "groq_invalid_output")
    with capture_logs() as logs, pytest.raises(PlannerInvalidOutput):
        await _corrected_plan(WorkspacePlanner(client), "hello", _facts(HR), _keep)
    (event,) = _scoped_turn_events(logs)
    assert (event["widen"], event["action"]) == ("invalid_output", "refused")
