"""Real browser checks for the Inbox queue bar: tabs with counts, search, Filters and Sort.

The same stubbed harness as `test_inbox_redesign_browser.py` (its module-scoped Next.js shell
and its synthetic Inbox API); the rows here carry the work-queue fields and the stub filters by
`state`, so each tab shows only its own rows.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import pytest
from csa.console.inbox_settings import InboxSettingsIn
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
    saved_view,
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
        # Every row has a selection box, hidden until the row is hovered or focused.
        await expect(listing.get_by_role("checkbox", include_hidden=True)).to_have_count(7)
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
    # A person holds this chat, so the reply box is on screen too.
    row_of(state, UNNAMED).update(human_muted=True, owned_by="human")
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

        # A 503 from assign is the member directory being down: nothing was written, so it is
        # settled. No Retry is offered and nothing in the Inbox waits for it.
        await more.click()
        await page.get_by_role("menuitem", name="Assign to…").click()
        assign = page.get_by_test_id("inbox-assign-dialog")
        await assign.get_by_role("radio", name="Aina Demo").check()
        await assign.get_by_role("button", name="Assign", exact=True).click()
        await refused(page, state, "The team list is unavailable right now. Try again in a moment.")
        await expect(page.get_by_role("button", name="Retry the same action")).to_have_count(0)
        for test_id in ("inbox-primary-action", "inbox-snooze-menu", "inbox-more-menu"):
            await expect(page.get_by_test_id(test_id)).to_be_enabled()
        await expect(page.get_by_test_id("inbox-composer").locator("textarea")).to_be_enabled()
        # Assigning again once the directory is back is a new request.
        await more.click()
        await page.get_by_role("menuitem", name="Assign to…").click()
        await assign.get_by_role("radio", name="Aina Demo").check()
        await assign.get_by_role("button", name="Assign", exact=True).click()
        await expect(page.get_by_text("Assigned to Aina Demo.", exact=True)).to_be_visible(
            timeout=WAIT
        )
        first, second = state.queue_calls[-2:]
        assert first["body"]["request_key"] != second["body"]["request_key"], (first, second)

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


# ---- selection and bulk actions ----------------------------------------------------------------

AS_OF = "2026-11-17T01:59:30.123456+00:00"
CAP_TEXT = "You can select up to 50 conversations at a time."
UNKNOWN_ROW = "a0000000-0000-4000-8000-000000000009"
CHECKED_JS = """els => els
    .filter(el => el.parentElement.querySelector('input[type="checkbox"]').checked)
    .map(el => el.dataset.conversationId)"""


def bulk_bar(page: Page) -> Any:
    return page.get_by_test_id("inbox-bulk-bar")


def bulk_result(page: Page) -> Any:
    return page.get_by_role("region", name="Bulk result")


def check_box(page: Page, identifier: str) -> Any:
    """The row's selection box: a sibling of the row's button in the same list item."""
    return page.locator(f'li:has(> [data-conversation-id="{identifier}"]) input[type="checkbox"]')


async def tick(page: Page, identifier: str) -> None:
    """Click a row's box as a mouse user does: it shows while the row is hovered."""
    await row(page, identifier).hover()
    await check_box(page, identifier).click()


async def checked_ids(page: Page) -> set[str]:
    return set(await page.locator("[data-conversation-id]").evaluate_all(CHECKED_JS))


def ids_of(rows: list[dict[str, Any]]) -> list[str]:
    return [item["id"] for item in rows]


async def test_checking_rows_shows_the_bulk_bar_and_sends_the_lists_read_time(
    shell: Shell,
) -> None:
    rows = many_rows(5)
    ids = ids_of(rows)
    state = Inbox(items=rows, as_of=AS_OF)
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 5)
        await expect(bulk_bar(page)).to_have_count(0)
        await tick(page, ids[0])
        await expect(bulk_bar(page)).to_contain_text("1 selected")
        # The bulk bar takes the place of the search, Filters and Sort line.
        await expect(page.get_by_test_id("inbox-search")).to_have_count(0)
        await tick(page, ids[1])
        await expect(bulk_bar(page)).to_contain_text("2 selected")
        assert await checked_ids(page) == set(ids[:2])
        await bulk_bar(page).get_by_role("button", name="Resolve", exact=True).click()
        result = bulk_result(page)
        await expect(result).to_contain_text("2 done.", timeout=WAIT)
        # The request: the action, the ids in list order, the list's own read time, no version.
        assert len(state.bulk_calls) == 1, state.bulk_calls
        body = state.bulk_calls[0]["body"]
        assert set(body) == {"request_key", "action", "ids", "seen_at"}, body
        assert body["action"] == "resolve" and body["ids"] == ids[:2], body
        assert body["seen_at"] == AS_OF, body
        # Everything was done: the selection is empty, focus is on the result.
        await expect(result).to_be_focused()
        await expect(bulk_bar(page)).to_have_count(0)
        await expect(page.get_by_test_id("inbox-search")).to_be_visible()
        assert await checked_ids(page) == set()


async def test_bulk_results_name_what_did_not_apply_and_keep_it_selected(shell: Shell) -> None:
    rows = many_rows(30)
    ids = ids_of(rows)
    state = Inbox(
        items=rows, bulk_outcomes={ids[5]: "held_reply_pending", ids[10]: "customer_wrote"}
    )
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 30)
        await tick(page, ids[0])
        bar = bulk_bar(page)
        await bar.get_by_role("button", name="Select all loaded").click()
        await expect(bar).to_contain_text("30 selected")
        await expect(bar).to_contain_text("Selected all 30 conversations loaded so far.")
        await bar.get_by_role("button", name="Claim", exact=True).click()
        result = bulk_result(page)
        await expect(result.get_by_role("status")).to_have_text(
            "28 done. 1 not done: a held reply needs a decision first."
            " 1 not done: the customer wrote after you opened the list.",
            timeout=WAIT,
        )
        await expect(result.get_by_role("listitem")).to_have_text(
            [
                "Customer 105: a held reply needs a decision first.",
                "Customer 110: the customer wrote after you opened the list.",
            ]
        )
        await expect(result).to_be_focused()
        body = state.bulk_calls[-1]["body"]
        assert body["action"] == "claim" and body["ids"] == ids, body
        # The two that did not apply stay selected; the 28 done leave the selection.
        await expect(bar).to_contain_text("2 selected")
        assert await checked_ids(page) == {ids[5], ids[10]}


async def test_the_selection_stops_at_fifty_and_says_why(shell: Shell) -> None:
    rows = many_rows(55)
    ids = ids_of(rows)
    state = Inbox(items=rows)
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 55)
        await tick(page, ids[0])
        bar = bulk_bar(page)
        await bar.get_by_role("button", name="Select all loaded").click()
        await expect(bar).to_contain_text("50 selected")
        await expect(bar).to_contain_text(
            "Selected the first 50 of the 55 conversations loaded so far."
        )
        assert await checked_ids(page) == set(ids[:50])
        # The 51st cannot be checked, by mouse or by key, and the bar says why.
        await tick(page, ids[50])
        await expect(check_box(page, ids[50])).not_to_be_checked()
        await expect(bar).to_contain_text(CAP_TEXT)
        await row(page, ids[52]).focus()
        await page.keyboard.press("x")
        await expect(check_box(page, ids[52])).not_to_be_checked()
        await expect(bar).to_contain_text("50 selected")
        # Clearing one makes room again.
        await check_box(page, ids[0]).click()
        await tick(page, ids[50])
        await expect(check_box(page, ids[50])).to_be_checked()
        await expect(bar).to_contain_text("50 selected")
        await expect(bar).not_to_contain_text(CAP_TEXT)


async def test_staff_get_no_bulk_assign_and_admins_do(shell: Shell) -> None:
    for role, assign in (("tenant_staff", 0), ("tenant_admin", 1)):
        state = queue_inbox()
        async with inbox_page(shell, state, role=role) as page:
            await rows_loaded(page, 3)
            await tick(page, NAMED)
            bar = bulk_bar(page)
            for name in ("Claim", "Resolve", "Clear"):
                await expect(bar.get_by_role("button", name=name, exact=True)).to_be_visible()
            await expect(bar.get_by_test_id("inbox-bulk-snooze")).to_be_visible()
            await expect(bar.get_by_role("button", name=re.compile("^Assign"))).to_have_count(
                assign
            )


async def test_platform_admin_on_a_company_assigns_many(shell: Shell) -> None:
    team = [
        {"id": AINA, "display_name": "Aina Demo", "role": "tenant_staff"},
        {
            "id": "33333333-3333-4333-8333-333333333332",
            "display_name": "Bakar Demo",
            "role": "tenant_admin",
        },
    ]
    state = queue_inbox(assignees=team)
    async with inbox_page(shell, state, role="platform_admin", path="/inbox") as page:
        await page.get_by_label("Tenant").select_option(redesign.TENANT)
        await rows_loaded(page, 3)
        await tick(page, NAMED)
        await tick(page, UNNAMED)
        await bulk_bar(page).get_by_role("button", name="Assign…").click()
        dialog = page.get_by_test_id("inbox-assign-dialog")
        await expect(dialog).to_be_visible()
        await expect(dialog).to_contain_text("Assign 2 conversations")
        await dialog.get_by_role("radio", name="Aina Demo").check()
        await dialog.get_by_role("button", name="Assign", exact=True).click()
        await expect(bulk_result(page)).to_contain_text("2 done.", timeout=WAIT)
        call = state.bulk_calls[-1]
        assert call["body"]["action"] == "assign", call
        assert call["body"]["assignee_id"] == AINA and call["body"]["ids"] == [NAMED, UNNAMED]
        assert "assigneeName" not in call["body"], call
        assert f"client_id={redesign.TENANT}" in call["query"], call


async def test_a_new_tab_or_filter_clears_the_selection(shell: Shell) -> None:
    state = queue_inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        await tick(page, NAMED)
        await expect(bulk_bar(page)).to_contain_text("1 selected")
        await tab(page, "all").click()
        await rows_loaded(page, 7)
        await expect(bulk_bar(page)).to_have_count(0)
        assert await checked_ids(page) == set()
        # A filter on top of the tab: removing its chip clears the selection too.
        panel = await open_filters(page)
        await panel.get_by_role("checkbox", name="Escalated").check()
        await page.keyboard.press("Escape")
        await expect(panel).to_be_hidden()
        await rows_loaded(page, 1)
        await tick(page, NAMED)
        await expect(bulk_bar(page)).to_contain_text("1 selected")
        await page.get_by_role("button", name="Remove filter: Reason: Escalated").click()
        await rows_loaded(page, 7)
        await expect(bulk_bar(page)).to_have_count(0)
        assert await checked_ids(page) == set()


async def test_escape_clears_the_selection_unless_a_menu_or_dialog_took_it(shell: Shell) -> None:
    state = queue_inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        await tick(page, NAMED)
        bar = bulk_bar(page)
        snooze = bar.get_by_test_id("inbox-bulk-snooze")
        await snooze.click()
        menu = page.get_by_role("menu")
        await expect(menu).to_be_visible()
        await page.keyboard.press("Escape")
        await expect(menu).to_have_count(0)
        await expect(snooze).to_be_focused()
        await expect(bar).to_contain_text("1 selected")
        await bar.get_by_role("button", name="Assign…").click()
        dialog = page.get_by_test_id("inbox-assign-dialog")
        await expect(dialog).to_be_visible()
        await page.keyboard.press("Escape")
        await expect(dialog).to_be_hidden()
        await expect(bar).to_contain_text("1 selected")
        # Now nothing else takes it: Escape clears the selection, and focus lands on Select.
        await page.keyboard.press("Escape")
        await expect(bar).to_have_count(0)
        assert await checked_ids(page) == set()
        await expect(page.get_by_test_id("inbox-select-mode")).to_be_focused()


