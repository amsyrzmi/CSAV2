"""Real browser acceptance for Overview as the assistant page.

The real Next.js shell and Overview pages run unmodified from a disposable staged copy. The
layouts fetch `/auth/me`, `/services/active` and `/profile` server-side, so a loopback stub origin
answers those; every browser-side `/console/v1/*` call (the status strip's two reads, the tenant
list, the workspace assistant's thread and turns, and the customer preview the old chat links
redirect to) is answered by Playwright, recorded, and an unexpected path fails the test, as do
page errors and console errors. Workspace bodies are built from the backend contracts so they
cannot drift. One dev server serves every test in this file.
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
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import EllipsisType
from typing import Any
from uuid import UUID

import httpx
import pytest
from csa.workspace.contracts import (
    AnswerResult,
    NavigateResult,
    WorkspaceActionResult,
    WorkspaceThreadOut,
    WorkspaceTurnOut,
)
from playwright.async_api import Page, Route, async_playwright, expect

from tests.shell_counts_stub import fulfill_shell_counts

pytestmark = pytest.mark.asyncio

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "CSA_WebConsole/csa-console/web"
TENANT = "11111111-1111-4111-8111-111111111111"
WAIT = 60_000  # First visit to each route compiles it in `next dev`.
REPLY = "Our opening hours are nine to six on weekdays."
MEMBERSHIP = "Your account has no active company. Ask your administrator to check your access."
WIDTHS = (320, 390, 768, 1024, 1440)
THEMES = ("cobalt", "night", "bureau")
CLIENTS = [{"id": "tenant-a", "name": "Tenant A"}, {"id": "tenant-b", "name": "Tenant B"}]
# The line the small home used to point at the dock with; nothing shows it any more.
HOME_SENTENCE = "Ask the assistant at the side of the screen."
HEADLINE = "How can I help today?"
EMPTY_HINT = (
    "Ask about this tab, find a colleague, or prepare an action. Nothing changes until you "
    "confirm it."
)
# Idea texts from W/lib/assistant-ideas.json (Overview shows only the "any" ideas).
IDEA_WHAT = "What can you help me with in this console?"
IDEA_CONNECTIONS = "Open Connections"
IDEA_PAYMENTS = "Open Payments"
IDEA_HR = "Open the HR tab"
IDEA_FINANCE = "Email the finance manager about the budget"
# Scripted planner: one plain answer, and one request that has to finish on Services.
ANSWER_ASK = "When does payroll close?"
ANSWER = "Payroll closes on the 25th of each month."
NAV_ASK = "Which services do we run?"
RESUMED = "You run Customer support and People & HR."
THREAD_A = UUID("33333333-3333-4333-8333-333333333331")
THREAD_MEMBER = UUID("33333333-3333-4333-8333-333333333332")


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


def planner(text: str, context: dict[str, Any] | None) -> WorkspaceActionResult | None:
    """The workspace planner's scripted results; anything else is an unexpected message."""
    tab = (context or {}).get("tab")
    if text == ANSWER_ASK:
        return AnswerResult(action="answer", markdown=ANSWER)
    if text == NAV_ASK and tab != "services":
        return NavigateResult(action="navigate", tab="services", resume=True)
    if text == NAV_ASK:
        return AnswerResult(action="answer", markdown=RESUMED)
    return None


def workspace_turn(number: int, body: str, result: WorkspaceActionResult) -> dict[str, Any]:
    return WorkspaceTurnOut(
        id=number,
        request_key=UUID(int=number),
        body=body,
        result=result,
        created_at=datetime(2026, 11, 16, tzinfo=UTC),
    ).model_dump(mode="json")


def workspace_thread(thread_id: UUID) -> dict[str, Any]:
    return WorkspaceThreadOut(
        id=thread_id, sequence=1, created_at=datetime(2026, 11, 16, tzinfo=UTC)
    ).model_dump(mode="json")


