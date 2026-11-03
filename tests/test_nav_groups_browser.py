"""Real browser acceptance for the rail groups: Business management and Business knowledge.

Same stubbed-upstream setup as `test_console_shell_browser.py`: the real Next.js shell runs
from a disposable staged copy, a loopback stub answers the layouts' server-side fetches and
Playwright answers every browser-side `/console/v1/*` call (the sub-pages themselves only need
to render, so their reads fail as "not stubbed"). One dev server serves every test here.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.async_api import Page, async_playwright, expect

pytestmark = pytest.mark.asyncio

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "CSA_WebConsole/csa-console/web"
TENANT = "11111111-1111-4111-8111-111111111111"
TENANT_PROFILE = {
    "id": TENANT,
    "slug": "synthetic",
    "name": "Synthetic Tenant",
    "timezone": "Asia/Kuala_Lumpur",
    "status": "active",
}
WAIT = 60_000  # First visit to each route compiles it in `next dev`.


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_me(role: str) -> dict[str, Any]:
    admin = role == "platform_admin"
    return {
        "user": {
            "id": "22222222-2222-4222-8222-222222222222",
            "email": "synthetic@example.test",
            "display_name": "Synthetic User",
            "is_platform_admin": admin,
            "must_change_password": False,
        },
        "memberships": [] if admin else [{"client_id": TENANT, "role": role}],
        "active_client_id": None if admin else TENANT,
        "role": role,
        "csrf_token": "synthetic-csrf",
    }


class Upstream:
    """Loopback stand-in for the gateway origin used by server-side layout fetches."""

    def __init__(self) -> None:
        self.me = make_me("tenant_admin")
        self.packages: list[dict[str, str]] = []
        self.profile_status = 200
        self.seen: list[tuple[str, str]] = []
        self.unexpected: list[str] = []
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = self.path.split("?")[0]
                upstream.seen.append((path, self.headers.get("cookie", "")))
                if path == "/console/v1/auth/me":
                    self.reply(200, upstream.me)
                elif path == "/console/v1/services/active":
                    self.reply(200, {"packages": upstream.packages})
                elif path == "/console/v1/profile":
                    if upstream.profile_status == 200:
                        self.reply(200, TENANT_PROFILE)
                    else:
                        self.reply(upstream.profile_status, {"detail": "synthetic profile failure"})
                else:
                    upstream.unexpected.append(f"GET {self.path}")
                    self.reply(500, {"detail": "unexpected synthetic upstream call"})

            def reply(self, status: int, body: Any) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> Upstream:
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


ALL_PACKAGES = [
    {"key": key, "status": "active"}
    for key in ("customer_support", "orders_products", "hr", "staff_email", "subscriptions")
]
# Every label a tenant admin with all packages gets, in navigation order.
TENANT_LABELS = (
    "Overview",
    "Services",
    "People & HR",
    "Assistant activity",
    "Knowledge base",
    "Customer inbox",
    "Email assistant",
    "Business management",
    "Subscriptions",
    "Team",
    "Password",
)
THEMES = ("cobalt", "night", "bureau")
WIDTHS = (320, 390, 768, 1024, 1440)


class Console:
    def __init__(self, base: str, upstream: Upstream) -> None:
        self.base = base
        self.upstream = upstream


@pytest.fixture(scope="module")
def console(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    node = shutil.which("node")
    assert node, "Node is required for the browser shell acceptance"
    assert (WEB / "node_modules/next/dist/bin/next").is_file(), (
        "Console dependencies are missing; run `npm ci --ignore-scripts` in "
        "CSA_WebConsole/csa-console/web before browser shell acceptance"
    )
    tmp_path = tmp_path_factory.mktemp("shell")
    stage = tmp_path / "web"
    with Upstream() as upstream:
        environment = {
            "PATH": os.environ["PATH"],
            "NEXT_TELEMETRY_DISABLED": "1",
            "CSA_CONSOLE_API_ORIGIN": upstream.origin,
        }
        subprocess.run(
            [node, str(WEB / "scripts/check-isolated.mjs"), "--context", str(stage)],
            check=True,
            cwd=ROOT,
            env=environment,
            capture_output=True,
        )
        (stage / "node_modules").symlink_to(WEB / "node_modules", target_is_directory=True)
        port = free_port()
        log_path = tmp_path / "frontend.log"
        log = log_path.open("w")
        process = subprocess.Popen(
            [
                node,
                str(WEB / "node_modules/next/dist/bin/next"),
                "dev",
                "--webpack",
                "--hostname",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=stage,
            env=environment,
            stdout=log,
            stderr=log,
        )
        try:
            with httpx.Client(trust_env=False) as http:
                for _ in range(120):
                    assert process.poll() is None, log_path.read_text()
                    try:
                        if http.get(f"http://127.0.0.1:{port}/login", timeout=2).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.5)
                else:
                    pytest.fail(f"Synthetic shell frontend did not start; log: {log_path}")
            yield Console(f"http://127.0.0.1:{port}", upstream)
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.close()


async def open_page(
    console: Console,
    path: str,
    *,
    width: int = 1280,
    height: int = 800,
    cookies: dict[str, str] | None = None,
    role: str = "tenant_admin",
    logout: tuple[int, list[str]] | None = None,
    profile_status: int = 200,
) -> tuple[Any, Any, Page]:
    """A fresh context signed in as `role` with every package active, on `path`.

    `logout` is the status the stubbed sign-out answers with and a list that records each call.
    `profile_status` is what the tenant profile read answers with (the user menu's tenant line).
    """
    console.upstream.profile_status = profile_status
    console.upstream.me = make_me(role)
    console.upstream.packages = ALL_PACKAGES
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch()
    context = await browser.new_context(viewport={"width": width, "height": height})
    context.set_default_navigation_timeout(WAIT)
    context.set_default_timeout(WAIT)
    jar = {"csa_session": "synthetic-session", **(cookies or {})}
    await context.add_cookies(
        [{"name": name, "value": value, "url": console.base} for name, value in jar.items()]
    )
    page = await context.new_page()

    async def route(request_route: Any) -> None:
        path_only = request_route.request.url.split("/console/v1/")[1].split("?")[0]
        if path_only == "auth/csrf":
            await request_route.fulfill(json={"csrf_token": "synthetic-csrf"})
        elif path_only == "services":
            await request_route.fulfill(json=[])
        elif path_only == "auth/logout" and logout is not None:
            logout[1].append(path_only)
            if logout[0] >= 400:
                await request_route.fulfill(status=logout[0], json={"detail": "Sign-out failed."})
            else:
                await request_route.fulfill(status=204)
        else:
            await request_route.fulfill(status=404, json={"detail": "not stubbed"})

    await page.route("**/console/v1/**", route)
    await page.goto(f"{console.base}{path}")
    return playwright, browser, page


async def close_page(playwright: Any, browser: Any) -> None:
    await browser.close()


EXPANDED = {"fs-rail": "expanded"}
GROUPS = {
    "business": (
        "Business management",
        {"orders": "Orders", "products": "Products & stock", "stores": "Store connections"},
    ),
    "knowledge_base": (
        "Knowledge base",
        {
            "library": "What it knows",
            "add": "Add knowledge",
            "questions": "Unanswered questions",
            "maintenance": "Maintenance and settings",
        },
    ),
}


def rail_nav(page: Page) -> Any:
    return page.get_by_test_id("shell-rail").get_by_role("navigation", name="Main navigation")


async def test_expanded_rail_group_opens_its_sub_pages(console: Console) -> None:
    playwright, browser, page = await open_page(console, "/portal/services", cookies=EXPANDED)
    try:
        for key, (_, children) in GROUPS.items():
            toggle = page.get_by_test_id(f"rail-group-{key}-toggle")
            listing = page.get_by_test_id(f"rail-group-{key}")
            await expect(toggle).to_have_attribute("aria-expanded", "false")
            await expect(toggle).to_have_accessible_name(f"{GROUPS[key][0]} pages")
            await expect(listing).to_be_hidden()
            await toggle.click()
            await expect(toggle).to_have_attribute("aria-expanded", "true")
            assert await toggle.get_attribute("aria-controls") == f"rail-group-{key}"
            assert await listing.get_attribute("id") == f"rail-group-{key}"
            for child_key in children:
                await expect(page.get_by_test_id(f"rail-child-{key}-{child_key}")).to_be_visible()
            await toggle.click()
            await expect(toggle).to_have_attribute("aria-expanded", "false")
            await expect(listing).to_be_hidden()
    finally:
        await close_page(playwright, browser)


async def test_group_of_the_current_page_is_open_on_arrival(console: Console) -> None:
    playwright, browser, page = await open_page(
        console, "/portal/business/products", cookies=EXPANDED
    )
    try:
        toggle = page.get_by_test_id("rail-group-business-toggle")
        await expect(toggle).to_have_attribute("aria-expanded", "true")
        products = page.get_by_test_id("rail-child-business-products")
        await expect(products).to_have_attribute("aria-current", "page")
        await expect(page.get_by_test_id("rail-child-business-orders")).not_to_have_attribute(
            "aria-current", "page"
        )
        parent = rail_nav(page).get_by_role("link", name="Business management", exact=True)
        await expect(parent).not_to_have_attribute("aria-current", "page")
        await expect(parent).to_have_attribute("data-group-current", "true")
        # The other group stays closed, and one link in the whole nav is current.
        await expect(page.get_by_test_id("rail-group-knowledge_base-toggle")).to_have_attribute(
            "aria-expanded", "false"
        )
        await expect(rail_nav(page).locator("[aria-current=page]")).to_have_count(1)
        # Closing the group hands the mark back to the parent link only on its own page.
        await toggle.click()
        await expect(parent).not_to_have_attribute("aria-current", "page")
    finally:
        await close_page(playwright, browser)


async def test_group_parent_page_marks_the_child_not_the_parent(console: Console) -> None:
    playwright, browser, page = await open_page(console, "/portal/business", cookies=EXPANDED)
    try:
        parent = rail_nav(page).get_by_role("link", name="Business management", exact=True)
        await expect(page.get_by_test_id("rail-child-business-orders")).to_have_attribute(
            "aria-current", "page"
        )
        await expect(parent).not_to_have_attribute("aria-current", "page")
        await page.get_by_test_id("rail-group-business-toggle").click()
        await expect(parent).to_have_attribute("aria-current", "page")
        await expect(rail_nav(page).locator("[aria-current=page]")).to_have_count(1)
    finally:
        await close_page(playwright, browser)


async def test_tab_order_is_link_then_chevron_then_children(console: Console) -> None:
    playwright, browser, page = await open_page(console, "/portal/business", cookies=EXPANDED)
    try:
        await rail_nav(page).get_by_role("link", name="Business management", exact=True).focus()
        order: list[str] = []
        for _ in range(4):
            await page.keyboard.press("Tab")
            order.append(
                await page.evaluate(
                    "() => document.activeElement.getAttribute('data-testid')"
                    " || document.activeElement.textContent.trim()"
                )
            )
        assert order == [
            "rail-group-business-toggle",
            "rail-child-business-orders",
            "rail-child-business-products",
            "rail-child-business-stores",
        ]
        # A closed group's children are skipped by the keyboard.
        await page.get_by_test_id("rail-group-business-toggle").focus()
        await page.keyboard.press("Enter")
        await expect(page.get_by_test_id("rail-group-business-toggle")).to_have_attribute(
            "aria-expanded", "false"
        )
        await page.keyboard.press("Tab")
        focused = await page.evaluate("() => document.activeElement.textContent.trim()")
        assert focused == "Subscriptions"
    finally:
        await close_page(playwright, browser)


async def test_collapsed_rail_shows_parent_icons_only(console: Console) -> None:
    playwright, browser, page = await open_page(console, "/portal/business/products")
    try:
        rail = page.get_by_test_id("shell-rail")
        await expect(rail).to_have_attribute("data-rail", "collapsed")
        nav = rail_nav(page)
        for label in TENANT_LABELS:
            await expect(nav.get_by_role("link", name=label, exact=True)).to_have_count(1)
        await expect(page.get_by_test_id("rail-group-business-toggle")).to_have_count(0)
        await expect(page.get_by_test_id("rail-group-knowledge_base-toggle")).to_have_count(0)
        for name in ("Orders", "Products & stock", "Store connections", "Add knowledge"):
            await expect(nav.get_by_role("link", name=name)).to_have_count(0)
        await expect(nav.get_by_role("button")).to_have_count(0)
        assert (await rail.bounding_box())["width"] == 56  # type: ignore[index]
    finally:
        await close_page(playwright, browser)


async def test_mobile_menu_groups_open_and_a_child_link_closes_the_drawer(
    console: Console,
) -> None:
    playwright, browser, page = await open_page(console, "/portal/services", width=390, height=844)
    try:
        await page.get_by_test_id("shell-menu").click()
        dialog = page.get_by_role("dialog")
        await expect(dialog).to_be_visible()
        nav = dialog.get_by_role("navigation", name="Main navigation")
        for label in TENANT_LABELS:
            await expect(nav.get_by_role("link", name=label, exact=True)).to_be_visible()
        toggle = page.get_by_test_id("menu-group-business-toggle")
        await expect(toggle).to_have_attribute("aria-expanded", "false")
        await expect(page.get_by_test_id("menu-child-business-products")).to_be_hidden()
        await toggle.click()
        await expect(toggle).to_have_attribute("aria-expanded", "true")
        assert await toggle.get_attribute("aria-controls") == "menu-group-business"
        await page.get_by_test_id("menu-child-business-products").click()
        await page.wait_for_url(f"{console.base}/portal/business/products")
        await expect(dialog).to_have_count(0)
        # Reopened on the sub-page, its group is already open and its page is current.
        await page.get_by_test_id("shell-menu").click()
        await expect(page.get_by_test_id("menu-group-business-toggle")).to_have_attribute(
            "aria-expanded", "true"
        )
        await expect(page.get_by_test_id("menu-child-business-products")).to_have_attribute(
            "aria-current", "page"
        )
        # Following the group's own link closes the drawer too.
        await dialog.get_by_role("link", name="Business management", exact=True).click()
        await page.wait_for_url(f"{console.base}/portal/business")
        await expect(dialog).to_have_count(0)
    finally:
        await close_page(playwright, browser)


async def test_child_links_navigate_to_the_sub_page_urls(console: Console) -> None:
    playwright, browser, page = await open_page(console, "/portal/services", cookies=EXPANDED)
    try:
        paths = {
            ("business", "products"): "/portal/business/products",
            ("business", "stores"): "/portal/business/stores",
            ("business", "orders"): "/portal/business",
            ("knowledge_base", "add"): "/portal/faq/add",
            ("knowledge_base", "questions"): "/portal/faq/questions",
            ("knowledge_base", "maintenance"): "/portal/faq/maintenance",
            ("knowledge_base", "library"): "/portal/faq",
        }
        for (group, child), path in paths.items():
            toggle = page.get_by_test_id(f"rail-group-{group}-toggle")
            if await toggle.get_attribute("aria-expanded") != "true":
                await toggle.click()
            await page.get_by_test_id(f"rail-child-{group}-{child}").click()
            await page.wait_for_url(f"{console.base}{path}")
            await expect(page.get_by_test_id(f"rail-child-{group}-{child}")).to_have_attribute(
                "aria-current", "page"
            )
        for group, (label, _) in GROUPS.items():
            link = rail_nav(page).get_by_role("link", name=label, exact=True)
            expected = "/portal/business" if group == "business" else "/portal/faq"
            assert await link.get_attribute("href") == expected
    finally:
        await close_page(playwright, browser)


async def test_platform_admin_sub_links_use_the_admin_paths(console: Console) -> None:
    playwright, browser, page = await open_page(
        console, "/knowledge-base/add", cookies=EXPANDED, role="platform_admin"
    )
    try:
        assert await page.get_by_test_id("rail-child-knowledge_base-add").get_attribute("href") == (
            "/knowledge-base/add"
        )
        await expect(page.get_by_test_id("rail-child-knowledge_base-add")).to_have_attribute(
            "aria-current", "page"
        )
        await page.get_by_test_id("rail-group-business-toggle").click()
        for child, path in (("products", "/business/products"), ("stores", "/business/stores")):
            link = page.get_by_test_id(f"rail-child-business-{child}")
            assert await link.get_attribute("href") == path
    finally:
        await close_page(playwright, browser)


@pytest.mark.parametrize("theme", THEMES)
async def test_rail_with_open_groups_fits_at_1024_and_1440_in_three_themes(
    console: Console, theme: str
) -> None:
    playwright, browser, page = await open_page(
        console, "/portal/business", cookies={**EXPANDED, "fs-theme": theme}
    )
    try:
        await expect(page.locator("html")).to_have_attribute("data-theme", theme)
        await page.get_by_test_id("rail-group-knowledge_base-toggle").click()
        for width in (1024, 1440):
            await page.set_viewport_size({"width": width, "height": 900})
            measured = await page.evaluate(
                """() => {
                    const rail = document
                        .querySelector('[data-testid="shell-rail"]').getBoundingClientRect();
                    const bad = [];
                    const items = document.querySelectorAll(
                        '[data-testid^="rail-child-"],'
                        + ' [data-testid$="-toggle"][data-testid^="rail-group-"]');
                    for (const el of items) {
                        const box = el.getBoundingClientRect();
                        if (box.right > rail.right + 0.5 || box.left < rail.left - 0.5
                            || el.scrollWidth > el.clientWidth + 1) bad.push(el.dataset.testid);
                    }
                    const root = document.documentElement;
                    return { bad, count: items.length, railWidth: rail.width,
                             overflow: root.scrollWidth - root.clientWidth };
                }"""
            )
            where = f"{theme} {width}px: {measured}"
            assert measured["count"] == 9, where  # two toggles and seven children
            assert measured["bad"] == [], where
            assert measured["overflow"] <= 0, where
            assert measured["railWidth"] == 240, where
    finally:
        await close_page(playwright, browser)


async def test_chevron_does_not_animate_under_reduced_motion(console: Console) -> None:
    playwright, browser, page = await open_page(console, "/portal/services", cookies=EXPANDED)
    try:
        chevron = "[data-testid='rail-group-business-toggle'] svg"
        await page.wait_for_function(
            "(s) => getComputedStyle(document.querySelector(s)).transitionDuration === '0.15s'",
            arg=chevron,
        )
        await page.emulate_media(reduced_motion="reduce")
        await page.reload()
        await expect(page.get_by_test_id("rail-group-business-toggle")).to_be_visible()
        await page.get_by_test_id("rail-group-business-toggle").click()
        await expect(page.get_by_test_id("rail-group-business-toggle")).to_have_attribute(
            "aria-expanded", "true"
        )
        style = await page.evaluate(
            "(s) => { const c = getComputedStyle(document.querySelector(s));"
            " return [c.transitionDuration, c.transitionProperty, c.transform]; }",
            chevron,
        )
        # The page-wide reduced-motion rule shortens every duration to a hair; the chevron's own
        # `transition: none` is what keeps it from animating.
        assert style[1] == "none" and float(style[0].rstrip("s")) < 0.001, style
        assert style[2] != "none", "the open chevron is still turned over, just without motion"
    finally:
        await close_page(playwright, browser)
