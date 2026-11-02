"""Real browser acceptance for the plain Assistant activity screen.

The real Next.js shell runs unmodified from one disposable staged copy for the whole file (see
`site`). Its layouts fetch `/auth/me` and `/services/active` server-side, so the loopback stub
from `test_services_browser` answers those; every browser-side `/console/v1/*` call is answered
by Playwright from `Stub`, which applies the same filters, keyset paging and `limit + 1` rule as
the real runs read and records the query string of every request.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import httpx
import pytest
from playwright.async_api import Locator, Page, async_playwright, expect

from tests.test_services_browser import (
    WAIT,
    Upstream,
    free_port,
    make_me,
)

pytestmark = pytest.mark.asyncio

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "CSA_WebConsole/csa-console/web"
THEMES = ("cobalt", "night", "bureau")
WIDTHS = (320, 390, 768, 1024, 1440)
ZONE = "Asia/Kuala_Lumpur"
# id: (capability, ended in error). Ids run 1..12 and the newest is 12.
DEFAULT_RUNS: dict[int, tuple[str, bool]] = {
    12: ("invoice_agent", False),
    11: ("kb_agent", False),
    10: ("escalation_agent", True),
    9: ("ticket_agent", False),
    8: ("order_agent", False),
    7: ("mystery_agent", False),
    6: ("ecommerce_agent", False),
    5: ("subscription_agent", False),
    4: ("order_approval", True),
    3: ("booking_agent", False),
    2: ("kb_agent", True),
    1: ("invoice_agent", False),
}


def make_run(run_id: int, agent: str, error: bool, created: datetime) -> dict[str, Any]:
    return {
        "id": run_id,
        "trace_id": f"00000000-0000-4000-8000-{run_id:012d}",
        "conversation_id": None,
        "agent": agent,
        "ok": not error,
        "status": "error" if error else "ok",
        "error_code": "timeout" if error else None,
        "error_category": "upstream" if error else None,
        "latency_ms": 17 + run_id,
        "created_at": created.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }


def default_runs() -> list[dict[str, Any]]:
    """Twelve runs over the last twelve minutes, the newest id the newest time."""
    now = datetime.now(UTC)
    return [
        make_run(run_id, agent, error, now - timedelta(minutes=13 - run_id))
        for run_id, (agent, error) in sorted(DEFAULT_RUNS.items())
    ]


def kl_instant(year: int, month: int, day: int) -> str:
    """Start of a Kuala Lumpur calendar day as the UTC instant the browser must send."""
    start = datetime(year, month, day, tzinfo=ZoneInfo(ZONE))
    return start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


@dataclass
class Stub:
    """Browser-side answers; `runs` can be changed by a test between polls."""

    runs: list[dict[str, Any]] = field(default_factory=default_runs)
    events: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    fail_runs: bool = False
    fail_overview: bool = False
    list_requests: list[dict[str, str]] = field(default_factory=list)
    strip_requests: list[dict[str, str]] = field(default_factory=list)
    event_requests: list[dict[str, str]] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)

    def last_list(self) -> dict[str, str]:
        return self.list_requests[-1]

    def filtered(self, query: dict[str, str]) -> list[dict[str, Any]]:
        rows = sorted(self.runs, key=lambda run: run["id"], reverse=True)
        if "before_id" in query:
            rows = [run for run in rows if run["id"] < int(query["before_id"])]
        if "agent" in query:
            rows = [run for run in rows if run["agent"] == query["agent"]]
        if "status" in query:
            rows = [run for run in rows if run["status"] == query["status"]]
        if "from" in query:
            rows = [run for run in rows if run["created_at"] >= query["from"]]
        if "to" in query:
            rows = [run for run in rows if run["created_at"] < query["to"]]
        return rows

    async def route(self, request_route: Any) -> None:
        request = request_route.request
        url = urlsplit(request.url)
        path = url.path.split("/console/v1/")[1]
        query = {
            key: values[0] for key, values in parse_qs(url.query, keep_blank_values=True).items()
        }
        status, data = 200, None
        if path == "runs" and request.method == "GET":
            if self.fail_runs:
                status, data = 503, {"detail": "synthetic transient failure"}
            else:
                limit = int(query["limit"])
                (self.strip_requests if limit == 100 else self.list_requests).append(query)
                rows = self.filtered(query)
                items = rows[:limit]
                more = len(rows) > limit
                data = {"items": items, "next_before_id": items[-1]["id"] if more else None}
        elif path == "overview" and request.method == "GET":
            if self.fail_overview:
                status, data = 503, {"detail": "synthetic overview outage"}
            else:
                failed = sum(1 for run in self.runs if run["status"] == "error")
                data = {
                    "runs": len(self.runs),
                    "succeeded": len(self.runs) - failed,
                    "failed": failed,
                    "last_run_at": None,
                    "window_hours": 24,
                }
        elif re.fullmatch(r"runs/\d+", path) and request.method == "GET":
            found = [run for run in self.runs if run["id"] == int(path.split("/")[1])]
            status, data = (200, found[0]) if found else (404, {"detail": "run_not_found"})
        elif path == "events" and request.method == "GET":
            self.event_requests.append(query)
            after = int(query.get("after_id", "0"))
            data = [e for e in self.events.get(query["trace_id"], []) if e["id"] > after]
        else:
            self.unexpected.append(f"{request.method} {path}")
            status, data = 500, {"detail": "unexpected synthetic call"}
        await request_route.fulfill(status=status, json=data)


def event(event_id: int, node: str, agent: str | None, duration: int) -> dict[str, Any]:
    return {
        "id": event_id,
        "node": node,
        "agent": agent,
        "status": "ok",
        "duration_ms": duration,
        "created_at": "2026-11-02T10:00:00.000Z",
    }


@dataclass
class Site:
    base: str
    upstream: Upstream
    log_path: Path


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Site]:
    node = shutil.which("node")
    assert node, "Node is required for browser activity acceptance"
    assert (WEB / "node_modules/next/dist/bin/next").is_file(), (
        "Console dependencies are missing; run `npm ci --ignore-scripts` in "
        "CSA_WebConsole/csa-console/web before browser activity acceptance"
    )
    tmp_path = tmp_path_factory.mktemp("activity")
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
                    pytest.fail(f"Synthetic activity frontend did not start; log: {log_path}")
            yield Site(f"http://127.0.0.1:{port}", upstream, log_path)
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.close()


async def with_browser(body: Any) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            await body(browser)
        finally:
            await browser.close()


async def open_activity(
    browser: Any,
    site: Site,
    stub: Stub,
    *,
    width: int = 1280,
    height: int = 1000,
    theme: str | None = None,
    wait_for_rows: bool = True,
    **options: Any,
) -> tuple[Any, Page, list[str]]:
    site.upstream.me = make_me("tenant_admin")
    site.upstream.packages = [{"key": "customer_support", "status": "active"}]
    options.setdefault("timezone_id", ZONE)
    context = await browser.new_context(viewport={"width": width, "height": height}, **options)
    context.set_default_navigation_timeout(WAIT)
    cookies = [{"name": "csa_session", "value": "synthetic-session", "url": site.base}]
    if theme:
        cookies.append({"name": "fs-theme", "value": theme, "url": site.base})
    await context.add_cookies(cookies)
    page = await context.new_page()
    diagnostics: list[str] = []
    page.on("pageerror", lambda error: diagnostics.append(str(error)))
    await page.route("**/console/v1/**", stub.route)
    await page.goto(f"{site.base}/portal/activity")
    if wait_for_rows:
        await expect(run_row(page, 12)).to_be_visible(timeout=WAIT)
    else:
        await expect(page.get_by_test_id("activity-last-assistants")).to_be_visible(timeout=WAIT)
    return context, page, diagnostics


def run_row(page: Page, run_id: int) -> Locator:
    return page.get_by_test_id(f"activity-run-{run_id}")


def row_ids(page: Page) -> Any:
    return page.evaluate(
        """() => [...document.querySelectorAll('[data-testid^="activity-run-"]')]
            .map(el => el.dataset.testid)
            .filter(id => /^activity-run-\\d+$/.test(id))
            .map(id => Number(id.split('-')[2]))"""
    )


async def wait_requests(stub: Stub, count: int, *, timeout: float = 12) -> None:
    """Wait until the list has been requested `count` times in total."""
    deadline = time.monotonic() + timeout
    while len(stub.list_requests) < count:
        assert time.monotonic() < deadline, f"only {len(stub.list_requests)} list requests"
        await asyncio.sleep(0.1)


async def frames(avatar: Locator) -> tuple[int, str]:
    """Rendered sprite frames and the animation name of the first, read in one step."""
    count, name = await avatar.evaluate(
        """el => {
            const list = el.querySelectorAll('svg g.fs-px-frame');
            return [list.length, getComputedStyle(list[0]).animationName];
        }"""
    )
    return count, name


async def test_five_rows_per_page_with_newer_and_older(site: Site) -> None:
    stub = Stub()

    async def body(browser: Any) -> None:
        context, page, diagnostics = await open_activity(browser, site, stub)
        assert await row_ids(page) == [12, 11, 10, 9, 8]
        assert stub.list_requests[0] == {"limit": "5"}
        assert any(request == {"limit": "100"} for request in stub.strip_requests)
        newer, older = page.get_by_test_id("activity-newer"), page.get_by_test_id("activity-older")
        await expect(newer).to_be_disabled()
        await expect(older).to_be_enabled()

        await older.click()
        await expect(run_row(page, 7)).to_be_visible()
        assert await row_ids(page) == [7, 6, 5, 4, 3]
        assert stub.last_list() == {"limit": "5", "before_id": "8"}
        await expect(newer).to_be_enabled()

        await older.click()
        await expect(run_row(page, 2)).to_be_visible()
        assert await row_ids(page) == [2, 1]
        assert stub.last_list() == {"limit": "5", "before_id": "3"}
        await expect(older).to_be_disabled()

        await newer.click()
        await expect(run_row(page, 7)).to_be_visible()
        assert await row_ids(page) == [7, 6, 5, 4, 3]
        assert stub.last_list() == {"limit": "5", "before_id": "8"}
        await newer.click()
        await expect(run_row(page, 12)).to_be_visible()
        assert stub.last_list() == {"limit": "5"}
        await expect(newer).to_be_disabled()
        assert not diagnostics, diagnostics
        await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected


async def test_arrow_opens_details_with_original_text_and_survives_refresh(site: Site) -> None:
    stub = Stub()
    trace = "00000000-0000-4000-8000-000000000012"
    stub.events[trace] = [event(101, "browser_seed_node", "invoice_agent", 23)]

    async def body(browser: Any) -> None:
        context, page, diagnostics = await open_activity(browser, site, stub)
        row = run_row(page, 12)
        toggle = page.get_by_test_id("activity-run-12-toggle")
        details = page.get_by_test_id("activity-run-12-details")
        # Plain words first: the technical text is hidden and nothing about it is requested.
        await expect(row).to_contain_text(
            re.compile(r"Invoice lookup agent finished \d+ minutes? ago")
        )
        await expect(row.get_by_test_id("activity-run-12-outcome")).to_have_text("Worked")
        await expect(toggle).to_have_attribute("aria-expanded", "false")
        await expect(
            page.get_by_text("#12 invoice_agent / ok / Succeeded", exact=True)
        ).to_be_hidden()
        await expect(page.get_by_role("heading", name="Run #12", exact=True)).to_have_count(0)
        assert stub.event_requests == []

        await toggle.click()
        await expect(toggle).to_have_attribute("aria-expanded", "true")
        await expect(page.get_by_role("heading", name="Run #12", exact=True)).to_be_visible()
        await expect(
            details.get_by_text("#12 invoice_agent / ok / Succeeded", exact=True)
        ).to_be_visible()
        await expect(details).to_contain_text("29 ms")
        await expect(details).to_contain_text(trace)
        await expect(
            details.get_by_role("heading", name="Trace events", exact=True)
        ).to_be_visible()
        await expect(
            details.get_by_text("#101 browser_seed_node / invoice_agent / ok / 23 ms", exact=True)
        ).to_be_visible()
        assert stub.event_requests[0] == {"trace_id": trace, "after_id": "0"}

        # An error run keeps its raw code, as it always did.
        await page.get_by_test_id("activity-run-10-toggle").click()
        await expect(
            page.get_by_text("#10 escalation_agent / error / timeout", exact=True)
        ).to_be_visible()

        # The 5-second refresh brings a newer run in; the opened rows stay open and the shown
        # detail keeps polling for new events.
        before = len(stub.list_requests)
        stub.runs.append(make_run(13, "kb_agent", False, datetime.now(UTC)))
        stub.events[trace].append(event(102, "browser_poll_followup", None, 31))
        await wait_requests(stub, before + 1)
        await expect(run_row(page, 13)).to_be_visible()
        assert await row_ids(page) == [13, 12, 11, 10, 9]
        await expect(page.get_by_test_id("activity-run-12-toggle")).to_have_attribute(
            "aria-expanded", "true"
        )
        await expect(page.get_by_test_id("activity-run-10-toggle")).to_have_attribute(
            "aria-expanded", "true"
        )
        await expect(
            details.get_by_text("#102 browser_poll_followup / Coordinator / ok / 31 ms", exact=True)
        ).to_be_visible(timeout=12_000)

        await toggle.click()
        await expect(toggle).to_have_attribute("aria-expanded", "false")
        assert not diagnostics, diagnostics
        await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected


async def test_date_range_sends_half_open_instants_and_resets_to_newest(site: Site) -> None:
    stub = Stub()

    async def body(browser: Any) -> None:
        # A short window: the calendar cannot fit above or below the filter and must scroll.
        context, page, diagnostics = await open_activity(browser, site, stub, height=640)
        await page.get_by_test_id("activity-older").click()
        await expect(run_row(page, 7)).to_be_visible()
        assert stub.last_list()["before_id"] == "8"

        today = datetime.now(ZoneInfo(ZONE))
        year, month = today.year, today.month
        await page.get_by_test_id("activity-date").click()
        await page.get_by_test_id(f"activity-date-day-{year}-{month:02d}-01").click()
        await page.get_by_test_id(f"activity-date-day-{year}-{month:02d}-03").click()
        await page.keyboard.press("Escape")
        # The 1st to the 3rd: from the start of the 1st, to the start of the 4th (exclusive),
        # both in the reader's zone, as instants. The page went back to the newest.
        await expect(page.get_by_test_id("activity-newer")).to_be_disabled()
        last = stub.last_list()
        assert "before_id" not in last
        assert last["from"] == kl_instant(year, month, 1)
        assert last["to"] == kl_instant(year, month, 4)
        assert last["from"].endswith("Z") and last["to"].endswith("Z")
        # Today's runs fall outside the 1st to the 3rd unless today is that early.
        if today.day > 3:
            await expect(
                page.get_by_text("Nothing has been recorded for this selection.")
            ).to_be_visible()
            assert await row_ids(page) == []

        await page.get_by_test_id("activity-date").click()
        popover = await page.get_by_test_id("activity-date-popover").bounding_box()
        assert popover is not None and popover["y"] >= 0, popover
        assert popover["y"] + popover["height"] <= 640 + 0.5, popover
        await page.get_by_test_id("activity-date-preset-clear").click()
        await page.keyboard.press("Escape")
        await expect(run_row(page, 12)).to_be_visible()
        assert "from" not in stub.last_list() and "to" not in stub.last_list()
        assert not diagnostics, diagnostics
        await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected


async def test_status_chip_sends_status(site: Site) -> None:
    stub = Stub()

    async def body(browser: Any) -> None:
        context, page, diagnostics = await open_activity(browser, site, stub)
        chips = page.get_by_test_id("activity-status")
        await expect(chips.get_by_test_id("activity-status-all")).to_have_attribute(
            "aria-pressed", "true"
        )
        await expect(chips.get_by_test_id("activity-status-ok")).to_have_text("Worked")
        await expect(chips.get_by_test_id("activity-status-error")).to_have_text("Needed attention")
        await page.get_by_test_id("activity-older").click()
        await expect(run_row(page, 7)).to_be_visible()

        await chips.get_by_test_id("activity-status-error").click()
        await expect(run_row(page, 10)).to_be_visible()
        assert stub.last_list() == {"limit": "5", "status": "error"}
        assert await row_ids(page) == [10, 4, 2]
        await expect(page.get_by_test_id("activity-run-10-outcome")).to_have_text(
            "Needed attention"
        )

        await chips.get_by_test_id("activity-status-ok").click()
        await expect(run_row(page, 12)).to_be_visible()
        assert stub.last_list() == {"limit": "5", "status": "ok"}
        assert await row_ids(page) == [12, 11, 9, 8, 7]

        await chips.get_by_test_id("activity-status-all").click()
        await expect(run_row(page, 10)).to_be_visible()
        # All sends no status at all, never an empty one (the server answers that with a 422).
        assert stub.last_list() == {"limit": "5"}
        assert all(request.get("status") != "" for request in stub.list_requests)
        assert not diagnostics, diagnostics
        await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected


async def test_assistant_select_sends_agent(site: Site) -> None:
    stub = Stub()

    async def body(browser: Any) -> None:
        context, page, diagnostics = await open_activity(browser, site, stub)
        select = page.get_by_test_id("activity-agent")
        labels = await select.locator("option").all_text_contents()
        assert labels[0] == "All assistants"
        assert len(labels) == 10 and len(set(labels)) == 10
        assert "Ticketing & handoff agent: escalation" in labels
        assert "Order management agent: approvals" in labels
        assert "Knowledge base agent" in labels

        await select.select_option("escalation_agent")
        await expect(run_row(page, 10)).to_be_visible()
        assert stub.last_list() == {"limit": "5", "agent": "escalation_agent"}
        assert await row_ids(page) == [10]

        # Combined with an outcome filter, both go to the server.
        await page.get_by_test_id("activity-status-ok").click()
        await expect(
            page.get_by_text("Nothing has been recorded for this selection.")
        ).to_be_visible()
        assert stub.last_list() == {"limit": "5", "agent": "escalation_agent", "status": "ok"}

        await select.select_option("")
        await page.get_by_test_id("activity-status-all").click()
        await expect(run_row(page, 12)).to_be_visible()
        assert stub.last_list() == {"limit": "5"}
        assert not diagnostics, diagnostics
        await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected


async def test_last_assistants_strip_dedupes_and_labels_outcomes(site: Site) -> None:
    stub = Stub()

    async def body(browser: Any) -> None:
        context, page, diagnostics = await open_activity(browser, site, stub)
        strip = page.get_by_test_id("activity-last-assistants")
        await expect(
            strip.locator("[data-testid^='activity-card-']:not([data-testid$='-outcome'])")
        ).to_have_count(5)
        order = await strip.locator("[data-agent]").evaluate_all(
            "els => els.map(el => el.dataset.agent)"
        )
        # Newest first, one card per avatar (escalation and ticket share tickets, the order
        # pair shares orders), the unknown capability skipped, never more than five.
        assert order == ["invoices", "knowledge", "tickets", "orders", "ecommerce"]
        assert await strip.get_by_test_id("activity-card-tickets-outcome").inner_text() == (
            "Needed attention"
        )
        for key in ("invoices", "knowledge", "orders", "ecommerce"):
            await expect(strip.get_by_test_id(f"activity-card-{key}-outcome")).to_have_text(
                "Worked"
            )
        await expect(strip.get_by_test_id("activity-card-tickets")).to_contain_text(
            "Ticketing & handoff agent"
        )
        await expect(strip.get_by_test_id("activity-card-tickets")).to_contain_text(
            re.compile(r"finished \d+ minutes? ago")
        )
        await expect(page.get_by_test_id("activity-overview")).to_have_text(
            "Last 24 hours: 12 runs, 3 needed attention"
        )
        # The overview line is left out when its read fails; it is never shown as zero.
        stub.fail_overview = True
        await page.reload()
        await expect(strip.get_by_test_id("activity-card-tickets")).to_be_visible()
        await expect(page.get_by_test_id("activity-overview")).to_have_count(0)
        assert not diagnostics, diagnostics
        await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected


async def test_error_run_shows_needs_attention_badge_and_still_undimmed_avatar(site: Site) -> None:
    stub = Stub()
    stub.runs = [run for run in default_runs() if run["id"] in (12, 10)]

    async def body(browser: Any) -> None:
        context, page, diagnostics = await open_activity(browser, site, stub)
        card = page.get_by_test_id("activity-card-tickets")
        avatar = card.locator("[data-agent]")
        await expect(card.get_by_test_id("activity-card-tickets-outcome")).to_have_text(
            "Needed attention"
        )
        await expect(page.get_by_test_id("activity-run-10-outcome")).to_have_text(
            "Needed attention"
        )
        # The words carry the state. The avatar is in its rest pose from the start, outline kept.
        await expect(avatar).to_have_attribute("data-state", "rest")
        await page.wait_for_timeout(2_000)
        await expect(avatar).to_have_attribute("data-state", "rest")
        assert await frames(avatar) == (1, "none")
        assert await avatar.locator("svg.fs-px-dim").count() == 0
        assert await avatar.evaluate("el => getComputedStyle(el).filter") != "none"
        # A finished one wakes once, then rests too.
        worked = page.get_by_test_id("activity-card-invoices").locator("[data-agent]")
        await expect(worked).to_have_attribute("data-state", "rest", timeout=6_000)
        assert await frames(worked) == (1, "none")
        assert not diagnostics, diagnostics
        await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected


async def test_strip_empty_state(site: Site) -> None:
    stub = Stub()
    stub.runs = []

    async def body(browser: Any) -> None:
        context, page, diagnostics = await open_activity(browser, site, stub, wait_for_rows=False)
        strip = page.get_by_test_id("activity-last-assistants")
        await expect(strip.get_by_text("Nothing has run yet.", exact=True)).to_be_visible()
        assert await strip.locator("[data-agent]").count() == 0
        await expect(
            page.get_by_text("Nothing has run yet. Runs appear here as your assistants work.")
        ).to_be_visible()
        await expect(page.get_by_test_id("activity-newer")).to_be_disabled()
        await expect(page.get_by_test_id("activity-older")).to_be_disabled()

        # A failed read is its own message, never "nothing has run" and never a zero.
        stub.fail_runs = True
        await expect(page.get_by_text("Runtime data unavailable.", exact=True)).to_be_visible(
            timeout=12_000
        )
        await expect(
            page.get_by_role("alert").filter(has_text="synthetic transient failure")
        ).to_have_count(1)
        assert not diagnostics, diagnostics
        await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected


async def test_failed_read_then_recovery_keeps_polling(site: Site) -> None:
    stub = Stub()

    async def body(browser: Any) -> None:
        context, page, diagnostics = await open_activity(browser, site, stub)
        stub.fail_runs = True
        alert = page.get_by_role("alert").filter(has_text="synthetic transient failure")
        await expect(alert).to_have_count(1, timeout=12_000)
        await expect(page.get_by_text("Runtime data unavailable.", exact=True)).to_be_visible()
        stub.fail_runs = False
        await expect(run_row(page, 12)).to_be_visible(timeout=12_000)
        await expect(alert).to_have_count(0)
        assert not diagnostics, diagnostics
        await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected


async def test_activity_at_five_widths_in_three_themes_without_overflow(site: Site) -> None:
    stub = Stub()
    stub.events["00000000-0000-4000-8000-000000000012"] = [
        event(101, "browser_seed_node_with_a_long_name", "invoice_agent", 23)
    ]

    async def body(browser: Any) -> None:
        for theme in THEMES:
            context, page, diagnostics = await open_activity(
                browser, site, stub, width=320, height=800, theme=theme
            )
            await expect(page.locator("html")).to_have_attribute("data-theme", theme)
            await page.get_by_test_id("activity-run-12-toggle").click()
            await expect(page.get_by_test_id("activity-run-12-details")).to_be_visible()
            await page.get_by_test_id("activity-date").click()
            await expect(page.get_by_test_id("activity-date-popover")).to_be_visible()
            for width in WIDTHS:
                await page.set_viewport_size(
                    {"width": width, "height": 800 if width < 768 else 900}
                )
                await page.wait_for_timeout(150)
                overflow = await page.evaluate(
                    """() => ({
                        page: [document.documentElement.scrollWidth, window.innerWidth],
                        wide: [...document.querySelectorAll(
                            '[data-testid^="activity-card-"], [data-testid^="activity-run-"], '
                            + '[data-testid="activity-last-assistants"], '
                            + '[data-testid="activity-status"], [data-testid="activity-agent"], '
                            + '[data-testid="activity-date"]'
                        )].filter(el => el.getBoundingClientRect().right > window.innerWidth + 0.5
                            || el.getBoundingClientRect().left < -0.5
                            || el.scrollWidth > el.clientWidth + 1).map(el => el.dataset.testid),
                    })"""
                )
                assert overflow["page"][0] <= overflow["page"][1], (theme, width, overflow)
                assert not overflow["wide"], (theme, width, overflow)
            assert not diagnostics, diagnostics
            await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected


async def test_reduced_motion_shows_rest_pose_in_strip(site: Site) -> None:
    stub = Stub()

    async def body(browser: Any) -> None:
        # Control: with motion allowed a finished assistant wakes (eight frames, played once),
        # then rests, so the reduced-motion checks below can fail.
        context, page, _ = await open_activity(browser, site, Stub())
        avatar = page.get_by_test_id("activity-card-invoices").locator("[data-agent]")
        await expect(avatar).to_have_attribute("data-state", "wake")
        for _ in range(30):  # motion is switched on a moment after mount
            if (await frames(avatar))[0] == 8:
                break
            await page.wait_for_timeout(50)
        assert await frames(avatar) == (8, "fs-px-show8")
        await expect(avatar).to_have_attribute("data-state", "rest", timeout=6_000)
        assert await frames(avatar) == (1, "none")
        await context.close()

        context, page, diagnostics = await open_activity(
            browser, site, stub, reduced_motion="reduce"
        )
        strip = page.get_by_test_id("activity-last-assistants")
        await expect(strip.locator("[data-agent]")).to_have_count(5)
        for index in range(5):
            avatar = strip.locator("[data-agent]").nth(index)
            assert await frames(avatar) == (1, "none")
        await page.wait_for_timeout(2_000)
        for index in range(5):
            assert await frames(strip.locator("[data-agent]").nth(index)) == (1, "none")
        assert not diagnostics, diagnostics
        await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected
