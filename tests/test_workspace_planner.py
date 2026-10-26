"""Planner-driven workspace turns: every kind round-trips and every gate runs server-side."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
import pytest
from csa.decide.groq import GroqError
from csa.workspace.planner import PlannerFacts, PlannerOutput, WorkspacePlanner, detect_language
from pydantic import BaseModel, ValidationError

from tests.conftest import requires_docker
from tests.test_console_services import activate
from tests.test_console_sessions import SessionWorld
from tests.test_workspace_registry import (  # noqa: F401 - fixture import
    _held_approval,
    _hr_member,
    proposals_app,
)

pytest_plugins = ["tests.test_console_sessions"]

THREADS = "/console/v1/workspace/conversations"
PROPOSALS = "/console/v1/workspace/proposals"
SEEDED_EMAIL = "ada.directory@example.test"


class ScriptedCompletion:
    """Scripted planner model: parses canned JSON exactly as GroqClient does."""

    model = "scripted"

    def __init__(self) -> None:
        self.replies: list[str | Callable[[dict[str, Any]], dict[str, Any]]] = []
        self.calls: list[tuple[str, str, str]] = []

    def fail(self, code: str) -> None:
        self.replies.append(f"!{code}")

    def reply(self, proposal: dict[str, Any]) -> None:
        self.replies.append(json.dumps({"proposal": proposal}))

    def respond(self, decide: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        """Reply computed from the prompt's facts, so a test can prove what the prompt held."""
        self.replies.append(decide)

    async def json_completion(
        self, schema: type[Any], *, system: str, user: str, timeout: float, max_tokens: int = 1500
    ) -> Any:
        self.calls.append((schema.__name__, system, user))
        raw = self.replies.pop(0)
        if callable(raw):
            start = user.index("<facts>") + len("<facts>")
            raw = json.dumps({"proposal": raw(json.loads(user[start : user.index("</facts>")]))})
        if raw.startswith("!"):
            raise GroqError(raw[1:])
        assert issubclass(schema, BaseModel)
        try:
            return schema.model_validate_json(raw)
        except ValidationError as exc:
            raise GroqError("groq_invalid_output") from exc

    def facts(self, index: int = -1) -> dict[str, Any]:
        user = self.calls[index][2]
        start = user.index("<facts>") + len("<facts>")
        return json.loads(user[start : user.index("</facts>")])


@pytest.fixture
async def planned(proposals_app) -> tuple[SessionWorld, ScriptedCompletion]:  # noqa: F811
    w, _ = proposals_app
    model = ScriptedCompletion()
    w.app.state.workspace_planner = WorkspacePlanner(model)
    return w, model


async def _thread(w: SessionWorld, browser: httpx.AsyncClient) -> str:
    created = await w.mutate(browser, THREADS, {"request_key": str(uuid4())})
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def _turn(
    w: SessionWorld,
    browser: httpx.AsyncClient,
    thread: str,
    body: str,
    tab: str,
    *,
    record: dict[str, Any] | None = None,
    key: str | None = None,
) -> httpx.Response:
    context: dict[str, Any] = {"tab": tab}
    if record is not None:
        context["record"] = record
    return await w.mutate(
        browser,
        f"{THREADS}/{thread}/turns",
        {"request_key": key or str(uuid4()), "body": body, "page_context": context},
    )


async def _turn_rows(w: SessionWorld) -> list[dict[str, Any]]:
    return await w.q("SELECT action, page_key, result FROM console_workspace_turns ORDER BY id")


async def _proposal_count(w: SessionWorld) -> int:
    return (await w.q("SELECT count(*) n FROM console_proposals"))[0]["n"]


async def _mail_ready(w: SessionWorld, *packages: str) -> httpx.AsyncClient:
    """A tenant_staff browser with staff_email (plus ``packages``) and a connected mailbox."""
    tenant = w.tenants[0]
    await activate(w, tenant, "staff_email", *packages)
    await w.q("UPDATE email_settings SET enabled=true WHERE client_id=%s", tenant)
    browser, _ = await w.member(role="tenant_staff")
    headers = {**w.headers(browser), "Idempotency-Key": str(uuid4())}
    started = await browser.post("/console/v1/email/mailbox/connect", headers=headers)
    assert started.status_code == 200, started.text
    state = parse_qs(urlsplit(started.json()["authorization_url"]).query)["state"][0]
    done = await browser.get(f"/console/v1/email/oauth/callback?code=abc&state={state}")
    assert done.status_code == 303, done.text
    return browser


