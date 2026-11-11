"""Real browser acceptance for the Connections tab (TikTok Shop card and Google card).

The real Next.js shell runs unmodified from the disposable staged copy that
`test_services_redesign_browser.site` builds. Its layouts fetch `/auth/me` and `/services/active`
server-side, so the loopback stub from `test_services_browser` answers those; every browser-side
`/console/v1/*` call is answered by Playwright from `Stub`, which records the stores, packages and
Google reads and fails on anything it does not know. Google's calendar and email calls are served
only when a test says it expects them (`serves_google`); otherwise any such call fails the test.
The browser clock is fixed (Date only, timers keep running) so the "Synced 3 min ago" line is exact.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.async_api import Page, expect

from tests import test_services_redesign_browser as services
from tests.test_business_browser import (
    AUTHORIZE_URL,
    NOW,
    SECOND_ID,
    STORE_ID,
    ZONE,
    make_store,
    until,
)
from tests.test_services_browser import TENANT, TENANT_PROFILE, WAIT, make_me
from tests.test_services_redesign_browser import Site, with_browser

pytestmark = pytest.mark.asyncio

# The staged copy of the console is built once per file by the services fixture.
site = services.site

CONNECT_PATH = "business/stores/tiktok/connect"
GOOGLE_URL = "https://accounts.google.com/o/oauth2/v2/auth?state=synthetic-state"
ADDRESS = "ada@example.test"
EVERYTHING = [
    {"key": "marketplaces", "status": "active"},
    {"key": "bookings", "status": "active"},
    {"key": "staff_email", "status": "active"},
]
BANNER = (
    "Google stopped accepting the connection, so new bookings aren't reaching your calendar. "
    "Reconnect to start them again."
)
CALENDAR_CONFIRM = (
    "Stop sending bookings to your Google Calendar? "
    "Your Bookings calendar and its events stay in your Google account."
)
GMAIL_QUESTION = (
    "Disconnect Gmail? Emails already loaded stay readable. Connect Gmail again to sync or send."
)
GMAIL_DISCONNECTED = "Gmail disconnected. Emails already loaded stay readable."
THEMES = ("cobalt", "night", "bureau")


def calendar_status(
    state: str | None = "not_connected",
    *,
    people: list[dict[str, str]] | None = None,
    available: bool = True,
    can_connect: bool = True,
) -> dict[str, Any]:
    return {"available": available, "can_connect": can_connect, "status": state, "people": people}


def email_context(
    status: str | None = None,
    *,
    address: str = ADDRESS,
    enabled: bool = True,
    configured: bool = True,
    can_connect: bool = True,
) -> dict[str, Any]:
    mailbox = (
        None
        if status is None
        else {"status": status, "address": address, "last_sync_at": "2026-11-02T01:57:00Z"}
    )
    return {
        "enabled": enabled,
        "can_manage": False,
        "can_connect": can_connect,
        "provider_configured": configured,
        "model_configured": True,
        "mailbox": mailbox,
        "disclosure": "Synthetic disclosure.",
        "last_synced_at": None,
        "more_available": False,
    }


MARKETPLACES = [{"key": "marketplaces", "status": "active"}]
STAFF_NOTE = "Only an Admin can connect or disconnect a shop."
UNAVAILABLE = "TikTok Shop isn't set up on this server yet."
CONNECTED_SENTENCE = "TikTok Shop is connected. Orders and products will start syncing shortly."
# Every reason the callback sends, with the sentence the tab says for it.
REASON_SENTENCES = {
    "tiktok_unavailable": UNAVAILABLE,
    "provider_denied": "TikTok said the connection wasn't approved. Nothing was changed.",
    "state_invalid": "That connection link has expired or was already used. Start again with Connect TikTok Shop.",  # noqa: E501
    "multiple_shops": "This TikTok account has more than one shop. Contact support to connect the right one.",  # noqa: E501
    "no_authorized_shop": "TikTok didn't share any shop with us. Connect again and choose your shop.",  # noqa: E501
    "shop_in_use": "That TikTok shop is already connected. If it is yours, use Reconnect on its card.",  # noqa: E501
    "insufficient_scope": "TikTok didn't give us all the access we need. Connect again and allow every permission.",  # noqa: E501
    "connect_failed": "We couldn't confirm the shop you approved. "
    "If you chose a different shop than before, connect the original one.",
}


@dataclass
class Stub:
    """Browser-side answers; fails (and records) every call it was not told to expect."""

    stores: list[dict[str, Any]] = field(default_factory=lambda: [make_store()])
    packages: list[dict[str, str]] = field(default_factory=lambda: list(MARKETPLACES))
    packages_fail: bool = False
    connect_status: int = 200
    connect_data: dict[str, Any] = field(
        default_factory=lambda: {"authorization_url": AUTHORIZE_URL}
    )
    connect_calls: list[dict[str, Any]] = field(default_factory=list)
    disconnect_calls: list[str] = field(default_factory=list)
    store_queries: list[dict[str, list[str]]] = field(default_factory=list)
    package_queries: list[dict[str, list[str]]] = field(default_factory=list)
    # Google: the calendar and email calls of the Google card. Served only when `serves_google`;
    # every call is recorded either way, and a test that does not serve them fails on the first.
    serves_google: bool = False
    calendar: dict[str, Any] = field(default_factory=calendar_status)
    calendar_connect_status: int = 200
    calendar_connect_data: dict[str, Any] = field(default_factory=lambda: {"url": GOOGLE_URL})
    email: dict[str, Any] = field(default_factory=email_context)
    email_connect_data: dict[str, Any] = field(
        default_factory=lambda: {"authorization_url": GOOGLE_URL}
    )
    google_calls: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)

    def google_paths(self) -> set[str]:
        """The distinct calls made so far, without the repeats strict mode causes."""
        return set(self.google_calls)

    def serve_google(self, method: str, path: str) -> tuple[int, Any]:
        self.google_calls.append(f"{method} {path}")
        if not self.serves_google:
            return 404, {"detail": "not stubbed"}
        if (method, path) == ("GET", "bookings/calendar/status"):
            return 200, self.calendar
        if (method, path) == ("POST", "bookings/calendar/connect"):
            return self.calendar_connect_status, self.calendar_connect_data
        if (method, path) == ("POST", "bookings/calendar/disconnect"):
            self.calendar = calendar_status("not_connected")
            return 204, None
        if (method, path) == ("GET", "email/context"):
            return 200, self.email
        if (method, path) == ("POST", "email/mailbox/connect"):
            return 200, self.email_connect_data
        if (method, path) == ("POST", "email/mailbox/disconnect"):
            self.email = email_context(None)
            return 200, {
                "operation": "disconnect",
                "resource_id": "mailbox",
                "status": "done",
                "duplicate": False,
                "detail": {},
            }
        self.unexpected.append(f"{method} {path}")
        return 500, {"detail": "unexpected synthetic call"}

    async def route(self, request_route: Any) -> None:
        request = request_route.request
        parts = urlsplit("http://x/" + request.url.split("/console/v1/")[1])
        path = parts.path.lstrip("/")
        query = parse_qs(parts.query, keep_blank_values=True)
        status, data = 200, None
        if path == "auth/csrf":
            data = {"csrf_token": "synthetic-csrf"}
        elif path == "services/active" and request.method == "GET":
            self.package_queries.append(query)
            if self.packages_fail:
                status, data = 503, {"detail": "synthetic packages outage"}
            else:
                data = {"packages": self.packages}
        elif path == "admin/clients" and request.method == "GET":
            data = [TENANT_PROFILE]
        elif path == "hr/context" and request.method == "GET":
            # The rail asks for the HR tabs on pages that name a tenant; unused here.
            await request_route.fulfill(status=404, json={"detail": "not stubbed"})
            return
        elif path == "business/stores" and request.method == "GET":
            self.store_queries.append(query)
            data = {
                "source": "local_credential_records",
                "marketplace_sync": "not_connected",
                "items": self.stores,
                "next_cursor": None,
            }
        elif path == CONNECT_PATH and request.method == "POST":
            self.connect_calls.append(
                {"body": request.post_data_json or {}, "client_id": query.get("client_id")}
            )
            status, data = self.connect_status, self.connect_data
        elif (
            re.fullmatch(r"business/stores/[0-9a-f-]+/disconnect", path)
            and request.method == "POST"
        ):
            store_id = path.split("/")[2]
            self.disconnect_calls.append(store_id)
            for store in self.stores:
                if store["id"] == store_id:
                    store["connection"] = {
                        "status": "disconnected",
                        "live_integration": "unavailable",
                    }
            await request_route.fulfill(status=204)
            return
        elif path.startswith(("bookings/calendar/", "email/")):
            status, data = self.serve_google(request.method, path)
            if status == 204:
                await request_route.fulfill(status=204)
                return
        else:
            self.unexpected.append(f"{request.method} {path}")
            status, data = 500, {"detail": "unexpected synthetic call"}
        try:
            await request_route.fulfill(status=status, json=data)
        except Exception:
            pass


@asynccontextmanager
async def connections_page(
    browser: Any,
    site: Site,
    stub: Stub,
    *,
    path: str = "/portal/connections",
    role: str = "tenant_admin",
    width: int = 1280,
    theme: str | None = None,
) -> AsyncIterator[Page]:
    site.upstream.me = make_me(role)
    site.upstream.packages = list(stub.packages)
    context = await browser.new_context(viewport={"width": width, "height": 1000}, timezone_id=ZONE)
    context.set_default_navigation_timeout(WAIT)
    context.set_default_timeout(15_000)
    await context.clock.set_fixed_time(NOW)
    cookies = [{"name": "csa_session", "value": "synthetic-session", "url": site.base}]
    if theme:
        cookies.append({"name": "fs-theme", "value": theme, "url": site.base})
    await context.add_cookies(cookies)
    page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.route("**/console/v1/**", stub.route)
    await page.goto(f"{site.base}{path}")
    try:
        yield page
        assert not errors, errors
        assert not stub.unexpected, stub.unexpected
        # Google calls are only for tests that serve them; the others must make none.
        assert stub.serves_google or not stub.google_calls, stub.google_calls
    finally:
        await context.close()


async def to_tiktok(page: Page) -> list[str]:
    """TikTok is never contacted: its authorize host is answered here."""
    reached: list[str] = []

    async def tiktok(route: Any) -> None:
        reached.append(route.request.url)
        await route.fulfill(status=200, content_type="text/html", body="<p>stub</p>")

    await page.route("https://services.tiktokshop.com/**", tiktok)
    return reached


def shop(page: Page, store_id: str = STORE_ID) -> Any:
    return page.get_by_test_id(f"connections-tiktok-{store_id}")


async def test_admin_connects_tiktok_and_goes_to_the_authorize_url(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub(stores=[])
        async with connections_page(browser, site, stub) as page:
            reached = await to_tiktok(page)
            connect = page.get_by_test_id("connections-tiktok-connect")
            await expect(connect).to_be_enabled(timeout=WAIT)
            await expect(page.get_by_test_id("connections-tiktok-new-word")).to_have_text(
                "Not connected"
            )
            await expect(page.get_by_test_id("connections-tiktok-new-sentence")).to_have_text(
                "Connect your TikTok Shop to sync its orders and products."
            )
            await expect(page.get_by_test_id("connections-tiktok-new-note")).to_have_count(0)
            await connect.click()
            await page.wait_for_url(AUTHORIZE_URL, timeout=WAIT)
            assert reached == [AUTHORIZE_URL]
            assert stub.connect_calls == [{"body": {}, "client_id": None}]
            # `next dev` runs effects twice (strict mode): the read may repeat, never differ.
            assert stub.store_queries
            assert all(query == {"platform": ["tiktok_shop"]} for query in stub.store_queries)

    await with_browser(body)


async def test_an_address_that_is_not_tiktoks_is_never_followed(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub(
            stores=[], connect_data={"authorization_url": "https://evil.example/open/authorize"}
        )
        async with connections_page(browser, site, stub) as page:
            reached: list[str] = []

            async def elsewhere(route: Any) -> None:
                reached.append(route.request.url)
                await route.abort()

            await page.route("https://evil.example/**", elsewhere)
            await page.get_by_test_id("connections-tiktok-connect").click(timeout=WAIT)
            await expect(page.get_by_test_id("connections-tiktok-problem")).to_have_text(
                "We couldn't start the connection. Try again in a moment."
            )
            await expect(page.get_by_test_id("connections-tiktok-connect")).to_be_enabled()
            assert reached == []
            assert page.url.endswith("/portal/connections")

    await with_browser(body)


async def test_reconnect_and_disconnect_from_the_card(site: Site) -> None:
    async def body(browser: Any) -> None:
        # Disconnect asks first; only the second answer goes through.
        stub = Stub()
        async with connections_page(browser, site, stub) as page:
            asked: list[str] = []
            answers = [False, True]

            async def reply(dialog: Any) -> None:
                asked.append(dialog.message)
                if answers.pop(0):
                    await dialog.accept()
                else:
                    await dialog.dismiss()

            page.on("dialog", lambda dialog: asyncio.ensure_future(reply(dialog)))
            await expect(page.get_by_test_id(f"connections-tiktok-{STORE_ID}-word")).to_have_text(
                "Connected", timeout=WAIT
            )
            await expect(page.get_by_test_id(f"connections-tiktok-{STORE_ID}-last")).to_have_text(
                "Synced 3 min ago"
            )
            await expect(
                page.get_by_test_id("connections-tiktok-reconnect-" + STORE_ID)
            ).to_have_count(0)
            button = page.get_by_test_id(f"connections-tiktok-disconnect-{STORE_ID}")
            await button.click()
            await until(lambda: len(asked) == 1)
            await asyncio.sleep(0.3)
            assert stub.disconnect_calls == []
            await button.click()
            await until(lambda: len(stub.disconnect_calls) == 1)
            assert (
                asked
                == [
                    "Stop syncing this shop? Orders stay visible for 30 days, then customer details are removed."  # noqa: E501
                ]
                * 2
            )
            await expect(page.get_by_test_id(f"connections-tiktok-{STORE_ID}-word")).to_have_text(
                "Disconnected"
            )
            await expect(
                page.get_by_test_id(f"connections-tiktok-reconnect-{STORE_ID}")
            ).to_be_visible()
            await expect(
                page.get_by_test_id(f"connections-tiktok-disconnect-{STORE_ID}")
            ).to_have_count(0)
            await expect(
                page.get_by_test_id(f"connections-tiktok-{STORE_ID}-sentence")
            ).to_contain_text("Customer details are removed 30 days")

        # An expired shop offers Reconnect only, and no second Connect beside it.
        stub = Stub(stores=[make_store("expired")])
        async with connections_page(browser, site, stub) as page:
            reached = await to_tiktok(page)
            await expect(page.get_by_test_id(f"connections-tiktok-{STORE_ID}-word")).to_have_text(
                "Needs reconnecting", timeout=WAIT
            )
            await expect(
                page.get_by_test_id(f"connections-tiktok-disconnect-{STORE_ID}")
            ).to_have_count(0)
            await expect(page.get_by_test_id("connections-tiktok-new")).to_have_count(0)
            await page.get_by_test_id(f"connections-tiktok-reconnect-{STORE_ID}").click()
            await page.wait_for_url(AUTHORIZE_URL, timeout=WAIT)
            assert reached == [AUTHORIZE_URL]
            assert stub.connect_calls == [{"body": {"store_id": STORE_ID}, "client_id": None}]

        # A shop waiting for TikTok's approval reads so, with no last activity.
        stub = Stub(stores=[make_store("missing_shop_id", shop_id=None)])
        async with connections_page(browser, site, stub) as page:
            await expect(page.get_by_test_id(f"connections-tiktok-{STORE_ID}-word")).to_have_text(
                "Waiting for TikTok approval", timeout=WAIT
            )
            await expect(page.get_by_test_id(f"connections-tiktok-{STORE_ID}-last")).to_have_count(
                0
            )
            await expect(shop(page)).to_contain_text("Store ID not recorded")

    await with_browser(body)


async def test_tiktok_off_disables_connect_and_says_why(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub(stores=[], connect_status=503, connect_data={"detail": "tiktok_unavailable"})
        async with connections_page(browser, site, stub) as page:
            connect = page.get_by_test_id("connections-tiktok-connect")
            await connect.click(timeout=WAIT)
            await expect(page.get_by_test_id("connections-tiktok-problem")).to_have_text(
                UNAVAILABLE
            )
            await expect(connect).to_be_disabled()
            assert len(stub.connect_calls) == 1

    await with_browser(body)


async def test_staff_see_the_shop_status_with_connect_disabled(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub(
            stores=[
                make_store(can_manage=False),
                make_store("expired", store_id=SECOND_ID, shop_id="SHOP-B", can_manage=False),
            ]
        )
        async with connections_page(browser, site, stub, role="tenant_staff") as page:
            await expect(page.get_by_test_id(f"connections-tiktok-{STORE_ID}-word")).to_have_text(
                "Connected", timeout=WAIT
            )
            await expect(page.get_by_test_id(f"connections-tiktok-{SECOND_ID}-word")).to_have_text(
                "Needs reconnecting"
            )
            await expect(page.get_by_test_id("connections-tiktok-note")).to_have_text(STAFF_NOTE)
            await expect(
                page.get_by_role("button", name=re.compile("Reconnect|Disconnect"))
            ).to_have_count(0)
            assert not stub.connect_calls and not stub.disconnect_calls

        # With only a connected shop the Connect button is there, and off.
        stub = Stub(stores=[make_store(can_manage=False)])
        async with connections_page(browser, site, stub, role="tenant_staff") as page:
            connect = page.get_by_test_id("connections-tiktok-connect")
            await expect(connect).to_be_visible(timeout=WAIT)
            await expect(connect).to_be_disabled()
            await expect(page.get_by_test_id("connections-tiktok-note")).to_have_text(STAFF_NOTE)

        # No shop yet: the same button and the same line.
        stub = Stub(stores=[])
        async with connections_page(browser, site, stub, role="tenant_staff") as page:
            await expect(page.get_by_test_id("connections-tiktok-connect")).to_be_disabled(
                timeout=WAIT
            )
            await expect(page.get_by_test_id("connections-tiktok-new-note")).to_have_text(
                STAFF_NOTE
            )

        # A read-only member: a disabled button and the role line, and no stores read at all.
        stub = Stub()
        async with connections_page(browser, site, stub, role="read_only") as page:
            await expect(page.get_by_test_id("connections-tiktok-connect")).to_be_disabled(
                timeout=WAIT
            )
            await expect(page.get_by_test_id("connections-tiktok-new-note")).to_have_text(
                "Your role can't connect this."
            )
            await expect(page.get_by_test_id("connections-tiktok-new-word")).to_have_count(0)
            await asyncio.sleep(0.5)
            assert stub.store_queries == []

    await with_browser(body)


async def test_without_marketplaces_the_card_is_locked_and_reads_no_stores(site: Site) -> None:
    async def body(browser: Any) -> None:
        for role in ("tenant_admin", "tenant_staff"):
            # Another package on, so Google stays locked and only the TikTok card is judged.
            stub = Stub(packages=[{"key": "customer_support", "status": "active"}])
            async with connections_page(browser, site, stub, role=role) as page:
                await expect(page.get_by_test_id("connections-tiktok-locked")).to_have_text(
                    "Included in the Marketplaces add-on.", timeout=WAIT
                )
                await expect(page.get_by_test_id("connections-tiktok-connect")).to_have_count(0)
                await asyncio.sleep(0.5)
                assert stub.store_queries == []

        # A suspended package locks the card as well.
        stub = Stub(packages=[{"key": "marketplaces", "status": "suspended"}])
        async with connections_page(browser, site, stub) as page:
            await expect(page.get_by_test_id("connections-tiktok-locked")).to_be_visible(
                timeout=WAIT
            )
            await asyncio.sleep(0.5)
            assert stub.store_queries == []

        # The packages read failing draws the card unlocked: the server's own gates still refuse.
        # Unlocked also means the Google rows read; they are served, TikTok is what is judged.
        stub = Stub(packages_fail=True, serves_google=True)
        async with connections_page(browser, site, stub) as page:
            await expect(page.get_by_test_id(f"connections-tiktok-{STORE_ID}-word")).to_have_text(
                "Connected", timeout=WAIT
            )
            await expect(page.get_by_test_id("connections-tiktok-locked")).to_have_count(0)

    await with_browser(body)


@pytest.mark.parametrize("outcome", ["connected", *REASON_SENTENCES])
async def test_each_tiktok_return_says_its_sentence_once_and_cleans_the_address(
    site: Site, outcome: str
) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        if outcome == "connected":
            query, sentence = "tiktok=connected", CONNECTED_SENTENCE
        else:
            query, sentence = f"tiktok=failed&reason={outcome}", REASON_SENTENCES[outcome]
        async with connections_page(
            browser, site, stub, path=f"/portal/connections?{query}"
        ) as page:
            result = page.get_by_test_id("connections-result-tiktok")
            await expect(result).to_have_text(sentence, timeout=WAIT)
            await expect(page).to_have_url(re.compile(r"/portal/connections$"), timeout=WAIT)
            # The line stays once the address is clean, and it is said once.
            await expect(result).to_be_visible()
            await expect(page.get_by_text(sentence, exact=True)).to_have_count(1)
            await expect(page.get_by_test_id(f"connections-tiktok-{STORE_ID}")).to_be_visible()
            if outcome == "tiktok_unavailable":
                await expect(page.get_by_test_id("connections-tiktok-connect")).to_be_disabled()
            else:
                await expect(page.get_by_test_id("connections-tiktok-connect")).to_be_enabled()

    await with_browser(body)


async def test_a_tiktok_return_shows_even_when_the_card_is_locked(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub(packages=[])
        async with connections_page(
            browser, site, stub, path="/portal/connections?tiktok=failed&reason=provider_denied"
        ) as page:
            await expect(page.get_by_test_id("connections-result-tiktok")).to_have_text(
                REASON_SENTENCES["provider_denied"], timeout=WAIT
            )
            await expect(page.get_by_test_id("connections-tiktok-locked")).to_be_visible()
            await expect(page).to_have_url(re.compile(r"/portal/connections$"), timeout=WAIT)
            assert stub.store_queries == []

    await with_browser(body)


async def test_platform_admin_return_keeps_the_client_and_reconnect_sends_it(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub(stores=[make_store("disconnected", shop_id="SHOP-A")])
        async with connections_page(
            browser,
            site,
            stub,
            path=f"/connections?client_id={TENANT}&tiktok=connected",
            role="platform_admin",
        ) as page:
            await expect(page.get_by_test_id("connections-result-tiktok")).to_have_text(
                CONNECTED_SENTENCE, timeout=WAIT
            )
            await expect(page).to_have_url(
                re.compile(rf"/connections\?client_id={TENANT}$"), timeout=WAIT
            )
            # No Google card for a platform admin, and the packages and stores are the company's.
            await expect(page.get_by_test_id("connections-group-you")).to_have_count(0)
            reconnect = page.get_by_test_id(f"connections-tiktok-reconnect-{STORE_ID}")
            await expect(reconnect).to_be_visible()
            await to_tiktok(page)
            await reconnect.click()
            await page.wait_for_url(AUTHORIZE_URL, timeout=WAIT)
            assert stub.connect_calls == [{"body": {"store_id": STORE_ID}, "client_id": [TENANT]}]
            assert stub.store_queries
            assert all(
                query == {"client_id": [TENANT], "platform": ["tiktok_shop"]}
                for query in stub.store_queries
            )
            assert stub.package_queries
            assert all(query == {"client_id": [TENANT]} for query in stub.package_queries)

    await with_browser(body)


async def test_platform_admin_before_choosing_a_company_shows_the_prompt_and_no_cards(
    site: Site,
) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with connections_page(
            browser, site, stub, path="/connections", role="platform_admin"
        ) as page:
            await expect(page.get_by_text("Select a tenant to see its connections.")).to_be_visible(
                timeout=WAIT
            )
            await asyncio.sleep(1)
            await expect(page.get_by_test_id("connections-card-tiktok")).to_have_count(0)
            await expect(page.get_by_test_id("connections-tiktok-locked")).to_have_count(0)
            assert stub.package_queries == [] and stub.store_queries == []

            # Choosing a company reads its packages and stores and draws the card.
            await page.get_by_test_id("connections-tenant").select_option(TENANT)
            await expect(page.get_by_test_id(f"connections-tiktok-{STORE_ID}-word")).to_have_text(
                "Connected", timeout=WAIT
            )
            await expect(page).to_have_url(re.compile(rf"/connections\?client_id={TENANT}$"))
            assert stub.package_queries
            assert all(query == {"client_id": [TENANT]} for query in stub.package_queries)

    await with_browser(body)


# --- The Google card (Calendar and Gmail) ------------------------------------------------------


async def overflow(page: Page) -> dict[str, int]:
    return await page.evaluate(
        "() => ({scroll: document.documentElement.scrollWidth, inner: window.innerWidth})"
    )


async def to_google(page: Page) -> list[str]:
    """Google is never contacted: its authorize host is answered here."""
    reached: list[str] = []

    async def google(route: Any) -> None:
        reached.append(route.request.url)
        await route.fulfill(status=200, content_type="text/html", body="<p>stub</p>")

    await page.route("https://accounts.google.com/**", google)
    return reached


def answer_dialogs(page: Page, answers: list[bool]) -> list[str]:
    """Answer each browser confirm in turn with the given yes or no; returns what was asked."""
    asked: list[str] = []

    async def reply(dialog: Any) -> None:
        asked.append(dialog.message)
        if answers.pop(0):
            await dialog.accept()
        else:
            await dialog.dismiss()

    page.on("dialog", lambda dialog: asyncio.ensure_future(reply(dialog)))
    return asked


def calendar_calls(stub: Stub, method: str, path: str) -> int:
    return stub.google_calls.count(f"{method} bookings/calendar/{path}")


async def test_a_member_connects_and_disconnects_google_calendar(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub(packages=EVERYTHING, serves_google=True)
        async with connections_page(browser, site, stub, role="tenant_staff") as page:
            reached = await to_google(page)
            await expect(page.get_by_test_id("connections-calendar-word")).to_have_text(
                "Not connected", timeout=WAIT
            )
            await expect(page.get_by_test_id("connections-calendar-sentence")).to_contain_text(
                "Your confirmed bookings appear on a calendar called Bookings"
            )
            await expect(page.get_by_test_id("connections-calendar-reconnect")).to_have_count(0)
            await expect(page.get_by_test_id("connections-calendar-disconnect")).to_have_count(0)
            await page.get_by_test_id("connections-calendar-connect").click()
            await page.wait_for_url(GOOGLE_URL, timeout=WAIT)
            assert reached == [GOOGLE_URL]
            assert calendar_calls(stub, "POST", "connect") == 1

        stub = Stub(packages=EVERYTHING, serves_google=True, calendar=calendar_status("connected"))
        async with connections_page(browser, site, stub, role="tenant_staff") as page:
            asked = answer_dialogs(page, [False, True])
            await expect(page.get_by_test_id("connections-calendar-word")).to_have_text(
                "Connected", timeout=WAIT
            )
            await expect(page.get_by_test_id("connections-calendar-connect")).to_have_count(0)
            button = page.get_by_test_id("connections-calendar-disconnect")
            await button.click()
            await until(lambda: len(asked) == 1)
            await asyncio.sleep(0.3)
            assert calendar_calls(stub, "POST", "disconnect") == 0
            await button.click()
            await until(lambda: calendar_calls(stub, "POST", "disconnect") == 1)
            assert asked == [CALENDAR_CONFIRM] * 2
            await expect(page.get_by_test_id("connections-calendar-word")).to_have_text(
                "Not connected"
            )
            await expect(page.get_by_test_id("connections-calendar-connect")).to_be_visible()
            await expect(page.get_by_test_id("connections-calendar-disconnect")).to_have_count(0)

        # Google refusing to start the connection shows the sentence and leaves the button usable.
        stub = Stub(
            packages=EVERYTHING,
            serves_google=True,
            calendar_connect_status=409,
            calendar_connect_data={"detail": "person_required"},
        )
        async with connections_page(browser, site, stub, role="tenant_staff") as page:
            await page.get_by_test_id("connections-calendar-connect").click(timeout=WAIT)
            await expect(page.get_by_test_id("connections-calendar-problem")).to_be_visible()
            await expect(page.get_by_test_id("connections-calendar-connect")).to_be_enabled()
            assert page.url.endswith("/portal/connections")

    await with_browser(body)


async def test_needs_reconnect_offers_reconnect_with_the_banner_sentence(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub(
            packages=EVERYTHING, serves_google=True, calendar=calendar_status("needs_reconnect")
        )
        async with connections_page(browser, site, stub) as page:
            reached = await to_google(page)
            await expect(page.get_by_test_id("connections-calendar-word")).to_have_text(
                "Needs reconnecting", timeout=WAIT
            )
            await expect(page.get_by_test_id("connections-calendar-sentence")).to_have_text(BANNER)
            await expect(page.get_by_test_id("connections-calendar-connect")).to_have_count(0)
            await expect(page.get_by_test_id("connections-calendar-disconnect")).to_be_visible()
            await page.get_by_test_id("connections-calendar-reconnect").click()
            await page.wait_for_url(GOOGLE_URL, timeout=WAIT)
            assert reached == [GOOGLE_URL]
            assert calendar_calls(stub, "POST", "connect") == 1

    await with_browser(body)


async def test_calendar_off_or_not_linked_says_so_without_buttons(site: Site) -> None:
    async def body(browser: Any) -> None:
        cases = [
            (
                calendar_status(None, available=False, can_connect=False),
                "Not switched on",
                "Google Calendar isn't switched on for this company yet. Ask your Admin.",
            ),
            (
                calendar_status(None, can_connect=False),
                "Not available",
                "Your account isn't linked to a person in Bookings yet. "
                "Ask your Admin to add you on People & rooms.",
            ),
        ]
        for status, word, sentence in cases:
            stub = Stub(packages=EVERYTHING, serves_google=True, calendar=status)
            async with connections_page(browser, site, stub) as page:
                await expect(page.get_by_test_id("connections-calendar-word")).to_have_text(
                    word, timeout=WAIT
                )
                await expect(page.get_by_test_id("connections-calendar-sentence")).to_have_text(
                    sentence
                )
                google = page.get_by_test_id("connections-card-google")
                # Only Connect Gmail is left in the card: the Calendar row has no button.
                await expect(google.get_by_role("button")).to_have_count(1)
                await expect(page.get_by_test_id("connections-calendar-connect")).to_have_count(0)

    await with_browser(body)


async def test_a_member_connects_and_disconnects_gmail_after_asking(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub(packages=EVERYTHING, serves_google=True)
        async with connections_page(browser, site, stub, role="tenant_staff") as page:
            reached = await to_google(page)
            await expect(page.get_by_test_id("connections-gmail-word")).to_have_text(
                "Not connected", timeout=WAIT
            )
            await expect(page.get_by_test_id("connections-gmail-sentence")).to_have_text(
                "No mailbox connected for your account."
            )
            await page.get_by_test_id("connections-gmail-connect").click()
            await page.wait_for_url(GOOGLE_URL, timeout=WAIT)
            assert reached == [GOOGLE_URL]
            assert stub.google_calls.count("POST email/mailbox/connect") == 1

        stub = Stub(packages=EVERYTHING, serves_google=True, email=email_context("connected"))
        async with connections_page(browser, site, stub, role="tenant_staff") as page:
            await expect(page.get_by_test_id("connections-gmail-word")).to_have_text(
                "Connected", timeout=WAIT
            )
            await expect(page.get_by_test_id("connections-gmail-sentence")).to_have_text(
                f"Your Gmail {ADDRESS} is connected to the Email assistant."
            )
            await expect(page.get_by_test_id("connections-gmail-last")).to_contain_text(
                "Last checked"
            )
            await expect(page.get_by_test_id("connections-gmail-connect")).to_have_count(0)
            await page.get_by_test_id("connections-gmail-disconnect").click()
            await expect(page.get_by_test_id("connections-gmail-disconnect-ask")).to_contain_text(
                GMAIL_QUESTION
            )
            # Cancel asks nothing of the server.
            await page.get_by_test_id("connections-gmail-disconnect-cancel").click()
            await asyncio.sleep(0.3)
            assert "POST email/mailbox/disconnect" not in stub.google_calls
            await page.get_by_test_id("connections-gmail-disconnect").click()
            await page.get_by_test_id("connections-gmail-disconnect-confirm").click()
            await expect(page.get_by_test_id("connections-gmail-message")).to_have_text(
                GMAIL_DISCONNECTED
            )
            assert stub.google_calls.count("POST email/mailbox/disconnect") == 1
            await expect(page.get_by_test_id("connections-gmail-word")).to_have_text(
                "Not connected"
            )
            await expect(page.get_by_test_id("connections-gmail-connect")).to_be_visible()
            await expect(page.get_by_test_id("connections-gmail-disconnect")).to_have_count(0)

        # This server has no Google client: the existing sentence under a disabled Connect.
        stub = Stub(packages=EVERYTHING, serves_google=True, email=email_context(configured=False))
        async with connections_page(browser, site, stub) as page:
            await expect(page.get_by_test_id("connections-gmail-connect")).to_be_disabled(
                timeout=WAIT
            )
            await expect(page.get_by_test_id("connections-gmail-note")).to_have_text(
                "Connecting Gmail isn't available yet. Contact fourstacks support."
            )

        # Switched off for the company: the word and the sentence, and nothing to press.
        stub = Stub(packages=EVERYTHING, serves_google=True, email=email_context(enabled=False))
        async with connections_page(browser, site, stub) as page:
            await expect(page.get_by_test_id("connections-gmail-word")).to_have_text(
                "Switched off", timeout=WAIT
            )
            await expect(page.get_by_test_id("connections-gmail-sentence")).to_have_text(
                "The email assistant is switched off for your company."
            )

    await with_browser(body)


CALENDAR_RETURNS = {
    "calendar=connected": "Google Calendar is connected. Your confirmed bookings will appear on a "
    "Bookings calendar in your Google account.",
    "calendar_error=oauth_state_invalid": "That connection attempt expired or was already used. "
    "Try connecting again.",
    "calendar_error=provider_denied": "Google didn't give access, so nothing was connected. "
    "Try again and choose Allow.",
    "calendar_error=oauth_issuer_invalid": "Google's answer couldn't be checked, so nothing was "
    "connected. Try again.",
    "calendar_error=scopes_not_granted": "Google didn't allow access to the calendar this makes. "
    "Try again and allow it.",
    "calendar_error=calendar_unavailable": "Google Calendar isn't switched on for this company "
    "yet. Ask your Admin.",
    "calendar_error=something_new": "Google Calendar could not be connected. Try again.",
}
GMAIL_RETURNS = {
    "connected=1": "Mailbox connected.",
    "error=oauth_state_invalid": "The sign-in link expired or was already used. "
    "Start the connection again.",
    "error=something_new": "Gmail could not be connected. Try again.",
}


async def test_each_google_return_says_its_sentence_once_and_cleans_the_address(
    site: Site,
) -> None:
    async def body(browser: Any) -> None:
        for kind, returns in (("calendar", CALENDAR_RETURNS), ("gmail", GMAIL_RETURNS)):
            for query, sentence in returns.items():
                stub = Stub(packages=EVERYTHING, serves_google=True)
                async with connections_page(
                    browser, site, stub, path=f"/portal/connections?{query}"
                ) as page:
                    result = page.get_by_test_id(f"connections-result-{kind}")
                    await expect(result).to_have_text(sentence, timeout=WAIT)
                    await expect(page).to_have_url(
                        re.compile(r"/portal/connections$"), timeout=WAIT
                    )
                    # The line stays once the address is clean, and it is said once.
                    await expect(result).to_be_visible()
                    await expect(page.get_by_text(sentence, exact=True)).to_have_count(1)
                    await expect(page.get_by_test_id(f"connections-{kind}-word")).to_be_visible()

        # A return shows even when its card is locked.
        stub = Stub(packages=MARKETPLACES)
        async with connections_page(
            browser, site, stub, path="/portal/connections?calendar_error=provider_denied"
        ) as page:
            await expect(page.get_by_test_id("connections-result-calendar")).to_have_text(
                CALENDAR_RETURNS["calendar_error=provider_denied"], timeout=WAIT
            )
            await expect(page.get_by_test_id("connections-calendar-locked")).to_be_visible()

    await with_browser(body)


async def test_read_only_sees_disabled_buttons_and_reads_no_status(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub(packages=EVERYTHING)
        async with connections_page(browser, site, stub, role="read_only") as page:
            for kind, label in (
                ("calendar", "Connect Google Calendar"),
                ("gmail", "Connect Gmail"),
            ):
                button = page.get_by_test_id(f"connections-{kind}-connect")
                await expect(button).to_be_disabled(timeout=WAIT)
                await expect(button).to_have_text(label)
                await expect(page.get_by_test_id(f"connections-{kind}-note")).to_have_text(
                    "Your role can't connect this."
                )
                await expect(page.get_by_test_id(f"connections-{kind}-word")).to_have_count(0)
            await asyncio.sleep(0.8)
            # No calendar, email or stores read at all (the stub fails the test on any Google call).
            assert stub.google_calls == [] and stub.store_queries == []

    await with_browser(body)


async def test_platform_admin_sees_only_your_business_and_reads_no_google_status(
    site: Site,
) -> None:
    async def body(browser: Any) -> None:
        stub = Stub(packages=EVERYTHING)
        async with connections_page(
            browser, site, stub, path=f"/connections?client_id={TENANT}", role="platform_admin"
        ) as page:
            await expect(page.get_by_test_id("connections-group-business")).to_be_visible(
                timeout=WAIT
            )
            await expect(page.get_by_test_id(f"connections-tiktok-{STORE_ID}-word")).to_be_visible()
            await expect(page.get_by_test_id("connections-group-you")).to_have_count(0)
            await expect(page.get_by_test_id("connections-card-google")).to_have_count(0)
            await expect(page.get_by_test_id("connections-calendar")).to_have_count(0)
            await expect(page.get_by_test_id("connections-gmail")).to_have_count(0)
            await asyncio.sleep(0.8)
            assert stub.google_calls == []

    await with_browser(body)


async def test_locked_google_rows_name_their_package_and_read_nothing(site: Site) -> None:
    async def body(browser: Any) -> None:
        # Neither package: both rows say which package, and nothing is read for them.
        for role in ("tenant_admin", "tenant_staff", "read_only"):
            stub = Stub(packages=MARKETPLACES)
            async with connections_page(browser, site, stub, role=role) as page:
                await expect(page.get_by_test_id("connections-calendar-locked")).to_have_text(
                    "Included in the Bookings package.", timeout=WAIT
                )
                await expect(page.get_by_test_id("connections-gmail-locked")).to_have_text(
                    "Included in the Staff email package."
                )
                await expect(page.get_by_test_id("connections-calendar-connect")).to_have_count(0)
                await expect(page.get_by_test_id("connections-gmail-connect")).to_have_count(0)
                await asyncio.sleep(0.5)
                assert stub.google_calls == []

        # Only Bookings: the calendar reads its status, the locked Gmail row reads nothing.
        stub = Stub(
            packages=[*MARKETPLACES, {"key": "bookings", "status": "active"}], serves_google=True
        )
        async with connections_page(browser, site, stub) as page:
            await expect(page.get_by_test_id("connections-calendar-word")).to_have_text(
                "Not connected", timeout=WAIT
            )
            await expect(page.get_by_test_id("connections-gmail-locked")).to_be_visible()
            await asyncio.sleep(0.5)
            assert stub.google_paths() == {"GET bookings/calendar/status"}

        # Only Staff email, suspended Bookings: the reverse.
        stub = Stub(
            packages=[
                *MARKETPLACES,
                {"key": "bookings", "status": "suspended"},
                {"key": "staff_email", "status": "active"},
            ],
            serves_google=True,
        )
        async with connections_page(browser, site, stub) as page:
            await expect(page.get_by_test_id("connections-gmail-word")).to_have_text(
                "Not connected", timeout=WAIT
            )
            await expect(page.get_by_test_id("connections-calendar-locked")).to_be_visible()
            await asyncio.sleep(0.5)
            assert stub.google_paths() == {"GET email/context"}

    await with_browser(body)


async def test_an_admin_sees_the_team_link_to_people_and_rooms(site: Site) -> None:
    async def body(browser: Any) -> None:
        people = [
            {
                "person_id": "33333333-3333-4333-8333-333333333333",
                "name": "Ada",
                "status": "connected",
            }
        ]
        stub = Stub(
            packages=EVERYTHING, serves_google=True, calendar=calendar_status(people=people)
        )
        async with connections_page(browser, site, stub) as page:
            link = page.get_by_test_id("connections-calendar-people")
            await expect(link).to_have_text("See your team's calendar connections", timeout=WAIT)
            await expect(link).to_have_attribute("href", "/portal/bookings/people-and-rooms")

        # A member's status has no team list, so no link.
        stub = Stub(packages=EVERYTHING, serves_google=True)
        async with connections_page(browser, site, stub, role="tenant_staff") as page:
            await expect(page.get_by_test_id("connections-calendar-word")).to_be_visible(
                timeout=WAIT
            )
            await expect(page.get_by_test_id("connections-calendar-people")).to_have_count(0)

    await with_browser(body)


@pytest.mark.parametrize("theme", THEMES)
async def test_the_tab_fits_320_390_and_768_in_three_themes(site: Site, theme: str) -> None:
    async def body(browser: Any) -> None:
        # The longest things a row can say: a long mailbox address, a team link, every card drawn.
        long_address = "a-very-long-synthetic-mailbox-name-for-the-fit-check@subdomain.example.test"
        people = [
            {
                "person_id": "33333333-3333-4333-8333-333333333333",
                "name": "Ada",
                "status": "connected",
            }
        ]
        stub = Stub(
            packages=EVERYTHING,
            serves_google=True,
            calendar=calendar_status("needs_reconnect", people=people),
            email=email_context("connected", address=long_address),
        )
        for width in (320, 390, 768):
            async with connections_page(
                browser, site, stub, width=width, theme=theme, role="tenant_admin"
            ) as page:
                await expect(page.get_by_test_id("connections-gmail-word")).to_have_text(
                    "Connected", timeout=WAIT
                )
                await expect(page.get_by_test_id("connections-calendar-reconnect")).to_be_visible()
                await expect(
                    page.get_by_test_id(f"connections-tiktok-{STORE_ID}-word")
                ).to_be_visible()
                measured = await overflow(page)
                assert measured["scroll"] <= measured["inner"], (theme, width, measured)
                # The disconnect question opens inside the card, still without sideways scrolling.
                await page.get_by_test_id("connections-gmail-disconnect").click()
                await expect(
                    page.get_by_test_id("connections-gmail-disconnect-ask")
                ).to_be_visible()
                measured = await overflow(page)
                assert measured["scroll"] <= measured["inner"], ("ask", theme, width, measured)

    await with_browser(body)
