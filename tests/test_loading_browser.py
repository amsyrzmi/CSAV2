"""Real browser acceptance for the loading screen (five walking agents).

The real Next.js shell runs unmodified from a disposable staged copy. Its layouts fetch
`/auth/me`, `/services/active` and `/profile` server-side, so a loopback stub origin answers
those, and it can hold any one of them back. Overview (`/portal`) became the client-side
assistant home and no longer waits on the server, so the staged copy replaces that one page
with a test-only server page that awaits `/profile` (production routing and the loading files
are unchanged). That is the deterministic trigger for the two loading files:

* in the shell: open `/portal/services`, then hold `/profile` back and follow the Overview
  link. The stand-in page awaits `/profile` on the server, so `app/portal/loading.tsx` shows
  inside the shell (the layout's own decorative read gives up after 3 s and lets the shell
  render); until then the root file shows full screen;
* full screen: open `/portal` with `/auth/me` held back. The layout awaits identity inside the
  root Suspense boundary, so `app/loading.tsx` streams first and shows alone.

A held answer is released by the test (or at teardown), after which the page replaces the screen.

Every browser-side `/console/v1/*` call is answered by Playwright. One dev server serves every
test in this file.
"""

from __future__ import annotations

import json
import os
import re
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
from playwright.async_api import Page, Route, async_playwright, expect

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
SLOW_PAGE = """import { requireOwnProfile } from "@/lib/server-identity";

export default async function SlowPage() {
  const profile = await requireOwnProfile();
  return <h1>Slow page for {profile.name}</h1>;
}
"""
AGENTS = ["support", "orders", "tickets", "bookings", "knowledge"]


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
        # Paths whose answers wait for the test: `gates[path].set()` lets them through.
        self.gates: dict[str, threading.Event] = {}
        self.unexpected: list[str] = []
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = self.path.split("?")[0]
                gate = upstream.gates.get(path)
                if gate is not None:
                    gate.wait(timeout=60)
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

    def hold(self, path: str) -> None:
        self.gates[path] = threading.Event()

    def release(self) -> None:
        for gate in self.gates.values():
            gate.set()
        self.gates = {}

    def __enter__(self) -> Upstream:
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class Console:
    def __init__(self, base: str, upstream: Upstream) -> None:
        self.base = base
        self.upstream = upstream


