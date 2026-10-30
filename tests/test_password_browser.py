"""Real browser acceptance for the in-shell Password page.

The real Next.js shell runs unmodified from a disposable staged copy. Its layouts fetch
`/auth/me` and `/services/active` server-side, so a loopback stub origin answers those;
every browser-side `/console/v1/*` call is answered by Playwright. One dev server serves
every test in this file.
"""

from __future__ import annotations

import asyncio
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
CURRENT = "synthetic-current-password"
NEW = "synthetic-new-password-1"


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
        self.unexpected: list[str] = []
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = self.path.split("?")[0]
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


class Console:
    def __init__(self, base: str, upstream: Upstream) -> None:
        self.base = base
        self.upstream = upstream


@pytest.fixture(scope="module")
def console(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    node = shutil.which("node")
    assert node, "Node is required for the browser password acceptance"
    assert (WEB / "node_modules/next/dist/bin/next").is_file(), (
        "Console dependencies are missing; run `npm ci --ignore-scripts` in "
        "CSA_WebConsole/csa-console/web before browser password acceptance"
    )
    tmp_path = tmp_path_factory.mktemp("password")
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
                    pytest.fail(f"Synthetic password frontend did not start; log: {log_path}")
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


# What the stubbed password change answers: (status, body), or an async callable for a slow one.
Reply = tuple[int, Any]


async def open_page(
    console: Console,
    path: str,
    *,
    role: str = "tenant_admin",
    signed_in: bool = True,
    answers: list[Reply] | None = None,
    delay: float = 0.0,
) -> tuple[Any, Any, Page, list[Any]]:
    """A fresh context on `path`; `posts` records every password-change POST.

    `answers` is consumed one per POST (the last repeats); the default is a success
    answering with the signed-in `Me`. `delay` holds each answer back that many seconds.
    """
    console.upstream.me = make_me(role)
    console.upstream.packages = [{"key": "customer_support", "status": "active"}]
    replies = answers or [(200, make_me(role))]
    posts: list[Any] = []
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch()
    context = await browser.new_context(viewport={"width": 1280, "height": 800})
    context.set_default_navigation_timeout(WAIT)
    context.set_default_timeout(WAIT)
    if signed_in:
        await context.add_cookies(
            [{"name": "csa_session", "value": "synthetic-session", "url": console.base}]
        )
    page = await context.new_page()

    async def route(request_route: Route) -> None:
        request = request_route.request
        path_only = request.url.split("/console/v1/")[1].split("?")[0]
        if path_only == "auth/csrf":
            await request_route.fulfill(json={"csrf_token": "synthetic-csrf"})
        elif path_only == "auth/password/change" and request.method == "POST":
            posts.append(request.post_data_json)
            if delay:
                await asyncio.sleep(delay)
            status, body = replies[min(len(posts), len(replies)) - 1]
            await request_route.fulfill(status=status, json=body)
        elif path_only == "services":
            await request_route.fulfill(json=[])
        else:
            await request_route.fulfill(status=404, json={"detail": "not stubbed"})

    await page.route("**/console/v1/**", route)
    await page.goto(f"{console.base}{path}")
    return playwright, browser, page, posts


async def close_page(playwright: Any, browser: Any) -> None:
    await browser.close()
    await playwright.stop()


async def fill_form(page: Page, current: str, new: str, confirm: str) -> None:
    await page.get_by_test_id("password-current").fill(current)
    await page.get_by_test_id("password-new").fill(new)
    await page.get_by_test_id("password-confirm").fill(confirm)


async def test_password_nav_opens_the_in_shell_page(console: Console) -> None:
    playwright, browser, page, _ = await open_page(console, "/portal/services")
    try:
        await page.get_by_role("link", name="Password", exact=True).click()
        await expect(page).to_have_url(f"{console.base}/portal/password")
        await expect(page.get_by_test_id("shell-rail")).to_be_visible()
        await expect(page.get_by_test_id("shell-topbar")).to_be_visible()
        await expect(page.get_by_test_id("shell-title")).to_have_text("Password")
        await expect(page.get_by_role("heading", name="Password", level=1)).to_be_visible()
        await expect(page.get_by_role("link", name="Password", exact=True)).to_have_attribute(
            "aria-current", "page"
        )
        for name in ("Current password", "New password", "Confirm new password"):
            await expect(page.get_by_label(name, exact=True)).to_be_visible()
        await expect(page.get_by_text("At least 12 characters.")).to_be_visible()
    finally:
        await close_page(playwright, browser)


async def test_password_page_for_a_platform_admin_lives_at_password(console: Console) -> None:
    playwright, browser, page, _ = await open_page(console, "/", role="platform_admin")
    try:
        await page.get_by_role("link", name="Password", exact=True).click()
        await expect(page).to_have_url(f"{console.base}/password")
        await expect(page.get_by_test_id("shell-rail")).to_be_visible()
        await expect(page.get_by_test_id("password-submit")).to_be_visible()
    finally:
        await close_page(playwright, browser)


async def test_password_change_success_stays_and_confirms(console: Console) -> None:
    playwright, browser, page, posts = await open_page(console, "/portal/password")
    try:
        await fill_form(page, CURRENT, NEW, NEW)
        await page.get_by_test_id("password-submit").click()
        success = page.get_by_test_id("password-success")
        await expect(success).to_have_text("Password updated. Other devices have been signed out.")
        await expect(success).to_have_attribute("role", "status")
        assert posts == [{"current_password": CURRENT, "new_password": NEW}]
        await expect(page).to_have_url(f"{console.base}/portal/password")
        for test_id in ("password-current", "password-new", "password-confirm"):
            await expect(page.get_by_test_id(test_id)).to_have_value("")
        await expect(page.get_by_test_id("password-error")).to_have_count(0)
        await expect(page.get_by_test_id("shell-rail")).to_be_visible()
    finally:
        await close_page(playwright, browser)


async def test_password_mismatch_blocks_the_request(console: Console) -> None:
    playwright, browser, page, posts = await open_page(console, "/portal/password")
    try:
        await fill_form(page, CURRENT, NEW, NEW + "-different")
        await page.get_by_test_id("password-submit").click()
        error = page.get_by_test_id("password-error")
        await expect(error).to_have_text("The new passwords do not match.")
        await expect(error).to_have_attribute("role", "alert")
        assert posts == []
        await expect(page.get_by_test_id("password-new")).to_have_value(NEW)
    finally:
        await close_page(playwright, browser)


async def test_password_server_error_is_shown(console: Console) -> None:
    answers: list[Reply] = [
        (429, {"detail": "too many attempts; try again later"}),
        (422, {"detail": "new password must differ from the current password"}),
    ]
    playwright, browser, page, posts = await open_page(console, "/portal/password", answers=answers)
    try:
        await fill_form(page, CURRENT, NEW, NEW)
        submit = page.get_by_test_id("password-submit")
        await submit.click()
        error = page.get_by_test_id("password-error")
        await expect(error).to_have_text("too many attempts; try again later")
        await expect(error).to_have_attribute("role", "alert")
        # The typed values stay so the person can correct and retry.
        await expect(page.get_by_test_id("password-current")).to_have_value(CURRENT)
        await expect(page.get_by_test_id("password-new")).to_have_value(NEW)
        await expect(page.get_by_test_id("password-confirm")).to_have_value(NEW)
        await expect(submit).to_be_enabled()
        await submit.click()
        await expect(error).to_have_text("new password must differ from the current password")
        assert len(posts) == 2
        await expect(page.get_by_test_id("password-success")).to_have_count(0)
        await expect(page.get_by_test_id("password-new")).to_have_value(NEW)
    finally:
        await close_page(playwright, browser)


async def test_password_double_submit_sends_one_request(console: Console) -> None:
    playwright, browser, page, posts = await open_page(console, "/portal/password", delay=1.5)
    try:
        await fill_form(page, CURRENT, NEW, NEW)
        submit = page.get_by_test_id("password-submit")
        # Two submits in the same task: the second finds the first still in flight.
        await submit.evaluate("(button) => { button.click(); button.click(); }")
        await expect(submit).to_be_disabled()
        await expect(page.get_by_test_id("password-success")).to_be_visible()
        assert len(posts) == 1
    finally:
        await close_page(playwright, browser)


async def test_change_password_page_still_works(console: Console) -> None:
    playwright, browser, page, posts = await open_page(console, "/change-password", signed_in=True)
    try:
        # The forced-change page keeps its own look: no rail and no top bar.
        await expect(page.get_by_test_id("shell-rail")).to_have_count(0)
        await expect(page.get_by_test_id("shell-topbar")).to_have_count(0)
        for name in ("Current password", "New password", "Confirm new password"):
            await expect(page.get_by_label(name, exact=True)).to_be_visible()
        await expect(page.get_by_role("button", name="Save password", exact=True)).to_be_visible()
        assert posts == []
    finally:
        await close_page(playwright, browser)
