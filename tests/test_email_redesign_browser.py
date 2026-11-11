"""Real browser acceptance for the friendlier Email assistant: status bar, chips, confirmations.

The real Next.js shell runs unmodified from the disposable staged copy that
`test_services_redesign_browser.site` builds (one per file). Its layouts fetch `/auth/me` and
`/services/active` server-side from a loopback stub; every browser-side `/console/v1/**` call is
answered by Playwright from `World`, a richer version of `MailWorld` in
`test_email_management_browser` (threads that differ in read state, category, priority and age,
drafts tied to threads, delete, disconnect, settings and the company list). That file keeps
pinning the sync and attention call counts; this one proves the new controls add no requests.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from csa.email.models import AttentionItem, AttentionPage, DraftOut, RecipientOut, draft_version
from playwright.async_api import Page, expect

from tests import test_services_redesign_browser as services
from tests.test_email_management_browser import (
    CARD_DRAFT,
    OWNER,
    REPLY_DRAFT,
    STUCK_DRAFT,
    SUPPLIER,
    MailWorld,
    outside,
    thread,
)
from tests.test_services_browser import WAIT, make_me
from tests.test_services_redesign_browser import Site, with_browser

pytestmark = pytest.mark.asyncio

# The staged copy of the console is built once per file by the services fixture.
site = services.site

THEMES = ("cobalt", "night", "bureau")
WIDTHS = (320, 390, 768, 1024, 1440)
KL = ZoneInfo("Asia/Kuala_Lumpur")
ACME = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"
BETA = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2"
PACKAGES = [
    {"key": "customer_support", "status": "active"},
    {"key": "staff_email", "status": "active"},
]
NOTE = "Showing the last 90 days, up to 200 emails."


def days_ago(days: int) -> str:
    """Noon on that local day in Kuala Lumpur, as the server would send it."""
    noon = (datetime.now(KL) - timedelta(days=days)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    return noon.astimezone(UTC).isoformat()


def rich_thread(
    n: int,
    subject: str,
    *,
    age: int,
    unread: bool = False,
    category: str | None = None,
    priority: str | None = None,
) -> dict[str, Any]:
    return {
        **thread(n),
        "thread_id": f"thread-{n}",
        "subject": subject,
        "received_at": days_ago(age),
        "unread": unread,
        "category": category,
        "priority": priority,
        "summary": f"{subject}: the customer wrote in.",
    }


THREADS = [
    rich_thread(
        1,
        "Late delivery to Shah Alam",
        age=1,
        unread=True,
        category="order_or_delivery",
        priority="high",
    ),
    rich_thread(2, "Invoice 114", age=3, unread=True, category="billing", priority="normal"),
    rich_thread(3, "Supplier price list", age=10, category="vendor_or_partner", priority="low"),
    rich_thread(4, "Newsletter October", age=40, category="newsletter_or_promotion"),
    rich_thread(5, "Refund request", age=2, unread=True, category="complaint", priority="high"),
]
ALL = [f"thread-{n}" for n in range(1, 6)]


class World(MailWorld):
    """`MailWorld` plus thread detail, delete, disconnect, settings and the company list."""

    def __init__(self) -> None:
        super().__init__()
        self.last_synced_at = datetime.now(UTC).isoformat()  # fresh: no sync on open
        self.threads = [dict(item) for item in THREADS]
        self.more_pages = 0
        self.enabled = True
        self.attention_items = 1
        self.mailbox_status = "connected"
        self.companies: list[dict[str, Any]] = [
            {
                "id": ACME,
                "slug": "acme",
                "name": "Kedai Aminah",
                "timezone": "Asia/Kuala_Lumpur",
                "status": "active",
            }
        ]
        self.companies_status = 200
        self.drafts = {
            REPLY_DRAFT: self.draft(
                REPLY_DRAFT,
                to=["siti@kedai.test"],
                subject="Re: Late delivery to Shah Alam",
                body="Hi Siti,\n\nHere is the delivery date.",
                thread_id="thread-1",
            ),
            CARD_DRAFT: self.draft(
                CARD_DRAFT,
                to=[SUPPLIER],
                subject="Re: Supplier price list",
                body="Hello,\n\nPlease send your latest price list.",
                thread_id="thread-3",
            ),
            STUCK_DRAFT: self.draft(
                STUCK_DRAFT,
                to=["ali@kedai.test"],
                subject="Stock count",
                body="Hi Ali, the stock count is done.",
                status="sending",
            ),
        }

    @staticmethod
    def draft(  # type: ignore[override]
        draft_id: UUID,
        *,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        status: str = "draft",
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        cc, bcc = cc or [], bcc or []
        now = datetime.now(UTC)
        return DraftOut(
            id=draft_id,
            thread_id=thread_id,
            recipient=to[0],
            to=to,
            cc=cc,
            bcc=bcc,
            recipients=[RecipientOut(address=a, outside=outside(a)) for a in (*to, *cc, *bcc)],
            subject=subject,
            body=body,
            status=status,  # type: ignore[arg-type]
            source="model",
            gmail_draft_id=None,
            sent_at=None,
            needs_staff_input=False,
            reply_to_message_id=None,
            created_at=now - timedelta(minutes=30),
            updated_at=now - timedelta(minutes=5),
        ).model_dump(mode="json")

    def context(self) -> dict[str, Any]:
        context = super().context()
        context["enabled"] = self.enabled
        context["mailbox"]["status"] = self.mailbox_status
        return context

    def count(self, method: str, path: str) -> int:
        return len([c for c in self.calls if c["method"] == method and c["path"] == path])

    async def answer(self, method: str, path: str, body: Any) -> tuple[int, Any]:
        if path == "admin/clients" and method == "GET":
            if self.companies_status != 200:
                return self.companies_status, {"detail": "synthetic outage"}
            return 200, self.companies
        opened = re.fullmatch(r"email/threads/([\w-]+)", path)
        if opened and method == "GET":
            found = next((t for t in self.threads if t["thread_id"] == opened[1]), None)
            if found is None:
                return 404, {"detail": "resource_unavailable"}
            return 200, {
                **found,
                "body": f"Hello, this is the full message for {found['subject']}.",
            }
        if path == "email/attention" and method == "GET":
            page = AttentionPage(
                items=[
                    AttentionItem(
                        thread_id=f"thread-{n}",
                        sender=f"Customer {n} <customer{n}@example.test>",
                        subject=f"Attention item {n}",
                        priority="high" if n == 1 else "normal",
                        summary=f"Summary of item {n}.",
                        next_steps=[f"Next step {n}"],
                    )
                    for n in range(1, self.attention_items + 1)
                ],
                as_of=datetime.now(UTC),
                skipped=0,
            )
            return 200, page.model_dump(mode="json")
        if path == "email/drafts" and method == "GET":
            return 200, {"items": list(self.drafts.values())}
        deleted = re.fullmatch(r"email/drafts/([0-9a-f-]+)/delete", path)
        if deleted and method == "POST":
            self.drafts.pop(UUID(deleted[1]), None)
            return 200, self.receipt("draft.delete", {})
        if path == "email/mailbox/disconnect" and method == "POST":
            self.mailbox_status = "revoked"
            return 200, self.receipt("mailbox.disconnect", {})
        if path == "email/settings" and method == "PUT":
            self.enabled = bool(body["enabled"])
            return 200, self.receipt("settings.update", {})
        return await super().answer(method, path, body)


@asynccontextmanager
async def email_page(
    browser: Any,
    site: Site,
    world: World,
    *,
    width: int = 1280,
    height: int = 1000,
    theme: str | None = None,
    role: str = "tenant_admin",
    dock: bool = False,
    wait: str | None = "email-thread-list",
) -> AsyncIterator[Page]:
    me = make_me(role)
    if role == "platform_admin":
        me["memberships"] = [
            {"client_id": ACME, "role": "tenant_admin"},
            {"client_id": BETA, "role": "tenant_admin"},
        ]
    if dock:
        me["workspace_assistant"] = True
    site.upstream.me, site.upstream.packages = me, PACKAGES
    context = await browser.new_context(
        viewport={"width": width, "height": height}, timezone_id="Asia/Kuala_Lumpur"
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
    await page.route("**/console/v1/**", world.route)
    await page.goto(f"{site.base}{'/email' if role == 'platform_admin' else '/portal/email'}")
    if wait:
        await expect(page.get_by_test_id(wait)).to_be_visible(timeout=WAIT)
    try:
        yield page
        assert not errors, errors
        assert not world.unexpected, world.unexpected
        assert not site.upstream.unexpected, site.upstream.unexpected
    finally:
        await context.close()


async def listed(page: Page) -> list[str]:
    return await page.eval_on_selector_all(
        '[data-testid="email-thread"]', "els => els.map(e => e.dataset.threadId)"
    )


async def pick_last_seven_days(page: Page) -> None:
    await page.get_by_test_id("email-date").click()
    await page.get_by_test_id("email-date-preset-last7").click()
    await page.get_by_test_id("email-date-done").click()
    await expect(page.get_by_test_id("email-date-popover")).to_have_count(0)


async def test_chips_filter_loaded_threads_without_new_requests(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World()
        async with email_page(browser, site, world) as page:
            assert await listed(page) == ALL
            unread = page.get_by_test_id("email-chips-unread")
            drafted = page.get_by_test_id("email-chips-hasDraft")
            await expect(unread).to_contain_text("3")
            await expect(drafted).to_contain_text("2")
            calls_before = len(world.calls)

            await unread.click()
            await expect(unread).to_have_attribute("aria-pressed", "true")
            assert await listed(page) == ["thread-1", "thread-2", "thread-5"]
            await drafted.click()
            assert await listed(page) == ["thread-1"]
            await unread.click()
            assert await listed(page) == ["thread-1", "thread-3"]
            await drafted.click()
            assert await listed(page) == ALL

            await page.get_by_test_id("email-category").select_option("billing")
            assert await listed(page) == ["thread-2"]
            options = (
                await page.get_by_test_id("email-category").locator("option").all_inner_texts()
            )
            assert options == [
                "All categories",
                "Order / delivery",
                "Complaint",
                "Billing",
                "Vendor / partner",
                "Newsletter / promotion",
            ]
            await page.get_by_test_id("email-category").select_option("")
            priorities = (
                await page.get_by_test_id("email-priority").locator("option").all_inner_texts()
            )
            assert priorities == ["All priorities", "High", "Normal", "Low"]
            await page.get_by_test_id("email-priority").select_option("high")
            assert await listed(page) == ["thread-1", "thread-5"]
            await page.get_by_test_id("email-priority").select_option("")

            await pick_last_seven_days(page)
            assert await listed(page) == ["thread-1", "thread-2", "thread-5"]
            await unread.click()
            await page.get_by_test_id("email-priority").select_option("high")
            await page.get_by_test_id("email-category").select_option("complaint")
            assert await listed(page) == ["thread-5"]
            await page.get_by_test_id("email-category").select_option("billing")
            await expect(page.get_by_test_id("email-no-match")).to_be_visible()
            assert await listed(page) == []
            # The empty message has its own button next to the text; the one in the bar stays.
            await page.get_by_test_id("email-no-match-clear").click()
            assert await listed(page) == ALL
            await expect(page.get_by_test_id("email-no-match")).to_have_count(0)
            await page.get_by_test_id("email-priority").select_option("high")
            await page.get_by_test_id("email-clear-filters").click()
            assert await listed(page) == ALL
            await expect(page.get_by_test_id("email-date")).to_contain_text("Any date")

            # Filtering happens in the browser: no request went out, and the tab count is the cache.
            assert len(world.calls) == calls_before, world.calls[calls_before:]
            await expect(
                page.get_by_role("button", name=re.compile(r"^Inbox \(5\)"))
            ).to_be_visible()

    await with_browser(body)


async def test_cache_note_shown_with_filters(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World()
        async with email_page(browser, site, world) as page:
            note = page.get_by_test_id("email-cache-note")
            await expect(note).to_have_text(NOTE)
            await expect(note).to_have_attribute("data-filtered", "false")
            await expect(page.get_by_test_id("email-match-count")).to_have_count(0)

            await page.get_by_test_id("email-chips-unread").click()
            await expect(note).to_have_attribute("data-filtered", "true")
            await expect(note).to_contain_text(NOTE)
            await expect(page.get_by_test_id("email-match-count")).to_have_text(
                "3 of 5 emails match."
            )
            await page.get_by_test_id("email-chips-unread").click()
            await expect(note).to_have_attribute("data-filtered", "false")

            await page.get_by_test_id("email-priority").select_option("low")
            await expect(note).to_have_attribute("data-filtered", "true")
            await expect(note).to_contain_text(NOTE)
            await page.get_by_test_id("email-clear-filters").click()
            await pick_last_seven_days(page)
            await expect(note).to_have_attribute("data-filtered", "true")
            await expect(note).to_contain_text(NOTE)
            await expect(page.get_by_test_id("email-match-count")).to_have_text(
                "3 of 5 emails match."
            )
            await page.get_by_test_id("email-clear-filters").click()
            await expect(note).to_have_text(NOTE)

    await with_browser(body)


async def test_delete_draft_asks_first_and_cancel_deletes_nothing(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World()
        async with email_page(browser, site, world) as page:
            await page.get_by_role("button", name=re.compile(r"^Drafts")).click()
            first = page.get_by_test_id("email-draft").first
            trigger = first.get_by_test_id("email-delete-draft")
            ask = first.get_by_test_id("email-delete-draft-ask")
            await expect(ask).to_have_count(0)

            await trigger.click()
            await expect(ask).to_be_visible()
            await expect(ask).to_contain_text("Delete this draft?")
            await expect(trigger).to_have_attribute("aria-expanded", "true")
            await expect(first.get_by_test_id("email-delete-draft-cancel")).to_be_focused()
            assert world.count("POST", f"email/drafts/{REPLY_DRAFT}/delete") == 0

            await page.keyboard.press("Escape")
            await expect(ask).to_have_count(0)
            await expect(trigger).to_be_focused()

            await trigger.click()
            await first.get_by_test_id("email-delete-draft-cancel").click()
            await expect(ask).to_have_count(0)
            await expect(trigger).to_be_focused()
            await expect(page.get_by_test_id("email-draft")).to_have_count(3)
            assert not [c for c in world.calls if c["path"].endswith("/delete")]

            # Confirm deletes exactly once, even when the button is double clicked.
            await trigger.click()
            await first.get_by_test_id("email-delete-draft-confirm").dblclick()
            await expect(page.get_by_test_id("email-draft")).to_have_count(2, timeout=WAIT)
            assert world.count("POST", f"email/drafts/{REPLY_DRAFT}/delete") == 1
            assert len([c for c in world.calls if c["path"].endswith("/delete")]) == 1
            await expect(
                page.get_by_role("button", name=re.compile(r"^Drafts \(2\)"))
            ).to_be_focused()

    await with_browser(body)


async def test_switch_off_asks_first_and_the_page_links_to_connections(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World()
        async with email_page(browser, site, world) as page:
            status = page.get_by_test_id("email-status")
            await expect(status).to_contain_text("Connected")
            await expect(status).to_contain_text(OWNER)
            await expect(page.get_by_role("button", name="Sync inbox")).to_have_count(1)
            # Both switches sit behind the arrow; the raw line and disclosure keep their words.
            await page.get_by_test_id("email-mailbox-toggle").click()
            await expect(page.get_by_test_id("email-mailbox-line")).to_contain_text(
                f"Mailbox: {OWNER} · connected"
            )
            await expect(page.get_by_test_id("email-disclosure")).to_have_text(
                "Synthetic disclosure."
            )

            off = page.get_by_test_id("email-switch-off")
            await off.click()
            await expect(page.get_by_test_id("email-switch-off-ask")).to_contain_text(
                "Switch the email assistant off"
            )
            await page.keyboard.press("Escape")
            await expect(page.get_by_test_id("email-switch-off-ask")).to_have_count(0)
            await expect(off).to_be_focused()
            await off.click()
            await page.get_by_test_id("email-switch-off-cancel").click()
            await expect(off).to_be_focused()
            assert world.count("PUT", "email/settings") == 0

            # Gmail is connected and disconnected on the Connections tab: the page keeps its
            # status and the way there, and no control of its own.
            link = page.get_by_test_id("email-manage-connections")
            await expect(link).to_have_text("Manage in Connections")
            await expect(link).to_have_attribute("href", "/portal/connections")
            await expect(
                page.get_by_role("button", name=re.compile("Connect|Disconnect"))
            ).to_have_count(0)
            assert world.count("POST", "email/mailbox/disconnect") == 0

            await off.click()
            await page.get_by_test_id("email-switch-off-confirm").dblclick()
            await expect(page.get_by_text("Assistant disabled.")).to_be_visible(timeout=WAIT)
            (put,) = [c for c in world.calls if c["path"] == "email/settings"]
            assert put["body"] == {"enabled": False}
            await expect(status).to_contain_text(
                "The email assistant is switched off for your company."
            )
            await expect(page.get_by_test_id("email-switch-on")).to_be_focused()

            # Switching on needs no confirmation.
            await page.get_by_test_id("email-switch-on").click()
            await expect(page.get_by_text("Assistant enabled.")).to_be_visible(timeout=WAIT)
            assert [c["body"] for c in world.calls if c["path"] == "email/settings"] == [
                {"enabled": False},
                {"enabled": True},
            ]

    await with_browser(body)


async def test_platform_admin_picker_shows_company_names(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World()
        async with email_page(
            browser, site, world, role="platform_admin", wait="email-tenant"
        ) as page:
            picker = page.get_by_test_id("email-tenant")
            await expect(picker.locator("option")).to_have_text(
                ["Choose a tenant", "Kedai Aminah", BETA]
            )
            # One read of the company list (development mode runs an effect twice, one is aborted).
            assert 1 <= world.count("GET", "admin/clients") <= 2
            reads = world.count("GET", "admin/clients")
            await picker.select_option(ACME)
            await page.get_by_role("button", name="Open email").click()
            await expect(page.get_by_test_id("email-thread-list")).to_be_visible(timeout=WAIT)
            context_calls = [c for c in world.calls if c["path"] == "email/context"]
            assert context_calls and {c["query"] for c in context_calls} == {f"client_id={ACME}"}
            assert world.count("GET", "admin/clients") == reads
            # A platform admin has no Gmail of their own: the status shows, the link does not.
            await expect(page.get_by_test_id("email-status")).to_be_visible()
            await expect(page.get_by_test_id("email-manage-connections")).to_have_count(0)

    await with_browser(body)


async def test_platform_admin_picker_falls_back_to_ids_when_names_fail(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World()
        world.companies_status = 500
        async with email_page(
            browser, site, world, role="platform_admin", wait="email-tenant"
        ) as page:
            await expect(page.get_by_test_id("email-tenant").locator("option")).to_have_text(
                ["Choose a tenant", ACME, BETA]
            )
            # Next's route announcer is an empty alert; only one with words would be a message.
            await expect(
                page.get_by_role("alert").filter(has_text=re.compile(r"\S"))
            ).to_have_count(0)

    await with_browser(body)


async def test_member_never_reads_the_company_list(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World()
        async with email_page(browser, site, world) as page:
            await expect(page.get_by_test_id("email-tenant")).to_have_count(0)
            assert world.count("GET", "admin/clients") == 0
            assert world.count("POST", "email/sync") == 0
            assert world.count("GET", "email/attention") == 1

    await with_browser(body)


async def test_only_one_dialog_when_sending_from_a_draft(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World()
        async with email_page(browser, site, world) as page:
            await page.get_by_test_id("email-thread").first.click()
            reading = page.get_by_test_id("email-reading")
            await expect(reading).to_contain_text(
                "Hello, this is the full message for Late delivery to Shah Alam."
            )
            # The draft for this thread is edited right here: no drawer, no dialog.
            inline = reading.get_by_test_id("email-thread-drafts").get_by_test_id("email-draft")
            await expect(inline).to_have_count(1)
            await expect(inline.get_by_test_id("email-to")).to_have_value("siti@kedai.test")
            await expect(page.get_by_role("dialog")).to_have_count(0)
            await expect(reading.get_by_test_id("email-reply-dock")).to_be_visible()

            await inline.get_by_test_id("email-send").click()
            dialog = page.get_by_role("dialog")
            await expect(dialog).to_have_count(1)
            await expect(dialog).to_contain_text("Send this email?")
            await expect(dialog).to_contain_text("Here is the delivery date.")
            assert world.count("PUT", f"email/drafts/{REPLY_DRAFT}") == 0  # nothing was edited
            # Focus enters the dialog once it is enabled, so Escape closes it.
            await expect(dialog.get_by_role("button", name="Keep editing")).to_be_focused()
            await page.keyboard.press("Escape")
            await expect(page.get_by_role("dialog")).to_have_count(0)

            await inline.get_by_test_id("email-send").click()
            await expect(page.get_by_role("dialog")).to_have_count(1)
            await page.get_by_test_id("email-send-confirm").click()
            await expect(reading.get_by_test_id("email-sent-at")).to_be_visible(timeout=WAIT)
            (send,) = [c for c in world.calls if c["path"] == f"email/drafts/{REPLY_DRAFT}/send"]
            assert (
                send["body"]["version"]
                == draft_version(DraftOut.model_validate(world.drafts[REPLY_DRAFT]))
                and send["key"]
            )
            await expect(page.get_by_role("dialog")).to_have_count(0)

    await with_browser(body)


async def test_sent_drafts_do_not_count_as_drafts(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World()
        sent = {**world.drafts[CARD_DRAFT], "status": "sent", "sent_at": days_ago(0)}
        world.drafts[CARD_DRAFT] = sent
        async with email_page(browser, site, world) as page:
            await expect(page.get_by_test_id("email-chips-hasDraft")).to_contain_text("1")
            await expect(page.get_by_test_id("email-thread-draft")).to_have_count(1)
            await page.get_by_test_id("email-chips-hasDraft").click()
            assert await listed(page) == ["thread-1"]
            # Its record stays on the thread: only the tag and the chip stop counting it.
            await page.get_by_test_id("email-chips-hasDraft").click()
            await page.get_by_test_id("email-thread").nth(2).click()
            await expect(page.get_by_test_id("email-thread-drafts")).to_contain_text("Sent")

    await with_browser(body)


async def test_note_says_when_drafts_may_be_cut_short(site: Site) -> None:
    async def body(browser: Any) -> None:
        clause = "Drafts beyond the first 100 are not counted."
        world = World()
        async with email_page(browser, site, world) as page:
            await expect(page.get_by_test_id("email-drafts-note")).to_have_count(0)
            await expect(page.get_by_test_id("email-cache-note")).to_have_text(NOTE)

        full = World()
        for n in range(97):  # three drafts exist already: the server stops at 100
            draft_id = UUID(int=0x7000 + n)
            full.drafts[draft_id] = full.draft(
                draft_id, to=["old@kedai.test"], subject=f"Old {n}", body="Done.", status="sent"
            )
        assert len(full.drafts) == 100
        async with email_page(browser, site, full) as page:
            note = page.get_by_test_id("email-cache-note")
            await expect(note).to_have_text(f"{NOTE} {clause}")
            await page.get_by_test_id("email-chips-unread").click()
            await expect(note).to_contain_text(NOTE)
            await expect(note).to_contain_text(clause)
            await expect(note).to_have_attribute("data-filtered", "true")

    await with_browser(body)


async def test_chosen_category_stays_in_the_list_after_a_sync_removes_it(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World()
        async with email_page(browser, site, world) as page:
            category = page.get_by_test_id("email-category")
            await category.select_option("billing")
            assert await listed(page) == ["thread-2"]
            # A sync brings a list without the only billing email.
            world.threads = [t for t in world.threads if t["thread_id"] != "thread-2"]
            await page.get_by_role("button", name="Sync inbox").click()
            await expect(page.get_by_text("Checked Gmail: 1 conversation.")).to_be_visible(
                timeout=WAIT
            )
            await expect(category).to_have_value("billing")
            options = await category.locator("option").all_inner_texts()
            assert "Billing" in options and options[0] == "All categories"
            await expect(page.get_by_test_id("email-no-match")).to_be_visible()
            note = page.get_by_test_id("email-cache-note")
            await expect(note).to_have_attribute("data-filtered", "true")
            await page.get_by_test_id("email-no-match-clear").click()
            assert await listed(page) == ["thread-1", "thread-3", "thread-4", "thread-5"]
            await expect(category).to_have_value("")
            assert "Billing" not in await category.locator("option").all_inner_texts()

    await with_browser(body)


async def test_phone_attention_shows_two_items_and_folds_the_rest(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World()
        world.attention_items = 4
        async with email_page(browser, site, world, width=390, height=844) as page:
            panel = page.get_by_test_id("email-attention")
            items = panel.locator("ul.divide-y > li")
            more = page.get_by_test_id("email-attention-more")
            await expect(items).to_have_count(4)  # all four stay in the page
            await expect(items.locator("visible=true")).to_have_count(2)
            await expect(panel).to_contain_text("Attention item 4")
            await expect(more).to_have_text("Show 2 more")
            await expect(more).to_have_attribute("aria-expanded", "false")
            await more.click()
            await expect(items.locator("visible=true")).to_have_count(4)
            await expect(more).to_have_text("Show fewer")
            await expect(more).to_have_attribute("aria-expanded", "true")
            await more.click()
            await expect(items.locator("visible=true")).to_have_count(2)
            assert world.count("GET", "email/attention") == 1  # folding asks the server for nothing

            # From a laptop width up nothing is folded and the button is not shown.
            await page.set_viewport_size({"width": 1280, "height": 900})
            await expect(items.locator("visible=true")).to_have_count(4)
            await expect(more).to_be_hidden()
            assert world.count("GET", "email/attention") == 1

        short = World()
        short.attention_items = 2
        async with email_page(browser, site, short, width=390, height=844) as page:
            await expect(page.get_by_test_id("email-attention-more")).to_have_count(0)

    await with_browser(body)


async def overflow(page: Page) -> dict[str, int]:
    return await page.evaluate(
        "() => ({scroll: document.documentElement.scrollWidth, inner: window.innerWidth})"
    )


async def outside_the_page(page: Page) -> list[str]:
    return await page.evaluate(
        """() => [...document.querySelectorAll('[data-testid^="email-"]')].filter(
            e => e.getBoundingClientRect().width > 0
                && e.getBoundingClientRect().right > window.innerWidth + 0.5
        ).map(e => e.dataset.testid)"""
    )


@pytest.mark.parametrize("theme", THEMES)
async def test_email_at_five_widths_in_three_themes(site: Site, theme: str) -> None:
    async def body(browser: Any) -> None:
        world = World()
        async with email_page(browser, site, world, theme=theme, dock=True) as page:
            await page.get_by_test_id("email-mailbox-toggle").click()
            await page.get_by_test_id("email-switch-off").click()
            await page.get_by_test_id("email-chips-unread").click()
            await page.get_by_test_id("email-thread").first.click()
            await expect(page.get_by_test_id("email-thread-drafts")).to_be_visible()
            for width in WIDTHS:
                await page.set_viewport_size({"width": width, "height": 900})
                await page.wait_for_timeout(150)
                measured = await overflow(page)
                assert measured["scroll"] <= measured["inner"], (theme, width, measured)
                assert not await outside_the_page(page), (theme, width)
            await page.get_by_role("button", name=re.compile(r"^Drafts")).click()
            await expect(page.get_by_test_id("email-draft").first).to_be_visible()
            await (
                page.get_by_test_id("email-draft")
                .first.get_by_test_id("email-delete-draft")
                .click()
            )
            for width in WIDTHS:
                await page.set_viewport_size({"width": width, "height": 900})
                await page.wait_for_timeout(150)
                measured = await overflow(page)
                assert measured["scroll"] <= measured["inner"], (theme, width, "drafts", measured)
                assert not await outside_the_page(page), (theme, width, "drafts")

    await with_browser(body)


@pytest.mark.parametrize("width", (320, 390))
async def test_platform_picker_with_long_names_fits_narrow_screens(site: Site, width: int) -> None:
    async def body(browser: Any) -> None:
        world = World()
        world.companies[0]["name"] = (
            "Perusahaan Perniagaan Serbaneka Kedai Runcit Aminah dan Anak-anak Sdn Bhd"
        )
        async with email_page(
            browser, site, world, width=width, role="platform_admin", wait="email-tenant"
        ) as page:
            await expect(page.get_by_test_id("email-tenant").locator("option").nth(1)).to_have_text(
                world.companies[0]["name"]
            )
            await page.get_by_test_id("email-tenant").select_option(ACME)
            await page.get_by_role("button", name="Open email").click()
            await expect(page.get_by_test_id("email-thread-list")).to_be_visible(timeout=WAIT)
            measured = await overflow(page)
            assert measured["scroll"] <= measured["inner"], measured

    await with_browser(body)