class Api:
    """The browser-side console API as Playwright answers it, with every request recorded.

    `packages` and `review` are what the strip's reads answer; None answers 500. `by_tenant`
    answers the packages of a named company instead, as a platform admin's switch needs.
    `warnings` is what the Go live banner's read reports. Workspace threads are kept per company
    (every workspace call names it with `client_id`), turns per thread.
    """

    def __init__(
        self,
        *,
        packages: list[dict[str, str]] | None = None,
        review: int | None = 3,
        turns: int = 0,
        by_tenant: dict[str, list[dict[str, str]]] | None = None,
        warnings: list[str] | None = None,
        counts: dict[str, Any] | None | EllipsisType = ...,
    ) -> None:
        # `...` leaves `counts` out of the answer (an older server); None is the server's "could
        # not read them".
        self.counts = counts
        self.packages = SUPPORT_AND_HR if packages is None else packages
        self.by_tenant = by_tenant or {}
        self.warnings = warnings or []
        self.packages_fail = False
        self.packages_delay = 0.0
        # When set, workspace turn reads wait for it: the thread list answers, the turns later.
        self.turns_gate: asyncio.Event | None = None
        self.review = review
        self.threads: list[dict[str, Any]] = []
        self.turns: dict[str, list[dict[str, Any]]] = {}
        self.workspace_threads: dict[str, list[dict[str, Any]]] = {}
        self.workspace_turns: dict[str, list[dict[str, Any]]] = {}
        self.requests: list[tuple[str, str]] = []
        self.bodies: list[tuple[str, dict[str, Any]]] = []
        self.unexpected: list[str] = []
        self.deliberate_failures = 0
        if turns:
            self.seed(TENANT, THREAD_MEMBER, turns)

    def seed(self, scope: str, thread_id: UUID, count: int, prefix: str = "") -> None:
        """A stored workspace thread for `scope` with `count` answered questions."""
        self.workspace_threads.setdefault(scope, []).append(workspace_thread(thread_id))
        self.workspace_turns[str(thread_id)] = [
            workspace_turn(
                n,
                f"{prefix}Question {n}",
                AnswerResult(action="answer", markdown=f"{prefix}Answer {n}"),
            )
            for n in range(1, count + 1)
        ]

    def packages_for(self, tail: str) -> list[dict[str, str]]:
        tenant = re.search(r"client_id=([^&]+)", tail)
        return self.by_tenant.get(tenant.group(1), self.packages) if tenant else self.packages

    def paths(self, prefix: str) -> list[str]:
        return [tail for method, tail in self.requests if tail.split("?")[0] == prefix]

    def workspace(self, method: str | None = None) -> list[tuple[str, str]]:
        return [
            (m, tail)
            for m, tail in self.requests
            if tail.startswith("workspace/") and (method is None or m == method)
        ]

    def posted_turns(self) -> list[dict[str, Any]]:
        return [body for path, body in self.bodies if path.endswith("/turns")]

    async def fail(self, route: Route) -> None:
        self.deliberate_failures += 1
        await route.fulfill(status=500, json={"detail": "synthetic outage"})

    async def route(self, route: Route) -> None:
        if await fulfill_shell_counts(route):
            return
        request = route.request
        tail = request.url.split("/console/v1/")[1]
        path = tail.split("?")[0]
        self.requests.append((request.method, tail))
        scope_match = re.search(r"client_id=([^&]+)", tail)
        scope = scope_match.group(1) if scope_match else ""
        workspace_turns = re.fullmatch(r"workspace/conversations/([0-9a-f-]+)/turns", path)
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
        elif path == "services" and request.method == "GET":
            # The chat links and a navigate result open Services; an empty list is enough there.
            data = []
        elif path == "inbox/conversations" and request.method == "GET":
            if self.review is None:
                await self.fail(route)
                return
            data = {"items": [], "next_cursor": None, "needs_review_count": self.review}
            if self.counts is not ...:
                data["counts"] = self.counts
        elif path == "services/customer-replies" and request.method == "GET":
            # The gateway answers Customer support active or suspended and refuses everyone else.
            # The page only asks where the banner shows, so a refusal here would be a console error.
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
        elif path == "workspace/conversations" and request.method == "GET":
            data = {"items": self.workspace_threads.get(scope, []), "next_cursor": None}
        elif path == "workspace/conversations" and request.method == "POST":
            known = sum(len(items) for items in self.workspace_threads.values())
            created = workspace_thread(UUID(int=0xC0FFEE + known))
            self.workspace_threads.setdefault(scope, []).insert(0, created)
            self.bodies.append((path, request.post_data_json or {}))
            data = created
        elif workspace_turns and request.method == "GET":
            if self.turns_gate is not None:
                await self.turns_gate.wait()
            data = {"items": self.workspace_turns.get(workspace_turns[1], []), "next_cursor": None}
        elif workspace_turns and request.method == "POST":
            body = request.post_data_json
            assert body is not None
            self.bodies.append((path, body))
            result = planner(body["body"], body.get("page_context"))
            if result is None:
                self.unexpected.append(f"turn {body['body']!r}")
                await route.fulfill(status=500, json={"detail": "unexpected synthetic message"})
                return
            items = self.workspace_turns.setdefault(workspace_turns[1], [])
            created_turn = workspace_turn(len(items) + 1, body["body"], result)
            created_turn["request_key"] = body["request_key"]
            items.append(created_turn)
            data = created_turn
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


# Counts every element with these test ids the page ever inserts, including one that is added and
# removed again before the next paint, so a body that flashes for one render is still seen.
WATCH_BODIES = """() => {
    const pairs = [["home", "workspace-home"], ["assistant", "overview-assistant"]];
    const seen = { home: 0, assistant: 0 };
    window.__overviewBodies = seen;
    const count = (node) => {
        if (!(node instanceof Element)) return;
        for (const [key, id] of pairs) {
            const selector = `[data-testid="${id}"]`;
            if (node.matches(selector)) seen[key] += 1;
            seen[key] += node.querySelectorAll(selector).length;
        }
    };
    new MutationObserver((records) => {
        for (const record of records) record.addedNodes.forEach(count);
    }).observe(document, { childList: true, subtree: true });
}"""


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
    watch: bool = False,
    init_script: str | None = None,
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
            if watch:
                await context.add_init_script(f"({WATCH_BODIES})()")
            if init_script:
                await context.add_init_script(init_script)
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
    return page.get_by_test_id("assistant-input")


def send_button(page: Page) -> Any:
    return page.get_by_test_id("assistant-send")


def assistant_page(page: Page) -> Any:
    return page.get_by_test_id("overview-assistant")


async def say(page: Page, text: str) -> None:
    await composer(page).fill(text)
    await expect(send_button(page)).to_be_enabled(timeout=WAIT)
    await send_button(page).click()


async def requested(page: Page, api: Api, path: str) -> None:
    """Wait until the browser has asked for `path` at least once."""
    for _ in range(200):
        if api.paths(path):
            return
        await page.wait_for_timeout(100)
    pytest.fail(f"{path} was never requested: {api.requests}")


