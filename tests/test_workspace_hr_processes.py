"""Assistant answers about HR processes (slice 1c-i): my tasks, a person's process, who is
onboarding, who is leaving and what is overdue, in English and Malay. Read only.

The planner is scripted (as in tests/test_workspace_planner.py). Processes and tasks are written
straight into the 0630 tables as the database owner (no template, so no trigger starts one), and
every answer is checked against what the console's own HR reads return for the same browser:
the assistant shows no more than the console. All names are synthetic (the repository is public).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from csa.workspace.hr_people import format_day

from tests.conftest import requires_docker
from tests.test_console_services import activate
from tests.test_console_sessions import SessionWorld
from tests.test_workspace_planner import (  # noqa: F401 - fixture import
    _proposal_count,
    _thread,
    _turn,
    planned,
)
from tests.test_workspace_registry import proposals_app  # noqa: F401 - fixture import

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

HR = "/console/v1/hr"
TODAY = datetime.now(ZoneInfo("Asia/Kuala_Lumpur")).date()
FAR = date(2099, 12, 1)  # a December date, so the Malay month differs from the English one
ENGLISH = "What are my HR tasks?"
MALAY = "Apa tugasan tertunggak saya?"
NOTES = (
    "Synthetic laptop description",
    "Synthetic HR note",
    "Synthetic manager note",
    "Synthetic bank description",
    "Synthetic return note",
)
NAMES = ("Aina Rahman", "Razak Ali", "Siti Aminah", "Danial Hakim", "Mei Ling Tan")
TITLES = (
    "Prepare the laptop",
    "Introduce the team",
    "Submit bank details",
    "Hantar butiran bank",
    "Sign the contract",
    "Show the office",
    "Return the laptop",
    "Remove access",
    "Prepare the desk",
)

UNAVAILABLE = {
    "en": "HR processes are part of the HR service, which is not active for this workspace.",
    "ms": "Proses HR ialah sebahagian daripada perkhidmatan HR, yang tidak aktif untuk "
    "ruang kerja ini.",
}
NOTHING_WAITING = {"en": "Nothing waiting for you.", "ms": "Tiada apa-apa yang menunggu anda."}
NOT_FOUND = {
    "en": "I can't find a process for that person that you can see.",
    "ms": "Saya tidak menjumpai proses untuk orang itu yang boleh anda lihat.",
}
NOBODY_ONBOARDING = {
    "en": "Nobody is onboarding that you can see.",
    "ms": "Tiada sesiapa yang sedang onboarding yang boleh anda lihat.",
}
NOBODY_LEAVING = {
    "en": "Nobody is leaving that you can see.",
    "ms": "Tiada sesiapa yang akan berhenti yang boleh anda lihat.",
}
NOTHING_OVERDUE = {
    "en": "Nothing is overdue that you can see.",
    "ms": "Tiada apa-apa yang lewat yang boleh anda lihat.",
}
ASK_PERSON = {
    "en": "Who do you mean? Type their name.",
    "ms": "Siapa yang anda maksudkan? Taip nama mereka.",
}
ONBOARDING_HEAD = "Onboarding in progress that you can see"
LEAVING_HEAD = "People leaving that you can see"
OVERDUE_HEAD = "Overdue steps you can see"
TASKS_HEAD = "Your open steps in My tasks"


@dataclass
class Company:
    """The browsers of one synthetic company and its people's and processes' ids."""

    hr: httpx.AsyncClient  # the HR owner (the HR team)
    manager: httpx.AsyncClient  # Razak, Aina's manager
    person: httpx.AsyncClient  # Aina, the new starter
    assignee: httpx.AsyncClient  # Siti, owner of one step of Aina's onboarding
    stranger: httpx.AsyncClient  # a member with no linked employee and no HR authority
    people: dict[str, UUID]
    processes: dict[str, UUID]


def _days(count: int) -> date:
    return TODAY + timedelta(days=count)


async def _employee(w: SessionWorld, name: str, *, manager: UUID | None = None) -> UUID:
    eid = uuid4()
    await w.q(
        "INSERT INTO hr_employees(client_id,id,display_name,job_title,manager_id)"
        " VALUES (%s,%s,%s,'Synthetic title',%s)",
        w.tenants[0], eid, name, manager,
    )  # fmt: skip
    return eid


async def _member(w: SessionWorld, employee: UUID | None = None) -> httpx.AsyncClient:
    browser, me = await w.member(role="tenant_staff")
    if employee is not None:
        await w.q(
            "INSERT INTO hr_employee_accounts(client_id,user_id,employee_id) VALUES (%s,%s,%s)",
            w.tenants[0], me["user"]["id"], employee,
        )  # fmt: skip
    return browser


async def _hr_owner(w: SessionWorld) -> httpx.AsyncClient:
    owner, me = await w.member(role="tenant_admin")
    user = me["user"]["id"]
    await w.q(
        "INSERT INTO hr_client_owners(client_id,user_id,approved_by) VALUES (%s,%s,%s)",
        w.tenants[0], user, user,
    )  # fmt: skip
    return owner


async def _process(w: SessionWorld, employee: UUID, kind: str = "onboarding") -> UUID:
    rows = await w.q(
        "INSERT INTO hr_processes(client_id,employee_id,kind) VALUES (%s,%s,%s) RETURNING id",
        w.tenants[0], employee, kind,
    )  # fmt: skip
    return rows[0]["id"]


async def _task(
    w: SessionWorld,
    process: UUID,
    ordinal: int,
    title: str,
    *,
    owner_role: str = "hr",
    assignee: UUID | None = None,
    due: date | None = None,
    status: str = "todo",
    title_ms: str | None = None,
    description: str | None = None,
    note: str | None = None,
    anchor: str = "start_date",
) -> None:
    await w.q(
        "INSERT INTO hr_process_tasks(client_id,process_id,ordinal,title_en,title_ms,"
        "description_en,owner_role,assignee_employee_id,anchor,due_offset_days,due_on,status,"
        "note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s)",
        w.tenants[0], process, ordinal, title, title_ms, description, owner_role, assignee,
        anchor, due, status, note,
    )  # fmt: skip


async def _company(w: SessionWorld, *, hr: bool = True) -> Company:
    """Aina (reports to Razak) is onboarding; Danial (no linked account) is leaving; Mei Ling
    is onboarding and is nobody's report. Siti owns one step of Aina's onboarding."""
    if hr:
        await activate(w, w.tenants[0], "hr")
    owner = await _hr_owner(w)
    razak = await _employee(w, "Razak Ali")
    aina = await _employee(w, "Aina Rahman", manager=razak)
    siti = await _employee(w, "Siti Aminah")
    danial = await _employee(w, "Danial Hakim")
    mei = await _employee(w, "Mei Ling Tan")
    onboarding = await _process(w, aina)
    await _task(
        w, onboarding, 1, "Prepare the laptop", due=_days(-3),
        description="Synthetic laptop description", note="Synthetic HR note",
    )  # fmt: skip
    await _task(
        w, onboarding, 2, "Introduce the team", owner_role="manager", assignee=razak,
        due=_days(1), note="Synthetic manager note",
    )  # fmt: skip
    await _task(
        w, onboarding, 3, "Submit bank details", owner_role="employee", assignee=aina, due=FAR,
        title_ms="Hantar butiran bank", description="Synthetic bank description",
    )  # fmt: skip
    await _task(w, onboarding, 4, "Sign the contract", due=_days(-10), status="done")
    await _task(
        w, onboarding, 5, "Show the office", owner_role="person", assignee=siti, due=_days(-1)
    )
    offboarding = await _process(w, danial, "offboarding")
    await _task(
        w, offboarding, 1, "Return the laptop", owner_role="employee", assignee=danial,
        due=_days(2), note="Synthetic return note", anchor="last_day",
    )  # fmt: skip
    await _task(w, offboarding, 2, "Remove access", due=_days(-2), anchor="last_day")
    desk = await _process(w, mei)
    await _task(w, desk, 1, "Prepare the desk", due=_days(10))
    return Company(
        hr=owner,
        manager=await _member(w, razak),
        person=await _member(w, aina),
        assignee=await _member(w, siti),
        stranger=await _member(w),
        people={"aina": aina, "razak": razak, "siti": siti, "danial": danial, "mei": mei},
        processes={"aina": onboarding, "danial": offboarding, "mei": desk},
    )


async def _ask(
    w: SessionWorld,
    model: Any,
    browser: httpx.AsyncClient,
    view: str,
    *,
    person: str | None = None,
    language: str = "en",
) -> dict[str, Any]:
    plan: dict[str, Any] = {"action": "hr_process_read", "view": view}
    if person is not None:
        plan["person"] = person
    model.reply(plan)
    thread = await _thread(w, browser)
    response = await _turn(w, browser, thread, MALAY if language == "ms" else ENGLISH, "hr")
    assert response.status_code == 201, response.text
    return response.json()["result"]


async def _answer(
    w: SessionWorld, model: Any, browser: httpx.AsyncClient, view: str, **kw: Any
) -> str:
    result = await _ask(w, model, browser, view, **kw)
    assert result["action"] == "answer", result
    return result["markdown"]


async def _get(browser: httpx.AsyncClient, path: str) -> Any:
    response = await browser.get(path)
    assert response.status_code == 200, response.text
    return response.json()


async def _oracle_my_titles(browser: httpx.AsyncClient) -> set[str]:
    """The titles the console's My tasks shows this browser."""
    return {task["title_en"] for task in (await _get(browser, f"{HR}/tasks/mine"))["items"]}