def test_detect_language_recognises_malay() -> None:
    assert detect_language("Siapa pengurus saya? Tolong hantar emel kepada dia") == "ms"
    assert detect_language("Who is my manager? Please email them") == "en"


def test_planner_output_rejects_legacy_actions() -> None:
    with pytest.raises(ValidationError):
        PlannerOutput.model_validate_json(
            json.dumps({"proposal": {"action": "page_guidance", "page": "workspace_home"}})
        )


@requires_docker
async def test_answer_navigate_and_clarification_round_trip(planned) -> None:
    w, model = planned
    await activate(w, w.tenants[0], "hr")
    browser, _ = await w.member(role="tenant_staff")
    thread = await _thread(w, browser)
    model.reply({"action": "answer", "markdown": "Here you go.", "reads": ["my_packages"]})
    answered = await _turn(w, browser, thread, "What can I use?", "overview")
    assert answered.status_code == 201, answered.text
    result = answered.json()["result"]
    assert result["action"] == "answer" and result["markdown"].startswith("Here you go.")
    assert "hr (active)" in result["markdown"]
    model.reply({"action": "navigate", "tab": "hr", "section": "training"})
    moved = await _turn(w, browser, thread, "Open HR", "overview")
    assert moved.status_code == 201, moved.text
    assert moved.json()["result"] == {
        "action": "navigate", "tab": "hr", "section": "training", "record": None, "resume": False,
    }  # fmt: skip
    model.reply(
        {
            "action": "clarification",
            "question": "Which request?",
            "options": [{"label": "First aid", "value": "first aid"}],
        }
    )
    asked = await _turn(w, browser, thread, "Submit it", "hr")
    assert asked.status_code == 201, asked.text
    assert asked.json()["result"]["action"] == "clarification"
    assert asked.json()["result"]["options"] == [{"label": "First aid", "value": "first aid"}]
    rows = await _turn_rows(w)
    assert [(r["action"], r["page_key"]) for r in rows] == [
        ("answer", "overview"), ("navigate", "overview"), ("clarification", "hr"),
    ]  # fmt: skip
    history = await browser.get(f"{THREADS}/{thread}/turns")
    assert [t["result"]["action"] for t in history.json()["items"]] == [
        "clarification", "navigate", "answer",
    ]  # fmt: skip
    facts = model.facts(0)
    assert facts["role"] == "tenant_staff" and facts["language"] == "en"
    assert {"key": "hr", "status": "active"} in facts["packages"]


@requires_docker
async def test_navigate_to_locked_tab_is_answered_not_refused(planned) -> None:
    w, model = planned
    browser, _ = await w.member(role="tenant_staff")
    thread = await _thread(w, browser)
    model.reply({"action": "navigate", "tab": "email"})
    turn = await _turn(w, browser, thread, "Take me to email", "overview")
    assert turn.status_code == 201, turn.text
    result = turn.json()["result"]
    assert result["action"] == "answer"
    assert "not active" in result["markdown"] and "Staff email" in result["markdown"]