async def strip_text(page: Page) -> str:
    return " ".join((await page.get_by_test_id("overview-strip").inner_text()).split())


async def idea_texts(page: Page) -> list[str]:
    return [
        " ".join(text.split())
        for text in await page.get_by_test_id("overview-ideas")
        .get_by_role("button")
        .all_inner_texts()
    ]


async def test_overview_is_the_assistant_page_for_a_tenant_member(console: Console) -> None:
    api = Api(review=3)
    me = make_me("tenant_staff", assistant=True)
    async with open_overview(console, api, "/portal", me=me) as page:
        await expect(assistant_page(page)).to_be_visible()
        await expect(page.get_by_role("heading", name=HEADLINE)).to_be_visible()
        await expect(page.get_by_text(EMPTY_HINT)).to_be_visible()
        await expect(composer(page)).to_be_enabled()
        await expect(page.get_by_test_id("overview-strip-review")).to_have_text(
            re.compile(r"^\s*3\s*Replies to check\s*$")
        )
        await expect(page.get_by_test_id("overview-strip-services")).to_have_text(
            re.compile(r"^\s*2\s*Services on\s*$")
        )
        # One h1 for the page (visually hidden); the assistant's own heading is an h2.
        await expect(page.locator("h1")).to_have_count(1)
        await expect(page.locator("h1")).to_have_text("Overview")
        await expect(page.get_by_test_id("shell-title")).to_have_text("Overview")
        # The strip reads its two endpoints without a tenant parameter for a member.
        assert api.paths("services/active") and all(
            "client_id" not in p for p in api.paths("services/active")
        )
        assert api.paths("inbox/conversations")[0] == "inbox/conversations?limit=1"
        # The page reads the thread at mount; the dock is not on Overview.
        await requested(page, api, "workspace/conversations")
        await expect(page.get_by_test_id("assistant-open")).to_have_count(0)
        # A question gets its answer on the page: one thread, one turn, from the Overview tab.
        await say(page, ANSWER_ASK)
        await expect(page.get_by_test_id("assistant-turn")).to_have_count(1)
        await expect(page.get_by_text(ANSWER)).to_be_visible()
        assert [b["page_context"]["tab"] for b in api.posted_turns()] == ["overview"]
        assert len(api.workspace("POST")) == 2  # the thread, then the turn
        # The ideas are for an empty thread only.
        await expect(page.get_by_test_id("overview-ideas")).to_have_count(0)
        await expect(page.get_by_test_id("overview-strip")).to_be_visible()
        # The customer chat is not on Overview any more.
        await expect(page.get_by_test_id("sandbox-scroll")).to_have_count(0)
        assert not [p for _, p in api.requests if p.startswith("chat/")]


async def test_a_customer_support_company_sees_strip_banner_assistant_and_ideas(
    console: Console,
) -> None:
    api = Api(warnings=["replies_off"])
    me = make_me("tenant_staff", assistant=True)
    async with open_overview(console, api, "/portal", me=me) as page:
        await expect(page.get_by_test_id("overview-strip")).to_be_visible()
        await expect(page.get_by_test_id("customer-replies-banner")).to_contain_text(
            "Your customers aren't getting replies yet."
        )
        await expect(assistant_page(page)).to_be_visible()
        await expect(page.get_by_test_id("workspace-home")).to_have_count(0)
        await expect(page.get_by_text(HOME_SENTENCE, exact=True)).to_have_count(0)
        # The banner sits above the assistant.
        banner_box = await page.get_by_test_id("customer-replies-banner").bounding_box()
        page_box = await assistant_page(page).bounding_box()
        assert banner_box and page_box and banner_box["y"] < page_box["y"], (banner_box, page_box)
        # The ideas this member may use: Customer support, HR active, Email assistant suspended.
        ideas = page.get_by_test_id("overview-ideas")
        await expect(ideas).to_be_visible()
        assert await idea_texts(page) == [IDEA_WHAT, IDEA_CONNECTIONS, IDEA_HR]
        # A chip fills the composer and sends nothing.
        await ideas.get_by_role("button", name=IDEA_HR).click()
        await expect(composer(page)).to_have_value(IDEA_HR)
        await expect(composer(page)).to_be_focused()
        await page.wait_for_timeout(800)
        assert api.workspace("POST") == [], api.requests
        await expect(page.get_by_test_id("assistant-turn")).to_have_count(0)
        nav = page.get_by_role("navigation", name="Main navigation")
        await expect(nav.get_by_role("link", name="Assistant activity")).to_have_count(1)


async def test_an_employee_only_company_sees_the_assistant_and_no_banner(
    console: Console,
) -> None:
    api = Api(packages=[package("hr"), package("subscriptions")], review=7)
    me = make_me("tenant_admin", assistant=True)
    async with open_overview(console, api, "/portal", me=me, rail="expanded") as page:
        await expect(assistant_page(page)).to_be_visible()
        await expect(composer(page)).to_be_enabled()
        await expect(page.get_by_test_id("workspace-home")).to_have_count(0)
        await expect(page.get_by_test_id("customer-replies-banner")).to_have_count(0)
        await expect(page.get_by_test_id("overview-strip-services")).to_contain_text("2")
        await expect(page.get_by_test_id("overview-strip-review")).to_have_count(0)
        # Subscriptions brings Payments; four ideas at most.
        await expect(page.get_by_test_id("overview-ideas")).to_be_visible()
        assert await idea_texts(page) == [IDEA_WHAT, IDEA_CONNECTIONS, IDEA_PAYMENTS, IDEA_HR]
        assert IDEA_FINANCE not in await idea_texts(page)
        # The rail loses Assistant activity with the customer conversations it belongs to.
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


