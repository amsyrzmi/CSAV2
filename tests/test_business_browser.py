"""Real browser acceptance for the plain Business management page.

The real Next.js shell runs unmodified from the disposable staged copy that
`test_services_redesign_browser.site` builds. Its layouts fetch `/auth/me` and
`/services/active` server-side, so the loopback stub from `test_services_browser` answers those;
every browser-side `/console/v1/*` call is answered by Playwright from `Stub`, which applies the
status and payment filters and the keyset paging of the real order read and records the query
string of every orders and counts request. The browser clock is fixed (Date only, timers keep
running) so the calendar and the default 30-day counts window are exact.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest
from playwright.async_api import Page, expect

from tests import test_services_redesign_browser as services
from tests.test_services_browser import TENANT, TENANT_PROFILE, WAIT, make_me
from tests.test_services_redesign_browser import Site, open_event, with_browser

pytestmark = pytest.mark.asyncio

# The staged copy of the console is built once per file by the services fixture.
site = services.site

THEMES = ("cobalt", "night", "bureau")
WIDTHS = (320, 390, 768, 1024, 1440)
ZONE = "Asia/Kuala_Lumpur"
TODAY = date(2026, 11, 2)
NOW = datetime(2026, 11, 2, 10, 0, tzinfo=ZoneInfo(ZONE))
STATUSES = (
    "draft",
    "pending_confirmation",
    "confirmed",
    "processing",
    "shipped",
    "delivered",
    "cancelled",
    "refunded",
)
PAYMENTS = ("unpaid", "cod_pending", "paid", "refunded", "failed")
CHANNELS = ("tiktok_shop", "shopee", "web", "telegram", "manual")
ORDERS_TOTAL = 25
PAGE_SIZE = 20


def instant(day: date) -> str:
    """The local start of `day` as the browser's `toISOString()` writes it."""
    start = datetime(day.year, day.month, day.day, tzinfo=ZoneInfo(ZONE))
    return start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def oid(n: int) -> str:
    return f"{n:08x}-0000-4000-8000-000000000000"


def make_order(n: int) -> dict[str, Any]:
    """Order 1 is the newest. Statuses and payments cycle, so every status has orders."""
    channel = CHANNELS[n % 5]
    status = STATUSES[n % 8]
    created = datetime(2026, 11, 2, 1, 59, tzinfo=UTC) - timedelta(minutes=n)
    return {
        "id": oid(n),
        "status": status,
        "payment_status": PAYMENTS[(n - 1) % 5],
        "total": f"{1000 + n}.50",
        "currency": "USD" if n == 2 else "MYR",
        "channel": channel,
        "external_order_id": f"EXT-{n:04d}",
        "external_shop_id": "SHOP-A" if channel in ("tiktok_shop", "shopee") else None,
        "tracking_no": f"TRK{n:05d}" if status == "shipped" else None,
        "carrier": "J&T" if status == "shipped" else None,
        "placed_at": created.isoformat().replace("+00:00", "Z"),
        "shipped_at": created.isoformat().replace("+00:00", "Z") if status == "shipped" else None,
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "updated_at": created.isoformat().replace("+00:00", "Z"),
    }


def make_products(available: tuple[int, ...] = (7, 7)) -> list[dict[str, Any]]:
    return [
        {
            "id": f"00000000-0000-4000-8000-00000000000{n}",
            "sku": f"MUG-{n}",
            "name": f"Blue mug {n}",
            "price": "18.00",
            "currency": "MYR",
            "stock_qty": 10,
            "reserved_qty": 3,
            "available_qty": available[n - 1],
            "is_active": n == 1,
            "created_at": "2026-10-01T00:00:00Z",
            "updated_at": "2026-10-02T00:00:00Z",
        }
        for n in range(1, len(available) + 1)
    ]


def make_store() -> dict[str, Any]:
    return {
        "id": "00000000-0000-4000-8000-0000000000aa",
        "platform": "tiktok_shop",
        "shop_id": "SHOP-A",
        "expires_at": None,
        "updated_at": "2026-10-03T00:00:00Z",
        "expiry": "unknown",
        "authorization": "unverified",
        "permissions": "unknown",
        "provider_reads": "unavailable",
        "connection": {"status": "unverified", "live_integration": "unavailable"},
    }


@dataclass
class Stub:
    """Browser-side answers. `counts_gate` holds the counts read, `counts_fail` makes it fail."""

    orders: list[dict[str, Any]] = field(
        default_factory=lambda: [make_order(n) for n in range(1, ORDERS_TOTAL + 1)]
    )
    counts_fail: bool = False
    # Available quantity of each product, and whether a further products page exists.
    availability: tuple[int, ...] = (7, 7)
    products_more: bool = False
    counts_gate: asyncio.Event = field(default_factory=open_event)
    list_gate: asyncio.Event = field(default_factory=open_event)
    older_gate: asyncio.Event = field(default_factory=open_event)
    hold_unfiltered: bool = False
    # The next N older-page reads (`before=`) answer 503.
    older_failures: int = 0
    order_queries: list[dict[str, list[str]]] = field(default_factory=list)
    counts_queries: list[dict[str, list[str]]] = field(default_factory=list)
    other_calls: list[str] = field(default_factory=list)
    product_queries: list[dict[str, list[str]]] = field(default_factory=list)
    store_queries: list[dict[str, list[str]]] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)

    def filtered(self, query: dict[str, list[str]], *, with_status: bool) -> list[dict[str, Any]]:
        rows = self.orders
        if with_status and query.get("status"):
            rows = [row for row in rows if row["status"] == query["status"][0]]
        if query.get("payment_status"):
            rows = [row for row in rows if row["payment_status"] == query["payment_status"][0]]
        return rows

    def counts(self, query: dict[str, list[str]]) -> dict[str, Any]:
        rows = self.filtered(query, with_status=False)
        return {
            "total": len(rows),
            "by_status": {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES},
        }

    async def route(self, request_route: Any) -> None:
        request = request_route.request
        parts = urlsplit("http://x/" + request.url.split("/console/v1/")[1])
        path = parts.path.lstrip("/")
        query = parse_qs(parts.query, keep_blank_values=True)
        status, data = 200, None
        if path == "auth/csrf":
            data = {"csrf_token": "synthetic-csrf"}
        elif path == "business/orders/counts" and request.method == "GET":
            self.counts_queries.append(query)
            await self.counts_gate.wait()
            if self.counts_fail:
                status, data = 503, {"detail": "synthetic counts outage"}
            else:
                data = self.counts(query)
        elif path == "business/orders" and request.method == "GET":
            self.order_queries.append(query)
            if self.hold_unfiltered and not query.get("status") and not query.get("before"):
                await self.list_gate.wait()
            if query.get("before"):
                await self.older_gate.wait()
                if self.older_failures > 0:
                    self.older_failures -= 1
                    await request_route.fulfill(
                        status=503, json={"detail": "synthetic older outage"}
                    )
                    return
            rows = self.filtered(query, with_status=True)
            if query.get("before"):
                ids = [row["id"] for row in rows]
                rows = rows[ids.index(query["before"][0]) + 1 :]
            page = rows[:PAGE_SIZE]
            more = len(rows) > PAGE_SIZE
            data = {
                "source": "local_records",
                "marketplace_sync": "unavailable",
                "items": page,
                "next_cursor": page[-1]["id"] if more else None,
            }
        elif path == "admin/clients" and request.method == "GET":
            data = [TENANT_PROFILE]
        elif path == "business/products" and request.method == "GET":
            self.other_calls.append("products")
            self.product_queries.append(query)
            items = make_products(self.availability)
            data = {
                "source": "local_catalogue",
                "marketplace_sync": "unavailable",
                "items": items,
                "next_cursor": items[-1]["id"] if self.products_more else None,
            }
        elif path == "business/stores" and request.method == "GET":
            self.other_calls.append("stores")
            self.store_queries.append(query)
            data = {
                "source": "local_credential_records",
                "marketplace_sync": "unavailable",
                "items": [make_store()],
                "next_cursor": None,
            }
        else:
            self.unexpected.append(f"{request.method} {path}")
            status, data = 500, {"detail": "unexpected synthetic call"}
        try:
            await request_route.fulfill(status=status, json=data)
        except Exception:  # noqa: BLE001 - the page aborted this request on a filter change
            pass