@requires_docker
async def test_directory_lookup_and_prompt_never_carries_work_email(planned) -> None:
    w, model = planned
    tenant = w.tenants[0]
    await activate(w, tenant, "hr")
    browser, me = await w.member(role="tenant_staff")
    ada, staff = uuid4(), uuid4()
    await w.q(
        "INSERT INTO hr_employees(client_id,id,display_name,job_title,work_email,manager_id)"
        " VALUES (%s,%s,'Ada Directory','Engineer',%s,NULL),"
        " (%s,%s,'Synthetic Staff',NULL,'staff.directory@example.test',%s)",
        tenant, ada, SEEDED_EMAIL, tenant, staff, ada,
    )  # fmt: skip
    await w.q(
        "INSERT INTO hr_employee_accounts(client_id,user_id,employee_id) VALUES (%s,%s,%s)",
        tenant, me["user"]["id"], staff,
    )  # fmt: skip
    thread = await _thread(w, browser)
    model.reply({"action": "directory_lookup", "query": "Ada"})
    record = {"kind": "employee", "id": str(ada)}
    found = await _turn(w, browser, thread, "Find Ada", "hr", record=record)
    assert found.status_code == 201, found.text
    candidates = found.json()["result"]["candidates"]
    assert found.json()["result"]["action"] == "directory_lookup"
    assert [(c["display_name"], c["work_email"]) for c in candidates] == [
        ("Ada Directory", SEEDED_EMAIL)
    ]
    model.reply({"action": "answer", "markdown": "Your manager:", "reads": ["my_profile"]})
    mine = await _turn(w, browser, thread, "Who is my manager?", "hr", record=record)
    assert mine.status_code == 201, mine.text
    assert "Ada Directory" in mine.json()["result"]["markdown"]
    for _, system, user in model.calls:
        assert SEEDED_EMAIL not in system + user
        assert "staff.directory@example.test" not in system + user
        assert "Ada Directory" not in system + user
    audits = await w.q("SELECT details FROM console_audit WHERE action='workspace.people.lookup'")
    assert [a["details"] for a in audits] == [{"candidates": 1}]


@requires_docker
async def test_training_submit_turn_creates_a_requested_proposal(planned) -> None:
    w, model = planned
    browser = await _hr_member(w)
    thread = await _thread(w, browser)
    model.reply(
        {
            "action": "training_submit",
            "title": "First aid",
            "learning_objective": "Certify in first aid",
        }
    )
    turn = await _turn(w, browser, thread, "Request first aid training", "hr")
    assert turn.status_code == 201, turn.text
    result = turn.json()["result"]
    assert result["action"] == "proposal"
    proposal = result["proposal"]
    assert proposal["kind"] == "training_submit" and proposal["status"] == "requested"
    rows = await w.q("SELECT thread_id, status, origin FROM console_proposals")
    assert rows == [{"thread_id": UUID(thread), "status": "requested", "origin": "assistant"}]
    assert [(r["action"], r["page_key"]) for r in await _turn_rows(w)] == [
        ("training_submit", "hr")
    ]
    assert (await w.q("SELECT count(*) n FROM hr_training_requests"))[0]["n"] == 0
    done = await w.mutate(browser, f"{PROPOSALS}/{proposal['id']}/confirm")
    assert done.status_code == 200, done.text
    assert done.json()["result"]["request"]["status"] == "submitted"


@requires_docker
async def test_training_submit_without_hr_is_refused_before_any_row(planned) -> None:
    w, model = planned
    browser, _ = await w.member(role="tenant_staff")
    thread = await _thread(w, browser)
    model.reply({"action": "training_submit", "title": "First aid", "learning_objective": "Go"})
    turn = await _turn(w, browser, thread, "Request first aid training", "hr")
    assert turn.status_code == 403 and turn.json()["detail"] == "package_inactive"
    assert await _proposal_count(w) == 0
    assert await _turn_rows(w) == []


@requires_docker
async def test_training_submit_turn_with_hr_assistant_off_is_refused_before_any_row(
    planned,
) -> None:
    w, model = planned
    browser = await _hr_member(w, switch=False)
    thread = await _thread(w, browser)
    model.reply({"action": "training_submit", "title": "First aid", "learning_objective": "Go"})
    turn = await _turn(w, browser, thread, "Request first aid training", "hr")
    assert turn.status_code == 409 and turn.json()["detail"] == "agent_disabled"
    assert await _proposal_count(w) == 0
    assert await _turn_rows(w) == []


@requires_docker
async def test_assistant_training_proposal_rechecks_the_switch_at_confirm(planned) -> None:
    w, model = planned
    browser = await _hr_member(w)
    thread = await _thread(w, browser)
    model.reply({"action": "training_submit", "title": "First aid", "learning_objective": "Go"})
    turn = await _turn(w, browser, thread, "Request first aid training", "hr")
    assert turn.status_code == 201, turn.text
    proposal = turn.json()["result"]["proposal"]
    await w.q(
        "UPDATE hr_agent_settings SET enabled=false WHERE client_id=%s AND name='hr_assistant'",
        w.tenants[0],
    )
    done = await w.mutate(browser, f"{PROPOSALS}/{proposal['id']}/confirm")
    assert done.status_code == 409 and done.json()["detail"] == "agent_disabled"
    assert (await w.q("SELECT count(*) n FROM hr_training_requests"))[0]["n"] == 0