@pytest.mark.parametrize("who", ["read_only", "assistant_off"])
async def test_without_the_assistant_overview_is_the_small_home(console: Console, who: str) -> None:
    # A read-only member never has the assistant; with the gateway flag off nobody has it.
    me = (
        make_me("read_only", assistant=True)
        if who == "read_only"
        else make_me("tenant_admin", assistant=False)
    )
    api = Api(packages=[package("hr"), package("staff_email", "suspended")], review=None)
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
        await expect(page.get_by_text(HOME_SENTENCE, exact=True)).to_have_count(0)
        await expect(composer(page)).to_have_count(0)
        await expect(assistant_page(page)).to_have_count(0)
        await expect(page.get_by_test_id("assistant-open")).to_have_count(0)
        await expect(page.locator("h1")).to_have_count(1)
        await page.wait_for_timeout(500)
        assert api.workspace() == [], api.requests


async def test_overview_platform_admin_without_tenant_asks_to_choose(console: Console) -> None:
    api = Api()
    me = make_me("platform_admin", assistant=True)
    async with open_overview(console, api, "/", me=me) as page:
        select = page.get_by_test_id("overview-tenant")
        await expect(select).to_be_visible()
        await expect(page.get_by_label("Tenant", exact=True)).to_have_value("")
        await expect(select.locator("option", has_text="Tenant B")).to_have_count(1)
        await expect(page.get_by_text("Choose a tenant to start chatting")).to_be_visible()
        await expect(composer(page)).to_have_count(0)
        await expect(page.get_by_test_id("assistant-open")).to_have_count(0)
        await expect(page.get_by_test_id("overview-strip")).to_have_count(0)
        await expect(page.locator("h1")).to_have_count(1)
        await page.wait_for_timeout(800)
        # No company, so no workspace route at all (Review Focus 2).
        assert api.workspace() == [], api.requests
        assert not [p for _, p in api.requests if p.startswith(("chat/", "inbox/", "services"))]
        # Nor on a page that has the dock for everyone else: still no company, so no dock.
        await page.goto(f"{console.base}/services")
        services_tenant = page.get_by_test_id("services-tenant")
        await expect(services_tenant).to_be_visible()
        # The tenant list has been read, so the page's effects have run before the checks below.
        await expect(services_tenant.locator("option").first).to_have_text("Select a tenant")
        await expect(page.get_by_test_id("assistant-open")).to_have_count(0)
        await expect(page.get_by_test_id("assistant-panel")).to_have_count(0)
        assert api.workspace() == [], api.requests


async def test_tenant_selector_writes_client_id_to_the_url(console: Console) -> None:
    api = Api(review=4)
    me = make_me("platform_admin", assistant=True)
    async with open_overview(console, api, "/?client_id=tenant-a&keep=1", me=me) as page:
        select = page.get_by_label("Tenant", exact=True)
        await expect(select).to_have_value("tenant-a")
        await expect(composer(page)).to_be_visible()
        await select.select_option("tenant-b")
        await page.wait_for_url(re.compile(r"client_id=tenant-b"))
        assert "keep=1" in page.url
        await expect(page.get_by_test_id("overview-strip-review")).to_contain_text("4")
        await expect(page.get_by_role("heading", name=HEADLINE)).to_be_visible()
        assert "services/active?client_id=tenant-b" in api.paths("services/active")
        assert "inbox/conversations?limit=1&client_id=tenant-b" in api.paths("inbox/conversations")
        await requested(page, api, "workspace/conversations")
        for _ in range(100):
            if "workspace/conversations?client_id=tenant-b" in api.paths("workspace/conversations"):
                break
            await page.wait_for_timeout(100)
        assert "workspace/conversations?client_id=tenant-b" in api.paths("workspace/conversations")
        # Back to no tenant: the parameter leaves the URL and the composer goes.
        await select.select_option("")
        await page.wait_for_url(lambda url: "client_id" not in url)
        await expect(composer(page)).to_have_count(0)
        await expect(page.get_by_text("Choose a tenant to start chatting")).to_be_visible()
        await expect(page.get_by_test_id("assistant-open")).to_have_count(0)


