"""The assistant's "mark my task done" card (slice 1c-ii).

The planner is scripted (as in tests/test_workspace_planner.py). Steps are written straight into the
0630 tables as the database owner; every card is checked against what the console's own My tasks
shows the same browser, and every completion against the row the console's complete path writes.
The assistant completes only the caller's own open step; it never approves, never writes a note,
and never touches an automatic-check step, a pool step or a step waiting for approval. All names
are synthetic (the repository is public).
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from uuid import UUID

import httpx
import pytest

from tests.conftest import requires_docker
from tests.test_console_services import activate
from tests.test_console_sessions import SessionWorld
from tests.test_workspace_hr_processes import (
    FAR,
    HR,
    _company,
    _days,
    _employee,
    _hr_owner,
    _member,
    _process,
)
from tests.test_workspace_planner import (  # noqa: F401 - fixture import
    _proposal_count,
    _thread,
    _turn,
    planned,
)
from tests.test_workspace_registry import proposals_app  # noqa: F401 - fixture import

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

PROPOSALS = "/console/v1/workspace/proposals"
ENGLISH = "Mark my task done"
MALAY = "Tandakan tugasan saya sebagai selesai"
NONE = {
    "en": "You have no step of your own to mark done. Other steps are done in My tasks.",
    "ms": "Anda tiada langkah sendiri untuk ditandakan selesai. Langkah lain dibuat dalam "
    "My tasks.",
}


async def _assistant_on(w: SessionWorld) -> None:
    await w.q(
        "UPDATE hr_agent_settings SET enabled=true WHERE client_id=%s AND name='hr_assistant'",
        w.tenants[0],
    )


async def _step(
    w: SessionWorld,
    process: UUID,
    ordinal: int,
    title: str,
    employee: UUID,
    *,
    due: date | None = None,
    approval_by: str | None = None,
    auto_check: str | None = None,
    status: str = "todo",
    title_ms: str | None = None,
) -> UUID:
    rows = await w.q(
        "INSERT INTO hr_process_tasks(client_id,process_id,ordinal,title_en,title_ms,owner_role,"
        "assignee_employee_id,anchor,due_offset_days,due_on,status,approval_by,auto_check)"
        " VALUES (%s,%s,%s,%s,%s,'employee',%s,'start_date',0,%s,%s,%s,%s) RETURNING id",
        w.tenants[0], process, ordinal, title, title_ms, employee, due, status, approval_by,
        auto_check,
    )  # fmt: skip
    return rows[0]["id"]


async def _ask(
    w: SessionWorld,
    model: Any,
    browser: httpx.AsyncClient,
    *,
    task: str | None = None,
    language: str = "en",
) -> httpx.Response:
    plan: dict[str, Any] = {"action": "hr_task_complete"}
    if task is not None:
        plan["task"] = task
    model.reply(plan)
    thread = await _thread(w, browser)
    response = await _turn(w, browser, thread, MALAY if language == "ms" else ENGLISH, "hr")
    assert response.status_code == 201, response.text
    return response


def _card(response: httpx.Response) -> dict[str, Any]:
    result = response.json()["result"]
    assert result["action"] == "proposal", result
    assert result["proposal"]["kind"] == "hr_task"
    return result["proposal"]


def _fields(proposal: dict[str, Any]) -> dict[str, str]:
    return {field["label"]: field["value"] for field in proposal["card"]["fields"]}


async def _confirm(w: SessionWorld, browser: httpx.AsyncClient, pid: str) -> httpx.Response:
    return await w.mutate(browser, f"{PROPOSALS}/{pid}/confirm")


async def _row(w: SessionWorld, task: UUID) -> dict[str, Any]:
    rows = await w.q(
        "SELECT status, version, completed_by, note FROM hr_process_tasks WHERE id=%s", task
    )
    return rows[0]


async def test_one_own_step_becomes_a_card_that_completes_it(planned) -> None:  # noqa: F811
    w, model = planned
    c = await _company(w)
    await _assistant_on(w)
    # Aina's only step of her own is "Submit bank details" (her HR steps are the HR team's).
    bank = (await w.q("SELECT id FROM hr_process_tasks WHERE title_en='Submit bank details'"))[0]
    proposal = _card(await _ask(w, model, c.person))
    assert proposal["card"]["title"] == "Mark a step done"
    assert _fields(proposal) == {
        "Step": "Submit bank details",
        "For": "Aina Rahman (onboarding)",
        "Due": "1 December 2099",
        "After you confirm": "The step is marked done.",
    }
    version = (await _row(w, bank["id"]))["version"]
    assert proposal["card"]["expected_version"] == str(version)
    assert (await _row(w, bank["id"]))["status"] == "todo"  # nothing changes before Confirm
    done = await _confirm(w, c.person, proposal["id"])
    assert done.status_code == 200, done.text
    receipt = done.json()["result"]
    assert done.json()["status"] == "executed"
    assert receipt["operation"] == "task.complete" and receipt["task_status"] == "done"
    assert set(receipt) == {
        "operation", "task_id", "process_id", "task_status", "version", "process_status",
        "status", "duplicate",
    }  # fmt: skip
    row = await _row(w, bank["id"])
    assert (row["status"], row["version"], row["note"]) == ("done", version + 1, None)
    again = await _confirm(w, c.person, proposal["id"])
    assert again.status_code == 200 and again.json() == done.json()
    assert (await _row(w, bank["id"]))["version"] == version + 1
    # The console agrees: the step has left My tasks.
    mine = await c.person.get(f"{HR}/tasks/mine")
    assert [t["title_en"] for t in mine.json()["items"]] == []


async def test_the_card_reads_in_malay_with_the_malay_title(planned) -> None:  # noqa: F811
    w, model = planned
    c = await _company(w)
    await _assistant_on(w)
    proposal = _card(await _ask(w, model, c.person, language="ms"))
    assert proposal["card"]["title"] == "Tandakan langkah sebagai selesai"
    assert _fields(proposal) == {
        "Langkah": "Hantar butiran bank",
        "Untuk": "Aina Rahman (onboarding)",
        "Tarikh akhir": "1 Disember 2099",
        "Selepas anda mengesahkan": "Langkah itu ditandakan selesai.",
    }
    assert proposal["card"]["language"] == "ms"


async def test_a_step_with_an_approver_says_it_then_waits(planned) -> None:  # noqa: F811
    w, model = planned
    c = await _company(w)
    await _assistant_on(w)
    aina = c.people["aina"]
    await w.q(
        "UPDATE hr_process_tasks SET approval_by='manager' WHERE title_en='Submit bank details'"
    )
    proposal = _card(await _ask(w, model, c.person))
    assert _fields(proposal)["After you confirm"] == (
        "The step then waits for approval by the manager."
    )
    done = await _confirm(w, c.person, proposal["id"])
    assert done.status_code == 200, done.text
    assert done.json()["result"]["task_status"] == "awaiting_approval"
    # It is the console's own state: waiting, not done, and not offered again.
    rows = await w.q("SELECT status FROM hr_process_tasks WHERE title_en='Submit bank details'")
    assert rows == [{"status": "awaiting_approval"}]
    assert aina  # the step's assignee is the caller
    again = _first_sentence(await _ask(w, model, c.person))
    assert again == NONE["en"]


def _first_sentence(response: httpx.Response) -> str:
    result = response.json()["result"]
    assert result["action"] == "answer", result
    return result["markdown"]


async def test_nothing_of_the_callers_own_gets_one_sentence_and_no_card(planned) -> None:  # noqa: F811
    w, model = planned
    c = await _company(w)
    await _assistant_on(w)
    # The HR owner sees four steps in My tasks, none of them assigned to them (pool steps and
    # other people's steps); a member with no employee link sees none.
    mine = await c.hr.get(f"{HR}/tasks/mine")
    assert len(mine.json()["items"]) == 4
    for browser in (c.hr, c.stranger):
        assert _first_sentence(await _ask(w, model, browser)) == NONE["en"]
    assert _first_sentence(await _ask(w, model, c.stranger, language="ms")) == NONE["ms"]
    assert await _proposal_count(w) == 0


async def test_automatic_check_pool_and_waiting_steps_are_not_offered(planned) -> None:  # noqa: F811
    w, model = planned
    await activate(w, w.tenants[0], "hr")
    await _assistant_on(w)
    await _hr_owner(w)
    kumar = await _employee(w, "Kumar Raj")
    process = await _process(w, kumar)
    await _step(w, process, 1, "Link your account", kumar, auto_check="account_linked")
    await _step(w, process, 2, "Wait for approval", kumar, status="awaiting_approval",
                approval_by="hr")  # fmt: skip
    await _step(w, process, 3, "Already done", kumar, status="done")
    browser = await _member(w, kumar)
    assert _first_sentence(await _ask(w, model, browser)) == NONE["en"]
    assert await _proposal_count(w) == 0


async def test_several_steps_ask_which_and_the_pick_gives_the_card(planned) -> None:  # noqa: F811
    w, model = planned
    await activate(w, w.tenants[0], "hr")
    await _assistant_on(w)
    await _hr_owner(w)
    kumar = await _employee(w, "Kumar Raj")
    process = await _process(w, kumar)
    first = await _step(w, process, 1, "Collect the laptop", kumar, due=_days(1))
    second = await _step(w, process, 2, "Sign the policy", kumar, due=FAR)
    browser = await _member(w, kumar)
    asked = (await _ask(w, model, browser)).json()["result"]
    assert asked["action"] == "clarification"
    assert asked["question"] == "Which step do you mean?"
    assert [option["value"] for option in asked["options"]] == [str(first), str(second)]
    assert asked["options"][0]["label"] == (
        "Collect the laptop — onboarding for Kumar Raj, due tomorrow"
    )
    # The turn row keeps the ids and a counter only: no title, name or date.
    stored = (await w.q("SELECT action, result FROM console_workspace_turns"))[0]
    assert stored["action"] == "hr_task_complete"
    assert [option["label"] for option in stored["result"]["options"]] == ["Step 1", "Step 2"]
    for word in ("Collect", "Sign", "Kumar"):
        assert word not in json.dumps(stored["result"])
    # A typed word narrows to one step; a picked id does too; an unknown word asks again.
    typed = _card(await _ask(w, model, browser, task="policy"))
    assert _fields(typed)["Step"] == "Sign the policy"
    picked = _card(await _ask(w, model, browser, task=str(first)))
    assert _fields(picked)["Step"] == "Collect the laptop"
    unknown = (await _ask(w, model, browser, task="nonsense")).json()["result"]
    assert unknown["action"] == "clarification" and len(unknown["options"]) == 2
    # Malay: the same question in Malay, with the Malay title when the template has one.
    malay = (await _ask(w, model, browser, language="ms")).json()["result"]
    assert malay["question"] == "Langkah yang mana satu?"


async def test_a_changed_or_finished_step_makes_the_card_stale(planned) -> None:  # noqa: F811
    w, model = planned
    c = await _company(w)
    await _assistant_on(w)
    card = _card(await _ask(w, model, c.person))
    await w.q("UPDATE hr_process_tasks SET status='skipped', version=version+1"
              " WHERE title_en='Submit bank details'")  # fmt: skip
    stale = await _confirm(w, c.person, card["id"])
    assert stale.status_code == 409 and stale.json()["detail"] == "proposal_stale"
    assert await w.q(
        "SELECT status FROM hr_process_tasks WHERE title_en='Submit bank details'"
    ) == [{"status": "skipped"}]


async def test_the_card_is_the_callers_own_and_the_switch_applies(planned) -> None:  # noqa: F811
    w, model = planned
    c = await _company(w)
    # The tenant's hr_assistant switch is off: the assistant is refused for the whole turn.
    model.reply({"action": "hr_task_complete"})
    thread = await _thread(w, c.person)
    off = await _turn(w, c.person, thread, ENGLISH, "hr")
    assert off.status_code == 409 and off.json() == {"detail": "agent_disabled"}
    await _assistant_on(w)
    card = _card(await _ask(w, model, c.person))
    # Someone else cannot confirm another person's card.
    other = await c.assignee.post(f"{PROPOSALS}/{card['id']}/confirm")
    assert other.status_code in (403, 404, 409)
    row = await w.q("SELECT status FROM hr_process_tasks WHERE title_en='Submit bank details'")
    assert row == [{"status": "todo"}]


async def test_without_the_hr_package_the_assistant_says_so(planned) -> None:  # noqa: F811
    w, model = planned
    c = await _company(w, hr=False)
    sentence = _first_sentence(await _ask(w, model, c.person))
    assert sentence == (
        "HR processes are part of the HR service, which is not active for this workspace."
    )
    assert await _proposal_count(w) == 0
