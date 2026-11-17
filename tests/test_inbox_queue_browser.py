"""Real browser checks for the Inbox queue bar: tabs with counts, search, Filters and Sort.

The same stubbed harness as `test_inbox_redesign_browser.py` (its module-scoped Next.js shell
and its synthetic Inbox API); the rows here carry the work-queue fields and the stub filters by
`state`, so each tab shows only its own rows.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs

import pytest
from playwright.async_api import Page, expect

from tests import test_inbox_redesign_browser as redesign
from tests.test_inbox_redesign_browser import (
    LONG,
    LONG_NAME,
    ME_ID,
    NAMED,
    NEWCOMER,
    UNNAMED,
    Inbox,
    Shell,
    conversation,
    eventually,
    inbox_page,
    on_top,
    open_filters,
    open_thread,
    page_width,
    row,
    rows_loaded,
)
from tests.test_services_browser import WAIT

pytestmark = pytest.mark.asyncio

# One staged shell for this file too, built by the redesign module's fixture.
shell = redesign.shell

WAITING_ROW = "a0000000-0000-4000-8000-000000000005"
RESOLVED_ROW = "a0000000-0000-4000-8000-000000000006"
BOT_ROW = "a0000000-0000-4000-8000-000000000007"
AINA = "33333333-3333-4333-8333-333333333331"
NUDGE = re.compile(r"\bfs-nudge\b")
TAB_IDS = ("needs_us", "mine", "unassigned", "overdue", "waiting", "snoozed", "resolved", "all")
TAB_NAMES = (
    "Needs us, 3",
    "Mine, 2",
    "Unassigned, 2",
    "Overdue, 1",
    "Waiting on customer, 1",
    "Snoozed, 1",
    "Resolved",
    "All",
)
# Exactly what each tab sends (the tenant query is empty for tenant staff).
TAB_QUERIES: dict[str, dict[str, list[str]]] = {
    "needs_us": {"state": ["needs_staff"], "order": ["due"]},
    "mine": {"state": ["needs_staff", "waiting_customer"], "assignee": ["me"], "order": ["due"]},
    "unassigned": {"state": ["needs_staff"], "assignee": ["none"], "order": ["due"]},
    "overdue": {"state": ["needs_staff"], "overdue": ["true"], "order": ["due"]},
    "waiting": {"state": ["waiting_customer"], "order": ["activity"]},
    "snoozed": {"state": ["snoozed"], "order": ["activity"]},
    "resolved": {"state": ["resolved"], "order": ["activity"]},
    "all": {"order": ["activity"]},
}
TAB_ROWS = {
    "needs_us": {NAMED, UNNAMED, WAITING_ROW},
    "mine": {NAMED, NEWCOMER},
    "unassigned": {UNNAMED, WAITING_ROW},
    "overdue": {UNNAMED},
    "waiting": {NEWCOMER},
    "snoozed": {LONG},
    "resolved": {RESOLVED_ROW},
    "all": {NAMED, UNNAMED, WAITING_ROW, NEWCOMER, LONG, RESOLVED_ROW, BOT_ROW},
}


def at(minutes: int) -> str:
    """An ISO time `minutes` from now (negative is the past)."""
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


def queue_rows() -> list[dict[str, Any]]:
    """Seven rows over every state: three need us (one mine, one overdue), one of each other."""
    return [
        conversation(
            NAMED,
            "telegram",
            name="Aisyah Rahman",
            known=True,
            preview="Is my order on the way?",
            minutes=2,
            queue_status="needs_staff",
            queue_reason="escalation",
            priority="high",
            assignee_id=ME_ID,
            assignee_name="Synthetic User",
            needs_staff_since=at(-40),
            due_at=at(20),
        ),
        conversation(
            UNNAMED,
            "whatsapp",
            name=None,
            masked="••••0148",
            preview="Hello?",
            minutes=5,
            queue_status="needs_staff",
            queue_reason="customer_message",
            needs_staff_since=at(-90),
            due_at=at(-30),
        ),
        conversation(
            WAITING_ROW,
            "telegram",
            name="Chong Wei",
            preview="Can I change my booking?",
            minutes=7,
            pending=1,
            queue_status="needs_staff",
            queue_reason="held_reply",
            needs_staff_since=at(-10),
            due_at=at(50),
        ),
        conversation(
            NEWCOMER,
            "telegram",
            name="Ben Tan",
            preview="Thanks, I will check.",
            minutes=9,
            queue_status="waiting_customer",
            assignee_id=ME_ID,
            assignee_name="Synthetic User",
        ),
        conversation(
            LONG,
            "whatsapp",
            name=LONG_NAME,
            masked="••••7781",
            known=True,
            preview="Remind me tomorrow.",
            minutes=30,
            queue_status="snoozed",
            snoozed_until=at(600),
        ),
        conversation(
            RESOLVED_ROW,
            "telegram",
            name="Dewi Lim",
            preview="All sorted, thank you.",
            minutes=60,
            queue_status="resolved",
        ),
        conversation(
            BOT_ROW,
            "whatsapp",
            name="Farid Musa",
            preview="What time do you open?",
            minutes=3,
        ),
    ]


def queue_inbox(**changes: Any) -> Inbox:
    return Inbox(items=queue_rows(), filter_state=True, **changes)


def tab(page: Page, identifier: str) -> Any:
    return page.get_by_test_id(f"inbox-tab-{identifier}")


def latest(state: Inbox) -> dict[str, list[str]]:
    return parse_qs(state.list_queries[-1])


async def shown_ids(page: Page) -> set[str]:
    return set(
        await page.locator("[data-conversation-id]").evaluate_all(
            "els => els.map(el => el.dataset.conversationId)"
        )
    )


async def settle(state: Inbox, before: int) -> dict[str, list[str]]:
    """Wait for a new list request after `before` requests, then read the newest one."""
    await eventually(lambda: len(state.list_queries) > before)
    await asyncio.sleep(0.3)
    return latest(state)


async def test_the_bar_shows_eight_tabs_with_the_servers_counts(shell: Shell) -> None:
    state = queue_inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        strip = page.get_by_role("tablist", name="Conversation queues")
        tabs = strip.get_by_role("tab")
        await expect(tabs).to_have_count(8)
        for index, name in enumerate(TAB_NAMES):
            await expect(tabs.nth(index)).to_have_accessible_name(name)
            await expect(tabs.nth(index)).to_have_attribute(
                "data-testid", f"inbox-tab-{TAB_IDS[index]}"
            )
        # Needs us opens first, and Resolved and All have no count.
        await expect(tab(page, "needs_us")).to_have_attribute("aria-selected", "true")
        await expect(page.get_by_test_id("inbox-needs-us-count")).to_have_text("3")
        await expect(tab(page, "resolved")).to_have_text("Resolved")
        await expect(tab(page, "all")).to_have_text("All")
        assert latest(state) == TAB_QUERIES["needs_us"]
        # The list is the panel the tabs control.
        panel_id = await tab(page, "needs_us").get_attribute("aria-controls")
        assert panel_id
        await expect(page.locator(f"[id='{panel_id}']")).to_have_attribute("role", "tabpanel")
        # Task 7 fills the Views slot; it is already in the bar.
        await expect(page.get_by_test_id("inbox-views-slot")).to_be_attached()
        # The old controls are gone.
        for removed in ("inbox-view", "inbox-channel-filter", "inbox-needs-review-count"):
            await expect(page.get_by_test_id(removed)).to_have_count(0)


async def test_each_tab_sends_exactly_its_filters_and_order(shell: Shell) -> None:
    state = queue_inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        for identifier in (*TAB_IDS[1:], "needs_us"):
            before = len(state.list_queries)
            await tab(page, identifier).click()
            await expect(tab(page, identifier)).to_have_attribute("aria-selected", "true")
            assert await settle(state, before) == TAB_QUERIES[identifier], identifier
            await rows_loaded(page, len(TAB_ROWS[identifier]))
            assert await shown_ids(page) == TAB_ROWS[identifier], identifier
        assert not any("needs_review" in query for query in state.list_queries)


async def test_counts_show_a_dash_until_answered_and_never_zero_on_failure(shell: Shell) -> None:
    state = queue_inbox(fail_lists=True)
    async with inbox_page(shell, state) as page:
        await expect(page.get_by_test_id("inbox-list-state-unavailable")).to_be_visible(
            timeout=WAIT
        )
        # No answer yet: a dash, and the tab is named without a number.
        count = page.get_by_test_id("inbox-needs-us-count")
        await expect(count).to_have_text("–")
        await expect(tab(page, "needs_us")).to_have_accessible_name("Needs us")
        await expect(tab(page, "mine")).to_have_accessible_name("Mine")
        await expect(tab(page, "mine")).not_to_contain_text("0")
        # The first answer brings the counts.
        state.fail_lists = False
        await expect(count).to_have_text("3", timeout=WAIT)
        await expect(tab(page, "mine")).to_have_accessible_name("Mine, 2")
        # A refresh that fails keeps the last good counts.
        state.fail_lists = True
        await expect(page.get_by_test_id("inbox-list-state-unavailable")).to_be_visible(
            timeout=WAIT
        )
        await expect(count).to_have_text("3")
        await expect(tab(page, "overdue")).to_have_accessible_name("Overdue, 1")
        # A server that could not read them says so: a dash again, never 0.
        state.fail_lists = False
        state.null_counts = True
        await expect(count).to_have_text("–", timeout=WAIT)
        await expect(tab(page, "needs_us")).to_have_accessible_name("Needs us")
        await expect(tab(page, "snoozed")).to_have_text("Snoozed–")


async def test_needs_us_count_nudges_only_when_it_rises(shell: Shell) -> None:
    counts = {
        "needs_us": 3,
        "mine": 2,
        "unassigned": 2,
        "overdue": 1,
        "waiting_customer": 1,
        "snoozed": 1,
        "oldest_waiting_at": None,
    }
    state = queue_inbox(counts_override=dict(counts))
    async with inbox_page(shell, state) as page:
        count = page.get_by_test_id("inbox-needs-us-count")
        await expect(count).to_have_text("3", timeout=WAIT)
        # The first answer never nudges, and neither does a fall.
        await expect(count).not_to_have_class(NUDGE)
        state.counts_override = {**counts, "needs_us": 2}
        await expect(count).to_have_text("2", timeout=WAIT)
        await expect(count).not_to_have_class(NUDGE)
        # Another count rising does not nudge Needs us.
        state.counts_override = {**counts, "needs_us": 2, "snoozed": 4}
        await expect(tab(page, "snoozed")).to_have_accessible_name("Snoozed, 4", timeout=WAIT)
        await expect(count).not_to_have_class(NUDGE)
        state.counts_override = {**counts, "needs_us": 5, "snoozed": 4}
        await expect(count).to_have_text("5", timeout=WAIT)
        await expect(count).to_have_class(NUDGE)
        assert await count.evaluate("el => getComputedStyle(el).animationName") == "fs-nudge"
    # With reduced motion the count changes but never moves.
    state = queue_inbox(counts_override=dict(counts))
    async with inbox_page(shell, state, reduced_motion=True) as page:
        count = page.get_by_test_id("inbox-needs-us-count")
        await expect(count).to_have_text("3", timeout=WAIT)
        state.counts_override = {**counts, "needs_us": 6}
        await expect(count).to_have_text("6", timeout=WAIT)
        await expect(count).not_to_have_class(NUDGE)


async def test_search_keeps_the_debounce_the_tab_and_ignores_one_character(shell: Shell) -> None:
    state = queue_inbox(filter_search=True)
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        search = page.get_by_test_id("inbox-search")
        await expect(search).to_have_accessible_name("Search customers")
        await search.fill("A")
        await expect(page.get_by_text("Type at least two characters to search.")).to_be_visible()
        # Past the 300 ms debounce and through at least one 3-second poll.
        await asyncio.sleep(3.5)
        assert not any("q=" in query for query in state.list_queries), state.list_queries
        await search.fill("Ai")
        await eventually(lambda: any("q=Ai" in query for query in state.list_queries))
        assert latest(state) == {**TAB_QUERIES["needs_us"], "q": ["Ai"]}
        await rows_loaded(page, 1)
        # Typing fast sends only what settled.
        await search.fill("Aisy")
        await search.fill("Aisyah")
        await eventually(lambda: "q=Aisyah" in state.list_queries[-1])
        assert not any(parse_qs(query).get("q") == ["Aisy"] for query in state.list_queries)
        # A tab change keeps the search.
        before = len(state.list_queries)
        await tab(page, "all").click()
        assert await settle(state, before) == {**TAB_QUERIES["all"], "q": ["Aisyah"]}


async def test_filters_send_priority_and_me_and_show_removable_chips(shell: Shell) -> None:
    state = queue_inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        button = page.get_by_test_id("inbox-queue-filters")
        await expect(button).to_have_accessible_name("Filters")
        panel = await open_filters(page)
        # Focus moves into the panel.
        assert await panel.evaluate("el => el.contains(document.activeElement)")
        for group, options in (
            ("Assigned to", ["Anyone", "Me", "Unassigned", "Aina Demo"]),
            ("Priority", ["Any", "High and above", "Urgent"]),
            ("Channel", ["Any", "Telegram", "WhatsApp"]),
            ("Sender", ["Anyone", "Customers", "New senders"]),
            ("Waiting longer than", ["Any", "15 minutes", "1 hour", "4 hours", "1 day"]),
        ):
            radios = panel.get_by_role("group", name=group).get_by_role("radio")
            await expect(radios).to_have_count(len(options), timeout=WAIT)
            for index, name in enumerate(options):
                await expect(radios.nth(index)).to_have_accessible_name(name)
        reasons = panel.get_by_role("group", name="Reason").get_by_role("checkbox")
        await expect(reasons).to_have_count(4)
        topics = panel.get_by_role("group", name="Topic").get_by_role("checkbox")
        await expect(topics).to_have_count(7)
        await expect(panel.get_by_role("checkbox", name="Overdue only")).not_to_be_checked()

        before = len(state.list_queries)
        await (
            panel.get_by_role("group", name="Priority")
            .get_by_role("radio", name="High and above")
            .check()
        )
        await panel.get_by_role("group", name="Assigned to").get_by_role("radio", name="Me").check()
        await eventually(lambda: "assignee=me" in state.list_queries[-1])
        await settle(state, before)
        assert latest(state) == {
            **TAB_QUERIES["needs_us"],
            "priority_min": ["high"],
            "assignee": ["me"],
        }
        await rows_loaded(page, 1)
        # Escape closes the panel and gives focus back to the button.
        await page.keyboard.press("Escape")
        await expect(panel).to_be_hidden()
        await expect(button).to_be_focused()
        await expect(button).to_have_accessible_name("Filters, 2 applied")

        chips = page.get_by_test_id("inbox-queue-chips")
        mine = chips.get_by_role("button", name="Remove filter: Assigned to me")
        await expect(mine).to_be_visible()
        priority_chip = chips.get_by_role("button", name="Remove filter: Priority high or above")
        await expect(priority_chip).to_be_visible()
        before = len(state.list_queries)
        await priority_chip.click()
        assert await settle(state, before) == {**TAB_QUERIES["needs_us"], "assignee": ["me"]}
        await expect(button).to_have_accessible_name("Filters, 1 applied")
        # Clear filters goes back to the tab's own filters.
        before = len(state.list_queries)
        await page.get_by_role("button", name="Clear filters").click()
        assert await settle(state, before) == TAB_QUERIES["needs_us"]
        await expect(chips.get_by_role("button")).to_have_count(0)
        await expect(button).to_have_accessible_name("Filters")
        await rows_loaded(page, 3)


async def test_a_tab_owns_its_filters_and_a_new_tab_clears_the_chips(shell: Shell) -> None:
    state = queue_inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        panel = await open_filters(page)
        await panel.get_by_role("checkbox", name="Escalated").check()
        await eventually(lambda: "reason=escalation" in state.list_queries[-1])
        await page.keyboard.press("Escape")
        await expect(
            page.get_by_role("button", name="Remove filter: Reason: Escalated")
        ).to_be_visible()
        # A new tab starts from its own filters only.
        before = len(state.list_queries)
        await tab(page, "mine").click()
        assert await settle(state, before) == TAB_QUERIES["mine"]
        chips = page.get_by_test_id("inbox-queue-chips")
        await expect(chips.get_by_role("button")).to_have_count(0)
        # Mine sets who it is assigned to, so that choice is fixed while the tab is open.
        panel = await open_filters(page)
        mine = panel.get_by_role("group", name="Assigned to")
        await expect(mine.get_by_role("radio", name="Me")).to_be_checked()
        await expect(mine.get_by_role("radio", name="Anyone")).to_be_disabled()


async def test_sort_sends_order_and_priority_only_in_needs_us(shell: Shell) -> None:
    state = queue_inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        sort = page.get_by_label("Sort")
        await expect(sort).to_have_value("due")
        options = sort.locator("option")
        await expect(options).to_have_text(
            ["Oldest waiting", "Due soonest", "Newest activity", "Priority"]
        )
        before = len(state.list_queries)
        await sort.select_option("priority")
        assert await settle(state, before) == {**TAB_QUERIES["needs_us"], "order": ["priority"]}
        before = len(state.list_queries)
        await sort.select_option("waiting")
        assert await settle(state, before) == {**TAB_QUERIES["needs_us"], "order": ["waiting"]}
        await sort.select_option("priority")
        # A new tab opens with its own order. Priority is offered only while the state is
        # exactly "needs us" (the API's rule), so Unassigned and Overdue keep it too.
        for identifier in ("mine", "unassigned", "overdue", "waiting", "snoozed", "all"):
            before = len(state.list_queries)
            await tab(page, identifier).click()
            assert (await settle(state, before))["order"] == TAB_QUERIES[identifier]["order"]
            choices = ["Oldest waiting", "Due soonest", "Newest activity"]
            if identifier in {"unassigned", "overdue"}:
                choices.append("Priority")
            await expect(options).to_have_text(choices)
        before = len(state.list_queries)
        await sort.select_option("activity")
        assert await settle(state, before) == {"order": ["activity"]}
        before = len(state.list_queries)
        await tab(page, "needs_us").click()
        assert await settle(state, before) == TAB_QUERIES["needs_us"]
        await expect(sort).to_have_value("due")
        assert not any("order=priority" in q for q in state.list_queries if "state=" not in q)


async def test_arrow_keys_move_between_tabs_and_enter_or_space_selects(shell: Shell) -> None:
    state = queue_inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        needs_us = tab(page, "needs_us")
        # Only the selected tab is in the tab order.
        await expect(needs_us).to_have_attribute("tabindex", "0")
        await expect(tab(page, "mine")).to_have_attribute("tabindex", "-1")
        await needs_us.focus()
        await page.keyboard.press("ArrowRight")
        await expect(tab(page, "mine")).to_be_focused()
        # Moving does not select; Enter does.
        await expect(needs_us).to_have_attribute("aria-selected", "true")
        before = len(state.list_queries)
        await page.keyboard.press("Enter")
        await expect(tab(page, "mine")).to_have_attribute("aria-selected", "true")
        assert await settle(state, before) == TAB_QUERIES["mine"]
        await page.keyboard.press("End")
        await expect(tab(page, "all")).to_be_focused()
        await page.keyboard.press("ArrowRight")
        await expect(needs_us).to_be_focused()
        await page.keyboard.press("ArrowLeft")
        await expect(tab(page, "all")).to_be_focused()
        await page.keyboard.press("Home")
        await expect(needs_us).to_be_focused()
        await page.keyboard.press("ArrowRight")
        await page.keyboard.press("ArrowRight")
        await expect(tab(page, "unassigned")).to_be_focused()
        before = len(state.list_queries)
        await page.keyboard.press(" ")
        await expect(tab(page, "unassigned")).to_have_attribute("aria-selected", "true")
        assert await settle(state, before) == TAB_QUERIES["unassigned"]
        # J and K are not taken by the tab strip.
        await expect(tab(page, "unassigned")).to_be_focused()


async def test_phone_strip_scrolls_in_its_region_and_filters_is_a_sheet(shell: Shell) -> None:
    state = queue_inbox()
    async with inbox_page(shell, state, width=390) as page:
        await rows_loaded(page, 3)
        region = page.get_by_role("region", name="Conversation queues")
        await expect(region).to_be_visible()
        scroll = await region.evaluate(
            """el => ({
                overflow: getComputedStyle(el).overflowX,
                scrolls: el.scrollWidth > el.clientWidth,
            })"""
        )
        assert scroll == {"overflow": "auto", "scrolls": True}, scroll
        measured = await page_width(page)
        assert measured["scroll"] <= measured["inner"], measured
        # The last tab can be reached from the keyboard, and it scrolls into view.
        await tab(page, "needs_us").focus()
        await page.keyboard.press("End")
        await expect(tab(page, "all")).to_be_in_viewport()
        button = page.get_by_test_id("inbox-queue-filters")
        await button.click()
        sheet = page.get_by_role("dialog", name="Filters")
        await expect(sheet).to_be_visible()
        box = await sheet.bounding_box()
        assert box is not None and box["width"] >= 389, box
        assert await sheet.evaluate("el => el.contains(document.activeElement)")
        priority = sheet.get_by_role("group", name="Priority")
        await priority.get_by_role("radio", name="Urgent").check()
        await eventually(lambda: "priority_min=urgent" in state.list_queries[-1])
        await page.keyboard.press("Escape")
        await expect(sheet).to_be_hidden()
        await expect(button).to_be_focused()
        await expect(
            page.get_by_role("button", name="Remove filter: Priority urgent")
        ).to_be_visible()
        measured = await page_width(page)
        assert measured["scroll"] <= measured["inner"], measured


async def test_no_page_overflow_at_320_and_1440_with_chips(shell: Shell) -> None:
    state = queue_inbox()
    async with inbox_page(shell, state, width=1440) as page:
        await rows_loaded(page, 3)
        panel = await open_filters(page)
        for name in ("Escalated", "Held reply", "Customer wrote", "Complaint", "Payment"):
            await panel.get_by_role("checkbox", name=name).check()
        await (
            panel.get_by_role("group", name="Waiting longer than")
            .get_by_role("radio", name="4 hours")
            .check()
        )
        await page.keyboard.press("Escape")
        await expect(page.get_by_test_id("inbox-queue-chips").get_by_role("button")).to_have_count(
            7
        )
        for width in (1440, 320):
            await page.set_viewport_size({"width": width, "height": 900})
            await asyncio.sleep(0.3)
            measured = await page_width(page)
            assert measured["scroll"] <= measured["inner"], (width, measured)
            # The bar's own Clear filters (the empty list offers one too) stays on screen.
            chips = page.get_by_test_id("inbox-queue-chips")
            await expect(chips.get_by_role("button", name="Clear filters")).to_be_in_viewport()
            await expect(page.get_by_test_id("inbox-no-match")).to_be_visible()


async def test_assignee_list_failure_unavailable_and_retry(shell: Shell) -> None:
    state = queue_inbox(assignees_status=500)
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        # The team list is read only once the panel opens.
        await asyncio.sleep(0.5)
        assert state.assignee_calls == 0
        panel = await open_filters(page)
        group = panel.get_by_role("group", name="Assigned to")
        await expect(group.get_by_text("Could not load the team.")).to_be_visible(timeout=WAIT)
        # The first three choices work without the list.
        await expect(group.get_by_role("radio")).to_have_count(3)
        await group.get_by_role("radio", name="Unassigned").check()
        await eventually(lambda: "assignee=none" in state.list_queries[-1])
        state.assignees_status = 200
        await group.get_by_role("button", name="Try again").click()
        person = group.get_by_role("radio", name="Aina Demo")
        await expect(person).to_be_visible(timeout=WAIT)
        # The signed-in person is "Me", not listed twice.
        await expect(group.get_by_role("radio", name="Synthetic User")).to_have_count(0)
        await person.check()
        await eventually(lambda: f"assignee={AINA}" in state.list_queries[-1])
        await page.keyboard.press("Escape")
        await expect(
            page.get_by_role("button", name="Remove filter: Assigned to Aina Demo")
        ).to_be_visible()
    state = queue_inbox(assignees_status=503)
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        panel = await open_filters(page)
        group = panel.get_by_role("group", name="Assigned to")
        await expect(group.get_by_text("The team list is unavailable right now.")).to_be_visible(
            timeout=WAIT
        )
        await expect(group.get_by_role("radio")).to_have_count(3)
        await group.get_by_role("radio", name="Me").check()
        await eventually(lambda: "assignee=me" in state.list_queries[-1])


# ---- the bar stays small on phones and fits the screen on desktop -----------------------------

# The phone's sticky block is the tab strip alone: a 40 px tab (44 px on a coarse pointer) and its
# 1 px border. Anything taller means search, Filters, Sort or the chips stuck to the top too.
STICKY_BOUND = 48
UNCOVERED_JS = """el => {
    const r = el.getBoundingClientRect();
    const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    return {top: r.top, bottom: r.bottom, uncovered: hit === el || el.contains(hit)};
}"""


def many_rows(count: int = 30) -> list[dict[str, Any]]:
    """Rows that all pass the chips the phone test applies (high, escalated, Telegram, customer)."""
    return [
        conversation(
            f"a0000000-0000-4000-8000-{index:012d}",
            "telegram",
            name=f"Customer {index:02d}",
            known=True,
            preview=f"Question number {index}.",
            minutes=index + 1,
            queue_status="needs_staff",
            queue_reason="escalation",
            priority="high",
            needs_staff_since=at(-index - 5),
            due_at=at(60 - index),
        )
        for index in range(100, 100 + count)
    ]


@pytest.mark.parametrize("size", ((390, 844), (320, 256)))
async def test_phone_only_the_tab_strip_sticks_and_rows_stay_clear_of_it(
    shell: Shell, size: tuple[int, int]
) -> None:
    width, height = size
    rows = many_rows()
    state = Inbox(items=rows)
    async with inbox_page(shell, state, width=width) as page:
        await page.set_viewport_size({"width": width, "height": height})
        await page.goto(f"{shell.base}/portal/inbox")
        await rows_loaded(page, 30)
        panel = await open_filters(page)
        await (
            panel.get_by_role("group", name="Priority")
            .get_by_role("radio", name="High and above")
            .check()
        )
        await panel.get_by_role("checkbox", name="Escalated").check()
        await (
            panel.get_by_role("group", name="Channel").get_by_role("radio", name="Telegram").check()
        )
        await (
            panel.get_by_role("group", name="Sender").get_by_role("radio", name="Customers").check()
        )
        await page.keyboard.press("Escape")
        await expect(panel).to_be_hidden()
        chips = page.get_by_test_id("inbox-queue-chips").get_by_role("button", name="Remove filter")
        await expect(chips).to_have_count(4)
        await rows_loaded(page, 30)

        # Scrolled into the list: only the tab strip is stuck to the top, and it is small.
        await page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight / 2)")
        await asyncio.sleep(0.2)
        strip = page.get_by_role("region", name="Conversation queues")
        box = await strip.bounding_box()
        assert box is not None and abs(box["y"]) < 1, box
        assert box["height"] <= STICKY_BOUND, box
        sticky_bottom = box["y"] + box["height"]
        for scrolled_away in (page.get_by_test_id("inbox-queue-filters"), chips.first):
            gone = await scrolled_away.bounding_box()
            assert gone is not None and gone["y"] + gone["height"] <= 0, gone
        measured = await page_width(page)
        assert measured["scroll"] <= measured["inner"], measured

        ids = [row["id"] for row in rows]
        # J moves the selection; K back onto a row hidden under the strip brings it out from under.
        for _ in range(11):
            await page.keyboard.press("j")
        await expect(row(page, ids[10])).to_have_attribute("aria-current", "true")
        await row(page, ids[10]).evaluate(
            "el => window.scrollBy(0, el.getBoundingClientRect().top - 5)"
        )
        await page.keyboard.press("k")
        chosen = row(page, ids[9])
        await expect(chosen).to_have_attribute("aria-current", "true")
        await asyncio.sleep(0.2)
        seen = await chosen.evaluate(UNCOVERED_JS)
        assert seen["uncovered"] and seen["top"] >= sticky_bottom - 0.5, (size, seen)

        # Shift+Tab onto a row that sits under the strip: focus brings it out from under too.
        await row(page, ids[20]).evaluate(
            "el => { window.scrollBy(0, el.getBoundingClientRect().top - 5);"
            " el.focus({preventScroll: true}); }"
        )
        await page.keyboard.press("Shift+Tab")
        focused = row(page, ids[19])
        await expect(focused).to_be_focused()
        await asyncio.sleep(0.2)
        seen = await focused.evaluate(UNCOVERED_JS)
        assert seen["uncovered"] and seen["top"] >= sticky_bottom - 0.5, (size, seen)


async def test_filters_popover_never_passes_the_screen_edge(shell: Shell) -> None:
    state = Inbox()
    async with inbox_page(shell, state, width=768) as page:
        for width in (768, 1024, 1440):
            await page.set_viewport_size({"width": width, "height": 900})
            await page.goto(f"{shell.base}/portal/inbox")
            await rows_loaded(page)
            panel = await open_filters(page)
            box = await panel.bounding_box()
            assert box is not None, width
            assert box["x"] >= 0 and box["x"] + box["width"] <= width, (width, box)
            measured = await page_width(page)
            assert measured["scroll"] <= measured["inner"], ("open", width, measured)
            await page.keyboard.press("Escape")
            await expect(panel).to_be_hidden()


async def test_desktop_bar_and_composer_fit_one_screen(shell: Shell) -> None:
    """At 1440 x 800 the bar stays at the top and the reply box is fully on screen below it."""
    state = Inbox()
    async with inbox_page(shell, state) as page:
        await page.set_viewport_size({"width": 1440, "height": 800})
        await page.goto(f"{shell.base}/portal/inbox")
        await rows_loaded(page)
        # Staff has taken over this one, so the reply box is open.
        await row(page, NEWCOMER).click()
        composer = page.get_by_test_id("inbox-composer")
        await expect(composer).to_be_visible(timeout=WAIT)
        # The page title and the how-to line sit above the Inbox: scroll them away, as staff do.
        await page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        await asyncio.sleep(0.2)
        bar = await page.get_by_test_id("inbox-queue-bar").bounding_box()
        assert bar is not None and abs(bar["y"]) < 1, bar
        bar_bottom = bar["y"] + bar["height"]
        seen = await composer.evaluate(UNCOVERED_JS)
        assert seen["top"] >= bar_bottom and seen["bottom"] <= 800, (bar, seen)
        thread = await page.get_by_test_id("inbox-thread").bounding_box()
        assert thread is not None and thread["y"] >= bar_bottom, (bar, thread)
        send = page.get_by_role("button", name="Send reply", exact=True)
        await expect(send).to_be_in_viewport()
        assert await on_top(send)
        # The tabs are still usable from there.
        assert await on_top(tab(page, "all"))


# ---- the three-line row ------------------------------------------------------------------------

# Every row of `queue_rows()` and the parts of its meta line, by `data-row-part`: exactly these
# parts and no others. A value is a pattern the part's text must match in full.
ROW_PARTS: dict[str, dict[str, str]] = {
    # Needs us, escalated, high, mine, a known customer: no sender pill.
    NAMED: {
        "state": "Needs us",
        "due": r"Due in (19|20)m",
        "priority": "High",
        "reason": "Escalated",
        "assignee": "SUAssigned to You",
        "channel": "Telegram",
    },
    # Overdue: the word and the time, never colour alone; a new sender keeps the dashed label.
    UNNAMED: {
        "state": "Needs us",
        "due": r"Overdue (30|31)m",
        "reason": "Customer wrote",
        "sender": "New sender",
        "channel": "WhatsApp",
    },
    # A held reply keeps its review marker, so the reason does not say "Held reply" twice.
    WAITING_ROW: {
        "review": "Needs review",
        "state": "Needs us",
        "due": r"Due in (49|50)m",
        "sender": "New sender",
        "channel": "Telegram",
    },
    NEWCOMER: {
        "state": "Waiting on customer",
        "assignee": "SUAssigned to You",
        "sender": "New sender",
        "channel": "Telegram",
    },
    LONG: {
        "state": r"Snoozed until (Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d\d:\d\d",
        "channel": "WhatsApp",
    },
    RESOLVED_ROW: {"state": "Resolved", "sender": "New sender", "channel": "Telegram"},
    # The assistant has it: no state label at all.
    BOT_ROW: {"sender": "New sender", "channel": "WhatsApp"},
}
PARTS_JS = """el => Object.fromEntries([...el.querySelectorAll('[data-row-part]')].map(part => [
    part.dataset.rowPart,
    {
        text: part.textContent.trim(),
        tone: part.querySelector('[data-tone]')?.dataset.tone ?? null,
        dashed: [part, ...part.querySelectorAll('*')]
            .some(e => getComputedStyle(e).outlineStyle === 'dashed'),
        icons: part.querySelectorAll('svg').length,
    },
]))"""


async def row_parts(page: Page, identifier: str) -> dict[str, dict[str, Any]]:
    """The meta line of a row: each part's text, StatusLabel tone, dashed edge and icons."""
    return await row(page, identifier).locator("xpath=..").evaluate(PARTS_JS)