async def test_a_platform_admin_switching_companies_follows_the_choice(console: Console) -> None:
    # The real chooser on Overview: A has Customer support and a stored thread, B is employee-only
    # with none. The conversation resets, nothing from A stays, and the small home never shows.
    api = Api(
        by_tenant={"tenant-a": SUPPORT_AND_HR, "tenant-b": [package("hr")]},
        warnings=["replies_off"],
    )
    api.seed("tenant-a", THREAD_A, 2, prefix="Tenant A ")
    me = make_me("platform_admin", assistant=True)
    async with open_overview(console, api, "/?client_id=tenant-a", me=me, watch=True) as page:
        select = page.get_by_label("Tenant", exact=True)
        turns = page.get_by_test_id("assistant-turn")
        banner = page.get_by_test_id("customer-replies-banner")
        await expect(turns).to_have_count(2)
        await expect(page.get_by_text("Tenant A Answer 2")).to_be_visible()
        await expect(banner).to_be_visible()
        await expect(page.get_by_test_id("overview-ideas")).to_have_count(0)
        assert await page.evaluate("window.__overviewBodies") == {"home": 0, "assistant": 1}

        await select.select_option("tenant-b")
        await page.wait_for_url(re.compile(r"client_id=tenant-b"))
        await expect(page.get_by_text(EMPTY_HINT)).to_be_visible()
        await expect(turns).to_have_count(0)
        await expect(page.get_by_text("Tenant A Answer 2")).to_have_count(0)
        await expect(banner).to_have_count(0)
        await expect(page.get_by_test_id("overview-ideas")).to_be_visible()
        await expect(page.get_by_test_id("workspace-home")).to_have_count(0)
        await expect(page.get_by_test_id("assistant-open")).to_have_count(0)
        assert "workspace/conversations?client_id=tenant-b" in api.paths("workspace/conversations")
        # B has no thread, so no turns are read for it.
        assert not [t for _, t in api.workspace("GET") if "/turns" in t and "tenant-b" in t]
        # One new assistant page per switch, and never the small home, not even for one render.
        assert await page.evaluate("window.__overviewBodies") == {"home": 0, "assistant": 2}

        await select.select_option("tenant-a")
        await page.wait_for_url(re.compile(r"client_id=tenant-a"))
        await expect(turns).to_have_count(2)
        await expect(banner).to_be_visible()
        assert await page.evaluate("window.__overviewBodies") == {"home": 0, "assistant": 3}
        # Nothing was ever written: switching only reads.
        assert api.workspace("POST") == []


# Counts every insertion of the starting ideas and of the empty hint (argument), so a state
# that shows for one render and is replaced before the next check is still seen.
WATCH_EMPTY_STATE = """(hint) => {
    const seen = { ideas: 0, hint: 0 };
    window.__emptyState = seen;
    const ideas = '[data-testid="overview-ideas"]';
    const count = (node) => {
        if (!(node instanceof Element)) return;
        seen.ideas += (node.matches(ideas) ? 1 : 0) + node.querySelectorAll(ideas).length;
        if ((node.textContent || "").includes(hint)) seen.hint += 1;
    };
    new MutationObserver((records) => {
        for (const record of records) record.addedNodes.forEach(count);
    }).observe(document, { childList: true, subtree: true });
}"""


async def test_a_stored_thread_shows_no_empty_state_before_its_turns_arrive(
    console: Console,
) -> None:
    # The thread list answers first and the turns later. Until the turns are in, the page offers
    # neither the starting ideas nor the empty hint, and Enter does not start a second thread.
    api = Api(turns=2)
    api.turns_gate = asyncio.Event()
    me = make_me("tenant_staff", assistant=True)
    watch = f"({WATCH_EMPTY_STATE})({json.dumps(EMPTY_HINT)})"
    turns_path = f"workspace/conversations/{THREAD_MEMBER}/turns"
    async with open_overview(console, api, "/portal", me=me, init_script=watch) as page:
        try:
            await expect(assistant_page(page)).to_be_visible()
            await requested(page, api, turns_path)  # held at the gate
            await composer(page).fill(ANSWER_ASK)
            await composer(page).press("Enter")
            await expect(send_button(page)).to_be_disabled()
            await page.wait_for_timeout(500)
            assert api.workspace("POST") == [], api.requests
            await expect(page.get_by_test_id("overview-ideas")).to_have_count(0)
            await expect(page.get_by_text(EMPTY_HINT)).to_have_count(0)
        finally:
            api.turns_gate.set()
        await expect(page.get_by_test_id("assistant-turn")).to_have_count(2)
        await expect(page.get_by_text("Answer 2", exact=True)).to_be_visible()
        assert await page.evaluate("window.__emptyState") == {"ideas": 0, "hint": 0}
        # Now Enter sends, into the stored thread: one turn and no new thread.
        await expect(composer(page)).to_have_value(ANSWER_ASK)
        await composer(page).press("Enter")
        await expect(page.get_by_text(ANSWER)).to_be_visible()
        posted = [tail.split("?")[0] for _, tail in api.workspace("POST")]
        assert posted == [turns_path], api.requests
    # A company with no thread reads no turns, so its ideas and hint show as soon as it lists.
    api = Api()
    api.turns_gate = asyncio.Event()
    async with open_overview(console, api, "/portal", me=me, init_script=watch) as page:
        await expect(page.get_by_test_id("overview-ideas")).to_be_visible()
        await expect(page.get_by_text(EMPTY_HINT)).to_be_visible()
        assert not [t for _, t in api.workspace("GET") if "/turns" in t], api.requests