async def test_x_on_a_focused_row_selects_it_and_the_box_is_not_a_tab_stop(shell: Shell) -> None:
    state = queue_inbox()
    state.items.append(
        conversation(
            UNKNOWN_ROW,
            "telegram",
            name=None,
            preview="Hi",
            minutes=6,
            queue_status="needs_staff",
            queue_reason="customer_message",
            needs_staff_since=at(-8),
            due_at=at(40),
        )
    )
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 4)
        await row(page, NAMED).focus()
        await page.keyboard.press("x")
        await expect(check_box(page, NAMED)).to_be_checked()
        await expect(bulk_bar(page)).to_contain_text("1 selected")
        await expect(row(page, NAMED)).to_be_focused()
        # Tab goes from row to row: the box is not a stop of its own outside Select mode.
        await page.keyboard.press("Tab")
        await expect(row(page, UNNAMED)).to_be_focused()
        await page.keyboard.press("x")
        await expect(bulk_bar(page)).to_contain_text("2 selected")
        await page.keyboard.press("x")
        await expect(check_box(page, UNNAMED)).not_to_be_checked()
        await expect(bulk_bar(page)).to_contain_text("1 selected")
        # The box names the person; a contact with no real name also gets the channel.
        await expect(check_box(page, NAMED)).to_have_accessible_name("Select Aisyah Rahman")
        await expect(check_box(page, UNNAMED)).to_have_accessible_name(
            "Select ••••0148 on WhatsApp"
        )
        # A hidden box has no accessible name: show it as a pointer user does.
        await row(page, UNKNOWN_ROW).hover()
        await expect(check_box(page, UNKNOWN_ROW)).to_have_accessible_name(
            "Select Unknown sender on Telegram"
        )


async def test_select_mode_shows_every_box_for_touch(shell: Shell) -> None:
    rows = many_rows(4)
    ids = ids_of(rows)
    state = Inbox(items=rows)
    async with inbox_page(shell, state, width=390) as page:
        await rows_loaded(page, 4)
        toggle = page.get_by_test_id("inbox-select-mode")
        await expect(toggle).to_have_text("Select")
        await expect(toggle).to_have_attribute("aria-pressed", "false")
        for identifier in ids:
            await expect(check_box(page, identifier)).to_be_hidden()
        await toggle.click()
        await expect(toggle).to_have_attribute("aria-pressed", "true")
        for identifier in ids:
            await expect(check_box(page, identifier)).to_be_visible()
        await check_box(page, ids[1]).click()
        await expect(bulk_bar(page)).to_contain_text("1 selected")
        # In Select mode each box is a stop in the tab order, before its row.
        await check_box(page, ids[1]).focus()
        await page.keyboard.press("Tab")
        await expect(row(page, ids[1])).to_be_focused()
        await page.keyboard.press("Tab")
        await expect(check_box(page, ids[2])).to_be_focused()
        await page.keyboard.press("Space")
        await expect(bulk_bar(page)).to_contain_text("2 selected")
        # Clear empties the selection and leaves Select mode.
        await bulk_bar(page).get_by_role("button", name="Clear", exact=True).click()
        await expect(bulk_bar(page)).to_have_count(0)
        await expect(toggle).to_have_attribute("aria-pressed", "false")
        await expect(toggle).to_be_focused()
        # The bar shrank under the pointer: move it off the rows, which show their box on hover.
        await page.mouse.move(1, 1)
        for identifier in ids:
            await expect(check_box(page, identifier)).to_be_hidden()


async def test_each_rows_box_stays_right_after_another_row_is_toggled(shell: Shell) -> None:
    rows = many_rows(4)
    a, b, c, _d = ids_of(rows)
    state = Inbox(items=rows)
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 4)
        await tick(page, a)
        await tick(page, b)
        await check_box(page, a).click()
        await tick(page, c)
        await expect(bulk_bar(page)).to_contain_text("2 selected")
        assert await checked_ids(page) == {b, c}
        await check_box(page, b).click()
        await expect(bulk_bar(page)).to_contain_text("1 selected")
        assert await checked_ids(page) == {c}
        await tick(page, a)
        await expect(bulk_bar(page)).to_contain_text("2 selected")
        assert await checked_ids(page) == {a, c}


async def test_a_retried_uncertain_bulk_applies_its_results(shell: Shell) -> None:
    rows = many_rows(3)
    ids = ids_of(rows)
    state = Inbox(items=rows, bulk_script=["abort"], bulk_outcomes={ids[1]: "customer_wrote"})
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        await tick(page, ids[0])
        bar = bulk_bar(page)
        await bar.get_by_role("button", name="Select all loaded").click()
        await expect(bar).to_contain_text("3 selected")
        await bar.get_by_role("button", name="Resolve", exact=True).click()
        await expect(page.get_by_text(UNCERTAIN, exact=True)).to_be_visible(timeout=WAIT)
        # Nothing else is sent while the uncertain request is kept.
        await expect(bar.get_by_role("button", name="Resolve", exact=True)).to_be_disabled()
        await expect(check_box(page, ids[0])).to_be_disabled()
        await page.get_by_role("button", name="Retry the same action").click()
        result = bulk_result(page)
        await expect(result.get_by_role("status")).to_have_text(
            "2 done. 1 not done: the customer wrote after you opened the list.", timeout=WAIT
        )
        await expect(result.get_by_role("listitem")).to_have_text(
            ["Customer 101: the customer wrote after you opened the list."]
        )
        await expect(result).to_be_focused()
        await expect(bar).to_contain_text("1 selected")
        assert await checked_ids(page) == {ids[1]}
        first, second = state.bulk_calls
        assert first["body"] == second["body"], (first, second)


@pytest.mark.parametrize("width", (320, 390))
async def test_the_bulk_bar_and_its_result_fit_small_screens(shell: Shell, width: int) -> None:
    rows = many_rows(30)
    ids = ids_of(rows)
    state = Inbox(
        items=rows,
        bulk_outcomes={
            ids[2]: "held_reply_pending",
            ids[3]: "not_allowed",
            ids[4]: "not_in_queue",
            ids[5]: "not_found",
        },
    )
    async with inbox_page(shell, state, width=width) as page:
        await rows_loaded(page, 30)
        await page.get_by_test_id("inbox-select-mode").click()
        await page.get_by_role("button", name="Select all loaded").click()
        bar = bulk_bar(page)
        await expect(bar).to_contain_text("30 selected")
        measured = await page_width(page)
        assert measured["scroll"] <= measured["inner"], ("bar", measured)
        for button in await bar.get_by_role("button").all():
            box = await button.bounding_box()
            assert box and box["height"] >= 40 and box["x"] + box["width"] <= width, box
        await bar.get_by_role("button", name="Claim", exact=True).click()
        result = bulk_result(page)
        await expect(result.get_by_role("listitem")).to_have_count(4, timeout=WAIT)
        measured = await page_width(page)
        assert measured["scroll"] <= measured["inner"], ("result", measured)
        box = await result.bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= width, box
        # Scrolled into the list, the tab strip is still the only thing stuck to the top.
        await page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight / 2)")
        await asyncio.sleep(0.2)
        strip = await page.get_by_role("region", name="Conversation queues").bounding_box()
        assert strip is not None and abs(strip["y"]) < 1 and strip["height"] <= STICKY_BOUND
        gone = await bar.bounding_box()
        assert gone is not None and gone["y"] + gone["height"] <= 0, gone


async def test_desktop_bar_with_the_bulk_bar_and_the_panes_fit_one_screen(shell: Shell) -> None:
    """The bulk bar (two lines with its message) is taller: the panes shrink to keep one screen."""
    rows = many_rows(30)
    state = Inbox(items=rows)
    async with inbox_page(shell, state) as page:
        await page.set_viewport_size({"width": 1440, "height": 800})
        await page.goto(f"{shell.base}/portal/inbox")
        await rows_loaded(page, 30)
        before = await page.get_by_test_id("inbox-queue-bar").bounding_box()
        await tick(page, rows[0]["id"])
        await bulk_bar(page).get_by_role("button", name="Select all loaded").click()
        await expect(bulk_bar(page)).to_contain_text("Selected all 30 conversations loaded so far.")
        await page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        await asyncio.sleep(0.3)
        bar = await page.get_by_test_id("inbox-queue-bar").bounding_box()
        assert before and bar and abs(bar["y"]) < 1, bar
        assert bar["height"] > before["height"], (before, bar)
        listing = await page.locator("[data-inbox-list-scroll]").bounding_box()
        assert listing is not None, listing
        assert (
            listing["y"] >= bar["y"] + bar["height"] and listing["y"] + listing["height"] <= 800
        ), (
            bar,
            listing,
        )


LATER = "2026-11-17T02:05:00.000000+00:00"
LATEST = "2026-11-17T02:10:00.000000+00:00"
NEWEST = "2026-11-17T02:15:00.000000+00:00"
CUSTOMER_WROTE = "1 not done: the customer wrote after you opened the list."


async def test_bulk_sends_the_read_time_of_the_selection_not_the_latest_poll(shell: Shell) -> None:
    rows = many_rows(4)
    ids = ids_of(rows)
    state = Inbox(items=rows, as_of=AS_OF, bulk_outcomes={ids[1]: "customer_wrote"})
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 4)
        await tick(page, ids[0])
        # The list polls and is now read later; a row added to the selection keeps the first time.
        state.as_of = LATER
        await settle(state, len(state.list_queries))
        await tick(page, ids[1])
        # From here the list cannot be read again until the test says so.
        state.fail_lists = True
        bar = bulk_bar(page)
        resolve = bar.get_by_role("button", name="Resolve", exact=True)
        await resolve.click()
        await expect(bulk_result(page).get_by_role("status")).to_have_text(
            f"1 done. {CUSTOMER_WROTE}", timeout=WAIT
        )
        assert state.bulk_calls[-1]["body"]["seen_at"] == AS_OF, state.bulk_calls
        await expect(bar).to_contain_text("1 selected")
        # Acting again with no new list answer: still the old time, so it is refused again.
        await resolve.click()
        await eventually(lambda: len(state.bulk_calls) == 2)
        await expect(bulk_result(page).get_by_role("status")).to_have_text(
            f"Nothing was done. {CUSTOMER_WROTE}", timeout=WAIT
        )
        assert state.bulk_calls[-1]["body"]["seen_at"] == AS_OF, state.bulk_calls
        # The refreshed list (with the customer's message) answers: its time is the new one.
        state.as_of = LATEST
        state.fail_lists = False
        await settle(state, len(state.list_queries))
        state.bulk_outcomes = {}
        await resolve.click()
        await eventually(lambda: len(state.bulk_calls) == 3)
        await expect(bulk_result(page).get_by_role("status")).to_have_text("1 done.", timeout=WAIT)
        assert state.bulk_calls[-1]["body"]["seen_at"] == LATEST, state.bulk_calls
        # A new selection after that starts from the list as it reads then.
        state.as_of = NEWEST
        await settle(state, len(state.list_queries))
        await tick(page, ids[2])
        await bar.get_by_role("button", name="Claim", exact=True).click()
        await eventually(lambda: len(state.bulk_calls) == 4)
        assert state.bulk_calls[-1]["body"]["seen_at"] == NEWEST, state.bulk_calls


