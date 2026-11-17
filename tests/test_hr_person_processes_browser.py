"""Real browser checks for a person's processes, the rehire warning and Home reminders (HR
processes console, Task 6).

The staged console runs from the copy `test_services_redesign_browser.site` builds, as in
`test_hr_processes_browser.py`. Playwright answers every browser-side `/console/v1/**` call from
`PersonWorld`: the My tasks company (Lina's onboarding, Hana's offboarding) whose person,
probation, rehire and notification routes come from the shared stub `tests/hr_process_world.py`.
Nadia (the HR owner) sees Lina's and Hana's profiles and Home; Farid manages Lina; Lina sees her
own My HR. Any call the stub does not expect fails the test. All data is synthetic.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Any

import pytest
from playwright.async_api import Locator, Page, Route, expect

from tests import test_services_redesign_browser as services
from tests.hr_process_world import (
    PROCESS,
    TASK,
    TEMPLATE,
    PersonFacts,
    kuala_lumpur_today,
)
from tests.test_hr_access_browser import EMPLOYEE, HR_PACKAGE, MANAGER, OWNER, SECTIONS, STAFF
from tests.test_hr_my_tasks_browser import MEMBERS, READ_ONLY_EMPLOYEE, TasksWorld
from tests.test_services_browser import TENANT, WAIT, make_me
from tests.test_services_redesign_browser import Site, with_browser
from tests.test_team_browser import THEMES, overflow

pytestmark = pytest.mark.asyncio

# The staged copy of the console is built once per file by the services fixture.
site = services.site

LINA = EMPLOYEE[STAFF]
HANA = READ_ONLY_EMPLOYEE
HOME = "/portal/hr"
LINA_PROFILE = f"/portal/hr/people/{LINA}"
HANA_PROFILE = f"/portal/hr/people/{HANA}"
REHIRE_QUESTION = (
    "Rehiring cancels the open offboarding and skips its remaining steps (for example final pay "
    "checks). Continue?"
)
PERSON_CHANGED = "Someone changed this person since you opened it. Reload to see it."
UNCERTAIN_MARK_ALL = "We couldn't confirm that. Choose Mark all read again; it won't be done twice."
UUID_TEXT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
STALE_TASK = "77777777-7777-4777-8777-000000000901"
OLD_RESOURCE = "12121212-1212-4212-8212-000000000001"
NOTE = {name: f"abababab-abab-4bab-8bab-{index:012d}" for index, name in enumerate("123456", 1)}


def day(offset: int) -> str:
    return (kuala_lumpur_today() + timedelta(days=offset)).isoformat()


def date_text(iso: str) -> str:
    """`dateText` of lib/hr-people.ts: "27 Oct 2026"."""
    value = date.fromisoformat(iso)
    return f"{value.day} {value.strftime('%b')} {value.year}"


def reminder(
    key: str, kind: str, task: str, subject: str | None, due: str | None, *, read: bool = False
) -> dict[str, Any]:
    return {
        "id": NOTE[key],
        "kind": kind,
        "resource_id": task,
        "created_at": "2026-11-10T01:00:00Z",
        "read_at": "2026-11-11T01:00:00Z" if read else None,
        "due_on": due,
        "subject": subject,
        "recipient": OWNER,
    }


class PersonWorld(TasksWorld):
    """The My tasks company with the person facts the profile routes need: Lina (managed by
    Farid, who may open People) and Hana, who has left while her offboarding is still open."""

    def __init__(self) -> None:
        super().__init__()
        self.people[MANAGER]["on"] = ["directory"]
        self.processes.persons = {
            LINA: PersonFacts(
                version=3, manager=MANAGER, start_date=day(-5), probation_end=day(85)
            ),
            HANA: PersonFacts(version=5, status="left", start_date="2024-03-04", last_day=day(-1)),
        }

    def profile_person(self, employee: str, *, hr: bool) -> dict[str, Any]:
        facts = self.processes.persons[employee]
        member = self.processes.members[employee]
        return {
            "employee_id": employee,
            "display_name": member.name,
            "job_title": "Packer",
            "department_id": None,
            "department": "Warehouse",
            "manager_id": EMPLOYEE[MANAGER] if facts.manager else None,
            "manager_name": "Farid Ismail" if facts.manager else None,
            "work_email": None,
            "work_phone": None,
            "start_date": facts.start_date,
            "employment_type": "full_time",
            "status": facts.status,
            "last_day": facts.last_day,
            "leaving": {"reason": "resigned", "note": None} if hr and facts.last_day else None,
            "employment_profile": None,
            "version": facts.version if hr else None,
            "account_linked": True,
        }

    def answer(
        self, caller: str, method: str, path: str, params: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        if params.get("client_id") == TENANT and method == "GET":
            if path == "hr/records/notifications":
                # The stub's notifications, not HomeWorld's empty list.
                answered = self.processes.answer(caller, method, path, params, body)
                assert answered is not None
                return answered
            employee = path.removeprefix("hr/people/")
            facts = self.processes.persons.get(employee)
            if facts and caller == OWNER:
                profile = self.profile_person(employee, hr=True)
                return 200, {"person": profile, "sections": SECTIONS, "can_edit": True}
            if facts and caller == MANAGER and facts.manager == MANAGER:
                profile = self.profile_person(employee, hr=False)
                return 200, {"person": profile, "sections": ["overview"], "can_edit": False}
        return super().answer(caller, method, path, params, body)

    def operations(self, name: str) -> list[dict[str, Any]]:
        return [
            c
            for c in self.calls
            if c["method"] == "POST"
            and c["path"] == "hr/operations"
            and isinstance(c["body"], dict)
            and c["body"].get("operation") == name
        ]

    def seed_reminders(self) -> None:
        """Nadia's reminders: her two open steps (the HR pool's payroll, Hana's laptop), one for
        a step that is no longer open, and an older kind."""
        self.processes.notifications = [
            reminder(
                "1", "task_assigned", TASK["payroll"], "New task: Set up the payroll record", day(7)
            ),
            reminder("2", "task_due_soon", TASK["laptop"], "Due soon: Return the laptop", day(1)),
            reminder(
                "3", "task_overdue", TASK["payroll"], "Overdue: Set up the payroll record", day(-2)
            ),
            reminder(
                "4",
                "task_overdue",
                TASK["laptop"],
                "Overdue: Return the laptop",
                day(-1),
                read=True,
            ),
            reminder("5", "task_overdue", STALE_TASK, "Overdue: Book the welcome lunch", day(-3)),
            reminder("6", "workflow_review", OLD_RESOURCE, None, None),
        ]


def section(page: Page) -> Locator:
    return page.get_by_test_id("hr-person-processes")


def reminders(page: Page) -> Locator:
    return page.get_by_test_id("hr-task-reminders")


def reminder_row(page: Page, key: str) -> Locator:
    return page.get_by_test_id(f"hr-reminder-{NOTE[key]}")


@asynccontextmanager
async def person_page(
    browser: Any,
    site: Site,
    world: PersonWorld,
    path: str,
    *,
    caller: str = OWNER,
    role: str | None = None,
    ready: Any = section,
    width: int = 1280,
    theme: str | None = None,
) -> AsyncIterator[Page]:
    """`path` signed in as `caller` (Nadia, the HR owner and a tenant admin, by default; `role`
    overrides the console role) once `ready(page)` is visible."""
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


def assert_operation(call: dict[str, Any], body: dict[str, Any]) -> None:
    """One operation write: the exact body, with CSRF and an Idempotency-Key."""
    assert call["path"] == "hr/operations", call
    assert call["params"] == {"client_id": TENANT}, call
    assert call["body"] == body, call
    assert call["key"], call
    assert call["csrf"] == "synthetic-csrf", call


async def until(check: Callable[[], bool], timeout: float = 15) -> None:
    """Wait until `check()` holds (a stub-side count the page cannot show)."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not check():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.05)