async def test_a_navigate_result_moves_to_the_tab_and_the_dock_there_continues_the_thread(
    console: Console,
) -> None:
    api = Api()
    me = make_me("tenant_staff", assistant=True)
    async with open_overview(console, api, "/portal", me=me) as page:
        # `next dev` compiles Services on first request; ask once so the navigate below only routes
        # (the resume gives up if the route takes longer than ten seconds).
        await page.request.get(f"{console.base}/portal/services", timeout=WAIT)
        await expect(assistant_page(page)).to_be_visible()
        await say(page, ANSWER_ASK)
        await expect(page.get_by_text(ANSWER)).to_be_visible()
        await say(page, NAV_ASK)
        await expect(page).to_have_url(f"{console.base}/portal/services", timeout=WAIT)
        await expect(page.get_by_role("heading", name="Services", level=1)).to_be_visible()
        # The dock is back on every page but Overview, over the same conversation, and it is
        # already open: the resumed answer arrives where the user can see it, with no click.
        panel = page.get_by_test_id("assistant-panel")
        await expect(panel).to_be_visible()
        await expect(page.get_by_test_id("assistant-open")).to_have_count(0)
        turns = panel.get_by_test_id("assistant-turn")
        await expect(turns).to_have_count(3, timeout=WAIT)
        await expect(turns.nth(0)).to_contain_text(ANSWER_ASK)
        await expect(turns.nth(0)).to_contain_text(ANSWER)
        await expect(turns.nth(1)).to_contain_text("Opened Services and continued there.")
        await expect(turns.nth(2)).to_contain_text(RESUMED)
        # Past the resume grace period: nothing else is sent.
        await page.wait_for_timeout(2_000)
        assert [(b["body"], b["page_context"]["tab"]) for b in api.posted_turns()] == [
            (ANSWER_ASK, "overview"),
            (NAV_ASK, "overview"),
            (NAV_ASK, "services"),
        ]
        # One thread for the whole exchange, on both pages (Review Focus 1).
        created = [
            t for _, t in api.workspace("POST") if t.split("?")[0] == "workspace/conversations"
        ]
        assert len(created) == 1, api.requests


async def test_the_dock_button_is_on_every_page_but_overview(console: Console) -> None:
    api = Api()
    me = make_me("tenant_admin", assistant=True)
    async with open_overview(console, api, "/portal", me=me, rail="expanded") as page:
        dock = page.get_by_test_id("assistant-open")
        await expect(assistant_page(page)).to_be_visible()
        await expect(dock).to_have_count(0)
        nav = page.get_by_role("navigation", name="Main navigation")
        await nav.get_by_role("link", name="Services", exact=True).click()
        await page.wait_for_url(f"{console.base}/portal/services")
        await expect(dock).to_be_visible()
        await nav.get_by_role("link", name="Overview", exact=True).click()
        await page.wait_for_url(f"{console.base}/portal")
        await expect(assistant_page(page)).to_be_visible()
        await expect(dock).to_have_count(0)
        # The thread was read once for the page view; coming back does not create one.
        assert api.workspace("POST") == []


async def test_member_without_active_tenant_sees_the_membership_message(console: Console) -> None:
    api = Api()
    me = make_me("tenant_staff", tenant=False, assistant=True)
    async with open_overview(console, api, "/portal", me=me) as page:
        await expect(page.get_by_text(MEMBERSHIP, exact=True)).to_be_visible()
        await expect(composer(page)).to_have_count(0)
        await expect(page.get_by_test_id("overview-strip")).to_have_count(0)
        await page.wait_for_timeout(500)
        assert not [p for _, p in api.requests if p.startswith(("chat/", "inbox/", "workspace/"))]


@pytest.mark.parametrize("support", ["active", "suspended"])
async def test_strip_shows_both_numbers_for_staff(console: Console, support: str) -> None:
    api = Api(packages=[package("customer_support", support), package("hr")], review=0)
    me = make_me("tenant_staff", assistant=True)
    async with open_overview(console, api, "/portal", me=me) as page:
        # Zero is a real answer here and shows; review sits first.
        await expect(page.get_by_test_id("overview-strip-review")).to_contain_text("0")
        services = 1 if support == "suspended" else 2
        await expect(page.get_by_test_id("overview-strip-services")).to_contain_text(str(services))
        assert await strip_text(page) == (f"0 Replies to check {services} Services on")
        # Suspended or active, the company has the assistant, and the banner has nothing to say.
        await expect(composer(page)).to_be_enabled()
        await expect(page.get_by_test_id("customer-replies-banner")).to_have_count(0)
        # Plain text: nothing in the strip is a link or a live region.
        strip = page.get_by_test_id("overview-strip")
        await expect(strip.locator("a")).to_have_count(0)
        await expect(page.locator('[data-testid="overview-strip"][role]')).to_have_count(0)


QUEUE_COUNTS = {
    "needs_us": 5,
    "mine": 3,
    "unassigned": 2,
    "overdue": 1,
    "waiting_customer": 4,
    "snoozed": 0,
    "oldest_waiting_at": None,
}
QUEUE_PILLS = [
    "overview-strip-" + name for name in ("review", "needs-us", "overdue", "mine", "services")
]


async def test_strip_shows_the_queue_counts_in_order(console: Console) -> None:
    api = Api(review=2, counts=QUEUE_COUNTS)
    me = make_me("tenant_staff", assistant=True)
    async with open_overview(console, api, "/portal", me=me) as page:
        await expect(page.get_by_test_id("overview-strip-mine")).to_be_visible()
        assert (
            await strip_text(page) == "2 Replies to check 5 Needs us 1 Overdue 3 Mine 2 Services on"
        )
        # The pills sit in the agreed order, each with its own test id.
        ids = (
            await page.get_by_test_id("overview-strip")
            .locator("li")
            .evaluate_all("items => items.map(item => item.dataset.testid)")
        )
        assert ids == QUEUE_PILLS
        await expect(page.get_by_test_id("overview-strip-needs-us")).to_contain_text("5")
        await expect(page.get_by_test_id("overview-strip-overdue")).to_contain_text("1")
        await expect(page.get_by_test_id("overview-strip-mine")).to_contain_text("3")
        # Overdue above zero takes the warning tone (the class, not the colour); Needs us does not.
        assert "fs-warning-bg" in (
            await page.get_by_test_id("overview-strip-overdue").get_attribute("class") or ""
        )
        assert "fs-warning-bg" not in (
            await page.get_by_test_id("overview-strip-needs-us").get_attribute("class") or ""
        )
        # No new request: the counts ride on the read the strip already makes.
        assert len(api.paths("inbox/conversations")) == 1, api.requests
        # Still plain text: no link, no role, no live region.
        strip = page.get_by_test_id("overview-strip")
        await expect(strip.locator("a")).to_have_count(0)
        await expect(strip.locator("[role]")).to_have_count(0)
        await expect(page.locator('[data-testid="overview-strip"][role]')).to_have_count(0)
        await expect(page.locator('[data-testid="overview-strip"][aria-live]')).to_have_count(0)


