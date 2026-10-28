"""Real browser checks for the redesigned three-pane customer inbox.

Same stubbed-upstream harness as the services and WhatsApp browser tests: the real Next.js
shell runs from a disposable staged copy, server-side layout fetches hit a loopback stub,
and every browser-side `/console/v1/*` call is answered by Playwright from synthetic rows.
One shell serves the whole module; each test opens its own browser.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from playwright.async_api import Page, async_playwright, expect

from tests.test_services_browser import (
    ROOT,
    WAIT,
    WEB,
    Upstream,
    free_port,
    make_me,
    make_service,
)

pytestmark = pytest.mark.asyncio

NAMED = "a0000000-0000-4000-8000-000000000001"
UNNAMED = "a0000000-0000-4000-8000-000000000002"
NEWCOMER = "a0000000-0000-4000-8000-000000000003"
LONG = "a0000000-0000-4000-8000-000000000004"
LONG_NAME = ("Wolfeschlegelsteinhausenbergerdorff" * 2)[:60]
FULL_PHONE = "+60125550148"
MASKED_PHONE = "••••0148"
WIDTHS = (320, 390, 768, 1024, 1440)


def ago(minutes: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()


def conversation(
    identifier: str,
    channel: str,
    *,
    name: str | None,
    username: str | None = None,
    masked: str | None = None,
    known: bool = False,
    preview: str,
    minutes: int,
    pending: int = 0,
    human_muted: bool = False,
) -> dict[str, Any]:
    return {
        "id": identifier,
        "channel": channel,
        "status": "open",
        "owned_by": "human" if human_muted else None,
        "ai_muted_until": None,
        "human_muted": human_muted,
        "inbox_version": 1,
        "opened_at": ago(minutes + 60),
        "contact": {
            "display_name": name,
            "telegram_username": username,
            "phone_masked": masked,
            "phone": None,
            "kind": "customer",
            "known": known,
        },
        "last_message": {"preview": preview, "at": ago(minutes)},
        "pending_approvals": pending,
    }


def message(
    identifier: str, direction: str, author: str, body: str, minutes: int
) -> dict[str, Any]:
    return {
        "id": identifier,
        "direction": direction,
        "author": author,
        "body": body,
        "created_at": ago(minutes),
        "trace_id": None,
        "delivery_status": "delivered" if direction == "outbound" else None,
        "delivery_error": None,
        "provider_status": None,
        "provider_error": None,
    }


@dataclass
class Inbox:
    """The synthetic inbox API one test sees; tests change it while the page polls."""

    items: list[dict[str, Any]] = field(
        default_factory=lambda: [
            conversation(
                NAMED,
                "telegram",
                name="Aisyah Rahman",
                username="aisyah_r",
                known=True,
                preview="Is my order on the way?",
                minutes=2,
                pending=1,
            ),
            # WhatsApp stored the number as the name, so the API sends no display name.
            conversation(
                UNNAMED,
                "whatsapp",
                name=None,
                masked=MASKED_PHONE,
                preview="Hello, do you deliver to Ipoh?",
                minutes=5,
            ),
            conversation(
                NEWCOMER,
                "telegram",
                name="Ben Tan",
                preview="First time asking here.",
                minutes=9,
                pending=1,
                human_muted=True,
            ),
            conversation(
                LONG,
                "whatsapp",
                name=LONG_NAME,
                masked="••••7781",
                known=True,
                preview="A long name should never break the row.",
                minutes=30,
            ),
        ]
    )
    needs_review_count: int = 2
    fail_lists: bool = False
    list_queries: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    messages: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def thread(self, identifier: str) -> list[dict[str, Any]]:
        """Newest first, as the API pages them."""
        if identifier not in self.messages:
            self.messages[identifier] = [
                message(f"{identifier[:-3]}m02", "outbound", "ai", "Let me check that.", 20),
                message(f"{identifier[:-3]}m01", "inbound", "customer", "Hi there.", 21),
            ]
        return self.messages[identifier]

    def detail(self, identifier: str) -> dict[str, Any] | None:
        row = next((item for item in self.items if item["id"] == identifier), None)
        if row is None:
            return None
        # Only the single-conversation read carries the full number.
        phone = FULL_PHONE if identifier == UNNAMED else None
        return {**row, "contact": {**row["contact"], "phone": phone}}

    async def route(self, request_route: Any) -> None:
        request = request_route.request
        path, _, query = request.url.split("/console/v1/")[1].partition("?")
        status, data = 200, None
        if path == "auth/csrf":
            data = {"csrf_token": "synthetic-csrf"}
        elif path == "services" and request.method == "GET":
            data = [make_service("customer_support", "Customer Support", "base", "active")]
        elif path == "inbox/conversations":
            self.list_queries.append(query)
            params = parse_qs(query)
            items = self.items
            if params.get("needs_review") == ["true"]:
                items = [item for item in items if item["pending_approvals"] > 0]
            if "channel" in params:
                items = [item for item in items if item["channel"] in params["channel"]]
            if self.fail_lists:
                status, data = 500, {"detail": "synthetic outage"}
            else:
                data = {
                    "items": items,
                    "next_cursor": None,
                    "needs_review_count": self.needs_review_count,
                }
        elif path.startswith("inbox/conversations/") and path.endswith("/messages"):
            data = {"items": self.thread(path.split("/")[2]), "next_cursor": None}
        elif path.startswith("inbox/conversations/"):
            data = self.detail(path.split("/")[2])
            if data is None:
                status, data = 404, {"detail": "conversation not found"}
        elif path == "inbox/approvals":
            data = {"items": [], "next_cursor": None}
        else:
            self.unexpected.append(f"{request.method} {path}?{query}")
            status, data = 500, {"detail": "unexpected synthetic call"}
        await request_route.fulfill(status=status, json=data)


@dataclass
class Shell:
    base: str
    upstream: Upstream
    logs: Path


@pytest.fixture(scope="module")
def shell(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Shell]:
    """One staged Next.js shell for the module; each `next dev` start costs a minute."""
    node = shutil.which("node")
    assert node, "Node is required for browser inbox acceptance"
    assert (WEB / "node_modules/next/dist/bin/next").is_file(), (
        "Console dependencies are missing; run `npm ci --ignore-scripts` in "
        "CSA_WebConsole/csa-console/web before browser inbox acceptance"
    )
    tmp_path = tmp_path_factory.mktemp("inbox-shell")
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
        base = f"http://127.0.0.1:{port}"
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
            upstream.me = make_me("tenant_admin")
            upstream.packages = [{"key": "customer_support", "status": "active"}]
            session = {"csa_session": "synthetic-session"}
            with httpx.Client(trust_env=False, cookies=session) as http:
                for _ in range(120):
                    assert process.poll() is None, log_path.read_text()
                    try:
                        if http.get(f"{base}/login", timeout=2).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.5)
                else:
                    pytest.fail(f"Synthetic inbox frontend did not start; log: {log_path}")
                # Compile the inbox route once, so the first test does not pay for it.
                http.get(f"{base}/portal/inbox", timeout=240)
            yield Shell(base=base, upstream=upstream, logs=tmp_path)
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.close()


@contextlib.asynccontextmanager
async def inbox_page(
    shell: Shell,
    state: Inbox,
    *,
    width: int = 1440,
    role: str = "tenant_admin",
    reduced_motion: bool = False,
) -> AsyncIterator[Page]:
    """A logged-in page on the inbox, answered by `state`; checks for stray calls on exit."""
    shell.upstream.me = make_me(role)
    shell.upstream.packages = [{"key": "customer_support", "status": "active"}]
    shell.upstream.unexpected.clear()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        context = await browser.new_context(
            viewport={"width": width, "height": 844 if width < 768 else 900},
            reduced_motion="reduce" if reduced_motion else "no-preference",
        )
        context.set_default_navigation_timeout(WAIT)
        await context.add_cookies(
            [{"name": "csa_session", "value": "synthetic-session", "url": shell.base}]
        )
        page = await context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            await page.route("**/console/v1/**", state.route)
            await page.goto(f"{shell.base}/portal/inbox")
            yield page
            assert not errors, errors
            assert not state.unexpected, state.unexpected
            assert not shell.upstream.unexpected, shell.upstream.unexpected
        except Exception as exc:
            exc.add_note(f"Page errors: {errors}; frontend log: {shell.logs / 'frontend.log'}")
            raise
        finally:
            await context.close()
            await browser.close()


def row(page: Page, identifier: str) -> Any:
    return page.locator(f'[data-conversation-id="{identifier}"]')


async def rows_loaded(page: Page, count: int = 4) -> None:
    await expect(page.locator("[data-conversation-id]")).to_have_count(count, timeout=WAIT)


async def open_thread(page: Page, identifier: str, text: str = "Let me check that.") -> None:
    await row(page, identifier).click()
    await expect(page.get_by_test_id("inbox-thread").get_by_text(text, exact=True)).to_be_visible(
        timeout=WAIT
    )


async def selected_id(page: Page) -> str | None:
    return await page.locator('[data-conversation-id][aria-current="true"]').get_attribute(
        "data-conversation-id"
    )


async def eventually(check: Any, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "condition not met in time"
        await asyncio.sleep(0.1)


async def page_width(page: Page) -> dict[str, int]:
    return await page.evaluate(
        "() => ({scroll: document.documentElement.scrollWidth, inner: window.innerWidth})"
    )


async def test_no_page_overflow_at_all_widths(shell: Shell) -> None:
    state = Inbox()
    async with inbox_page(shell, state, width=WIDTHS[0]) as page:
        for width in WIDTHS:
            await page.set_viewport_size({"width": width, "height": 900})
            await page.goto(f"{shell.base}/portal/inbox")
            await rows_loaded(page)
            measured = await page_width(page)
            assert measured["scroll"] <= measured["inner"], ("list", width, measured)
            await open_thread(page, LONG)
            measured = await page_width(page)
            assert measured["scroll"] <= measured["inner"], ("thread", width, measured)


async def test_long_name_does_not_break_the_row(shell: Shell) -> None:
    assert len(LONG_NAME) == 60 and " " not in LONG_NAME
    state = Inbox()
    for width in (320, 1440):
        async with inbox_page(shell, state, width=width) as page:
            await rows_loaded(page)
            button = row(page, LONG)
            title = button.locator("span.truncate").first
            await expect(title).to_have_text(LONG_NAME)
            box = await button.bounding_box()
            container = await page.get_by_role("list", name="Conversations").bounding_box()
            assert box and container
            assert box["x"] >= container["x"] - 0.5, (width, box, container)
            assert box["x"] + box["width"] <= container["x"] + container["width"] + 0.5, (
                width,
                box,
                container,
            )
            clipped = await title.evaluate(
                """el => ({
                    ellipsis: getComputedStyle(el).textOverflow,
                    cut: el.scrollWidth > el.clientWidth,
                })"""
            )
            assert clipped == {"ellipsis": "ellipsis", "cut": True}, (width, clipped)


async def test_needs_review_count_stays_visible_and_filters_on_the_server(shell: Shell) -> None:
    state = Inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page)
        count = page.get_by_test_id("inbox-needs-review-count")
        await expect(count).to_have_text("2")
        await expect(count).to_be_visible()
        assert not any("needs_review" in query for query in state.list_queries)
        chip = page.get_by_test_id("inbox-filter-needs-review")
        await chip.click()
        await expect(chip).to_have_attribute("aria-pressed", "true")
        await rows_loaded(page, 2)
        assert any("needs_review=true" in query for query in state.list_queries)
        await expect(row(page, NAMED)).to_have_count(1)
        await expect(row(page, NEWCOMER)).to_have_count(1)
        await expect(count).to_be_visible()
        await expect(count).to_have_text("2")


async def test_search_is_sent_to_the_server_and_ignores_one_character(shell: Shell) -> None:
    state = Inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page)
        search = page.get_by_test_id("inbox-search")
        await search.fill("A")
        await expect(page.get_by_text("Type at least two characters to search.")).to_be_visible()
        # Past the 300 ms debounce and through at least one 3-second poll.
        await asyncio.sleep(3.5)
        assert not any("q=" in query for query in state.list_queries), state.list_queries
        await search.fill("Ai")
        await eventually(lambda: any("q=Ai" in query for query in state.list_queries))
        assert all(parse_qs(query).get("q", ["Ai"]) == ["Ai"] for query in state.list_queries), (
            state.list_queries
        )


async def test_failed_refresh_keeps_rows(shell: Shell) -> None:
    state = Inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page)
        calls = len(state.list_queries)
        state.fail_lists = True
        await expect(page.get_by_test_id("inbox-list-state-unavailable")).to_be_visible(
            timeout=WAIT
        )
        assert len(state.list_queries) > calls
        await expect(page.locator("[data-conversation-id]")).to_have_count(4)
        await expect(page.get_by_test_id("inbox-list-state-empty")).to_have_count(0)
        await expect(row(page, NAMED)).to_contain_text("Aisyah Rahman")


async def test_unnamed_whatsapp_sender_shows_masked_number_in_list_and_full_in_card(
    shell: Shell,
) -> None:
    state = Inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page)
        listing = page.get_by_role("list", name="Conversations")
        await expect(row(page, UNNAMED).locator("span.truncate").first).to_have_text(MASKED_PHONE)
        assert "0125550148" not in await listing.inner_text()
        await open_thread(page, UNNAMED)
        card = page.get_by_test_id("inbox-customer-card")
        await expect(card).to_contain_text(FULL_PHONE, timeout=WAIT)
        assert "0125550148" not in await listing.inner_text()


async def test_j_k_move_selection_but_not_inside_inputs(shell: Shell) -> None:
    state = Inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page)
        await open_thread(page, NAMED)
        assert await selected_id(page) == NAMED
        # Outside any field, j moves to the next row and k back.
        await page.keyboard.press("j")
        await expect(row(page, UNNAMED)).to_have_attribute("aria-current", "true")
        await page.keyboard.press("k")
        await expect(row(page, NAMED)).to_have_attribute("aria-current", "true")

        # Staff has taken over this one, so the human-reply composer is on screen.
        await open_thread(page, NEWCOMER)
        await expect(page.get_by_label("Human reply", exact=True)).to_be_visible(timeout=WAIT)
        # Held by tag: the wrapping label's text grows with the textarea's contents.
        reply = page.get_by_test_id("inbox-thread").locator("textarea")
        await reply.press_sequentially("jk")
        await expect(reply).to_have_value("jk")
        await asyncio.sleep(0.3)
        assert await selected_id(page) == NEWCOMER

        search = page.get_by_test_id("inbox-search")
        await search.press_sequentially("jk")
        await expect(search).to_have_value("jk")
        await asyncio.sleep(0.3)
        assert await selected_id(page) == NEWCOMER


async def test_slide_over_opens_closes_with_escape_and_returns_focus(shell: Shell) -> None:
    state = Inbox()
    async with inbox_page(shell, state, width=390) as page:
        await rows_loaded(page)
        await open_thread(page, UNNAMED)
        details = page.get_by_test_id("inbox-details-button")
        await details.click()
        dialog = page.get_by_role("dialog", name="Customer details")
        await expect(dialog).to_be_visible()
        await expect(dialog.get_by_test_id("inbox-customer-card")).to_contain_text(
            FULL_PHONE, timeout=WAIT
        )
        await page.keyboard.press("Escape")
        await expect(dialog).to_be_hidden()
        await expect(details).to_be_focused()
        # Escape closed only the panel; the thread is still open.
        await expect(page.get_by_test_id("inbox-thread")).to_be_visible()


async def test_empty_state_connect_button_only_for_admins(shell: Shell) -> None:
    ask = "Ask your administrator to connect WhatsApp."
    async with inbox_page(shell, Inbox(items=[], needs_review_count=0)) as page:
        empty = page.get_by_test_id("inbox-list-state-empty")
        await expect(empty).to_be_visible(timeout=WAIT)
        await expect(empty.get_by_role("heading", name="No conversations yet")).to_be_visible()
        connect = page.get_by_test_id("inbox-connect-whatsapp")
        await expect(connect).to_be_visible()
        await expect(connect).to_have_attribute("href", "/portal/services")
        await expect(page.get_by_text(ask, exact=True)).to_have_count(0)
    async with inbox_page(
        shell, Inbox(items=[], needs_review_count=0), role="tenant_staff"
    ) as page:
        empty = page.get_by_test_id("inbox-list-state-empty")
        await expect(empty).to_be_visible(timeout=WAIT)
        await expect(empty.get_by_text(ask, exact=True)).to_be_visible()
        await expect(page.get_by_test_id("inbox-connect-whatsapp")).to_have_count(0)


async def arrived_animation(page: Page, state: Inbox) -> str:
    """Open a thread, let one new message arrive, and read the bubble's animation name."""
    await rows_loaded(page)
    await open_thread(page, NAMED)
    state.thread(NAMED).insert(
        0, message(f"{NAMED[:-3]}m03", "inbound", "customer", "Just checking in again.", 0)
    )
    bubble = page.get_by_test_id("inbox-thread").locator("li", has_text="Just checking in again.")
    await expect(bubble).to_be_visible(timeout=WAIT)
    return await bubble.evaluate("el => getComputedStyle(el).animationName")