@asynccontextmanager
async def business_page(
    browser: Any,
    site: Site,
    stub: Stub,
    *,
    width: int = 1280,
    height: int = 1000,
    theme: str | None = None,
    wait_for_rows: bool = True,
    path: str = "/portal/business",
    role: str = "tenant_admin",
    rail_expanded: bool = False,
) -> AsyncIterator[Page]:
    site.upstream.me = make_me(role)
    site.upstream.packages = [{"key": "orders_products", "status": "active"}]
    context = await browser.new_context(
        viewport={"width": width, "height": height}, timezone_id=ZONE
    )
    context.set_default_navigation_timeout(WAIT)
    context.set_default_timeout(15_000)
    await context.clock.set_fixed_time(NOW)
    cookies = [{"name": "csa_session", "value": "synthetic-session", "url": site.base}]
    if rail_expanded:
        cookies.append({"name": "fs-rail", "value": "expanded", "url": site.base})
    if theme:
        cookies.append({"name": "fs-theme", "value": theme, "url": site.base})
    await context.add_cookies(cookies)
    page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.route("**/console/v1/**", stub.route)
    await page.goto(f"{site.base}{path}")
    if wait_for_rows:
        await expect(order_card(page, 1)).to_be_visible(timeout=WAIT)
    try:
        yield page
        assert not errors, errors
        assert not stub.unexpected, stub.unexpected
    finally:
        await context.close()


async def until(
    check: Any, message: str = "condition not met in time", timeout: float = 10
) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, message
        await asyncio.sleep(0.1)


async def open_fold(page: Page, name: str) -> None:
    """Open one of the closed-by-default folds (`business-<name>`) and wait for its text."""
    await page.get_by_test_id(f"business-{name}-toggle").click()
    await expect(page.get_by_test_id(f"business-{name}-details")).to_be_visible()


async def open_filters(page: Page) -> None:
    """Open the Filters panel (date, payment, source)."""
    if (
        await page.get_by_test_id("business-filters-toggle").get_attribute("aria-expanded")
        != "true"
    ):
        await page.get_by_test_id("business-filters-toggle").click()
    await expect(page.get_by_test_id("business-filters")).to_be_visible()


async def open_statuses(page: Page) -> None:
    """Open the All statuses arrow so the status chips can be used."""
    if (
        await page.get_by_test_id("business-all-statuses-toggle").get_attribute("aria-expanded")
        != "true"
    ):
        await page.get_by_test_id("business-all-statuses-toggle").click()
    await expect(page.get_by_test_id("business-status")).to_be_visible()


async def open_tech(page: Page, test_id: str) -> None:
    """Open a row's Technical details arrow (the row itself must already be open)."""
    await page.get_by_test_id(f"{test_id}-tech-toggle").click()
    await expect(page.get_by_test_id(f"{test_id}-tech-details")).to_be_visible()


def order_card(page: Page, n: int) -> Any:
    return page.get_by_test_id(f"business-order-{oid(n)}")


def cards(page: Page) -> Any:
    return page.get_by_test_id("business-orders-list").locator("> li")


async def wait_for_orders(page: Page, count: int) -> None:
    """The list has settled: no request in flight and `count` order cards on screen."""
    await expect(cards(page)).to_have_count(count)
    await expect(page.get_by_text("Loading local orders...")).to_have_count(0)