@requires_docker
async def test_inbox_decision_turn_uses_the_redacted_record_summary(planned) -> None:
    w, model = planned
    approval, conversation_id, version = await _held_approval(w)
    browser, _ = await w.member(role="tenant_staff")
    thread = await _thread(w, browser)
    model.reply(
        {
            "action": "inbox_decision",
            "approval_id": approval,
            "conversation_id": conversation_id,
            "decision": "approve",
            "expected_version": version,
        }
    )
    record = {"kind": "approval", "id": str(approval)}
    turn = await _turn(w, browser, thread, "Approve this reply", "inbox", record=record)
    assert turn.status_code == 201, turn.text
    proposal = turn.json()["result"]["proposal"]
    assert proposal["kind"] == "inbox_decision" and proposal["status"] == "requested"
    summary = model.facts()["record_summary"]
    assert str(conversation_id) in summary and f"inbox_version {version}" in summary
    draft = (await w.q("SELECT draft FROM approvals WHERE id=%s", approval))[0]["draft"]
    assert draft not in model.calls[-1][2]


@requires_docker
async def test_email_compose_turn_drafts_and_proposes_export(planned) -> None:
    w, model = planned
    tenant = w.tenants[0]
    browser = await _mail_ready(w, "hr")
    ada = uuid4()
    await w.q(
        "INSERT INTO hr_employees(client_id,id,display_name,work_email) VALUES (%s,%s,%s,%s)",
        tenant, ada, "Ada Directory", SEEDED_EMAIL,
    )  # fmt: skip
    thread = await _thread(w, browser)
    compose = {
        "action": "email_compose",
        "recipient_employee_id": str(ada),
        "subject": "Leave next week",
        "instructions": "Ask to take Friday off",
    }
    model.reply(compose)
    key = str(uuid4())
    turn = await _turn(w, browser, thread, "Email Ada about leave", "email", key=key)
    assert turn.status_code == 201, turn.text
    proposal = turn.json()["result"]["proposal"]
    assert proposal["kind"] == "email_export" and proposal["status"] == "requested"
    fields = {f["label"]: f["value"] for f in proposal["card"]["fields"]}
    assert fields["To"] == SEEDED_EMAIL
    assert [r["action"] for r in await _turn_rows(w)] == ["email_compose"]
    again = await _turn(w, browser, thread, "Email Ada about leave", "email", key=key)
    assert again.status_code == 200 and again.json()["duplicate"] is True
    assert again.json()["result"] == turn.json()["result"]
    assert len(model.calls) == 1  # a replayed turn never re-plans
    assert (await w.q("SELECT count(*) n FROM email_drafts"))[0]["n"] == 1
    assert SEEDED_EMAIL not in model.calls[0][2]


@requires_docker
async def test_changing_tab_cancels_requested_proposals_on_the_thread(planned) -> None:
    w, model = planned
    browser = await _hr_member(w)
    thread = await _thread(w, browser)
    model.reply({"action": "training_submit", "title": "First aid", "learning_objective": "Go"})
    first = await _turn(w, browser, thread, "Request first aid", "hr")
    assert first.status_code == 201, first.text
    model.reply({"action": "answer", "markdown": "Still here."})
    same = await _turn(w, browser, thread, "Thanks", "hr")
    assert same.status_code == 201, same.text
    assert (await w.q("SELECT status FROM console_proposals"))[0]["status"] == "requested"
    model.reply({"action": "answer", "markdown": "Inbox time."})
    moved = await _turn(w, browser, thread, "Hello", "inbox")
    assert moved.status_code == 201, moved.text
    assert (await w.q("SELECT status FROM console_proposals"))[0]["status"] == "cancelled"