async def test_rows_show_only_the_labels_that_apply(shell: Shell) -> None:
    state = queue_inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        await tab(page, "all").click()
        await rows_loaded(page, 7)
        for identifier, expected in ROW_PARTS.items():
            found = await row_parts(page, identifier)
            assert set(found) == set(expected), (identifier, found)
            for part, pattern in expected.items():
                assert re.fullmatch(pattern, found[part]["text"]), (identifier, part, found[part])
        named, unnamed = await row_parts(page, NAMED), await row_parts(page, UNNAMED)
        # Tones carry the meaning alongside the words.
        assert named["state"]["tone"] == "attention", named
        assert named["due"]["tone"] == "neutral", named
        assert unnamed["due"]["tone"] == "error", unnamed
        assert (await row_parts(page, LONG))["state"]["tone"] == "info"
        assert (await row_parts(page, RESOLVED_ROW))["state"]["tone"] == "success"
        assert (await row_parts(page, NEWCOMER))["state"]["tone"] == "neutral"
        # Priority is an icon plus the word; a new sender's label is dashed; the review marker
        # keeps its bell.
        assert named["priority"]["icons"] == 1, named
        assert unnamed["sender"]["dashed"], unnamed
        assert not any(part["dashed"] for part in named.values()), named
        assert (await row_parts(page, WAITING_ROW))["review"]["icons"] == 1
        # The channel is a small icon with its name for assistive technology.
        for identifier, channel in ((NAMED, "Telegram"), (UNNAMED, "WhatsApp")):
            icon = row(page, identifier).get_by_role("img", name=channel, exact=True)
            await expect(icon).to_have_count(1)
            await expect(icon).to_have_attribute("data-testid", "inbox-channel-badge")
        # The owner words moved out of the row: the state and the assignee say it.
        listing = page.get_by_role("list", name="Conversations")
        await expect(listing.get_by_text("Staff has taken over")).to_have_count(0)
        # No selection checkboxes until bulk selection is wired up.
        await expect(listing.get_by_role("checkbox")).to_have_count(0)

        # At 320 the rows wrap their meta line instead of pushing the page sideways.
        await page.set_viewport_size({"width": 320, "height": 844})
        await asyncio.sleep(0.3)
        measured = await page_width(page)
        assert measured["scroll"] <= measured["inner"], measured
        container = await listing.bounding_box()
        assert container
        for identifier in ROW_PARTS:
            box = await row(page, identifier).bounding_box()
            assert box and box["x"] + box["width"] <= container["x"] + container["width"] + 0.5, (
                identifier,
                box,
                container,
            )