async def _oracle_names(browser: httpx.AsyncClient, kind: str) -> set[str]:
    page = await _get(browser, f"{HR}/processes?kind={kind}&status=active")
    return {row["employee_name"] for row in page["items"]}


async def _oracle_overdue(browser: httpx.AsyncClient) -> set[str]:
    """Overdue steps the console shows this browser: in My tasks, and in each overdue process."""
    mine = (await _get(browser, f"{HR}/tasks/mine"))["items"]
    titles = {task["title_en"] for task in mine if task["overdue"]}
    page = await _get(browser, f"{HR}/processes?status=active&overdue=true")
    for row in page["items"]:
        detail = await _get(browser, f"{HR}/processes/{row['process_id']}")
        titles |= {task["title_en"] for task in detail["tasks"] if task["overdue"]}
    return titles


async def _oracle_open_steps(browser: httpx.AsyncClient, process: UUID) -> set[str]:
    response = await browser.get(f"{HR}/processes/{process}")
    if response.status_code == 404:
        return set()
    assert response.status_code == 200, response.text
    tasks = response.json()["tasks"]
    return {task["title_en"] for task in tasks if task["status"] not in ("done", "skipped")}


def _shown(markdown: str, words: tuple[str, ...]) -> set[str]:
    return {word for word in words if word in markdown}


