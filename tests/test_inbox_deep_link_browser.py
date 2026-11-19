"""Real browser checks for the Inbox deep link: `/inbox?conversation=<id>` and the bell's rows.

The same stubbed harness as `test_inbox_redesign_browser.py` (its module-scoped Next.js shell, its
synthetic API and its `inbox_page(..., path=...)`). `next dev` runs mount effects twice, so a read
is asserted "at least"; what must hold exactly is what the person sees.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from playwright.async_api import Page, expect

from tests import test_inbox_redesign_browser as redesign
from tests.test_inbox_redesign_browser import (
    NAMED,
    NEWCOMER,
    OTHER_TENANT,
    Inbox,
    Shell,
    conversation,
    inbox_page,
    notification,
    rows_loaded,
    selected_id,
)
from tests.test_notifications_browser import bell, open_bell, panel
from tests.test_notifications_browser import rows as bell_rows
from tests.test_services_browser import TENANT, WAIT

pytestmark = pytest.mark.asyncio

# One staged shell for this file too, built by the redesign module's fixture.
shell = redesign.shell

OFF_PAGE = "a0000000-0000-4000-8000-000000000009"
FOREIGN = "a0000000-0000-4000-8000-00000000000f"
MISSING = "a0000000-0000-4000-8000-0000000000aa"
OFF_PAGE_NAME = "Faizal Ahmad"
FOREIGN_NAME = "Outsider Customer"
SELECT_PROMPT = "Select a conversation to read it."
DETAIL_ERROR = "We could not refresh this conversation."


def seeded() -> Inbox:
    """The default four rows, plus a conversation only the detail read serves and one that
    belongs to another company."""
    state = Inbox()
    state.off_page = [
        conversation(OFF_PAGE, "telegram", name=OFF_PAGE_NAME, preview="Old chat.", minutes=400)
    ]
    state.other_company = [
        conversation(FOREIGN, "telegram", name=FOREIGN_NAME, preview="Not yours.", minutes=3)
    ]
    return state


def thread(page: Page) -> Any:
    return page.get_by_test_id("inbox-thread")


async def thread_text(page: Page, text: str = "Let me check that.") -> None:
    await expect(thread(page).get_by_text(text, exact=True)).to_be_visible(timeout=WAIT)


async def settled(page: Page) -> None:
    """Long enough for a link that was going to open a thread to have done it (and read it)."""
    await page.wait_for_timeout(1500)


async def url_is(page: Page, path: str) -> None:
    """The address, after the screen has dropped the parameter it consumed."""
    await expect(page).to_have_url(re.compile(f"{re.escape(path)}$"), timeout=WAIT)


async def test_a_link_to_a_listed_conversation_opens_and_highlights_it(shell: Shell) -> None:
    state = seeded()
    async with inbox_page(shell, state, path=f"/portal/inbox?conversation={NEWCOMER}") as page:
        await rows_loaded(page, 4)
        await thread_text(page)
        await expect(thread(page)).to_contain_text("Ben Tan")
        assert await selected_id(page) == NEWCOMER
        assert NEWCOMER in state.detail_ids
        await url_is(page, "/portal/inbox")
        # The list is not stuck on it: another row opens as usual.
        await redesign.open_thread(page, NAMED)
        assert await selected_id(page) == NAMED


async def test_a_conversation_outside_the_loaded_page_opens_from_its_own_read(
    shell: Shell,
) -> None:
    state = seeded()
    async with inbox_page(shell, state, path=f"/portal/inbox?conversation={OFF_PAGE}") as page:
        await rows_loaded(page, 4)
        await thread_text(page)
        await expect(thread(page)).to_contain_text(OFF_PAGE_NAME)
        await expect(page.locator("[data-conversation-id]")).to_have_count(4)
        await expect(page.locator('[data-conversation-id][aria-current="true"]')).to_have_count(0)
        assert OFF_PAGE in state.detail_ids
        await url_is(page, "/portal/inbox")


async def test_a_link_to_a_missing_conversation_shows_the_thread_error_and_the_list_works(
    shell: Shell,
) -> None:
    state = seeded()
    async with inbox_page(shell, state, path=f"/portal/inbox?conversation={MISSING}") as page:
        await rows_loaded(page, 4)
        await expect(thread(page).get_by_text(DETAIL_ERROR)).to_be_visible(timeout=WAIT)
        await url_is(page, "/portal/inbox")
        await redesign.open_thread(page, NAMED)
        assert await selected_id(page) == NAMED
        await expect(thread(page).get_by_text(DETAIL_ERROR)).to_have_count(0)


async def test_a_member_cannot_open_another_companys_conversation_by_id(shell: Shell) -> None:
    state = seeded()
    async with inbox_page(shell, state, path=f"/portal/inbox?conversation={FOREIGN}") as page:
        await rows_loaded(page, 4)
        await expect(thread(page).get_by_text(DETAIL_ERROR)).to_be_visible(timeout=WAIT)
        assert FOREIGN in state.detail_ids
        # The stub answered 404 for it, so nothing of that company's customer reached the screen.
        await expect(page.get_by_text(FOREIGN_NAME)).to_have_count(0)
        await expect(page.locator('[data-conversation-id][aria-current="true"]')).to_have_count(0)


@pytest.mark.parametrize(
    "value",
    ["not-a-uuid", "'; DROP TABLE conversations; --", f"{NAMED}x", "", f"{NAMED}%26x"],
)
async def test_a_junk_value_is_ignored(shell: Shell, value: str) -> None:
    state = seeded()
    async with inbox_page(shell, state, path=f"/portal/inbox?conversation={value}") as page:
        await rows_loaded(page, 4)
        await settled(page)
        await expect(page.get_by_text(SELECT_PROMPT)).to_be_visible()
        await expect(thread(page)).to_have_count(0)
        assert state.detail_ids == []


async def test_a_repeated_parameter_is_ignored(shell: Shell) -> None:
    state = seeded()
    path = f"/portal/inbox?conversation={NAMED}&conversation={NEWCOMER}"
    async with inbox_page(shell, state, path=path) as page:
        await rows_loaded(page, 4)
        await settled(page)
        await expect(page.get_by_text(SELECT_PROMPT)).to_be_visible()
        await expect(thread(page)).to_have_count(0)
        assert state.detail_ids == []


async def test_other_parameters_stay_and_the_conversation_goes(shell: Shell) -> None:
    state = seeded()
    path = f"/portal/inbox?conversation={NAMED}&source=bell"
    async with inbox_page(shell, state, path=path) as page:
        await rows_loaded(page, 4)
        await thread_text(page)
        await url_is(page, "/portal/inbox?source=bell")


async def test_the_bell_opens_a_second_conversation_while_already_on_the_inbox(
    shell: Shell,
) -> None:
    state = seeded()
    state.notifications = [
        notification(1, 30),
        notification(
            2, 5, key="inbox.snooze_due", link={"route": "inbox", "conversation": NEWCOMER}
        ),
        notification(
            3, 4, key="inbox.follow_up", link={"route": "inbox", "conversation": OFF_PAGE}
        ),
    ]
    async with inbox_page(shell, state) as page:
        await rows_loaded(page, 4)
        await expect(page.get_by_text(SELECT_PROMPT)).to_be_visible()
        # Newest first: row 0 is the off-page one, row 1 NEWCOMER, row 2 NAMED.
        await open_bell(page)
        await bell_rows(page).nth(2).get_by_role("link").click()
        await thread_text(page)
        assert await selected_id(page) == NAMED
        await url_is(page, "/portal/inbox")
        await open_bell(page)
        await bell_rows(page).nth(1).get_by_role("link").click()
        await expect(thread(page)).to_contain_text("Ben Tan", timeout=WAIT)
        assert await selected_id(page) == NEWCOMER
        await url_is(page, "/portal/inbox")
        # A conversation outside the page opens too, and drops the highlight.
        await open_bell(page)
        await bell_rows(page).nth(0).get_by_role("link").click()
        await expect(thread(page)).to_contain_text(OFF_PAGE_NAME, timeout=WAIT)
        await expect(page.locator('[data-conversation-id][aria-current="true"]')).to_have_count(0)
        await url_is(page, "/portal/inbox")
        await expect(panel(page)).to_have_count(0)


async def test_the_same_bell_row_opens_the_thread_again_after_it_was_closed(
    shell: Shell,
) -> None:
    state = seeded()
    state.notifications = [notification(1, 5)]
    async with inbox_page(shell, state, width=390) as page:
        await rows_loaded(page, 4)
        await expect(thread(page)).to_have_count(0)
        for _ in range(2):
            await open_bell(page)
            await bell_rows(page).nth(0).get_by_role("link").click()
            await expect(thread(page)).to_be_visible(timeout=WAIT)
            await thread_text(page)
            await url_is(page, "/portal/inbox")
            await page.get_by_role("button", name="Back to conversations").click()
            await expect(thread(page)).to_be_hidden()
        assert state.detail_ids.count(NAMED) >= 2
        await expect(bell(page)).to_be_visible()


async def test_a_platform_admin_link_opens_that_company_and_conversation(shell: Shell) -> None:
    state = seeded()
    path = f"/inbox?conversation={NAMED}&client_id={TENANT}"
    async with inbox_page(shell, state, role="platform_admin", path=path) as page:
        await expect(page.get_by_label("Tenant")).to_have_value(TENANT, timeout=WAIT)
        await rows_loaded(page, 4)
        await thread_text(page)
        assert await selected_id(page) == NAMED
        await url_is(page, f"/inbox?client_id={TENANT}")
        assert any(f"client_id={TENANT}" in query for query in state.list_queries)
        # The company stays chosen after the parameter went.
        await expect(page.get_by_label("Tenant")).to_have_value(TENANT)


async def test_a_platform_admin_link_to_a_conversation_of_another_company_is_refused(
    shell: Shell,
) -> None:
    state = seeded()
    state.clients = [
        *state.clients,
        {"id": OTHER_TENANT, "slug": "other", "name": "Other Tenant", "status": "active"},
    ]
    # The link names this company, but the conversation belongs to the other one: the read is
    # scoped to the company the link chose, so it is 404 and nothing of the other leaks.
    path = f"/inbox?conversation={FOREIGN}&client_id={TENANT}"
    async with inbox_page(shell, state, role="platform_admin", path=path) as page:
        await rows_loaded(page, 4)
        await expect(thread(page).get_by_text(DETAIL_ERROR)).to_be_visible(timeout=WAIT)
        await expect(page.get_by_text(FOREIGN_NAME)).to_have_count(0)


async def test_a_platform_admin_link_without_a_valid_company_opens_nothing(shell: Shell) -> None:
    state = seeded()
    path = f"/inbox?conversation={NAMED}&client_id=not-a-company"
    async with inbox_page(shell, state, role="platform_admin", path=path) as page:
        await expect(page.get_by_text("Select a tenant to open its inbox.")).to_be_visible(
            timeout=WAIT
        )
        await expect(page.get_by_label("Tenant")).to_have_value("")
        assert state.list_queries == []
        # A conversation id means nothing without its company: choosing one later opens no thread.
        await page.get_by_label("Tenant").select_option(TENANT)
        await rows_loaded(page, 4)
        await settled(page)
        await expect(page.get_by_text(SELECT_PROMPT)).to_be_visible()
        assert state.detail_ids == []
