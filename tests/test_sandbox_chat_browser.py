"""Real browser acceptance for the ChatGPT-style sandbox chat layout.

`SandboxWorkspace` is mounted in a test-only harness page (a disposable staged copy of the
console, as in tests/test_voice_browser.py): the harness reads the role, the mode (legacy page
chrome or chromeless) and the tenant from its query string. Every browser-side
`/console/v1/*` call is answered by Playwright; an unexpected path is recorded, answered 500
and fails the test. One dev server serves every test in this file.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.async_api import Page, Route, async_playwright, expect

pytestmark = pytest.mark.asyncio

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "CSA_WebConsole/csa-console/web"
WAIT = 60_000  # The first visit compiles the harness in `next dev`.
CHIPS = [
    "Ask something a customer might ask",
    "Check what the knowledge base says about a topic",
    "Rephrase an answer for a customer",
]
FULL_NOTICE = (
    "No customer records, orders, bookings, tickets, invoices, payments, email or staff "
    "escalation. No external customer sends. Business confirmations and attachments are "
    "unavailable. Browser dictation fills a draft for review; read aloud uses a local browser "
    "voice. Use Knowledge base for document uploads. Archive hides history; it does not "
    "delete it."
)
COMPACT = "Private sandbox: knowledge and product questions only."
WIDTHS = [320, 390, 768, 1024, 1440]
THEMES = ["cobalt", "night", "bureau"]
REPLY = "Our opening hours are nine to six on weekdays."

HARNESS = """"use client";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { SandboxWorkspace } from "@/components/console/SandboxWorkspace";
import { AssistantDock } from "@/components/console/AssistantDock";
import type { Me } from "@/lib/identity-contracts";
function Inner() {
  const params = useSearchParams();
  const role = params.get("role") ?? "tenant_staff";
  const tenant = role === "platform_admin" ? null : "synthetic-tenant";
  const dock = params.get("dock") === "1";
  const me = { role, active_client_id: tenant, workspace_assistant: dock } as Me;
  const chosen = params.get("tenant") ?? undefined;
  if (params.get("mode") === "chromeless")
    return <div className="h-dvh">
      <SandboxWorkspace me={me} chromeless tenant={chosen} />
      {dock && <AssistantDock me={me} packages={[]} />}
    </div>;
  return <main className="p-4"><SandboxWorkspace me={me} /></main>;
}
export default function Harness() {
  return <Suspense><Inner /></Suspense>;
}
"""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def thread(sequence: int) -> dict[str, Any]:
    return {
        "id": f"thread-{sequence}",
        "sequence": sequence,
        "created_at": f"2026-10-2{sequence}T00:00:00Z",
        "archived_at": None,
    }


class Synthetic:
    """The chat API as Playwright answers it, with every request recorded."""

    def __init__(self, threads: int = 0) -> None:
        self.threads = [thread(n) for n in range(threads, 0, -1)]
        self.turns: dict[str, list[dict[str, Any]]] = {}
        self.requests: list[tuple[str, str]] = []
        self.bodies: list[tuple[str, Any]] = []
        self.unexpected: list[str] = []
        # Deliberate failures: answer the next N conversation creations, and every turn read of
        # these conversations, with 500 (the browser logs those as console errors).
        self.failed_creations = 0
        self.failed_reads: set[str] = set()
        self.deliberate_failures = 0
        # Turn pages hold this many turns, newest first by id, with `before=` paging.
        self.page_size = 50

    @property
    def posts(self) -> list[str]:
        return [path.split("?")[0] for method, path in self.requests if method == "POST"]

    async def route(self, route: Route) -> None:
        request = route.request
        tail = request.url.split("/console/v1/")[1]
        path = tail.split("?")[0]
        self.requests.append((request.method, tail))
        if request.method == "POST":
            self.bodies.append((path, request.post_data_json))
        data: Any
        if path == "auth/csrf":
            data = {"csrf_token": "synthetic-csrf"}
        elif path == "admin/clients" and request.method == "GET":
            data = [
                {"id": "tenant-a", "name": "Tenant A"},
                {"id": "tenant-b", "name": "Tenant B"},
            ]
        elif path == "chat/voice" and request.method == "GET":
            data = {"enabled": False}
        elif path == "chat/conversations" and request.method == "GET":
            data = {"items": self.threads, "next_cursor": None}
        elif path == "chat/conversations" and request.method == "POST" and self.failed_creations:
            self.failed_creations -= 1
            self.deliberate_failures += 1
            await route.fulfill(status=500, json={"detail": "synthetic outage"})
            return
        elif path == "chat/conversations" and request.method == "POST":
            created = thread(len(self.threads) + 1)
            self.threads.insert(0, created)
            await route.fulfill(status=201, json=created)
            return
        elif re.fullmatch(r"chat/conversations/[\w-]+/turns", path):
            conversation = path.split("/")[2]
            items = self.turns.setdefault(conversation, [])
            if request.method == "GET" and conversation in self.failed_reads:
                self.deliberate_failures += 1
                await route.fulfill(status=500, json={"detail": "synthetic outage"})
                return
            if request.method == "POST":
                body = request.post_data_json
                assert body is not None and set(body) == {"request_key", "body"}
                items.append(
                    {
                        "id": len(items) + 1,
                        "trace_id": "trace",
                        "request_key": body["request_key"],
                        "body": body["body"],
                        "created_at": "2026-10-30T00:00:00Z",
                        "status": "available",
                        "replies": [{"id": f"reply-{len(items) + 1}", "body": REPLY}],
                    }
                )
                data = {"duplicate": False}
            else:
                match = re.search(r"before=(\d+)", tail)
                older = [t for t in items if not match or t["id"] < int(match.group(1))]
                page = older[-self.page_size :]
                more = len(older) > len(page)
                data = {"items": page, "next_cursor": page[0]["id"] if more else None}
        else:
            self.unexpected.append(f"{request.method} {tail}")
            await route.fulfill(status=500, json={"detail": "unexpected synthetic call"})
            return
        await route.fulfill(json=data)


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    node = shutil.which("node")
    assert node, "Node is required for browser acceptance"
    assert (WEB / "node_modules/next/dist/bin/next").is_file(), "run npm ci in the web root first"
    root = tmp_path_factory.mktemp("sandbox-chat")
    stage = root / "web"
    environment = {
        "PATH": os.environ["PATH"],
        "NEXT_TELEMETRY_DISABLED": "1",
        "CSA_CONSOLE_API_ORIGIN": "http://127.0.0.1:1",
    }
    subprocess.run(
        [node, str(WEB / "scripts/check-isolated.mjs"), "--context", str(stage)],
        check=True,
        cwd=ROOT,
        env=environment,
        capture_output=True,
    )
    (stage / "node_modules").symlink_to(WEB / "node_modules", target_is_directory=True)
    # Test-only page in a disposable source copy. Production routing and auth are unchanged.
    next((stage / "app").glob("**/login/page.tsx")).write_text(HARNESS)
    port = free_port()
    log_path = root / "frontend.log"
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
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(240):
            assert process.poll() is None, log_path.read_text()
            try:
                if httpx.get(f"{base}/login", timeout=30, trust_env=False).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        else:
            pytest.fail("Synthetic chat frontend did not start")
        yield base
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()


@asynccontextmanager
async def open_chat(
    server: str,
    api: Synthetic,
    *,
    query: str = "",
    width: int = 1440,
    theme: str = "cobalt",
) -> AsyncIterator[Page]:
    """A fresh browser on the harness; fails on unexpected API calls, page or console errors."""
    errors: list[str] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context(viewport={"width": width, "height": 900})
            context.set_default_timeout(WAIT)
            await context.add_cookies([{"name": "fs-theme", "value": theme, "url": server}])
            page = await context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            # React reports hydration mismatches and key warnings as console errors.
            page.on(
                "console",
                lambda message: errors.append(message.text) if message.type == "error" else None,
            )
            await page.route("**/console/v1/**", api.route)
            await page.goto(f"{server}/login{query}")
            yield page
        finally:
            await browser.close()
    assert not api.unexpected, api.unexpected
    if api.deliberate_failures:
        errors = [e for e in errors if "status of 500" not in e]
    assert not errors, errors


def squash(text: str) -> str:
    return " ".join(text.split())


async def test_empty_state_shows_prompt_chips_and_full_disclaimer(server: str) -> None:
    api = Synthetic()
    async with open_chat(server, api) as page:
        await expect(page.get_by_role("heading", name="How can I help today?")).to_be_visible(
            timeout=WAIT
        )
        chips = page.get_by_test_id("sandbox-chips").get_by_role("button")
        await expect(chips).to_have_text(CHIPS)
        notice = page.get_by_test_id("sandbox-disclaimer")
        await expect(notice).to_be_visible()
        assert squash(await notice.inner_text()) == FULL_NOTICE
        await expect(page.get_by_role("button", name="What it cannot do")).to_have_count(0)
        await expect(page.get_by_label("Text question", exact=False)).to_be_enabled()
        await expect(page.get_by_role("button", name="New chat")).to_be_enabled()


async def test_chip_fills_the_composer_without_sending(server: str) -> None:
    api = Synthetic()
    async with open_chat(server, api) as page:
        chip = page.get_by_role("button", name=CHIPS[1], exact=True)
        await chip.click(timeout=WAIT)
        composer = page.get_by_label("Text question", exact=False)
        await expect(composer).to_have_value(CHIPS[1])
        await expect(composer).to_be_focused()
        await page.wait_for_timeout(800)
        assert api.posts == []
        await expect(page.get_by_role("heading", name="How can I help today?")).to_be_visible()


async def test_send_shows_reply_and_compacts_the_disclaimer(server: str) -> None:
    api = Synthetic()
    async with open_chat(server, api) as page:
        composer = page.get_by_label("Text question", exact=False)
        await composer.fill("What are your opening hours?", timeout=WAIT)
        await page.get_by_role("button", name="Send to sandbox", exact=True).click()
        log = page.get_by_role("log", name="Sandbox messages")
        await expect(log.get_by_text(REPLY)).to_be_visible()
        await expect(log.get_by_text("What are your opening hours?")).to_be_visible()
        await expect(log.get_by_text("Available privately in this sandbox")).to_be_visible()
        assert api.posts == ["chat/conversations", "chat/conversations/thread-1/turns"]
        await expect(composer).to_have_value("")
        await expect(page.get_by_text(COMPACT, exact=True)).to_be_visible()
        await expect(page.get_by_test_id("sandbox-disclaimer")).to_be_hidden()
        disclosure = page.get_by_role("button", name="What it cannot do")
        await expect(disclosure).to_have_attribute("aria-expanded", "false")
        await disclosure.click()
        await expect(disclosure).to_have_attribute("aria-expanded", "true")
        notice = page.get_by_test_id("sandbox-disclaimer")
        await expect(notice).to_be_visible()
        assert squash(await notice.inner_text()) == FULL_NOTICE
        # The new conversation is selected in the history list.
        await expect(
            page.get_by_role("button", name="Conversation 1", exact=True)
        ).to_have_attribute("aria-current", "true")


async def test_history_lists_conversations_and_new_chat_creates_one(server: str) -> None:
    api = Synthetic(threads=2)
    api.turns["thread-2"] = [
        {
            "id": 1,
            "trace_id": "trace",
            "request_key": "earlier",
            "body": "Do you deliver on Sundays?",
            "created_at": "2026-10-22T00:00:00Z",
            "status": "available",
            "replies": [{"id": "r1", "body": "Not on Sundays."}],
        }
    ]
    async with open_chat(server, api) as page:
        history = page.get_by_role("navigation", name="Your private conversations")
        await expect(history.get_by_role("button")).to_have_text(
            ["Conversation 2", "Conversation 1"], timeout=WAIT
        )
        second = history.get_by_role("button", name="Conversation 2", exact=True)
        await second.click()
        await expect(second).to_have_attribute("aria-current", "true")
        await expect(
            page.get_by_role("log", name="Sandbox messages").get_by_text("Not on Sundays.")
        ).to_be_visible()
        await expect(page.get_by_text(COMPACT, exact=True)).to_be_visible()
        await page.get_by_role("button", name="New chat").click()
        third = history.get_by_role("button", name="Conversation 3", exact=True)
        await expect(third).to_have_attribute("aria-current", "true")
        await expect(history.get_by_role("button")).to_have_text(
            ["Conversation 3", "Conversation 2", "Conversation 1"]
        )
        assert api.posts == ["chat/conversations"]
        await expect(page.get_by_role("heading", name="How can I help today?")).to_be_visible()
        await expect(second).not_to_have_attribute("aria-current", "true")


async def test_read_only_member_composer_is_disabled(server: str) -> None:
    api = Synthetic(threads=1)
    async with open_chat(server, api, query="?role=read_only") as page:
        await expect(
            page.get_by_text(
                "Read-only membership: sending, creating and archiving conversations are disabled."
            )
        ).to_be_visible(timeout=WAIT)
        await expect(page.get_by_label("Text question", exact=False)).to_be_disabled()
        await expect(
            page.get_by_role("button", name="Send to sandbox", exact=True)
        ).to_be_disabled()
        await expect(page.get_by_role("button", name="New chat")).to_be_disabled()
        for chip in CHIPS:
            await expect(page.get_by_role("button", name=chip, exact=True)).to_be_disabled()
        # Reading history still works.
        await page.get_by_role("button", name="Conversation 1", exact=True).click()
        await expect(page.get_by_role("button", name="Archive conversation")).to_be_disabled()
        assert api.posts == []


async def test_history_opens_as_a_slide_over_on_phone(server: str) -> None:
    api = Synthetic(threads=1)
    async with open_chat(server, api, width=390, query="?mode=chromeless") as page:
        opener = page.get_by_role("button", name="History", exact=True)
        await expect(opener).to_be_visible(timeout=WAIT)
        # The column is not shown at this width; the list lives in the slide-over.
        await expect(page.get_by_role("button", name="Conversation 1", exact=True)).to_be_hidden()
        await opener.click()
        dialog = page.get_by_role("dialog", name="Your private conversations")
        await expect(dialog).to_be_visible()
        await expect(dialog.get_by_role("button", name="New chat")).to_be_visible()
        await dialog.get_by_role("button", name="Conversation 1", exact=True).click()
        await expect(dialog).to_be_hidden()
        await expect(page.get_by_test_id("sandbox-chat-title")).to_have_text("Conversation 1")
        await expect(opener).to_be_focused()


async def overflow(page: Page) -> int:
    return await page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )


def intersects(a: Any, b: Any) -> bool:
    return not (
        a["x"] + a["width"] <= b["x"]
        or b["x"] + b["width"] <= a["x"]
        or a["y"] + a["height"] <= b["y"]
        or b["y"] + b["height"] <= a["y"]
    )


async def check_widths(page: Page, label: str) -> None:
    for width in WIDTHS:
        await page.set_viewport_size({"width": width, "height": 800})
        await page.wait_for_timeout(150)
        assert await overflow(page) <= 0, f"{label} overflows at {width}px"
        composer = page.get_by_label("Text question", exact=False)
        send = page.get_by_role("button", name="Send to sandbox", exact=True)
        box = await composer.bounding_box()
        send_box = await send.bounding_box()
        assert box and send_box
        assert box["width"] >= 150, f"{label}: composer too narrow at {width}px"
        assert send_box["x"] + send_box["width"] <= width, f"{label}: send off screen at {width}px"
        # The real floating assistant dock button (workspace_assistant on) must not cover it.
        dock_box = await page.get_by_test_id("assistant-open").bounding_box()
        assert dock_box, f"{label}: the dock button is missing at {width}px"
        assert not intersects(send_box, dock_box), (
            f"{label}: send button {send_box} under the dock {dock_box} at {width}px"
        )


async def composer_radius(page: Page) -> str:
    return await page.get_by_label("Text question", exact=False).evaluate(
        "element => getComputedStyle(element.parentElement).borderTopLeftRadius"
    )


async def test_chat_fits_five_widths_in_three_themes(server: str) -> None:
    for theme in THEMES:
        api = Synthetic()
        query = "?mode=chromeless&dock=1"
        async with open_chat(server, api, theme=theme, query=query) as page:
            assert await page.evaluate("document.documentElement.dataset.theme") == theme
            await expect(page.get_by_role("heading", name="How can I help today?")).to_be_visible(
                timeout=WAIT
            )
            # Bureau is square-cornered; cobalt and night keep the rounded composer.
            radius = await composer_radius(page)
            assert (radius == "0px") == (theme == "bureau"), f"{theme} composer radius {radius}"
            await check_widths(page, f"{theme} empty")
            await page.get_by_label("Text question", exact=False).fill("Opening hours?")
            await page.get_by_role("button", name="Send to sandbox", exact=True).click()
            await expect(page.get_by_text(REPLY)).to_be_visible()
            await check_widths(page, f"{theme} reply")


async def test_chromeless_mode_has_no_heading_or_tenant_select(server: str) -> None:
    api = Synthetic()
    async with open_chat(
        server, api, query="?mode=chromeless&role=platform_admin&tenant=tenant-b"
    ) as page:
        await expect(page.get_by_role("heading", name="How can I help today?")).to_be_visible(
            timeout=WAIT
        )
        await expect(page.get_by_role("heading", name="Sandbox assistant")).to_have_count(0)
        await expect(page.get_by_label("Tenant", exact=True)).to_have_count(0)
        # The empty state renders before the list read lands: wait for the read.
        history = page.get_by_role("navigation", name="Your private conversations")
        await expect(history.get_by_text("No conversations yet.")).to_be_visible()
        reads = [path for method, path in api.requests if method == "GET"]
        assert "chat/conversations?client_id=tenant-b" in reads, reads
        assert not any(path.startswith("admin/clients") for path in reads), reads


async def test_legacy_mode_keeps_heading_and_tenant_select_for_platform_admin(server: str) -> None:
    api = Synthetic()
    async with open_chat(server, api, query="?role=platform_admin") as page:
        await expect(page.get_by_role("heading", name="Sandbox assistant", level=1)).to_be_visible(
            timeout=WAIT
        )
        tenant = page.get_by_label("Tenant", exact=True)
        await expect(tenant.locator("option")).to_have_count(3)
        await expect(page.get_by_text("Select a tenant to continue.")).to_be_visible()
        await tenant.select_option("tenant-a")
        await expect(page.get_by_role("heading", name="How can I help today?")).to_be_visible()
        history = page.get_by_role("navigation", name="Your private conversations")
        await expect(history.get_by_text("No conversations yet.")).to_be_visible()
        reads = [path for method, path in api.requests if method == "GET"]
        assert "chat/conversations?client_id=tenant-a" in reads, reads


async def test_failed_creation_keeps_the_draft_and_send_retries_it(server: str) -> None:
    api = Synthetic()
    api.failed_creations = 1
    async with open_chat(server, api) as page:
        composer = page.get_by_label("Text question", exact=False)
        await composer.fill("Do you open on Sundays?", timeout=WAIT)
        send = page.get_by_role("button", name="Send to sandbox", exact=True)
        await send.click()
        await expect(page.get_by_text("Retry creation keeps the same request key.")).to_be_visible()
        # Not locked out, and the question is still there.
        await expect(composer).to_be_enabled()
        await expect(composer).to_have_value("Do you open on Sundays?")
        await expect(page.get_by_role("button", name=CHIPS[0], exact=True)).to_be_enabled()
        await expect(send).to_be_enabled()
        await send.click()
        log = page.get_by_role("log", name="Sandbox messages")
        await expect(log.get_by_text("Do you open on Sundays?")).to_be_visible()
        await expect(log.get_by_text(REPLY)).to_be_visible()
        creations = [body for path, body in api.bodies if path == "chat/conversations"]
        assert len(creations) == 2
        assert creations[0]["request_key"] == creations[1]["request_key"], "retry keeps the key"
        sends = [body for path, body in api.bodies if path.endswith("/turns")]
        assert [body["body"] for body in sends] == ["Do you open on Sundays?"]


def numbered_turns(count: int) -> list[dict[str, Any]]:
    return [
        {
            "id": n,
            "trace_id": "trace",
            "request_key": f"key-{n}",
            "body": f"Question {n}",
            "created_at": "2026-10-29T00:00:00Z",
            "status": "available",
            "replies": [{"id": f"r{n}", "body": f"Answer {n}"}],
        }
        for n in range(1, count + 1)
    ]


async def test_scroll_follows_the_reader_not_the_poll(server: str) -> None:
    api = Synthetic(threads=1)
    api.turns["thread-1"] = numbered_turns(30)
    api.page_size = 20
    async with open_chat(server, api) as page:
        await page.get_by_role("button", name="Conversation 1", exact=True).click(timeout=WAIT)
        log = page.get_by_role("log", name="Sandbox messages")
        await expect(log.get_by_text(re.compile(r"Answer 30$"))).to_be_visible()
        scroller = page.get_by_test_id("sandbox-scroll")
        gap = "e => e.scrollHeight - e.scrollTop - e.clientHeight"
        assert await scroller.evaluate(gap) < 2, "a conversation opens at its end"
        # Older messages are added above without moving what the reader sees.
        await scroller.evaluate("e => { e.scrollTop = 0; }")
        anchor = log.get_by_text(re.compile(r"Question 11$"))
        before = await anchor.bounding_box()
        assert before
        await page.get_by_role("button", name="Older messages").click()
        await expect(log.get_by_text(re.compile(r"Question 1$"))).to_be_attached()
        await page.wait_for_timeout(300)
        after = await anchor.bounding_box()
        assert after and abs(after["y"] - before["y"]) < 4, (before, after)
        # A reply landing by polling does not pull a reader who scrolled up.
        await scroller.evaluate("e => { e.scrollTop = 300; }")
        await page.wait_for_timeout(200)
        api.turns["thread-1"].append(numbered_turns(31)[-1])
        await expect(log.get_by_text(re.compile(r"Answer 31$"))).to_be_attached(timeout=10_000)
        await page.wait_for_timeout(300)
        assert abs(await scroller.evaluate("e => e.scrollTop") - 300) < 2
        # The reader's own question scrolls to the end.
        await page.get_by_label("Text question", exact=False).fill("One more question")
        await page.get_by_role("button", name="Send to sandbox", exact=True).click()
        await expect(log.get_by_text("One more question")).to_be_visible()
        await page.wait_for_timeout(300)
        assert await scroller.evaluate(gap) < 2


async def test_disclaimer_shows_when_the_first_read_fails(server: str) -> None:
    api = Synthetic(threads=1)
    api.failed_reads.add("thread-1")
    async with open_chat(server, api) as page:
        await page.get_by_role("button", name="Conversation 1", exact=True).click(timeout=WAIT)
        await expect(page.get_by_text("Polling disconnected:", exact=False)).to_be_visible()
        await expect(page.get_by_role("heading", name="How can I help today?")).to_have_count(0)
        notice = page.get_by_test_id("sandbox-disclaimer")
        await expect(notice).to_be_visible()
        assert squash(await notice.inner_text()) == FULL_NOTICE
        await expect(page.get_by_label("Text question", exact=False)).to_be_enabled()


async def test_enter_sends_and_keeps_focus_but_not_with_shift_or_composition(server: str) -> None:
    api = Synthetic()
    async with open_chat(server, api) as page:
        composer = page.get_by_label("Text question", exact=False)
        await composer.fill("Hello", timeout=WAIT)
        await composer.press("Shift+Enter")
        await expect(composer).to_have_value("Hello\n")
        # An IME composition commits with Enter: never a send (Chromium isComposing, Safari 229).
        await composer.evaluate(
            """element => {
              element.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
              element.dispatchEvent(new KeyboardEvent("keydown",
                { key: "Enter", bubbles: true, cancelable: true, isComposing: true }));
              element.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true }));
              const safari = new KeyboardEvent("keydown",
                { key: "Enter", bubbles: true, cancelable: true });
              Object.defineProperty(safari, "keyCode", { get: () => 229 });
              element.dispatchEvent(safari);
            }"""
        )
        await page.wait_for_timeout(500)
        assert api.posts == []
        await composer.fill("What are your opening hours?")
        await composer.press("Enter")
        log = page.get_by_role("log", name="Sandbox messages")
        await expect(log.get_by_text(REPLY)).to_be_visible()
        assert api.posts == ["chat/conversations", "chat/conversations/thread-1/turns"]
        await expect(composer).to_have_value("")
        await expect(composer).to_be_focused()
