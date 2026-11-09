"""Real browser acceptance for Overview as the assistant home.

The real Next.js shell and Overview pages run unmodified from a disposable staged copy. The
layouts fetch `/auth/me`, `/services/active` and `/profile` server-side, so a loopback stub origin
answers those; every browser-side `/console/v1/*` call (the status strip's two reads, the tenant
list and the sandbox chat) is answered by Playwright, recorded, and an unexpected path fails the
test, as do page errors and console errors. One dev server serves every test in this file.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
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
WAIT = 60_000  # First visit to each route compiles it in `next dev`.
REPLY = "Our opening hours are nine to six on weekdays."
# The old header sentence; the honesty line under the composer replaced it.
REMOVED_LABEL = "Private chat: ask about your knowledge base and products"
MEMBERSHIP = "Your account has no active company. Ask your administrator to check your access."
WIDTHS = (320, 390, 768, 1024, 1440)
THEMES = ("cobalt", "night", "bureau")
CLIENTS = [{"id": "tenant-a", "name": "Tenant A"}, {"id": "tenant-b", "name": "Tenant B"}]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_me(role: str | None, *, tenant: bool = True, assistant: bool = False) -> dict[str, Any]:
    admin = role == "platform_admin"
    member = role is not None and not admin and tenant
    return {
        "user": {
            "id": "22222222-2222-4222-8222-222222222222",
            "email": "synthetic@example.test",
            "display_name": "Synthetic User",
            "is_platform_admin": admin,
            "must_change_password": False,
        },
        "memberships": [{"client_id": TENANT, "role": role}] if member else [],
        "active_client_id": TENANT if member else None,
        "role": role,
        "csrf_token": "synthetic-csrf",
        "workspace_assistant": assistant,
    }


class Upstream:
    """Loopback stand-in for the gateway origin used by server-side layout fetches."""

    def __init__(self) -> None:
        self.me = make_me("tenant_staff")
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
                    self.reply(200, {"id": TENANT, "name": "Synthetic Tenant"})
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


def package(key: str, status: str = "active") -> dict[str, str]:
    return {"key": key, "status": status}


SUPPORT_AND_HR = [package("customer_support"), package("hr"), package("staff_email", "suspended")]


class Api:
    """The browser-side console API as Playwright answers it, with every request recorded.

    `packages` and `review` are what the strip's reads answer; None answers 500. `by_tenant`
    answers the packages of a named company instead, as a platform admin's switch needs.
    `warnings` is what the Go live banner's read reports.
    """

    def __init__(
        self,
        *,
        packages: list[dict[str, str]] | None = None,
        review: int | None = 3,
        turns: int = 0,
        by_tenant: dict[str, list[dict[str, str]]] | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        self.packages = SUPPORT_AND_HR if packages is None else packages
        self.by_tenant = by_tenant or {}
        self.warnings = warnings or []
        self.packages_fail = False
        self.packages_delay = 0.0
        self.review = review
        self.threads: list[dict[str, Any]] = []
        self.turns: dict[str, list[dict[str, Any]]] = {}
        self.requests: list[tuple[str, str]] = []
        self.unexpected: list[str] = []
        self.deliberate_failures = 0
        if turns:
            self.threads = [thread(1)]
            self.turns["thread-1"] = [turn(n, f"Question {n}") for n in range(1, turns + 1)]

    def packages_for(self, tail: str) -> list[dict[str, str]]:
        tenant = re.search(r"client_id=([^&]+)", tail)
        return self.by_tenant.get(tenant.group(1), self.packages) if tenant else self.packages

    def paths(self, prefix: str) -> list[str]:
        return [tail for method, tail in self.requests if tail.split("?")[0] == prefix]

    async def fail(self, route: Route) -> None:
        self.deliberate_failures += 1
        await route.fulfill(status=500, json={"detail": "synthetic outage"})

    async def route(self, route: Route) -> None:
        request = route.request
        tail = request.url.split("/console/v1/")[1]
        path = tail.split("?")[0]
        self.requests.append((request.method, tail))
        data: Any
        if path == "auth/csrf":
            data = {"csrf_token": "synthetic-csrf"}
        elif path == "services/active" and request.method == "GET":
            if self.packages_fail:
                await self.fail(route)
                return
            if self.packages_delay:
                await asyncio.sleep(self.packages_delay)
            data = {"packages": self.packages_for(tail)}
        elif path == "inbox/conversations" and request.method == "GET":
            if self.review is None:
                await self.fail(route)
                return
            data = {"items": [], "next_cursor": None, "needs_review_count": self.review}
        elif path == "services/customer-replies" and request.method == "GET":
            # The gateway answers Customer support active or suspended and refuses everyone else.
            # The page only asks where the chat shows, so a refusal here would be a console error.
            support = {
                item["status"]
                for item in self.packages_for(tail)
                if item["key"] == "customer_support"
            }
            if not support & {"active", "suspended"}:
                self.unexpected.append(f"{request.method} {tail} (no Customer support)")
                await route.fulfill(status=403, json={"detail": "package_inactive"})
                return
            data = {
                "mode": "off" if self.warnings else "auto",
                "brand_name": "Synthetic",
                "can_manage": True,
                "warnings": self.warnings,
            }
        elif path == "admin/clients" and request.method == "GET":
            data = CLIENTS
        elif path == "hr/context" and request.method == "GET":
            # The rail asks for the HR tabs of a company with the HR package; a member with no
            # access beyond Home (the shape of `HrContext`). A refused read is a console error.
            data = {
                "employee": None,
                "client_owner": False,
                "private_areas": [],
                "actions": [],
                "switches": [],
                "workflows": [],
                "hr_duties": False,
            }
        elif path == "chat/voice" and request.method == "GET":
            data = {"enabled": False}
        elif path == "chat/conversations" and request.method == "GET":
            data = {"items": self.threads, "next_cursor": None}
        elif path == "chat/conversations" and request.method == "POST":
            created = thread(len(self.threads) + 1)
            self.threads.insert(0, created)
            await route.fulfill(status=201, json=created)
            return
        elif re.fullmatch(r"chat/conversations/[\w-]+/turns", path):
            items = self.turns.setdefault(path.split("/")[2], [])
            if request.method == "POST":
                body = request.post_data_json
                assert body is not None and set(body) == {"request_key", "body"}
                items.append(turn(len(items) + 1, body["body"], body["request_key"]))
                data = {"duplicate": False}
            else:
                data = {"items": items, "next_cursor": None}
        else:
            self.unexpected.append(f"{request.method} {tail}")
            await route.fulfill(status=500, json={"detail": "unexpected synthetic call"})
            return
        await route.fulfill(json=data)


def thread(sequence: int) -> dict[str, Any]:
    return {
        "id": f"thread-{sequence}",
        "sequence": sequence,
        "created_at": f"2026-10-2{sequence}T00:00:00Z",
        "archived_at": None,
    }


def turn(number: int, body: str, key: str = "") -> dict[str, Any]:
    return {
        "id": number,
        "trace_id": "trace",
        "request_key": key or f"key-{number}",
        "body": body,
        "created_at": "2026-10-30T00:00:00Z",
        "status": "available",
        "replies": [{"id": f"reply-{number}", "body": REPLY}],
    }


class Console:
    def __init__(self, base: str, upstream: Upstream) -> None:
        self.base = base
        self.upstream = upstream


@pytest.fixture(scope="module")
def console(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Console]:
    node = shutil.which("node")
    assert node, "Node is required for browser acceptance"
    assert (WEB / "node_modules/next/dist/bin/next").is_file(), "run npm ci in the web root first"
    tmp_path = tmp_path_factory.mktemp("overview")
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
                for _ in range(240):
                    assert process.poll() is None, log_path.read_text()
                    try:
                        if (
                            http.get(f"http://127.0.0.1:{port}/login", timeout=30).status_code
                            == 200
                        ):
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.5)
                else:
                    pytest.fail(f"Synthetic overview frontend did not start; log: {log_path}")
            yield Console(f"http://127.0.0.1:{port}", upstream)
            assert not upstream.unexpected, upstream.unexpected
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.close()


@asynccontextmanager
async def open_overview(
    console: Console,
    api: Api,
    path: str,
    *,
    me: dict[str, Any] | None = None,
    width: int = 1440,
    height: int = 900,
    theme: str = "cobalt",
    rail: str = "collapsed",
) -> AsyncIterator[Page]:
    """A fresh signed-in browser on `path`; fails on unexpected calls, page or console errors."""
    console.upstream.me = me or make_me("tenant_staff")
    console.upstream.packages = api.packages
    errors: list[str] = []
    # The shared test host is slow under load: assertions here wait longer (restored after).
    expect.set_options(timeout=20_000)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context(viewport={"width": width, "height": height})
            context.set_default_navigation_timeout(WAIT)
            context.set_default_timeout(WAIT)
            jar = {"csa_session": "synthetic-session", "fs-theme": theme, "fs-rail": rail}
            await context.add_cookies(
                [{"name": name, "value": value, "url": console.base} for name, value in jar.items()]
            )
            page = await context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: errors.append(message.text) if message.type == "error" else None,
            )
            await page.route("**/console/v1/**", api.route)
            await page.goto(f"{console.base}{path}")
            yield page
        finally:
            await browser.close()
            expect.set_options(timeout=5_000)
    assert not api.unexpected, api.unexpected
    if api.deliberate_failures:
        errors = [e for e in errors if "status of 500" not in e]
    assert not errors, errors


def composer(page: Page) -> Any:
    return page.get_by_label("Your question", exact=False)


async def requested(page: Page, api: Api, path: str) -> None:
    """Wait until the browser has asked for `path` at least once."""
    for _ in range(200):
        if api.paths(path):
            return
        await page.wait_for_timeout(100)
    pytest.fail(f"{path} was never requested: {api.requests}")


async def strip_text(page: Page) -> str:
    return " ".join((await page.get_by_test_id("overview-strip").inner_text()).split())


async def test_overview_is_the_assistant_home_for_a_tenant_member(console: Console) -> None:
    api = Api(review=3)
    async with open_overview(console, api, "/portal") as page:
        await expect(page.get_by_role("heading", name="How can I help today?")).to_be_visible()
        await expect(composer(page)).to_be_enabled()
        await expect(page.get_by_text(REMOVED_LABEL, exact=True)).to_have_count(0)
        await expect(page.get_by_test_id("chat-honesty-line")).to_have_count(1)
        await expect(page.get_by_test_id("overview-strip-review")).to_have_text(
            re.compile(r"^\s*3\s*Replies to check\s*$")
        )
        await expect(page.get_by_test_id("overview-strip-services")).to_have_text(
            re.compile(r"^\s*2\s*Services on\s*$")
        )
        # One h1 for the page (visually hidden); the chat's own heading is an h2.
        await expect(page.locator("h1")).to_have_count(1)
        await expect(page.locator("h1")).to_have_text("Overview")
        await expect(page.get_by_test_id("shell-title")).to_have_text("Overview")
        # The strip reads its two endpoints without a tenant parameter for a member.
        assert api.paths("services/active") and all(
            "client_id" not in p for p in api.paths("services/active")
        )
        assert api.paths("inbox/conversations")[0] == "inbox/conversations?limit=1"
        # The chat works from the empty state.
        await composer(page).fill("When are you open?")
        await page.get_by_role("button", name="Send message", exact=True).click()
        await expect(page.get_by_text(REPLY)).to_be_visible()
        await expect(page.get_by_test_id("overview-strip")).to_be_visible()


async def test_overview_platform_admin_without_tenant_asks_to_choose(console: Console) -> None:
    api = Api()
    async with open_overview(console, api, "/", me=make_me("platform_admin")) as page:
        select = page.get_by_test_id("overview-tenant")
        await expect(select).to_be_visible()
        await expect(page.get_by_label("Tenant", exact=True)).to_have_value("")
        await expect(select.locator("option", has_text="Tenant B")).to_have_count(1)
        await expect(page.get_by_text("Choose a tenant to start chatting")).to_be_visible()
        await expect(composer(page)).to_have_count(0)
        await expect(page.get_by_test_id("overview-strip")).to_have_count(0)
        await expect(page.locator("h1")).to_have_count(1)
        await page.wait_for_timeout(500)
        assert not [p for _, p in api.requests if p.startswith(("chat/", "inbox/", "services"))]


async def test_tenant_selector_writes_client_id_to_the_url(console: Console) -> None:
    api = Api(review=4)
    me = make_me("platform_admin")
    async with open_overview(console, api, "/?client_id=tenant-a&keep=1", me=me) as page:
        select = page.get_by_label("Tenant", exact=True)
        await expect(select).to_have_value("tenant-a")
        await expect(composer(page)).to_be_visible()
        await select.select_option("tenant-b")
        await page.wait_for_url(re.compile(r"client_id=tenant-b"))
        assert "keep=1" in page.url
        await expect(page.get_by_test_id("overview-strip-review")).to_contain_text("4")
        await expect(page.get_by_role("heading", name="How can I help today?")).to_be_visible()
        assert "services/active?client_id=tenant-b" in api.paths("services/active")
        assert "inbox/conversations?limit=1&client_id=tenant-b" in api.paths("inbox/conversations")
        assert any("client_id=tenant-b" in p for p in api.paths("chat/conversations"))
        # Back to no tenant: the parameter leaves the URL and the composer goes.
        await select.select_option("")
        await page.wait_for_url(lambda url: "client_id" not in url)
        await expect(composer(page)).to_have_count(0)


async def test_member_without_active_tenant_sees_the_membership_message(console: Console) -> None:
    api = Api()
    async with open_overview(
        console, api, "/portal", me=make_me("tenant_staff", tenant=False)
    ) as page:
        await expect(page.get_by_text(MEMBERSHIP, exact=True)).to_be_visible()
        await expect(page.get_by_text(REMOVED_LABEL, exact=True)).to_have_count(0)
        await expect(composer(page)).to_have_count(0)
        await expect(page.get_by_test_id("overview-strip")).to_have_count(0)
        await page.wait_for_timeout(500)
        assert not [p for _, p in api.requests if p.startswith(("chat/", "inbox/"))]


@pytest.mark.parametrize("support", ["active", "suspended"])
async def test_strip_shows_both_numbers_for_staff(console: Console, support: str) -> None:
    api = Api(packages=[package("customer_support", support), package("hr")], review=0)
    async with open_overview(console, api, "/portal") as page:
        # Zero is a real answer here and shows; review sits first.
        await expect(page.get_by_test_id("overview-strip-review")).to_contain_text("0")
        services = 1 if support == "suspended" else 2
        await expect(page.get_by_test_id("overview-strip-services")).to_contain_text(str(services))
        assert await strip_text(page) == (f"0 Replies to check {services} Services on")
        # Suspended or active, the company keeps its chat, and the banner has nothing to say.
        await expect(composer(page)).to_be_enabled()
        await expect(page.get_by_test_id("customer-replies-banner")).to_have_count(0)
        # Plain text: nothing in the strip is a link or a live region.
        strip = page.get_by_test_id("overview-strip")
        await expect(strip.locator("a")).to_have_count(0)
        await expect(page.locator('[data-testid="overview-strip"][role]')).to_have_count(0)


async def test_strip_hides_review_for_read_only_and_when_inbox_read_fails(
    console: Console,
) -> None:
    # Read-only: no Inbox read at all, services stay, the composer is disabled.
    api = Api(review=5)
    async with open_overview(console, api, "/portal", me=make_me("read_only")) as page:
        await expect(page.get_by_test_id("overview-strip-services")).to_contain_text("2")
        await expect(page.get_by_test_id("overview-strip-review")).to_have_count(0)
        await expect(composer(page)).to_be_disabled()
        await page.wait_for_timeout(500)
        assert api.paths("inbox/conversations") == []
    # A failed Inbox read hides only that number; it never shows as zero.
    api = Api(review=None)
    async with open_overview(console, api, "/portal") as page:
        await expect(page.get_by_test_id("overview-strip-services")).to_contain_text("2")
        await requested(page, api, "inbox/conversations")
        await expect(page.get_by_test_id("overview-strip-review")).to_have_count(0)
        assert await strip_text(page) == "2 Services on"
    # Without Customer Support the Inbox is not asked, and the page is the small home.
    api = Api(packages=[package("hr")], review=7)
    async with open_overview(console, api, "/portal") as page:
        await expect(page.get_by_test_id("overview-strip-services")).to_contain_text("1")
        await expect(page.get_by_test_id("overview-strip-review")).to_have_count(0)
        await expect(page.get_by_test_id("workspace-home")).to_be_visible()
        assert api.paths("inbox/conversations") == []


async def test_strip_is_absent_when_both_reads_fail(console: Console) -> None:
    api = Api(review=None)
    api.packages_fail = True
    async with open_overview(console, api, "/portal") as page:
        await expect(composer(page)).to_be_enabled()
        await requested(page, api, "services/active")
        await page.wait_for_timeout(500)
        await expect(page.get_by_test_id("overview-strip")).to_have_count(0)
        # Unknown packages: the Inbox is not asked either.
        assert api.paths("inbox/conversations") == []


async def test_chat_routes_redirect_to_overview_with_client_id(console: Console) -> None:
    api = Api()
    async with open_overview(console, api, "/chat", me=make_me("platform_admin")) as page:
        await page.wait_for_url(f"{console.base}/")
        await page.goto(f"{console.base}/chat?client_id=tenant-b")
        await page.wait_for_url(f"{console.base}/?client_id=tenant-b")
        await expect(page.get_by_label("Tenant", exact=True)).to_have_value("tenant-b")
    api = Api()
    async with open_overview(console, api, "/portal/chat") as page:
        await page.wait_for_url(f"{console.base}/portal")
        await page.goto(f"{console.base}/portal/chat?client_id={TENANT}")
        await page.wait_for_url(f"{console.base}/portal?client_id={TENANT}")
        await expect(composer(page)).to_be_visible()


@pytest.mark.parametrize(
    ("query", "target"),
    [
        # Encoded by the redirect, never concatenated: the value stays one parameter.
        ("client_id=a%26b%3Dc", "?client_id=a%26b%3Dc"),
        # A repeated parameter is ambiguous, so it is dropped rather than guessed.
        ("client_id=one&client_id=two", ""),
        # A protocol-relative look-alike stays on this origin as an encoded value.
        ("client_id=//evil.example", "?client_id=%2F%2Fevil.example"),
        ("client_id=%20%20", ""),
    ],
)
async def test_chat_redirect_keeps_awkward_client_ids_inert(
    console: Console, query: str, target: str
) -> None:
    api = Api()
    async with open_overview(console, api, "/chat", me=make_me("platform_admin")) as page:
        await page.wait_for_url(f"{console.base}/")
        await page.goto(f"{console.base}/chat?{query}")
        await page.wait_for_url(f"{console.base}/{target}")
        assert page.url.startswith(console.base + "/")
        assert "evil" not in page.url.split("?")[0]


@pytest.mark.parametrize("role", ["tenant_admin", "platform_admin"])
async def test_sandbox_assistant_is_gone_from_the_navigation(console: Console, role: str) -> None:
    api = Api(packages=[package(k) for k in ("customer_support", "hr", "staff_email")])
    home = "/" if role == "platform_admin" else "/portal"
    async with open_overview(console, api, home, me=make_me(role), rail="expanded") as page:
        nav = page.get_by_role("navigation", name="Main navigation")
        await expect(nav.get_by_role("link", name="Overview", exact=True)).to_have_attribute(
            "aria-current", "page"
        )
        await expect(nav.get_by_role("link", name="Sandbox assistant")).to_have_count(0)
        await expect(nav.locator('a[href$="/chat"]')).to_have_count(0)
        await expect(nav.get_by_role("link", name="Services", exact=True)).to_be_visible()


async def page_metrics(page: Page) -> dict[str, Any]:
    return await page.evaluate(
        """() => {
            const doc = document.documentElement;
            const scroll = document.querySelector('[data-testid="sandbox-scroll"]');
            return {
                scrollHeight: doc.scrollHeight, innerHeight: window.innerHeight,
                scrollWidth: doc.scrollWidth, clientWidth: doc.clientWidth,
                inner: scroll ? [scroll.scrollHeight, scroll.clientHeight] : null,
            };
        }"""
    )


async def test_overview_does_not_scroll_the_page(console: Console) -> None:
    for width, height in ((1440, 900), (390, 800)):
        api = Api(turns=30)
        async with open_overview(console, api, "/portal", width=width, height=height) as page:
            history = page.get_by_role("button", name="Conversation 1", exact=True)
            if width < 768:
                await page.get_by_role("button", name="History", exact=True).click()
            await history.click()
            await expect(page.get_by_text("Question 30")).to_be_visible()
            measured = await page_metrics(page)
            where = f"{width}x{height}: {measured}"
            assert measured["scrollHeight"] <= measured["innerHeight"], where
            # The long conversation scrolls inside the message list instead.
            assert measured["inner"][0] > measured["inner"][1], where
            await expect(page.get_by_role("button", name="Send message")).to_be_in_viewport()


def intersects(a: Any, b: Any) -> bool:
    return not (
        a["x"] + a["width"] <= b["x"]
        or b["x"] + b["width"] <= a["x"]
        or a["y"] + a["height"] <= b["y"]
        or b["y"] + b["height"] <= a["y"]
    )


async def test_composer_clear_of_the_dock_button(console: Console) -> None:
    me = make_me("tenant_staff", assistant=True)
    for rail in ("expanded", "collapsed"):
        api = Api()
        async with open_overview(console, api, "/portal", me=me, rail=rail) as page:
            dock = page.get_by_test_id("assistant-open")
            await expect(dock).to_be_visible()
            for width in (390, 1024, 1280, 1440):
                await page.set_viewport_size({"width": width, "height": 900})
                await page.wait_for_timeout(150)
                dock_box = await dock.bounding_box()
                send_box = await page.get_by_role("button", name="Send message").bounding_box()
                box = await composer(page).locator("..").bounding_box()
                where = f"rail {rail} {width}px: dock {dock_box} send {send_box} composer {box}"
                assert dock_box and send_box and box, where
                assert not intersects(send_box, dock_box), where
                assert not intersects(box, dock_box), where
                measured = await page_metrics(page)
                assert measured["scrollHeight"] <= measured["innerHeight"], where


async def test_overview_fits_five_widths_in_three_themes(console: Console) -> None:
    for theme in THEMES:
        api = Api()
        async with open_overview(console, api, "/portal", theme=theme) as page:
            await expect(page.locator("html")).to_have_attribute("data-theme", theme)
            await expect(page.get_by_test_id("overview-strip")).to_be_visible()
            for width in WIDTHS:
                await page.set_viewport_size({"width": width, "height": 800})
                await page.wait_for_timeout(150)
                measured = await page_metrics(page)
                where = f"{theme} {width}px: {measured}"
                assert measured["scrollWidth"] <= measured["clientWidth"], where
                assert measured["scrollHeight"] <= measured["innerHeight"], where
                await expect(page.get_by_test_id("overview-strip")).to_be_in_viewport()
                await expect(page.get_by_role("button", name="Send message")).to_be_in_viewport()
                send = await page.get_by_role("button", name="Send message").bounding_box()
                assert send and send["x"] + send["width"] <= width, where


HOME_SENTENCE = "Ask the assistant at the side of the screen."


async def test_a_company_without_customer_support_gets_the_small_home(console: Console) -> None:
    api = Api(packages=[package("hr"), package("staff_email", "suspended")], review=7)
    me = make_me("tenant_admin", assistant=True)
    async with open_overview(console, api, "/portal", me=me, rail="expanded") as page:
        home = page.get_by_test_id("workspace-home")
        await expect(home).to_be_visible()
        await expect(home.get_by_role("link", name="People & HR", exact=True)).to_have_attribute(
            "href", "/portal/hr"
        )
        await expect(home.get_by_role("link", name="Services", exact=True)).to_have_attribute(
            "href", "/portal/services"
        )
        # Suspended Email assistant is not something the company has.
        await expect(home.get_by_role("link", name="Email assistant")).to_have_count(0)
        # The sentence points at the dock, and with the assistant on the dock is there.
        await expect(home.get_by_text(HOME_SENTENCE, exact=True)).to_be_visible()
        await expect(page.get_by_test_id("assistant-open")).to_be_visible()
        await expect(composer(page)).to_have_count(0)
        await expect(page.get_by_role("heading", name="How can I help today?")).to_have_count(0)
        await expect(page.locator("h1")).to_have_count(1)
        await expect(page.get_by_test_id("overview-strip-services")).to_contain_text("1")
        # The rail loses Assistant activity with the chat it belongs to.
        nav = page.get_by_role("navigation", name="Main navigation")
        await expect(nav.get_by_role("link", name="People & HR")).to_be_visible()
        await expect(nav.get_by_role("link", name="Assistant activity")).to_have_count(0)
        # One packages read serves the page and the strip; nothing customer-facing is asked for.
        # `next dev` mounts effects twice, so the rail's single HR read is the yardstick. The two
        # reads arrive independently, so wait until their counts meet (a fixed wait failed under
        # load with one read still in flight), then check they stay equal.
        for _ in range(100):
            if len(api.paths("services/active")) == len(api.paths("hr/context")):
                break
            await page.wait_for_timeout(100)
        await page.wait_for_timeout(300)
        assert len(api.paths("services/active")) == len(api.paths("hr/context")), api.requests
        assert not [p for _, p in api.requests if p.startswith(("chat/", "inbox/"))]
        assert not api.paths("services/customer-replies"), api.requests


async def test_the_small_home_has_no_assistant_sentence_for_a_read_only_member(
    console: Console,
) -> None:
    # No dock for a read-only member, even with the assistant on: the links stay, the sentence goes.
    api = Api(packages=[package("hr")], review=None)
    me = make_me("read_only", assistant=True)
    async with open_overview(console, api, "/portal", me=me) as page:
        home = page.get_by_test_id("workspace-home")
        await expect(home).to_be_visible()
        await expect(home.get_by_role("link", name="Services", exact=True)).to_have_attribute(
            "href", "/portal/services"
        )
        await expect(page.get_by_text(HOME_SENTENCE, exact=True)).to_have_count(0)
        await expect(page.get_by_test_id("assistant-open")).to_have_count(0)


async def test_the_small_home_has_no_assistant_sentence_when_the_assistant_is_off(
    console: Console,
) -> None:
    api = Api(packages=[package("hr")])
    me = make_me("tenant_admin", assistant=False)
    async with open_overview(console, api, "/portal", me=me) as page:
        home = page.get_by_test_id("workspace-home")
        await expect(home).to_be_visible()
        await expect(home.get_by_role("link", name="Services", exact=True)).to_be_visible()
        await expect(page.get_by_text(HOME_SENTENCE, exact=True)).to_have_count(0)
        await expect(page.get_by_test_id("assistant-open")).to_have_count(0)


async def test_a_platform_admin_sees_the_assistant_sentence_where_the_dock_is(
    console: Console,
) -> None:
    # The chosen company has no Customer support; the dock is there because ?client_id= names it.
    api = Api(packages=[package("hr")])
    me = make_me("platform_admin", assistant=True)
    async with open_overview(console, api, "/?client_id=tenant-b", me=me) as page:
        home = page.get_by_test_id("workspace-home")
        await expect(home).to_be_visible()
        await expect(home.get_by_text(HOME_SENTENCE, exact=True)).to_be_visible()
        await expect(page.get_by_test_id("assistant-open")).to_be_visible()


async def test_a_company_with_customer_support_gets_the_chat_and_the_banner(
    console: Console,
) -> None:
    api = Api(warnings=["replies_off"])
    async with open_overview(console, api, "/portal", me=make_me("tenant_admin")) as page:
        await expect(page.get_by_role("heading", name="How can I help today?")).to_be_visible()
        await expect(composer(page)).to_be_enabled()
        await expect(page.get_by_test_id("customer-replies-banner")).to_contain_text(
            "Your customers aren't getting replies yet."
        )
        await expect(page.get_by_test_id("workspace-home")).to_have_count(0)
        await expect(page.get_by_text(HOME_SENTENCE, exact=True)).to_have_count(0)
        # The chat still works with replies off: it answers as a preview.
        await composer(page).fill("When are you open?")
        await page.get_by_role("button", name="Send message", exact=True).click()
        await expect(page.get_by_text(REPLY)).to_be_visible()
        nav = page.get_by_role("navigation", name="Main navigation")
        await expect(nav.get_by_role("link", name="Assistant activity")).to_have_count(1)


async def test_a_failed_packages_read_keeps_the_chat_without_an_error(console: Console) -> None:
    api = Api(review=None)
    api.packages_fail = True
    async with open_overview(console, api, "/portal") as page:
        await requested(page, api, "services/active")
        await expect(composer(page)).to_be_enabled()
        await expect(page.get_by_test_id("workspace-home")).to_have_count(0)
        await expect(page.locator('p[role="alert"]')).to_have_count(0)


async def test_a_platform_admin_switching_companies_follows_the_choice(console: Console) -> None:
    api = Api(by_tenant={"tenant-a": SUPPORT_AND_HR, "tenant-b": [package("hr")]})
    async with open_overview(
        console, api, "/?client_id=tenant-a", me=make_me("platform_admin")
    ) as page:
        select = page.get_by_label("Tenant", exact=True)
        await expect(composer(page)).to_be_enabled()
        await select.select_option("tenant-b")
        home = page.get_by_test_id("workspace-home")
        await expect(home).to_be_visible()
        await expect(composer(page)).to_have_count(0)
        # The admin's links open the chosen company's pages.
        await expect(home.get_by_role("link", name="People & HR")).to_have_attribute(
            "href", "/hr?client_id=tenant-b"
        )
        await select.select_option("tenant-a")
        await expect(composer(page)).to_be_enabled()
        await expect(home).to_have_count(0)


async def test_a_packages_read_that_never_answers_does_not_leave_the_page_waiting(
    console: Console,
) -> None:
    api = Api()
    api.packages_delay = 11.0  # the page gives up after 8 seconds
    async with open_overview(console, api, "/portal") as page:
        await expect(page.locator('p[role="status"]', has_text="Loading")).to_have_count(1)
        await expect(composer(page)).to_be_enabled()
        await expect(page.get_by_test_id("workspace-home")).to_have_count(0)
        # The read carried on: its late answer still reaches the status strip.
        await expect(page.get_by_test_id("overview-strip-services")).to_contain_text("2")
