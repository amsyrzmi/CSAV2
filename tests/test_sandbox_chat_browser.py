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
    "What does our knowledge base say about ",
    "Which of our products ",
    "How should we answer a customer who asks about ",
]
# A chip's button reads the starter, then an ellipsis; a click fills the composer with the starter.
CHIP_LABELS = [chip.rstrip() + "…" for chip in CHIPS]
HONESTY = "Answers come from your knowledge base and products."
# Text of the removed limits wall; none of it may appear anywhere in the chat any more.
REMOVED_TEXT = [
    "What it cannot do",
    "It cannot look up",
    "It cannot read or send email",
    "Archiving a chat hides it but does not delete it.",
    "Dictation fills in your message",
    "Private chat. It only answers",
]
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
        # Whether transcription answers "enabled" (the voice tray then shows its consent boxes).
        self.voice_enabled = False

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
            data = {"enabled": self.voice_enabled}
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
    init_script: str | None = None,
    reduced_motion: bool = False,
) -> AsyncIterator[Page]:
    """A fresh browser on the harness; fails on unexpected API calls, page or console errors."""
    errors: list[str] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context(
                viewport={"width": width, "height": 900},
                reduced_motion="reduce" if reduced_motion else "no-preference",
            )
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
            if init_script:
                await page.add_init_script(init_script)
            await page.goto(f"{server}/login{query}")
            yield page
        finally:
            await browser.close()
    assert not api.unexpected, api.unexpected
    if api.deliberate_failures:
        errors = [e for e in errors if "status of 500" not in e]
    assert not errors, errors


async def test_empty_state_shows_prompt_chips_and_the_honesty_line(server: str) -> None:
    api = Synthetic()
    async with open_chat(server, api) as page:
        await expect(page.get_by_role("heading", name="How can I help today?")).to_be_visible(
            timeout=WAIT
        )
        chips = page.get_by_test_id("sandbox-chips").get_by_role("button")
        await expect(chips).to_have_text(CHIP_LABELS)
        await expect(page.get_by_test_id("sandbox-disclaimer")).to_have_count(0)
        await expect(page.get_by_test_id("chat-honesty-line")).to_have_text(HONESTY)
        await expect(page.get_by_label("Your question", exact=False)).to_be_enabled()
        await expect(page.get_by_role("button", name="New chat")).to_be_enabled()


def seeded_turn(number: int, status: str = "available", **extra: Any) -> dict[str, Any]:
    replies = [{"id": f"r{number}", "body": REPLY}] if status == "available" else []
    return {
        "id": number,
        "trace_id": "trace",
        "request_key": f"seeded-{number}",
        "body": f"Seeded question {number}",
        "created_at": "2026-10-22T00:00:00Z",
        "status": status,
        "replies": replies,
    } | extra


async def test_long_limits_text_and_expander_are_gone(server: str) -> None:
    api = Synthetic(threads=1)
    api.turns["thread-1"] = [seeded_turn(1)]
    async with open_chat(server, api) as page:
        # Empty state (a new chat), then a conversation with a message, then after a send.
        await page.get_by_role("button", name="New chat").click(timeout=WAIT)
        await expect(page.get_by_role("heading", name="How can I help today?")).to_be_visible()
        for stage in ("empty state", "conversation"):
            if stage == "conversation":
                await page.get_by_role("button", name="Conversation 1", exact=True).click()
                await expect(page.get_by_role("log", name="Chat messages")).to_be_visible()
            body = await page.locator("body").inner_text()
            for text in REMOVED_TEXT:
                assert text not in body, (stage, text)
            await expect(page.get_by_role("button", name="What it cannot do")).to_have_count(0)
            await expect(page.get_by_test_id("sandbox-disclaimer")).to_have_count(0)
            await expect(page.get_by_test_id("chat-honesty-line")).to_have_count(1)
            await expect(page.get_by_test_id("chat-honesty-line")).to_have_text(HONESTY)
        await page.get_by_label("Your question", exact=False).fill("What are your opening hours?")
        await page.get_by_role("button", name="Send message", exact=True).click()
        await expect(
            page.get_by_role("log").get_by_text("What are your opening hours?")
        ).to_be_visible()
        body = await page.locator("body").inner_text()
        assert body.count(HONESTY) == 1
        for text in REMOVED_TEXT:
            assert text not in body, text