# A fixed browser clock, so the wait and the times are exact: 10:00 on 17 Nov 2026 in +08:00.
NOW = datetime(2026, 11, 17, 2, 0, tzinfo=UTC)
CLOCK_ROW = "a0000000-0000-4000-8000-000000000101"
QUIET_ROW = "a0000000-0000-4000-8000-000000000102"


def fixed(minutes: int) -> str:
    return (NOW + timedelta(minutes=minutes)).isoformat()


def clock_rows() -> list[dict[str, Any]]:
    waiting = conversation(
        CLOCK_ROW,
        "telegram",
        name="Nadia Ismail",
        known=True,
        preview="Where is my parcel?",
        minutes=3,
        queue_status="needs_staff",
        queue_reason="escalation",
        priority="urgent",
        # Claimed by someone the member directory cannot name: still shown as claimed.
        assignee_id=AINA,
        assignee_name=None,
        needs_staff_since=fixed(-130),
        due_at=fixed(5),
    )
    quiet = conversation(
        QUIET_ROW,
        "whatsapp",
        name="Omar Hakim",
        known=True,
        preview="Thank you!",
        minutes=8,
    )
    waiting.update(
        opened_at=fixed(-200),
        last_message={"preview": "Where is my parcel?", "at": fixed(-3)},
        last_customer_at=fixed(-3),
        last_staff_reply_at=None,
    )
    # Staff answered after the customer's last message: no emphasis.
    quiet.update(
        opened_at=fixed(-300),
        last_message={"preview": "Thank you!", "at": fixed(-8)},
        last_customer_at=fixed(-20),
        last_staff_reply_at=fixed(-8),
    )
    return [waiting, quiet]