async def focused_inside(page: Page, locator: Locator) -> bool:
    """Focus is on `locator` or inside it (never the page body)."""
    return await locator.evaluate(
        "(el) => document.activeElement !== document.body && el.contains(document.activeElement)"
    )


# ---- The profile section ----


async def test_hr_sees_progress_dates_the_no_template_notice_and_a_link_to_each_process(
    site: Site,
) -> None:
    async def body(browser: Any) -> None:
        world = PersonWorld()
        leaving = world.processes.template(TEMPLATE["leaving"])
        assert leaving
        leaving.is_active = False
        async with person_page(browser, site, world, LINA_PROFILE) as page:
            panel = section(page)
            await expect(
                panel.get_by_role("heading", name="Onboarding & offboarding")
            ).to_be_visible()
            await expect(
                panel.get_by_role("heading", name="Onboarding", exact=True)
            ).to_be_visible()
            await expect(
                panel.get_by_role("heading", name="Offboarding", exact=True)
            ).to_be_visible()
            item = panel.get_by_test_id(f"hr-person-process-{PROCESS['onboarding']}")
            await expect(item).to_contain_text("Warehouse onboarding")
            await expect(item).to_contain_text("In progress")
            await expect(item).to_contain_text("0 of 6 steps")
            await expect(item).to_contain_text("Next: Read the safety handbook")
            await expect(item).to_contain_text("Overdue by 3 days")
            link = item.get_by_role("link", name="Open process")
            await expect(link).to_have_attribute(
                "href", f"/portal/hr/processes/{PROCESS['onboarding']}"
            )
            await expect(link).to_have_accessible_description("Warehouse onboarding")
            # The dates the steps count from, in words.
            dates = panel.get_by_test_id("hr-person-process-dates")
            await expect(dates).to_contain_text(f"Start date{date_text(day(-5))}")
            await expect(dates).to_contain_text(f"Probation end{date_text(day(85))}")
            # No offboarding template matches: HR is told so and can make one.
            notice = panel.get_by_test_id("hr-person-no-template-offboarding")
            await expect(notice).to_contain_text("No offboarding template matches this person.")
            await expect(notice.get_by_role("link", name="Create a template")).to_have_attribute(
                "href", "/portal/hr/processes/templates/new"
            )
            await expect(panel.get_by_test_id("hr-person-no-template-onboarding")).to_have_count(0)
            text = await panel.inner_text()
            assert not UUID_TEXT.search(text) and "_" not in text, text
            reads = [c for c in world.calls if c["path"] == f"hr/people/{LINA}/processes"]
            assert reads and all(c["params"] == {"client_id": TENANT} for c in reads), reads

    await with_browser(body)