async def test_a_thread_action_after_a_bulk_shows_in_the_thread_without_the_bulk_list(
    shell: Shell,
) -> None:
    state = action_inbox(bulk_outcomes={NAMED: "customer_wrote"})
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 4)
        await open_item(page, UNNAMED)
        await tick(page, NAMED)
        await tick(page, WAITING_ROW)
        await bulk_bar(page).get_by_role("button", name="Resolve", exact=True).click()
        result = bulk_result(page)
        await expect(result.get_by_role("list", name="Not done")).to_have_count(1, timeout=WAIT)
        await expect(primary(page)).to_have_text("Take it")
        await primary(page).click()
        thread = page.get_by_test_id("inbox-thread")
        await expect(thread.get_by_text("You took this conversation.", exact=True)).to_be_visible(
            timeout=WAIT
        )
        await expect(result).to_have_count(0)
        await expect(page.get_by_role("list", name="Not done")).to_have_count(0)
        await expect(page.get_by_text("You took this conversation.", exact=True)).to_have_count(1)


async def test_select_all_by_keyboard_moves_focus_to_the_count(shell: Shell) -> None:
    rows = many_rows(4)
    ids = ids_of(rows)
    state = Inbox(items=rows)
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 4)
        count = page.get_by_test_id("inbox-bulk-count")
        # From Select mode, where the button's whole line gives way to the bulk bar.
        toggle = page.get_by_test_id("inbox-select-mode")
        await toggle.focus()
        await page.keyboard.press("Enter")
        await expect(toggle).to_have_attribute("aria-pressed", "true")
        await page.get_by_role("button", name="Select all loaded").focus()
        await page.keyboard.press("Enter")
        await expect(bulk_bar(page)).to_contain_text("4 selected")
        await expect(count).to_be_focused()
        await page.keyboard.press("Escape")
        await expect(bulk_bar(page)).to_have_count(0)
        # From inside the bulk bar, where the button goes once everything loaded is selected.
        await row(page, ids[0]).focus()
        await page.keyboard.press("x")
        await expect(bulk_bar(page)).to_contain_text("1 selected")
        select_all = bulk_bar(page).get_by_role("button", name="Select all loaded")
        await select_all.focus()
        await page.keyboard.press("Enter")
        await expect(bulk_bar(page)).to_contain_text("4 selected")
        await expect(select_all).to_have_count(0)
        await expect(count).to_be_focused()


# ---- saved views (Task 7) ----

VIEWS_EMPTY = (
    "You have no saved views yet. Set up the filters you want, then choose Save this view."
)
VIEWS_FAILED = "Could not load your saved views. Try again."
UNSUPPORTED = "This view uses a filter this screen does not have yet."
LONG_VIEW = ("Every urgent WhatsApp complaint waiting longer than four hours" * 2)[:60]


def views_button(page: Page) -> Any:
    return page.get_by_test_id("inbox-views-menu")


def views_menu(page: Page) -> Any:
    return page.get_by_role("menu", name="Saved views")


def view_calls(state: Inbox, method: str) -> list[dict[str, Any]]:
    return [call for call in state.view_calls if call["method"] == method]


async def open_views(page: Page) -> Any:
    await views_button(page).click()
    menu = views_menu(page)
    await expect(menu).to_be_visible()
    return menu


async def menu_texts(menu: Any) -> list[str]:
    return [
        text.strip()
        for text in await menu.locator("[role^='menuitem']").evaluate_all(
            "els => els.map(el => el.querySelector('[data-label]')?.textContent ?? el.textContent)"
        )
    ]


async def test_saving_the_current_view_posts_its_name_and_filters_never_the_search(
    shell: Shell,
) -> None:
    state = queue_inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        # Needs us, high and above, sorted by priority, with a search typed in.
        panel = await open_filters(page)
        await (
            panel.get_by_role("group", name="Priority")
            .get_by_role("radio", name="High and above")
            .check()
        )
        await page.keyboard.press("Escape")
        await page.get_by_test_id("inbox-queue-sort").select_option("priority")
        await page.get_by_test_id("inbox-search").fill("Aisyah")
        await eventually(lambda: "q=Aisyah" in state.list_queries[-1])
        # Nobody has asked for the views before the menu opens.
        assert state.view_calls == []
        button = views_button(page)
        await expect(button).to_have_accessible_name("Views")
        await expect(button).to_have_attribute("aria-haspopup", "menu")
        await expect(page.get_by_test_id("inbox-views-slot").locator(button)).to_have_count(1)
        menu = await open_views(page)
        await expect(button).to_have_attribute("aria-expanded", "true")
        # No views yet: the sentence and Save this view, never Manage views.
        await expect(menu.get_by_text(VIEWS_EMPTY, exact=True)).to_be_visible(timeout=WAIT)
        assert await menu_texts(menu) == [VIEWS_EMPTY, "Save this view…"]
        await menu.get_by_role("menuitem", name="Save this view…").click()
        dialog = page.get_by_role("dialog", name="Save this view")
        await expect(dialog).to_be_visible()
        name = dialog.get_by_label("Name", exact=True)
        await expect(name).to_be_focused()
        await expect(name).to_have_attribute("maxlength", "60")
        # A name is required: nothing is sent without one.
        await name.fill("   ")
        await dialog.get_by_role("button", name="Save view").click()
        await expect(dialog.get_by_text("Enter a name for the view.", exact=True)).to_be_visible()
        await expect(name).to_have_attribute("aria-invalid", "true")
        assert view_calls(state, "POST") == []
        await name.fill("  Urgent first  ")
        await dialog.get_by_role("button", name="Save view").click()
        await expect(dialog).to_be_hidden(timeout=WAIT)
        posts = view_calls(state, "POST")
        assert len(posts) == 1, posts
        body = posts[0]["body"]
        assert set(body) == {"name", "filters"}, body
        assert body["name"] == "Urgent first", body
        assert body["filters"] == {
            "state": ["needs_staff"],
            "priority_min": "high",
            "order": "priority",
        }, body
        # Said in words, focus back on the button, which now names the view.
        await expect(page.get_by_text("Saved the view “Urgent first”.", exact=True)).to_be_visible()
        await expect(button).to_be_focused()
        await expect(button).to_have_text("Urgent first")
        await expect(button).to_have_accessible_name("Views, showing Urgent first")
        menu = await open_views(page)
        item = menu.get_by_role("menuitemradio", name="Urgent first")
        await expect(item).to_have_attribute("aria-checked", "true")
        assert await menu_texts(menu) == ["Urgent first", "Save this view…", "Manage views…"]


async def test_choosing_a_view_sends_its_filters_and_order_and_clears_search_and_select(
    shell: Shell,
) -> None:
    state = queue_inbox(
        views={
            ME_ID: [
                saved_view(
                    "My high ones",
                    {
                        "state": ["needs_staff", "waiting_customer"],
                        "assignee": "me",
                        "priority_min": "high",
                        "order": "activity",
                    },
                ),
                saved_view(
                    "WhatsApp complaints",
                    {"topic": ["complaint"], "channel": "whatsapp", "order": "waiting"},
                ),
            ]
        }
    )
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        search = page.get_by_test_id("inbox-search")
        await search.fill("Aisyah")
        await eventually(lambda: "q=Aisyah" in state.list_queries[-1])
        toggle = page.get_by_test_id("inbox-select-mode")
        await toggle.click()
        await expect(toggle).to_have_attribute("aria-pressed", "true")
        menu = await open_views(page)
        before = len(state.list_queries)
        await menu.get_by_role("menuitemradio", name="My high ones").click()
        await expect(menu).to_have_count(0)
        # Exactly the view's filters and order; the search and Select mode are gone.
        assert await settle(state, before) == {
            "state": ["needs_staff", "waiting_customer"],
            "assignee": ["me"],
            "priority_min": ["high"],
            "order": ["activity"],
        }
        await asyncio.sleep(0.4)
        assert "q=" not in state.list_queries[-1], state.list_queries[-1]
        await expect(search).to_have_value("")
        await expect(toggle).to_have_attribute("aria-pressed", "false")
        # Its tab is Mine (the view holds Mine's own filters), the rest shows as a chip.
        await expect(tab(page, "mine")).to_have_attribute("aria-selected", "true")
        chips = page.get_by_test_id("inbox-queue-chips")
        await expect(
            chips.get_by_role("button", name="Remove filter: Priority high or above")
        ).to_be_visible()
        await expect(page.get_by_test_id("inbox-queue-sort")).to_have_value("activity")
        await expect(views_button(page)).to_have_text("My high ones")
        await expect(
            page.get_by_text("Showing the view “My high ones”.", exact=True)
        ).to_be_visible()

        menu = await open_views(page)
        before = len(state.list_queries)
        await menu.get_by_role("menuitemradio", name="WhatsApp complaints").click()
        assert await settle(state, before) == {
            "topic": ["complaint"],
            "channel": ["whatsapp"],
            "order": ["waiting"],
        }
        await expect(tab(page, "all")).to_have_attribute("aria-selected", "true")
        await expect(
            chips.get_by_role("button", name="Remove filter: Topic: Complaint")
        ).to_be_visible()
        await expect(
            chips.get_by_role("button", name="Remove filter: Channel: WhatsApp")
        ).to_be_visible()
        await expect(views_button(page)).to_have_text("WhatsApp complaints")


async def test_a_view_opens_on_the_most_specific_tab_whose_own_filters_it_holds(
    shell: Shell,
) -> None:
    state = queue_inbox(
        views={
            ME_ID: [
                saved_view("Plain needs us", {"state": ["needs_staff"], "order": "due"}),
                saved_view(
                    "Unassigned and late",
                    {"state": ["needs_staff"], "assignee": "none", "overdue": True, "order": "due"},
                ),
                saved_view(
                    "Overdue by priority",
                    {
                        "state": ["needs_staff"],
                        "overdue": True,
                        "order": "priority",
                    },
                ),
            ]
        }
    )
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        chips = page.get_by_test_id("inbox-queue-chips")
        for name, tab_id, chip_names, order in (
            ("Unassigned and late", "unassigned", ["Overdue only"], "due"),
            ("Overdue by priority", "overdue", [], "priority"),
            ("Plain needs us", "needs_us", [], "due"),
        ):
            menu = await open_views(page)
            await menu.get_by_role("menuitemradio", name=name).click()
            await expect(tab(page, tab_id)).to_have_attribute("aria-selected", "true")
            await expect(page.get_by_test_id("inbox-queue-sort")).to_have_value(order)
            if chip_names:
                for chip in chip_names:
                    await expect(
                        chips.get_by_role("button", name=f"Remove filter: {chip}")
                    ).to_be_visible()
                await expect(chips.get_by_role("button", name=re.compile("^Remove"))).to_have_count(
                    len(chip_names)
                )
            else:
                await expect(chips).to_have_count(0)
            await expect(views_button(page)).to_have_text(name)


