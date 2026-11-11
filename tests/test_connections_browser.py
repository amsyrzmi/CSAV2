"""Real browser acceptance for the Connections tab (TikTok Shop card).

The real Next.js shell runs unmodified from the disposable staged copy that
`test_services_redesign_browser.site` builds. Its layouts fetch `/auth/me` and `/services/active`
server-side, so the loopback stub from `test_services_browser` answers those; every browser-side
`/console/v1/*` call is answered by Playwright from `Stub`, which records the stores, packages and
Google reads and fails on anything it does not know. The browser clock is fixed (Date only, timers
keep running) so the "Synced 3 min ago" line is exact.
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
    # Calendar and email status reads: none of them belongs to the TikTok card.
    google_calls: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)

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
            self.google_calls.append(f"{request.method} {path}")
            status, data = 404, {"detail": "not stubbed"}
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
) -> AsyncIterator[Page]:
    site.upstream.me = make_me(role)
    site.upstream.packages = list(stub.packages)
    context = await browser.new_context(viewport={"width": width, "height": 1000}, timezone_id=ZONE)
    context.set_default_navigation_timeout(WAIT)
    context.set_default_timeout(15_000)
    await context.clock.set_fixed_time(NOW)
    await context.add_cookies(
        [{"name": "csa_session", "value": "synthetic-session", "url": site.base}]
    )
    page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.route("**/console/v1/**", stub.route)
    await page.goto(f"{site.base}{path}")
    try:
        yield page
        assert not errors, errors
        assert not stub.unexpected, stub.unexpected
        assert not stub.google_calls, stub.google_calls
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
            stub = Stub(packages=[{"key": "bookings", "status": "active"}])
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
        stub = Stub(packages_fail=True)
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