def _private(markdown: str) -> None:
    for note in NOTES:
        assert note not in markdown


# ----------------------------------------------------------------------------------- my_tasks


async def test_my_tasks_for_each_role_in_english(planned) -> None:  # noqa: F811
    w, model = planned
    c = await _company(w)
    expected = {
        "manager": f"{TASKS_HEAD} (1):\n"
        "1. Introduce the team — onboarding for Aina Rahman, due tomorrow",
        "person": f"{TASKS_HEAD} (1):\n"
        "1. Submit bank details — onboarding for Aina Rahman, due 1 December 2099",
        "assignee": f"{TASKS_HEAD} (1):\n"
        "1. Show the office — onboarding for Aina Rahman, overdue by 1 day",
        "stranger": NOTHING_WAITING["en"],
    }
    for role, text in expected.items():
        browser = getattr(c, role)
        markdown = await _answer(w, model, browser, "my_tasks")
        assert markdown == text, role
        assert _shown(markdown, TITLES) == await _oracle_my_titles(browser), role
    hr = await _answer(w, model, c.hr, "my_tasks")
    assert hr == (
        f"{TASKS_HEAD} (4):\n"
        "1. Prepare the laptop — onboarding for Aina Rahman, overdue by 3 days (HR team)\n"
        "2. Remove access — offboarding for Danial Hakim, overdue by 2 days (HR team)\n"
        "3. Return the laptop — offboarding for Danial Hakim, "
        f"due {format_day(_days(2), 'en')} (assigned to Danial Hakim)\n"
        "4. Prepare the desk — onboarding for Mei Ling Tan, "
        f"due {format_day(_days(10), 'en')} (HR team)"
    )
    assert _shown(hr, TITLES) == await _oracle_my_titles(c.hr)
    _private(hr)
    assert await _proposal_count(w) == 0