async def test_the_clock_is_the_servers_wait_in_queue_tabs_and_the_last_message_elsewhere(
    shell: Shell,
) -> None:
    state = Inbox(items=clock_rows())
    async with inbox_page(shell, state) as page:
        await page.context.clock.set_fixed_time(NOW)
        await page.reload()
        await rows_loaded(page, 2)
        clock = row(page, CLOCK_ROW).get_by_test_id("inbox-row-time")
        quiet = row(page, QUIET_ROW).get_by_test_id("inbox-row-time")
        # Needs us: how long it has waited since the server's needs_staff_since, not page open.
        await expect(clock).to_have_text("2h 10m")
        await expect(clock).to_have_attribute("title", "Waiting since 17 Nov 2026, 07:50")
        # A row with no wait clock shows its last message time even here.
        await expect(quiet).to_have_text("8 min")
        await expect(quiet).to_have_attribute("title", "Last message 17 Nov 2026, 09:52")
        numerals = await clock.evaluate("el => getComputedStyle(el).fontVariantNumeric")
        assert "tabular-nums" in numerals, numerals

        found = await row_parts(page, CLOCK_ROW)
        assert set(found) == {"state", "due", "priority", "reason", "assignee", "channel"}, found
        # The last quarter of the window reads "Due soon", in the attention tone.
        assert found["due"]["text"] == "Due soon" and found["due"]["tone"] == "attention", found
        assert found["priority"]["text"] == "Urgent" and found["priority"]["icons"] == 1, found
        assert found["assignee"]["text"] == "?Assigned to Someone", found
        assert set(await row_parts(page, QUIET_ROW)) == {"channel"}

        # The newest message is the customer's and unanswered: a heavier name, nothing else.
        async def weight(identifier: str) -> str:
            name = row(page, identifier).locator("span.truncate").first
            return await name.evaluate("el => getComputedStyle(el).fontWeight")

        assert await weight(CLOCK_ROW) == "700"
        assert await weight(QUIET_ROW) == "600"

        # Every other tab shows the last message time.
        await tab(page, "all").click()
        await expect(clock).to_have_text("3 min")
        await expect(clock).to_have_attribute("title", "Last message 17 Nov 2026, 09:57")
        await tab(page, "needs_us").click()
        await expect(clock).to_have_text("2h 10m")


