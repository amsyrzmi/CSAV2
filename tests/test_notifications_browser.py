"""Real browser checks for the notification bell and its panel in the console top bar.

The same stubbed harness as `test_inbox_redesign_browser.py` (its module-scoped Next.js shell and
its synthetic API): the Inbox stub answers the notification list, the count and the mark-read
route from `Inbox.notifications`, and records every call. The shell polls the count and `next dev`
runs mount effects twice, so reads are asserted "at least"; every mark (a POST) is exact.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest
from playwright.async_api import Page, expect

from tests import test_inbox_redesign_browser as redesign
from tests.test_inbox_redesign_browser import (
    LONG_NAME,
    NAMED,
    NEWCOMER,
    Inbox,
    Shell,
    eventually,
    inbox_page,
    notification,
    page_width,
)
from tests.test_services_browser import WAIT

pytestmark = pytest.mark.asyncio

# One staged shell for this file too, built by the redesign module's fixture.
shell = redesign.shell

NUDGE = re.compile(r"\bfs-nudge\b")
ABSOLUTE_TIME = re.compile(r"^\d{1,2} [A-Z][a-z]{2} \d{4}, \d{2}:\d{2}$")
EMPTY = "No notifications yet. Reminders about waiting customers appear here."
LOAD_FAILED = "Notifications did not load."
MARK_FAILED = "Could not mark as read. Try again."
ALL_MARKED = "All marked as read."
FAILURE = (500, {"detail": "synthetic outage"})


def seeded() -> Inbox:
    """Four notifications, three of them unread: newest is the snooze, then the assignment (its
    route is one this version cannot open), then the overdue reminder, then a read one."""
    state = Inbox()
    state.notifications = [
        notification(1, 120, read=True),
        notification(2, 30),
        notification(3, 10, key="inbox.assigned", link={"route": "payments"}),
        notification(
            4,
            5,
            key="inbox.snooze_due",
            params={"customer": "Ben Tan"},
            link={"route": "inbox", "conversation": NEWCOMER},
        ),
    ]
    return state


def nid(number: int) -> str:
    return f"b0000000-0000-4000-8000-{number:012d}"


def bell(page: Page) -> Any:
    return page.get_by_test_id("shell-notifications")


def badge(page: Page) -> Any:
    return page.get_by_test_id("shell-notifications-count")


def panel(page: Page) -> Any:
    return page.get_by_test_id("shell-notifications-panel")


def rows(page: Page) -> Any:
    return panel(page).get_by_test_id("notification-row")


def list_reads(state: Inbox) -> int:
    return sum(1 for call in state.notification_calls if call["path"] == "notifications")


def count_reads(state: Inbox) -> int:
    return sum(1 for call in state.notification_calls if call["path"] == "notifications/count")


async def open_bell(page: Page) -> Any:
    await expect(bell(page)).to_be_visible(timeout=WAIT)
    await bell(page).click()
    await expect(panel(page)).to_be_visible()
    return panel(page)


async def record_pushes(page: Page) -> None:
    """Record the router's own history writes (path in `__pushed`, path and query in
    `__pushed_urls`): the inbox screen may later drop the query from the address bar, so the
    navigation itself is what a test asserts."""
    await page.evaluate(
        """() => {
            window.__pushed = [];
            window.__pushed_urls = [];
            const push = history.pushState.bind(history);
            history.pushState = (state, title, url) => {
                const target = new URL(String(url), location.href);
                window.__pushed.push(target.pathname);
                window.__pushed_urls.push(target.pathname + target.search);
                return push(state, title, url);
            };
        }"""
    )


async def eventually_pushed(page: Page, path: str) -> None:
    await page.wait_for_function(
        "(path) => (window.__pushed || []).includes(path)", arg=path, timeout=WAIT
    )


async def posted(state: Inbox, expected: list[dict[str, Any]]) -> None:
    """The mark-read bodies the server received. The call is a CSRF fetch, then the POST, fired
    after the optimistic update, so wait for it to arrive before comparing."""
    await eventually(lambda: len(state.notification_posts()) >= len(expected))
    assert [call["body"] for call in state.notification_posts()] == expected


async def inside_viewport(page: Page, locator: Any, label: str) -> None:
    box = await locator.bounding_box()
    width = (await page_width(page))["inner"]
    assert box, label
    assert box["x"] >= -0.5, (label, box, width)
    assert box["x"] + box["width"] <= width + 0.5, (label, box, width)


async def test_the_badge_shows_the_unread_count_and_names_the_button(shell: Shell) -> None:
    state = seeded()
    async with inbox_page(shell, state) as page:
        await expect(badge(page)).to_have_text("3", timeout=WAIT)
        await expect(badge(page)).to_have_attribute("aria-hidden", "true")
        await expect(bell(page)).to_have_attribute("aria-label", "Notifications, 3 unread")
        await expect(page.get_by_role("button", name="Notifications, 3 unread")).to_have_count(1)
        assert count_reads(state) >= 1


async def test_no_badge_and_a_plain_name_at_zero(shell: Shell) -> None:
    state = Inbox()
    async with inbox_page(shell, state) as page:
        await expect(bell(page)).to_be_visible(timeout=WAIT)
        await eventually(lambda: count_reads(state) >= 1)
        await expect(badge(page)).to_have_count(0)
        await expect(page.get_by_role("button", name="Notifications", exact=True)).to_have_count(1)


async def test_the_badge_caps_at_99_plus_and_the_name_keeps_the_number(shell: Shell) -> None:
    state = Inbox()
    state.unread_override = 150
    async with inbox_page(shell, state) as page:
        await expect(badge(page)).to_have_text("99+", timeout=WAIT)
        await expect(bell(page)).to_have_attribute("aria-label", "Notifications, 150 unread")


async def test_opening_lists_the_rows_newest_first_and_marks_nothing(shell: Shell) -> None:
    state = seeded()
    async with inbox_page(shell, state) as page:
        await expect(badge(page)).to_have_text("3", timeout=WAIT)
        await expect(bell(page)).to_have_attribute("aria-expanded", "false")
        popover = await open_bell(page)
        await expect(bell(page)).to_have_attribute("aria-expanded", "true")
        await expect(bell(page)).to_have_attribute(
            "aria-controls", await popover.get_attribute("id") or "missing"
        )
        await expect(page.get_by_role("group", name="Notifications")).to_have_count(1)
        await expect(rows(page)).to_have_count(4)
        titles = [
            (await row.get_by_test_id("notification-title").inner_text()).strip()
            for row in await rows(page).all()
        ]
        assert titles == [
            "Snooze ended",
            "Assigned to you",
            "Waiting for your team",
            "Waiting for your team",
        ]
        newest = rows(page).nth(0)
        await expect(newest.get_by_test_id("notification-detail")).to_have_text(
            "Ben Tan is back in Needs us."
        )
        # Seeded before the browser started: a slow start may add a minute or two.
        await expect(newest.locator("time")).to_have_text(re.compile(r"^[5-7] minutes ago$"))
        assert ABSOLUTE_TIME.match(await newest.locator("time").get_attribute("title") or "")
        await expect(rows(page).nth(2).get_by_test_id("notification-detail")).to_have_text(
            "Aisyah Rahman has waited 45 minutes."
        )
        await expect(rows(page).nth(2).locator("time")).to_have_text(
            re.compile(r"^(30|31|32) minutes ago$")
        )
        # "New" and the screen-reader "Unread." are on the three unread rows and not the read one.
        for index in range(3):
            await expect(rows(page).nth(index).get_by_text("New", exact=True)).to_have_count(1)
            await expect(rows(page).nth(index).locator(".sr-only")).to_have_text("Unread. ")
        await expect(rows(page).nth(3).get_by_text("New", exact=True)).to_have_count(0)
        await expect(rows(page).nth(3).get_by_text("Unread.")).to_have_count(0)
        # Opening reads the list (newest 50) and posts nothing.
        assert list_reads(state) >= 1
        assert any("limit=50" in call["query"] for call in state.notification_calls)
        assert state.notification_posts() == []
        await expect(badge(page)).to_have_text("3")


async def test_clicking_a_row_with_a_link_marks_it_and_opens_the_inbox(shell: Shell) -> None:
    state = seeded()
    async with inbox_page(shell, state) as page:
        await expect(badge(page)).to_have_text("3", timeout=WAIT)
        await open_bell(page)
        link = rows(page).nth(2).get_by_role("link")
        await expect(link).to_have_attribute("href", f"/portal/inbox?conversation={NAMED}")
        await expect(rows(page).nth(0).get_by_role("link")).to_have_attribute(
            "href", f"/portal/inbox?conversation={NEWCOMER}"
        )
        await record_pushes(page)
        await link.click()
        await eventually_pushed(page, "/portal/inbox")
        await expect(badge(page)).to_have_text("2", timeout=WAIT)
        await posted(state, [{"ids": [nid(2)]}])
        await expect(panel(page)).to_have_count(0)
        await expect(bell(page)).to_have_attribute("aria-label", "Notifications, 2 unread")


async def test_a_keyboard_activated_link_opens_the_inbox_and_focus_returns_to_the_bell(
    shell: Shell,
) -> None:
    state = seeded()
    async with inbox_page(shell, state) as page:
        await expect(badge(page)).to_have_text("3", timeout=WAIT)
        await open_bell(page)
        link = rows(page).nth(2).get_by_role("link")
        await expect(link).to_have_attribute("href", f"/portal/inbox?conversation={NAMED}")
        await record_pushes(page)
        await link.focus()
        await page.keyboard.press("Enter")
        # The page already starts on the inbox, so the navigation itself is the proof.
        await eventually_pushed(page, "/portal/inbox")
        pushed = await page.evaluate("() => window.__pushed_urls")
        assert f"/portal/inbox?conversation={NAMED}" in pushed, pushed
        await expect(panel(page)).to_have_count(0)
        await expect(bell(page)).to_be_focused()
        await posted(state, [{"ids": [nid(2)]}])


async def test_a_row_without_a_usable_link_is_plain_text_with_a_mark_button(shell: Shell) -> None:
    state = seeded()
    # A kind this version does not know, with a link that names nothing it can open.
    state.notifications.append(
        notification(5, 2, key="hr.something_new", params={}, link={"route": "inbox"})
    )
    async with inbox_page(shell, state) as page:
        await expect(badge(page)).to_have_text("4", timeout=WAIT)
        await open_bell(page)
        await expect(rows(page)).to_have_count(5)
        unknown = rows(page).nth(0)
        await expect(unknown.get_by_test_id("notification-title")).to_have_text("New notification")
        await expect(unknown.get_by_test_id("notification-detail")).to_have_text(
            "Open the console to see the details."
        )
        await expect(unknown.get_by_role("link")).to_have_count(0)
        route = rows(page).nth(2)
        await expect(route.get_by_test_id("notification-title")).to_have_text("Assigned to you")
        await expect(route.get_by_role("link")).to_have_count(0)
        # A read row has nothing left to mark.
        await expect(rows(page).nth(4).get_by_role("button")).to_have_count(0)
        await route.get_by_role("button", name="Mark as read").click()
        await expect(route.get_by_text("New", exact=True)).to_have_count(0)
        await expect(badge(page)).to_have_text("3", timeout=WAIT)
        await posted(state, [{"ids": [nid(3)]}])
        # The row is still there, now read, and the page did not move.
        await expect(rows(page)).to_have_count(5)
        assert page.url.endswith("/portal/inbox")


async def test_mark_all_posts_all_says_so_and_is_disabled_at_zero(shell: Shell) -> None:
    state = seeded()
    async with inbox_page(shell, state) as page:
        await expect(badge(page)).to_have_text("3", timeout=WAIT)
        await open_bell(page)
        mark_all = panel(page).get_by_role("button", name="Mark all as read")
        await expect(mark_all).to_be_enabled()
        await mark_all.click()
        await expect(panel(page).get_by_role("status")).to_have_text(ALL_MARKED)
        await posted(state, [{"all": True}])
        await expect(panel(page).get_by_text("New", exact=True)).to_have_count(0)
        await expect(mark_all).to_be_disabled()
        await expect(badge(page)).to_have_count(0, timeout=WAIT)
        await expect(bell(page)).to_have_attribute("aria-label", "Notifications")
    state = Inbox()
    state.notifications = [notification(1, 5, read=True)]
    async with inbox_page(shell, state) as page:
        await open_bell(page)
        await expect(rows(page)).to_have_count(1)
        await expect(panel(page).get_by_role("button", name="Mark all as read")).to_be_disabled()
        await expect(panel(page).get_by_role("status")).to_have_text("")


async def test_a_failed_mark_restores_the_row_and_says_so(shell: Shell) -> None:
    state = seeded()
    state.notification_script["read"] = [FAILURE]
    state.notification_gates["read"] = gate = asyncio.Event()
    async with inbox_page(shell, state) as page:
        await expect(badge(page)).to_have_text("3", timeout=WAIT)
        await open_bell(page)
        route = rows(page).nth(1)
        await route.get_by_role("button", name="Mark as read").click()
        # Optimistic: the row reads as done while the call is held.
        await expect(route.get_by_text("New", exact=True)).to_have_count(0)
        await expect(route.locator(".sr-only")).to_have_count(0)
        gate.set()
        await expect(panel(page).get_by_text(MARK_FAILED, exact=True)).to_be_visible(timeout=WAIT)
        await expect(route.get_by_text("New", exact=True)).to_have_count(1)
        await expect(route.get_by_role("button", name="Mark as read")).to_be_visible()
        await expect(badge(page)).to_have_text("3")
        assert len(state.notification_posts()) == 1
        assert state.notifications[2]["read_at"] is None
        # Trying again works and clears the message.
        await route.get_by_role("button", name="Mark as read").click()
        await expect(route.get_by_text("New", exact=True)).to_have_count(0)
        await expect(panel(page).get_by_text(MARK_FAILED, exact=True)).to_have_count(0)
        await expect(badge(page)).to_have_text("2", timeout=WAIT)
        await eventually(lambda: len(state.notification_posts()) == 2)


async def test_a_failed_mark_all_restores_every_row(shell: Shell) -> None:
    state = seeded()
    state.notification_script["read"] = [FAILURE]
    async with inbox_page(shell, state) as page:
        await expect(badge(page)).to_have_text("3", timeout=WAIT)
        await open_bell(page)
        await panel(page).get_by_role("button", name="Mark all as read").click()
        await expect(panel(page).get_by_text(MARK_FAILED, exact=True)).to_be_visible(timeout=WAIT)
        await expect(panel(page).get_by_text("New", exact=True)).to_have_count(3)
        await expect(panel(page).get_by_role("status")).to_have_text("")
        await expect(panel(page).get_by_role("button", name="Mark all as read")).to_be_enabled()
        await expect(badge(page)).to_have_text("3")


async def test_the_empty_state_says_what_will_appear(shell: Shell) -> None:
    state = Inbox()
    async with inbox_page(shell, state) as page:
        await open_bell(page)
        await expect(panel(page).get_by_text(EMPTY, exact=True)).to_be_visible(timeout=WAIT)
        await expect(rows(page)).to_have_count(0)
        await expect(panel(page).get_by_role("button", name="Mark all as read")).to_be_disabled()


async def test_a_failed_list_says_so_and_try_again_reloads(shell: Shell) -> None:
    state = seeded()
    state.notification_script["list"] = [FAILURE] * 6
    async with inbox_page(shell, state) as page:
        await open_bell(page)
        await expect(panel(page).get_by_text(LOAD_FAILED, exact=True)).to_be_visible(timeout=WAIT)
        await expect(rows(page)).to_have_count(0)
        before = list_reads(state)
        assert before >= 1
        state.notification_script["list"].clear()
        await panel(page).get_by_role("button", name="Try again").click()
        await expect(rows(page)).to_have_count(4, timeout=WAIT)
        await expect(panel(page).get_by_text(LOAD_FAILED, exact=True)).to_have_count(0)
        assert list_reads(state) > before


async def test_loading_shows_its_own_words(shell: Shell) -> None:
    state = seeded()
    state.notification_gates["list"] = gate = asyncio.Event()
    async with inbox_page(shell, state) as page:
        await open_bell(page)
        await expect(panel(page).get_by_text("Loading notifications…", exact=True)).to_be_visible()
        gate.set()
        await expect(rows(page)).to_have_count(4, timeout=WAIT)
        await expect(panel(page).get_by_text("Loading notifications…")).to_have_count(0)


async def test_escape_returns_focus_to_the_bell_and_a_click_outside_closes(shell: Shell) -> None:
    state = seeded()
    async with inbox_page(shell, state) as page:
        await open_bell(page)
        await rows(page).nth(1).get_by_role("button", name="Mark as read").focus()
        await page.keyboard.press("Escape")
        await expect(panel(page)).to_have_count(0)
        await expect(bell(page)).to_be_focused()
        await expect(bell(page)).to_have_attribute("aria-expanded", "false")
        await open_bell(page)
        await page.get_by_test_id("shell-title").click()
        await expect(panel(page)).to_have_count(0)
        # Closing marks nothing.
        assert state.notification_posts() == []
        # The bell opens again, and a second press on it closes the panel.
        await open_bell(page)
        await bell(page).click()
        await expect(panel(page)).to_have_count(0)


async def test_a_rise_while_open_reloads_the_list_and_no_rise_does_not(shell: Shell) -> None:
    state = seeded()
    async with inbox_page(shell, state) as page:
        await expect(badge(page)).to_have_text("3", timeout=WAIT)
        await open_bell(page)
        await expect(rows(page)).to_have_count(4)
        # Same count on a re-read (focus): the panel is not read again.
        reads, counts = list_reads(state), count_reads(state)
        await page.evaluate("() => window.dispatchEvent(new Event('focus'))")
        await eventually(lambda: count_reads(state) > counts)
        await asyncio.sleep(0.5)
        assert list_reads(state) == reads
        # A new notification arrives; the next shell read raises the count and the list follows.
        state.notifications.append(notification(9, 1, key="inbox.follow_up"))
        await page.evaluate("() => window.dispatchEvent(new Event('focus'))")
        await expect(badge(page)).to_have_text("4", timeout=WAIT)
        await expect(rows(page)).to_have_count(5, timeout=WAIT)
        await expect(rows(page).nth(0).get_by_test_id("notification-title")).to_have_text(
            "Follow up or resolve"
        )
        assert list_reads(state) > reads


async def test_the_badge_nudges_on_a_rise_when_motion_is_allowed(shell: Shell) -> None:
    state = seeded()
    async with inbox_page(shell, state) as page:
        await expect(badge(page)).to_have_text("3", timeout=WAIT)
        await expect(badge(page)).not_to_have_class(NUDGE)
        state.notifications.append(notification(9, 1))
        await page.evaluate("() => window.dispatchEvent(new Event('focus'))")
        await expect(badge(page)).to_have_text("4", timeout=WAIT)
        await expect(badge(page)).to_have_class(NUDGE)
        assert await badge(page).evaluate("el => getComputedStyle(el).animationName") == "fs-nudge"


async def test_the_badge_does_not_nudge_under_reduced_motion(shell: Shell) -> None:
    state = seeded()
    async with inbox_page(shell, state, reduced_motion=True) as page:
        await expect(badge(page)).to_have_text("3", timeout=WAIT)
        state.notifications.append(notification(9, 1))
        await page.evaluate("() => window.dispatchEvent(new Event('focus'))")
        await expect(badge(page)).to_have_text("4", timeout=WAIT)
        await expect(badge(page)).not_to_have_class(NUDGE)


def crowded() -> Inbox:
    """Rows whose customer is one unbroken 60-letter name, read and not, with and without link."""
    state = Inbox()
    long_params = {"customer": LONG_NAME, "waited_minutes": 2900, "priority": "urgent", "repeat": 3}
    state.notifications = [
        notification(1, 40, params=long_params),
        notification(2, 20, key="inbox.assigned", params=long_params, link={"route": "payments"}),
        notification(3, 10, key="inbox.stale_claim", params=long_params, read=True),
        notification(4, 5, key="inbox.handback", params=long_params),
    ]
    return state


async def assert_contained(page: Page, label: str, *, open_panel: bool = True) -> None:
    measured = await page_width(page)
    assert measured["scroll"] <= measured["inner"], (label, "page", measured)
    await inside_viewport(page, page.get_by_test_id("shell-topbar"), f"{label} topbar")
    header = await page.get_by_test_id("shell-topbar").evaluate(
        "el => ({scroll: el.scrollWidth, client: el.clientWidth})"
    )
    assert header["scroll"] <= header["client"], (label, "topbar", header)
    await inside_viewport(page, bell(page), f"{label} bell")
    if not open_panel:
        return
    await inside_viewport(page, panel(page), f"{label} panel")
    inner = await panel(page).evaluate("el => ({scroll: el.scrollWidth, client: el.clientWidth})")
    assert inner["scroll"] <= inner["client"], (label, "panel content", inner)


@pytest.mark.parametrize("theme", ["cobalt", "night", "bureau"])
async def test_at_320_px_the_bar_and_the_panel_stay_inside_the_viewport(
    shell: Shell, theme: str
) -> None:
    state = crowded()
    async with inbox_page(shell, state, width=320, theme=theme) as page:
        await expect(badge(page)).to_have_text("3", timeout=WAIT)
        await assert_contained(page, f"{theme} closed", open_panel=False)
        await open_bell(page)
        await expect(rows(page)).to_have_count(4)
        await assert_contained(page, f"{theme} open")
        # The long name wraps inside its row instead of widening the panel.
        for row in await rows(page).all():
            box = await row.bounding_box()
            container = await panel(page).bounding_box()
            assert box and container
            assert box["x"] + box["width"] <= container["x"] + container["width"] + 0.5
        await expect(rows(page).nth(0).get_by_test_id("notification-detail")).to_contain_text(
            LONG_NAME
        )


@pytest.mark.parametrize("width", [390, 768, 1024, 1440])
async def test_the_panel_stays_inside_the_viewport_at_wider_widths(
    shell: Shell, width: int
) -> None:
    state = crowded()
    async with inbox_page(shell, state, width=width) as page:
        await expect(badge(page)).to_have_text("3", timeout=WAIT)
        await open_bell(page)
        await expect(rows(page)).to_have_count(4)
        await assert_contained(page, f"{width}")
        box = await panel(page).bounding_box()
        assert box
        # 24 rem wide where the screen allows it.
        assert box["width"] <= 384.5
        if width >= 768:
            assert box["width"] >= 383.5


async def test_a_read_only_member_sees_and_uses_the_bell(shell: Shell) -> None:
    state = seeded()
    async with inbox_page(shell, state, role="read_only") as page:
        await expect(badge(page)).to_have_text("3", timeout=WAIT)
        await open_bell(page)
        await rows(page).nth(1).get_by_role("button", name="Mark as read").click()
        await expect(badge(page)).to_have_text("2", timeout=WAIT)
        await posted(state, [{"ids": [nid(3)]}])


async def test_a_platform_admin_has_no_bell_and_makes_no_notification_calls(
    shell: Shell,
) -> None:
    state = seeded()
    async with inbox_page(shell, state, role="platform_admin", path="/inbox") as page:
        await expect(page.get_by_test_id("shell-topbar")).to_be_visible(timeout=WAIT)
        await expect(page.get_by_test_id("shell-user-menu")).to_be_visible()
        await asyncio.sleep(2)
        await expect(bell(page)).to_have_count(0)
        await expect(badge(page)).to_have_count(0)
        assert state.notification_calls == []


async def test_after_a_keyboard_mark_escape_still_closes_and_returns_focus(shell: Shell) -> None:
    state = seeded()
    async with inbox_page(shell, state) as page:
        await expect(badge(page)).to_have_text("3", timeout=WAIT)
        await open_bell(page)
        route = rows(page).nth(1)
        await route.get_by_role("button", name="Mark as read").focus()
        await page.keyboard.press("Enter")
        # The focused button is gone with the row's unread state; focus stays inside the panel.
        await expect(route.get_by_text("New", exact=True)).to_have_count(0)
        await expect(badge(page)).to_have_text("2", timeout=WAIT)
        assert await panel(page).evaluate("el => el.contains(document.activeElement)")
        await page.keyboard.press("Escape")
        await expect(panel(page)).to_have_count(0)
        await expect(bell(page)).to_be_focused()
        await posted(state, [{"ids": [nid(3)]}])


async def test_after_a_keyboard_mark_all_escape_still_closes_and_returns_focus(
    shell: Shell,
) -> None:
    state = seeded()
    async with inbox_page(shell, state) as page:
        await expect(badge(page)).to_have_text("3", timeout=WAIT)
        await open_bell(page)
        mark_all = panel(page).get_by_role("button", name="Mark all as read")
        await mark_all.focus()
        await page.keyboard.press("Enter")
        await expect(mark_all).to_be_disabled()
        assert await panel(page).evaluate("el => el.contains(document.activeElement)")
        await page.keyboard.press("Escape")
        await expect(panel(page)).to_have_count(0)
        await expect(bell(page)).to_be_focused()
        await posted(state, [{"all": True}])


async def test_overlapping_marks_where_the_first_fails_keep_the_unread_number_true(
    shell: Shell,
) -> None:
    # Two plain unread rows. Mark A is sent, then mark B while A is still out; A fails, B works.
    # The rollback must add back only A's unread (not reset to the number before B), so when A
    # is marked again nothing is left unread and "Mark all as read" is disabled.
    state = Inbox()
    state.notifications = [
        notification(1, 120, read=True),
        notification(2, 20, key="hr.something_new", params={}, link={}),
        notification(3, 10, key="hr.something_new", params={}, link={}),
    ]
    state.notification_script["read"] = [FAILURE]
    state.notification_gates["read"] = gate = asyncio.Event()
    async with inbox_page(shell, state) as page:
        await expect(badge(page)).to_have_text("2", timeout=WAIT)
        await open_bell(page)
        first, second = rows(page).nth(0), rows(page).nth(1)
        await first.get_by_role("button", name="Mark as read").click()
        await second.get_by_role("button", name="Mark as read").click()
        await eventually(lambda: len(state.notification_posts()) == 2)
        gate.set()
        await expect(panel(page).get_by_text(MARK_FAILED, exact=True)).to_be_visible(timeout=WAIT)
        await expect(first.get_by_text("New", exact=True)).to_have_count(1)
        await expect(second.get_by_text("New", exact=True)).to_have_count(0)
        await expect(badge(page)).to_have_text("1", timeout=WAIT)
        await first.get_by_role("button", name="Mark as read").click()
        await expect(first.get_by_text("New", exact=True)).to_have_count(0)
        await expect(panel(page).get_by_role("button", name="Mark all as read")).to_be_disabled()
        await expect(badge(page)).to_have_count(0, timeout=WAIT)
        await posted(state, [{"ids": [nid(3)]}, {"ids": [nid(2)]}, {"ids": [nid(3)]}])