async def test_my_tasks_in_malay_uses_anda_malay_months_and_the_english_fallback(
    planned,  # noqa: F811
) -> None:
    w, model = planned
    c = await _company(w)
    head = "Langkah anda yang belum selesai dalam My tasks (1):\n"
    person = await _answer(w, model, c.person, "my_tasks", language="ms")
    assert person == head + (
        "1. Hantar butiran bank — onboarding untuk Aina Rahman, perlu siap pada 1 Disember 2099"
    )
    # No Malay title: the English one, inside a Malay sentence.
    manager = await _answer(w, model, c.manager, "my_tasks", language="ms")
    assert manager == head + "1. Introduce the team — onboarding untuk Aina Rahman, perlu siap esok"
    assignee = await _answer(w, model, c.assignee, "my_tasks", language="ms")
    assert assignee == head + "1. Show the office — onboarding untuk Aina Rahman, lewat 1 hari"
    hr = await _answer(w, model, c.hr, "my_tasks", language="ms")
    assert "(pasukan HR)" in hr and "lewat 3 hari" in hr
    assert "(ditugaskan kepada Danial Hakim)" in hr
    stranger = await _answer(w, model, c.stranger, "my_tasks", language="ms")
    assert stranger == NOTHING_WAITING["ms"]


# ----------------------------------------------------------------------------- person_process


async def test_person_process_follows_each_roles_visibility(planned) -> None:  # noqa: F811
    w, model = planned
    c = await _company(w)
    manager = await _answer(w, model, c.manager, "person_process", person="Aina")
    assert manager == (
        "Aina Rahman's onboarding: 1 of 5 steps done.\n"
        "Open steps (4):\n"
        "1. Prepare the laptop (HR), overdue by 3 days\n"
        "2. Show the office (Named person), overdue by 1 day\n"
        "3. Introduce the team (Manager), due tomorrow\n"
        "4. Submit bank details (Employee), due 1 December 2099"
    )
    _private(manager)  # a manager never gets a note or a description
    # The person sees their own steps only; progress counts every step, as the console shows.
    person = await _answer(w, model, c.person, "person_process", person="aina rahman")
    assert person == (
        "Aina Rahman's onboarding: 1 of 5 steps done.\n"
        "Open steps (1):\n"
        "1. Submit bank details (Employee), due 1 December 2099"
    )
    hr = await _answer(w, model, c.hr, "person_process", person="Aina")
    assert hr == manager  # titles, owners and dates: never the HR team's notes either
    for role in ("hr", "manager", "person", "assignee", "stranger"):
        browser = getattr(c, role)
        result = await _ask(w, model, browser, "person_process", person="Aina")
        markdown = result.get("markdown", "")
        expected = await _oracle_open_steps(browser, c.processes["aina"])
        listed = await _get(browser, f"{HR}/processes")
        if "Aina Rahman" not in {row["employee_name"] for row in listed["items"]}:
            expected = set()  # not in the console's Processes list: not found
        assert _shown(markdown, TITLES) == expected, role
    # The task owner and the stranger are told the same as for a name nobody has.
    for browser in (c.assignee, c.stranger):
        assert await _answer(w, model, browser, "person_process", person="Aina") == NOT_FOUND["en"]