async def test_orders_show_cards_and_arrow_details(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with business_page(browser, site, stub) as page:
            # Order 1: pending confirmation, unpaid, from Shopee.
            first = order_card(page, 1)
            await expect(cards(page)).to_have_count(PAGE_SIZE)
            await expect(first.get_by_test_id(f"business-order-{oid(1)}-title")).to_have_text(
                "Order EXT-0001"
            )
            await expect(first).to_contain_text("Shopee")
            await expect(first).to_contain_text("RM 1,001.50")
            await expect(first).to_contain_text("2 Nov 2026, 09:58")
            await expect(first.get_by_test_id(f"business-order-{oid(1)}-status")).to_have_text(
                "Waiting for confirmation"
            )
            await expect(first.get_by_test_id(f"business-order-{oid(1)}-payment")).to_have_text(
                "Not paid yet"
            )
            await expect(order_card(page, 2)).to_contain_text("USD 1,002.50")
            # The raw codes and the original lines sit behind the arrow.
            details = page.get_by_test_id(f"business-order-{oid(1)}-details")
            await expect(details).to_be_hidden()
            await first.get_by_test_id(f"business-order-{oid(1)}-toggle").click()
            await expect(details).to_be_visible()
            # The plain groups come first; the exact lines wait behind Technical details.
            await expect(details).to_contain_text("Total: RM 1,001.50")
            await expect(details).to_contain_text("Courier: not added yet")
            await expect(details).to_contain_text("Source: Shopee")
            await expect(details).to_contain_text("Last changed here: 2 Nov 2026, 09:58")
            raw = page.get_by_test_id(f"business-order-{oid(1)}-tech-details")
            await expect(raw).to_be_hidden()
            await open_tech(page, f"business-order-{oid(1)}")
            await expect(raw).to_contain_text(oid(1))
            await expect(raw).to_contain_text(
                "pending_confirmation · Payment: unpaid · MYR 1001.50"
            )
            await expect(raw).to_contain_text("Source: shopee · Store: SHOP-A")
            await expect(raw).to_contain_text("Platform order: EXT-0001")
            await expect(raw).to_contain_text("Tracking: Not recorded · Carrier: Not recorded")
            await expect(raw).to_contain_text("Created: 2 Nov 2026, 09:58")
            await expect(raw).to_contain_text("Placed: 2 Nov 2026, 09:58")
            await expect(raw).to_contain_text("Shipped: Not recorded")
            await expect(raw).to_contain_text("not a marketplace sync timestamp")
            # A shipped order shows its tracking in its own details, and the line under technical.
            shipped = order_card(page, 4)
            await shipped.get_by_test_id(f"business-order-{oid(4)}-toggle").click()
            await expect(page.get_by_test_id(f"business-order-{oid(4)}-details")).to_contain_text(
                "Tracking number: TRK00004"
            )
            await expect(page.get_by_test_id(f"business-order-{oid(4)}-details")).to_contain_text(
                "Courier: J&T"
            )
            await open_tech(page, f"business-order-{oid(4)}")
            await expect(
                page.get_by_test_id(f"business-order-{oid(4)}-tech-details")
            ).to_contain_text("Tracking: TRK00004 · Carrier: J&T")
            # The long notices and the filters stay folded; products and stores are other pages.
            await expect(page.get_by_test_id("business-more-filters-details")).to_be_hidden()
            await expect(page.get_by_test_id("business-about-details")).to_be_hidden()
            await expect(page.get_by_test_id("business-products-list")).to_have_count(0)
            await expect(page.get_by_test_id("business-stores")).to_have_count(0)
            # Only the out-of-stock tile reads products here: one read of the newest 50.
            assert set(stub.other_calls) == {"products"}, stub.other_calls
            assert all(q == {"limit": ["50"]} for q in stub.product_queries), stub.product_queries

    await with_browser(body)


async def test_products_page_lists_products_with_arrow_details(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with business_page(
            browser, site, stub, path="/portal/business/products", wait_for_rows=False
        ) as page:
            product = page.get_by_test_id("business-product-00000000-0000-4000-8000-000000000001")
            await expect(product).to_be_visible(timeout=WAIT)
            await expect(product).to_contain_text("Blue mug 1")
            await expect(product).to_contain_text("RM 18.00 · 7 available")
            product_details = page.get_by_test_id(
                "business-product-00000000-0000-4000-8000-000000000001-details"
            )
            await expect(product_details).to_be_hidden()
            await product.get_by_role("button", name="Show details").click()
            await expect(product_details).to_contain_text("In stock: 10")
            await expect(product_details).to_contain_text("Set aside for orders: 3")
            await expect(product_details).to_contain_text("Available: 7")
            await expect(product_details).to_contain_text("Price: RM 18.00")
            await expect(product_details).to_contain_text("Status: For sale")
            product_id = "business-product-00000000-0000-4000-8000-000000000001"
            await open_tech(page, product_id)
            raw = page.get_by_test_id(f"{product_id}-tech-details")
            await expect(raw).to_contain_text("MUG-1 · Blue mug 1")
            await expect(raw).to_contain_text("MYR 18.00 · Local status: Active")
            await expect(raw).to_contain_text("Stock: 10 · Reserved: 3 · Available: 7")
            await expect(page.get_by_test_id("business-about-catalogue-details")).to_be_hidden()
            await open_fold(page, "about-catalogue")
            # Orders belong to the other page: nothing here reads them or the stores.
            assert not stub.order_queries and not stub.counts_queries, stub.order_queries
            assert set(stub.other_calls) == {"products"}, stub.other_calls
            assert all(q == {"limit": ["50"]} for q in stub.product_queries), stub.product_queries
            await expect(page.get_by_test_id("business-orders-list")).to_have_count(0)

    await with_browser(body)


async def test_stores_page_shows_store_records(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with business_page(
            browser, site, stub, path="/portal/business/stores", wait_for_rows=False
        ) as page:
            # The buttons, the platform choice and the records are on the page itself.
            store = page.get_by_test_id("business-store-00000000-0000-4000-8000-0000000000aa")
            await expect(store).to_be_visible(timeout=WAIT)
            await expect(
                page.get_by_role("button", name="Connect unavailable", exact=True)
            ).to_be_disabled()
            await expect(page.get_by_label("Store-record platform")).to_be_visible()
            # The long notices stay in the fold.
            await expect(page.get_by_test_id("business-stores-details")).to_be_hidden()
            await open_fold(page, "stores")
            await expect(page.get_by_test_id("business-stores-details")).to_contain_text(
                "Store connections are coming soon. These are saved store details only"
            )
            # The original notices are word for word under Technical details.
            await open_tech(page, "business-stores")
            await expect(page.get_by_test_id("business-stores-tech-details")).to_contain_text(
                "Account registration alone does not establish eligibility"
            )
            await expect(page.get_by_test_id("business-stores-tech-details")).to_contain_text(
                "Pages follow stable local record IDs"
            )
            await expect(store).to_contain_text("TikTok Shop · SHOP-A")
            await expect(store).to_contain_text("Not verified")
            store_details = page.get_by_test_id(
                "business-store-00000000-0000-4000-8000-0000000000aa-details"
            )
            await expect(store_details).to_be_hidden()
            await store.get_by_role("button", name="Show details").click()
            await expect(store_details).to_contain_text("Marketplace: TikTok Shop")
            await expect(store_details).to_contain_text("Store ID: SHOP-A")
            await expect(store_details).to_contain_text("Status: Not verified")
            await expect(store_details).to_contain_text("Last changed here: 3 Oct 2026, 08:00")
            await open_tech(page, "business-store-00000000-0000-4000-8000-0000000000aa")
            raw = page.get_by_test_id(
                "business-store-00000000-0000-4000-8000-0000000000aa-tech-details"
            )
            await expect(raw).to_contain_text(
                "Stored lifecycle: No usable local authorization evidence"
            )
            await expect(raw).to_contain_text("Live integration: unavailable")
            assert not stub.order_queries and set(stub.other_calls) == {"stores"}, stub.other_calls

    await with_browser(body)


async def test_section_bar_moves_between_pages_and_marks_the_current_one(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with business_page(browser, site, stub) as page:
            bar = page.get_by_role("navigation", name="Business management pages")
            await expect(bar.get_by_role("link")).to_have_text(
                ["Orders", "Products & stock", "Store connections"]
            )
            await expect(page.get_by_role("heading", level=1)).to_have_text("Business management")
            heading = page.get_by_test_id("business-page-heading")
            await expect(heading).to_have_text("Orders")
            await expect(page.get_by_test_id("business-subnav-orders")).to_have_attribute(
                "aria-current", "page"
            )
            await page.get_by_test_id("business-subnav-products").click()
            await expect(page).to_have_url(re.compile(r"/portal/business/products$"), timeout=WAIT)
            await expect(heading).to_have_text("Products & stock")
            await expect(heading).to_be_focused()
            await expect(page.get_by_test_id("business-subnav-products")).to_have_attribute(
                "aria-current", "page"
            )
            await expect(page.get_by_test_id("business-subnav-orders")).not_to_have_attribute(
                "aria-current", "page"
            )
            await expect(page.get_by_test_id("business-products-list")).to_be_visible()
            await page.get_by_test_id("business-subnav-stores").click()
            await expect(page).to_have_url(re.compile(r"/portal/business/stores$"), timeout=WAIT)
            await expect(heading).to_have_text("Store connections")
            await expect(heading).to_be_focused()
            await expect(page.get_by_test_id("business-subnav-stores")).to_have_attribute(
                "aria-current", "page"
            )
            await page.get_by_test_id("business-subnav-orders").click()
            await expect(page).to_have_url(re.compile(r"/portal/business$"), timeout=WAIT)
            await expect(order_card(page, 1)).to_be_visible()
            await expect(page.get_by_test_id("business-subnav-orders")).to_have_attribute(
                "aria-current", "page"
            )

    await with_browser(body)


async def test_section_bar_keeps_the_admin_tenant(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with business_page(
            browser, site, stub, path="/business", role="platform_admin", wait_for_rows=False
        ) as page:
            await expect(
                page.get_by_text("Select a tenant to view its business records.")
            ).to_be_visible(timeout=WAIT)
            assert not stub.order_queries
            await expect(page.get_by_test_id("business-subnav-products")).to_have_attribute(
                "href", "/business/products"
            )
            select = page.get_by_label("Tenant")
            await expect(select.locator("option")).to_have_count(2)
            await select.select_option(TENANT)
            await expect(page).to_have_url(
                re.compile(rf"/business\?client_id={TENANT}$"), timeout=WAIT
            )
            await expect(order_card(page, 1)).to_be_visible()
            assert stub.order_queries[-1].get("client_id") == [TENANT], stub.order_queries
            await expect(page.get_by_test_id("business-subnav-products")).to_have_attribute(
                "href", f"/business/products?client_id={TENANT}"
            )
            await page.get_by_test_id("business-subnav-products").click()
            await expect(page).to_have_url(
                re.compile(rf"/business/products\?client_id={TENANT}$"), timeout=WAIT
            )
            await expect(page.get_by_test_id("business-products-list")).to_be_visible()
            assert stub.product_queries[-1].get("client_id") == [TENANT], stub.product_queries
            await expect(page.get_by_label("Tenant")).to_have_value(TENANT)
            # A reload keeps the tenant, and the next page too.
            await page.reload()
            await expect(page.get_by_test_id("business-products-list")).to_be_visible()
            await expect(page.get_by_label("Tenant")).to_have_value(TENANT)
            assert stub.product_queries[-1].get("client_id") == [TENANT], stub.product_queries
            await page.get_by_test_id("business-subnav-stores").click()
            await open_fold(page, "stores")
            await expect(
                page.get_by_test_id("business-store-00000000-0000-4000-8000-0000000000aa")
            ).to_be_visible()
            assert stub.store_queries[-1].get("client_id") == [TENANT], stub.store_queries
            # Clearing the choice removes it from the address.
            await page.get_by_label("Tenant").select_option("")
            await expect(page).to_have_url(re.compile(r"/business/stores$"), timeout=WAIT)
            await expect(
                page.get_by_text("Select a tenant to view its business records.")
            ).to_be_visible()

    await with_browser(body)


async def test_a_rail_link_without_a_tenant_drops_the_tenant_on_screen(site: Site) -> None:
    """A soft move from ?client_id=A to the same page without it must not keep A's data."""

    async def body(browser: Any) -> None:
        stub = Stub()
        async with business_page(
            browser,
            site,
            stub,
            path=f"/business/products?client_id={TENANT}",
            role="platform_admin",
            wait_for_rows=False,
            rail_expanded=True,
        ) as page:
            await expect(page.get_by_test_id("business-products-list")).to_be_visible(timeout=WAIT)
            await expect(page.get_by_label("Tenant")).to_have_value(TENANT)
            await page.get_by_test_id("rail-child-business-products").click()
            await expect(page).to_have_url(re.compile(r"/business/products$"), timeout=WAIT)
            await expect(
                page.get_by_text("Select a tenant to view its business records.")
            ).to_be_visible()
            await expect(page.get_by_test_id("business-products-list")).to_have_count(0)
            await expect(page.get_by_label("Tenant")).to_have_value("")
            await expect(page.get_by_test_id("business-subnav-orders")).to_have_attribute(
                "href", "/business"
            )
            # Back returns to the first tenant and its data.
            await page.go_back()
            await expect(page).to_have_url(re.compile(rf"client_id={TENANT}$"), timeout=WAIT)
            await expect(page.get_by_label("Tenant")).to_have_value(TENANT)
            await expect(page.get_by_test_id("business-products-list")).to_be_visible()

    await with_browser(body)


async def test_a_rail_link_without_a_tenant_lands_on_select_a_tenant(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with business_page(
            browser,
            site,
            stub,
            path="/business/products",
            role="platform_admin",
            wait_for_rows=False,
        ) as page:
            await expect(
                page.get_by_text("Select a tenant to view its business records.")
            ).to_be_visible(timeout=WAIT)
            assert not stub.product_queries and not stub.order_queries

    await with_browser(body)


async def test_unknown_page_is_not_found(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with business_page(browser, site, stub) as page:
            for path in ("/portal/business/nothing", "/portal/business/products/extra"):
                response = await page.goto(f"{site.base}{path}")
                assert response is not None and response.status == 404, (path, response)

    await with_browser(body)


async def test_date_range_status_and_payment_filters_send_the_right_query(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with business_page(browser, site, stub) as page:
            default_from = instant(TODAY - timedelta(days=29))
            default_to = instant(TODAY + timedelta(days=1))
            assert default_from == "2026-10-03T16:00:00.000Z"
            week_from = instant(TODAY - timedelta(days=6))
            week_to = instant(TODAY + timedelta(days=1))
            assert week_from == "2026-10-26T16:00:00.000Z"
            default_counts = {"from": [default_from], "to": [default_to]}

            def last_order() -> dict[str, list[str]]:
                return stub.order_queries[-1]

            def last_counts() -> dict[str, list[str]]:
                return stub.counts_queries[-1]

            # First load: the list has no filter at all, the counts carry the default 30 days.
            # (The dev server runs effects twice, so there can be two identical requests.)
            assert all(query == {} for query in stub.order_queries), stub.order_queries
            assert all(query == default_counts for query in stub.counts_queries), (
                stub.counts_queries
            )
            await expect(page.get_by_test_id("business-counts-note")).to_contain_text(
                "The numbers cover the last 30 days."
            )
            await expect(page.get_by_test_id("business-counts-note")).to_contain_text(
                "The list shows every date until you choose dates."
            )
            await expect(page.get_by_test_id("business-status")).to_have_attribute(
                "aria-label", "Orders, last 30 days"
            )
            await expect(page.get_by_test_id("business-status-all")).to_have_text(
                f"All orders ({ORDERS_TOTAL})"
            )

            # The chips sit behind the All statuses arrow, the rest behind Filters.
            await expect(page.get_by_test_id("business-status")).to_be_hidden()
            await expect(page.get_by_test_id("business-payment")).to_be_hidden()
            await open_statuses(page)
            await open_filters(page)

            # A status chip filters the list and does not ask for new counts.
            counts_before = len(stub.counts_queries)
            await page.get_by_test_id("business-status-shipped").click()
            await wait_for_orders(page, 3)
            assert last_order() == {"status": ["shipped"]}
            assert len(stub.counts_queries) == counts_before
            await expect(page.get_by_test_id("business-status-shipped")).to_have_attribute(
                "aria-pressed", "true"
            )

            # The payment select adds payment_status to the list and the counts, not status.
            await page.get_by_test_id("business-payment").select_option("refunded")
            await wait_for_orders(page, 1)
            assert last_order() == {"status": ["shipped"], "payment_status": ["refunded"]}
            await until(lambda: last_counts().get("payment_status") == ["refunded"])
            assert last_counts() == {**default_counts, "payment_status": ["refunded"]}

            # The calendar sends UTC instants for the local days: last 7 days, end exclusive.
            await page.get_by_test_id("business-date").click()
            await page.get_by_test_id("business-date-preset-last7").click()
            await page.get_by_test_id("business-date-done").click()
            await until(lambda: last_order().get("from") == [week_from])
            await expect(page.get_by_test_id("business-status")).to_have_attribute(
                "aria-label", "Orders, dates you chose"
            )
            assert last_order() == {
                "status": ["shipped"],
                "payment_status": ["refunded"],
                "from": [week_from],
                "to": [week_to],
            }
            await until(lambda: last_counts().get("from") == [week_from])
            assert last_counts() == {
                "from": [week_from],
                "to": [week_to],
                "payment_status": ["refunded"],
            }
            for query in stub.order_queries + stub.counts_queries:
                for value in query.get("from", []) + query.get("to", []):
                    assert value.endswith("Z") and "+" not in value, value

            # Back to every status: the parameter is left out, not sent empty.
            await page.get_by_test_id("business-status-all").click()
            await until(lambda: "status" not in last_order())
            assert last_order() == {
                "payment_status": ["refunded"],
                "from": [week_from],
                "to": [week_to],
            }
            await page.get_by_test_id("business-payment").select_option("")
            await until(lambda: "payment_status" not in last_order())
            await until(lambda: "payment_status" not in last_counts())
            assert last_order() == {"from": [week_from], "to": [week_to]}

            # Clearing the dates leaves the list unbounded; the counts return to the default.
            await page.get_by_test_id("business-date").click()
            await page.get_by_test_id("business-date-preset-clear").click()
            await page.get_by_test_id("business-date-done").click()
            await until(lambda: last_order() == {})
            await until(lambda: last_counts() == default_counts)

            # "All time" is an explicit choice and asks for the counts without a window.
            await page.get_by_test_id("business-counts-all-time").click()
            await until(lambda: last_counts() == {})
            await expect(page.get_by_test_id("business-counts-note")).to_contain_text("all time")
            await expect(page.get_by_test_id("business-status")).to_have_attribute(
                "aria-label", "Orders, all dates"
            )
            # Source filters still apply with the existing button and ride on both reads.
            await open_fold(page, "more-filters")
            await page.get_by_label("Where the order came from").select_option("shopee")
            await page.get_by_role("button", name="Apply order filters").click()
            await until(lambda: last_order() == {"channel": ["shopee"]})
            await until(lambda: last_counts() == {"channel": ["shopee"]})
            await expect(page.get_by_test_id("business-more-filters-details")).to_contain_text(
                "Showing: Shopee, all stores"
            )

    await with_browser(body)


async def test_counts_request_is_aborted_when_a_filter_changes(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        stub.counts_gate.clear()
        async with business_page(browser, site, stub) as page:
            # The list is up while the first counts read is still running (they run in parallel).
            assert stub.counts_queries
            await expect(page.get_by_test_id("business-status-all")).to_have_text("All orders")
            seen = len(stub.counts_queries)
            await open_filters(page)
            failed: list[str] = []
            page.on("requestfailed", lambda request: failed.append(request.url))
            await page.get_by_test_id("business-payment").select_option("refunded")
            await until(lambda: len(stub.counts_queries) == seen + 1)
            await until(lambda: bool(failed), "the first counts read was not aborted")
            assert any("orders/counts" in url and "payment_status" not in url for url in failed), (
                failed
            )
            stub.counts_gate.set()
            await expect(page.get_by_test_id("business-status-all")).to_have_text(
                re.compile(r"^All orders \(\d+\)$")
            )

    await with_browser(body)


async def test_status_counts_show_and_hide_when_the_counts_read_fails(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with business_page(browser, site, stub) as page:
            await open_statuses(page)
            # Counts read fine: every chip carries its number and the sum is the "All" figure.
            await expect(page.get_by_test_id("business-status-all")).to_have_text(
                f"All orders ({ORDERS_TOTAL})"
            )
            await expect(page.get_by_test_id("business-status-pending_confirmation")).to_have_text(
                "Waiting for confirmation (4)"
            )
            await expect(page.get_by_test_id("business-status-draft")).to_have_text("Draft (3)")
            await expect(page.get_by_test_id("business-counts-failed")).to_have_count(0)

            # The next counts read fails: the numbers go, no zero takes their place, the list stays.
            stub.counts_fail = True
            await page.get_by_test_id("business-orders-refresh").click()
            await expect(page.get_by_test_id("business-counts-failed")).to_be_visible()
            for status in ("all", "draft", "pending_confirmation", "shipped"):
                text = await page.get_by_test_id(f"business-status-{status}").inner_text()
                assert "(" not in text and "0" not in text, text
            await expect(page.get_by_test_id("business-status-shipped")).to_have_text("Shipped")
            await expect(cards(page)).to_have_count(PAGE_SIZE)
            # The chips still filter the list from the list read.
            await page.get_by_test_id("business-status-shipped").click()
            await wait_for_orders(page, 3)

            # And back again when the read recovers.
            stub.counts_fail = False
            await page.get_by_test_id("business-orders-refresh").click()
            await expect(page.get_by_test_id("business-status-shipped")).to_have_text("Shipped (3)")
            await expect(page.get_by_test_id("business-counts-failed")).to_have_count(0)

    await with_browser(body)


async def test_tiles_show_counts_and_filter_the_list(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with business_page(browser, site, stub) as page:
            group = page.get_by_role("group", name="Orders at a glance")
            await expect(group).to_have_count(1)
            # Statuses cycle: orders 1, 9, 17, 25 wait for confirmation; 3 are confirmed; 3 shipped.
            for tile, number in (("to-confirm", "4"), ("to-pack", "3"), ("on-the-way", "3")):
                await expect(page.get_by_test_id(f"business-tile-{tile}-value")).to_have_text(
                    number
                )
            tile = page.get_by_test_id("business-tile-to-confirm")
            await expect(tile).to_have_attribute("aria-pressed", "false")
            counts_before = len(stub.counts_queries)
            await tile.click()
            await wait_for_orders(page, 4)
            assert stub.order_queries[-1] == {"status": ["pending_confirmation"]}
            await expect(tile).to_have_attribute("aria-pressed", "true")
            assert len(stub.counts_queries) == counts_before  # a status never asks for new counts
            await expect(page.get_by_test_id("business-all-statuses")).to_contain_text(
                "Showing: Waiting for confirmation"
            )
            # Pressing the pressed tile drops the filter.
            await tile.click()
            await until(lambda: "status" not in stub.order_queries[-1])
            await wait_for_orders(page, PAGE_SIZE)
            await expect(tile).to_have_attribute("aria-pressed", "false")
            await page.get_by_test_id("business-tile-on-the-way").click()
            await wait_for_orders(page, 3)
            assert stub.order_queries[-1] == {"status": ["shipped"]}

    await with_browser(body)


async def test_tiles_say_not_available_when_the_counts_read_fails(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub(counts_fail=True)
        async with business_page(browser, site, stub) as page:
            for tile in ("to-confirm", "to-pack", "on-the-way"):
                value = page.get_by_test_id(f"business-tile-{tile}-value")
                await expect(value).to_have_text("Not available")
            for tile in ("to-confirm", "to-pack", "on-the-way"):
                text = await page.get_by_test_id(f"business-tile-{tile}").inner_text()
                assert "0" not in text, text
            # The list and the products tile still work.
            await expect(page.get_by_test_id("business-tile-out-of-stock-value")).to_have_text("0")
            await page.get_by_test_id("business-tile-on-the-way").click()
            await wait_for_orders(page, 3)

    await with_browser(body)


async def test_out_of_stock_tile_counts_the_newest_50_and_opens_products_filtered(
    site: Site,
) -> None:
    async def body(browser: Any) -> None:
        stub = Stub(availability=(7, 0, -1, 2), products_more=True)
        async with business_page(browser, site, stub) as page:
            tile = page.get_by_test_id("business-tile-out-of-stock")
            await expect(page.get_by_test_id("business-tile-out-of-stock-value")).to_have_text("2")
            await expect(page.get_by_test_id("business-tile-out-of-stock-caption")).to_have_text(
                "Among your newest 50 products"
            )
            assert all(q == {"limit": ["50"]} for q in stub.product_queries), stub.product_queries
            await expect(tile).to_have_attribute("href", "/portal/business/products?stock=out")
            await tile.click()
            await expect(page).to_have_url(
                re.compile(r"/portal/business/products\?stock=out$"), timeout=WAIT
            )
            chip = page.get_by_test_id("business-stock-filter-out")
            await expect(chip).to_have_attribute("aria-pressed", "true")
            await expect(page.get_by_test_id("business-stock-note")).to_have_text(
                "Out of stock among the products loaded so far."
            )
            products = page.get_by_test_id("business-products-list").locator("> li")
            await expect(products).to_have_count(2)
            await page.get_by_test_id("business-stock-filter-all").click()
            await expect(products).to_have_count(4)
            await expect(page.get_by_test_id("business-stock-note")).to_have_count(0)

    await with_browser(body)

    # Fewer than 50 products: no caption.
    async def plain(browser: Any) -> None:
        stub = Stub(availability=(0, 3))
        async with business_page(browser, site, stub) as page:
            await expect(page.get_by_test_id("business-tile-out-of-stock-value")).to_have_text("1")
            await expect(page.get_by_test_id("business-tile-out-of-stock-caption")).to_have_count(0)

    await with_browser(plain)


async def test_all_statuses_arrow_holds_the_status_chips(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with business_page(browser, site, stub) as page:
            row = page.get_by_test_id("business-all-statuses")
            await expect(row).to_contain_text("Showing: All orders")
            toggle = page.get_by_test_id("business-all-statuses-toggle")
            await expect(toggle).to_have_attribute("aria-expanded", "false")
            await expect(page.get_by_test_id("business-status")).to_be_hidden()
            await toggle.click()
            await expect(toggle).to_have_attribute("aria-expanded", "true")
            await expect(page.get_by_test_id("business-status")).to_be_visible()
            await expect(page.get_by_test_id("business-status").locator("button")).to_have_count(9)
            await page.get_by_test_id("business-status-processing").click()
            await wait_for_orders(page, 3)
            await expect(row).to_contain_text("Showing: Being prepared")

    await with_browser(body)


async def test_filters_button_holds_date_payment_and_source(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with business_page(browser, site, stub) as page:
            toggle = page.get_by_test_id("business-filters-toggle")
            await expect(toggle).to_have_attribute("aria-expanded", "false")
            panel_id = await toggle.get_attribute("aria-controls")
            assert (
                panel_id
                and await page.get_by_test_id("business-filters").get_attribute("id") == panel_id
            )
            for testid in ("business-date", "business-payment", "business-more-filters-toggle"):
                await expect(page.get_by_test_id(testid)).to_be_hidden()
            await toggle.click()
            await expect(toggle).to_have_attribute("aria-expanded", "true")
            for testid in ("business-date", "business-payment", "business-more-filters-toggle"):
                await expect(page.get_by_test_id(testid)).to_be_visible()
            await page.get_by_test_id("business-payment").select_option("paid")
            # Closing the panel keeps what was chosen.
            await toggle.click()
            await expect(page.get_by_test_id("business-payment")).to_be_hidden()
            await expect(page.get_by_test_id("business-active-payment")).to_contain_text("Paid")
            await toggle.click()
            await expect(page.get_by_test_id("business-payment")).to_have_value("paid")

    await with_browser(body)


async def test_active_filters_show_as_removable_chips(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with business_page(browser, site, stub) as page:
            await expect(page.get_by_test_id("business-active-filters")).to_have_count(0)
            await open_filters(page)
            await page.get_by_test_id("business-payment").select_option("refunded")
            await page.get_by_test_id("business-date").click()
            await page.get_by_test_id("business-date-preset-last7").click()
            await page.get_by_test_id("business-date-done").click()
            await page.get_by_test_id("business-tile-to-confirm").click()
            row = page.get_by_test_id("business-active-filters")
            await expect(row).to_be_visible()
            await expect(page.get_by_test_id("business-active-payment")).to_contain_text(
                "Payment refunded"
            )
            await expect(page.get_by_test_id("business-active-date")).to_contain_text("2 Nov 2026")
            await expect(page.get_by_test_id("business-active-status")).to_contain_text(
                "Waiting for confirmation"
            )
            await until(lambda: stub.order_queries[-1].get("status") == ["pending_confirmation"])
            await page.get_by_role("button", name="Remove Payment refunded filter").click()
            await expect(page.get_by_test_id("business-active-payment")).to_have_count(0)
            await until(lambda: "payment_status" not in stub.order_queries[-1])
            await page.get_by_role("button", name="Remove Waiting for confirmation filter").click()
            await until(lambda: "status" not in stub.order_queries[-1])
            await expect(page.get_by_test_id("business-tile-to-confirm")).to_have_attribute(
                "aria-pressed", "false"
            )
            await page.get_by_test_id("business-active-date-remove").click()
            await until(lambda: "from" not in stub.order_queries[-1])
            await expect(row).to_have_count(0)

    await with_browser(body)


async def test_order_row_has_details_and_technical_details(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with business_page(browser, site, stub) as page:
            row_id = f"business-order-{oid(4)}"  # a shipped order
            await page.get_by_test_id(f"{row_id}-toggle").click()
            details = page.get_by_test_id(f"{row_id}-details")
            for title in ("Payment", "Delivery", "Record"):
                await expect(details.get_by_role("heading", name=title, level=4)).to_be_visible()
            await expect(details).to_contain_text("Tracking number: TRK00004")
            await expect(details).to_contain_text("Courier: J&T")
            await expect(details).to_contain_text("Shipped: 2 Nov 2026")
            await expect(details).to_contain_text("Marketplace order no.: EXT-0004")
            await expect(details).to_contain_text("Source: Manual")
            # The second level: closed, then the seven original lines.
            toggle = page.get_by_test_id(f"{row_id}-tech-toggle")
            await expect(toggle).to_have_attribute("aria-expanded", "false")
            await expect(toggle).to_contain_text("technical details")
            raw = page.get_by_test_id(f"{row_id}-tech-details")
            await expect(raw).to_be_hidden()
            await toggle.click()
            await expect(toggle).to_have_attribute("aria-expanded", "true")
            await expect(raw.locator("p")).to_have_count(7)
            await expect(raw).to_contain_text(oid(4))
            await expect(raw).to_contain_text("Tracking: TRK00004 · Carrier: J&T")
            await expect(raw).to_contain_text("Local update:")

    await with_browser(body)


async def test_show_technical_details_opens_every_row(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with business_page(browser, site, stub) as page:
            switch = page.get_by_test_id("business-tech-all")
            await expect(switch).to_have_text("Show technical details")
            await expect(switch).to_have_attribute("aria-pressed", "false")
            await switch.click()
            await expect(switch).to_have_attribute("aria-pressed", "true")
            for n in (1, 2, PAGE_SIZE):
                row_id = f"business-order-{oid(n)}"
                await expect(page.get_by_test_id(f"{row_id}-toggle")).to_have_attribute(
                    "aria-expanded", "true"
                )
                await expect(page.get_by_test_id(f"{row_id}-tech-toggle")).to_have_attribute(
                    "aria-expanded", "true"
                )
                await expect(page.get_by_test_id(f"{row_id}-tech-details")).to_contain_text(oid(n))
            # Rows that arrive later open too.
            await page.get_by_role("button", name="Older orders").click()
            await expect(cards(page)).to_have_count(ORDERS_TOTAL)
            late = f"business-order-{oid(ORDERS_TOTAL)}"
            await expect(page.get_by_test_id(f"{late}-toggle")).to_have_attribute(
                "aria-expanded", "true"
            )
            await expect(page.get_by_test_id(f"{late}-tech-toggle")).to_have_attribute(
                "aria-expanded", "true"
            )
            # One row can still be closed by hand; pressing the switch again closes them all.
            await page.get_by_test_id(f"business-order-{oid(1)}-tech-toggle").click()
            await expect(
                page.get_by_test_id(f"business-order-{oid(1)}-tech-details")
            ).to_be_hidden()
            await expect(
                page.get_by_test_id(f"business-order-{oid(2)}-tech-details")
            ).to_be_visible()
            await switch.click()
            await expect(switch).to_have_attribute("aria-pressed", "false")
            for n in (1, 2, ORDERS_TOTAL):
                await expect(
                    page.get_by_test_id(f"business-order-{oid(n)}-toggle")
                ).to_have_attribute("aria-expanded", "false")

    await with_browser(body)


async def test_see_an_example_opens_the_demo(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with business_page(browser, site, stub) as page:
            example = page.get_by_role("button", name="See an example")
            await expect(example).to_have_attribute("aria-pressed", "false")
            # It is a quiet button at the bottom, under the orders and the two folds.
            example_top = (await example.bounding_box() or {})["y"]
            list_top = (await page.get_by_test_id("business-orders-list").bounding_box() or {})["y"]
            assert example_top > list_top, (example_top, list_top)
            await expect(page.get_by_text("Show synthetic demo")).to_have_count(0)
            await example.click()
            await expect(
                page.get_by_text("Example data only, made up for this view.", exact=False)
            ).to_be_visible()
            await expect(page.get_by_test_id("business-tiles")).to_have_count(0)
            exit_button = page.get_by_role("button", name="Hide the example")
            await expect(exit_button).to_have_attribute("aria-pressed", "true")
            await expect(page.get_by_role("button", name="See an example")).to_have_count(0)
            exit_top = (await exit_button.bounding_box() or {})["y"]
            assert exit_top < 500, exit_top  # at the top while in demo
            await exit_button.click()
            await expect(order_card(page, 1)).to_be_visible()
            await expect(page.get_by_role("button", name="See an example")).to_be_visible()

    await with_browser(body)


async def test_older_accumulates_rows(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with business_page(browser, site, stub) as page:
            await expect(cards(page)).to_have_count(PAGE_SIZE)
            await order_card(page, 1).get_by_role("button", name="Show details").click()
            older = page.get_by_role("button", name="Older orders")
            stub.older_gate.clear()
            await older.click()
            # While the older page loads the button says so, the first page stays on screen.
            loading = page.get_by_role("button", name="Loading...", exact=True)
            await expect(loading).to_be_disabled()
            await expect(page.get_by_text("Loading local orders...")).to_be_visible()
            await expect(cards(page)).to_have_count(PAGE_SIZE)
            stub.older_gate.set()
            await expect(cards(page)).to_have_count(ORDERS_TOTAL)
            assert stub.order_queries[-1] == {"before": [oid(PAGE_SIZE)]}
            # The first page is still there, and its opened row stayed open.
            await expect(order_card(page, 1)).to_be_visible()
            await expect(order_card(page, 1)).to_have_attribute("data-open", "true")
            await expect(order_card(page, ORDERS_TOTAL)).to_be_visible()
            await expect(older).to_have_count(0)
            # No row twice.
            ids = (
                await cards(page)
                .locator("[data-testid^='business-order-']")
                .evaluate_all(
                    "els => els.map(e => e.dataset.testid).filter(t => /^business-order-[0-9a-f-]+$/.test(t))"  # noqa: E501
                )
            )
            assert len(ids) == len(set(ids)) == ORDERS_TOTAL
            # Refresh newest starts again from the first page.
            await page.get_by_test_id("business-orders-refresh").click()
            await expect(cards(page)).to_have_count(PAGE_SIZE)
            assert stub.order_queries[-1] == {}
            await expect(page.get_by_role("button", name="Older orders")).to_be_visible()

    await with_browser(body)


async def test_older_works_again_after_a_failed_older_page(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        stub.older_failures = 1
        async with business_page(browser, site, stub) as page:
            await expect(cards(page)).to_have_count(PAGE_SIZE)
            older = page.get_by_role("button", name="Older orders")
            await older.click()
            # The failed page says so, keeps the first page, and nothing stays "loading".
            await expect(page.get_by_text("synthetic older outage")).to_be_visible()
            await expect(cards(page)).to_have_count(PAGE_SIZE)
            await expect(page.get_by_text("Loading local orders...")).to_have_count(0)
            reads = len([q for q in stub.order_queries if q.get("before")])
            assert reads == 1, stub.order_queries
            # Pressing Older again sends a second read (the cursor did not change) and merges it.
            await expect(older).to_be_enabled()
            await older.click()
            await expect(cards(page)).to_have_count(ORDERS_TOTAL)
            assert len([q for q in stub.order_queries if q.get("before")]) == 2, stub.order_queries
            await expect(page.get_by_text("Loading local orders...")).to_have_count(0)
            await expect(page.get_by_text("synthetic older outage")).to_have_count(0)
            # Refresh is usable again.
            refresh = page.get_by_test_id("business-orders-refresh")
            await expect(refresh).to_be_enabled()
            await refresh.click()
            await expect(cards(page)).to_have_count(PAGE_SIZE)

    await with_browser(body)


async def test_empty_state_keeps_the_original_sentence(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub(orders=[])
        async with business_page(browser, site, stub, wait_for_rows=False) as page:
            await expect(
                page.get_by_text(
                    "No local orders match these filters. This does not mean your marketplace "
                    "has no orders."
                )
            ).to_be_visible(timeout=WAIT)
            await expect(page.get_by_test_id("business-status-all")).to_have_text("All orders (0)")

    await with_browser(body)


async def overflow(page: Page) -> dict[str, int]:
    return await page.evaluate(
        "() => ({scroll: document.documentElement.scrollWidth, inner: window.innerWidth})"
    )


async def check_widths(page: Page, theme: str, where: str) -> None:
    for width in WIDTHS:
        await page.set_viewport_size({"width": width, "height": 900})
        measured = await overflow(page)
        assert measured["scroll"] <= measured["inner"], ("page", where, theme, width, measured)
        # No card, chip row or filter sticks out of the viewport either.
        outside = await page.evaluate(
            """() => [...document.querySelectorAll(
                '[data-testid^="business-"]')].filter(
                e => {
                    const clipped = e.parentElement?.getAttribute('role') === 'group'
                        && getComputedStyle(e.parentElement).overflowX === 'auto';
                    const right = e.getBoundingClientRect().right;
                    return !clipped && right > window.innerWidth + 0.5;
                }
            ).map(e => e.dataset.testid)"""
        )
        assert not outside, (where, theme, width, outside)


@pytest.mark.parametrize("theme", THEMES)
async def test_business_at_five_widths_in_three_themes(site: Site, theme: str) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with business_page(browser, site, stub, theme=theme) as page:
            await expect(page.get_by_test_id("business-tiles")).to_be_visible()
            await page.get_by_test_id("business-tile-to-confirm").click()
            await expect(page.get_by_test_id("business-tile-to-confirm")).to_have_attribute(
                "aria-pressed", "true"
            )
            await open_filters(page)
            await open_statuses(page)
            await open_fold(page, "more-filters")
            await page.get_by_test_id("business-payment").select_option("unpaid")
            await wait_for_orders(page, 1)
            await expect(page.get_by_test_id("business-active-filters")).to_be_visible()
            first_id = f"business-order-{oid(1)}"
            await order_card(page, 1).get_by_role("button", name="Show details").click()
            await open_tech(page, first_id)
            for name in ("about", "not-yet"):
                await open_fold(page, name)
            await expect(order_card(page, 1)).to_have_attribute("data-open", "true")
            await check_widths(page, theme, "orders")
            for width in WIDTHS:
                await page.set_viewport_size({"width": width, "height": 900})
                await page.get_by_test_id("business-date").click()
                await expect(page.get_by_test_id("business-date-popover")).to_be_visible()
                measured = await overflow(page)
                assert measured["scroll"] <= measured["inner"], ("calendar", theme, width, measured)
                await page.keyboard.press("Escape")
                await expect(page.get_by_test_id("business-date-popover")).to_have_count(0)
            # The section bar itself, then the other two pages.
            await page.get_by_test_id("business-subnav-products").click()
            product = page.get_by_test_id("business-product-00000000-0000-4000-8000-000000000001")
            await expect(product).to_be_visible(timeout=WAIT)
            await product.get_by_role("button", name="Show details").click()
            await open_tech(page, "business-product-00000000-0000-4000-8000-000000000001")
            await open_fold(page, "about-catalogue")
            await check_widths(page, theme, "products")
            await page.get_by_test_id("business-subnav-stores").click()
            store = page.get_by_test_id("business-store-00000000-0000-4000-8000-0000000000aa")
            await expect(store).to_be_visible(timeout=WAIT)
            await open_fold(page, "stores")
            await store.get_by_role("button", name="Show details").click()
            await open_tech(page, "business-store-00000000-0000-4000-8000-0000000000aa")
            await check_widths(page, theme, "stores")

    await with_browser(body)


async def test_demo_mode_strings_and_disabled_connect_buttons_remain(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with business_page(browser, site, stub) as page:
            # The long notices are folded; their text stays reachable on the Orders page.
            await expect(page.get_by_test_id("business-about-details")).to_be_hidden()
            await open_fold(page, "about")
            await expect(
                page.get_by_text("These are orders and products saved in fourstacks.", exact=False)
            ).to_be_visible()
            await open_fold(page, "not-yet")
            await expect(page.get_by_text("Not available yet", exact=True)).to_be_visible()
            before = (len(stub.order_queries), len(stub.counts_queries), list(stub.other_calls))
            await page.get_by_role("button", name="See an example").click()
            await expect(
                page.get_by_text(
                    "Example data only, made up for this view. Not connected to TikTok; nothing "
                    "here is sent to TikTok.",
                    exact=False,
                )
            ).to_be_visible()
            await expect(
                page.get_by_text("No TikTok request was made. Demo listing changes are not saved.")
            ).to_be_visible()
            await expect(page.get_by_text("DEMO-MUG-001 · Sample ceramic mug")).to_be_visible()
            await page.get_by_role("button", name="Simulate create listing").click()
            await expect(page.get_by_text("DEMO-NEW-003 · Sample listing 3")).to_be_visible()
            await expect(page.get_by_test_id("business-orders-list")).to_have_count(0)
            assert (
                len(stub.order_queries),
                len(stub.counts_queries),
                list(stub.other_calls),
            ) == before
            await page.get_by_role("button", name="Hide the example").click()
            await expect(order_card(page, 1)).to_be_visible()
            # The demo lives on the Orders page only; the Connect buttons live on Stores.
            await page.get_by_test_id("business-subnav-stores").click()
            await expect(page.get_by_role("button", name="See an example")).to_have_count(0)
            connect = page.get_by_role("button", name="Connect unavailable", exact=True)
            reconnect = page.get_by_role("button", name="Reconnect unavailable", exact=True)
            await expect(connect).to_be_disabled()
            await expect(reconnect).to_be_disabled()
            await expect(page.get_by_role("button", name=" unavailable")).to_have_count(2)
            await page.get_by_test_id("business-subnav-products").click()
            await expect(page.get_by_role("button", name="See an example")).to_have_count(0)

    await with_browser(body)


async def test_first_screen_at_390_shows_tiles_the_filters_button_and_the_first_orders(
    site: Site,
) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with business_page(browser, site, stub, width=390, height=844) as page:
            await expect(
                page.get_by_role("heading", name="Business management")
            ).to_be_in_viewport()
            for testid in (
                "business-tile-to-confirm",
                "business-tile-to-pack",
                "business-tile-on-the-way",
                "business-tile-out-of-stock",
                "business-filters-toggle",
                "business-tech-all",
            ):
                await expect(page.get_by_test_id(testid)).to_be_in_viewport()
            # The first order's summary is on the first screen, with no technical paragraph above.
            top = await order_card(page, 1).evaluate(
                "el => el.closest('li').getBoundingClientRect().top"
            )
            title = (
                await order_card(page, 1)
                .get_by_test_id(f"business-order-{oid(1)}-title")
                .evaluate("el => el.getBoundingClientRect().bottom")
            )
            # The first order's title is on the first screen with room to spare, and the four
            # tiles share one row.
            assert top < 844 - 60 and title < 844 - 30, (top, title)
            rows = await page.evaluate(
                """() => new Set([...document.querySelectorAll('[data-testid^=business-tile-]')]
                    .filter((el) => el.tagName !== 'SPAN')
                    .map((el) => Math.round(el.getBoundingClientRect().top))).size"""
            )
            assert rows == 1, rows
            for text in (
                "These are orders and products saved",
                "Connect unavailable",
                "Exact store ID",
            ):
                await expect(page.get_by_text(text, exact=False).first).to_be_hidden()
            # Everything behind the Filters button and the All statuses arrow is closed.
            for testid in ("business-date", "business-payment", "business-status-all"):
                await expect(page.get_by_test_id(testid)).to_be_hidden()
            await expect(page.get_by_test_id("business-all-statuses-toggle")).to_have_attribute(
                "aria-expanded", "false"
            )
            await expect(page.get_by_test_id("business-filters-toggle")).to_have_attribute(
                "aria-expanded", "false"
            )
            assert (await overflow(page))["scroll"] <= 390
            # Open, the status chips wrap inside the page (no sideways scrolling row).
            await open_statuses(page)
            row = await page.get_by_test_id("business-status").evaluate(
                """el => ({
                    scrolls: el.scrollWidth > el.clientWidth,
                    page: document.documentElement.scrollWidth <= window.innerWidth,
                    right: Math.max(...[...el.children].map(b => b.getBoundingClientRect().right)),
                })"""
            )
            assert not row["scrolls"] and row["page"] and row["right"] <= 390, row

    await with_browser(body)


async def test_active_source_filter_shows_outside_the_closed_fold(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with business_page(browser, site, stub) as page:
            default_counts = {
                "from": [instant(TODAY - timedelta(days=29))],
                "to": [instant(TODAY + timedelta(days=1))],
            }
            await expect(page.get_by_test_id("business-source-filter")).to_have_count(0)
            await open_filters(page)
            await open_fold(page, "more-filters")
            await page.get_by_label("Where the order came from").select_option("shopee")
            await page.get_by_role("button", name="Apply order filters").click()
            await until(lambda: stub.order_queries[-1] == {"channel": ["shopee"]})
            # Close the fold again: the narrowing is still said in words, outside it.
            await page.get_by_test_id("business-more-filters-toggle").click()
            await expect(page.get_by_test_id("business-more-filters-details")).to_be_hidden()
            line = page.get_by_test_id("business-source-filter")
            await expect(line).to_be_visible()
            await expect(line).to_contain_text("Showing orders from Shopee only")
            await expect(page.get_by_test_id("business-more-filters-hint")).to_have_text(
                "· Source: Shopee"
            )
            await expect(page.get_by_test_id("business-more-filters-toggle")).to_be_visible()
            # "Show all sources" clears it for the list and the counts, fold still closed.
            await page.get_by_test_id("business-source-clear").click()
            await until(lambda: stub.order_queries[-1] == {})
            await until(lambda: stub.counts_queries[-1] == default_counts)
            await expect(line).to_have_count(0)
            await expect(page.get_by_test_id("business-more-filters-hint")).to_have_count(0)
            await expect(page.get_by_test_id("business-more-filters-details")).to_be_hidden()
            await open_fold(page, "more-filters")
            await expect(page.get_by_test_id("business-more-filters-details")).to_contain_text(
                "Showing: all sources, all stores"
            )
            await expect(page.get_by_label("Where the order came from")).to_have_value("")

    await with_browser(body)


async def test_a_slow_earlier_list_response_never_replaces_the_newer_list(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        stub.hold_unfiltered = True
        stub.list_gate.clear()
        async with business_page(browser, site, stub, wait_for_rows=False) as page:
            # The unfiltered list is still on its way when a newer filter is chosen.
            await expect(page.get_by_text("Loading local orders...")).to_be_visible()
            await open_statuses(page)
            await page.get_by_test_id("business-status-shipped").click()
            await wait_for_orders(page, 3)
            stub.list_gate.set()
            await asyncio.sleep(1.5)
            await wait_for_orders(page, 3)
            await expect(page.get_by_test_id("business-status-shipped")).to_have_attribute(
                "aria-pressed", "true"
            )
            ids = await cards(page).evaluate_all("els => els.length")
            assert ids == 3

    await with_browser(body)


async def test_the_default_window_moves_on_when_the_tab_returns_on_a_new_day(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with business_page(browser, site, stub) as page:
            first = instant(TODAY - timedelta(days=29))
            assert stub.counts_queries[-1]["from"] == [first]
            tomorrow = TODAY + timedelta(days=1)
            await page.context.clock.set_fixed_time(NOW + timedelta(days=1))
            await page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
            await until(
                lambda: (
                    stub.counts_queries[-1].get("from") == [instant(tomorrow - timedelta(days=29))]
                ),
                "the counts window did not move to the new day",
            )
            assert stub.counts_queries[-1]["to"] == [instant(tomorrow + timedelta(days=1))]

    await with_browser(body)