# ---- the thread header: one primary action, Snooze, More, the dialogs and the banner -----------

OTHER_ROW = "a0000000-0000-4000-8000-000000000008"
KL = timezone(timedelta(hours=8))
UNCERTAIN = "Could not confirm that. Retry sends the same request again."


def other_row() -> dict[str, Any]:
    """Needs us, held by Aina (another member of the team)."""
    return conversation(
        OTHER_ROW,
        "telegram",
        name="Hana Yusof",
        known=True,
        preview="Any update on my refund?",
        minutes=4,
        queue_status="needs_staff",
        queue_reason="escalation",
        assignee_id=AINA,
        assignee_name="Aina Demo",
        needs_staff_since=at(-60),
        due_at=at(60),
    )


def action_inbox(**changes: Any) -> Inbox:
    state = queue_inbox(**changes)
    state.items.append(other_row())
    return state


def primary(page: Page) -> Any:
    return page.get_by_test_id("inbox-primary-action")


def header(page: Page) -> Any:
    return page.get_by_test_id("inbox-thread").locator("header")


async def open_item(page: Page, identifier: str) -> None:
    """Open a row from the All tab and wait for its detail read (the actions need its version)."""
    if await row(page, identifier).count() == 0:
        await tab(page, "all").click()
    await open_thread(page, identifier)
    loaded = page.locator(
        f'[data-testid="inbox-thread"][data-conversation="{identifier}"][data-loaded="true"]'
    )
    await expect(loaded).to_have_count(1, timeout=WAIT)


async def menu_items(page: Page, trigger: str) -> list[str]:
    await page.get_by_test_id(trigger).click()
    menu = page.get_by_role("menu")
    await expect(menu).to_be_visible()
    names = [name.strip() for name in await menu.get_by_role("menuitem").all_inner_texts()]
    await page.keyboard.press("Escape")
    await expect(menu).to_have_count(0)
    return names


def row_of(state: Inbox, identifier: str) -> dict[str, Any]:
    return next(item for item in state.items if item["id"] == identifier)