@requires_docker
async def test_invalid_planner_output_is_502_and_stores_nothing(planned) -> None:
    w, model = planned
    browser, _ = await w.member(role="tenant_staff")
    thread = await _thread(w, browser)
    model.replies.append("not json at all")
    turn = await _turn(w, browser, thread, "Hello", "overview")
    assert turn.status_code == 502 and turn.json()["detail"] == "planner_invalid_output"
    model.reply({"action": "page_guidance", "page": "workspace_home"})
    legacy = await _turn(w, browser, thread, "Hello", "overview")
    assert legacy.status_code == 502 and legacy.json()["detail"] == "planner_invalid_output"
    await activate(w, w.tenants[0], "hr")
    model.reply({"action": "directory_lookup", "query": "   "})
    blank = await _turn(w, browser, thread, "Everyone", "hr")
    assert blank.status_code == 502 and blank.json()["detail"] == "planner_invalid_output"
    assert await _turn_rows(w) == []


@requires_docker
@pytest.mark.parametrize("code", ["groq_timeout", "groq_transport_error", "groq_http_500"])
async def test_planner_provider_failure_is_503_and_stores_nothing(planned, code: str) -> None:
    w, model = planned
    browser = await _hr_member(w)
    thread = await _thread(w, browser)
    model.fail(code)
    turn = await _turn(w, browser, thread, "Request first aid training", "hr")
    assert turn.status_code == 503 and turn.json()["detail"] == "planner_unavailable"
    assert await _turn_rows(w) == []
    assert await _proposal_count(w) == 0


@requires_docker
async def test_malay_message_sets_language_in_facts(planned) -> None:
    w, model = planned
    browser, _ = await w.member(role="tenant_staff")
    thread = await _thread(w, browser)
    model.reply({"action": "answer", "markdown": "Boleh."})
    turn = await _turn(w, browser, thread, "Siapa pengurus saya? Tolong bantu saya", "overview")
    assert turn.status_code == 201, turn.text
    facts = PlannerFacts.model_validate_json(json.dumps(model.facts()), strict=False)
    assert facts.language == "ms"


@requires_docker
async def test_page_context_turn_without_a_planner_is_503(proposals_app) -> None:  # noqa: F811
    w, _ = proposals_app
    browser, _ = await w.member(role="tenant_staff")
    thread = await _thread(w, browser)
    turn = await _turn(w, browser, thread, "Hello", "overview")
    assert turn.status_code == 503 and turn.json()["detail"] == "planner_unavailable"
    legacy = await w.mutate(
        browser,
        f"{THREADS}/{thread}/turns",
        {"request_key": str(uuid4()), "body": "Help", "page_hint": {"page": "workspace_home"}},
    )
    assert legacy.status_code == 201, legacy.text
    assert legacy.json()["result"]["action"] == "page_guidance"


@requires_docker
async def test_directory_recipient_without_hr_is_refused_before_any_write(planned) -> None:
    w, model = planned
    tenant = w.tenants[0]
    browser = await _mail_ready(w)
    ada = uuid4()
    await w.q(
        "INSERT INTO hr_employees(client_id,id,display_name,work_email) VALUES (%s,%s,%s,%s)",
        tenant, ada, "Ada Directory", SEEDED_EMAIL,
    )  # fmt: skip
    thread = await _thread(w, browser)
    model.reply({"action": "email_compose", "recipient_employee_id": str(ada), "subject": "Leave"})
    turn = await _turn(w, browser, thread, f"Email employee {ada} about leave", "email")
    assert turn.status_code == 403 and turn.json()["detail"] == "package_inactive"
    assert (await w.q("SELECT count(*) n FROM email_drafts"))[0]["n"] == 0
    assert await _proposal_count(w) == 0
    assert await _turn_rows(w) == []


@requires_docker
async def test_directory_lookup_without_hr_is_answered_not_refused(planned) -> None:
    w, model = planned
    browser, _ = await w.member(role="tenant_staff")
    thread = await _thread(w, browser)
    model.reply({"action": "directory_lookup", "query": "Ada"})
    turn = await _turn(w, browser, thread, "Find Ada", "overview")
    assert turn.status_code == 201, turn.text
    result = turn.json()["result"]
    assert result["action"] == "answer" and "not part of" in result["markdown"]
    audits = await w.q(
        "SELECT count(*) n FROM console_audit WHERE action='workspace.people.lookup'"
    )
    assert audits[0]["n"] == 0