async def test_rename_and_delete_call_their_routes_and_say_what_happened(shell: Shell) -> None:
    morning = saved_view("Morning queue", {"state": ["needs_staff"], "order": "waiting"})
    complaints = saved_view("Complaints", {"topic": ["complaint"], "order": "activity"})
    state = queue_inbox(views={ME_ID: [morning, complaints]})
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        menu = await open_views(page)
        await menu.get_by_role("menuitem", name="Manage views…").click()
        dialog = page.get_by_role("dialog", name="Manage views")
        await expect(dialog).to_be_visible()
        await expect(dialog.get_by_role("listitem")).to_have_count(2)

        # Rename: Escape leaves the name as it was and keeps the dialog open.
        await dialog.get_by_role("button", name="Rename Morning queue").click()
        field = dialog.get_by_label("New name for Morning queue")
        await expect(field).to_be_focused()
        await expect(field).to_have_value("Morning queue")
        await page.keyboard.press("Escape")
        await expect(field).to_have_count(0)
        await expect(dialog).to_be_visible()
        await expect(dialog.get_by_role("button", name="Rename Morning queue")).to_be_focused()
        assert view_calls(state, "PUT") == []
        # Enter saves.
        await dialog.get_by_role("button", name="Rename Morning queue").click()
        await field.fill("Morning")
        await page.keyboard.press("Enter")
        await expect(dialog.get_by_text("Renamed to “Morning”.", exact=True)).to_be_visible(
            timeout=WAIT
        )
        puts = view_calls(state, "PUT")
        assert len(puts) == 1, puts
        assert puts[0]["path"] == f"inbox/views/{morning['id']}", puts
        assert puts[0]["body"] == {"name": "Morning"}, puts
        await expect(
            dialog.get_by_role("button", name="Rename Morning", exact=True)
        ).to_be_focused()

        # Delete asks first; Keep it sends nothing.
        await dialog.get_by_role("button", name="Delete Complaints").click()
        await expect(dialog.get_by_text("Delete “Complaints”?", exact=True)).to_be_visible()
        keep = dialog.get_by_role("button", name="Keep it")
        await expect(keep).to_be_focused()
        await keep.click()
        await expect(dialog.get_by_role("button", name="Delete Complaints")).to_be_focused()
        assert view_calls(state, "DELETE") == []
        await dialog.get_by_role("button", name="Delete Complaints").click()
        await dialog.get_by_role("button", name="Yes, delete").click()
        await expect(
            dialog.get_by_text("Deleted the view “Complaints”.", exact=True)
        ).to_be_visible(timeout=WAIT)
        deletes = view_calls(state, "DELETE")
        assert [call["path"] for call in deletes] == [f"inbox/views/{complaints['id']}"], deletes
        await expect(dialog.get_by_role("listitem")).to_have_count(1)
        assert [view["name"] for view in state.views[ME_ID]] == ["Morning"]
        # Escape closes the dialog and gives focus back to the Views button.
        await page.keyboard.press("Escape")
        await expect(dialog).to_be_hidden()
        await expect(views_button(page)).to_be_focused()
        menu = await open_views(page)
        assert await menu_texts(menu) == ["Morning", "Save this view…", "Manage views…"]


async def test_a_taken_name_and_the_twenty_view_limit_are_said_in_words(shell: Shell) -> None:
    views = [saved_view(f"View {n}", {"order": "activity"}, minutes=n) for n in range(1, 21)]
    state = queue_inbox(views={ME_ID: views})
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        menu = await open_views(page)
        await expect(menu.get_by_role("menuitemradio")).to_have_count(20, timeout=WAIT)
        await menu.get_by_role("menuitem", name="Save this view…").click()
        dialog = page.get_by_role("dialog", name="Save this view")
        name = dialog.get_by_label("Name", exact=True)
        await name.fill("view 3")
        await dialog.get_by_role("button", name="Save view").click()
        await expect(
            dialog.get_by_text("You already have a view with that name.", exact=True)
        ).to_be_visible(timeout=WAIT)
        await name.fill("Fresh")
        await dialog.get_by_role("button", name="Save view").click()
        await expect(
            dialog.get_by_text("You can keep at most 20 saved views. Delete one first.", exact=True)
        ).to_be_visible(timeout=WAIT)
        await expect(name).to_have_value("Fresh")
        assert len(view_calls(state, "POST")) == 2
        # Cancel gives focus back to the Views button.
        await dialog.get_by_role("button", name="Cancel").click()
        await expect(dialog).to_be_hidden()
        await expect(views_button(page)).to_be_focused()
        # A rename to a taken name is refused in words too.
        menu = await open_views(page)
        await menu.get_by_role("menuitem", name="Manage views…").click()
        manage = page.get_by_role("dialog", name="Manage views")
        await manage.get_by_role("button", name="Rename View 1", exact=True).click()
        await manage.get_by_label("New name for View 1", exact=True).fill("VIEW 2")
        await page.keyboard.press("Enter")
        await expect(
            manage.get_by_text("You already have a view with that name.", exact=True)
        ).to_be_visible(timeout=WAIT)
        await manage.get_by_role("button", name="Close").click()
        await expect(manage).to_be_hidden()
        await expect(views_button(page)).to_be_focused()


async def test_another_persons_views_are_never_shown(shell: Shell) -> None:
    views = {
        ME_ID: [saved_view("Admin private", {"order": "activity"})],
        AINA: [saved_view("Aina queue", {"state": ["needs_staff"], "order": "due"})],
    }
    state = queue_inbox(views=views, viewer=AINA)
    async with inbox_page(shell, state, role="tenant_staff") as page:
        await rows_loaded(page, 3)
        menu = await open_views(page)
        await expect(menu.get_by_role("menuitemradio", name="Aina queue")).to_be_visible(
            timeout=WAIT
        )
        await expect(page.get_by_text("Admin private")).to_have_count(0)
        await menu.get_by_role("menuitem", name="Save this view…").click()
        dialog = page.get_by_role("dialog", name="Save this view")
        await dialog.get_by_label("Name", exact=True).fill("Aina second")
        await dialog.get_by_role("button", name="Save view").click()
        await expect(dialog).to_be_hidden(timeout=WAIT)
    assert [view["name"] for view in views[AINA]] == ["Aina second", "Aina queue"]
    assert [view["name"] for view in views[ME_ID]] == ["Admin private"]

    state = queue_inbox(views=views)
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        menu = await open_views(page)
        await expect(menu.get_by_role("menuitemradio", name="Admin private")).to_be_visible(
            timeout=WAIT
        )
        await expect(menu.get_by_role("menuitemradio")).to_have_count(1)
        await expect(page.get_by_text(re.compile("Aina"))).to_have_count(0)


async def test_platform_admin_on_a_company_keeps_views_there(shell: Shell) -> None:
    state = queue_inbox(views={ME_ID: [saved_view("Company view", {"order": "activity"})]})
    async with inbox_page(shell, state, role="platform_admin", path="/inbox") as page:
        await page.get_by_label("Tenant").select_option(redesign.TENANT)
        await rows_loaded(page, 3)
        menu = await open_views(page)
        await expect(menu.get_by_role("menuitemradio", name="Company view")).to_be_visible(
            timeout=WAIT
        )
        assert all(f"client_id={redesign.TENANT}" in call["query"] for call in state.view_calls), (
            state.view_calls
        )


async def test_a_view_this_screen_cannot_show_is_listed_disabled_with_why(shell: Shell) -> None:
    state = queue_inbox(
        views={
            ME_ID: [
                saved_view("Telegram only", {"channel": "telegram", "order": "activity"}),
                saved_view("Held for review", {"needs_review": True}),
                saved_view("Assistant chats", {"state": ["bot"]}),
            ]
        }
    )
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        menu = await open_views(page)
        for name in ("Held for review", "Assistant chats"):
            item = menu.get_by_role("menuitemradio", name=name)
            await expect(item).to_have_attribute("aria-disabled", "true", timeout=WAIT)
            await expect(item).to_have_accessible_description(UNSUPPORTED)
            await expect(item).to_contain_text(UNSUPPORTED)
        ok = menu.get_by_role("menuitemradio", name="Telegram only")
        await expect(ok).not_to_have_attribute("aria-disabled", "true")
        # Choosing it does nothing: the menu stays open, nothing is asked for.
        before = len(state.list_queries)
        # (Forced: Playwright waits for an aria-disabled item to become enabled.)
        await menu.get_by_role("menuitemradio", name="Held for review").click(force=True)
        await expect(menu).to_be_visible()
        await menu.get_by_role("menuitemradio", name="Assistant chats").focus()
        await page.keyboard.press("Enter")
        await expect(menu).to_be_visible()
        await asyncio.sleep(0.5)
        assert len(state.list_queries) == before
        await expect(tab(page, "needs_us")).to_have_attribute("aria-selected", "true")
        await expect(views_button(page)).to_have_text("Views")


async def test_views_show_only_a_loading_line_until_read_and_a_failed_read_retries(
    shell: Shell,
) -> None:
    state = queue_inbox(
        views={ME_ID: [saved_view("Slow view", {"order": "activity"})]},
        views_delay=1.5,
    )
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        menu = await open_views(page)
        # While loading: one line, never the empty sentence or Save and Manage.
        items = menu.locator("[role^='menuitem']")
        await expect(items).to_have_count(1)
        await expect(items.first).to_have_text("Loading your saved views…")
        await expect(items.first).to_have_attribute("aria-disabled", "true")
        await expect(items.first).to_be_focused()
        await expect(page.get_by_text(VIEWS_EMPTY)).to_have_count(0)
        await expect(menu.get_by_role("menuitemradio", name="Slow view")).to_be_visible(
            timeout=WAIT
        )
        # Focus stays in the menu when the loading line gives way to the list.
        assert await menu.evaluate("el => el.contains(document.activeElement)")

    state = queue_inbox(
        views={ME_ID: [saved_view("Back again", {"order": "activity"})]},
        views_script={"GET": [(500, {"detail": "synthetic outage"})]},
    )
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        menu = await open_views(page)
        await expect(menu.get_by_text(VIEWS_FAILED, exact=True)).to_be_visible(timeout=WAIT)
        await expect(page.get_by_text("synthetic outage")).to_have_count(0)
        assert await menu_texts(menu) == [VIEWS_FAILED, "Try again"]
        await menu.get_by_role("menuitem", name="Try again", exact=True).click()
        # The menu stays open and the list arrives.
        await expect(menu.get_by_role("menuitemradio", name="Back again")).to_be_visible(
            timeout=WAIT
        )
        assert len(view_calls(state, "GET")) == 2

    state = queue_inbox(views_script={"GET": [(403, {"detail": "forbidden"})]})
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        menu = await open_views(page)
        await expect(
            menu.get_by_text("You do not have permission to do that.", exact=True)
        ).to_be_visible(timeout=WAIT)


async def test_the_active_views_name_drops_when_the_tab_filters_or_sort_change(
    shell: Shell,
) -> None:
    state = queue_inbox(
        views={
            ME_ID: [
                saved_view(
                    "Needs us urgent",
                    {"state": ["needs_staff"], "priority_min": "urgent", "order": "due"},
                )
            ]
        }
    )
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        button = views_button(page)

        async def choose() -> None:
            menu = await open_views(page)
            await menu.get_by_role("menuitemradio", name="Needs us urgent").click()
            await expect(button).to_have_text("Needs us urgent")

        await choose()
        # Another filter: the screen no longer is the view.
        panel = await open_filters(page)
        await (
            panel.get_by_role("group", name="Channel").get_by_role("radio", name="Telegram").check()
        )
        await page.keyboard.press("Escape")
        await expect(button).to_have_text("Views")
        menu = await open_views(page)
        await expect(menu.get_by_role("menuitemradio", name="Needs us urgent")).to_have_attribute(
            "aria-checked", "false"
        )
        await page.keyboard.press("Escape")
        await choose()
        await page.get_by_test_id("inbox-queue-sort").select_option("activity")
        await expect(button).to_have_text("Views")
        await choose()
        await tab(page, "needs_us").click()
        await expect(button).to_have_text("Views")
        await expect(button).to_have_accessible_name("Views")


