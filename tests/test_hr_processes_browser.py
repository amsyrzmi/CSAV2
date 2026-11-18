"""Real browser checks for the Processes tab (HR processes console, Task 4).

The staged console runs from the copy `test_services_redesign_browser.site` builds, as in
`test_hr_my_tasks_browser.py`. Playwright answers every browser-side `/console/v1/**` call from
`ProcessesWorld`: the My tasks company (Lina's onboarding, Hana's offboarding) plus Farid's finished
custom process, a few more steps and some history. The processes, templates and task routes all
come from the shared stub `tests/hr_process_world.py`. Nadia (the HR owner) works the tab; Lina has
no HR duties. Any call the stub does not expect fails the test. All data is synthetic.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from playwright.async_api import Locator, Page, expect

from tests import test_services_redesign_browser as services
from tests.hr_process_world import (
    DEPARTMENT,
    PROCESS,
    TASK,
    TEMPLATE,
    Process,
    Step,
    kuala_lumpur_today,
)
from tests.test_hr_access_browser import EMPLOYEE, HR_PACKAGE, MANAGER, OWNER, STAFF
from tests.test_hr_my_tasks_browser import MEMBERS, TasksWorld
from tests.test_services_browser import TENANT, WAIT, make_me
from tests.test_services_redesign_browser import Site, with_browser
from tests.test_team_browser import THEMES, overflow

pytestmark = pytest.mark.asyncio

# The staged copy of the console is built once per file by the services fixture.
site = services.site

LIST = "/portal/hr/processes"
FINISHED = "88888888-8888-4888-8888-000000000003"
BANK = "77777777-7777-4777-8777-000000000101"
LUNCH = "77777777-7777-4777-8777-000000000102"
ORDERS = "77777777-7777-4777-8777-000000000103"
STEP = TASK | {"bank": BANK, "lunch": LUNCH}
TASK_CHANGED = "Someone changed this step since you opened it. Reload to see it."
REASON_SENTENCE = "Give a reason of 1 to 500 characters."
NO_MATCH = "No processes match."
WRITE_LABELS = (
    "Start a process",
    "Cancel process",
    "Close anyway",
    "Reschedule",
    "Reassign",
    "Skip",
)


def day(offset: int) -> str:
    return (kuala_lumpur_today() + timedelta(days=offset)).isoformat()


def date_text(iso: str) -> str:
    """`dateText` of lib/hr-people.ts: "27 Oct 2026"."""
    value = date.fromisoformat(iso)
    return f"{value.day} {value.strftime('%b')} {value.year}"


def utc_at(offset: int, hour: int) -> str:
    moment = datetime.combine(kuala_lumpur_today() + timedelta(days=offset), datetime.min.time())
    return moment.replace(hour=hour, tzinfo=ZoneInfo("UTC")).isoformat()


class ProcessesWorld(TasksWorld):
    """The My tasks company with a finished custom process, two more onboarding steps, history
    on Lina's onboarding and two departments."""

    def __init__(self) -> None:
        super().__init__()
        world = self.processes
        lina, farid, nadia = MEMBERS[STAFF], MEMBERS[MANAGER], MEMBERS[OWNER]
        onboarding = {
            "process_id": PROCESS["onboarding"],
            "process_kind": "onboarding",
            "process_name": "Warehouse onboarding",
            "about": lina,
        }
        world.steps += [
            Step(
                task_id=BANK,
                ordinal=7,
                title_en="Open a salary bank account",
                owner_role="employee",
                assignee=lina,
                due_on=day(-1),
                status="done",
                completed_by=STAFF,
                completed_at=utc_at(-1, 3),
                **onboarding,
            ),
            Step(
                task_id=LUNCH,
                ordinal=8,
                title_en="Book the welcome lunch",
                owner_role="manager",
                assignee=nadia,
                assignee_overridden=True,
                due_on=day(3),
                **onboarding,
            ),
            Step(
                task_id=ORDERS,
                process_id=FINISHED,
                process_kind="custom",
                process_name="Transfer checklist",
                about=farid,
                ordinal=1,
                title_en="Hand over open orders",
                owner_role="employee",
                assignee=farid,
                due_on=day(-15),
                status="done",
                completed_by=MANAGER,
                completed_at=utc_at(-15, 4),
            ),
        ]
        world.processes.append(
            Process(
                process_id=FINISHED,
                kind="custom",
                name="Transfer checklist",
                about=farid,
                started_at=utc_at(-20, 2),
                department_id=DEPARTMENT["warehouse"],
                status="completed",
                closed_at=utc_at(-15, 4),
                template_id=TEMPLATE["transfer"],
                template_version=1,
            )
        )
        process = world.process(PROCESS["onboarding"])
        assert process
        # Lina's onboarding history in the shapes the migrations write (0632, 0634): codes in
        # `reason`, HR's words in `remark`, employee ids in `from`/`to`, dates in `old`/`new`.
        log = world.log
        log(
            process,
            "process.started",
            None,
            kind="onboarding",
            template_id=TEMPLATE["warehouse"],
            template_version=2,
        )
        log(
            process,
            "task.created",
            None,
            TASK["sign"],
            owner_role="employee",
            assignee_employee_id=EMPLOYEE[STAFF],
            due_on=day(2),
        )
        log(
            process,
            "task.created",
            None,
            TASK["payroll"],
            owner_role="hr",
            assignee_employee_id=None,
            due_on=day(7),
        )
        log(
            process,
            "task.created",
            None,
            LUNCH,
            owner_role="manager",
            assignee_employee_id=None,
            due_on=day(3),
            fallback="hr_pool",
            reason="no_manager",
        )
        log(
            process,
            "task.reassigned",
            OWNER,
            LUNCH,
            to=EMPLOYEE[OWNER],
            reason="reassigned_by_hr",
            **{"from": None},
        )
        log(
            process,
            "task.sent_back",
            MANAGER,
            TASK["handbook"],
            remark="Please sign the last page too.",
        )
        log(process, "task.completed", STAFF, BANK, awaiting_approval=False, on_behalf=False)
        log(
            process,
            "task.date_changed",
            None,
            TASK["sign"],
            anchor="start_date",
            old=day(1),
            new=day(2),
        )
        # An action and a key this console does not know; the id in it is never shown.
        log(process, "task.escalated", OWNER, TASK["handbook"], level=2, owner=EMPLOYEE[STAFF])
        log(process, "task.noted", STAFF, TASK["kit"], reason="later_reason")

    def answer(
        self, caller: str, method: str, path: str, params: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        if path == "hr/departments" and method == "GET" and caller == OWNER:
            return 200, {
                "items": [
                    {
                        "department_id": DEPARTMENT[key],
                        "name": name,
                        "archived": False,
                        "version": 1,
                        "people": 2,
                    }
                    for key, name in (("operations", "Operations"), ("warehouse", "Warehouse"))
                ]
            }
        return super().answer(caller, method, path, params, body)

    def reads(self, path: str = "hr/processes") -> list[dict[str, Any]]:
        return [c for c in self.calls if c["method"] == "GET" and c["path"] == path]

    def posts(self, path: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["method"] == "POST" and c["path"] == path]


def tab(page: Page) -> Locator:
    return page.get_by_test_id("hr-processes")


def process_row(page: Page, process_id: str) -> Locator:
    return page.get_by_test_id(f"hr-process-row-{process_id}")


def process_card(page: Page, process_id: str) -> Locator:
    return page.get_by_test_id(f"hr-process-card-{process_id}")


def detail(page: Page) -> Locator:
    return page.get_by_test_id("hr-process-detail")


def step(page: Page, name: str) -> Locator:
    return page.get_by_test_id(f"hr-process-step-{STEP[name]}")


def dialog(page: Page) -> Locator:
    return page.get_by_test_id("hr-process-dialog")


def start_dialog(page: Page) -> Locator:
    return page.get_by_test_id("hr-start-dialog")


def outcome(page: Page) -> Locator:
    return page.get_by_test_id("hr-process-outcome")


def detail_path(process_id: str) -> str:
    return f"{LIST}/{process_id}"


@asynccontextmanager
async def processes_page(
    browser: Any,
    site: Site,
    world: ProcessesWorld,
    path: str = LIST,
    *,
    caller: str = OWNER,
    role: str | None = None,
    ready: Any = tab,
    width: int = 1280,
    theme: str | None = None,
) -> AsyncIterator[Page]:
    """`path` signed in as `caller` (Nadia, the HR owner and a tenant admin, by default) once
    `ready(page)` is visible."""
    me = make_me(role or ("tenant_admin" if caller == OWNER else "tenant_staff"))
    me["user"]["id"] = caller
    me["user"]["display_name"] = MEMBERS[caller].name
    site.upstream.me = {**me, "workspace_assistant": False}
    site.upstream.packages = HR_PACKAGE
    context = await browser.new_context(
        viewport={"width": width, "height": 1000}, timezone_id="Asia/Kuala_Lumpur"
    )
    context.set_default_navigation_timeout(WAIT)
    context.set_default_timeout(15_000)
    cookies = [{"name": "csa_session", "value": "synthetic-session", "url": site.base}]
    if theme:
        cookies.append({"name": "fs-theme", "value": theme, "url": site.base})
    await context.add_cookies(cookies)
    page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.route("**/console/v1/**", world.route_for(caller))
    await page.goto(f"{site.base}{path}")
    await expect(ready(page)).to_be_visible(timeout=WAIT)
    try:
        yield page
        assert not errors, errors
        assert not world.unexpected, world.unexpected
        assert not site.upstream.unexpected, site.upstream.unexpected
    finally:
        await context.close()


def assert_write(call: dict[str, Any], path: str, body: Any) -> None:
    """One write: the path and exact body, with CSRF and an Idempotency-Key."""
    assert call["method"] == "POST", call
    assert call["path"] == path, call
    assert call["params"] == {"client_id": TENANT}, call
    assert call["body"] == body, call
    assert call["key"], call
    assert call["csrf"] == "synthetic-csrf", call


UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CODES = (
    "_by_hr",
    "hr_pool",
    "no_manager",
    "person_left",
    "superseded",
    "closed_anyway",
    "later_reason",
    "task.",
    "process.",
    "employee_id",
    "owner:",
    "template id",
)


async def assert_readable(history: Locator) -> None:
    """History holds no id and no code, only words."""
    text = await history.inner_text()
    assert not UUID.search(text), text
    assert not [code for code in CODES if code in text], text


async def wait_for_query(world: ProcessesWorld, page: Page, wanted: dict[str, str]) -> None:
    """The newest list read asked for exactly `wanted` (plus the tenant)."""
    expected = {"client_id": TENANT} | wanted
    for _ in range(100):
        reads = world.reads()
        if reads and reads[-1]["params"] == expected:
            return
        await page.wait_for_timeout(100)
    raise AssertionError((expected, [c["params"] for c in world.reads()]))


async def test_hr_sees_the_list_filters_it_and_switches_to_templates(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = ProcessesWorld()
        async with processes_page(browser, site, world) as page:
            views = page.get_by_role("navigation", name="Process pages")
            await expect(views.get_by_role("link", name="Processes")).to_have_attribute(
                "aria-current", "page"
            )
            lina = process_row(page, PROCESS["onboarding"])
            await expect(lina).to_be_visible(timeout=WAIT)
            # Active processes by default, newest first.
            await wait_for_query(world, page, {"status": "active"})
            rows = tab(page).locator("[data-testid^=hr-process-row-]")
            ids = await rows.evaluate_all("(els) => els.map((el) => el.dataset.testid)")
            assert ids == [
                f"hr-process-row-{PROCESS['offboarding']}",
                f"hr-process-row-{PROCESS['onboarding']}",
            ], ids
            # Person, kind, template, progress, next due and the overdue chip.
            for text in (
                "Lina Wong",
                "Onboarding",
                "Warehouse onboarding",
                "1 of 8 steps",
                "Overdue by 3 days",
                "In progress",
            ):
                await expect(lina).to_contain_text(text)
            await expect(lina.get_by_text("Overdue", exact=True)).to_be_visible()
            hana = process_row(page, PROCESS["offboarding"])
            await expect(hana).to_contain_text("Hana Yusof")
            await expect(hana).to_contain_text("Offboarding")
            await expect(hana).to_contain_text("No date yet")
            await expect(hana).to_contain_text("0 of 1 step")
            await expect(hana.get_by_text("Overdue", exact=True)).to_have_count(0)
            # Each row links to its process; the link's name says which process it is.
            link = lina.get_by_role("link", name="Lina Wong")
            await expect(link).to_have_attribute("href", detail_path(PROCESS["onboarding"]))
            await expect(link).to_have_accessible_name(
                f"Lina Wong, Onboarding, started {date_text(day(-5))}, 10:00"
            )

            filters = page.get_by_role("search", name="Filter processes")
            await filters.get_by_label("Kind").select_option("onboarding")
            await wait_for_query(world, page, {"status": "active", "kind": "onboarding"})
            await expect(hana).to_have_count(0, timeout=WAIT)
            await filters.get_by_label("Overdue only").check()
            await wait_for_query(
                world, page, {"status": "active", "kind": "onboarding", "overdue": "true"}
            )
            await filters.get_by_label("Department").select_option(label="Warehouse")
            await wait_for_query(
                world,
                page,
                {
                    "status": "active",
                    "kind": "onboarding",
                    "overdue": "true",
                    "department": DEPARTMENT["warehouse"],
                },
            )
            await expect(lina).to_be_visible(timeout=WAIT)
            # Farid owns no step of an active onboarding: nothing matches.
            await filters.get_by_label("Assigned to").select_option(EMPLOYEE[MANAGER])
            await wait_for_query(
                world,
                page,
                {
                    "status": "active",
                    "kind": "onboarding",
                    "overdue": "true",
                    "department": DEPARTMENT["warehouse"],
                    "owner": EMPLOYEE[MANAGER],
                },
            )
            await expect(tab(page).get_by_text(NO_MATCH)).to_be_visible(timeout=WAIT)
            await filters.get_by_role("button", name="Clear filters").click()
            await wait_for_query(world, page, {"status": "active"})
            await expect(rows).to_have_count(2, timeout=WAIT)
            # Any status shows Farid's finished transfer too.
            await filters.get_by_label("Status").select_option("")
            await wait_for_query(world, page, {})
            finished = process_row(page, FINISHED)
            await expect(finished).to_contain_text("Transfer checklist", timeout=WAIT)
            await expect(finished).to_contain_text("Completed")
            await expect(finished).to_contain_text("1 of 1 step")

            # The Templates view has its own route and keeps the sub-navigation in step.
            await views.get_by_role("link", name="Templates").click()
            await page.wait_for_url(f"{site.base}{LIST}/templates")
            await expect(views.get_by_role("link", name="Templates")).to_have_attribute(
                "aria-current", "page"
            )
            await expect(page.get_by_test_id("hr-template-list")).to_contain_text(
                "Warehouse onboarding", timeout=WAIT
            )
            await views.get_by_role("link", name="Processes").click()
            await page.wait_for_url(f"{site.base}{LIST}")
            await expect(process_row(page, PROCESS["onboarding"])).to_be_visible(timeout=WAIT)

    await with_browser(body)


async def test_load_more_follows_the_keyset(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = ProcessesWorld()
        world.processes.page_size = 1
        async with processes_page(browser, site, world) as page:
            rows = tab(page).locator("[data-testid^=hr-process-row-]")
            await expect(rows).to_have_count(1, timeout=WAIT)
            await expect(process_row(page, PROCESS["offboarding"])).to_be_visible()
            more = tab(page).get_by_role("button", name="Load more")
            await more.click()
            await expect(rows).to_have_count(2, timeout=WAIT)
            await expect(process_row(page, PROCESS["onboarding"])).to_be_visible()
            await expect(more).to_have_count(0)
            cursors = [c["params"].get("after") for c in world.reads()]
            assert [c for c in cursors if c] == [PROCESS["offboarding"]], cursors
            assert world.reads()[-1]["params"]["status"] == "active"

    await with_browser(body)


async def test_staff_without_hr_duties_get_no_tab_and_a_notice(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = ProcessesWorld()
        notice = lambda page: page.get_by_text(  # noqa: E731
            "You don't have access to this part of People & HR."
        )
        async with processes_page(browser, site, world, caller=STAFF, ready=notice) as page:
            await expect(page.get_by_test_id("hr-nav-processes")).to_have_count(0)
            await expect(tab(page)).to_have_count(0)
            await page.goto(f"{site.base}{detail_path(PROCESS['onboarding'])}")
            await expect(notice(page)).to_be_visible(timeout=WAIT)
            touched = [c["path"] for c in world.calls if "processes" in c["path"]]
            assert touched == [], touched

    await with_browser(body)


async def test_detail_shows_the_timeline_and_the_history(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = ProcessesWorld()
        async with processes_page(browser, site, world) as page:
            await (
                process_row(page, PROCESS["onboarding"])
                .get_by_role("link", name="Lina Wong")
                .click()
            )
            await page.wait_for_url(f"{site.base}{detail_path(PROCESS['onboarding'])}")
            panel = detail(page)
            await expect(
                panel.get_by_role("heading", name="Onboarding for Lina Wong")
            ).to_be_visible(timeout=WAIT)
            views = page.get_by_role("navigation", name="Process pages")
            await expect(views.get_by_role("link", name="Processes")).to_have_attribute(
                "aria-current", "page"
            )
            header = page.get_by_test_id("hr-process-header")
            for text in (
                "Warehouse onboarding, version 2",
                "In progress",
                "1 of 8 steps",
                f"Start date{date_text(day(-5))}",
                f"Probation end{date_text(day(85))}",
            ):
                await expect(header).to_contain_text(text)
            await expect(header).not_to_contain_text("Last day")
            # The steps in order.
            items = page.get_by_test_id("hr-process-steps").locator(
                "[data-testid^=hr-process-step-]"
            )
            ids = await items.evaluate_all("(els) => els.map((el) => el.dataset.testid)")
            order = ["sign", "details", "handbook", "badge", "kit", "payroll", "bank", "lunch"]
            assert ids == [f"hr-process-step-{STEP[name]}" for name in order], ids
            sign = step(page, "sign")
            for text in (
                "Sign the employment contract",
                "The employee",
                "Lina Wong",
                "Due in 2 days",
                "To do",
                "Reference: Contract template: HR shared folder",
            ):
                await expect(sign).to_contain_text(text)
            await expect(sign.locator("[lang=ms]")).to_have_text("Tandatangan kontrak pekerjaan")
            await expect(step(page, "payroll")).to_contain_text("HR team")
            await expect(step(page, "handbook")).to_contain_text(
                "Sent back: Please sign the last page too."
            )
            await expect(step(page, "handbook")).to_contain_text("Note: Read chapters 1 to 3.")
            await expect(step(page, "handbook")).to_contain_text("Overdue by 3 days")
            await expect(step(page, "badge")).to_contain_text("Done by HR")
            await expect(step(page, "bank")).to_contain_text(
                f"Done by Lina Wong on {date_text(day(-1))}"
            )
            await expect(step(page, "bank")).to_contain_text("Done")
            await expect(step(page, "lunch")).to_contain_text("Nadia Hassan")
            await expect(step(page, "lunch")).to_contain_text("Overridden by HR")
            await expect(step(page, "sign")).not_to_contain_text("Overridden by HR")
            # A done step offers no HR action; an open one does.
            for label in ("Reschedule", "Reassign", "Skip"):
                await expect(step(page, "bank").get_by_role("button", name=label)).to_have_count(0)
                await expect(sign.get_by_role("button", name=label)).to_be_visible()

            # History is collapsed until opened.
            history = page.get_by_test_id("hr-process-history")
            events = history.get_by_test_id("hr-process-event")
            await expect(events.first).to_be_hidden()
            await history.locator("summary").click()
            expected = [
                ["Process started", "By the system", "Onboarding", "Template version 2"],
                # The steps a start created: one line, and why one went to the HR team.
                [
                    "3 steps created",
                    "Book the welcome lunch: Assigned to the HR team (the person has no manager)",
                ],
                [
                    "Step reassigned: Book the welcome lunch",
                    "By you",
                    "Reassigned by HR",
                    "Assigned to Nadia Hassan",
                    "Was the HR team",
                ],
                [
                    "Step sent back: Read the safety handbook",
                    "By someone in your team",
                    "Note: Please sign the last page too.",
                ],
                ["Step marked done: Open a salary bank account"],
                [
                    "Due date moved with its date: Sign the employment contract",
                    f"Due date changed from {date_text(day(1))} to {date_text(day(2))}",
                    "Counted from the start date",
                ],
                # An action this console does not know reads generically; a key it does not know
                # is plain text, unless it holds an id.
                ["Something changed: Read the safety handbook", "level: 2"],
                ["Note changed: Pick up the safety kit", "Updated"],
            ]
            await expect(events).to_have_count(len(expected))
            for index, texts in enumerate(expected):
                for text in texts:
                    await expect(events.nth(index)).to_contain_text(text)
            await assert_readable(history)

    await with_browser(body)


async def test_start_asks_before_replacing_an_active_onboarding(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = ProcessesWorld()
        async with processes_page(browser, site, world) as page:
            opener = tab(page).get_by_role("button", name="Start a process")
            await opener.click()
            ask = start_dialog(page)
            await expect(ask).to_be_visible()
            await ask.get_by_label("Person").select_option(EMPLOYEE[STAFF])
            await ask.get_by_label("Kind").select_option("onboarding")
            # The template is optional for an onboarding: the matching one is used.
            template = ask.get_by_label("Template")
            await expect(template.locator("option")).to_have_text(
                ["Use the matching template", "Warehouse onboarding"]
            )
            await ask.get_by_role("button", name="Start process").click()
            question = ask.get_by_test_id("hr-start-replace")
            await expect(question).to_contain_text(
                "There is already an active onboarding for Lina Wong. Replace it?", timeout=WAIT
            )
            (first,) = world.posts("hr/processes")
            assert_write(
                first, "hr/processes", {"employee_id": EMPLOYEE[STAFF], "kind": "onboarding"}
            )
            # Keep sends nothing more and leaves the old process running.
            await question.get_by_role("button", name="Keep").click()
            await expect(question).to_have_count(0)
            assert len(world.posts("hr/processes")) == 1
            await ask.get_by_role("button", name="Start process").click()
            await question.get_by_role("button", name="Replace").click()
            await page.wait_for_url(re.compile(r"/portal/hr/processes/88888888-[0-9a-f-]+0004$"))
            await expect(
                detail(page).get_by_role("heading", name="Onboarding for Lina Wong")
            ).to_be_visible(timeout=WAIT)
            posts = world.posts("hr/processes")
            assert [p["body"].get("replace") for p in posts] == [None, None, True], posts
            replaced = posts[-1]
            assert_write(
                replaced,
                "hr/processes",
                {"employee_id": EMPLOYEE[STAFF], "kind": "onboarding", "replace": True},
            )
            # The same start sent again keeps its key; a different body gets a new one.
            assert posts[0]["key"] == posts[1]["key"], posts
            assert replaced["key"] not in {p["key"] for p in posts[:-1]}, posts
            old = world.processes.process(PROCESS["onboarding"])
            assert old and old.status == "cancelled"

    await with_browser(body)


async def test_custom_start_requires_a_template(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = ProcessesWorld()
        async with processes_page(browser, site, world) as page:
            await tab(page).get_by_role("button", name="Start a process").click()
            ask = start_dialog(page)
            await ask.get_by_label("Person").select_option(EMPLOYEE[MANAGER])
            await ask.get_by_label("Kind").select_option("custom")
            # Only the active custom templates, and no "matching" choice.
            await expect(ask.get_by_label("Template").locator("option")).to_have_text(
                ["Choose a template…", "Transfer checklist"]
            )
            await ask.get_by_role("button", name="Start process").click()
            await expect(ask.get_by_text("Choose a template for a custom process.")).to_be_visible()
            await expect(ask.get_by_label("Template")).to_be_focused()
            assert world.posts("hr/processes") == []
            await ask.get_by_label("Template").select_option(label="Transfer checklist")
            await ask.get_by_role("button", name="Start process").click()
            await page.wait_for_url(re.compile(r"/portal/hr/processes/88888888-[0-9a-f-]+0004$"))
            heading = detail(page).get_by_role("heading", name="Custom process for Farid Ismail")
            await expect(heading).to_be_visible(timeout=WAIT)
            # The start dialog is gone: focus is on the new process's heading.
            await expect(heading).to_be_focused()
            await expect(detail(page)).to_contain_text("Hand over open orders")
            (start,) = world.posts("hr/processes")
            assert_write(
                start,
                "hr/processes",
                {
                    "employee_id": EMPLOYEE[MANAGER],
                    "kind": "custom",
                    "template_id": TEMPLATE["transfer"],
                },
            )
            assert world.reads("hr/templates"), world.calls

    await with_browser(body)


async def test_cancel_and_close_need_confirmation(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = ProcessesWorld()
        path = detail_path(PROCESS["onboarding"])
        async with processes_page(browser, site, world, path, ready=detail) as page:
            cancel = detail(page).get_by_role("button", name="Cancel process")
            await cancel.click()
            await expect(dialog(page)).to_contain_text("Cancel the onboarding for Lina Wong?")
            await dialog(page).get_by_role("button", name="Keep it").click()
            await expect(dialog(page)).to_have_count(0)
            await expect(cancel).to_be_focused()
            await cancel.click()
            await dialog(page).get_by_role("button", name="Cancel process").click()
            await expect(page.get_by_test_id("hr-process-notice")).to_have_text(
                "Process cancelled. Its open steps were skipped.", timeout=WAIT
            )
            (sent,) = world.posts(f"hr/processes/{PROCESS['onboarding']}/cancel")
            assert_write(sent, f"hr/processes/{PROCESS['onboarding']}/cancel", None)
            header = page.get_by_test_id("hr-process-header")
            await expect(header).to_contain_text("Cancelled", timeout=WAIT)
            for label in ("Cancel process", "Close anyway", "Skip", "Reschedule", "Reassign"):
                await expect(detail(page).get_by_role("button", name=label)).to_have_count(0)
            # The button is gone: focus is on the message, never the page body.
            await expect(outcome(page)).to_be_focused()

            await page.goto(f"{site.base}{detail_path(PROCESS['offboarding'])}")
            close = detail(page).get_by_role("button", name="Close anyway")
            await close.click(timeout=WAIT)
            ask = dialog(page)
            submit = ask.get_by_role("button", name="Close anyway")
            await expect(submit).to_be_disabled()
            note = ask.get_by_label("Note")
            await note.fill("x" * 501)
            await expect(ask.get_by_text(REASON_SENTENCE)).to_be_visible()
            await expect(submit).to_be_disabled()
            await note.fill("Hana left early; the laptop came back at the desk.")
            await submit.click()
            await expect(page.get_by_test_id("hr-process-notice")).to_have_text(
                "Process closed. Its open steps were skipped.", timeout=WAIT
            )
            (closed,) = world.posts(f"hr/processes/{PROCESS['offboarding']}/close")
            assert_write(
                closed,
                f"hr/processes/{PROCESS['offboarding']}/close",
                {"note": "Hana left early; the laptop came back at the desk."},
            )
            await expect(page.get_by_test_id("hr-process-header")).to_contain_text("Completed")
            await expect(outcome(page)).to_be_focused()

    await with_browser(body)


async def test_skip_needs_a_reason_and_reassign_sends_the_chosen_person(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = ProcessesWorld()
        path = detail_path(PROCESS["onboarding"])
        async with processes_page(browser, site, world, path, ready=detail) as page:
            payroll = step(page, "payroll")
            await payroll.get_by_role("button", name="Skip").click()
            ask = dialog(page)
            await expect(ask).to_contain_text("Set up the payroll record")
            skip = ask.get_by_role("button", name="Skip")
            await expect(skip).to_be_disabled()
            await ask.get_by_label("Reason").fill("   ")
            await expect(skip).to_be_disabled()
            await ask.get_by_label("Reason").fill("Payroll is set up by the agency.")
            await skip.click()
            await expect(payroll).to_contain_text("Skipped", timeout=WAIT)
            (skipped,) = world.posts(f"hr/tasks/{TASK['payroll']}/skip")
            assert_write(
                skipped,
                f"hr/tasks/{TASK['payroll']}/skip",
                {"expected_version": 1, "reason": "Payroll is set up by the agency."},
            )
            await expect(page.get_by_test_id("hr-process-notice")).to_have_text("Step skipped.")
            # Its Skip button is gone: focus is on the message.
            await expect(outcome(page)).to_be_focused()

            sign = step(page, "sign")
            reassign = sign.get_by_role("button", name="Reassign")
            await reassign.click()
            await dialog(page).get_by_label("Assign to").select_option(EMPLOYEE[MANAGER])
            await dialog(page).get_by_role("button", name="Reassign").click()
            await expect(sign).to_contain_text("Farid Ismail", timeout=WAIT)
            await expect(sign).to_contain_text("Overridden by HR")
            (moved,) = world.posts(f"hr/tasks/{TASK['sign']}/reassign")
            assert_write(
                moved,
                f"hr/tasks/{TASK['sign']}/reassign",
                {"expected_version": 1, "assignee_employee_id": EMPLOYEE[MANAGER]},
            )
            # The button is still there: focus goes back to it.
            await expect(reassign).to_be_focused()
            # The HR team (no one named) is a choice too.
            await reassign.click()
            await dialog(page).get_by_label("Assign to").select_option("")
            await dialog(page).get_by_role("button", name="Reassign").click()
            await expect(sign).to_contain_text("HR team", timeout=WAIT)
            pooled = world.posts(f"hr/tasks/{TASK['sign']}/reassign")[-1]
            assert pooled["body"] == {"expected_version": 2, "assignee_employee_id": None}

    await with_browser(body)


async def test_reschedule_rejects_an_empty_date_and_a_stale_step_reloads(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = ProcessesWorld()
        path = detail_path(PROCESS["onboarding"])
        async with processes_page(browser, site, world, path, ready=detail) as page:
            sign = step(page, "sign")
            reschedule = sign.get_by_role("button", name="Reschedule")
            await reschedule.click()
            ask = dialog(page)
            when = ask.get_by_label("New due date")
            await expect(when).to_have_value(day(2))
            await when.fill("")
            await ask.get_by_role("button", name="Reschedule").click()
            await expect(ask.get_by_text("Give the new due date as a date.")).to_be_visible()
            await expect(when).to_be_focused()
            assert world.posts(f"hr/tasks/{TASK['sign']}/reschedule") == []
            # Someone else changes the step while the dialog is open.
            moved = world.processes.touch(TASK["sign"], due_on=day(4)).version
            reads = len(world.reads(f"hr/processes/{PROCESS['onboarding']}"))
            await when.fill(day(10))
            await ask.get_by_role("button", name="Reschedule").click()
            await expect(page.get_by_test_id("hr-process-error")).to_have_text(
                TASK_CHANGED, timeout=WAIT
            )
            await expect(ask).to_have_count(0)
            await expect(sign).to_contain_text("Due in 4 days", timeout=WAIT)
            assert len(world.reads(f"hr/processes/{PROCESS['onboarding']}")) > reads
            await expect(reschedule).to_be_focused()
            (stale,) = world.posts(f"hr/tasks/{TASK['sign']}/reschedule")
            assert stale["body"] == {"expected_version": 1, "due_on": day(10)}
            # The reloaded step carries the new version.
            await reschedule.click()
            await dialog(page).get_by_label("New due date").fill(day(10))
            await dialog(page).get_by_role("button", name="Reschedule").click()
            await expect(sign).to_contain_text("Due in 10 days", timeout=WAIT)
            await expect(page.get_by_test_id("hr-process-error")).to_have_count(0)
            again = world.posts(f"hr/tasks/{TASK['sign']}/reschedule")[-1]
            assert again["body"] == {"expected_version": moved, "due_on": day(10)}
            await expect(reschedule).to_be_focused()

    await with_browser(body)


async def test_read_only_member_sees_no_write_controls(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = ProcessesWorld()
        async with processes_page(browser, site, world, role="read_only") as page:
            await expect(process_row(page, PROCESS["onboarding"])).to_be_visible(timeout=WAIT)
            await expect(tab(page).get_by_role("button", name="Start a process")).to_have_count(0)
            await page.goto(f"{site.base}{detail_path(PROCESS['onboarding'])}")
            await expect(step(page, "sign")).to_be_visible(timeout=WAIT)
            for label in WRITE_LABELS:
                await expect(detail(page).get_by_role("button", name=label)).to_have_count(0)
            assert [c for c in world.calls if c["method"] == "POST"] == []

    await with_browser(body)


async def test_step_buttons_are_described_by_their_step(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = ProcessesWorld()
        path = detail_path(PROCESS["onboarding"])
        async with processes_page(browser, site, world, path, ready=detail) as page:
            await expect(step(page, "sign")).to_be_visible(timeout=WAIT)
            for name, title in (
                ("sign", "Sign the employment contract"),
                ("handbook", "Read the safety handbook"),
            ):
                for label in ("Reschedule", "Reassign", "Skip"):
                    button = step(page, name).get_by_role("button", name=label, exact=True)
                    await expect(button).to_have_accessible_description(title)
            ids = await page.evaluate(
                "() => [...document.querySelectorAll('[id^=hr-process-step-title-]')]"
                ".map((el) => el.id)"
            )
            assert len(ids) == len(set(ids)) == 8, ids

    await with_browser(body)


SIDEWAYS = """(root) => {
    const out = [...root.querySelectorAll('*')].filter((el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && (r.left < -0.5 || r.right > innerWidth + 0.5);
    }).map((el) => el.dataset.testid || el.tagName);
    const scrollers = [root, ...root.querySelectorAll('*')].filter((el) => {
        const style = getComputedStyle(el);
        return el.clientWidth > 0 && ['auto', 'scroll'].includes(style.overflowX)
            && el.scrollWidth > el.clientWidth + 1;
    }).map((el) => el.dataset.testid || el.className);
    return { out, scrollers };
}"""


@pytest.mark.parametrize("theme", THEMES)
async def test_processes_fit_phone_widths_in_three_themes(site: Site, theme: str) -> None:
    async def body(browser: Any) -> None:
        world = ProcessesWorld()
        async with processes_page(browser, site, world, theme=theme, width=320) as page:
            await expect(page.locator("html")).to_have_attribute("data-theme", theme)
            await expect(process_card(page, PROCESS["onboarding"])).to_be_visible(timeout=WAIT)
            for where_path in (LIST, detail_path(PROCESS["onboarding"])):
                if where_path != LIST:
                    await page.goto(f"{site.base}{where_path}")
                    await expect(step(page, "sign")).to_be_visible(timeout=WAIT)
                for width in (320, 390, 768):
                    await page.set_viewport_size({"width": width, "height": 900})
                    await page.wait_for_timeout(150)
                    where = (theme, where_path, width)
                    measured = await overflow(page)
                    assert measured["scroll"] <= measured["inner"], (where, measured)
                    if where_path == LIST:
                        table = width >= 768
                        await expect(page.get_by_test_id("hr-process-table")).to_be_visible(
                            visible=table
                        )
                        await expect(page.get_by_test_id("hr-process-cards")).to_be_visible(
                            visible=not table
                        )
                    sideways = await tab(page).evaluate(SIDEWAYS)
                    assert sideways == {"out": [], "scrollers": []}, (where, sideways)
            # A step dialog fits a phone too.
            await page.set_viewport_size({"width": 320, "height": 640})
            await step(page, "sign").get_by_role("button", name="Reassign").click()
            box = await dialog(page).bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= 320, (theme, box)

    await with_browser(body)


async def test_escape_closes_dialogs_and_focus_returns(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = ProcessesWorld()
        async with processes_page(browser, site, world) as page:
            opener = tab(page).get_by_role("button", name="Start a process")
            await opener.focus()
            await page.keyboard.press("Enter")
            await expect(start_dialog(page)).to_be_visible()
            await expect(start_dialog(page).get_by_label("Person")).to_be_visible()
            await page.keyboard.press("Escape")
            await expect(start_dialog(page)).to_have_count(0)
            await expect(opener).to_be_focused()

            await page.goto(f"{site.base}{detail_path(PROCESS['onboarding'])}")
            for label in ("Skip", "Reschedule", "Reassign"):
                button = step(page, "sign").get_by_role("button", name=label)
                await button.focus(timeout=WAIT)
                await page.keyboard.press("Enter")
                await expect(dialog(page)).to_be_visible()
                await page.keyboard.press("Escape")
                await expect(dialog(page)).to_have_count(0)
                await expect(button).to_be_focused()
            cancel = detail(page).get_by_role("button", name="Cancel process")
            await cancel.focus()
            await page.keyboard.press("Enter")
            await expect(dialog(page)).to_be_visible()
            await page.keyboard.press("Escape")
            await expect(cancel).to_be_focused()
            assert [c for c in world.calls if c["method"] == "POST"] == []

    await with_browser(body)


async def test_two_processes_of_one_person_have_distinct_link_names(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = ProcessesWorld()
        world.processes.processes.append(
            Process(
                process_id="88888888-8888-4888-8888-000000000009",
                kind="custom",
                name="Transfer checklist",
                about=MEMBERS[STAFF],
                started_at=utc_at(-1, 1),
                department_id=DEPARTMENT["warehouse"],
            )
        )
        async with processes_page(browser, site, world, width=390) as page:
            links = (
                tab(page).get_by_test_id("hr-process-cards").get_by_role("link", name="Lina Wong")
            )
            await expect(links).to_have_count(2, timeout=WAIT)
            names = [
                await link.evaluate("(el) => el.getAttribute('aria-label')")
                for link in await links.all()
            ]
            assert names == [
                f"Lina Wong, Custom process, started {date_text(day(-1))}, 09:00",
                f"Lina Wong, Onboarding, started {date_text(day(-5))}, 10:00",
            ], names

    await with_browser(body)


async def test_history_reads_every_known_event_kind(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = ProcessesWorld()
        process = world.processes.process(PROCESS["offboarding"])
        assert process
        laptop = TASK["laptop"]
        lina = MEMBERS[STAFF].employee_id
        gone = "55555555-5555-4555-8555-000000000099"
        log = world.processes.log
        log(
            process,
            "task.skipped",
            OWNER,
            laptop,
            reason="skipped_by_hr",
            remark="Sent by courier.",
        )
        log(
            process,
            "task.skipped",
            MANAGER,
            laptop,
            reason="person_left",
            remark="x",
            sent_back=True,
        )
        log(process, "task.skipped", None, laptop, reason="process_cancelled")
        log(process, "task.skipped", None, laptop, reason="process_completed")
        log(process, "task.skipped", OWNER, laptop, reason="process_closed_anyway")
        log(process, "task.rescheduled", OWNER, laptop, old=None, new=day(9))
        log(
            process,
            "task.reassigned",
            OWNER,
            laptop,
            to=gone,
            reason="reassigned_by_hr",
            **{"from": lina},
        )
        log(
            process,
            "task.reassigned",
            None,
            laptop,
            to=None,
            fallback="hr_pool",
            reason="person_left",
            **{"from": lina},
        )
        log(
            process,
            "task.reassigned",
            None,
            laptop,
            to=None,
            fallback="hr_pool",
            reason="manager_inactive",
            **{"from": gone},
        )
        log(
            process,
            "task.completed",
            OWNER,
            laptop,
            awaiting_approval=True,
            on_behalf=True,
            auto_check="document_uploaded:other",
        )
        log(
            process,
            "task.approved",
            OWNER,
            laptop,
            approver="hr",
            approved_without_second_person=True,
        )
        log(process, "task.approved", MANAGER, laptop, approver="manager")
        log(process, "task.noted", OWNER, laptop)
        log(process, "process.cancelled", OWNER, reason="superseded")
        log(process, "process.cancelled", None, reason="rehired")
        log(process, "process.cancelled", None, reason="last_day_cleared")
        log(process, "process.cancelled", OWNER, reason="cancelled_by_hr")
        log(process, "process.completed", None)
        log(process, "process.completed", OWNER, reason="closed_anyway", remark="All done.\nFiled.")
        log(
            process,
            "task.date_changed",
            None,
            laptop,
            anchor="last_day",
            old=day(12),
            new=day(10),
            clamped=True,
        )
        expected = [
            ["Step skipped: Return the laptop", "Skipped by HR", "Note: Sent by courier."],
            ["The person left", "Note: x", "Sent back after the person left, so it was skipped"],
            ["Skipped because the process was cancelled"],
            ["Skipped because the process was completed"],
            ["Skipped because HR closed the process"],
            [
                "Step rescheduled: Return the laptop",
                f"Due date changed from no date to {date_text(day(9))}",
            ],
            ["Step reassigned", "Reassigned by HR", "Assigned to someone else", "Was Lina Wong"],
            ["Assigned to the HR team (the person left)", "Was Lina Wong"],
            ["Assigned to the HR team (their manager is not active)"],
            [
                "Step marked done",
                "Checked automatically: a document is uploaded.",
                "Waiting for approval",
                "Done by HR for the person it is assigned to",
            ],
            [
                "Step approved",
                "Approved by HR",
                "Approved without a second person: no one else in HR could approve it",
            ],
            ["Step approved", "Approved by the manager"],
            ["Note changed: Return the laptop"],
            ["Process cancelled", "Replaced by a new process"],
            ["Process cancelled", "Cancelled because the person was rehired"],
            ["Process cancelled", "Cancelled because the last day was cleared"],
            ["Process cancelled", "Cancelled by HR"],
            ["Process completed"],
            [
                "Process completed",
                "Closed by HR before every step was done",
                "Note: All done.\nFiled.",
            ],
            [
                "Due date moved with its date",
                f"Due date changed from {date_text(day(12))} to {date_text(day(10))}",
                "Counted from the last day",
                "Kept on or before the last day",
            ],
        ]
        path = detail_path(PROCESS["offboarding"])
        async with processes_page(browser, site, world, path, ready=detail) as page:
            history = page.get_by_test_id("hr-process-history")
            await history.locator("summary").click(timeout=WAIT)
            events = history.get_by_test_id("hr-process-event")
            await expect(events).to_have_count(len(expected))
            for index, texts in enumerate(expected):
                for text in texts:
                    await expect(events.nth(index)).to_contain_text(text, use_inner_text=True)
            await assert_readable(history)

    await with_browser(body)


async def test_start_refusals_say_why_and_a_retry_keeps_its_key(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = ProcessesWorld()
        world.processes.failures["POST hr/processes"] = [
            (500, "synthetic_outage"),
            (409, "process_no_template"),
        ]
        async with processes_page(browser, site, world) as page:
            await tab(page).get_by_role("button", name="Start a process").click()
            ask = start_dialog(page)
            await ask.get_by_label("Person").select_option(EMPLOYEE[MANAGER])
            await ask.get_by_label("Kind").select_option("offboarding")
            start = ask.get_by_role("button", name="Start process")
            await start.click()
            # Uncertain: the same button again is safe.
            uncertain = (
                "We couldn't confirm that. Choose Start process again; it won't be done twice."
            )
            await expect(ask.get_by_text(uncertain)).to_be_visible(timeout=WAIT)
            await expect(ask.locator(":focus")).to_contain_text(uncertain)
            await start.click()
            refusal_text = "No active template applies to this person."
            refusal = ask.get_by_text(refusal_text)
            await expect(refusal).to_be_visible(timeout=WAIT)
            await expect(ask.locator(":focus")).to_contain_text(refusal_text)
            await start.click()
            await page.wait_for_url(re.compile(r"/portal/hr/processes/88888888-[0-9a-f-]+0004$"))
            posts = world.posts("hr/processes")
            assert len(posts) == 3, posts
            assert len({p["key"] for p in posts}) == 1, posts
            assert posts[0]["body"] == {"employee_id": EMPLOYEE[MANAGER], "kind": "offboarding"}

    await with_browser(body)


async def test_cancel_of_a_process_closed_meanwhile_says_so_and_reloads(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = ProcessesWorld()
        path = detail_path(PROCESS["onboarding"])
        async with processes_page(browser, site, world, path, ready=detail) as page:
            cancel = detail(page).get_by_role("button", name="Cancel process")
            await expect(cancel).to_be_visible(timeout=WAIT)
            process = world.processes.process(PROCESS["onboarding"])
            assert process
            # Someone else closes it while the page is open.
            world.processes.end(
                process,
                "completed",
                MANAGER,
                "process.completed",
                "process_closed_anyway",
                reason="closed_anyway",
                remark="Done elsewhere.",
            )
            await cancel.click()
            await dialog(page).get_by_role("button", name="Cancel process").click()
            await expect(page.get_by_test_id("hr-process-error")).to_have_text(
                "This process is already completed or cancelled.", timeout=WAIT
            )
            await expect(dialog(page)).to_have_count(0)
            await expect(page.get_by_test_id("hr-process-header")).to_contain_text(
                "Completed", timeout=WAIT
            )
            await expect(detail(page).get_by_role("button", name="Cancel process")).to_have_count(0)
            await expect(outcome(page)).to_be_focused()

    await with_browser(body)


async def focus_place(page: Page, dialog_id: str) -> str:
    """Where focus is: in the dialog, in the page, or on the body (lost)."""
    return await page.evaluate(
        "(id) => { const el = document.activeElement;"
        " if (!el || el === document.body) return 'body';"
        " return el.closest(`[data-testid=${id}]`) ? 'dialog' : 'page'; }",
        dialog_id,
    )


async def wait_for_posts(world: ProcessesWorld, page: Page, path: str, count: int) -> None:
    """Until `count` POSTs to `path` have reached the stub."""
    for _ in range(100):
        if len(world.posts(path)) >= count:
            return
        await page.wait_for_timeout(100)
    raise AssertionError((path, count, len(world.posts(path))))


async def test_close_anyway_failing_twice_keeps_focus_on_the_message(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = ProcessesWorld()
        close_path = f"hr/processes/{PROCESS['offboarding']}/close"
        world.processes.failures[f"POST {close_path}"] = [
            (500, "synthetic_outage"),
            (500, "synthetic_outage"),
        ]
        path = detail_path(PROCESS["offboarding"])
        async with processes_page(browser, site, world, path, ready=detail) as page:
            await detail(page).get_by_role("button", name="Close anyway").click(timeout=WAIT)
            ask = dialog(page)
            await ask.get_by_label("Note").fill("Hana left early.")
            uncertain = (
                "We couldn't confirm that. Choose Close anyway again; it won't be done twice."
            )
            for attempt in (1, 2):
                await ask.get_by_role("button", name="Close anyway").click()
                await wait_for_posts(world, page, close_path, attempt)
                await expect(ask.get_by_text(uncertain)).to_be_visible(timeout=WAIT)
                # The refusal, not the page body, has focus after each attempt.
                await expect(ask.locator(":focus")).to_contain_text(uncertain)
                assert await focus_place(page, "hr-process-dialog") == "dialog", attempt
            await ask.get_by_role("button", name="Close anyway").click()
            await expect(page.get_by_test_id("hr-process-notice")).to_have_text(
                "Process closed. Its open steps were skipped.", timeout=WAIT
            )
            posts = world.posts(close_path)
            assert len(posts) == 3 and len({p["key"] for p in posts}) == 1, posts

    await with_browser(body)


async def test_a_failed_reload_after_a_write_keeps_the_message(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = ProcessesWorld()
        path = detail_path(PROCESS["onboarding"])
        async with processes_page(browser, site, world, path, ready=detail) as page:
            payroll = step(page, "payroll")
            await payroll.get_by_role("button", name="Skip").click(timeout=WAIT)
            await dialog(page).get_by_label("Reason").fill("Done by the agency.")
            world.processes.failures[f"GET hr/processes/{PROCESS['onboarding']}"] = [
                (503, "hr_database_unavailable")
            ]
            await dialog(page).get_by_role("button", name="Skip").click()
            await expect(page.get_by_test_id("hr-process-notice")).to_have_text(
                "Step skipped.", timeout=WAIT
            )
            reload_error = page.get_by_test_id("hr-process-reload-error")
            await expect(reload_error).to_contain_text("We couldn't load this. Try again.")
            # The page stays: the step is still listed, as last read.
            await expect(payroll).to_contain_text("To do")
            await reload_error.get_by_role("button", name="Try again").click()
            await expect(payroll).to_contain_text("Skipped", timeout=WAIT)
            await expect(reload_error).to_have_count(0)
            await expect(page.get_by_test_id("hr-process-notice")).to_have_text("Step skipped.")

    await with_browser(body)