async def test_reduced_motion_removes_animations(shell: Shell) -> None:
    # Control: with motion allowed the same arrival animates, so "none" below means something.
    state = Inbox()
    async with inbox_page(shell, state) as page:
        assert await arrived_animation(page, state) == "fs-message-in"
    state = Inbox()
    async with inbox_page(shell, state, reduced_motion=True) as page:
        assert await page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
        assert await arrived_animation(page, state) == "none"


async def test_agent_avatar_loads_and_the_proxy_still_denies_by_default(shell: Shell) -> None:
    state = Inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page)
        await open_thread(page, NAMED)
        avatar = "[data-testid='inbox-thread'] img[src*='/fourstacks/agents/support.svg']"
        await page.wait_for_function(
            f'() => {{ const img = document.querySelector("{avatar}");'
            " return !!img && img.complete && img.naturalWidth > 0; }",
            timeout=WAIT,
        )
    async with httpx.AsyncClient(base_url=shell.base, trust_env=False, timeout=60) as http:
        artwork = await http.get("/fourstacks/agents/support.svg")
        assert artwork.status_code == 200
        assert "svg" in artwork.headers["content-type"]
        # Everything else is unchanged: unlisted paths are 404, adopted pages need a session.
        assert (await http.get("/definitely-not-a-page")).status_code == 404
        assert (await http.get("/fourstacks-but-not-artwork")).status_code == 404
        gated = await http.get("/portal/inbox")
        assert gated.status_code in {302, 303, 307, 308}, gated.status_code
        assert gated.headers["location"].endswith("/login")
