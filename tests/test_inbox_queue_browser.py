"""Real browser checks for the Inbox queue bar: tabs with counts, search, Filters and Sort.

The same stubbed harness as `test_inbox_redesign_browser.py` (its module-scoped Next.js shell
and its synthetic Inbox API); the rows here carry the work-queue fields and the stub filters by
`state`, so each tab shows only its own rows.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
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
