"""Real browser acceptance for the console shell: icon rail, labelled expand and mobile menu.

The real Next.js shell runs unmodified from a disposable staged copy. Its layouts fetch
`/auth/me` and `/services/active` server-side, so a loopback stub origin answers those;
every browser-side `/console/v1/*` call is answered by Playwright. One dev server serves
every test in this file.
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
                    self.reply(200, TENANT_PROFILE)
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
    "Runtime activity",
    "Knowledge base",
    "Sandbox assistant",
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
) -> tuple[Any, Any, Page]:
    """A fresh context signed in as `role` with every package active, on `path`.

    `logout` is the status the stubbed sign-out answers with and a list that records each call.
    """
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
    await playwright.stop()


async def test_rail_collapsed_links_keep_exact_names(console: Console) -> None:
    playwright, browser, page = await open_page(console, "/portal/services")
    try:
        rail = page.get_by_test_id("shell-rail")
        await expect(rail).to_have_attribute("data-rail", "collapsed")
        assert (await rail.bounding_box())["width"] == 56  # type: ignore[index]
        nav = page.get_by_role("navigation", name="Main navigation")
        for label in TENANT_LABELS:
            await expect(nav.get_by_role("link", name=label, exact=True)).to_have_count(1)
        # Collapsed, the label is not on screen; the link is still named by it.
        services = page.get_by_role("link", name="Services", exact=True)
        await expect(services).to_have_attribute("aria-current", "page")
        assert (await services.bounding_box())["width"] <= 56  # type: ignore[index]
        # The brand mark is the home link.
        await expect(
            page.get_by_role("link", name="fourstacks home", exact=True)
        ).to_have_attribute("href", "/portal")
    finally:
        await close_page(playwright, browser)


async def test_rail_tooltip_shows_on_hover_and_keyboard_focus(console: Console) -> None:
    playwright, browser, page = await open_page(console, "/portal/services")
    try:
        tip = page.get_by_role("tooltip")
        await expect(tip).to_have_count(0)
        await page.get_by_role("link", name="Customer inbox", exact=True).hover()
        await expect(tip).to_have_text("Customer inbox")
        await page.mouse.move(700, 400)
        await expect(tip).to_have_count(0)
        await page.get_by_role("link", name="Knowledge base", exact=True).focus()
        await page.keyboard.press("Tab")
        await expect(tip).to_have_text("Sandbox assistant")
        await page.keyboard.press("Escape")
        await expect(tip).to_have_count(0)
    finally:
        await close_page(playwright, browser)


async def test_rail_toggle_persists_across_reload(console: Console) -> None:
    playwright, browser, page = await open_page(console, "/portal/services")
    try:
        toggle = page.get_by_test_id("shell-rail-toggle")
        rail = page.get_by_test_id("shell-rail")
        await expect(toggle).to_have_accessible_name("Expand navigation")
        await toggle.click()
        await expect(rail).to_have_attribute("data-rail", "expanded")
        await expect(toggle).to_have_accessible_name("Collapse navigation")
        assert (await rail.bounding_box())["width"] == 240  # type: ignore[index]
        # Expanded, the label is on screen and still the link's name.
        services = page.get_by_role("link", name="Services", exact=True)
        await expect(services.get_by_text("Services", exact=True)).to_be_visible()
        cookies = {c.get("name"): c for c in await page.context.cookies()}
        assert cookies["fs-rail"].get("value") == "expanded"
        assert cookies["fs-rail"].get("sameSite") == "Lax"
        assert cookies["fs-rail"].get("path") == "/"

        response = await page.reload()
        assert response is not None
        # The server read the cookie: the first HTML already carries the state, no flash.
        assert 'data-rail="expanded"' in await response.text()
        await expect(rail).to_have_attribute("data-rail", "expanded")

        await toggle.click()
        await expect(rail).to_have_attribute("data-rail", "collapsed")
        response = await page.reload()
        assert response is not None
        assert 'data-rail="collapsed"' in await response.text()

        # A tampered cookie value falls back to collapsed.
        await page.context.add_cookies(
            [{"name": "fs-rail", "value": "<script>", "url": console.base}]
        )
        response = await page.reload()
        assert response is not None
        html = await response.text()
        assert 'data-rail="collapsed"' in html and "<script>" not in html.split("fs-rail")[-1][:40]
    finally:
        await close_page(playwright, browser)


async def test_mobile_menu_lists_all_links_and_closes_with_escape(console: Console) -> None:
    playwright, browser, page = await open_page(console, "/portal/services", width=390, height=844)
    try:
        await expect(page.get_by_test_id("shell-rail")).to_be_hidden()
        menu = page.get_by_test_id("shell-menu")
        await expect(menu).to_be_visible()
        dialog = page.get_by_role("dialog")
        await expect(dialog).to_have_count(0)
        await menu.click()
        await expect(dialog).to_be_visible()
        nav = dialog.get_by_role("navigation", name="Main navigation")
        for label in TENANT_LABELS:
            link = nav.get_by_role("link", name=label, exact=True)
            await expect(link).to_be_visible()
            await expect(link).to_contain_text(label)
        await page.keyboard.press("Escape")
        await expect(dialog).to_have_count(0)
        await expect(menu).to_be_focused()
    finally:
        await close_page(playwright, browser)


async def test_mobile_menu_closes_when_a_link_is_followed(console: Console) -> None:
    playwright, browser, page = await open_page(console, "/portal/services", width=390, height=844)
    try:
        await page.get_by_test_id("shell-menu").click()
        dialog = page.get_by_role("dialog")
        await dialog.get_by_role("link", name="Overview", exact=True).click()
        await page.wait_for_url(f"{console.base}/portal")
        await expect(dialog).to_have_count(0)
    finally:
        await close_page(playwright, browser)


@pytest.mark.parametrize("theme", THEMES)
async def test_no_page_overflow_at_320_and_1440_in_each_theme(console: Console, theme: str) -> None:
    playwright, browser, page = await open_page(
        console, "/portal/services", cookies={"fs-theme": theme}
    )
    try:
        await expect(page.locator("html")).to_have_attribute("data-theme", theme)
        for rail in ("collapsed", "expanded"):
            await page.context.add_cookies(
                [{"name": "fs-rail", "value": rail, "url": console.base}]
            )
            await page.reload()
            for width in WIDTHS:
                await page.set_viewport_size({"width": width, "height": 800})
                overflow = await page.evaluate(
                    "() => [document.documentElement.scrollWidth, window.innerWidth]"
                )
                assert overflow[0] <= overflow[1], f"{theme} {rail} {width}px: {overflow}"
    finally:
        await close_page(playwright, browser)


async def first_html(console: Console, page: Page, path: str) -> str:
    """The server's first response for `path`, with this context's cookies and no scripts run."""
    response = await page.request.get(f"{console.base}{path}")
    return await response.text()


async def test_theme_switch_applies_and_persists_without_flash(console: Console) -> None:
    playwright, browser, page = await open_page(console, "/portal/services")
    try:
        await expect(page.locator("html")).to_have_attribute("data-theme", "cobalt")
        await page.get_by_test_id("shell-theme").click()
        menu = page.get_by_role("menu")
        await expect(
            menu.get_by_role("menuitemradio", name="Cobalt", exact=True)
        ).to_have_attribute("aria-checked", "true")
        await menu.get_by_role("menuitemradio", name="Night", exact=True).click()
        await expect(page.locator("html")).to_have_attribute("data-theme", "night")
        await expect(menu).to_have_count(0)
        await expect(page.get_by_test_id("shell-theme")).to_be_focused()
        cookies = {c.get("name"): c for c in await page.context.cookies()}
        assert cookies["fs-theme"].get("value") == "night"
        assert cookies["fs-theme"].get("sameSite") == "Lax"

        # The server read the cookie: the first HTML, before any script runs, is already night.
        assert 'data-theme="night"' in await first_html(console, page, "/portal/services")
        response = await page.reload()
        assert response is not None
        assert 'data-theme="night"' in await response.text()
        await expect(page.locator("html[data-theme='night']")).to_have_count(1)
        # The control marks the stored theme after a reload too.
        await page.get_by_test_id("shell-theme").click()
        await expect(page.get_by_role("menuitemradio", name="Night", exact=True)).to_have_attribute(
            "aria-checked", "true"
        )
    finally:
        await close_page(playwright, browser)


async def test_tampered_theme_cookie_falls_back_to_cobalt(console: Console) -> None:
    playwright, browser, page = await open_page(
        console, "/portal/services", cookies={"fs-theme": "night"}
    )
    try:
        await expect(page.locator("html")).to_have_attribute("data-theme", "night")
        # A browser refuses to store some of these values, so send the raw Cookie header too.
        with httpx.Client(trust_env=False) as http:
            for value in (
                "night<script>alert(1)</script>",
                '"><script>alert(1)</script>',
                "NIGHT",
                "__proto__",
            ):
                response = http.get(
                    f"{console.base}/portal/services",
                    headers={"cookie": f"csa_session=synthetic-session; fs-theme={value}"},
                )
                html = response.text
                assert 'data-theme="cobalt"' in html, value
                assert "<script>alert(1)</script>" not in html, value
        await page.context.add_cookies(
            [{"name": "fs-theme", "value": "NIGHT", "url": console.base}]
        )
        await page.reload()
        await expect(page.locator("html")).to_have_attribute("data-theme", "cobalt")
        await expect(page.get_by_test_id("shell-theme")).to_be_visible()
    finally:
        await close_page(playwright, browser)


async def test_calm_motion_toggle_sets_pause_key(console: Console) -> None:
    playwright, browser, page = await open_page(console, "/portal/services")
    try:
        await page.get_by_test_id("shell-user-menu").click()
        toggle = page.get_by_test_id("shell-motion-toggle")
        await expect(toggle).to_have_attribute("role", "switch")
        await expect(toggle).to_have_accessible_name("Calm motion")
        await expect(toggle).to_have_attribute("aria-checked", "false")
        assert await page.evaluate("() => localStorage.getItem('fs-motion-paused')") is None
        await toggle.click()
        await expect(toggle).to_have_attribute("aria-checked", "true")
        assert await page.evaluate("() => localStorage.getItem('fs-motion-paused')") == "1"
        # The stored preference is what the switch shows after a reload.
        await page.reload()
        await page.get_by_test_id("shell-user-menu").click()
        await expect(page.get_by_test_id("shell-motion-toggle")).to_have_attribute(
            "aria-checked", "true"
        )
        await page.get_by_test_id("shell-motion-toggle").click()
        await expect(page.get_by_test_id("shell-motion-toggle")).to_have_attribute(
            "aria-checked", "false"
        )
        assert await page.evaluate("() => localStorage.getItem('fs-motion-paused')") is None
    finally:
        await close_page(playwright, browser)


async def test_user_menu_shows_identity_and_closes_with_escape(console: Console) -> None:
    playwright, browser, page = await open_page(console, "/portal/services")
    try:
        # The eyebrow header is gone and the title is not a second h1.
        await expect(page.get_by_text("CSA Console", exact=True)).to_have_count(0)
        assert await page.locator("h1").count() == 1
        trigger = page.get_by_test_id("shell-user-menu")
        await trigger.click()
        panel = page.get_by_test_id("shell-user-menu-panel")
        await expect(panel).to_contain_text("Synthetic User")
        await expect(panel).to_contain_text("Tenant admin")
        await page.keyboard.press("Escape")
        await expect(panel).to_have_count(0)
        await expect(trigger).to_be_focused()
        # The identity text left the rail.
        await expect(page.get_by_test_id("shell-rail")).not_to_contain_text("Synthetic User")
    finally:
        await close_page(playwright, browser)


async def test_sign_out_button_keeps_its_name_and_signs_out(console: Console) -> None:
    calls: list[str] = []
    playwright, browser, page = await open_page(console, "/portal/services", logout=(204, calls))
    try:
        # At desktop width the button is on the page without opening anything.
        sign_out = page.get_by_role("button", name="Sign out", exact=True)
        await expect(sign_out).to_have_count(1)
        await expect(sign_out).to_be_visible()
        await sign_out.click()
        await page.wait_for_url(f"{console.base}/login")
        assert calls == ["auth/logout"]
    finally:
        await close_page(playwright, browser)


async def test_theme_control_keyboard(console: Console) -> None:
    playwright, browser, page = await open_page(console, "/portal/services")
    try:
        trigger = page.get_by_test_id("shell-theme")
        await expect(trigger).to_have_attribute("aria-expanded", "false")
        await trigger.focus()
        await page.keyboard.press("Enter")
        await expect(trigger).to_have_attribute("aria-expanded", "true")
        items = page.get_by_role("menuitemradio")
        await expect(items).to_have_count(3)
        # Focus lands on the current theme; arrows move and wrap.
        await expect(items.nth(0)).to_be_focused()
        await page.keyboard.press("ArrowDown")
        await expect(items.nth(1)).to_be_focused()
        await page.keyboard.press("ArrowUp")
        await page.keyboard.press("ArrowUp")
        await expect(items.nth(2)).to_be_focused()
        await page.keyboard.press("Escape")
        await expect(page.get_by_role("menu")).to_have_count(0)
        await expect(trigger).to_be_focused()
        await expect(page.locator("html")).to_have_attribute("data-theme", "cobalt")
        # Space opens, Enter on an item chooses it and focus returns to the trigger.
        await page.keyboard.press("Space")
        await page.keyboard.press("ArrowDown")
        await page.keyboard.press("Enter")
        await expect(page.locator("html")).to_have_attribute("data-theme", "night")
        await expect(trigger).to_be_focused()
    finally:
        await close_page(playwright, browser)


async def test_signout_error_is_visible_in_mobile_layout(console: Console) -> None:
    calls: list[str] = []
    playwright, browser, page = await open_page(
        console, "/portal/services", width=390, height=844, logout=(500, calls)
    )
    try:
        # Below 1024 px the one Sign out control lives in the user menu.
        await expect(page.get_by_role("button", name="Sign out", exact=True)).to_have_count(0)
        await page.get_by_test_id("shell-user-menu").click()
        sign_out = page.get_by_role("button", name="Sign out", exact=True)
        await expect(sign_out).to_have_count(1)
        await sign_out.click()
        alert = page.get_by_role("alert").filter(has_text="Sign-out failed.")
        await expect(alert).to_be_visible()
        assert calls == ["auth/logout"]
        # No modal dialog is open over it, and it is the topmost thing at its own position.
        await expect(page.get_by_role("dialog")).to_have_count(0)
        box = await alert.bounding_box()
        assert box is not None
        covered = await page.evaluate(
            "([x, y]) => { const e = document.elementFromPoint(x, y);"
            " return !!e && !!e.closest('[role=alert]'); }",
            [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2],
        )
        assert covered
        # The user can try again.
        await page.get_by_test_id("shell-user-menu").click()
        await expect(page.get_by_role("button", name="Sign out", exact=True)).to_be_enabled()
    finally:
        await close_page(playwright, browser)
