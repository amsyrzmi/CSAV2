"""Real browser acceptance for the cobalt auth pages (AuthCard).

The real Next.js app runs unmodified from a disposable staged copy. The auth pages are
static client pages, so no upstream call is made while they load; a loopback stub origin
exists only so the staged build has a gateway origin, and every browser-side
`/console/v1/*` call is answered by Playwright. One dev server serves every test here.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.async_api import Page, Route, async_playwright, expect

pytestmark = pytest.mark.asyncio

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "CSA_WebConsole/csa-console/web"
WAIT = 60_000  # First visit to each route compiles it in `next dev`.
TAGLINE = "Your customer team, always on."
OLD_COPY = (
    "A clear view of your workspace",
    "Your identity, verified",
    "The right level of access",
    "Accountable administration",
    "Restricted workspace access",
    "CSA Console",
)
# Public pages and the forced-change page; /invite reads its token from the hash.
ROUTES = ("/login", "/forgot-password", "/change-password", "/invite#token=synthetic")
THEMES = ("cobalt", "night", "bureau")
WIDTHS = (320, 390, 768, 1024, 1440)
COBALT_FROM = "rgb(0, 71, 171)"
COBALT_TO = "rgb(45, 124, 240)"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass
class Console:
    base: str
    posts: list[Any]


@pytest.fixture(scope="module")
def console(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    node = shutil.which("node")
    assert node, "Node is required for the browser auth acceptance"
    assert (WEB / "node_modules/next/dist/bin/next").is_file(), (
        "Console dependencies are missing; run `npm ci --ignore-scripts` in "
        "CSA_WebConsole/csa-console/web before browser auth acceptance"
    )
    tmp_path = tmp_path_factory.mktemp("auth")
    stage = tmp_path / "web"
    environment = {
        "PATH": os.environ["PATH"],
        "NEXT_TELEMETRY_DISABLED": "1",
        # Nothing listens here: the auth pages make no server-side upstream call.
        "CSA_CONSOLE_API_ORIGIN": "http://127.0.0.1:9",
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
                pytest.fail(f"Synthetic auth frontend did not start; log: {log_path}")
        yield Console(f"http://127.0.0.1:{port}", [])
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()


async def new_context(
    console: Console,
    browser: Any,
    *,
    theme: str = "cobalt",
    width: int = 1440,
    reduced_motion: str = "no-preference",
) -> Any:
    context = await browser.new_context(
        viewport={"width": width, "height": 800}, reduced_motion=reduced_motion
    )
    context.set_default_navigation_timeout(WAIT)
    context.set_default_timeout(WAIT)
    # /change-password is behind the session-cookie check; the page itself is static.
    await context.add_cookies(
        [
            {"name": "csa_session", "value": "synthetic-session", "url": console.base},
            {"name": "fs-theme", "value": theme, "url": console.base},
        ]
    )

    async def route(request_route: Route) -> None:
        request = request_route.request
        path_only = request.url.split("/console/v1/")[1].split("?")[0]
        if path_only == "auth/login" and request.method == "POST":
            console.posts.append(request.post_data_json)
            await request_route.fulfill(status=401, json={"detail": "synthetic wrong password"})
        elif path_only == "auth/csrf":
            await request_route.fulfill(json={"csrf_token": "synthetic-csrf"})
        else:
            await request_route.fulfill(status=404, json={"detail": "not stubbed"})

    await context.route("**/console/v1/**", route)
    return context


async def open_route(context: Any, console: Console, route: str) -> Page:
    page = await context.new_page()
    await page.goto(f"{console.base}{route}")
    await page.get_by_test_id("auth-title").wait_for()
    return page


async def test_auth_pages_show_logo_and_tagline_and_none_of_the_old_copy(
    console: Console,
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await new_context(console, browser)
            for route in ROUTES:
                page = await open_route(context, console, route)
                text = await page.locator("body").inner_text()
                assert TAGLINE in text, route
                assert "fourstacks" in text, route
                for old in OLD_COPY:
                    assert old not in text, f"{route} still says {old!r}"
                logo = page.get_by_test_id("auth-panel").get_by_test_id("mito-mark")
                await expect(logo).to_be_visible()
                box = await logo.bounding_box()
                assert box is not None and 100 <= box["width"] <= 140, (route, box)
                assert await page.get_by_role("heading", level=1).count() == 1, route
                await page.close()
        finally:
            await browser.close()


async def test_auth_pages_fit_five_widths_in_three_themes(console: Console) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            for theme in THEMES:
                context = await new_context(console, browser, theme=theme)
                page = await context.new_page()
                for width in WIDTHS:
                    await page.set_viewport_size({"width": width, "height": 800})
                    for route in ROUTES:
                        await page.goto(f"{console.base}{route}")
                        await page.get_by_test_id("auth-title").wait_for()
                        where = f"{theme} {width}px {route}"
                        sizes = await page.evaluate(
                            "() => [document.documentElement.scrollWidth,"
                            " document.documentElement.clientWidth]"
                        )
                        assert sizes[0] <= sizes[1], f"{where}: scrolls sideways {sizes}"
                        if width < 1024:
                            banner = page.get_by_test_id("auth-banner")
                            await expect(banner).to_be_visible()
                            await expect(page.get_by_test_id("auth-panel")).to_be_hidden()
                            logo = banner.locator("svg[viewBox='0 0 48 52']")
                            await expect(logo).to_be_visible()
                            box = await logo.bounding_box()
                            assert box is not None and 36 <= box["width"] <= 44, (where, box)
                            await expect(banner.get_by_text("fourstacks")).to_be_visible()
                        else:
                            await expect(page.get_by_test_id("auth-panel")).to_be_visible()
                            await expect(page.get_by_test_id("auth-banner")).to_be_hidden()
                        # The form stays usable: the first control and the submit button sit
                        # fully inside the viewport width.
                        controls = page.locator("main form input:visible, main form button:visible")
                        assert await controls.count() >= 1, where
                        for index in range(await controls.count()):
                            control = await controls.nth(index).bounding_box()
                            assert control is not None, where
                            assert control["x"] >= 0 and control["x"] + control["width"] <= width, (
                                where,
                                control,
                            )
                await context.close()
        finally:
            await browser.close()


async def test_auth_left_panel_is_brand_cobalt_in_every_theme(console: Console) -> None:
    seen: dict[str, tuple[str, str, str, str]] = {}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            for theme in THEMES:
                for width, test_id in ((1440, "auth-panel"), (390, "auth-banner")):
                    context = await new_context(console, browser, theme=theme, width=width)
                    page = await open_route(context, console, "/login")
                    panel = page.get_by_test_id(test_id)
                    await expect(panel).to_be_visible()
                    seen[f"{theme}-{test_id}"] = tuple(  # type: ignore[assignment]
                        await panel.evaluate(
                            """(el) => {
                              const wordmark = el.querySelector(
                                '[data-testid$="wordmark"]'
                              );
                              const logo = el.querySelectorAll('svg path');
                              return [
                                getComputedStyle(el).backgroundImage,
                                getComputedStyle(wordmark).color,
                                getComputedStyle(logo[0]).fill,
                                getComputedStyle(logo[2]).fill,
                              ];
                            }"""
                        )
                    )
                    await context.close()
        finally:
            await browser.close()
    for kind in ("auth-panel", "auth-banner"):
        reference = seen[f"cobalt-{kind}"]
        assert COBALT_FROM in reference[0] and COBALT_TO in reference[0], reference
        assert reference[1] == "rgb(255, 255, 255)", reference
        assert reference[2] == "rgb(255, 255, 255)", reference
        assert reference[3] == "rgb(127, 176, 255)", reference
        for theme in THEMES:
            assert seen[f"{theme}-{kind}"] == reference, (theme, kind, seen[f"{theme}-{kind}"])


async def test_panel_text_sits_where_the_gradient_is_dark_enough(console: Console) -> None:
    """theme-contrast.test.mjs assumes the text never passes 75% of the gradient.

    This measures it: the farthest corner of the wordmark and the tagline along the 155deg gradient
    line, as a fraction of the panel's gradient length, at the narrowest and widest panel layouts.
    """
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            for width, height in ((1024, 600), (1440, 900), (1920, 1080)):
                context = await new_context(console, browser, width=width)
                page = await open_route(context, console, "/login")
                await page.set_viewport_size({"width": width, "height": height})
                panel = page.get_by_test_id("auth-panel")
                await expect(panel).to_be_visible()
                farthest = await page.evaluate(
                    """() => {
                      const panel = document
                        .querySelector('[data-testid="auth-panel"]').getBoundingClientRect();
                      const angle = (155 * Math.PI) / 180;
                      const dx = Math.sin(angle), dy = -Math.cos(angle);
                      const length = Math.abs(panel.width * dx) + Math.abs(panel.height * dy);
                      const cx = panel.x + panel.width / 2, cy = panel.y + panel.height / 2;
                      let worst = 0;
                      for (const id of ['auth-wordmark', 'auth-tagline']) {
                        const r = document
                          .querySelector(`[data-testid="${id}"]`).getBoundingClientRect();
                        const corners = [[r.left, r.top], [r.right, r.top],
                          [r.left, r.bottom], [r.right, r.bottom]];
                        for (const [x, y] of corners)
                          worst = Math.max(worst, ((x - cx) * dx + (y - cy) * dy) / length + 0.5);
                      }
                      return worst;
                    }"""
                )
                assert farthest <= 0.75, (width, height, farthest)
                await context.close()
        finally:
            await browser.close()


async def drop_animations(page: Page) -> list[str]:
    return await page.evaluate(
        """() => [...document.querySelectorAll('[data-testid="auth-panel"] .mito-lobe-a')]
          .flatMap((lobe) => lobe.getAnimations())
          .map((animation) => animation.animationName)
          .filter((name) => name === 'mito-apart-a')"""
    )


async def test_auth_logo_is_still_under_reduced_motion(console: Console) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            # Control: with motion allowed the Mito lobes do move apart and back.
            context = await new_context(console, browser)
            page = await open_route(context, console, "/login")
            await page.wait_for_function(
                """() => [...document.querySelectorAll('[data-testid="auth-panel"] .mito-lobe-a')]
                  .flatMap((lobe) => lobe.getAnimations())
                  .filter((a) => a.animationName === 'mito-apart-a').length >= 1"""
            )
            await context.close()

            # The operating-system preference.
            context = await new_context(console, browser, reduced_motion="reduce")
            page = await open_route(context, console, "/login")
            await page.wait_for_timeout(1200)
            assert await drop_animations(page) == []
            await expect(page.get_by_test_id("auth-panel").locator("svg")).to_be_visible()
            await context.close()

            # The product's Calm motion preference.
            context = await new_context(console, browser)
            await context.add_init_script("window.localStorage.setItem('fs-motion-paused', '1')")
            page = await open_route(context, console, "/login")
            await page.wait_for_timeout(1200)
            assert await drop_animations(page) == []
            await context.close()
        finally:
            await browser.close()


async def test_login_form_still_works(console: Console) -> None:
    console.posts.clear()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await new_context(console, browser)
            page = await open_route(context, console, "/login")
            await expect(page.get_by_role("heading", name="Sign in", exact=True)).to_be_visible()
            await page.get_by_label("Email", exact=True).fill("synthetic@example.test")
            await page.get_by_label("Password", exact=True).fill("synthetic-password-1")
            await page.get_by_role("button", name="Sign in", exact=True).click()
            await expect(page.get_by_text("synthetic wrong password")).to_be_visible()
            assert console.posts == [
                {"email": "synthetic@example.test", "password": "synthetic-password-1"}
            ]
            await expect(page.get_by_role("link", name="Forgot password?")).to_be_visible()
            await expect(page).to_have_url(f"{console.base}/login")
        finally:
            await browser.close()