async def test_strip_overdue_at_zero_is_neutral_and_zero_counts_show(console: Console) -> None:
    counts = {**QUEUE_COUNTS, "needs_us": 0, "mine": 0, "overdue": 0}
    api = Api(review=0, counts=counts)
    async with open_overview(console, api, "/portal", me=make_me("tenant_staff")) as page:
        await expect(page.get_by_test_id("overview-strip-mine")).to_be_visible()
        assert (
            await strip_text(page) == "0 Replies to check 0 Needs us 0 Overdue 0 Mine 2 Services on"
        )
        for name in ("review", "needs-us", "overdue", "mine"):
            pill = page.get_by_test_id(f"overview-strip-{name}")
            assert "fs-warning-bg" not in (await pill.get_attribute("class") or "")


async def test_strip_without_counts_shows_no_queue_pill(console: Console) -> None:
    # `counts: null` is the server saying it could not read them; an absent key is an older server.
    for counts in (None, ...):
        api = Api(review=0, counts=counts)
        async with open_overview(console, api, "/portal", me=make_me("tenant_staff")) as page:
            await expect(page.get_by_test_id("overview-strip-services")).to_be_visible()
            assert await strip_text(page) == "0 Replies to check 2 Services on"
            for name in ("needs-us", "overdue", "mine"):
                await expect(page.get_by_test_id(f"overview-strip-{name}")).to_have_count(0)


async def test_strip_platform_admin_has_no_mine(console: Console) -> None:
    api = Api(review=2, counts=QUEUE_COUNTS)
    me = make_me("platform_admin", assistant=True)
    async with open_overview(console, api, "/?client_id=tenant-a", me=me) as page:
        await expect(page.get_by_test_id("overview-strip-needs-us")).to_be_visible()
        assert await strip_text(page) == "2 Replies to check 5 Needs us 1 Overdue 2 Services on"
        await expect(page.get_by_test_id("overview-strip-mine")).to_have_count(0)


async def test_strip_hides_review_for_read_only_and_when_inbox_read_fails(
    console: Console,
) -> None:
    # Read-only: no Inbox read at all, services stay, and the page is the small home.
    api = Api(review=5)
    async with open_overview(console, api, "/portal", me=make_me("read_only")) as page:
        await expect(page.get_by_test_id("overview-strip-services")).to_contain_text("2")
        await expect(page.get_by_test_id("overview-strip-review")).to_have_count(0)
        await expect(page.get_by_test_id("workspace-home")).to_be_visible()
        await expect(composer(page)).to_have_count(0)
        await page.wait_for_timeout(500)
        assert api.paths("inbox/conversations") == []
    # A failed Inbox read hides only that number; it never shows as zero.
    api = Api(review=None)
    async with open_overview(console, api, "/portal") as page:
        await expect(page.get_by_test_id("overview-strip-services")).to_contain_text("2")
        await requested(page, api, "inbox/conversations")
        await expect(page.get_by_test_id("overview-strip-review")).to_have_count(0)
        assert await strip_text(page) == "2 Services on"
    # Without Customer Support the Inbox is not asked.
    api = Api(packages=[package("hr")], review=7)
    async with open_overview(console, api, "/portal") as page:
        await expect(page.get_by_test_id("overview-strip-services")).to_contain_text("1")
        await expect(page.get_by_test_id("overview-strip-review")).to_have_count(0)
        await expect(page.get_by_test_id("workspace-home")).to_be_visible()
        assert api.paths("inbox/conversations") == []


async def test_strip_is_absent_when_both_reads_fail(console: Console) -> None:
    api = Api(review=None)
    api.packages_fail = True
    me = make_me("tenant_staff", assistant=True)
    async with open_overview(console, api, "/portal", me=me) as page:
        await expect(composer(page)).to_be_enabled()
        await requested(page, api, "services/active")
        await page.wait_for_timeout(500)
        await expect(page.get_by_test_id("overview-strip")).to_have_count(0)
        # Unknown packages: the Inbox is not asked either.
        assert api.paths("inbox/conversations") == []


async def test_chat_routes_redirect_to_the_services_preview_with_client_id(
    console: Console,
) -> None:
    api = Api()
    async with open_overview(console, api, "/chat", me=make_me("platform_admin")) as page:
        await page.wait_for_url(f"{console.base}/services?section=preview")
        await page.goto(f"{console.base}/chat?client_id=tenant-b")
        await page.wait_for_url(f"{console.base}/services?client_id=tenant-b&section=preview")
        await expect(page.get_by_label("Tenant", exact=True)).to_have_value("tenant-b")
    api = Api()
    async with open_overview(console, api, "/portal/chat") as page:
        await page.wait_for_url(f"{console.base}/portal/services?section=preview")
        await page.goto(f"{console.base}/portal/chat?client_id={TENANT}")
        await page.wait_for_url(
            f"{console.base}/portal/services?client_id={TENANT}&section=preview"
        )
        await expect(page.get_by_role("heading", name="Services", level=1)).to_be_visible()