async def test_chip_fills_the_composer_without_sending(server: str) -> None:
    api = Synthetic()
    async with open_chat(server, api) as page:
        chip = page.get_by_role("button", name=CHIP_LABELS[1], exact=True)
        await chip.click(timeout=WAIT)
        composer = page.get_by_label("Your question", exact=False)
        await expect(composer).to_have_value(CHIPS[1])
        await expect(composer).to_be_focused()
        await page.wait_for_timeout(800)
        assert api.posts == []
        await expect(page.get_by_role("heading", name="How can I help today?")).to_be_visible()


async def test_send_shows_reply_and_keeps_one_honesty_line(server: str) -> None:
    api = Synthetic()
    async with open_chat(server, api) as page:
        composer = page.get_by_label("Your question", exact=False)
        await composer.fill("What are your opening hours?", timeout=WAIT)
        await page.get_by_role("button", name="Send message", exact=True).click()
        log = page.get_by_role("log", name="Chat messages")
        await expect(log.get_by_text(REPLY)).to_be_visible()
        await expect(log.get_by_text("What are your opening hours?")).to_be_visible()
        await expect(log.get_by_text("Available privately in this sandbox")).to_be_visible()
        assert api.posts == ["chat/conversations", "chat/conversations/thread-1/turns"]
        await expect(composer).to_have_value("")
        await expect(page.get_by_test_id("chat-honesty-line")).to_have_text(HONESTY)
        await expect(page.get_by_test_id("sandbox-disclaimer")).to_have_count(0)
        await expect(page.get_by_role("button", name="What it cannot do")).to_have_count(0)
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
            page.get_by_role("log", name="Chat messages").get_by_text("Not on Sundays.")
        ).to_be_visible()
        await expect(page.get_by_test_id("chat-honesty-line")).to_have_text(HONESTY)
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
                "Read-only access: you can read your chats but not send messages, "
                "start chats or archive them."
            )
        ).to_be_visible(timeout=WAIT)
        await expect(page.get_by_label("Your question", exact=False)).to_be_disabled()
        await expect(page.get_by_role("button", name="Send message", exact=True)).to_be_disabled()
        await expect(page.get_by_role("button", name="New chat")).to_be_disabled()
        for label in CHIP_LABELS:
            await expect(page.get_by_role("button", name=label, exact=True)).to_be_disabled()
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
        composer = page.get_by_label("Your question", exact=False)
        send = page.get_by_role("button", name="Send message", exact=True)
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
    return await page.get_by_label("Your question", exact=False).evaluate(
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
            await page.get_by_label("Your question", exact=False).fill("Opening hours?")
            await page.get_by_role("button", name="Send message", exact=True).click()
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


async def test_switching_conversation_clears_a_failed_new_chat(server: str) -> None:
    api = Synthetic(threads=1)
    api.failed_creations = 1
    async with open_chat(server, api) as page:
        await page.get_by_role("button", name="New chat", exact=True).click(timeout=WAIT)
        failure = page.get_by_text(
            "Trying again repeats the same request, so no second chat is created."
        )
        retry = page.get_by_role("button", name="Try creating the chat again")
        await expect(failure).to_be_visible()
        await expect(retry).to_be_visible()
        await page.get_by_role("button", name="Conversation 1", exact=True).click()
        await expect(failure).to_have_count(0)
        await expect(page.get_by_role("button", name="New chat", exact=True)).to_be_visible()
        await expect(retry).to_have_count(0)


async def test_failed_creation_keeps_the_draft_and_send_retries_it(server: str) -> None:
    api = Synthetic()
    api.failed_creations = 1
    async with open_chat(server, api) as page:
        composer = page.get_by_label("Your question", exact=False)
        await composer.fill("Do you open on Sundays?", timeout=WAIT)
        send = page.get_by_role("button", name="Send message", exact=True)
        await send.click()
        await expect(
            page.get_by_text("Trying again repeats the same request, so no second chat is created.")
        ).to_be_visible()
        # Not locked out, and the question is still there.
        await expect(composer).to_be_enabled()
        await expect(composer).to_have_value("Do you open on Sundays?")
        await expect(page.get_by_role("button", name=CHIP_LABELS[0], exact=True)).to_be_enabled()
        await expect(send).to_be_enabled()
        await send.click()
        log = page.get_by_role("log", name="Chat messages")
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
        log = page.get_by_role("log", name="Chat messages")
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
        await page.get_by_label("Your question", exact=False).fill("One more question")
        await page.get_by_role("button", name="Send message", exact=True).click()
        await expect(log.get_by_text("One more question")).to_be_visible()
        await page.wait_for_timeout(300)
        assert await scroller.evaluate(gap) < 2


async def test_honesty_line_stays_when_the_first_read_fails(server: str) -> None:
    api = Synthetic(threads=1)
    api.failed_reads.add("thread-1")
    async with open_chat(server, api) as page:
        await page.get_by_role("button", name="Conversation 1", exact=True).click(timeout=WAIT)
        await expect(page.get_by_text("We lost touch with the chat:", exact=False)).to_be_visible()
        await expect(page.get_by_role("heading", name="How can I help today?")).to_have_count(0)
        await expect(page.get_by_test_id("sandbox-disclaimer")).to_have_count(0)
        await expect(page.get_by_test_id("chat-honesty-line")).to_have_text(HONESTY)
        await expect(page.get_by_label("Your question", exact=False)).to_be_enabled()


async def test_enter_sends_and_keeps_focus_but_not_with_shift_or_composition(server: str) -> None:
    api = Synthetic()
    async with open_chat(server, api) as page:
        composer = page.get_by_label("Your question", exact=False)
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
        log = page.get_by_role("log", name="Chat messages")
        await expect(log.get_by_text(REPLY)).to_be_visible()
        assert api.posts == ["chat/conversations", "chat/conversations/thread-1/turns"]
        await expect(composer).to_have_value("")
        await expect(composer).to_be_focused()


SPEECH_STUB = """
window.speechTest = { speaking: false };
window.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
Object.defineProperty(window, "speechSynthesis", { value: {
  getVoices: () => [{ localService: true, lang: "en-US", name: "stub" }],
  speak: () => { window.speechTest.speaking = true; },
  cancel: () => { window.speechTest.speaking = false; },
  get speaking() { return window.speechTest.speaking; },
} });
"""


async def test_stop_reading_aloud_only_while_speaking(server: str) -> None:
    api = Synthetic(threads=1)
    api.turns["thread-1"] = [seeded_turn(1)]
    async with open_chat(server, api, init_script=SPEECH_STUB) as page:
        await page.get_by_role("button", name="Conversation 1", exact=True).click(timeout=WAIT)
        stop = page.get_by_role("button", name="Stop reading aloud")
        read = page.get_by_role("button", name="Read reply aloud")
        await expect(read).to_be_visible()
        await expect(stop).to_have_count(0)
        await read.click()
        await expect(stop).to_be_visible()
        await stop.click()
        await expect(stop).to_have_count(0)
        # Speech that ends by itself takes the button away too.
        await read.click()
        await expect(stop).to_be_visible()
        await page.evaluate("window.speechTest.speaking = false")
        await expect(stop).to_have_count(0)


async def test_check_for_new_messages_appears_after_a_failed_refresh_and_with_the_delay_notice(
    server: str,
) -> None:
    api = Synthetic(threads=2)
    api.turns["thread-1"] = [seeded_turn(1)]
    # Long past the 90-second delay, still waiting on a reply.
    api.turns["thread-2"] = [seeded_turn(1, status="processing")]
    check = lambda page: page.get_by_role("button", name="Check for new messages")  # noqa: E731
    async with open_chat(server, api) as page:
        await page.get_by_role("button", name="Conversation 1", exact=True).click(timeout=WAIT)
        await expect(page.get_by_role("log", name="Chat messages")).to_be_visible()
        # A healthy conversation: no button, however long it has been open.
        await page.wait_for_timeout(600)
        await expect(check(page)).to_have_count(0)
        # The automatic refresh fails: the lost-touch notice and the button appear together.
        api.failed_reads.add("thread-1")
        await expect(page.get_by_text("We lost touch with the chat:", exact=False)).to_be_visible(
            timeout=15_000
        )
        await expect(check(page)).to_be_visible()
        api.failed_reads.discard("thread-1")
        await check(page).click()
        await expect(page.get_by_text("We lost touch with the chat:", exact=False)).to_have_count(0)
        await expect(check(page)).to_have_count(0)
        # A reply that has taken more than 90 seconds: the delay notice points at the button.
        await page.get_by_role("button", name="Conversation 2", exact=True).click()
        await expect(
            page.get_by_text("This is taking longer than usual.", exact=False)
        ).to_be_visible()
        await expect(check(page)).to_be_visible()


async def test_microphone_opens_the_voice_tray_with_consent_inside(server: str) -> None:
    api = Synthetic(threads=1)
    api.voice_enabled = True
    async with open_chat(server, api) as page:
        await page.get_by_role("button", name="Conversation 1", exact=True).click(timeout=WAIT)
        mic = page.get_by_role("button", name="Voice options", exact=True)
        await expect(mic).to_have_attribute("aria-expanded", "false")
        tray = page.get_by_role("group", name="Voice controls")
        # Closed by default: nothing of the tray is reachable.
        await expect(tray).to_be_hidden()
        await expect(page.get_by_role("checkbox")).to_have_count(0)
        # The toolbar holds one microphone button and Send, nothing else voice-related.
        await expect(page.get_by_role("button", name="Dictate a message")).to_have_count(0)
        await expect(page.get_by_role("button", name="Stop reading aloud")).to_have_count(0)
        await mic.click()
        await expect(mic).to_have_attribute("aria-expanded", "true")
        await expect(tray).to_be_visible()
        voice = tray.get_by_role("region", name="Hands-free voice chat")
        await expect(voice.get_by_role("checkbox")).to_have_count(1)
        await expect(tray.get_by_role("checkbox")).to_have_count(2)  # hands-free and recorded
        await expect(tray.get_by_label("Dictation language")).to_be_visible()
        await expect(voice.get_by_label("Playback language")).to_be_visible()
        await expect(tray.get_by_role("button", name="Dictate a message")).to_be_visible()
        await expect(
            tray.get_by_text("Speech pauses submit your question.", exact=False)
        ).to_be_visible()
        await expect(tray.get_by_text("Maximum 60 seconds / 8 MB.", exact=False)).to_be_visible()
        await mic.click()
        await expect(tray).to_be_hidden()


FOLLOW_UPS = ["Tell me more about…", "Say that in a shorter way", "Give me an example"]
FOLLOW_UP_VALUES = ["Tell me more about ", "Say that in a shorter way", "Give me an example"]


async def test_working_row_shows_the_working_pose_only_while_processing(server: str) -> None:
    api = Synthetic(threads=1)
    api.turns["thread-1"] = [seeded_turn(1), seeded_turn(2, status="processing")]
    async with open_chat(server, api) as page:
        await page.get_by_role("button", name="Conversation 1", exact=True).click(timeout=WAIT)
        row = page.get_by_test_id("chat-working")
        await expect(row).to_be_visible()
        await expect(row).to_contain_text("Working on it")
        avatar = row.locator("[data-agent='assistant']")
        await expect(avatar).to_have_attribute("data-state", "working")
        box = await avatar.bounding_box()
        assert box and round(box["width"]) == 48 and round(box["height"]) == 48, box
        # One row, inside the log, and the status words still say what the server says.
        await expect(page.get_by_test_id("chat-working")).to_have_count(1)
        await expect(
            page.get_by_role("log", name="Chat messages").get_by_test_id("chat-working")
        ).to_be_visible()
        await expect(page.get_by_text("Processing", exact=True)).to_be_visible()
        # No follow-up starters while a turn is being worked on.
        await expect(page.get_by_test_id("chat-followups")).to_have_count(0)
        # The reply lands: the row goes away and the starters appear.
        api.turns["thread-1"][1] = seeded_turn(2)
        await expect(row).to_have_count(0, timeout=10_000)
        await expect(page.get_by_test_id("chat-followups")).to_be_visible()


async def test_queued_turn_shows_no_working_row(server: str) -> None:
    api = Synthetic(threads=3)
    api.turns["thread-1"] = [seeded_turn(1, status="queued")]
    api.turns["thread-2"] = [seeded_turn(1, status="awaiting_delivery")]
    api.turns["thread-3"] = [seeded_turn(1, status="held")]
    labels = {
        1: "Queued - not delivered",
        2: "Reply queued - not available yet",
        3: "Held by tenant policy",
    }
    async with open_chat(server, api) as page:
        for number, label in labels.items():
            await page.get_by_role("button", name=f"Conversation {number}", exact=True).click(
                timeout=WAIT
            )
            await expect(page.get_by_text(label, exact=False)).to_be_visible()
            await page.wait_for_timeout(300)
            await expect(page.get_by_test_id("chat-working")).to_have_count(0)
            await expect(page.get_by_test_id("chat-followups")).to_have_count(0)


async def test_composer_grows_and_shift_enter_adds_a_line(server: str) -> None:
    api = Synthetic()
    async with open_chat(server, api) as page:
        composer = page.get_by_label("Your question", exact=False)
        await composer.fill("One line", timeout=WAIT)
        height = "e => e.getBoundingClientRect().height"
        one = await composer.evaluate(height)
        await composer.press("Shift+Enter")
        await composer.type("two")
        await composer.press("Shift+Enter")
        await composer.type("three")
        await expect(composer).to_have_value("One line\ntwo\nthree")
        three = await composer.evaluate(height)
        line = await composer.evaluate("e => parseFloat(getComputedStyle(e).lineHeight)")
        assert three > one + line, (one, three, line)
        assert api.posts == []
        # Long text stops growing at about eight lines and scrolls instead.
        await composer.fill("\n".join(f"line {n}" for n in range(1, 21)))
        tall = await composer.evaluate(height)
        padding = await composer.evaluate(
            "e => { const c = getComputedStyle(e);"
            " return parseFloat(c.paddingTop) + parseFloat(c.paddingBottom); }"
        )
        assert 7 * line < tall <= 8 * line + padding + 1, (tall, line, padding)
        assert await composer.evaluate("e => e.scrollHeight > e.clientHeight")
        # Shorter text shrinks it back.
        await composer.fill("short")
        assert await composer.evaluate(height) <= one + 1


async def test_jump_to_latest_pill_appears_when_scrolled_up(server: str) -> None:
    api = Synthetic(threads=1)
    api.turns["thread-1"] = numbered_turns(30)
    async with open_chat(server, api) as page:
        await page.get_by_role("button", name="Conversation 1", exact=True).click(timeout=WAIT)
        log = page.get_by_role("log", name="Chat messages")
        await expect(log.get_by_text(re.compile(r"Answer 30$"))).to_be_visible()
        scroller = page.get_by_test_id("sandbox-scroll")
        pill = page.get_by_test_id("chat-jump-latest")
        await expect(pill).to_have_count(0)
        # Less than a screen from the end: no pill.
        await scroller.evaluate("e => { e.scrollTop = e.scrollHeight - e.clientHeight - 200; }")
        await page.wait_for_timeout(300)
        await expect(pill).to_have_count(0)
        # More than a screen from the end: the pill shows and takes the reader to the end.
        await scroller.evaluate("e => { e.scrollTop = 0; }")
        await expect(pill).to_be_visible()
        await expect(pill).to_have_text("Jump to latest")
        await pill.click()
        gap = "e => e.scrollHeight - e.scrollTop - e.clientHeight"
        await expect(pill).to_have_count(0)
        await page.wait_for_timeout(300)
        assert await scroller.evaluate(gap) < 2
        # Focus is not lost to the page when the pill goes away.
        assert await page.evaluate("document.activeElement !== document.body")


CLIPBOARD_STUB = """
window.copied = [];
Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
  writeText: async (text) => { window.copied.push(text); },
} });
"""


async def test_copy_reply_puts_the_text_on_the_clipboard(server: str) -> None:
    api = Synthetic(threads=1)
    api.turns["thread-1"] = [seeded_turn(1)]
    async with open_chat(server, api, init_script=CLIPBOARD_STUB) as page:
        await page.get_by_role("button", name="Conversation 1", exact=True).click(timeout=WAIT)
        copy = page.get_by_role("button", name="Copy reply")
        await expect(copy).to_have_count(1)
        await expect(page.get_by_text("Copied", exact=True)).to_have_count(0)
        await copy.click()
        await expect(page.get_by_text("Copied", exact=True)).to_be_visible()
        # The plain reply text only: no "Assistant:" prefix, no question, no status line.
        assert await page.evaluate("window.copied") == [REPLY]
        # The confirmation goes away by itself after about two seconds.
        await page.wait_for_timeout(1200)
        await expect(page.get_by_text("Copied", exact=True)).to_be_visible()
        await expect(page.get_by_text("Copied", exact=True)).to_have_count(0, timeout=3000)
        # A turn without a reply has nothing to copy.
        api.turns["thread-1"].append(seeded_turn(2, status="held"))
        await expect(page.get_by_text("Seeded question 2")).to_be_visible(timeout=10_000)
        await expect(page.get_by_role("button", name="Copy reply")).to_have_count(1)


async def test_followups_only_prefill_and_never_send(server: str) -> None:
    api = Synthetic(threads=1)
    api.turns["thread-1"] = [seeded_turn(1), seeded_turn(2)]
    async with open_chat(server, api) as page:
        await page.get_by_role("button", name="Conversation 1", exact=True).click(timeout=WAIT)
        starters = page.get_by_test_id("chat-followups")
        await expect(starters).to_be_visible()
        # One set, after the latest reply only.
        await expect(starters).to_have_count(1)
        await expect(starters.get_by_role("button")).to_have_text(FOLLOW_UPS)
        composer = page.get_by_label("Your question", exact=False)
        for label, value in zip(FOLLOW_UPS, FOLLOW_UP_VALUES, strict=True):
            await starters.get_by_role("button", name=label, exact=True).click()
            await expect(composer).to_have_value(value)
            await expect(composer).to_be_focused()
        await page.wait_for_timeout(800)
        assert api.posts == []
        await expect(starters).to_be_visible()
        body = await page.locator("body").inner_text()
        assert "Seeded question 2" in body and body.count(FOLLOW_UPS[2]) == 1


async def test_followups_are_absent_for_read_only_and_for_an_empty_chat(server: str) -> None:
    api = Synthetic(threads=1)
    api.turns["thread-1"] = [seeded_turn(1)]
    async with open_chat(server, api, query="?role=read_only") as page:
        await page.get_by_role("button", name="Conversation 1", exact=True).click(timeout=WAIT)
        await expect(page.get_by_text(REPLY)).to_be_visible()
        await expect(page.get_by_test_id("chat-followups")).to_have_count(0)
        # The reader can still copy what they are allowed to read.
        await expect(page.get_by_role("button", name="Copy reply")).to_be_enabled()
    api = Synthetic()
    async with open_chat(server, api) as page:
        await expect(page.get_by_role("heading", name="How can I help today?")).to_be_visible(
            timeout=WAIT
        )
        await expect(page.get_by_test_id("chat-followups")).to_have_count(0)


async def arrive(page: Page, api: Synthetic) -> None:
    """Open the one seeded conversation, then let a second turn arrive by polling."""
    await page.get_by_role("button", name="Conversation 1", exact=True).click(timeout=WAIT)
    log = page.get_by_role("log", name="Chat messages")
    await expect(log.get_by_text("Seeded question 1")).to_be_visible()
    api.turns["thread-1"].append(seeded_turn(2))
    await expect(log.get_by_text("Seeded question 2")).to_be_visible(timeout=10_000)


async def test_reduced_motion_disables_message_easing(server: str) -> None:
    arrived = "article[data-arrived='true']"
    # Normal motion: the turn that arrived after the first load eases in; the first load does not.
    api = Synthetic(threads=1)
    api.turns["thread-1"] = [seeded_turn(1)]
    async with open_chat(server, api) as page:
        await arrive(page, api)
        await expect(page.locator(arrived)).to_have_count(1)
        await expect(page.locator(arrived)).to_have_class(re.compile(r"\bfs-message-in\b"))
        await expect(page.locator("article.fs-message-in")).to_have_count(1)
    # The reader asks for reduced motion: the same turn just appears.
    api = Synthetic(threads=1)
    api.turns["thread-1"] = [seeded_turn(1)]
    async with open_chat(server, api, reduced_motion=True) as page:
        await arrive(page, api)
        await expect(page.locator(arrived)).to_have_count(1)
        await expect(page.locator("article.fs-message-in")).to_have_count(0)
    # The product's "Reduce animations" preference does the same.
    api = Synthetic(threads=1)
    api.turns["thread-1"] = [seeded_turn(1)]
    pause = 'window.localStorage.setItem("fs-motion-paused", "1");'
    async with open_chat(server, api, init_script=pause) as page:
        await arrive(page, api)
        await expect(page.locator(arrived)).to_have_count(1)
        await expect(page.locator("article.fs-message-in")).to_have_count(0)


async def test_stop_reading_aloud_hands_focus_to_the_microphone(server: str) -> None:
    api = Synthetic(threads=1)
    api.turns["thread-1"] = [seeded_turn(1)]
    async with open_chat(server, api, init_script=SPEECH_STUB) as page:
        await page.get_by_role("button", name="Conversation 1", exact=True).click(timeout=WAIT)
        await page.get_by_role("button", name="Read reply aloud").click()
        stop = page.get_by_role("button", name="Stop reading aloud")
        await expect(stop).to_be_visible()
        await stop.focus()
        await page.keyboard.press("Enter")
        await expect(stop).to_have_count(0)
        await expect(page.get_by_role("button", name="Voice options", exact=True)).to_be_focused()


async def test_voice_tray_moves_focus_in_and_back_and_is_keyboard_reachable(server: str) -> None:
    api = Synthetic(threads=1)
    api.voice_enabled = True
    async with open_chat(server, api) as page:
        await page.get_by_role("button", name="Conversation 1", exact=True).click(timeout=WAIT)
        mic = page.get_by_role("button", name="Voice options", exact=True)
        tray = page.get_by_role("group", name="Voice controls")
        # aria-controls names the tray.
        controls = await mic.get_attribute("aria-controls")
        assert controls
        # (A hidden group is out of the role tree, so find it by its id.)
        assert (
            await page.locator(f'[id="{controls}"]').get_attribute("aria-label") == "Voice controls"
        )
        # Open with the keyboard: focus lands on the first control.
        await mic.focus()
        await page.keyboard.press("Enter")
        await expect(tray).to_be_visible()
        dictate = tray.get_by_role("button", name="Dictate a message")
        await expect(dictate).to_be_focused()
        # The tray's controls are reachable with Tab, in order.
        await page.keyboard.press("Tab")
        await expect(tray.get_by_label("Dictation language")).to_be_focused()
        await page.keyboard.press("Tab")
        await expect(tray.get_by_label("Playback language")).to_be_focused()
        # Close with the keyboard from the microphone: focus stays on it.
        await mic.focus()
        await page.keyboard.press("Enter")
        await expect(tray).to_be_hidden()
        await expect(mic).to_be_focused()
        # Closing with a click from inside the tray hands focus back too.
        await mic.click()
        await expect(dictate).to_be_focused()
        await mic.click()
        await expect(tray).to_be_hidden()
        await expect(mic).to_be_focused()


async def test_read_only_member_keeps_the_honesty_line_and_a_working_microphone(
    server: str,
) -> None:
    api = Synthetic()
    async with open_chat(server, api, query="?role=read_only") as page:
        await expect(page.get_by_test_id("chat-honesty-line")).to_have_text(HONESTY, timeout=WAIT)
        await expect(page.get_by_test_id("chat-honesty-line")).to_have_count(1)
        mic = page.get_by_role("button", name="Voice options", exact=True)
        # The tray itself opens (it explains itself) but nothing in it can start a recording.
        await expect(mic).to_be_enabled()
        await mic.click()
        tray = page.get_by_role("group", name="Voice controls")
        await expect(tray).to_be_visible()
        await expect(tray.get_by_role("button", name="Dictate a message")).to_be_disabled()
        await expect(tray.get_by_label("Dictation language")).to_be_disabled()
        await expect(page.get_by_role("button", name="Send message", exact=True)).to_be_disabled()
        assert api.posts == []


async def test_open_voice_tray_text_has_no_limits_copy(server: str) -> None:
    api = Synthetic(threads=1)
    api.voice_enabled = True
    async with open_chat(server, api) as page:
        await page.get_by_role("button", name="Conversation 1", exact=True).click(timeout=WAIT)
        await page.get_by_role("button", name="Voice options", exact=True).click()
        tray = page.get_by_role("group", name="Voice controls")
        await expect(tray).to_be_visible()
        text = await tray.inner_text()
        assert text.strip()
        for removed in [*REMOVED_TEXT, "It cannot", "cannot confirm business", "attachments"]:
            assert removed not in text, removed
        # The honesty line stays outside the tray, once.
        assert HONESTY not in text
        assert (await page.locator("body").inner_text()).count(HONESTY) == 1


async def test_older_messages_paged_in_do_not_ease_in(server: str) -> None:
    api = Synthetic(threads=1)
    api.turns["thread-1"] = numbered_turns(30)
    api.page_size = 20
    async with open_chat(server, api) as page:
        await page.get_by_role("button", name="Conversation 1", exact=True).click(timeout=WAIT)
        log = page.get_by_role("log", name="Chat messages")
        await expect(log.get_by_text(re.compile(r"Answer 30$"))).to_be_visible()
        await page.get_by_role("button", name="Older messages").click()
        await expect(log.get_by_text(re.compile(r"Question 1$"))).to_be_attached()
        await expect(log.locator("article")).to_have_count(30)
        # History is not an arrival: no turn is marked or eased in.
        await expect(page.locator("article[data-arrived='true']")).to_have_count(0)
        await expect(page.locator("article.fs-message-in")).to_have_count(0)
        # A turn that arrives afterwards still eases in.
        api.turns["thread-1"].append(numbered_turns(31)[-1])
        await expect(log.get_by_text(re.compile(r"Answer 31$"))).to_be_attached(timeout=10_000)
        await expect(page.locator("article[data-arrived='true']")).to_have_count(1)
        await expect(page.locator("article.fs-message-in")).to_have_count(1)


async def test_stop_reading_aloud_falls_back_to_a_reply_button_when_mic_and_text_are_off(
    server: str,
) -> None:
    api = Synthetic(threads=1)
    api.turns["thread-1"] = [seeded_turn(1)]
    async with open_chat(server, api, init_script=SPEECH_STUB) as page:
        await page.get_by_role("button", name="Conversation 1", exact=True).click(timeout=WAIT)
        read = page.get_by_role("button", name="Read reply aloud")
        await read.click()
        stop = page.get_by_role("button", name="Stop reading aloud")
        await expect(stop).to_be_visible()
        # Switch the microphone and the text box off by hand: neither prop changes, so React leaves them.
        await page.evaluate(
            """() => {
              document.querySelector('button[aria-label="Voice options"]').disabled = true;
              document.getElementById("sandbox-text").disabled = true;
            }"""
        )
        await stop.focus()
        await page.keyboard.press("Enter")
        await expect(stop).to_have_count(0)
        await expect(read).to_be_focused()
        # With no reply button either, focus goes to the log's scroller, never to the page body.
        await page.evaluate("document.querySelector('button[aria-label=\"Read reply aloud\"]').disabled = true")
        await page.evaluate("window.speechTest.speaking = true")
        await expect(stop).to_be_visible()
        await stop.focus()
        await page.keyboard.press("Enter")
        await expect(stop).to_have_count(0)
        await expect(page.get_by_test_id("sandbox-scroll")).to_be_focused()


CLIPBOARD_REJECTS = """
Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
  writeText: async () => { throw new Error("denied"); },
} });
"""
CLIPBOARD_MISSING = """
Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
"""


@pytest.mark.parametrize("stub", [CLIPBOARD_REJECTS, CLIPBOARD_MISSING], ids=["rejects", "missing"])
async def test_copy_reply_says_could_not_copy_when_the_browser_refuses(
    server: str, stub: str
) -> None:
    api = Synthetic(threads=1)
    api.turns["thread-1"] = [seeded_turn(1)]
    async with open_chat(server, api, init_script=stub) as page:
        await page.get_by_role("button", name="Conversation 1", exact=True).click(timeout=WAIT)
        await page.get_by_role("button", name="Copy reply").click()
        failed = page.get_by_text("Could not copy", exact=True)
        await expect(failed).to_be_visible()
        await expect(page.get_by_text("Copied", exact=True)).to_have_count(0)
        # The words go away by themselves, and the button still works afterwards.
        await expect(failed).to_have_count(0, timeout=4000)
        await expect(page.get_by_role("button", name="Copy reply")).to_be_enabled()