@pytest.fixture(scope="module")
def console(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    node = shutil.which("node")
    assert node, "Node is required for the browser loading acceptance"
    assert (WEB / "node_modules/next/dist/bin/next").is_file(), (
        "Console dependencies are missing; run `npm ci --ignore-scripts` in "
        "CSA_WebConsole/csa-console/web before browser loading acceptance"
    )
    tmp_path = tmp_path_factory.mktemp("loading")
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
        # Test-only stand-in for Overview in the disposable copy: a server page that waits.
        (stage / "app/portal/page.tsx").write_text(SLOW_PAGE)
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
                    pytest.fail(f"Synthetic loading frontend did not start; log: {log_path}")
            yield Console(f"http://127.0.0.1:{port}", upstream)
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.close()
            assert not upstream.unexpected, upstream.unexpected


async def open_console(
    console: Console,
    path: str,
    *,
    hold: str | None = None,
    width: int = 1280,
    height: int = 800,
    reduced_motion: bool = False,
    calm: bool = False,
) -> tuple[Any, Any, Page]:
    """A fresh signed-in context on `path`; `hold` is a stub path held back until released.

    The page is returned as soon as the response starts (`commit`), without waiting for the
    streamed document to finish.
    """
    console.upstream.me = make_me("tenant_admin")
    console.upstream.packages = [{"key": "customer_support", "status": "active"}]
    console.upstream.release()
    if hold:
        console.upstream.hold(hold)
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch()
    context = await browser.new_context(
        viewport={"width": width, "height": height},
        reduced_motion="reduce" if reduced_motion else "no-preference",
    )
    context.set_default_navigation_timeout(WAIT)
    context.set_default_timeout(WAIT)
    await context.add_cookies(
        [{"name": "csa_session", "value": "synthetic-session", "url": console.base}]
    )
    if calm:
        await context.add_init_script("window.localStorage.setItem('fs-motion-paused', '1')")
    page = await context.new_page()

    async def route(request_route: Route) -> None:
        path_only = request_route.request.url.split("/console/v1/")[1].split("?")[0]
        if path_only == "auth/csrf":
            await request_route.fulfill(json={"csrf_token": "synthetic-csrf"})
        elif path_only == "services":
            await request_route.fulfill(json=[])
        else:
            await request_route.fulfill(status=404, json={"detail": "not stubbed"})

    await page.route("**/console/v1/**", route)
    await page.goto(f"{console.base}{path}", wait_until="commit")
    return playwright, browser, page


async def close_console(console: Console, playwright: Any, browser: Any) -> None:
    console.upstream.release()
    await browser.close()
    await playwright.stop()


async def open_loading(console: Console, **options: Any) -> tuple[Any, Any, Page]:
    """The loading screen on screen, full screen, held there by a slow identity answer."""
    playwright, browser, page = await open_console(
        console, "/portal", hold="/console/v1/auth/me", **options
    )
    await expect(page.get_by_test_id("loading-screen")).to_be_visible(timeout=WAIT)
    # The streamed fallback can be visible before the parser has reached all five sprites.
    await expect(page.locator("[data-agent]")).to_have_count(5)
    return playwright, browser, page


async def open_shell_loading(console: Console, **options: Any) -> tuple[Any, Any, Page]:
    """The loading screen inside the shell: a client navigation to a slow server page.

    While `/profile` is held back the layout's own 3 s read gives up (it only decorates the user
    menu), the shell appears, and the stand-in Overview page keeps waiting: that is
    `app/portal/loading.tsx`.
    """
    playwright, browser, page = await open_console(console, "/portal/services", **options)
    await expect(page.get_by_test_id("shell-topbar")).to_be_visible()
    # Server HTML is visible before React attaches; a click before that is a full page load.
    await page.wait_for_function(
        "Object.keys(document.querySelector('[data-testid=shell-rail]')).some("
        "(key) => key.startsWith('__reactFiber'))"
    )
    console.upstream.hold("/console/v1/profile")
    # The link may sit in the closed mobile menu: a dispatched click still runs Next's handler.
    await page.locator('a[href="/portal"]').first.dispatch_event("click")
    # The first visit compiles the route in `next dev`, hence the long wait.
    await expect(page.get_by_test_id("loading-screen")).to_be_visible(timeout=WAIT)
    await expect(page.get_by_test_id("shell-topbar")).to_be_visible(timeout=20_000)
    await expect(page.get_by_test_id("loading-screen")).not_to_have_class(
        re.compile("fs-load-full")
    )
    await expect(page.get_by_test_id("loading-screen")).to_be_visible()
    return playwright, browser, page


async def test_loading_screen_shows_five_agents_and_the_word_loading(console: Console) -> None:
    """Both loading files: inside the shell on a slow page, alone on a slow first load."""
    for opener in (open_shell_loading, open_loading):
        playwright, browser, page = await opener(console)
        try:
            screen = page.get_by_test_id("loading-screen")
            await expect(screen).to_have_attribute("role", "status")
            await expect(screen).to_have_text("Loading")
            sprites = screen.locator("[data-agent]")
            await expect(sprites).to_have_count(5)
            assert [await sprites.nth(i).get_attribute("data-agent") for i in range(5)] == AGENTS
            for i in range(5):
                await expect(sprites.nth(i)).to_have_attribute("data-state", "rest")
            await expect(page.get_by_test_id("loading-ground")).to_have_count(1)
            # The full-screen fallback is never hydrated, so CSS alone animates it ("auto"); inside
            # the shell the page has hydrated and the hook has decided ("on").
            await expect(screen).to_have_attribute(
                "data-motion", "on" if opener is open_shell_loading else "auto"
            )
            if opener is open_shell_loading:
                await expect(page.get_by_test_id("shell-topbar")).to_be_visible()
            else:
                await expect(page.get_by_test_id("shell-topbar")).to_have_count(0)
            # Motion runs by default: the wave, the ground and the dots (the unhydrated full-screen
            # fallback animates from CSS alone).
            names = await page.evaluate(
                """() => {
                    const q = (s, p) =>
                        getComputedStyle(document.querySelector(s), p).animationName;
                    return [q('[data-testid=loading-walker]'), q('[data-testid=loading-ground]'),
                            q('[data-testid=loading-word]', '::after')];
                }"""
            )
            assert names == ["fs-load-step", "fs-load-ground", "fs-load-dots"]
            delays = await page.evaluate(
                """() => [...document.querySelectorAll('[data-testid=loading-walker]')]
                    .map((el) => getComputedStyle(el).animationDelay)"""
            )
            assert delays == ["0s", "0.1s", "0.2s", "0.3s", "0.4s"]
            # The page replaces it when the held answer arrives.
            console.upstream.release()
            await expect(screen).to_have_count(0)
            await expect(
                page.get_by_role("heading", name="Slow page for Synthetic Tenant")
            ).to_be_visible()
        finally:
            await close_console(console, playwright, browser)


async def assert_still(page: Page) -> None:
    screen = page.get_by_test_id("loading-screen")
    await expect(screen).to_have_text("Loading")
    await expect(screen.locator("[data-agent]")).to_have_count(5)
    names = await page.evaluate(
        """() => {
            const q = (s, p) => getComputedStyle(s, p).animationName;
            const walkers = [...document.querySelectorAll('[data-testid=loading-walker]')];
            const ground = document.querySelector('[data-testid=loading-ground]');
            const word = document.querySelector('[data-testid=loading-word]');
            return [...walkers.map((el) => q(el)), q(ground), q(word, '::after')];
        }"""
    )
    assert names == ["none"] * 7, names
    running = await page.evaluate("document.getAnimations().map((a) => a.animationName)")
    assert not [name for name in running if str(name).startswith("fs-load")], running


async def test_loading_screen_is_still_under_reduced_motion(console: Console) -> None:
    for opener in (open_shell_loading, open_loading):
        playwright, browser, page = await opener(console, reduced_motion=True)
        try:
            await assert_still(page)
        finally:
            await close_console(console, playwright, browser)


async def test_loading_screen_respects_calm_motion(console: Console) -> None:
    for opener in (open_shell_loading, open_loading):
        playwright, browser, page = await opener(console, calm=True)
        try:
            await assert_still(page)
        finally:
            await close_console(console, playwright, browser)


async def test_loading_screen_fits_320_px(console: Console) -> None:
    for opener in (open_loading, open_shell_loading):
        playwright, browser, page = await opener(console, width=320, height=640)
        try:
            await expect(
                page.get_by_test_id("loading-screen").locator("[data-agent]")
            ).to_have_count(5)
            measured = await page.evaluate(
                """() => ({
                    scroll: document.documentElement.scrollWidth,
                    client: document.documentElement.clientWidth,
                    agents: [...document.querySelectorAll('[data-agent]')].map((el) => {
                        const r = el.getBoundingClientRect();
                        return [r.left, r.right, r.width];
                    }),
                })"""
            )
            assert measured["scroll"] <= measured["client"] == 320, measured
            for left, right, width in measured["agents"]:
                assert left >= 0 and right <= 320, measured
                assert width == 48, measured  # the 1x sprite below 560 px
        finally:
            await close_console(console, playwright, browser)


async def test_loading_screen_sprites_are_96_px_on_wide_screens(console: Console) -> None:
    playwright, browser, page = await open_loading(console, width=1440, height=900)
    try:
        sizes = await page.evaluate(
            """() => [...document.querySelectorAll('[data-agent]')].map((el) => {
                const r = el.getBoundingClientRect();
                return [r.width, r.height];
            })"""
        )
        assert sizes == [[96.0, 96.0]] * 5, sizes
        screen = await page.get_by_test_id("loading-screen").bounding_box()
        assert screen is not None and screen["height"] >= 900  # full screen centres in the viewport
    finally:
        await close_console(console, playwright, browser)