async def test_person_process_in_malay(planned) -> None:  # noqa: F811
    w, model = planned
    c = await _company(w)
    manager = await _answer(w, model, c.manager, "person_process", person="Aina", language="ms")
    assert manager == (
        "Onboarding Aina Rahman: 1 daripada 5 langkah selesai.\n"
        "Langkah yang belum selesai (4):\n"
        "1. Prepare the laptop (HR), lewat 3 hari\n"
        "2. Show the office (Orang yang dinamakan), lewat 1 hari\n"
        "3. Introduce the team (Pengurus), perlu siap esok\n"
        "4. Hantar butiran bank (Pekerja), perlu siap pada 1 Disember 2099"
    )
    missing = await _answer(w, model, c.manager, "person_process", person="Zul", language="ms")
    assert missing == NOT_FOUND["ms"]


async def test_person_process_none_and_not_visible_answer_alike(planned) -> None:  # noqa: F811
    w, model = planned
    c = await _company(w)
    nobody = await _answer(w, model, c.manager, "person_process", person="Zulkifli")
    hidden = await _answer(w, model, c.manager, "person_process", person="Mei Ling")
    assert nobody == hidden == NOT_FOUND["en"]
    # The HR team does see Mei Ling's onboarding.
    hr = await _answer(w, model, c.hr, "person_process", person="Mei Ling")
    assert hr.startswith("Mei Ling Tan's onboarding: 0 of 1 step done.")


async def test_person_process_with_several_matches_asks_which(planned) -> None:  # noqa: F811
    w, model = planned
    c = await _company(w)
    yusof = await _employee(w, "Aina Yusof")
    second = await _process(w, yusof, "offboarding")
    await _task(w, second, 1, "Return the badge", due=FAR, anchor="last_day")
    result = await _ask(w, model, c.hr, "person_process", person="Aina")
    assert result == {
        "action": "clarification",
        "question": "Which Aina do you mean?",
        "options": [
            {"label": "Aina Rahman — onboarding", "value": str(c.processes["aina"])},
            {"label": "Aina Yusof — offboarding", "value": str(second)},
        ],
    }
    # The pick comes back as the process id, which answers exactly that process.
    picked = await _answer(w, model, c.hr, "person_process", person=str(second))
    assert picked == (
        "Aina Yusof's offboarding: 0 of 1 step done.\n"
        "Open steps (1):\n"
        "1. Return the badge (HR), due 1 December 2099"
    )
    # A manager sees only his report, so the same words answer at once.
    manager = await _answer(w, model, c.manager, "person_process", person="Aina")
    assert manager.startswith("Aina Rahman's onboarding:")


@pytest.mark.parametrize("language", ["en", "ms"])
async def test_person_process_with_a_blank_person_asks_for_the_name(
    planned,  # noqa: F811
    language: str,
) -> None:
    w, model = planned
    c = await _company(w)
    result = await _ask(w, model, c.hr, "person_process", person="   ", language=language)
    assert result == {"action": "clarification", "question": ASK_PERSON[language], "options": []}


# ----------------------------------------------------------------- who_onboarding, who_leaving


async def test_who_is_onboarding_and_leaving_per_role(planned) -> None:  # noqa: F811
    w, model = planned
    c = await _company(w)
    hr = await _answer(w, model, c.hr, "who_onboarding")
    assert hr == (
        f"{ONBOARDING_HEAD} (2):\n"
        "1. Aina Rahman: 1 of 5 steps done, next step overdue by 3 days\n"
        f"2. Mei Ling Tan: 0 of 1 step done, next step due {format_day(_days(10), 'en')}"
    )
    leaving = await _answer(w, model, c.hr, "who_leaving")
    assert leaving == (
        f"{LEAVING_HEAD} (1):\n1. Danial Hakim: 0 of 2 steps done, next step overdue by 2 days"
    )
    for role in ("hr", "manager", "person", "assignee", "stranger"):
        browser = getattr(c, role)
        for view, kind, empty in (
            ("who_onboarding", "onboarding", NOBODY_ONBOARDING["en"]),
            ("who_leaving", "offboarding", NOBODY_LEAVING["en"]),
        ):
            markdown = await _answer(w, model, browser, view)
            expected = await _oracle_names(browser, kind)
            assert _shown(markdown, NAMES) == expected, (role, view)
            if not expected:
                assert markdown == empty, (role, view)
    # The person sees their own onboarding, with their own steps' next due date.
    person = await _answer(w, model, c.person, "who_onboarding")
    assert person == (
        f"{ONBOARDING_HEAD} (1):\n1. Aina Rahman: 1 of 5 steps done, next step due 1 December 2099"
    )