async def test_probation_end_is_set_and_cleared_with_the_person_version(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = PersonWorld()
        async with person_page(browser, site, world, LINA_PROFILE) as page:
            panel = section(page)
            when = panel.get_by_label("Probation end")
            await expect(when).to_have_value(day(85))
            await when.fill(day(90))
            await panel.get_by_role("button", name="Save", exact=True).click()
            notice = panel.get_by_test_id("hr-probation-outcome")
            await expect(notice).to_have_text("Probation end saved.")
            assert await focused_inside(page, notice)
            [saved] = world.operations("person.probation_set")
            assert_operation(
                saved,
                {
                    "operation": "person.probation_set",
                    "employee_id": LINA,
                    "expected_version": 3,
                    "probation_end": day(90),
                },
            )
            dates = panel.get_by_test_id("hr-person-process-dates")
            await expect(dates).to_contain_text(f"Probation end{date_text(day(90))}")
            await expect(when).to_have_value(day(90))
            # Clear sends null with the version the save moved to.
            await panel.get_by_role("button", name="Clear", exact=True).click()
            await expect(notice).to_have_text("Probation end cleared.")
            assert await focused_inside(page, notice)
            cleared = world.operations("person.probation_set")[1]
            assert_operation(
                cleared,
                {
                    "operation": "person.probation_set",
                    "employee_id": LINA,
                    "expected_version": 4,
                    "probation_end": None,
                },
            )
            assert cleared["key"] != saved["key"]
            await expect(dates).not_to_contain_text("Probation end")
            await expect(when).to_have_value("")
            await expect(panel.get_by_role("button", name="Clear", exact=True)).to_have_count(0)
            # An empty date is not sent.
            await panel.get_by_role("button", name="Save", exact=True).click()
            await expect(notice).to_have_text("Choose a date.")
            assert len(world.operations("person.probation_set")) == 2

    await with_browser(body)


async def test_probation_end_from_the_keyboard_and_a_stale_version(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = PersonWorld()
        async with person_page(browser, site, world, LINA_PROFILE) as page:
            panel = section(page)
            when = panel.get_by_label("Probation end")
            await expect(when).to_have_value(day(85))
            # Someone else changed Lina since the page loaded.
            world.processes.persons[LINA].version = 7
            await when.focus()
            await when.fill(day(95))
            await page.keyboard.press("Enter")
            notice = panel.get_by_test_id("hr-probation-outcome")
            await expect(notice).to_contain_text(PERSON_CHANGED)
            assert await focused_inside(page, notice)
            # The same refusal again still lands focus on the notice, not the page.
            await panel.get_by_role("button", name="Save", exact=True).focus()
            await page.keyboard.press("Enter")
            await until(lambda: len(world.operations("person.probation_set")) == 2)
            await expect(notice).to_contain_text(PERSON_CHANGED)
            assert await focused_inside(page, notice)
            versions = [
                c["body"]["expected_version"] for c in world.operations("person.probation_set")
            ]
            assert versions == [3, 3], versions
            # Reload reads the person again; the next save sends the new version.
            await notice.get_by_role("button", name="Reload").click()
            await when.fill(day(95))
            await panel.get_by_role("button", name="Save", exact=True).click()
            await expect(notice).to_have_text("Probation end saved.")
            assert world.operations("person.probation_set")[-1]["body"]["expected_version"] == 7
            assert world.processes.persons[LINA].probation_end == day(95)

    await with_browser(body)


async def test_manager_sees_progress_of_a_report_without_notes_dates_or_hr_controls(
    site: Site,
) -> None:
    async def body(browser: Any) -> None:
        world = PersonWorld()
        leaving = world.processes.template(TEMPLATE["leaving"])
        assert leaving
        leaving.is_active = False
        async with person_page(browser, site, world, LINA_PROFILE, caller=MANAGER) as page:
            panel = section(page)
            item = panel.get_by_test_id(f"hr-person-process-{PROCESS['onboarding']}")
            await expect(item).to_contain_text("0 of 6 steps")
            await expect(item).to_contain_text("Next: Read the safety handbook")
            await expect(panel.get_by_role("link")).to_have_count(0)
            await expect(panel.get_by_role("button")).to_have_count(0)
            await expect(panel.get_by_label("Probation end")).to_have_count(0)
            await expect(panel.get_by_test_id("hr-person-process-dates")).to_have_count(0)
            await expect(panel).not_to_contain_text("No offboarding template")
            # Steps' notes stay with the assignee and HR.
            await expect(page.get_by_text("Read chapters 1 to 3.")).to_have_count(0)
            assert {c["path"] for c in world.calls if "processes" in c["path"]} == {
                f"hr/people/{LINA}/processes"
            }

    await with_browser(body)


async def test_the_person_sees_their_own_progress_on_my_hr(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = PersonWorld()
        leaving = world.processes.template(TEMPLATE["leaving"])
        assert leaving
        leaving.is_active = False
        async with person_page(browser, site, world, HOME, caller=STAFF) as page:
            panel = section(page)
            item = panel.get_by_test_id(f"hr-person-process-{PROCESS['onboarding']}")
            await expect(item).to_contain_text("Warehouse onboarding")
            await expect(item).to_contain_text("0 of 6 steps")
            # Her own next step.
            await expect(item).to_contain_text("Next: Read the safety handbook")
            dates = panel.get_by_test_id("hr-person-process-dates")
            await expect(dates).to_contain_text(f"Probation end{date_text(day(85))}")
            await expect(panel.get_by_role("link")).to_have_count(0)
            await expect(panel.get_by_role("button")).to_have_count(0)
            await expect(panel.get_by_label("Probation end")).to_have_count(0)
            await expect(panel).not_to_contain_text("template")

    await with_browser(body)


async def test_no_section_for_a_viewer_the_api_does_not_serve(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = PersonWorld()
        async with person_page(
            browser,
            site,
            world,
            HOME,
            caller=MANAGER,
            ready=lambda p: p.get_by_test_id("hr-my-tasks"),
        ) as page:
            reads = lambda: [  # noqa: E731
                c for c in world.calls if c["path"] == f"hr/people/{EMPLOYEE[MANAGER]}/processes"
            ]
            await until(lambda: len(reads()) > 0)
            await expect(section(page)).to_have_count(0)

    await with_browser(body)


# ---- Rehire ----


async def open_rehire(page: Page) -> Locator:
    await page.get_by_role("button", name="Rehire", exact=True).click()
    form = page.get_by_test_id("hr-rehire-form")
    await expect(form).to_be_visible()
    return form


async def test_rehire_asks_first_when_an_offboarding_is_open(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = PersonWorld()
        async with person_page(browser, site, world, HANA_PROFILE) as page:
            form = await open_rehire(page)
            submit = form.get_by_role("button", name="Rehire", exact=True)
            await submit.click()
            group = form.get_by_test_id("hr-rehire-confirm-group")
            await expect(group.get_by_test_id("hr-rehire-confirm-question")).to_have_text(
                REHIRE_QUESTION
            )
            await expect(group.get_by_role("button", name="Keep the offboarding")).to_be_focused()
            assert world.operations("person.rehire") == []
            # Keep sends nothing and hands focus back to Rehire.
            await group.get_by_role("button", name="Keep the offboarding").click()
            await expect(group).to_have_count(0)
            await expect(submit).to_be_focused()
            assert world.operations("person.rehire") == []
            # Escape closes the question too.
            await page.keyboard.press("Enter")
            await expect(group).to_be_visible()
            await page.keyboard.press("Escape")
            await expect(group).to_have_count(0)
            assert world.operations("person.rehire") == []
            await submit.click()
            await group.get_by_role("button", name="Continue").click()
            await expect(page.get_by_text("Rehired. Invite them again")).to_be_visible()
            [rehire] = world.operations("person.rehire")
            assert_operation(
                rehire,
                {
                    "operation": "person.rehire",
                    "employee_id": HANA,
                    "expected_version": 5,
                    "start_date": kuala_lumpur_today().isoformat(),
                },
            )
            offboarding = world.processes.process(PROCESS["offboarding"])
            assert offboarding and offboarding.status == "cancelled"
            item = section(page).get_by_test_id(f"hr-person-process-{PROCESS['offboarding']}")
            await expect(item).to_contain_text("Cancelled")

    await with_browser(body)


async def test_rehire_without_an_open_offboarding_sends_straight_away(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = PersonWorld()
        offboarding = world.processes.process(PROCESS["offboarding"])
        assert offboarding
        offboarding.status = "completed"
        async with person_page(browser, site, world, HANA_PROFILE) as page:
            form = await open_rehire(page)
            await until(
                lambda: (
                    len([c for c in world.calls if c["path"] == f"hr/people/{HANA}/processes"]) > 1
                )
            )
            await form.get_by_role("button", name="Rehire", exact=True).click()
            await expect(page.get_by_text("Rehired. Invite them again")).to_be_visible()
            assert len(world.operations("person.rehire")) == 1
            await expect(page.get_by_test_id("hr-rehire-confirm-group")).to_have_count(0)

    await with_browser(body)


async def test_rehire_still_works_when_the_processes_cannot_be_read(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = PersonWorld()
        async with person_page(browser, site, world, HANA_PROFILE) as page:
            # The form's read fails (the dev server runs effects twice, so every one of them).
            world.processes.failures[f"GET hr/people/{HANA}/processes"] = [(500, "boom")] * 4
            form = await open_rehire(page)
            await expect(form.get_by_test_id("hr-rehire-check-failed")).to_have_text(
                "We couldn't check for an open offboarding. If one is open, rehiring cancels it "
                "and skips its remaining steps."
            )
            await form.get_by_role("button", name="Rehire", exact=True).click()
            await expect(page.get_by_text("Rehired. Invite them again")).to_be_visible()
            assert len(world.operations("person.rehire")) == 1
            world.processes.failures.clear()

    await with_browser(body)


# ---- Home reminders ----


async def test_home_reminders_show_their_line_grouped_and_link_to_my_tasks(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = PersonWorld()
        world.seed_reminders()
        async with person_page(browser, site, world, HOME, ready=reminders) as page:
            block = reminders(page)
            # More than a few: grouped by kind with a count, most urgent first.
            headings = block.get_by_role("heading")
            await expect(headings).to_have_text(
                ["Overdue (2)", "Due soon (1)", "New tasks (1)", "Already done (1)"]
            )
            overdue = reminder_row(page, "3")
            await expect(overdue).to_contain_text("Overdue: Set up the payroll record")
            await expect(overdue).to_contain_text("Overdue by 2 days")
            await expect(reminder_row(page, "2")).to_contain_text("Due tomorrow")
            await expect(reminder_row(page, "1")).to_contain_text("Due in 7 days")
            for key in ("1", "2", "3", "4"):
                link = reminder_row(page, key).get_by_role("link")
                await expect(link).to_have_attribute("href", "#hr-my-tasks")
            # Read ones have no Mark read; unread ones name their reminder.
            await expect(reminder_row(page, "4").get_by_role("button")).to_have_count(0)
            mark = reminder_row(page, "3").get_by_role("button", name="Mark read")
            await expect(mark).to_have_accessible_description("Overdue: Set up the payroll record")
            # The step that is no longer open is history: no link, no button.
            stale = reminder_row(page, "5")
            await expect(stale).to_contain_text("Overdue: Book the welcome lunch")
            await expect(stale).to_contain_text("Done")
            await expect(stale.get_by_role("link")).to_have_count(0)
            await expect(stale.get_by_role("button")).to_have_count(0)
            text = await block.inner_text()
            assert not UUID_TEXT.search(text) and "task_" not in text, text
            # An older kind reads as it always did.
            old = reminder_row(page, "6")
            await expect(old).to_contain_text(f"workflow review · {OLD_RESOURCE}")
            await expect(old.get_by_role("button", name="Mark read")).to_be_visible()
            # The link goes to My tasks on the same page.
            await reminder_row(page, "3").get_by_role("link").click()
            await expect(page).to_have_url(re.compile(r"#hr-my-tasks$"))
            await expect(page.get_by_test_id("hr-my-tasks")).to_be_in_viewport()

    await with_browser(body)


async def test_a_few_reminders_are_not_grouped(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = PersonWorld()
        world.seed_reminders()
        world.processes.notifications = world.processes.notifications[:2]
        async with person_page(browser, site, world, HOME, ready=reminders) as page:
            await expect(reminder_row(page, "1")).to_be_visible()
            await expect(reminder_row(page, "2")).to_be_visible()
            await expect(reminders(page).get_by_role("heading")).to_have_count(0)

    await with_browser(body)


async def test_mark_read_on_one_reminder(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = PersonWorld()
        world.seed_reminders()
        async with person_page(browser, site, world, HOME, ready=reminders) as page:
            await reminder_row(page, "2").get_by_role("button", name="Mark read").click()
            notice = page.get_by_test_id("hr-reminders-outcome")
            await expect(notice).to_have_text("Marked as read.")
            assert await focused_inside(page, notice)
            assert world.processes.notification_reads == [NOTE["2"]]
            [call] = world.operations("notification.read")
            assert_operation(call, {"operation": "notification.read", "notification_id": NOTE["2"]})
            await expect(reminder_row(page, "2").get_by_role("button")).to_have_count(0)

    await with_browser(body)


async def test_mark_all_read_goes_one_by_one_in_screen_order(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = PersonWorld()
        world.seed_reminders()
        async with person_page(browser, site, world, HOME, ready=reminders) as page:

            async def slow(route: Route) -> None:
                await page.wait_for_timeout(300)
                await route.fallback()

            await page.route("**/console/v1/hr/operations**", slow)
            await page.get_by_role("button", name="Mark all read").click()
            await expect(page.get_by_test_id("hr-reminders-progress")).to_have_text(
                "Marking 1 of 5…"
            )
            notice = page.get_by_test_id("hr-reminders-outcome")
            await expect(notice).to_have_text("Marked 5 reminders as read.", timeout=WAIT)
            assert await focused_inside(page, notice)
            # Overdue, due soon, new, done, then the older kind; the read one is skipped.
            assert world.processes.notification_reads == [
                NOTE[key] for key in ("3", "2", "1", "5", "6")
            ]
            await expect(page.get_by_role("button", name="Mark read")).to_have_count(0)
            await expect(page.get_by_role("button", name="Mark all read")).to_have_count(0)

    await with_browser(body)


async def test_mark_all_read_stops_on_the_first_error(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = PersonWorld()
        world.seed_reminders()
        world.processes.read_failures[NOTE["1"]] = (500, "boom")
        async with person_page(browser, site, world, HOME, ready=reminders) as page:
            mark_all = page.get_by_role("button", name="Mark all read")
            await mark_all.click()
            notice = page.get_by_test_id("hr-reminders-outcome")
            await expect(notice).to_contain_text(UNCERTAIN_MARK_ALL)
            await expect(notice).to_contain_text("Marked 2 of 5 as read.")
            assert await focused_inside(page, notice)
            assert world.processes.notification_reads == [NOTE["3"], NOTE["2"], NOTE["1"]]
            first = [c["key"] for c in world.operations("notification.read")]
            # Again: the rest, with the same key for the one that may have landed.
            await mark_all.click()
            await expect(notice).to_have_text("Marked 3 reminders as read.")
            assert world.processes.notification_reads[3:] == [NOTE["1"], NOTE["5"], NOTE["6"]]
            again = [c["key"] for c in world.operations("notification.read")]
            assert again[3] == first[2], (first, again)

    await with_browser(body)


async def test_mark_all_read_refused_twice_keeps_focus_on_the_notice(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = PersonWorld()
        world.seed_reminders()
        async with person_page(browser, site, world, HOME, ready=reminders) as page:
            mark_all = page.get_by_role("button", name="Mark all read")
            notice = page.get_by_test_id("hr-reminders-outcome")
            for attempt in (1, 2):
                world.processes.read_failures[NOTE["3"]] = (404, "resource_unavailable")
                await mark_all.focus()
                await page.keyboard.press("Enter")
                await until(lambda n=attempt: len(world.processes.notification_reads) == n)
                await expect(notice).to_contain_text("Marked 0 of 5 as read.")
                assert await focused_inside(page, notice), attempt

    await with_browser(body)


# ---- Phone widths ----


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
async def test_section_and_reminders_fit_phone_widths_in_three_themes(
    site: Site, theme: str
) -> None:
    async def body(browser: Any) -> None:
        world = PersonWorld()
        world.seed_reminders()
        leaving = world.processes.template(TEMPLATE["leaving"])
        assert leaving
        leaving.is_active = False
        async with person_page(browser, site, world, LINA_PROFILE, theme=theme, width=320) as page:
            await expect(page.locator("html")).to_have_attribute("data-theme", theme)
            for path, ready in ((LINA_PROFILE, section), (HOME, reminders)):
                if path != LINA_PROFILE:
                    await page.goto(f"{site.base}{path}")
                    await expect(ready(page)).to_be_visible(timeout=WAIT)
                for width in (320, 390, 768):
                    await page.set_viewport_size({"width": width, "height": 900})
                    await page.wait_for_timeout(150)
                    where = (theme, path, width)
                    measured = await overflow(page)
                    assert measured["scroll"] <= measured["inner"], (where, measured)
                    sideways = await ready(page).evaluate(SIDEWAYS)
                    assert sideways == {"out": [], "scrollers": []}, (where, sideways)

    await with_browser(body)


async def test_read_only_session_sees_the_section_without_write_controls(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = PersonWorld()
        leaving = world.processes.template(TEMPLATE["leaving"])
        assert leaving
        leaving.is_active = False
        world.seed_reminders()
        async with person_page(browser, site, world, LINA_PROFILE, role="read_only") as page:
            panel = section(page)
            item = panel.get_by_test_id(f"hr-person-process-{PROCESS['onboarding']}")
            await expect(item).to_contain_text("0 of 6 steps")
            await expect(panel.get_by_test_id("hr-person-no-template-offboarding")).to_be_visible()
            await expect(panel.get_by_role("link", name="Create a template")).to_have_count(0)
            await expect(panel.get_by_role("button")).to_have_count(0)
            await expect(panel.get_by_label("Probation end")).to_have_count(0)
            await page.goto(f"{site.base}{HOME}")
            await expect(reminders(page)).to_be_visible(timeout=WAIT)
            await expect(page.get_by_role("button", name="Mark all read")).to_have_count(0)
            assert world.operations("person.probation_set") == []

    await with_browser(body)