@requires_docker
async def test_planner_remembers_the_thread_across_a_directory_pick(planned) -> None:
    """The compose only succeeds because the prompt carries the earlier turn's request."""
    w, model = planned
    tenant = w.tenants[0]
    browser = await _mail_ready(w, "hr")
    ali = uuid4()
    await w.q(
        "INSERT INTO hr_employees(client_id,id,display_name,work_email) VALUES (%s,%s,%s,%s)",
        tenant, ali, "Ali Roster", "ali.roster@example.test",
    )  # fmt: skip
    thread = await _thread(w, browser)
    model.reply({"action": "directory_lookup", "query": "Ali"})
    found = await _turn(w, browser, thread, "Email Ali about the roster", "email")
    assert found.status_code == 201, found.text
    assert [c["employee_id"] for c in found.json()["result"]["candidates"]] == [str(ali)]

    def compose_from_history(facts: dict[str, Any]) -> dict[str, Any]:
        earlier = [t for t in facts["history"] if str(ali) in t["offered_employee_ids"]]
        if not earlier or "roster" not in earlier[-1]["message"]:
            return {"action": "answer", "markdown": "What should the email be about?"}
        return {
            "action": "email_compose",
            "recipient_employee_id": str(ali),
            "subject": "Roster",
            "instructions": "Ask about the roster",
        }

    model.respond(compose_from_history)
    picked = await _turn(w, browser, thread, f"Use employee {ali}", "email")
    assert picked.status_code == 201, picked.text
    proposal = picked.json()["result"]["proposal"]
    assert proposal["kind"] == "email_export"
    fields = {f["label"]: f["value"] for f in proposal["card"]["fields"]}
    assert fields["To"] == "ali.roster@example.test"
    prompt = model.calls[-1][2]
    assert "Email Ali about the roster" in prompt and str(ali) in prompt
    assert "Ali Roster" not in prompt and "ali.roster@example.test" not in prompt
    model.reply({"action": "answer", "markdown": "Done."})
    thanks = await _turn(w, browser, thread, "Thanks", "email")
    assert thanks.status_code == 201, thanks.text
    history = model.facts()["history"]
    assert [t["result"] for t in history] == ["directory_lookup", "proposal"]
    assert history[0]["chosen"] == str(ali)
    assert history[1]["proposal_kind"] == "email_export"


@requires_docker
async def test_clarification_answer_reaches_the_planner_with_its_question(planned) -> None:
    w, model = planned
    browser, _ = await w.member(role="tenant_staff")
    thread = await _thread(w, browser)
    model.reply(
        {
            "action": "clarification",
            "question": "Which tab?",
            "options": [{"label": "HR", "value": "hr"}, {"label": "Inbox", "value": "inbox"}],
        }
    )
    asked = await _turn(w, browser, thread, "Take me there", "overview")
    assert asked.status_code == 201, asked.text
    model.reply({"action": "navigate", "tab": "overview"})
    await _turn(w, browser, thread, "hr", "overview")
    first = model.facts()["history"][0]
    assert first["question"] == "Which tab?" and first["offered_options"] == ["hr", "inbox"]
    assert first["chosen"] == "hr" and first["message"] == "Take me there"


@requires_docker
async def test_email_my_boss_resolves_the_manager_before_planning(planned) -> None:
    w, model = planned
    tenant = w.tenants[0]
    browser = await _mail_ready(w, "hr")
    me = (await browser.get("/console/v1/auth/me")).json()
    boss, staff = uuid4(), uuid4()
    await w.q(
        "INSERT INTO hr_employees(client_id,id,display_name,work_email,manager_id)"
        " VALUES (%s,%s,'Bea Boss','bea.boss@example.test',NULL),"
        " (%s,%s,'Synthetic Staff',NULL,%s)",
        tenant, boss, tenant, staff, boss,
    )  # fmt: skip
    await w.q(
        "INSERT INTO hr_employee_accounts(client_id,user_id,employee_id) VALUES (%s,%s,%s)",
        tenant, me["user"]["id"], staff,
    )  # fmt: skip
    thread = await _thread(w, browser)

    def to_manager(facts: dict[str, Any]) -> dict[str, Any]:
        manager = facts["my_manager"]
        if manager is None or manager["employee_id"] is None:
            return {"action": "answer", "markdown": "No manager is recorded."}
        return {
            "action": "email_compose",
            "recipient_employee_id": manager["employee_id"],
            "subject": "Leave",
            "instructions": "Ask for Friday off",
        }

    model.respond(to_manager)
    turn = await _turn(w, browser, thread, "Email my boss about leave on Friday", "email")
    assert turn.status_code == 201, turn.text
    fields = {f["label"]: f["value"] for f in turn.json()["result"]["proposal"]["card"]["fields"]}
    assert fields["To"] == "bea.boss@example.test"
    assert model.facts()["my_manager"] == {"employee_id": str(boss), "reason": None}
    assert "Bea Boss" not in model.calls[-1][2] and "bea.boss@" not in model.calls[-1][2]
    model.reply({"action": "answer", "markdown": "Hello."})
    await _turn(w, browser, thread, "Hello there", "email")
    assert model.facts()["my_manager"] is None  # only resolved when the message asks


