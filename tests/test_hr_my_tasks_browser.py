"""Real browser checks for My tasks (HR processes console, Task 3).

The staged console runs from the copy `test_services_redesign_browser.site` builds, as in
`test_hr_restyle_browser.py`. Playwright answers every browser-side `/console/v1/**` call from
`TasksWorld`: the restyle company (`HomeWorld`) whose process steps come from the shared stub
`tests/hr_process_world.py`, seeded with Lina's onboarding and Hana's offboarding. Each test signs
in as one person: Lina (the assignee), Farid (her manager, who approves), Nadia (the HR owner, on
Home) or Hana (a read-only member). Any call the stub does not expect fails the test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from playwright.async_api import Locator, Page, expect

from tests import test_services_redesign_browser as services
from tests.hr_process_world import TASK, Member
from tests.test_hr_access_browser import EMPLOYEE, HR_PACKAGE, MANAGER, OWNER, STAFF
from tests.test_hr_restyle_browser import HomeWorld
from tests.test_services_browser import TENANT, WAIT, make_me
from tests.test_services_redesign_browser import Site, with_browser
from tests.test_team_browser import THEMES, WIDTHS, overflow

pytestmark = pytest.mark.asyncio

# The staged copy of the console is built once per file by the services fixture.
site = services.site

READ_ONLY = "44444444-4444-4444-8444-333333333333"
READ_ONLY_EMPLOYEE = "55555555-5555-4555-8555-000000000004"
MEMBERS = {
    OWNER: Member(OWNER, EMPLOYEE[OWNER], "Nadia Hassan"),
    MANAGER: Member(MANAGER, EMPLOYEE[MANAGER], "Farid Ismail"),
    STAFF: Member(STAFF, EMPLOYEE[STAFF], "Lina Wong"),
    READ_ONLY: Member(READ_ONLY, READ_ONLY_EMPLOYEE, "Hana Yusof"),
}
HOME = "/portal/hr"
EMPTY = "Nothing waiting for you."
REASON_SENTENCE = "Give a reason of 1 to 500 characters."
CHECK_NOT_MET = "The automatic check for this step is not met yet."
TASK_CHANGED = "Someone changed this step since you opened it. Reload to see it."


class TasksWorld(HomeWorld):
    """The restyle company with My tasks seeded, and My HR's reads for members without HR duties."""

    def __init__(self) -> None:
        super().__init__(empty=False)
        self.processes.seed_company(
            hr=MEMBERS[OWNER],
            manager=MEMBERS[MANAGER],
            staff=MEMBERS[STAFF],
            read_only=MEMBERS[READ_ONLY],
        )

    def answer(
        self, caller: str, method: str, path: str, params: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        member = MEMBERS.get(caller)
        if caller != OWNER and member and params.get("client_id") == TENANT and method == "GET":
            employee = member.employee_id
            if path == "hr/context" and caller == READ_ONLY:
                return 200, {
                    "employee": {"tenant_id": TENANT, "employee_id": employee},
                    "client_owner": False,
                    "private_areas": [],
                    "actions": [],
                    "switches": [],
                    "workflows": [],
                    "hr_duties": False,
                }
            if path == f"hr/people/{employee}":
                return 200, {
                    "person": self.own(member),
                    "sections": ["overview"],
                    "can_edit": False,
                }
            if path == f"hr/people/{employee}/personal":
                return 200, {
                    key: None
                    for key in (
                        "ic_or_passport",
                        "date_of_birth",
                        "home_address",
                        "personal_phone",
                        "emergency_name",
                        "emergency_relationship",
                        "emergency_phone",
                        "bank_name",
                        "bank_account",
                        "tax_number",
                        "epf_number",
                        "socso_number",
                        "version",
                    )
                } | {"can_edit": "contact"}
            if path in {f"hr/people/{employee}/documents", "hr/records/balances"}:
                return 200, {"items": []}
        return super().answer(caller, method, path, params, body)

    def own(self, member: Member) -> dict[str, Any]:
        return {
            "employee_id": member.employee_id,
            "display_name": member.name,
            "job_title": "Packer",
            "department_id": None,
            "department": "Warehouse",
            "manager_id": None,
            "manager_name": None,
            "work_email": None,
            "work_phone": None,
            "start_date": "2026-11-02",
            "employment_type": "full_time",
            "status": "active",
            "employment_profile": None,
            "version": 1,
            "account_linked": True,
        }

    def task_posts(self, action: str | None = None) -> list[dict[str, Any]]:
        return [
            call
            for call in self.calls
            if call["method"] == "POST"
            and call["path"].startswith("hr/tasks/")
            and (action is None or call["path"].endswith(f"/{action}"))
        ]

    def task_reads(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["path"] == "hr/tasks/mine"]


def my_tasks(page: Page) -> Locator:
    return page.get_by_test_id("hr-my-tasks")


def row(page: Page, name: str) -> Locator:
    """The table row of a step (the table shows from 768 px)."""
    return page.get_by_test_id(f"hr-task-row-{TASK[name]}")


def card(page: Page, name: str) -> Locator:
    """The card of a step (below 768 px)."""
    return page.get_by_test_id(f"hr-task-card-{TASK[name]}")


def dialog(page: Page) -> Locator:
    return page.get_by_test_id("hr-task-dialog")


def outcome(page: Page) -> Locator:
    """The saved or refused message; focus lands here when the row button is gone after a write."""
    return page.get_by_test_id("hr-my-tasks-outcome")


@asynccontextmanager
async def tasks_page(
    browser: Any,
    site: Site,
    world: TasksWorld,
    caller: str,
    *,
    ready: Any = my_tasks,
    width: int = 1280,
    theme: str | None = None,
) -> AsyncIterator[Page]:
    """People & HR Home signed in as `caller` once `ready(page)` is visible: the HR owner as the
    tenant admin, Hana as a read-only member, everyone else as staff."""
    role = {OWNER: "tenant_admin", READ_ONLY: "read_only"}.get(caller, "tenant_staff")
    me = make_me(role)
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
    await page.goto(f"{site.base}{HOME}")
    await expect(ready(page)).to_be_visible(timeout=WAIT)
    try:
        yield page
        assert not errors, errors
        assert not world.unexpected, world.unexpected
        assert not site.upstream.unexpected, site.upstream.unexpected
    finally:
        await context.close()


def assert_write(call: dict[str, Any], action: str, name: str, body: dict[str, Any]) -> None:
    """One task write: the path, method and exact body, with CSRF and an Idempotency-Key."""
    assert call["method"] == "POST", call
    assert call["path"] == f"hr/tasks/{TASK[name]}/{action}", call
    assert call["params"] == {"client_id": TENANT}, call
    assert call["body"] == body, call
    assert call["key"], call
    assert call["csrf"] == "synthetic-csrf", call


async def test_assignee_marks_a_step_done_with_a_note_and_adds_a_note(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TasksWorld()
        async with tasks_page(browser, site, world, STAFF) as page:
            sign = row(page, "sign")
            await expect(sign).to_be_visible(timeout=WAIT)
            # Title, the Malay line, the process and person, due, status and the reference note.
            await expect(sign).to_contain_text("Sign the employment contract")
            await expect(sign.locator("[lang=ms]")).to_have_text("Tandatangan kontrak pekerjaan")
            await expect(sign).to_contain_text("Warehouse onboarding")
            await expect(sign).to_contain_text("Lina Wong")
            await expect(sign).to_contain_text("Due in 2 days")
            await expect(sign).to_contain_text("To do")
            await expect(sign).to_contain_text("Read the contract and sign the last page.")
            reference = sign.get_by_test_id("hr-task-reference")
            await expect(reference).to_have_text(
                "Reference: Contract template: HR shared folder\nAsk HR for the signing slot",
                use_inner_text=True,
            )
            # Lina's own open steps only, soonest first; done ones and others' steps are not here.
            rows = my_tasks(page).locator("[data-testid^=hr-task-row-]")
            await expect(rows).to_have_count(5)
            ids = await rows.evaluate_all("(els) => els.map((el) => el.dataset.testid)")
            order = ["handbook", "details", "sign", "badge", "kit"]
            assert ids == [f"hr-task-row-{TASK[name]}" for name in order], ids
            await expect(row(page, "payroll")).to_have_count(0)
            # The sent-back step says why; the step HR did for her says so.
            await expect(row(page, "handbook")).to_contain_text(
                "Sent back: Please sign the last page too."
            )
            await expect(row(page, "handbook")).to_contain_text("Note: Read chapters 1 to 3.")
            await expect(row(page, "handbook")).to_contain_text("Overdue by 3 days")
            badge = row(page, "badge")
            await expect(badge).to_contain_text("Done by HR")
            await expect(badge).to_contain_text("Waiting for approval")
            await expect(badge.get_by_role("button", name="Mark done")).to_have_count(0)
            await expect(badge.get_by_role("button", name="Add note")).to_be_visible()
            await expect(sign).not_to_contain_text("Done by HR")

            # Mark done asks for an optional note and sends it with the step's version.
            await sign.get_by_role("button", name="Mark done").click()
            await expect(dialog(page)).to_be_visible()
            await (
                dialog(page).get_by_label("Note (optional)").fill("Signed.\nCopy left at the desk.")
            )
            await dialog(page).get_by_role("button", name="Mark done").click()
            await expect(dialog(page)).to_have_count(0, timeout=WAIT)
            await expect(sign).to_contain_text("Waiting for approval", timeout=WAIT)
            await expect(page.get_by_test_id("hr-my-tasks-notice")).to_contain_text("Marked done")
            # Its button is gone after the reload, so focus is on the message, never the page body.
            await expect(outcome(page)).to_be_focused()
            (complete,) = world.task_posts("complete")
            assert_write(
                complete,
                "complete",
                "sign",
                {"expected_version": 1, "note": "Signed.\nCopy left at the desk."},
            )
            await expect(sign.get_by_role("button", name="Mark done")).to_have_count(0)

            # Add note replaces the shared note; it takes no version.
            await row(page, "kit").get_by_role("button", name="Add note").click()
            note = dialog(page).get_by_label("Note")
            await expect(note).to_have_value("Size M boots.")
            await note.fill("Size M boots and L gloves.")
            await dialog(page).get_by_role("button", name="Save note").click()
            await expect(row(page, "kit")).to_contain_text(
                "Note: Size M boots and L gloves.", timeout=WAIT
            )
            (noted,) = world.task_posts("note")
            assert_write(noted, "note", "kit", {"note": "Size M boots and L gloves."})
            # The row and its button are still there: focus goes back to it.
            await expect(row(page, "kit").get_by_role("button", name="Add note")).to_be_focused()

    await with_browser(body)


async def test_automatic_check_says_when_it_is_not_met(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TasksWorld()
        async with tasks_page(browser, site, world, STAFF) as page:
            details = row(page, "details")
            await expect(details).to_contain_text("Due today", timeout=WAIT)
            await expect(details.get_by_test_id("hr-task-check")).to_have_text(
                "Checked automatically: your personal details are filled in."
            )
            await expect(details.get_by_role("button", name="Mark done")).to_have_count(0)
            await details.get_by_role("button", name="Check now").click()
            await expect(page.get_by_test_id("hr-my-tasks-error")).to_have_text(
                CHECK_NOT_MET, timeout=WAIT
            )
            # Not met reloads nothing: focus goes back to the button, never the page body.
            await expect(details.get_by_role("button", name="Check now")).to_be_focused()
            (check,) = world.task_posts("complete")
            assert_write(check, "complete", "details", {"expected_version": 1})
            # Met now: the same button completes it and the step leaves the list.
            step = world.processes.step(TASK["details"])
            assert step
            step.check_met = True
            await details.get_by_role("button", name="Check now").click()
            await expect(details).to_have_count(0, timeout=WAIT)
            await expect(page.get_by_test_id("hr-my-tasks-error")).to_have_count(0)
            await expect(outcome(page)).to_be_focused()

    await with_browser(body)


async def test_manager_approves_once_and_sends_back_with_a_reason(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TasksWorld()
        async with tasks_page(browser, site, world, MANAGER) as page:
            kit, badge = row(page, "kit"), row(page, "badge")
            await expect(kit).to_be_visible(timeout=WAIT)
            await expect(my_tasks(page).locator("[data-testid^=hr-task-row-]")).to_have_count(2)
            await expect(kit).to_contain_text("Pick up the safety kit")
            await expect(kit).to_contain_text("Lina Wong")
            # The assignee's text is withheld from the approver: nothing stands in its place.
            panel = my_tasks(page)
            for withheld in (
                "Boots, gloves and a vest",
                "Store room key",
                "Size M boots",
                "The badge desk",
                "Done by HR",
                "No description",
                "Reference:",
                "Note:",
                "Sent back:",
                "For ",
            ):
                await expect(panel).not_to_contain_text(withheld)
            await expect(kit.get_by_role("button", name="Mark done")).to_have_count(0)
            await expect(kit.get_by_role("button", name="Add note")).to_have_count(0)

            # A double click on Approve sends one request.
            await kit.get_by_role("button", name="Approve").dblclick()
            await expect(kit).to_have_count(0, timeout=WAIT)
            (approve,) = world.task_posts("approve")
            assert_write(approve, "approve", "kit", {"expected_version": 2})
            await expect(page.get_by_test_id("hr-my-tasks-notice")).to_have_text("Approved.")
            await expect(outcome(page)).to_be_focused()

            # Send back needs a reason of 1 to 500 characters.
            await badge.get_by_role("button", name="Send back").click()
            ask = dialog(page)
            send = ask.get_by_role("button", name="Send back")
            await expect(send).to_be_disabled()
            reason = ask.get_by_label("Reason")
            await reason.fill("   ")
            await expect(send).to_be_disabled()
            await reason.fill("x" * 501)
            await expect(ask.get_by_text(REASON_SENTENCE)).to_be_visible()
            await expect(send).to_be_disabled()
            await reason.fill("The badge photo is missing.")
            await expect(ask.get_by_text(REASON_SENTENCE)).to_have_count(0)
            await send.click()
            await expect(ask).to_have_count(0, timeout=WAIT)
            (back,) = world.task_posts("send-back")
            assert_write(
                back,
                "send-back",
                "badge",
                {"expected_version": 2, "reason": "The badge photo is missing."},
            )
            await expect(panel.get_by_text(EMPTY)).to_be_visible(timeout=WAIT)
            await expect(outcome(page)).to_be_focused()
            assert len(world.task_posts()) == 2

    await with_browser(body)


async def test_assignee_sees_the_reason_a_step_was_sent_back(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TasksWorld()
        world.processes.touch(
            TASK["badge"],
            status="todo",
            sent_back_reason="The badge photo is missing.",
            done_by_hr=False,
        )
        async with tasks_page(browser, site, world, STAFF) as page:
            badge = row(page, "badge")
            await expect(badge).to_contain_text(
                "Sent back: The badge photo is missing.", timeout=WAIT
            )
            await expect(badge).to_contain_text("To do")
            await expect(badge.get_by_role("button", name="Mark done")).to_be_visible()

    await with_browser(body)


async def test_hr_owner_marks_a_step_done_for_a_member_on_home(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TasksWorld()
        async with tasks_page(browser, site, world, OWNER) as page:
            laptop, payroll = row(page, "laptop"), row(page, "payroll")
            await expect(laptop).to_be_visible(timeout=WAIT)
            # On Home, My tasks sits under "Needs you today" and above the reminders.
            today = await page.get_by_role("heading", name="Needs you today").bounding_box()
            tasks = await my_tasks(page).bounding_box()
            reminders = await page.get_by_role("heading", name="Local HR reminders").bounding_box()
            assert today and tasks and reminders, (today, tasks, reminders)
            assert today["y"] < tasks["y"] < reminders["y"], (today, tasks, reminders)
            # Hana cannot get reminders, so HR sees her step on her behalf.
            await expect(laptop).to_contain_text("Return the laptop")
            await expect(laptop.locator("[lang=ms]")).to_have_text("Pulangkan komputer riba")
            await expect(laptop).to_contain_text("For Hana Yusof")
            await expect(laptop).to_contain_text("Offboarding")
            await expect(laptop).to_contain_text("No date yet")
            # The HR pool step.
            await expect(payroll).to_contain_text("HR team")
            await expect(payroll.get_by_role("button", name="Mark done")).to_be_visible()
            # Lina's own steps are hers, not HR's.
            await expect(row(page, "sign")).to_have_count(0)

            for_them = laptop.get_by_role("button", name="Mark done for them")
            await expect(for_them).to_have_accessible_description("Return the laptop")
            await for_them.click()
            await dialog(page).get_by_role("button", name="Mark done for them").click()
            await expect(laptop).to_have_count(0, timeout=WAIT)
            await expect(outcome(page)).to_be_focused()
            (complete,) = world.task_posts("complete")
            assert_write(complete, "complete", "laptop", {"expected_version": 1})

    await with_browser(body)


async def test_read_only_member_sees_rows_without_buttons(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TasksWorld()
        async with tasks_page(browser, site, world, READ_ONLY) as page:
            laptop = row(page, "laptop")
            await expect(laptop).to_contain_text("Return the laptop", timeout=WAIT)
            await expect(laptop).not_to_contain_text("For Hana Yusof")
            await expect(my_tasks(page).get_by_role("button")).to_have_count(0)
            assert world.task_posts() == []

    await with_browser(body)


async def test_a_stale_step_shows_the_sentence_and_reloads(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TasksWorld()
        async with tasks_page(browser, site, world, STAFF) as page:
            sign = row(page, "sign")
            await expect(sign).to_contain_text("Due in 2 days", timeout=WAIT)
            # Someone else moves the due date while the list is open.
            moved = world.processes.touch(TASK["sign"], due_on=None).version
            await sign.get_by_role("button", name="Mark done").click()
            await dialog(page).get_by_role("button", name="Mark done").click()
            await expect(page.get_by_test_id("hr-my-tasks-error")).to_have_text(
                TASK_CHANGED, timeout=WAIT
            )
            await expect(dialog(page)).to_have_count(0)
            await expect(sign).to_contain_text("No date yet", timeout=WAIT)
            # The step is still open after the reload: focus is back on its Mark done.
            await expect(sign.get_by_role("button", name="Mark done")).to_be_focused()
            (stale,) = world.task_posts("complete")
            # The list was read again after the refusal.
            posted = next(i for i, call in enumerate(world.calls) if call is stale)
            later = [c["path"] for c in world.calls[posted + 1 :]]
            assert "hr/tasks/mine" in later, later
            assert stale["body"] == {"expected_version": 1}, stale
            # The reloaded row carries the new version: trying again saves it.
            await sign.get_by_role("button", name="Mark done").click()
            await dialog(page).get_by_role("button", name="Mark done").click()
            await expect(sign).to_contain_text("Waiting for approval", timeout=WAIT)
            await expect(outcome(page)).to_be_focused()
            await expect(page.get_by_test_id("hr-my-tasks-error")).to_have_count(0)
            assert world.task_posts("complete")[-1]["body"] == {"expected_version": moved}
            assert world.task_posts("complete")[-1]["key"] != stale["key"]

    await with_browser(body)


async def test_a_dialog_write_failing_twice_keeps_focus_on_the_message(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TasksWorld()
        path = f"hr/tasks/{TASK['sign']}/complete"
        world.processes.failures[f"POST {path}"] = [
            (500, "synthetic_outage"),
            (500, "synthetic_outage"),
        ]
        async with tasks_page(browser, site, world, STAFF) as page:
            await row(page, "sign").get_by_role("button", name="Mark done").click(timeout=WAIT)
            ask = dialog(page)
            uncertain = "We couldn't confirm that. Choose Mark done again; it won't be done twice."
            for attempt in (1, 2):
                await ask.get_by_role("button", name="Mark done").click()
                for _ in range(100):
                    if len(world.task_posts("complete")) >= attempt:
                        break
                    await page.wait_for_timeout(100)
                # The same refusal again still takes focus: never the page body.
                await expect(ask.locator(":focus")).to_contain_text(uncertain, timeout=WAIT)
            await ask.get_by_role("button", name="Mark done").click()
            await expect(row(page, "sign")).to_contain_text("Waiting for approval", timeout=WAIT)
            posts = world.task_posts("complete")
            assert len(posts) == 3 and len({p["key"] for p in posts}) == 1, posts

    await with_browser(body)


async def test_load_more_follows_the_cursor(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TasksWorld()
        world.processes.page_size = 2
        async with tasks_page(browser, site, world, STAFF) as page:
            rows = my_tasks(page).locator("[data-testid^=hr-task-row-]")
            await expect(rows).to_have_count(2, timeout=WAIT)
            more = my_tasks(page).get_by_role("button", name="Load more")
            await more.click()
            await expect(rows).to_have_count(4, timeout=WAIT)
            await more.click()
            await expect(rows).to_have_count(5, timeout=WAIT)
            await expect(more).to_have_count(0)
            # `next dev` mounts twice, so the first page may be read twice; the cursors follow it.
            cursors = [c["params"].get("after") for c in world.task_reads()]
            assert [c for c in cursors if c] == [TASK["details"], TASK["badge"]], cursors
            assert set(cursors[: cursors.index(TASK["details"])]) == {None}, cursors

            # A write on the third page re-reads all three pages: the list does not fold back.
            reads = len(world.task_reads())
            await row(page, "kit").get_by_role("button", name="Add note").click()
            await dialog(page).get_by_label("Note").fill("Size M boots, collected.")
            await dialog(page).get_by_role("button", name="Save note").click()
            await expect(row(page, "kit")).to_contain_text(
                "Note: Size M boots, collected.", timeout=WAIT
            )
            await expect(rows).to_have_count(5)
            await expect(more).to_have_count(0)
            again = [c["params"].get("after") for c in world.task_reads()[reads:]]
            assert again == [None, TASK["details"], TASK["badge"]], again

    await with_browser(body)


async def test_empty_list_says_nothing_is_waiting(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TasksWorld()
        world.processes.steps = []
        async with tasks_page(browser, site, world, STAFF) as page:
            await expect(my_tasks(page).get_by_text(EMPTY)).to_be_visible(timeout=WAIT)
            await expect(my_tasks(page).get_by_role("heading", name="My tasks")).to_be_visible()

    await with_browser(body)


@pytest.mark.parametrize("theme", THEMES)
async def test_my_tasks_fit_five_widths_in_three_themes(site: Site, theme: str) -> None:
    async def body(browser: Any) -> None:
        world = TasksWorld()
        async with tasks_page(browser, site, world, STAFF, theme=theme) as page:
            await expect(page.locator("html")).to_have_attribute("data-theme", theme)
            await expect(row(page, "sign")).to_be_visible(timeout=WAIT)
            for width in WIDTHS:
                await page.set_viewport_size({"width": width, "height": 900})
                await page.wait_for_timeout(150)
                where = (theme, width)
                measured = await overflow(page)
                assert measured["scroll"] <= measured["inner"], (where, measured)
                table = width >= 768
                await expect(page.get_by_test_id("hr-my-tasks-table")).to_be_visible(visible=table)
                await expect(page.get_by_test_id("hr-my-tasks-cards")).to_be_visible(
                    visible=not table
                )
                shown = row(page, "sign") if table else card(page, "sign")
                await expect(shown.get_by_role("button", name="Mark done")).to_be_visible()
                # Nothing inside the list sticks out sideways.
                out = await my_tasks(page).evaluate(
                    """(root) => [...root.querySelectorAll('*')].filter((el) => {
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && (r.left < -0.5 || r.right > innerWidth + 0.5);
                    }).map((el) => el.dataset.testid || el.tagName)"""
                )
                assert out == [], (where, out)
            # The Mark done dialog fits a phone too.
            await page.set_viewport_size({"width": 320, "height": 640})
            await card(page, "sign").get_by_role("button", name="Mark done").click()
            box = await dialog(page).bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= 320, (theme, box)

    await with_browser(body)


async def test_keyboard_reaches_the_actions_and_dialogs_keep_focus(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TasksWorld()
        async with tasks_page(browser, site, world, STAFF) as page:
            sign = row(page, "sign")
            done = sign.get_by_role("button", name="Mark done")
            await expect(done).to_be_visible(timeout=WAIT)
            # Tab walks the row's actions in reading order, with a visible focus ring.
            await done.focus()
            await page.keyboard.press("Tab")
            await expect(sign.get_by_role("button", name="Add note")).to_be_focused()
            ring = await page.evaluate(
                "() => { const s = getComputedStyle(document.activeElement);"
                " return [s.outlineStyle, parseFloat(s.outlineWidth)]; }"
            )
            assert ring[0] != "none" and ring[1] >= 2, ring
            await page.keyboard.press("Shift+Tab")
            await expect(done).to_be_focused()

            # Enter opens the dialog and focus moves in. The page behind is inert (a native modal
            # dialog): Tab never reaches it, only the dialog's controls or, past the last one, the
            # browser's own chrome (`body` from the page's side), and it comes back round.
            await page.keyboard.press("Enter")
            await expect(dialog(page)).to_be_visible()
            where = (
                "() => { const el = document.activeElement;"
                " if (!el || el === document.body) return 'chrome';"
                " return el.closest('[data-testid=hr-task-dialog]') ? 'dialog' : 'page'; }"
            )
            assert await page.evaluate(where) == "dialog"
            seen = []
            for _ in range(10):
                await page.keyboard.press("Tab")
                seen.append(await page.evaluate(where))
            assert "page" not in seen, seen
            assert seen.count("dialog") >= 6 and seen[-1] in {"dialog", "chrome"}, seen
            # Escape closes it, sends nothing and gives focus back to the button.
            await dialog(page).get_by_label("Note (optional)").focus()
            await page.keyboard.press("Escape")
            await expect(dialog(page)).to_have_count(0)
            await expect(done).to_be_focused()
            assert world.task_posts() == []

            # Cancel in the Add note dialog gives focus back too.
            note = sign.get_by_role("button", name="Add note")
            await note.focus()
            await page.keyboard.press("Enter")
            await expect(dialog(page)).to_be_visible()
            await dialog(page).get_by_role("button", name="Cancel").click()
            await expect(dialog(page)).to_have_count(0)
            await expect(note).to_be_focused()

    await with_browser(body)


@pytest.mark.parametrize("width", (390, 1280))
async def test_row_buttons_are_described_by_their_step(site: Site, width: int) -> None:
    async def body(browser: Any) -> None:
        world = TasksWorld()
        async with tasks_page(browser, site, world, STAFF, width=width) as page:
            place = card if width < 768 else row
            sign, handbook, kit = place(page, "sign"), place(page, "handbook"), place(page, "kit")
            await expect(sign).to_be_visible(timeout=WAIT)
            # Two rows' "Mark done" buttons share a name but not a description.
            for step, title in (
                (sign, "Sign the employment contract"),
                (handbook, "Read the safety handbook"),
            ):
                for label in ("Mark done", "Add note"):
                    button = step.get_by_role("button", name=label, exact=True)
                    await expect(button).to_have_accessible_description(title)
            await expect(kit.get_by_role("button", name="Add note")).to_have_accessible_description(
                "Pick up the safety kit"
            )
            check = place(page, "details").get_by_role("button", name="Check now")
            await expect(check).to_have_accessible_description("Fill in your personal details")
            # Each layout has its own ids, so the hidden one never doubles an id.
            ids = await page.evaluate(
                "() => [...document.querySelectorAll('[id^=hr-task-]')].map((el) => el.id)"
            )
            assert len(ids) == len(set(ids)) == 10, ids

    await with_browser(body)


async def test_approver_buttons_are_described_by_their_step(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TasksWorld()
        async with tasks_page(browser, site, world, MANAGER) as page:
            kit, badge = row(page, "kit"), row(page, "badge")
            await expect(kit).to_be_visible(timeout=WAIT)
            for step, title in (
                (kit, "Pick up the safety kit"),
                (badge, "Collect your building badge"),
            ):
                for label in ("Approve", "Send back"):
                    button = step.get_by_role("button", name=label)
                    await expect(button).to_have_accessible_description(title)

    await with_browser(body)