async def test_the_views_menu_works_by_keyboard_and_closes_on_tab_and_outside(
    shell: Shell,
) -> None:
    state = queue_inbox(
        views={
            ME_ID: [
                saved_view("First view", {"order": "activity"}),
                saved_view("Second view", {"order": "waiting"}),
            ]
        }
    )
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        button = views_button(page)
        await button.focus()
        await page.keyboard.press("Enter")
        menu = views_menu(page)
        await expect(menu.get_by_role("menuitemradio")).to_have_count(2, timeout=WAIT)
        items = menu.locator("[role^='menuitem']")
        await expect(items).to_have_count(4)
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
        # Escape closes it and gives focus back; the Inbox's own keys stay out of it.
        await page.keyboard.press("Escape")
        await expect(menu).to_have_count(0)
        await expect(button).to_be_focused()
        await expect(button).to_have_attribute("aria-expanded", "false")
        # Tab closes it.
        await page.keyboard.press("ArrowDown")
        await expect(items.nth(0)).to_be_focused()
        await page.keyboard.press("Tab")
        await expect(menu).to_have_count(0)
        # A press outside closes it.
        await button.click()
        await expect(menu).to_be_visible()
        await page.get_by_role("tablist", name="Conversation queues").click(
            position={"x": 5, "y": 5}
        )
        await expect(menu).to_have_count(0)
        # Enter on an item chooses it; focus is back on the button.
        await button.click()
        await expect(items.nth(1)).to_have_count(1)
        await page.keyboard.press("ArrowDown")
        await page.keyboard.press("Enter")
        await expect(menu).to_have_count(0)
        await expect(button).to_have_text("Second view")
        await expect(button).to_be_focused()
        # Every dialog gives focus back to the button: Save (Escape) and Manage (Escape).
        for item, title in (
            ("Save this view…", "Save this view"),
            ("Manage views…", "Manage views"),
        ):
            menu = await open_views(page)
            await menu.get_by_role("menuitem", name=item).click()
            dialog = page.get_by_role("dialog", name=title)
            await expect(dialog).to_be_visible()
            await page.keyboard.press("Escape")
            await expect(dialog).to_be_hidden()
            await expect(button).to_be_focused()


async def test_manage_views_acts_on_one_row_while_a_change_is_pending(shell: Shell) -> None:
    alpha = saved_view("Alpha", {"order": "activity"})
    beta = saved_view("Beta", {"order": "waiting"})
    state = queue_inbox(views={ME_ID: [alpha, beta]}, views_write_delay=1.5)
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        menu = await open_views(page)
        await menu.get_by_role("menuitem", name="Manage views…").click()
        dialog = page.get_by_role("dialog", name="Manage views")
        await dialog.get_by_role("button", name="Rename Alpha", exact=True).click()
        await dialog.get_by_label("New name for Alpha", exact=True).fill("Alpha two")
        await page.keyboard.press("Enter")
        # Pending: only Alpha's own button says so and is inert; Beta's controls are inert too.
        save = dialog.get_by_role("button", name="Saving…")
        await expect(save).to_have_attribute("aria-disabled", "true")
        beta_delete = dialog.get_by_role("button", name="Delete Beta", exact=True)
        beta_rename = dialog.get_by_role("button", name="Rename Beta", exact=True)
        for control in (beta_delete, beta_rename):
            await expect(control).to_have_attribute("aria-disabled", "true")
        await beta_delete.click(force=True)
        await expect(dialog.get_by_text("Delete “Beta”?", exact=True)).to_have_count(0)
        await expect(dialog.get_by_role("button", name="Deleting…")).to_have_count(0)
        await beta_rename.click(force=True)
        await expect(dialog.get_by_label("New name for Beta", exact=True)).to_have_count(0)
        await expect(dialog.get_by_label("New name for Alpha", exact=True)).to_be_visible()
        # Alpha's answer lands on Alpha; Beta's controls work again afterwards.
        await expect(dialog.get_by_text("Renamed to “Alpha two”.", exact=True)).to_be_visible(
            timeout=WAIT
        )
        assert [call["body"] for call in view_calls(state, "PUT")] == [{"name": "Alpha two"}]
        assert view_calls(state, "DELETE") == []
        await expect(beta_rename).not_to_have_attribute("aria-disabled", "true")
        await beta_rename.click()
        beta_field = dialog.get_by_label("New name for Beta", exact=True)
        await expect(beta_field).to_be_focused()
        await page.keyboard.press("Escape")

        # A pending delete holds the other rows the same way, and only its own button says so.
        await dialog.get_by_role("button", name="Delete Beta", exact=True).click()
        await dialog.get_by_role("button", name="Yes, delete").click()
        deleting = dialog.get_by_role("button", name="Deleting…")
        await expect(deleting).to_have_attribute("aria-disabled", "true")
        alpha_rename = dialog.get_by_role("button", name="Rename Alpha two", exact=True)
        await expect(alpha_rename).to_have_attribute("aria-disabled", "true")
        await alpha_rename.click(force=True)
        await expect(dialog.get_by_label("New name for Alpha two", exact=True)).to_have_count(0)
        await expect(dialog.get_by_text("Deleted the view “Beta”.", exact=True)).to_be_visible(
            timeout=WAIT
        )
        assert len(view_calls(state, "DELETE")) == 1
        await expect(alpha_rename).to_be_focused()


async def test_a_failed_rename_puts_its_error_and_focus_on_its_own_row(shell: Shell) -> None:
    alpha = saved_view("Alpha", {"order": "activity"})
    beta = saved_view("Beta", {"order": "waiting"})
    state = queue_inbox(
        views={ME_ID: [alpha, beta]},
        views_write_delay=1.5,
        views_script={"PUT": [(409, {"detail": "you already have a saved view with this name"})]},
    )
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        menu = await open_views(page)
        await menu.get_by_role("menuitem", name="Manage views…").click()
        dialog = page.get_by_role("dialog", name="Manage views")
        await dialog.get_by_role("button", name="Rename Alpha", exact=True).click()
        alpha_field = dialog.get_by_label("New name for Alpha", exact=True)
        await alpha_field.fill("beta")
        await page.keyboard.press("Enter")
        await expect(dialog.get_by_role("button", name="Saving…")).to_be_visible()
        # A press on another row while it is pending changes nothing.
        await dialog.get_by_role("button", name="Rename Beta", exact=True).click(force=True)
        error = dialog.get_by_text("You already have a view with that name.", exact=True)
        await expect(error).to_be_visible(timeout=WAIT)
        # (`has` is looked up inside each item, so it is rooted at the page, not the dialog.)
        alpha_row = dialog.get_by_role("listitem").filter(
            has=page.get_by_label("New name for Alpha", exact=True)
        )
        await expect(
            alpha_row.get_by_text("You already have a view with that name.")
        ).to_be_visible()
        await expect(alpha_field).to_be_focused()
        await expect(alpha_field).to_have_value("beta")
        await expect(alpha_field).to_have_accessible_description(
            "You already have a view with that name."
        )
        await expect(dialog.get_by_label("New name for Beta", exact=True)).to_have_count(0)


@pytest.mark.parametrize("width", (320, 390))
async def test_the_views_menu_and_dialogs_fit_small_screens(shell: Shell, width: int) -> None:
    state = queue_inbox(
        views={
            ME_ID: [
                saved_view(LONG_VIEW, {"order": "activity"}),
                saved_view("Odd one", {"needs_review": True}),
            ]
        }
    )
    async with inbox_page(shell, state, width=width) as page:
        await rows_loaded(page, 3)

        async def fits(what: str, locator: Any) -> None:
            measured = await page_width(page)
            assert measured["scroll"] <= measured["inner"], (what, measured)
            box = await locator.bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= width, (what, box)

        button = views_button(page)
        box = await button.bounding_box()
        assert box and box["height"] >= 40, box
        menu = await open_views(page)
        await expect(menu.get_by_role("menuitemradio", name=LONG_VIEW)).to_be_visible(timeout=WAIT)
        await fits("menu", menu)
        for item in await menu.locator("[role^='menuitem']").all():
            item_box = await item.bounding_box()
            assert item_box and item_box["height"] >= 40, item_box
        # A chosen long name stays inside the bar.
        await menu.get_by_role("menuitemradio", name=LONG_VIEW).click()
        await expect(button).to_have_accessible_name(f"Views, showing {LONG_VIEW}")
        await fits("button", button)
        # The tab strip is still the only sticky part of the bar.
        sticky = await page.get_by_test_id("inbox-queue-bar").evaluate(
            """bar => [bar, ...bar.querySelectorAll('*')]
                .filter(el => getComputedStyle(el).position === 'sticky')
                .map(el => el.getAttribute('aria-label'))"""
        )
        assert sticky == ["Conversation queues"], sticky
        menu = await open_views(page)
        await menu.get_by_role("menuitem", name="Save this view…").click()
        dialog = page.get_by_role("dialog", name="Save this view")
        await dialog.get_by_label("Name", exact=True).fill(LONG_VIEW)
        await dialog.get_by_role("button", name="Save view").click()
        await expect(
            dialog.get_by_text("You already have a view with that name.", exact=True)
        ).to_be_visible(timeout=WAIT)
        await fits("save dialog", dialog)
        await page.keyboard.press("Escape")
        menu = await open_views(page)
        await menu.get_by_role("menuitem", name="Manage views…").click()
        manage = page.get_by_role("dialog", name="Manage views")
        await expect(manage.get_by_role("listitem")).to_have_count(2)
        await fits("manage dialog", manage)
        await manage.get_by_role("button", name=f"Rename {LONG_VIEW}").click()
        await fits("manage rename", manage)
        await page.keyboard.press("Escape")
        await manage.get_by_role("button", name=f"Delete {LONG_VIEW}").click()
        await expect(manage.get_by_role("button", name="Yes, delete")).to_be_visible()
        await fits("manage delete", manage)


# ---- response times (Task 8) ----

SETTINGS_DEFAULTS = {
    "target_urgent_min": 15,
    "target_high_min": 60,
    "target_normal_min": 240,
    "target_low_min": 1440,
    "repeat_minutes": 60,
    "max_repeats": 3,
    "follow_up_hours": 48,
    "stale_claim_hours": 8,
    "handback_prompt_hours": 24,
}
TIMES_LOADING = "Loading the response times…"
TIMES_FAILED = "Could not load the response times. Try again."
TIMES_SAVED = "Response times saved."
TIMES_STAFF = "Only administrators can change these."
TIMES_LATER = "Used by reminders, which come later."
TIMES_CONFLICT = (
    "Someone else changed these. The latest values are shown; check them and save again."
)
TARGET_WORDS = "Enter a whole number of minutes, from 5 to 10080."
TARGET_LABELS = ("Urgent", "High", "Normal", "Low")