@requires_docker
async def test_email_my_boss_without_a_manager_or_hr_is_answered(planned) -> None:
    w, model = planned
    tenant = w.tenants[0]
    browser = await _mail_ready(w, "hr")
    me = (await browser.get("/console/v1/auth/me")).json()
    staff = uuid4()
    await w.q(
        "INSERT INTO hr_employees(client_id,id,display_name,manager_id)"
        " VALUES (%s,%s,'Synthetic Staff',NULL)",
        tenant, staff,
    )  # fmt: skip
    await w.q(
        "INSERT INTO hr_employee_accounts(client_id,user_id,employee_id) VALUES (%s,%s,%s)",
        tenant, me["user"]["id"], staff,
    )  # fmt: skip
    thread = await _thread(w, browser)
    model.reply({"action": "answer", "markdown": "No manager is recorded for you."})
    turn = await _turn(w, browser, thread, "Email my boss", "email")
    assert turn.status_code == 201, turn.text
    assert turn.json()["result"]["action"] == "answer"
    assert model.facts()["my_manager"] == {"employee_id": None, "reason": "no_manager"}
    await w.q("DELETE FROM console_service_packages WHERE client_id=%s AND package='hr'", tenant)
    model.reply({"action": "answer", "markdown": "HR is not active."})
    await _turn(w, browser, thread, "Siapa bos saya?", "email")
    assert model.facts()["my_manager"] == {"employee_id": None, "reason": "package_inactive"}
    assert await _proposal_count(w) == 0


@requires_docker
async def test_inbox_decision_from_a_published_conversation_context(planned) -> None:
    w, model = planned
    approval, conversation_id, version = await _held_approval(w)
    browser, _ = await w.member(role="tenant_staff")
    thread = await _thread(w, browser)

    def decide(facts: dict[str, Any]) -> dict[str, Any]:
        summary = facts["record_summary"]
        found = re.search(r"approval (\d+) \(status pending\)", summary)
        current = re.search(r"inbox_version (\d+)", summary)
        if found is None or current is None:
            return {"action": "answer", "markdown": "No held reply is pending."}
        return {
            "action": "inbox_decision",
            "approval_id": int(found.group(1)),
            "conversation_id": facts["page_context"]["record"]["id"],
            "decision": "reject",
            "expected_version": int(current.group(1)),
        }

    model.respond(decide)
    record = {"kind": "conversation", "id": conversation_id}
    turn = await _turn(w, browser, thread, "Reject the held reply", "inbox", record=record)
    assert turn.status_code == 201, turn.text
    proposal = turn.json()["result"]["proposal"]
    assert proposal["kind"] == "inbox_decision" and proposal["status"] == "requested"
    summary = model.facts()["record_summary"]
    assert f"approval {approval} (status pending)" in summary
    assert f"inbox_version {version}" in summary
    draft = (await w.q("SELECT draft FROM approvals WHERE id=%s", approval))[0]["draft"]
    assert draft not in model.calls[-1][2]
    done = await w.mutate(browser, f"{PROPOSALS}/{proposal['id']}/confirm")
    assert done.status_code == 200, done.text
    assert (await w.q("SELECT status FROM approvals WHERE id=%s", approval)) == [
        {"status": "rejected"}
    ]