async def test_each_item_gets_its_one_primary_action_and_sends_the_right_body(
    shell: Shell,
) -> None:
    state = action_inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 4)
        await tab(page, "all").click()
        await rows_loaded(page, 8)
        expected = {
            UNNAMED: "Take it",
            WAITING_ROW: "Take it",
            NAMED: "Resolve",
            NEWCOMER: "Resolve",
            LONG: "Resolve",
            OTHER_ROW: "Take it from Aina",
            BOT_ROW: "Take over",
        }
        for identifier, text in expected.items():
            await open_item(page, identifier)
            await expect(primary(page)).to_have_text(text)
            await expect(primary(page)).to_have_count(1)
        # Resolved: nothing to do first, so no primary action at all.
        await open_item(page, RESOLVED_ROW)
        await expect(primary(page)).to_have_count(0)
        # The assistant has this one: Take over as before, and no queue menus.
        await open_item(page, BOT_ROW)
        await expect(page.get_by_test_id("inbox-snooze-menu")).to_have_count(0)
        await expect(page.get_by_test_id("inbox-more-menu")).to_have_count(0)

        # Take it claims with the version that was read, and nothing else.
        await open_item(page, UNNAMED)
        await expect(header(page).get_by_text("Needs us", exact=True)).to_be_visible()
        await expect(header(page).get_by_text(re.compile(r"^Overdue \d+m$"))).to_be_visible()
        # The request is held back so the controls can be seen disabled while it is in flight.
        state.queue_delay = 1.0
        await primary(page).click()
        for test_id in ("inbox-primary-action", "inbox-snooze-menu", "inbox-more-menu"):
            await expect(page.get_by_test_id(test_id)).to_be_disabled()
        await expect(page.get_by_text("You took this conversation.", exact=True)).to_be_visible(
            timeout=WAIT
        )
        state.queue_delay = 0.0
        call = state.queue_calls[-1]
        assert (call["id"], call["action"]) == (UNNAMED, "claim"), call
        assert set(call["body"]) == {"request_key", "expected_version"}, call
        assert call["body"]["expected_version"] == 1, call
        # Refreshed: the item is now mine, so the primary action is Resolve.
        await expect(primary(page)).to_have_text("Resolve", timeout=WAIT)
        await expect(primary(page)).to_be_enabled()

        # Resolve says when the customer last wrote, as the loaded item had it.
        await open_item(page, NAMED)
        seen = row_of(state, NAMED)["last_customer_at"]
        await primary(page).click()
        await expect(
            page.get_by_text("Resolved. The assistant has this conversation again.", exact=True)
        ).to_be_visible(timeout=WAIT)
        call = state.queue_calls[-1]
        assert (call["id"], call["action"]) == (NAMED, "resolve"), call
        assert call["body"]["seen_customer_at"] == seen, call
        assert call["body"]["expected_version"] == 1, call
        await expect(primary(page)).to_have_count(0, timeout=WAIT)

        # An administrator takes someone else's item with a claim too.
        await open_item(page, OTHER_ROW)
        await primary(page).click()
        await expect(page.get_by_text("You took this conversation.", exact=True)).to_be_visible(
            timeout=WAIT
        )
        assert (state.queue_calls[-1]["id"], state.queue_calls[-1]["action"]) == (
            OTHER_ROW,
            "claim",
        )

        # Take over on the assistant's chat is the existing route, unchanged.
        await open_item(page, BOT_ROW)
        await primary(page).click()
        await eventually(lambda: len(state.handoffs) == 1)
        assert state.handoffs[0]["action"] == "takeover", state.handoffs
        assert state.handoffs[0]["body"]["expected_version"] == 1, state.handoffs


async def test_staff_never_see_assign_or_take_from_and_admins_do(shell: Shell) -> None:
    state = action_inbox()
    async with inbox_page(shell, state, role="tenant_staff") as page:
        await rows_loaded(page, 4)
        await open_item(page, OTHER_ROW)
        # Staff may still resolve someone else's item; they are told who has it.
        await expect(primary(page)).to_have_text("Resolve")
        await expect(page.get_by_text("Take it from", exact=False)).to_have_count(0)
        await expect(page.get_by_test_id("inbox-queue-banner")).to_contain_text(
            "Assigned to Aina Demo"
        )
        items = await menu_items(page, "inbox-more-menu")
        assert "Assign to…" not in items and "Give up this item" not in items, items
        assert {"Set priority…", "Set topic…"} <= set(items), items
        # Their own item: they may give it up, still never assign.
        await open_item(page, NAMED)
        items = await menu_items(page, "inbox-more-menu")
        assert "Give up this item" in items and "Assign to…" not in items, items
        await page.get_by_test_id("inbox-more-menu").click()
        await page.get_by_role("menuitem", name="Give up this item").click()
        await expect(page.get_by_text("Gave up this item.", exact=True)).to_be_visible(timeout=WAIT)
        assert state.queue_calls[-1]["action"] == "unclaim", state.queue_calls
    state = action_inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 4)
        await open_item(page, OTHER_ROW)
        await expect(primary(page)).to_have_text("Take it from Aina")
        items = await menu_items(page, "inbox-more-menu")
        assert {"Assign to…", "Give up this item", "Set priority…", "Set topic…"} <= set(items), (
            items
        )


async def test_platform_admin_on_a_company_can_assign(shell: Shell) -> None:
    # A platform administrator is not a member, so the team list never names them.
    team = [
        {"id": AINA, "display_name": "Aina Demo", "role": "tenant_staff"},
        {
            "id": "33333333-3333-4333-8333-333333333332",
            "display_name": "Bakar Demo",
            "role": "tenant_admin",
        },
    ]
    state = action_inbox(assignees=team)
    async with inbox_page(shell, state, role="platform_admin", path="/inbox") as page:
        await page.get_by_label("Tenant").select_option(redesign.TENANT)
        await rows_loaded(page, 4)
        await open_item(page, UNNAMED)
        await page.get_by_test_id("inbox-more-menu").click()
        await page.get_by_role("menuitem", name="Assign to…").click()
        dialog = page.get_by_test_id("inbox-assign-dialog")
        await expect(dialog).to_be_visible()
        await dialog.get_by_role("radio", name="Aina Demo").check()
        await dialog.get_by_role("button", name="Assign", exact=True).click()
        await expect(page.get_by_text("Assigned to Aina Demo.", exact=True)).to_be_visible(
            timeout=WAIT
        )
        call = state.queue_calls[-1]
        assert call["action"] == "assign" and call["body"]["assignee_id"] == AINA, call
        assert f"client_id={redesign.TENANT}" in call["query"], call


async def test_snooze_presets_send_an_aware_until_and_the_menu_works_by_keyboard(
    shell: Shell,
) -> None:
    state = action_inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 4)
        await open_item(page, NAMED)
        trigger = page.get_by_test_id("inbox-snooze-menu")
        await trigger.click()
        menu = page.get_by_role("menu", name="Snooze")
        await expect(menu).to_be_visible()
        items = menu.get_by_role("menuitem")
        assert [text.strip() for text in await items.all_inner_texts()] == [
            "Later today",
            "Tomorrow 09:00",
            "Next Monday 09:00",
            "Pick a time…",
        ]
        # Focus moves in; the arrows, Home and End move it and wrap round.
        await expect(items.nth(0)).to_be_focused()
        await page.keyboard.press("ArrowDown")
        await expect(items.nth(1)).to_be_focused()
        await page.keyboard.press("End")
        await expect(items.nth(3)).to_be_focused()
        await page.keyboard.press("ArrowDown")
        await expect(items.nth(0)).to_be_focused()
        await page.keyboard.press("ArrowUp")
        await expect(items.nth(3)).to_be_focused()
        await page.keyboard.press("Home")
        await expect(items.nth(0)).to_be_focused()
        # Escape closes it and puts focus back on its button; J does not move the list meanwhile.
        await page.keyboard.press("j")
        await expect(row(page, NAMED)).to_have_attribute("aria-current", "true")
        await page.keyboard.press("Escape")
        await expect(menu).to_have_count(0)
        await expect(trigger).to_be_focused()
        await expect(page.get_by_test_id("inbox-thread")).to_be_visible()
        # A click elsewhere closes it too.
        await trigger.click()
        await expect(menu).to_be_visible()
        await page.get_by_role("log").click()
        await expect(menu).to_have_count(0)

        await trigger.click()
        await menu.get_by_role("menuitem", name="Tomorrow 09:00").click()
        await expect(page.get_by_text(re.compile(r"^Snoozed until \w{3} 09:00\.$"))).to_be_visible(
            timeout=WAIT
        )
        call = state.queue_calls[-1]
        assert call["action"] == "snooze", call
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT09:00:00\+08:00", call["body"]["until"]), call
        until = datetime.fromisoformat(call["body"]["until"])
        assert until > datetime.now(UTC), call
        assert call["body"]["seen_customer_at"] == row_of(state, NAMED)["last_customer_at"], call
        # Refreshed: the item is snoozed now, and its header says until when.
        await expect(header(page).get_by_text(re.compile(r"^Snoozed until "))).to_be_visible(
            timeout=WAIT
        )