async def test_who_is_onboarding_and_leaving_in_malay(planned) -> None:  # noqa: F811
    w, model = planned
    c = await _company(w)
    onboarding = await _answer(w, model, c.manager, "who_onboarding", language="ms")
    assert onboarding == (
        "Onboarding yang sedang berjalan yang boleh anda lihat (1):\n"
        "1. Aina Rahman: 1 daripada 5 langkah selesai, langkah seterusnya lewat 3 hari"
    )
    leaving = await _answer(w, model, c.hr, "who_leaving", language="ms")
    assert leaving == (
        "Orang yang akan berhenti yang boleh anda lihat (1):\n"
        "1. Danial Hakim: 0 daripada 2 langkah selesai, langkah seterusnya lewat 2 hari"
    )
    assert await _answer(w, model, c.manager, "who_leaving", language="ms") == NOBODY_LEAVING["ms"]
    nobody = await _answer(w, model, c.stranger, "who_onboarding", language="ms")
    assert nobody == NOBODY_ONBOARDING["ms"]


# ------------------------------------------------------------------------------------ overdue


async def test_overdue_per_role(planned) -> None:  # noqa: F811
    w, model = planned
    c = await _company(w)
    hr = await _answer(w, model, c.hr, "overdue")
    assert hr == (
        f"{OVERDUE_HEAD} (3):\n"
        "1. Prepare the laptop — onboarding for Aina Rahman, overdue by 3 days\n"
        "2. Remove access — offboarding for Danial Hakim, overdue by 2 days\n"
        "3. Show the office — onboarding for Aina Rahman, overdue by 1 day"
    )
    manager = await _answer(w, model, c.manager, "overdue")
    assert manager == (
        f"{OVERDUE_HEAD} (2):\n"
        "1. Prepare the laptop — onboarding for Aina Rahman, overdue by 3 days\n"
        "2. Show the office — onboarding for Aina Rahman, overdue by 1 day"
    )
    assignee = await _answer(w, model, c.assignee, "overdue")
    assert assignee == (
        f"{OVERDUE_HEAD} (1):\n1. Show the office — onboarding for Aina Rahman, overdue by 1 day"
    )
    assert await _answer(w, model, c.person, "overdue") == NOTHING_OVERDUE["en"]
    assert await _answer(w, model, c.stranger, "overdue") == NOTHING_OVERDUE["en"]
    for role in ("hr", "manager", "person", "assignee", "stranger"):
        browser = getattr(c, role)
        markdown = await _answer(w, model, browser, "overdue")
        assert _shown(markdown, TITLES) == await _oracle_overdue(browser), role
        _private(markdown)
    malay = await _answer(w, model, c.manager, "overdue", language="ms")
    assert malay == (
        "Langkah lewat yang boleh anda lihat (2):\n"
        "1. Prepare the laptop — onboarding untuk Aina Rahman, lewat 3 hari\n"
        "2. Show the office — onboarding untuk Aina Rahman, lewat 1 hari"
    )
    assert await _answer(w, model, c.person, "overdue", language="ms") == NOTHING_OVERDUE["ms"]


# ------------------------------------------------------------------------- caps and count line