@pytest.mark.parametrize(
    ("query", "target"),
    [
        # Encoded by the redirect, never concatenated: the value stays one parameter.
        ("client_id=a%26b%3Dc", "client_id=a%26b%3Dc&"),
        # A repeated parameter is ambiguous, so it is dropped rather than guessed.
        ("client_id=one&client_id=two", ""),
        # A protocol-relative look-alike stays on this origin as an encoded value.
        ("client_id=//evil.example", "client_id=%2F%2Fevil.example&"),
        ("client_id=%20%20", ""),
    ],
)
async def test_chat_redirect_keeps_awkward_client_ids_inert(
    console: Console, query: str, target: str
) -> None:
    api = Api()
    async with open_overview(console, api, "/chat", me=make_me("platform_admin")) as page:
        await page.wait_for_url(f"{console.base}/services?section=preview")
        await page.goto(f"{console.base}/chat?{query}")
        await page.wait_for_url(f"{console.base}/services?{target}section=preview")
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
            const scroll = document.querySelector('[data-testid="assistant-scroll"]');
            return {
                scrollHeight: doc.scrollHeight, innerHeight: window.innerHeight,
                scrollWidth: doc.scrollWidth, clientWidth: doc.clientWidth,
                inner: scroll ? [scroll.scrollHeight, scroll.clientHeight] : null,
            };
        }"""
    )


async def test_overview_does_not_scroll_the_page(console: Console) -> None:
    me = make_me("tenant_staff", assistant=True)
    for width, height in ((1440, 900), (390, 800)):
        api = Api(turns=30)
        async with open_overview(
            console, api, "/portal", me=me, width=width, height=height
        ) as page:
            # The newest turn is in view once the stored thread has loaded.
            await expect(page.get_by_text("Answer 30", exact=True)).to_be_visible()
            measured = await page_metrics(page)
            where = f"{width}x{height}: {measured}"
            assert measured["scrollHeight"] <= measured["innerHeight"], where
            # The long conversation scrolls inside the turn list instead.
            assert measured["inner"][0] > measured["inner"][1], where
            await expect(send_button(page)).to_be_in_viewport()
            await expect(page.get_by_test_id("overview-strip")).to_be_in_viewport()


async def test_overview_fits_five_widths_in_three_themes(console: Console) -> None:
    me = make_me("tenant_staff", assistant=True)
    for theme in THEMES:
        api = Api()
        async with open_overview(console, api, "/portal", me=me, theme=theme) as page:
            await expect(page.locator("html")).to_have_attribute("data-theme", theme)
            await expect(page.get_by_test_id("overview-strip")).to_be_visible()
            await expect(page.get_by_test_id("overview-ideas")).to_be_visible()
            for width in WIDTHS:
                await page.set_viewport_size({"width": width, "height": 800})
                await page.wait_for_timeout(150)
                measured = await page_metrics(page)
                where = f"{theme} {width}px: {measured}"
                assert measured["scrollWidth"] <= measured["clientWidth"], where
                assert measured["scrollHeight"] <= measured["innerHeight"], where
                await expect(page.get_by_test_id("overview-strip")).to_be_in_viewport()
                await expect(send_button(page)).to_be_in_viewport()
                send = await send_button(page).bounding_box()
                assert send and send["x"] + send["width"] <= width, where


async def test_a_platform_admin_with_a_company_gets_the_assistant_page(console: Console) -> None:
    # The chosen company has no Customer support; ?client_id= names it, so the assistant is on.
    api = Api(packages=[package("hr")])
    me = make_me("platform_admin", assistant=True)
    async with open_overview(console, api, "/?client_id=tenant-b", me=me) as page:
        await expect(assistant_page(page)).to_be_visible()
        await expect(page.get_by_test_id("workspace-home")).to_have_count(0)
        await expect(page.get_by_test_id("assistant-open")).to_have_count(0)
        await requested(page, api, "workspace/conversations")
        assert api.paths("workspace/conversations") and all(
            p == "workspace/conversations?client_id=tenant-b"
            for p in api.paths("workspace/conversations")
        )


async def test_a_failed_packages_read_keeps_the_assistant_without_an_error(
    console: Console,
) -> None:
    api = Api(review=None)
    api.packages_fail = True
    me = make_me("tenant_staff", assistant=True)
    async with open_overview(console, api, "/portal", me=me) as page:
        await requested(page, api, "services/active")
        await expect(composer(page)).to_be_enabled()
        await expect(page.get_by_test_id("workspace-home")).to_have_count(0)
        await expect(page.locator('p[role="alert"]')).to_have_count(0)


async def test_a_packages_read_that_never_answers_does_not_leave_the_page_waiting(
    console: Console,
) -> None:
    api = Api()
    api.packages_delay = 11.0  # the page gives up after 8 seconds
    me = make_me("tenant_staff", assistant=True)
    async with open_overview(console, api, "/portal", me=me) as page:
        await expect(page.locator('p[role="status"]', has_text="Loading")).to_have_count(1)
        await expect(composer(page)).to_be_enabled()
        await expect(page.get_by_test_id("workspace-home")).to_have_count(0)
        # The read carried on: its late answer still reaches the status strip.
        await expect(page.get_by_test_id("overview-strip-services")).to_contain_text("2")