async def test_pick_a_time_checks_the_time_in_words_and_sends_the_picked_time(
    shell: Shell,
) -> None:
    state = action_inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 4)
        await open_item(page, NAMED)
        trigger = page.get_by_test_id("inbox-snooze-menu")
        await trigger.click()
        await page.get_by_role("menuitem", name="Pick a time…").click()
        dialog = page.get_by_test_id("inbox-snooze-custom")
        await expect(dialog).to_be_visible()
        field_ = dialog.get_by_label("Snooze until")
        await expect(field_).to_have_attribute("type", "datetime-local")
        inside = await dialog.evaluate("el => el.contains(document.activeElement)")
        assert inside, "focus stayed outside the dialog"
        now = datetime.now(KL)
        send = dialog.get_by_role("button", name="Snooze", exact=True)
        await field_.fill((now - timedelta(days=1)).strftime("%Y-%m-%dT09:00"))
        await send.click()
        await expect(dialog.get_by_text("Pick a time in the future.", exact=True)).to_be_visible()
        await field_.fill((now + timedelta(days=61)).strftime("%Y-%m-%dT09:00"))
        await send.click()
        await expect(
            dialog.get_by_text("Pick a time within the next 60 days.", exact=True)
        ).to_be_visible()
        assert state.queue_calls == []
        # Escape closes it and focus goes back to the Snooze button.
        await page.keyboard.press("Escape")
        await expect(dialog).to_be_hidden()
        await expect(trigger).to_be_focused()

        await trigger.click()
        await page.get_by_role("menuitem", name="Pick a time…").click()
        picked = (now + timedelta(days=2)).strftime("%Y-%m-%dT10:30")
        await dialog.get_by_label("Snooze until").fill(picked)
        await dialog.get_by_role("button", name="Snooze", exact=True).click()
        await expect(dialog).to_be_hidden()
        await expect(page.get_by_text(re.compile(r"^Snoozed until \w{3} 10:30\.$"))).to_be_visible(
            timeout=WAIT
        )
        call = state.queue_calls[-1]
        assert call["action"] == "snooze" and call["body"]["until"] == f"{picked}:00+08:00", call


async def refused(page: Page, state: Inbox, sentence: str) -> None:
    """The sentence shows once, and the item is read again at once (not at the next poll)."""
    await expect(page.get_by_text(sentence, exact=True)).to_have_count(1, timeout=WAIT)
    sent = state.queue_calls[-1]["at"]
    await eventually(lambda: any(sent < read < sent + 1.5 for read in state.detail_reads), 3)


async def test_each_refusal_ends_in_a_plain_sentence_and_a_refresh(shell: Shell) -> None:
    state = action_inbox()
    state.queue_script = {
        "resolve": [
            (409, {"detail": {"code": "held_reply_pending"}}),
            (409, {"detail": {"code": "customer_wrote"}}),
        ],
        "claim": [
            (409, {"detail": "conversation changed; refresh before acting"}),
            (403, {"detail": "only an administrator can take over someone else's claim"}),
        ],
        "priority": [(404, {"detail": "conversation not found"})],
        "assign": [(503, {"detail": "member directory unavailable"})],
        "topic": [(503, {"detail": "synthetic outage"})],
    }
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 4)
        await open_item(page, NAMED)
        await primary(page).click()
        await refused(page, state, "A held reply needs a decision first.")
        await primary(page).click()
        await refused(page, state, "The customer wrote after you opened this. Reload to see it.")
        await expect(page.get_by_text("A held reply needs a decision first.")).to_have_count(0)

        await open_item(page, UNNAMED)
        await primary(page).click()
        await refused(
            page, state, "This conversation changed. Refreshed, so look again before acting."
        )
        await primary(page).click()
        await refused(page, state, "You do not have permission to do that.")

        more = page.get_by_test_id("inbox-more-menu")
        await more.click()
        await page.get_by_role("menuitem", name="Set priority…").click()
        dialog = page.get_by_test_id("inbox-priority-dialog")
        await dialog.get_by_role("radio", name="Urgent").check()
        await dialog.get_by_role("button", name="Save priority").click()
        await refused(page, state, "This conversation is not available any more.")
        # None of these is kept for a retry: they were refused.
        await expect(page.get_by_role("button", name="Retry the same action")).to_have_count(0)

        # A 503 from assign is the team list; it may not have been applied, so Retry is offered.
        await more.click()
        await page.get_by_role("menuitem", name="Assign to…").click()
        assign = page.get_by_test_id("inbox-assign-dialog")
        await assign.get_by_role("radio", name="Aina Demo").check()
        await assign.get_by_role("button", name="Assign", exact=True).click()
        await refused(page, state, "The team list is unavailable right now. Try again in a moment.")
        retry = page.get_by_role("button", name="Retry the same action")
        await expect(retry).to_be_visible()
        # A kept request owns its key: every queue control waits for it.
        for test_id in ("inbox-primary-action", "inbox-snooze-menu", "inbox-more-menu"):
            await expect(page.get_by_test_id(test_id)).to_be_disabled()
        await retry.click()
        await expect(page.get_by_text("Assigned to Aina Demo.", exact=True)).to_be_visible(
            timeout=WAIT
        )
        first, second = state.queue_calls[-2:]
        assert first["body"] == second["body"], (first, second)

        # A 503 from anything else is the uncertain sentence, not the team list.
        await more.click()
        await page.get_by_role("menuitem", name="Set topic…").click()
        topic = page.get_by_test_id("inbox-topic-dialog")
        await topic.get_by_role("radio", name="Payment").check()
        await topic.get_by_role("button", name="Save topic").click()
        await refused(page, state, UNCERTAIN)
        await page.get_by_role("button", name="Retry the same action").click()
        await expect(page.get_by_text("Topic set to Payment.", exact=True)).to_be_visible(
            timeout=WAIT
        )


async def test_a_network_failure_is_uncertain_and_retry_sends_the_same_request(
    shell: Shell,
) -> None:
    state = action_inbox()
    state.queue_script = {"claim": ["abort"]}
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 4)
        await open_item(page, UNNAMED)
        await primary(page).click()
        await refused(page, state, UNCERTAIN)
        await expect(page.get_by_text(re.compile("Nothing was changed"))).to_have_count(0)
        retry = page.get_by_role("button", name="Retry the same action")
        await expect(primary(page)).to_be_disabled()
        await retry.click()
        await expect(page.get_by_text("You took this conversation.", exact=True)).to_be_visible(
            timeout=WAIT
        )
        first, second = state.queue_calls[-2:]
        assert first["body"]["request_key"] == second["body"]["request_key"], (first, second)
        assert first["body"] == second["body"]


async def test_set_priority_and_topic_send_their_body_and_dialogs_close_on_escape(
    shell: Shell,
) -> None:
    state = action_inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 4)
        await open_item(page, NAMED)
        more = page.get_by_test_id("inbox-more-menu")
        await more.click()
        await page.get_by_role("menuitem", name="Set priority…").click()
        dialog = page.get_by_test_id("inbox-priority-dialog")
        await expect(dialog).to_be_visible()
        radios = dialog.get_by_role("radio")
        await expect(radios).to_have_count(4)
        # The current priority is chosen to begin with.
        await expect(dialog.get_by_role("radio", name="High")).to_be_checked()
        await page.keyboard.press("Escape")
        await expect(dialog).to_be_hidden()
        await expect(more).to_be_focused()
        assert state.queue_calls == []

        await more.click()
        await page.get_by_role("menuitem", name="Set priority…").click()
        await dialog.get_by_role("radio", name="Urgent").check()
        await dialog.get_by_role("button", name="Save priority").click()
        await expect(page.get_by_text("Priority set to Urgent.", exact=True)).to_be_visible(
            timeout=WAIT
        )
        call = state.queue_calls[-1]
        assert call["action"] == "priority" and call["body"]["priority"] == "urgent", call
        assert "seen_customer_at" not in call["body"], call

        await more.click()
        await page.get_by_role("menuitem", name="Set topic…").click()
        topic = page.get_by_test_id("inbox-topic-dialog")
        await expect(topic.get_by_role("radio")).to_have_count(7)
        await page.keyboard.press("Escape")
        await expect(topic).to_be_hidden()
        await expect(more).to_be_focused()
        await more.click()
        await page.get_by_role("menuitem", name="Set topic…").click()
        await topic.get_by_role("radio", name="Complaint").check()
        await topic.get_by_role("button", name="Save topic").click()
        await expect(page.get_by_text("Topic set to Complaint.", exact=True)).to_be_visible(
            timeout=WAIT
        )
        call = state.queue_calls[-1]
        assert call["action"] == "topic" and call["body"]["topic"] == "complaint", call

        # The More menu moves by arrow keys and closes on Escape like Snooze.
        await more.click()
        items = page.get_by_role("menu", name="More actions").get_by_role("menuitem")
        await expect(items.nth(0)).to_be_focused()
        await page.keyboard.press("ArrowDown")
        await expect(items.nth(1)).to_be_focused()
        await page.keyboard.press("Escape")
        await expect(items).to_have_count(0)
        await expect(more).to_be_focused()