@pytest.mark.parametrize("count", [0, 3, 12])
async def test_lists_are_capped_at_ten_with_a_count_line(planned, count: int) -> None:  # noqa: F811
    w, model = planned
    await activate(w, w.tenants[0], "hr")
    owner = await _hr_owner(w)
    for index in range(count):
        joiner = await _employee(w, f"Synthetic Joiner {index:02d}")
        started = await _process(w, joiner)
        await _task(w, started, 1, f"Synthetic step {index:02d}", due=_days(-index - 1))
        leaver = await _employee(w, f"Synthetic Leaver {index:02d}")
        leaving = await _process(w, leaver, "offboarding")
        await _task(w, leaving, 1, "Synthetic exit", due=FAR, anchor="last_day")
    for view, empty, head, page in (
        ("who_onboarding", NOBODY_ONBOARDING, ONBOARDING_HEAD, "Processes"),
        ("who_leaving", NOBODY_LEAVING, LEAVING_HEAD, "Processes"),
        ("overdue", NOTHING_OVERDUE, OVERDUE_HEAD, "Processes"),
        ("my_tasks", NOTHING_WAITING, TASKS_HEAD, "My tasks"),
    ):
        markdown = await _answer(w, model, owner, view)
        lines = markdown.split("\n")
        total = count * 2 if view == "my_tasks" else count
        if total == 0:
            assert markdown == empty["en"], view
            continue
        assert lines[0] == f"{head} ({total}):", view
        rows = [line for line in lines[1:] if line[:1].isdigit() and ". " in line[:4]]
        assert len(rows) == min(total, 10), view
        if total > 10:
            assert lines[-1] == f"{total - 10} more not shown. See them all in {page}.", view
        else:
            assert len(lines) == total + 1, view
    if count == 12:
        malay = await _answer(w, model, owner, "who_onboarding", language="ms")
        assert malay.split("\n")[-1] == "2 lagi tidak ditunjukkan. Lihat semuanya dalam Processes."
        # Most overdue first.
        overdue = await _answer(w, model, owner, "overdue")
        assert overdue.split("\n")[1].startswith("1. Synthetic step 11 — onboarding for")


# ---------------------------------------------------------------------- package, storage, writes


@pytest.mark.parametrize("language", ["en", "ms"])
async def test_hr_package_off_answers_the_usual_sentence(planned, language: str) -> None:  # noqa: F811
    w, model = planned
    c = await _company(w, hr=False)
    for view in ("my_tasks", "who_onboarding", "who_leaving", "overdue"):
        assert await _answer(w, model, c.hr, view, language=language) == UNAVAILABLE[language]
    person = await _answer(w, model, c.manager, "person_process", person="Aina", language=language)
    assert person == UNAVAILABLE[language]


async def test_stored_turns_hold_counts_and_fixed_text_only(planned) -> None:  # noqa: F811
    w, model = planned
    c = await _company(w)
    yusof = await _employee(w, "Aina Yusof")
    await _process(w, yusof)
    before = await w.q("SELECT id, status, version, note FROM hr_process_tasks ORDER BY id")
    shown = [
        await _answer(w, model, c.hr, "my_tasks"),
        await _answer(w, model, c.hr, "who_onboarding"),
        await _answer(w, model, c.hr, "who_leaving", language="ms"),
        await _answer(w, model, c.hr, "overdue"),
        await _answer(w, model, c.manager, "person_process", person="Aina"),
    ]
    assert all("Aina Rahman" in text or "Danial" in text for text in shown)
    asked = await _ask(w, model, c.hr, "person_process", person="Aina")
    assert asked["action"] == "clarification"
    rows = await w.q("SELECT action, result FROM console_workspace_turns ORDER BY id")
    assert [row["action"] for row in rows] == ["answer"] * 6
    stored = [row["result"] for row in rows]
    assert [item["markdown"] for item in stored[:5]] == [
        "HR process answer: 4 items.",
        "HR process answer: 3 items.",
        "Jawapan proses HR: 1 item.",
        "HR process answer: 3 items.",
        "HR process answer: 4 items.",
    ]
    assert stored[5]["question"] == "Which Aina do you mean?"
    assert [option["label"] for option in stored[5]["options"]] == ["onboarding", "onboarding"]
    text = json.dumps(stored)
    for secret in (*NAMES, "Aina Yusof", *TITLES, *NOTES):
        assert secret not in text
    # Read only: no task changed and no card was made.
    after = await w.q("SELECT id, status, version, note FROM hr_process_tasks ORDER BY id")
    assert after == before
    assert await _proposal_count(w) == 0