def times_button(page: Page) -> Any:
    return page.get_by_test_id("inbox-response-times")


def times_panel(page: Page) -> Any:
    return page.get_by_role("dialog", name="Response times")


def settings_calls(state: Inbox, method: str) -> list[dict[str, Any]]:
    return [call for call in state.settings_calls if call["method"] == method]


async def open_times(page: Page) -> Any:
    await times_button(page).click()
    panel = times_panel(page)
    await expect(panel).to_be_visible()
    return panel


async def times_loaded(panel: Any) -> None:
    await expect(panel.get_by_label("Normal", exact=True)).to_be_visible(timeout=WAIT)


def test_the_panels_ranges_are_the_apis() -> None:
    """The client's SETTINGS_RANGES (lib/inbox-api.ts) equal the API model's bounds."""
    source = (
        Path(__file__).resolve().parents[1] / "CSA_WebConsole/csa-console/web/lib/inbox-api.ts"
    ).read_text()
    block = source.split("export const SETTINGS_RANGES", 1)[1].split("};", 1)[0]
    client = {
        key: (int(low), int(high))
        for key, low, high in re.findall(r"(\w+): \[(\d+), (\d+)\]", block)
    }
    api = {}
    for key, info in InboxSettingsIn.model_fields.items():
        if key == "version":
            continue
        bounds = {type(item).__name__: item for item in info.metadata}
        api[key] = (bounds["Ge"].ge, bounds["Le"].le)
    assert client == api


async def test_an_admin_changes_a_target_and_the_put_carries_every_field(shell: Shell) -> None:
    state = queue_inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        # Nothing is read until the panel first opens.
        assert state.settings_calls == []
        button = times_button(page)
        await expect(button).to_have_accessible_name("Response times")
        panel = await open_times(page)
        await times_loaded(panel)
        for label, key in zip(TARGET_LABELS, ("urgent", "high", "normal", "low"), strict=True):
            await expect(panel.get_by_label(label, exact=True)).to_have_value(
                str(SETTINGS_DEFAULTS[f"target_{key}_min"])
            )
        normal = panel.get_by_label("Normal", exact=True)
        await expect(normal).to_have_accessible_description("4 h")
        await expect(panel.get_by_text(TIMES_LATER, exact=True)).to_be_visible()
        await expect(
            panel.get_by_text("A change applies only to conversations that enter the queue")
        ).to_be_visible()
        save = panel.get_by_role("button", name="Save", exact=True)
        # Unchanged: Save does nothing.
        await expect(save).to_have_attribute("aria-disabled", "true")
        status = panel.get_by_test_id("inbox-response-times-status")
        await expect(status).to_have_text("")
        await expect(status).to_have_attribute("role", "status")
        await normal.fill("90")
        await expect(normal).to_have_accessible_description("1 h 30 min")
        await normal.fill("30")
        await expect(normal).to_have_accessible_description("30 min")
        await expect(save).not_to_have_attribute("aria-disabled", "true")
        await save.click()
        await expect(status).to_have_text(TIMES_SAVED, timeout=WAIT)
        puts = settings_calls(state, "PUT")
        assert [call["body"] for call in puts] == [
            {**SETTINGS_DEFAULTS, "target_normal_min": 30, "version": 0}
        ]
        await expect(save).to_have_attribute("aria-disabled", "true")
        # The next save is made on the version the first one returned.
        await panel.get_by_label("Urgent", exact=True).fill("10")
        await save.click()
        await expect(status).to_have_text(TIMES_SAVED, timeout=WAIT)
        await eventually(lambda: len(settings_calls(state, "PUT")) == 2)
        assert settings_calls(state, "PUT")[1]["body"] == {
            **SETTINGS_DEFAULTS,
            "target_normal_min": 30,
            "target_urgent_min": 10,
            "version": 1,
        }
        assert len(settings_calls(state, "GET")) == 1

    # A platform administrator edits the chosen company's settings.
    state = queue_inbox()
    async with inbox_page(shell, state, role="platform_admin", path="/inbox") as page:
        await page.get_by_label("Tenant").select_option(redesign.TENANT)
        await rows_loaded(page, 3)
        panel = await open_times(page)
        await times_loaded(panel)
        await panel.get_by_label("Low", exact=True).fill("720")
        await panel.get_by_role("button", name="Save", exact=True).click()
        await expect(panel.get_by_test_id("inbox-response-times-status")).to_have_text(
            TIMES_SAVED, timeout=WAIT
        )
        assert [call["method"] for call in state.settings_calls] == ["GET", "PUT"]
        assert all(f"client_id={redesign.TENANT}" in call["query"] for call in state.settings_calls)
        assert state.settings_calls[1]["body"]["target_low_min"] == 720


async def test_invalid_times_are_refused_in_words_before_anything_is_sent(shell: Shell) -> None:
    state = queue_inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        panel = await open_times(page)
        await times_loaded(panel)
        normal = panel.get_by_label("Normal", exact=True)
        save = panel.get_by_role("button", name="Save", exact=True)
        for bad in ("4", "10081", "", "abc", "1.5"):
            await normal.fill(bad)
            await expect(normal).to_have_accessible_description(TARGET_WORDS)
            await expect(normal).to_have_attribute("aria-invalid", "true")
            await expect(save).to_have_attribute("aria-disabled", "true")
            await save.click(force=True)
            await normal.press("Enter")
        # A press on Save while invalid moves focus to the field that needs fixing.
        await panel.get_by_label("Urgent", exact=True).focus()
        await save.click(force=True)
        await expect(normal).to_be_focused()
        # The valid value then goes out once: nothing was sent for the invalid ones.
        await normal.fill("5")
        await expect(normal).not_to_have_attribute("aria-invalid", "true")
        await save.click()
        await expect(panel.get_by_test_id("inbox-response-times-status")).to_have_text(
            TIMES_SAVED, timeout=WAIT
        )
        assert [call["body"]["target_normal_min"] for call in settings_calls(state, "PUT")] == [5]


async def test_staff_see_the_times_read_only_without_save(shell: Shell) -> None:
    state = queue_inbox()
    state.settings = {**SETTINGS_DEFAULTS, "target_normal_min": 90, "stale_claim_hours": 0}
    state.settings["version"] = 2
    async with inbox_page(shell, state, role="tenant_staff") as page:
        await rows_loaded(page, 3)
        panel = await open_times(page)
        await expect(panel.get_by_text(TIMES_STAFF, exact=True)).to_be_visible(timeout=WAIT)
        values = panel.get_by_test_id("inbox-response-times-values")
        await expect(values).to_contain_text("Normal")
        await expect(values).to_contain_text("90 minutes (1 h 30 min)")
        await expect(panel.get_by_text(TIMES_LATER, exact=True)).to_be_visible()
        await expect(panel.get_by_test_id("inbox-response-times-reminders")).to_contain_text("Off")
        await expect(panel.get_by_role("textbox")).to_have_count(0)
        await expect(panel.get_by_role("button", name="Save", exact=True)).to_have_count(0)
        assert settings_calls(state, "PUT") == []


async def test_a_conflict_reloads_the_latest_values_and_says_so(shell: Shell) -> None:
    state = queue_inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        panel = await open_times(page)
        await times_loaded(panel)
        # Someone else saves after this panel read the settings.
        state.settings = {**SETTINGS_DEFAULTS, "target_normal_min": 120, "version": 4}
        normal = panel.get_by_label("Normal", exact=True)
        await normal.fill("30")
        await panel.get_by_role("button", name="Save", exact=True).click()
        await expect(panel.get_by_text(TIMES_CONFLICT, exact=True)).to_be_visible(timeout=WAIT)
        await expect(normal).to_have_value("120")
        await expect(page.get_by_text("version_conflict")).to_have_count(0)
        assert [call["method"] for call in state.settings_calls] == ["GET", "PUT", "GET"]
        # Saving again is made on the version read anew.
        await normal.fill("30")
        await panel.get_by_role("button", name="Save", exact=True).click()
        await expect(panel.get_by_test_id("inbox-response-times-status")).to_have_text(
            TIMES_SAVED, timeout=WAIT
        )
        assert settings_calls(state, "PUT")[-1]["body"]["version"] == 4
        await expect(panel.get_by_text(TIMES_CONFLICT, exact=True)).to_have_count(0)


async def test_other_save_failures_are_plain_sentences(shell: Shell) -> None:
    state = queue_inbox(
        settings_script={
            "PUT": [(403, {"detail": "forbidden"}), (500, {"detail": "synthetic outage"})]
        }
    )
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        panel = await open_times(page)
        await times_loaded(panel)
        await panel.get_by_label("High", exact=True).fill("45")
        save = panel.get_by_role("button", name="Save", exact=True)
        await save.click()
        await expect(
            panel.get_by_text("You do not have permission to do that.", exact=True)
        ).to_be_visible(timeout=WAIT)
        await save.click()
        await expect(
            panel.get_by_text("Could not save the response times. Try again.", exact=True)
        ).to_be_visible(timeout=WAIT)
        await expect(page.get_by_text("synthetic outage")).to_have_count(0)
        await expect(panel.get_by_label("High", exact=True)).to_have_value("45")


async def test_one_save_at_a_time_and_a_late_answer_stays_out_of_a_closed_panel(
    shell: Shell,
) -> None:
    gate = asyncio.Event()
    state = queue_inbox(settings_gates={"PUT": gate})
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        panel = await open_times(page)
        await times_loaded(panel)
        normal = panel.get_by_label("Normal", exact=True)
        await normal.fill("30")
        await panel.get_by_role("button", name="Save", exact=True).click()
        saving = panel.get_by_role("button", name="Saving…", exact=True)
        await expect(saving).to_have_attribute("aria-disabled", "true")
        await eventually(lambda: len(settings_calls(state, "PUT")) == 1)
        # Every field is inert while the save is pending, and Save cannot go out twice.
        for label in TARGET_LABELS:
            await expect(panel.get_by_label(label, exact=True)).to_have_attribute(
                "aria-disabled", "true"
            )
        await normal.press("End")
        await page.keyboard.type("9")
        await expect(normal).to_have_value("30")
        await saving.click(force=True)
        await normal.press("Enter")
        # The panel closes while the answer is still out; the answer then lands.
        await page.keyboard.press("Escape")
        await expect(panel).to_be_hidden()
        gate.set()
        await eventually(lambda: state.settings["version"] == 1)
        assert len(settings_calls(state, "PUT")) == 1
        panel = await open_times(page)
        # The saved values show, without a confirmation for a save made in the closed panel.
        await expect(panel.get_by_label("Normal", exact=True)).to_have_value("30", timeout=WAIT)
        await expect(panel.get_by_test_id("inbox-response-times-status")).to_have_text("")
        await expect(panel.get_by_role("button", name="Save", exact=True)).to_have_attribute(
            "aria-disabled", "true"
        )
        assert len(settings_calls(state, "PUT")) == 1


async def test_escape_closes_the_times_panel_and_returns_focus(shell: Shell) -> None:
    state = queue_inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        button = times_button(page)
        await expect(button).to_have_attribute("aria-haspopup", "dialog")
        await expect(button).to_have_attribute("aria-expanded", "false")
        panel = await open_times(page)
        await expect(button).to_have_attribute("aria-expanded", "true")
        assert await panel.evaluate("el => el.contains(document.activeElement)")
        await times_loaded(panel)
        await panel.get_by_label("Normal", exact=True).focus()
        await page.keyboard.press("Escape")
        await expect(panel).to_be_hidden()
        await expect(button).to_be_focused()
        # The Close button does the same.
        panel = await open_times(page)
        await panel.get_by_role("button", name="Close", exact=True).click()
        await expect(panel).to_be_hidden()
        await expect(button).to_be_focused()
        # The settings were read once, when the panel first opened.
        assert len(settings_calls(state, "GET")) == 1


async def test_a_failed_read_says_so_and_try_again_reads_again(shell: Shell) -> None:
    gate = asyncio.Event()
    state = queue_inbox(
        settings_script={"GET": [(500, {"detail": "synthetic outage"})]},
        settings_gates={"GET": gate},
    )
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        panel = await open_times(page)
        # While the read is out: the loading line only, never a field.
        await expect(panel.get_by_text(TIMES_LOADING, exact=True)).to_be_visible()
        await expect(panel.get_by_role("textbox")).to_have_count(0)
        await expect(panel.get_by_role("button", name="Save", exact=True)).to_have_count(0)
        gate.set()
        await expect(panel.get_by_text(TIMES_FAILED, exact=True)).to_be_visible(timeout=WAIT)
        await expect(page.get_by_text("synthetic outage")).to_have_count(0)
        await expect(panel.get_by_role("textbox")).to_have_count(0)
        await panel.get_by_role("button", name="Try again", exact=True).click()
        await times_loaded(panel)
        assert len(settings_calls(state, "GET")) == 2

    state = queue_inbox(settings_script={"GET": [(403, {"detail": "forbidden"})]})
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 3)
        panel = await open_times(page)
        await expect(
            panel.get_by_text("You do not have permission to do that.", exact=True)
        ).to_be_visible(timeout=WAIT)
        await expect(panel.get_by_role("textbox")).to_have_count(0)


async def test_the_times_panel_fits_small_screens(shell: Shell) -> None:
    state = queue_inbox()
    async with inbox_page(shell, state, width=390) as page:
        await rows_loaded(page, 3)
        for width in (390, 320, 768):
            await page.set_viewport_size({"width": width, "height": 844})
            measured = await page_width(page)
            assert measured["scroll"] <= measured["inner"], (width, measured)
            button = times_button(page)
            box = await button.bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= width, (width, box)
            assert box["height"] >= 40, box
            panel = await open_times(page)
            await times_loaded(panel)
            await panel.get_by_label("Normal", exact=True).fill("4")
            await expect(panel.get_by_text(TARGET_WORDS, exact=True)).to_be_visible()
            panel_box = await panel.bounding_box()
            assert panel_box and panel_box["x"] >= 0, (width, panel_box)
            assert panel_box["x"] + panel_box["width"] <= width, (width, panel_box)
            overflow = await panel.evaluate(
                """el => [...el.querySelectorAll('*')].filter(child => {
                    const box = child.getBoundingClientRect();
                    const out = box.left < -0.5 || box.right > window.innerWidth + 0.5;
                    return box.width > 0 && out;
                }).map(child => child.outerHTML.slice(0, 80))"""
            )
            assert overflow == [], (width, overflow)
            await page.keyboard.press("Escape")
            await expect(panel).to_be_hidden()
            await expect(button).to_be_focused()


# ---- Task 9: shortcuts C, E, S, / and ?, tab empty states, the count announcement ----


@dataclass
class GatedInbox(Inbox):
    """The stub with doors the test opens: queue actions and the company-wide probe can be held
    shut until the test sets the event, and the probe (the All list asked for one row) can fail."""

    action_gate: asyncio.Event | None = None
    probe_gate: asyncio.Event | None = None
    fail_probe: bool = False
    # What reached the stub, before any gate: queue actions by name, and probes.
    arrived: list[str] = field(default_factory=list)
    probes_arrived: int = 0
    probe_answers: int = 0

    async def route(self, request_route: Any) -> None:
        url = request_route.request.url
        probe = "/console/v1/inbox/conversations?" in url and "limit=1" in url
        if "/queue/" in url:
            self.arrived.append(url.partition("?")[0].rsplit("/", 1)[-1])
            if self.action_gate is not None:
                await self.action_gate.wait()
        if probe:
            self.probes_arrived += 1
            if self.probe_gate is not None:
                await self.probe_gate.wait()
        if probe and self.fail_probe:
            self.list_queries.append(url.partition("?")[2])
            self.probe_answers += 1
            await request_route.fulfill(
                status=500,
                content_type="application/json",
                body='{"detail": "synthetic outage"}',
            )
            return
        await super().route(request_route)
        if probe:
            self.probe_answers += 1


def gated_action_inbox(**changes: Any) -> GatedInbox:
    state = GatedInbox(items=queue_rows(), filter_state=True, **changes)
    state.items.append(other_row())
    return state


def help_dialog(page: Page) -> Any:
    return page.get_by_role("dialog", name="Keyboard shortcuts")


def actions_sent(state: GatedInbox) -> list[str]:
    """The queue actions the page sent, in order, counted when they reach the stub (gate or not)."""
    return list(state.arrived)


async def test_c_takes_the_open_item_and_waits_while_its_request_is_out(shell: Shell) -> None:
    state = gated_action_inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 4)
        await open_item(page, UNNAMED)
        await expect(primary(page)).to_have_text("Take it")
        state.action_gate = asyncio.Event()
        await page.keyboard.press("c")
        await eventually(lambda: actions_sent(state) == ["claim"])
        # The same button a click uses: it waits, disabled, while the request is out.
        await expect(primary(page)).to_be_disabled()
        # One action at a time: C, E and S do nothing until the answer is in.
        for key in ("c", "e", "s"):
            await page.keyboard.press(key)
        await expect(page.get_by_role("menu")).to_have_count(0)
        state.action_gate.set()
        await expect(page.get_by_text("You took this conversation.", exact=True)).to_be_visible(
            timeout=WAIT
        )
        await expect(primary(page)).to_have_text("Resolve", timeout=WAIT)
        # Now Resolve is the primary action, so E sends it: the presses made while the claim was
        # out sent nothing (the list below would hold a second claim or a resolve before this).
        await page.keyboard.press("e")
        await eventually(lambda: actions_sent(state) == ["claim", "resolve"])
        assert state.queue_calls[0]["id"] == UNNAMED, state.queue_calls
        assert state.queue_calls[1]["id"] == UNNAMED, state.queue_calls


async def test_c_never_takes_a_colleagues_claim_but_the_button_still_does(
    shell: Shell,
) -> None:
    state = gated_action_inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 4)
        # Held by Aina: an administrator is offered "Take it from Aina", a deliberate click only.
        await open_item(page, OTHER_ROW)
        await expect(primary(page)).to_have_text("Take it from Aina")
        await row(page, OTHER_ROW).focus()
        await page.keyboard.press("c")
        # Keys are handled in order: once S has opened the Snooze menu, C has been and gone.
        await page.keyboard.press("s")
        menu = page.get_by_role("menu", name="Snooze")
        await expect(menu).to_be_visible()
        assert actions_sent(state) == [], state.arrived
        await page.keyboard.press("Escape")
        await expect(menu).to_have_count(0)
        await primary(page).click()
        await expect(page.get_by_text("You took this conversation.", exact=True)).to_be_visible(
            timeout=WAIT
        )
        assert actions_sent(state) == ["claim"], state.arrived
        assert state.queue_calls[0]["id"] == OTHER_ROW, state.queue_calls


async def test_e_resolves_with_the_buttons_refusal_sentence_and_only_when_offered(
    shell: Shell,
) -> None:
    state = gated_action_inbox()
    state.queue_script = {"resolve": [(409, {"detail": {"code": "held_reply_pending"}})]}
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 4)
        # Take it is the primary action here, not Resolve: E leaves it alone and C takes it.
        await open_item(page, WAITING_ROW)
        await page.keyboard.press("e")
        await page.keyboard.press("c")
        await eventually(lambda: actions_sent(state) == ["claim"])
        await expect(primary(page)).to_have_text("Resolve", timeout=WAIT)
        # Refused: the same plain sentence the button gets.
        await page.keyboard.press("e")
        await refused(page, state, "A held reply needs a decision first.")
        await page.keyboard.press("e")
        await expect(
            page.get_by_text("Resolved. The assistant has this conversation again.", exact=True)
        ).to_be_visible(timeout=WAIT)
        assert actions_sent(state) == ["claim", "resolve", "resolve"], state.queue_calls
        assert row_of(state, WAITING_ROW)["queue_status"] == "resolved"


async def test_s_opens_the_snooze_menu_of_the_open_conversation(shell: Shell) -> None:
    state = gated_action_inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 4)
        # Nothing open: S has no menu to open.
        await page.keyboard.press("s")
        await expect(page.get_by_role("menu")).to_have_count(0)
        await open_item(page, NAMED)
        await page.keyboard.press("s")
        menu = page.get_by_role("menu", name="Snooze")
        await expect(menu).to_be_visible()
        await expect(menu.get_by_role("menuitem").nth(0)).to_be_focused()
        # Inside the menu the letters are the menu's: S again does not reopen or close it.
        await page.keyboard.press("s")
        await expect(menu).to_be_visible()
        await page.keyboard.press("Escape")
        await expect(menu).to_have_count(0)
        await expect(page.get_by_test_id("inbox-snooze-menu")).to_be_focused()
        assert state.queue_calls == []


async def test_slash_goes_to_search_and_typing_there_fires_nothing(shell: Shell) -> None:
    state = gated_action_inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 4)
        await open_item(page, UNNAMED)
        search = page.get_by_test_id("inbox-search")
        await page.keyboard.press("/")
        await expect(search).to_be_focused()
        # The slash itself is not typed into the box.
        await expect(search).to_have_value("")
        # In the box every key is just text: no claim, no menu, no help, no move.
        await page.keyboard.type("cesjkx/?")
        await expect(search).to_have_value("cesjkx/?")
        await expect(help_dialog(page)).to_have_count(0)
        await expect(page.get_by_role("menu")).to_have_count(0)
        await expect(row(page, UNNAMED)).to_have_attribute("aria-current", "true")
        await search.fill("")
        # Back on the row, C works: the one claim is the first action sent.
        await row(page, UNNAMED).focus()
        await page.keyboard.press("c")
        await eventually(lambda: actions_sent(state) == ["claim"])


async def test_no_shortcut_fires_while_typing_a_reply(shell: Shell) -> None:
    state = gated_action_inbox()
    # A person holds this chat, so the reply box is on screen.
    row_of(state, UNNAMED).update(human_muted=True, owned_by="human")
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 4)
        await open_item(page, UNNAMED)
        box = page.get_by_test_id("inbox-composer").locator("textarea")
        await box.click()
        await page.keyboard.type("c e s / ? j k x")
        await expect(box).to_have_value("c e s / ? j k x")
        await expect(help_dialog(page)).to_have_count(0)
        await expect(page.get_by_role("menu")).to_have_count(0)
        await expect(page.get_by_test_id("inbox-search")).not_to_be_focused()
        await expect(row(page, UNNAMED)).to_have_attribute("aria-current", "true")
        await expect(page.get_by_test_id("inbox-bulk-bar")).to_have_count(0)
        # Out of the box, C takes the item: the first and only action sent.
        await row(page, UNNAMED).focus()
        await page.keyboard.press("c")
        await eventually(lambda: actions_sent(state) == ["claim"])


async def test_question_mark_opens_the_help_and_escape_returns_focus(shell: Shell) -> None:
    state = gated_action_inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 4)
        await open_item(page, NAMED)
        await row(page, NAMED).focus()
        await page.keyboard.press("Shift+Slash")
        dialog = help_dialog(page)
        await expect(dialog).to_be_visible()
        terms = [text.strip() for text in await dialog.locator("dt kbd").all_inner_texts()]
        assert terms == ["J", "K", "X", "C", "E", "S", "/", "?", "Escape"], terms
        await expect(dialog).to_contain_text("Take the open conversation, when Take it is offered.")
        await expect(dialog).to_contain_text("Open the Snooze menu of the open conversation.")
        # Inside the help, the keys are inert: J does not move the list behind it.
        await page.keyboard.press("j")
        await page.keyboard.press("Escape")
        await expect(dialog).to_have_count(0)
        await expect(row(page, NAMED)).to_be_focused()
        await expect(row(page, NAMED)).to_have_attribute("aria-current", "true")
        # The visible button opens the same help; Close returns focus to it.
        button = page.get_by_role("button", name="Keyboard shortcuts")
        await button.click()
        await expect(dialog).to_be_visible()
        await dialog.get_by_role("button", name="Close").click()
        await expect(dialog).to_have_count(0)
        await expect(button).to_be_focused()
        assert state.queue_calls == []


async def test_j_and_k_wait_while_the_filters_popover_is_open(shell: Shell) -> None:
    state = gated_action_inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 4)
        # The middle row, so J and K each have somewhere to go.
        await open_item(page, UNNAMED)
        current = row(page, UNNAMED)
        panel = await open_filters(page)
        # Focus on a button inside the popover: J and K belong to it.
        await panel.get_by_role("button", name="Close").focus()
        await page.keyboard.press("j")
        await expect(current).to_have_attribute("aria-current", "true")
        await page.keyboard.press("k")
        await expect(current).to_have_attribute("aria-current", "true")
        await expect(panel).to_be_visible()
        # A click on the popover's text leaves focus on the page: still open, still inert.
        await panel.get_by_role("heading", name="Filters").click()
        await expect(panel).to_be_visible()
        await page.keyboard.press("j")
        await expect(current).to_have_attribute("aria-current", "true")
        await page.keyboard.press("c")
        await expect(primary(page)).to_have_text("Take it")
        # Closed, J moves again: one row on from where it was, so none of the presses above moved
        # it (they would have left it on Chong Wei, and this J would go on to Hana Yusof).
        await panel.get_by_role("button", name="Close").click()
        await expect(panel).to_have_count(0)
        await page.keyboard.press("j")
        await expect(row(page, WAITING_ROW)).to_have_attribute("aria-current", "true")
        assert state.queue_calls == []


EMPTY_TABS = {
    "needs_us": "Nothing needs your team right now",
    "mine": "Nothing is assigned to you",
    "unassigned": "Nothing is waiting for someone to take it",
    "overdue": "Nothing is overdue",
    "waiting": "No one is waiting on a customer",
    "snoozed": "Nothing is snoozed",
    "resolved": "No resolved conversations yet",
}


def bot_only(**changes: Any) -> GatedInbox:
    """One conversation, the assistant's: every work tab is empty and every count is zero."""
    rows = [item for item in queue_rows() if item["id"] == BOT_ROW]
    return GatedInbox(items=rows, filter_state=True, **changes)


async def test_each_tab_says_its_own_empty_sentence(shell: Shell) -> None:
    state = bot_only()
    async with inbox_page(shell, state) as page:
        empty = page.get_by_test_id("inbox-empty-needs_us")
        await expect(empty).to_contain_text(EMPTY_TABS["needs_us"], timeout=WAIT)
        # The assistant's resting avatar beside the sentence, 48 px, in Needs us only.
        avatar = empty.locator("[data-agent='support']")
        await expect(avatar).to_have_count(1)
        await expect(avatar).to_have_attribute("data-state", "rest")
        box = await avatar.bounding_box()
        assert box is not None and round(box["width"]) == 48, box
        # Said once: inside the list's one polite live region, with no live region of its own.
        live = await empty.evaluate(
            """el => ({
                inside: el.parentElement.closest('[aria-live], [role=status]') !== null,
                own: el.matches('[aria-live], [role=status], [role=alert]')
                    || el.querySelector('[aria-live], [role=status], [role=alert]') !== null,
            })"""
        )
        assert live == {"inside": True, "own": False}, live
        # The company has a conversation (the probe found it): never the first-run state.
        await eventually(lambda: state.probe_answers >= 1)
        await expect(page.get_by_test_id("inbox-list-state-empty")).to_have_count(0)
        await expect(page.get_by_test_id("inbox-connect-whatsapp")).to_have_count(0)
        for identifier, sentence in EMPTY_TABS.items():
            await tab(page, identifier).click()
            shown = page.get_by_test_id(f"inbox-empty-{identifier}")
            await expect(shown).to_contain_text(sentence, timeout=WAIT)
            if identifier != "needs_us":
                await expect(shown.locator("[data-agent]")).to_have_count(0)
                await expect(
                    page.get_by_role("button", name="See all conversations")
                ).to_have_count(0)
        await expect(page.get_by_test_id("inbox-list-state-empty")).to_have_count(0)
        # See all conversations switches to All, where the assistant's chat is, and focus follows.
        await tab(page, "needs_us").click()
        await page.get_by_role("button", name="See all conversations").click()
        await expect(tab(page, "all")).to_have_attribute("aria-selected", "true")
        await expect(tab(page, "all")).to_be_focused()
        await rows_loaded(page, 1)
        # A filtered tab with nothing in it still says what to clear, not the tab's sentence.
        await tab(page, "needs_us").click()
        await page.get_by_test_id("inbox-search").fill("zzzz")
        await expect(page.get_by_test_id("inbox-no-match")).to_be_visible(timeout=WAIT)
        await expect(page.get_by_test_id("inbox-empty-needs_us")).to_have_count(0)


async def test_first_run_copy_only_when_the_company_has_no_conversations(shell: Shell) -> None:
    state = GatedInbox(items=[], filter_state=True, probe_gate=asyncio.Event())
    async with inbox_page(shell, state) as page:
        # While the probe is out, the tab's own sentence; never the first-run state.
        await expect(page.get_by_test_id("inbox-empty-needs_us")).to_be_visible(timeout=WAIT)
        await eventually(lambda: state.probes_arrived >= 1)
        await expect(page.get_by_test_id("inbox-list-state-empty")).to_have_count(0)
        state.probe_gate.set()
        first_run = page.get_by_test_id("inbox-list-state-empty")
        await expect(first_run).to_be_visible(timeout=WAIT)
        await expect(first_run.get_by_role("heading", name="No conversations yet")).to_be_visible()
        await expect(page.get_by_test_id("inbox-connect-whatsapp")).to_be_visible()
        await expect(page.get_by_test_id("inbox-empty-needs_us")).to_have_count(0)
        # The All tab is the probe's own list: empty there is the first-run state at once.
        await tab(page, "all").click()
        await expect(first_run).to_be_visible(timeout=WAIT)
    # A failed probe proves nothing: the tab's own sentence stays.
    state = GatedInbox(items=[], filter_state=True, fail_probe=True)
    async with inbox_page(shell, state) as page:
        await expect(page.get_by_test_id("inbox-empty-needs_us")).to_be_visible(timeout=WAIT)
        await eventually(lambda: state.probe_answers >= 2, 20)
        await expect(page.get_by_test_id("inbox-empty-needs_us")).to_be_visible()
        await expect(page.get_by_test_id("inbox-list-state-empty")).to_have_count(0)


@pytest.mark.parametrize("width", (320, 390, 768))
async def test_empty_states_and_the_help_fit_small_screens(shell: Shell, width: int) -> None:
    state = bot_only()
    async with inbox_page(shell, state, width=width) as page:
        await expect(page.get_by_test_id("inbox-empty-needs_us")).to_be_visible(timeout=WAIT)
        measured = await page_width(page)
        assert measured["scroll"] <= measured["inner"], ("empty", width, measured)
        see_all = page.get_by_role("button", name="See all conversations")
        assert await on_top(see_all)
        await page.get_by_role("button", name="Keyboard shortcuts").click()
        dialog = help_dialog(page)
        await expect(dialog).to_be_visible()
        measured = await page_width(page)
        assert measured["scroll"] <= measured["inner"], ("help", width, measured)
        overflow = await dialog.evaluate(
            """el => [...el.querySelectorAll('*')].filter(child => {
                const box = child.getBoundingClientRect();
                const out = box.left < -0.5 || box.right > window.innerWidth + 0.5;
                return box.width > 0 && out;
            }).map(child => child.outerHTML.slice(0, 80))"""
        )
        assert overflow == [], (width, overflow)


ANNOUNCE_JS = """el => {
    window.__said = [];
    new MutationObserver(() => window.__said.push(el.textContent))
        .observe(el, {childList: true, characterData: true, subtree: true});
}"""


async def test_count_changes_are_announced_once_per_burst(shell: Shell) -> None:
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
        # The page's clock is the test's: nothing waits on real time from here.
        await page.clock.install()
        await page.goto(f"{shell.base}/portal/inbox")
        count = page.get_by_test_id("inbox-needs-us-count")
        await expect(count).to_have_text("3", timeout=WAIT)
        await page.clock.pause_at(datetime.now(UTC) + timedelta(minutes=1))
        live = page.get_by_test_id("inbox-count-announcement")
        await live.evaluate(ANNOUNCE_JS)
        # A burst: two answers in a row change the count twice (each tab click reads the list).
        state.counts_override = {**counts, "needs_us": 4}
        await tab(page, "mine").click()
        await expect(count).to_have_text("4", timeout=WAIT)
        state.counts_override = {**counts, "needs_us": 5}
        await tab(page, "needs_us").click()
        await expect(count).to_have_text("5", timeout=WAIT)
        # The clock has not moved: nothing is said yet.
        assert await page.evaluate("window.__said") == []
        await page.clock.run_for(1600)
        await expect(live).to_have_text("Needs us: 5")
        assert await page.evaluate("window.__said") == ["Needs us: 5"]
        # Polls with the same count say nothing more.
        before = len(state.list_queries)
        await page.clock.run_for(3100)
        await eventually(lambda: len(state.list_queries) > before)
        before = len(state.list_queries)
        await page.clock.run_for(3100)
        await eventually(lambda: len(state.list_queries) > before)
        await page.clock.run_for(1600)
        assert await page.evaluate("window.__said") == ["Needs us: 5"]
        # A later change is its own burst, and back to the count already said is no news.
        state.counts_override = {**counts, "needs_us": 2}
        await page.clock.run_for(3100)
        await expect(count).to_have_text("2", timeout=WAIT)
        await page.clock.run_for(1600)
        await expect(live).to_have_text("Needs us: 2")
        state.counts_override = {**counts, "needs_us": 6}
        await page.clock.run_for(3100)
        await expect(count).to_have_text("6", timeout=WAIT)
        state.counts_override = {**counts, "needs_us": 2}
        await tab(page, "mine").click()
        await expect(count).to_have_text("2", timeout=WAIT)
        await page.clock.run_for(1600)
        assert await page.evaluate("window.__said") == ["Needs us: 5", "Needs us: 2"]