async def test_assign_lists_the_team_never_an_empty_list_and_retries(shell: Shell) -> None:
    state = action_inbox(assignees_status=500)
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 4)
        await open_item(page, UNNAMED)
        assert state.assignee_calls == 0
        more = page.get_by_test_id("inbox-more-menu")
        await more.click()
        await page.get_by_role("menuitem", name="Assign to…").click()
        dialog = page.get_by_test_id("inbox-assign-dialog")
        await expect(dialog.get_by_text("Could not load the team.")).to_be_visible(timeout=WAIT)
        await expect(dialog.get_by_role("radio")).to_have_count(0)
        await expect(dialog.get_by_role("button", name="Assign", exact=True)).to_be_disabled()
        state.assignees_status = 200
        await dialog.get_by_role("button", name="Try again").click()
        person = dialog.get_by_role("radio", name="Aina Demo")
        await expect(person).to_be_visible(timeout=WAIT)
        await expect(dialog.get_by_text("Could not load the team.")).to_have_count(0)
        await person.check()
        await dialog.get_by_role("button", name="Assign", exact=True).click()
        await expect(page.get_by_text("Assigned to Aina Demo.", exact=True)).to_be_visible(
            timeout=WAIT
        )
        assert state.queue_calls[-1]["body"]["assignee_id"] == AINA
        # Escape and Cancel return focus to More.
        await more.click()
        await page.get_by_role("menuitem", name="Assign to…").click()
        await expect(dialog).to_be_visible()
        await dialog.get_by_role("button", name="Cancel").click()
        await expect(dialog).to_be_hidden()
        await expect(more).to_be_focused()
    state = action_inbox(assignees_status=503)
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 4)
        await open_item(page, UNNAMED)
        await page.get_by_test_id("inbox-more-menu").click()
        await page.get_by_role("menuitem", name="Assign to…").click()
        dialog = page.get_by_test_id("inbox-assign-dialog")
        await expect(
            dialog.get_by_text("The team list is unavailable right now. Try again in a moment.")
        ).to_be_visible(timeout=WAIT)
        await expect(dialog.get_by_role("radio")).to_have_count(0)
        await expect(dialog.get_by_role("button", name="Assign", exact=True)).to_be_disabled()
        await page.keyboard.press("Escape")
        await expect(page.get_by_test_id("inbox-more-menu")).to_be_focused()


def banner_rows() -> list[dict[str, Any]]:
    overdue = conversation(
        CLOCK_ROW,
        "telegram",
        name="Nadia Ismail",
        known=True,
        preview="Where is my parcel?",
        minutes=3,
        queue_status="needs_staff",
        queue_reason="escalation",
        assignee_id=AINA,
        assignee_name="Aina Demo",
        needs_staff_since=fixed(-200),
        due_at=fixed(-60),
    )
    soon = conversation(
        QUIET_ROW,
        "whatsapp",
        name="Omar Hakim",
        known=True,
        preview="Hello?",
        minutes=8,
        queue_status="needs_staff",
        queue_reason="customer_message",
        assignee_id=AINA,
        assignee_name=None,
        needs_staff_since=fixed(-20),
        due_at=fixed(40),
    )
    mine = conversation(
        NAMED,
        "telegram",
        name="Aisyah Rahman",
        known=True,
        preview="Thanks",
        minutes=9,
        queue_status="waiting_customer",
        assignee_id=ME_ID,
        assignee_name="Synthetic User",
    )
    return [overdue, soon, mine]


async def test_the_banner_says_how_long_it_has_waited_and_who_has_it(shell: Shell) -> None:
    state = Inbox(items=banner_rows())
    async with inbox_page(shell, state) as page:
        await page.context.clock.set_fixed_time(NOW)
        await page.reload()
        await rows_loaded(page, 3)
        await open_item(page, CLOCK_ROW)
        banner = page.get_by_test_id("inbox-queue-banner")
        await expect(banner).to_contain_text("Waiting on us for 3h 20m, due 1h ago")
        await expect(banner).to_contain_text("Assigned to Aina Demo")
        await expect(header(page).get_by_text("Overdue 1h", exact=True)).to_be_visible()
        await open_item(page, QUIET_ROW)
        await expect(banner).to_contain_text("Waiting on us for 20m, due in 40m")
        await expect(banner).to_contain_text("Assigned to someone outside the team list")
        # Mine and waiting on the customer: nothing to add.
        await open_item(page, NAMED)
        await expect(header(page).get_by_text("Waiting on customer", exact=True)).to_be_visible()
        await expect(banner).to_have_count(0)


async def test_take_over_hand_back_and_older_rows_still_work(shell: Shell) -> None:
    # An older server: this row has no queue fields at all.
    state = Inbox(bare={LONG})
    # The escalation paused the assistant for a while: Take over indefinitely is offered.
    row_of(state, NAMED).update(human_muted=True, owned_by="human", ai_muted_until=at(600))
    async with inbox_page(shell, state) as page:
        await rows_loaded(page)
        await open_thread(page, LONG)
        await expect(primary(page)).to_have_text("Take over")
        await expect(page.get_by_test_id("inbox-snooze-menu")).to_have_count(0)
        await expect(page.get_by_test_id("inbox-more-menu")).to_have_count(0)

        await open_thread(page, NEWCOMER)
        await expect(primary(page)).to_have_text("Hand back to assistant")
        page.once("dialog", lambda dialog: asyncio.ensure_future(dialog.dismiss()))
        await primary(page).click()
        await asyncio.sleep(0.5)
        assert state.handoffs == []
        page.once("dialog", lambda dialog: asyncio.ensure_future(dialog.accept()))
        await primary(page).click()
        await eventually(lambda: len(state.handoffs) == 1)
        assert state.handoffs[0]["action"] == "release", state.handoffs

        await open_thread(page, NAMED)
        await page.get_by_role("button", name="Take over indefinitely").click()
        await eventually(lambda: len(state.handoffs) == 2)
        assert state.handoffs[1]["action"] == "takeover", state.handoffs


async def test_hand_back_from_a_queued_item_asks_first(shell: Shell) -> None:
    state = action_inbox()
    row_of(state, NAMED).update(human_muted=True, owned_by="human")
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 4)
        await open_item(page, NAMED)
        more = page.get_by_test_id("inbox-more-menu")
        items = await menu_items(page, "inbox-more-menu")
        assert "Take over the chat" not in items and "Hand back to the assistant" in items, items
        await more.click()
        page.once("dialog", lambda dialog: asyncio.ensure_future(dialog.dismiss()))
        await page.get_by_role("menuitem", name="Hand back to the assistant").click()
        await asyncio.sleep(0.5)
        assert state.handoffs == []
        await more.click()
        page.once("dialog", lambda dialog: asyncio.ensure_future(dialog.accept()))
        await page.get_by_role("menuitem", name="Hand back to the assistant").click()
        await eventually(lambda: len(state.handoffs) == 1)
        assert state.handoffs[0]["action"] == "release", state.handoffs
        # A held-reply item the assistant still answers: Take over is in More so staff can reply.
        await open_item(page, WAITING_ROW)
        items = await menu_items(page, "inbox-more-menu")
        assert "Take over the chat" in items, items


@pytest.mark.parametrize("width", (320, 390))
async def test_the_thread_header_fits_small_screens(shell: Shell, width: int) -> None:
    state = action_inbox()
    async with inbox_page(shell, state, width=width) as page:
        await rows_loaded(page, 4)
        await open_item(page, OTHER_ROW)
        await expect(primary(page)).to_have_text("Take it from Aina")
        for test_id in ("inbox-primary-action", "inbox-snooze-menu", "inbox-more-menu"):
            control = page.get_by_test_id(test_id)
            box = await control.bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= width, (test_id, box)
            assert box["height"] >= 40, (test_id, box)
        primary_box = await primary(page).bounding_box()
        snooze_box = await page.get_by_test_id("inbox-snooze-menu").bounding_box()
        assert primary_box and snooze_box
        # The primary action comes first in reading order.
        assert (primary_box["y"], primary_box["x"]) <= (snooze_box["y"], snooze_box["x"])
        measured = await page_width(page)
        assert measured["scroll"] <= measured["inner"], measured
        for test_id in ("inbox-more-menu", "inbox-snooze-menu"):
            await page.get_by_test_id(test_id).click()
            menu = page.get_by_role("menu")
            box = await menu.bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= width, (test_id, box)
            for item in await menu.get_by_role("menuitem").all():
                assert await on_top(item), test_id
            measured = await page_width(page)
            assert measured["scroll"] <= measured["inner"], (test_id, measured)
            await page.keyboard.press("Escape")
        await page.get_by_test_id("inbox-more-menu").click()
        await page.get_by_role("menuitem", name="Assign to…").click()
        dialog = page.get_by_test_id("inbox-assign-dialog")
        await expect(dialog.get_by_role("radio", name="Aina Demo")).to_be_visible(timeout=WAIT)
        box = await dialog.bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= width, box
