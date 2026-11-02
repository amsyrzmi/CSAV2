"""Real browser acceptance for the redesigned Services page (cobalt rows, stat cards, avatars).

The real Next.js shell runs unmodified from one disposable staged copy for the whole file (see
`site`). Its layouts fetch `/auth/me` and `/services/active` server-side, so the loopback stub
from `test_services_browser` answers those; every browser-side `/console/v1/*` call is answered
by Playwright. The action POSTs and the list reload each wait on their own event, so every state
(in flight, answered but not reloaded, reloaded) can be asserted while it lasts.
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
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.async_api import Locator, Page, async_playwright, expect

from tests.test_services_browser import (
    TENANT,
    TENANT_PROFILE,
    WAIT,
    Upstream,
    free_port,
    make_me,
    make_service,
)

pytestmark = pytest.mark.asyncio

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "CSA_WebConsole/csa-console/web"
REASON = "Activation is locked while billing is reviewed."
SAVED = "Saved, but the list could not refresh. Reload to see the change."
THEMES = ("cobalt", "night", "bureau")
WIDTHS = (320, 390, 768, 1024, 1440)
NO_WHATSAPP = {
    "connected": False,
    "display_phone": None,
    "phone_number_id_last4": None,
    "waba_id": None,
    "enabled": False,
    "callback_url": None,
    "connected_at": None,
}


def change(action: str, day: int, actor: str = "Preview Admin") -> dict[str, str]:
    """A `last_change` as the server sends it: the actor is already the string to show."""
    return {"action": action, "at": f"2026-10-{day:02d}T12:00:00Z", "actor": actor}


def make_agent(
    name: str, title: str, *, active: bool, last_change: dict[str, str] | None = None
) -> dict[str, Any]:
    return {
        "name": name,
        "title": title,
        "capability": {
            "name": name,
            "is_enabled": active,
            "client_enabled": True,
            "active": active,
            "capability_kind": "agent",
            "circuit_state": "closed",
            "fail_count": 0,
            "updated_at": "2026-10-25T00:00:00Z",
            "last_change": last_change,
        },
        "implementation": "local",
        "readiness": "available",
        "detail": f"Synthetic {title} detail.",
        "required_system": False,
        "specialized": False,
        "can_view_specialized": False,
        "can_subscribe": True,
        "can_toggle": True,
        "embedding_model": None,
        "compatible_chunks": None,
        "incompatible_chunks": None,
    }


def default_services() -> dict[str, dict[str, Any]]:
    """Eight services: active 3, agents on 5, waiting 1, suspended 1."""
    rows = [
        make_service(
            "customer_support",
            "Customer support",
            "base",
            "active",
            can_suspend=True,
            reference="Q-1",
            activated_at="2026-10-20T00:00:00Z",
            last_change=change("activated", 20),
            agents=[
                make_agent(
                    "kb_agent",
                    "Knowledge base agent",
                    active=True,
                    last_change=change("subscribed", 22),
                ),
                make_agent("ticket_agent", "Ticketing & handoff agent", active=True),
                make_agent("escalation_agent", "Escalation agent", active=True),
                make_agent("order_approval", "Order approval agent", active=True),
            ],
        ),
        make_service(
            "orders_products",
            "Orders and products",
            "addon",
            "active",
            can_suspend=True,
            agents=[
                make_agent("order_agent", "Order management agent", active=True),
                make_agent("invoice_agent", "Invoice lookup agent", active=False),
            ],
        ),
        make_service(
            "subscriptions",
            "Subscriptions",
            "addon",
            "requested",
            can_activate=True,
            requested_at="2026-10-25T00:00:00Z",
            last_change=change("requested", 25, "Tenant Admin Lee"),
            agents=[make_agent("subscription_agent", "Subscriptions agent", active=False)],
        ),
        make_service("bookings", "Bookings", "addon", "available", can_request=True),
        make_service("marketplaces", "Marketplaces", "addon", "available", can_request=True),
        make_service("e_invoicing", "E-invoicing", "addon", "available", sellable=False),
        make_service("hr", "People and HR", "standalone", "active", can_suspend=True),
        make_service(
            "staff_email",
            "Staff email assistant",
            "standalone",
            "suspended",
            can_activate=True,
            last_change=change("suspended", 27),
        ),
    ]
    return {row["key"]: row for row in rows}


@dataclass
class Site:
    base: str
    upstream: Upstream
    log_path: Path


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Site]:
    node = shutil.which("node")
    assert node, "Node is required for browser services acceptance"
    assert (WEB / "node_modules/next/dist/bin/next").is_file(), (
        "Console dependencies are missing; run `npm ci --ignore-scripts` in "
        "CSA_WebConsole/csa-console/web before browser services acceptance"
    )
    tmp_path = tmp_path_factory.mktemp("services-redesign")
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
                    pytest.fail(f"Synthetic services frontend did not start; log: {log_path}")
            yield Site(f"http://127.0.0.1:{port}", upstream, log_path)
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.close()


def open_event() -> asyncio.Event:
    event = asyncio.Event()
    event.set()
    return event


@dataclass
class Stub:
    """Browser-side answers. `gate` holds service actions, `list_gate` holds the list reload."""

    services: dict[str, dict[str, Any]] = field(default_factory=default_services)
    fail_status: int = 0
    gate: asyncio.Event = field(default_factory=open_event)
    list_gate: asyncio.Event = field(default_factory=open_event)
    posts: list[dict[str, Any]] = field(default_factory=list)
    patches: list[dict[str, Any]] = field(default_factory=list)
    list_gets: int = 0
    list_fails: bool = False
    switch_change: dict[str, str] = field(default_factory=lambda: change("tenant_switch_off", 28))
    unexpected: list[str] = field(default_factory=list)

    def hold(self, *, reload: bool = False) -> None:
        self.gate.clear()
        if reload:
            self.list_gate.clear()

    def release(self) -> None:
        self.gate.set()
        self.list_gate.set()

    def apply(self, key: str, verb: str, body: dict[str, Any]) -> dict[str, Any]:
        row = self.services[key]
        if verb == "request":
            row = {**row, "status": "requested", "requested_at": "2026-10-26T00:00:00Z"}
            row["can_request"] = False
        elif verb == "activate":
            row = {**row, "status": "active", "reference": body["reference"]}
            row.update(activated_at="2026-10-26T00:00:00Z", can_activate=False, can_suspend=True)
        else:
            row = {**row, "status": "suspended", "can_suspend": False, "can_activate": True}
        self.services[key] = row
        return row

    async def route(self, request_route: Any) -> None:
        request = request_route.request
        path = request.url.split("/console/v1/")[1].partition("?")[0]
        status, data = 200, None
        if path == "auth/csrf":
            data = {"csrf_token": "synthetic-csrf"}
        elif path == "admin/clients" and request.method == "GET":
            data = [TENANT_PROFILE]
        elif path == "services" and request.method == "GET":
            self.list_gets += 1
            await self.list_gate.wait()
            if self.list_fails:
                status, data = 503, {"detail": "synthetic list outage"}
            else:
                data = list(self.services.values())
        elif path == "channels/whatsapp" and request.method == "GET":
            data = NO_WHATSAPP
        elif path.startswith("services/") and request.method == "POST":
            key, verb = path.split("/")[1:3]
            body = request.post_data_json
            self.posts.append({"key": key, "verb": verb, "body": body})
            await self.gate.wait()
            if self.fail_status:
                status, data = self.fail_status, {"detail": REASON}
            else:
                data = self.apply(key, verb, body)
        elif path.startswith("capabilities/") and request.method == "PATCH":
            name = path.removeprefix("capabilities/")
            self.patches.append({"name": name, "body": request.post_data_json})
            await self.gate.wait()
            data = self.switch_agent(name, request.post_data_json)
        else:
            self.unexpected.append(f"{request.method} {path}")
            status, data = 500, {"detail": "unexpected synthetic call"}
        await request_route.fulfill(status=status, json=data)

    def switch_agent(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        """Apply a capability switch; the server records the change the list then reports."""
        capability: dict[str, Any] = {}
        for key, row in self.services.items():
            agents = []
            for agent in row["agents"]:
                if agent["name"] == name:
                    capability = {**agent["capability"], **body, "last_change": self.switch_change}
                    capability["active"] = capability["is_enabled"] and capability["client_enabled"]
                    agent = {**agent, "capability": capability}
                agents.append(agent)
            self.services[key] = {**row, "agents": agents}
        return capability


async def open_services(
    browser: Any,
    site: Site,
    stub: Stub,
    *,
    width: int = 1280,
    height: int = 1000,
    theme: str | None = None,
    tenant: bool = False,
    **options: Any,
) -> tuple[Any, Page, list[str]]:
    site.upstream.me, site.upstream.packages = make_me("platform_admin"), []
    if tenant:
        site.upstream.me = make_me("tenant_admin")
        site.upstream.packages = [{"key": "customer_support", "status": "active"}]
    # Dates on the last-changed line are rendered in the browser's zone; pin it so they are stable.
    options.setdefault("timezone_id", "UTC")
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
    await page.goto(
        f"{site.base}/portal/services" if tenant else f"{site.base}/services?client_id={TENANT}"
    )
    await expect(row_of(page, "customer_support")).to_be_visible(timeout=WAIT)
    return context, page, diagnostics


def row_of(page: Page, key: str) -> Locator:
    return page.get_by_test_id(f"services-card-{key}")


def avatar_of(row: Locator) -> Locator:
    return row.locator("[data-agent]").first


async def watch_states(row: Locator) -> None:
    """Record every data-state the row's own avatar shows from now on, in order."""
    await avatar_of(row).evaluate(
        """el => {
            const seen = (window.__seen ??= {});
            const list = (seen[el.closest('[data-testid]').dataset.testid] = [el.dataset.state]);
            new MutationObserver(() => {
                if (el.dataset.state !== list[list.length - 1]) list.push(el.dataset.state);
            }).observe(el, { attributes: true, attributeFilter: ["data-state"] });
        }"""
    )


async def seen_states(row: Locator) -> list[str]:
    key = await row.get_attribute("data-testid")
    return await row.page.evaluate("key => window.__seen[key]", key)


async def frames(row: Locator) -> tuple[int, str]:
    """Rendered sprite frames of the row's own avatar and the animation name of the first one."""
    frame = avatar_of(row).locator("svg g.fs-px-frame")
    return await frame.count(), await frame.first.evaluate(
        "el => getComputedStyle(el).animationName"
    )


async def with_browser(body: Any) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            await body(browser)
        finally:
            await browser.close()


async def test_activate_pending_then_wake_after_confirmation(site: Site) -> None:
    stub = Stub()

    async def body(browser: Any) -> None:
        context, page, diagnostics = await open_services(browser, site, stub)
        row = row_of(page, "subscriptions")
        avatar = avatar_of(row)
        status = row.get_by_test_id("services-status-subscriptions")
        pending = row.get_by_test_id("services-pending-subscriptions")
        await expect(status).to_have_text("Requested")
        await expect(avatar).to_have_attribute("data-agent", "subscriptions")
        await expect(avatar).to_have_attribute("data-state", "still")
        await expect(pending).to_be_empty()
        await watch_states(row)
        await watch_states(row_of(page, "customer_support"))

        await row.get_by_test_id("services-activate-subscriptions").click()
        await row.get_by_test_id("services-activate-reference").fill("Q-9")
        stub.hold(reload=True)
        await row.get_by_test_id("services-activate-confirm").click()

        # In flight: a pending word, a still dimmed avatar, nothing reflected, every button off.
        await expect(pending).to_have_text("Working...")
        await expect(avatar).to_have_attribute("data-state", "pending")
        assert await frames(row) == (1, "none")
        await expect(status).to_have_text("Requested")
        await expect(row.get_by_test_id("services-activation-subscriptions")).to_have_count(0)
        for button in await row.get_by_role("button").all():
            await expect(button).to_be_disabled()
        assert len(stub.posts) == 1

        # The server has answered but the list is not reloaded yet: still nothing changes.
        stub.gate.set()
        await page.wait_for_timeout(600)
        await expect(avatar).to_have_attribute("data-state", "pending")
        await expect(status).to_have_text("Requested")
        assert await seen_states(row) == ["still", "pending"]

        # Only with the reloaded list does the row become Active and the avatar wake.
        stub.release()
        await expect(status).to_have_text("Active")
        await expect(pending).to_be_empty()
        await expect(avatar).to_have_attribute("data-state", "wake", timeout=5_000)
        count, animation = await frames(row)
        assert count > 1 and animation != "none"
        await expect(row.get_by_test_id("services-activation-subscriptions")).to_contain_text(
            "Reference: Q-9"
        )
        await expect(avatar).to_have_attribute("data-state", "idle", timeout=10_000)
        assert await seen_states(row) == ["still", "pending", "wake", "idle"]
        # A reload alone never wakes a row nobody acted on.
        assert await seen_states(row_of(page, "customer_support")) == ["idle"]
        assert stub.posts == [
            {"key": "subscriptions", "verb": "activate", "body": {"reference": "Q-9", "note": None}}
        ]
        assert not diagnostics, diagnostics
        await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected
    assert not site.upstream.unexpected, site.upstream.unexpected


async def test_suspend_settles_only_after_confirmation(site: Site) -> None:
    stub = Stub()

    async def body(browser: Any) -> None:
        context, page, diagnostics = await open_services(browser, site, stub)
        row = row_of(page, "hr")
        avatar = avatar_of(row)
        status = row.get_by_test_id("services-status-hr")
        await expect(status).to_have_text("Active")
        await expect(avatar).to_have_attribute("data-state", "idle")
        await watch_states(row)

        await row.get_by_test_id("services-suspend-hr").click()
        await row.get_by_test_id("services-suspend-note").fill("Paused by request")
        stub.hold(reload=True)
        await row.get_by_test_id("services-suspend-confirm").click()

        await expect(row.get_by_test_id("services-pending-hr")).to_have_text("Working...")
        await expect(avatar).to_have_attribute("data-state", "pending")
        assert await frames(row) == (1, "none")
        await expect(status).to_have_text("Active")  # not yet suspended, so not yet settled
        stub.gate.set()
        await page.wait_for_timeout(600)
        await expect(avatar).to_have_attribute("data-state", "pending")
        await expect(status).to_have_text("Active")

        stub.release()
        await expect(status).to_have_text("Suspended")
        await expect(avatar).to_have_attribute("data-state", "still")
        await expect(row.get_by_test_id("services-pending-hr")).to_be_empty()
        await page.wait_for_timeout(800)
        assert await frames(row) == (1, "none")
        assert await seen_states(row) == ["idle", "pending", "still"]
        assert stub.posts == [
            {"key": "hr", "verb": "suspend", "body": {"note": "Paused by request"}}
        ]
        assert not diagnostics, diagnostics
        await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected


async def test_failed_activation_keeps_row_and_shows_reason(site: Site) -> None:
    stub = Stub(fail_status=409)

    async def body(browser: Any) -> None:
        context, page, diagnostics = await open_services(browser, site, stub)
        row = row_of(page, "subscriptions")
        avatar = avatar_of(row)
        await expect(avatar).to_have_attribute("data-state", "still")
        await watch_states(row)

        await row.get_by_test_id("services-activate-subscriptions").click()
        await row.get_by_test_id("services-activate-reference").fill("Q-9")
        stub.hold()
        await row.get_by_test_id("services-activate-confirm").click()
        await expect(avatar).to_have_attribute("data-state", "pending")
        stub.release()

        await expect(row.get_by_role("alert")).to_have_text(REASON)
        await expect(row.get_by_test_id("services-status-subscriptions")).to_have_text("Requested")
        await expect(row.get_by_test_id("services-activation-subscriptions")).to_have_count(0)
        await expect(avatar).to_have_attribute("data-state", "still")
        await expect(row.get_by_test_id("services-pending-subscriptions")).to_be_empty()
        # The confirm form stays so the reason can be acted on, and the buttons work again.
        await expect(row.get_by_test_id("services-activate-confirm")).to_be_enabled()
        await page.wait_for_timeout(1_800)  # a wake would have started and ended by now
        assert await frames(row) == (1, "none")
        assert await seen_states(row) == ["still", "pending", "still"]
        assert len(stub.posts) == 1
        assert not diagnostics, diagnostics
        await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected


async def stat_text(page: Page, kind: str) -> str:
    return " ".join((await page.get_by_test_id(f"services-stat-{kind}").inner_text()).split())


async def test_stat_cards_show_real_counts(site: Site) -> None:
    stub = Stub()

    async def body(browser: Any) -> None:
        context, page, diagnostics = await open_services(browser, site, stub)
        for kind, label, figure in (
            ("active", "Active services", "3"),
            ("agents", "Agents on", "5"),
            ("waiting", "Waiting for activation", "1"),
            ("suspended", "Suspended", "1"),
        ):
            stat = page.get_by_test_id(f"services-stat-{kind}")
            await expect(stat).to_be_visible()
            text = await stat_text(page, kind)
            assert text.startswith(f"{label} {figure} "), text
            assert len(text) > len(f"{label} {figure} "), "the footer line is missing"
            assert await stat.locator("a, button").count() == 0, "a stat footer is not a link"
            # Only the icon chip draws; the footer carries no arrow that would suggest a link.
            assert await stat.locator("svg").count() == 1, kind

        # Activate one service: waiting drops, active rises, suspended and agents stay.
        row = row_of(page, "subscriptions")
        await row.get_by_test_id("services-activate-subscriptions").click()
        await row.get_by_test_id("services-activate-reference").fill("Q-9")
        await row.get_by_test_id("services-activate-confirm").click()
        await expect(page.get_by_test_id("services-stat-active")).to_contain_text("4")
        assert (await stat_text(page, "active")).startswith("Active services 4 ")
        assert (await stat_text(page, "waiting")).startswith("Waiting for activation 0 ")
        assert (await stat_text(page, "suspended")).startswith("Suspended 1 ")
        assert (await stat_text(page, "agents")).startswith("Agents on 5 ")

        # Suspend another: active falls back, suspended rises.
        row = row_of(page, "hr")
        await row.get_by_test_id("services-suspend-hr").click()
        await row.get_by_test_id("services-suspend-confirm").click()
        await expect(page.get_by_test_id("services-stat-suspended")).to_contain_text("2")
        assert (await stat_text(page, "active")).startswith("Active services 3 ")
        assert (await stat_text(page, "suspended")).startswith("Suspended 2 ")
        assert not diagnostics, diagnostics
        await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected


async def test_einvoicing_row_shows_one_status_word_without_avatar(site: Site) -> None:
    stub = Stub()

    async def body(browser: Any) -> None:
        context, page, diagnostics = await open_services(browser, site, stub)
        row = row_of(page, "e_invoicing")
        # One status word: "Coming soon" (asserted across the Services tests); no extra "Planned".
        await expect(row.get_by_test_id("services-status-e_invoicing")).to_have_text("Coming soon")
        await expect(row.get_by_text("Planned", exact=True)).to_have_count(0)
        await expect(row.locator("[data-agent]")).to_have_count(0)
        await expect(row.locator("svg").first).to_be_visible()  # the plain icon, no chip
        await expect(row.get_by_role("button")).to_have_count(0)
        # Every other row keeps its own pixel avatar, and nothing else says "Planned".
        for key, agent in (
            ("customer_support", "support"),
            ("orders_products", "orders"),
            ("subscriptions", "subscriptions"),
            ("bookings", "bookings"),
            ("marketplaces", "ecommerce"),
            ("hr", "hr"),
            ("staff_email", "email"),
        ):
            await expect(avatar_of(row_of(page, key))).to_have_attribute("data-agent", agent)
            await expect(row_of(page, key).get_by_text("Planned", exact=True)).to_have_count(0)
        assert not diagnostics, diagnostics
        await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected


async def test_services_row_avatar_has_no_second_chip(site: Site) -> None:
    """The avatar sits straight on the row: no chip, tile, border or radius around it."""
    stub = Stub()

    async def body(browser: Any) -> None:
        for theme in THEMES:
            context, page, diagnostics = await open_services(browser, site, stub, theme=theme)
            boxes = await page.evaluate(
                """() => [...document.querySelectorAll('[data-testid^="services-card-"]')]
                .map(row => {
                    const mark = row.querySelector('[data-agent]') ?? row.querySelector('svg');
                    const chain = [];
                    for (let node = mark; node && node !== row; node = node.parentElement) {
                        const style = getComputedStyle(node);
                        chain.push({
                            key: row.dataset.testid,
                            tag: node.tagName.toLowerCase(),
                            background: style.backgroundColor + ' ' + style.backgroundImage,
                            radius: style.borderRadius,
                            overflow: style.overflow,
                            border: style.borderTopWidth,
                        });
                    }
                    return chain;
                })"""
            )
            assert len(boxes) >= 8, boxes
            for chain in boxes:
                for box in chain:
                    if box["tag"] == "svg":
                        continue
                    assert box["background"] == "rgba(0, 0, 0, 0) none", (theme, box)
                    assert box["radius"] == "0px", (theme, box)
                    assert box["border"] == "0px", (theme, box)
            # The avatar and its icon fallback are the only thing in their slot: no wrapper chip.
            slot = await avatar_of(row_of(page, "customer_support")).evaluate(
                "el => [el.parentElement.children.length, el.parentElement.dataset.agent ?? null]"
            )
            assert slot[0] == 1, slot
            assert not diagnostics, diagnostics
            await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected


async def test_headings_keep_the_page_outline_valid(site: Site) -> None:
    stub = Stub()

    async def body(browser: Any) -> None:
        context, page, diagnostics = await open_services(browser, site, stub)
        await expect(page.get_by_role("heading", level=1)).to_have_text(["Services"])
        groups = page.get_by_role("heading", level=2)
        # The WhatsApp panel (shown while Customer support is active) is a section of its own.
        await expect(groups).to_have_text(
            ["Core service", "Add-ons", "Separate services", "WhatsApp number"]
        )
        titles = page.get_by_role("heading", level=3)
        await expect(titles.filter(has_text="Customer support")).to_have_count(1)
        for kind, count in (("base", 1), ("addon", 5), ("standalone", 2)):
            group = page.get_by_test_id(f"services-group-{kind}")
            await expect(group.get_by_role("heading", level=3)).to_have_count(count)
        # Agent cards open under their service row: their titles are h4, never another h3.
        row = row_of(page, "customer_support")
        await row.get_by_test_id("services-agents-toggle-customer_support").click()
        agents = page.get_by_test_id("services-agents-customer_support")
        await expect(agents.get_by_role("heading", level=4)).to_have_count(4)
        await expect(agents.get_by_role("heading", level=3)).to_have_count(0)
        await expect(row.get_by_role("heading", level=3)).to_have_text(["Customer support"])
        assert not diagnostics, diagnostics
        await context.close()

    await with_browser(body)


async def test_reduced_motion_rows_are_still(site: Site) -> None:
    stub = Stub()

    async def body(browser: Any) -> None:
        # Control: with motion allowed an active row loops four frames, so the checks below bite.
        context, page, _ = await open_services(browser, site, Stub())
        await expect(avatar_of(row_of(page, "customer_support"))).to_have_attribute(
            "data-state", "idle"
        )
        # Motion is switched on after mount, so wait for the loop to appear.
        control = row_of(page, "customer_support")
        await expect(avatar_of(control).locator("svg g.fs-px-frame")).to_have_count(4)
        assert await frames(control) == (4, "fs-px-show")
        await context.close()

        context, page, diagnostics = await open_services(
            browser, site, stub, reduced_motion="reduce"
        )
        active = row_of(page, "customer_support")
        await expect(avatar_of(active)).to_have_attribute("data-state", "idle")
        for key in stub.services:
            row = row_of(page, key)
            if key != "e_invoicing":
                assert await frames(row) == (1, "none"), key
        await page.wait_for_timeout(600)
        assert await frames(active) == (1, "none")

        row = row_of(page, "subscriptions")
        await watch_states(row)
        await row.get_by_test_id("services-activate-subscriptions").click()
        await row.get_by_test_id("services-activate-reference").fill("Q-9")
        stub.hold()
        await row.get_by_test_id("services-activate-confirm").click()
        await expect(avatar_of(row)).to_have_attribute("data-state", "pending")
        stub.release()
        await expect(row.get_by_test_id("services-status-subscriptions")).to_have_text("Active")
        await expect(avatar_of(row)).to_have_attribute("data-state", "idle")
        await page.wait_for_timeout(1_800)
        assert await frames(row) == (1, "none")
        assert await frames(active) == (1, "none")
        seen = await seen_states(row)
        assert "wake" not in seen and seen[-1] == "idle", seen
        assert not diagnostics, diagnostics
        await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected


async def test_double_submit_sends_one_request(site: Site) -> None:
    stub = Stub()

    async def body(browser: Any) -> None:
        context, page, _ = await open_services(browser, site, stub)
        stub.hold()

        # Two clicks inside one task: the second lands before any request has been answered.
        bookings = row_of(page, "bookings")
        request = bookings.get_by_test_id("services-request-bookings")
        await expect(request).to_be_enabled()
        await request.evaluate("el => { el.click(); el.click(); }")
        await expect(bookings.get_by_test_id("services-pending-bookings")).to_have_text(
            "Working..."
        )
        await expect(request).to_be_disabled()
        stub.release()
        await expect(bookings.get_by_test_id("services-status-bookings")).to_have_text("Requested")
        await page.wait_for_timeout(500)
        assert [(p["key"], p["verb"]) for p in stub.posts] == [("bookings", "request")], stub.posts

        # The same for a form submitted twice in one task (Enter held down, a double click).
        row = row_of(page, "subscriptions")
        await row.get_by_test_id("services-activate-subscriptions").click()
        await row.get_by_test_id("services-activate-reference").fill("Q-9")
        stub.hold()
        await row.get_by_test_id("services-activate-reference").evaluate(
            "el => { el.form.requestSubmit(); el.form.requestSubmit(); }"
        )
        await expect(row.get_by_test_id("services-pending-subscriptions")).to_have_text(
            "Working..."
        )
        stub.release()
        await expect(row.get_by_test_id("services-status-subscriptions")).to_have_text("Active")
        await page.wait_for_timeout(500)
        verbs = [(p["key"], p["verb"]) for p in stub.posts]
        assert verbs == [("bookings", "request"), ("subscriptions", "activate")], stub.posts
        await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected


async def test_layout_fits_every_width_in_every_theme(site: Site) -> None:
    stub = Stub()

    async def body(browser: Any) -> None:
        for theme in THEMES:
            context, page, diagnostics = await open_services(
                browser, site, stub, width=320, height=800, theme=theme
            )
            await expect(page.locator("html")).to_have_attribute("data-theme", theme)
            # The widest content: agent cards under the first row and an open confirm form.
            await (
                row_of(page, "customer_support")
                .get_by_test_id("services-agents-toggle-customer_support")
                .click()
            )
            await expect(page.get_by_test_id("services-agents-customer_support")).to_be_visible()
            await (
                row_of(page, "subscriptions")
                .get_by_test_id("services-activate-subscriptions")
                .click()
            )
            await expect(page.get_by_test_id("services-activate-reference")).to_be_visible()
            for width in WIDTHS:
                height = 800 if width < 768 else 900
                await page.set_viewport_size({"width": width, "height": height})
                await page.wait_for_timeout(150)
                overflow = await page.evaluate(
                    """() => ({
                        page: [document.documentElement.scrollWidth, window.innerWidth],
                        wide: [...document.querySelectorAll(
                            '[data-testid^="services-card-"], [data-testid^="services-stat-"], '
                            + '[data-testid^="services-group-"]'
                        )].filter(el => el.getBoundingClientRect().right > window.innerWidth + 0.5
                            || el.scrollWidth > el.clientWidth + 1).map(el => el.dataset.testid),
                    })"""
                )
                assert overflow["page"][0] <= overflow["page"][1], (theme, width, overflow)
                assert not overflow["wide"], (theme, width, overflow)
            assert not diagnostics, diagnostics
            await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected


def tenant_view(services: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """What the server sends a tenant member: the same changes, but only role labels as actors."""
    labels = {"requested": "a member of your team", "subscribed": "a member of your team"}

    def relabel(value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        return {**value, "actor": labels.get(value["action"], "a platform administrator")}

    out: dict[str, dict[str, Any]] = {}
    for key, row in services.items():
        agents = [
            {
                **a,
                "capability": {
                    **a["capability"],
                    "last_change": relabel(a["capability"]["last_change"]),
                },
            }
            for a in row["agents"]
        ]
        out[key] = {**row, "last_change": relabel(row.get("last_change")), "agents": agents}
    return out


def line_of(page: Page, key: str) -> Locator:
    return page.get_by_test_id(f"services-last-change-{key}")


async def test_platform_admin_sees_who_made_each_change(site: Site) -> None:
    stub = Stub()

    async def body(browser: Any) -> None:
        context, page, diagnostics = await open_services(browser, site, stub)
        await expect(line_of(page, "customer_support")).to_have_text(
            "Activated 20 Oct 2026 by Preview Admin"
        )
        await expect(line_of(page, "subscriptions")).to_have_text(
            "Requested 25 Oct 2026 by Tenant Admin Lee"
        )
        await expect(line_of(page, "staff_email")).to_have_text(
            "Suspended 27 Oct 2026 by Preview Admin"
        )
        # The kept line above it only adds what the last-change line does not say.
        activation = row_of(page, "customer_support").get_by_test_id(
            "services-activation-customer_support"
        )
        await expect(activation).to_have_text("Reference: Q-1.")
        await expect(
            row_of(page, "subscriptions").get_by_test_id("services-requested-subscriptions")
        ).to_have_text("Awaiting activation.")
        # No last change (older backend or no audit row): the full sentence stays.
        await expect(row_of(page, "hr").get_by_test_id("services-activation-hr")).to_have_text(
            "Activated by a platform administrator."
        )
        await expect(line_of(page, "hr")).to_have_count(0)
        for key in ("customer_support", "subscriptions"):
            text = await row_of(page, key).inner_text()
            assert text.count("Activated") + text.count("Requested") <= 2, (key, text)
            assert "by a platform administrator" not in text, (key, text)
        order = await row_of(page, "customer_support").evaluate(
            """row => {
                const find = id => row.querySelector(`[data-testid="${id}-customer_support"]`);
                const a = find("services-activation");
                const b = find("services-last-change");
                return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
            }"""
        )
        assert order, "the last-change line belongs below the activation line"

        # Agent cards carry their own line, under the status words.
        await (
            row_of(page, "customer_support")
            .get_by_test_id("services-agents-toggle-customer_support")
            .click()
        )
        await expect(page.get_by_test_id("agent-last-change-kb_agent")).to_have_text(
            "Subscription turned on 22 Oct 2026 by Preview Admin"
        )
        assert not diagnostics, diagnostics
        await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected


async def test_no_line_without_a_usable_change(site: Site) -> None:
    stub = Stub()
    stub.services["bookings"]["last_change"] = {**change("activated", 20), "at": "not a date"}
    stub.services["marketplaces"]["last_change"] = {**change("exploded", 20)}

    async def body(browser: Any) -> None:
        context, page, diagnostics = await open_services(browser, site, stub)
        for key in ("hr", "orders_products", "bookings", "marketplaces", "e_invoicing"):
            await expect(row_of(page, key)).to_be_visible()
            await expect(line_of(page, key)).to_have_count(0)
        await expect(line_of(page, "customer_support")).to_have_count(1)
        # An agent with no change shows no line either; the others keep theirs.
        await (
            row_of(page, "customer_support")
            .get_by_test_id("services-agents-toggle-customer_support")
            .click()
        )
        await expect(page.get_by_test_id("agent-card-ticket_agent")).to_be_visible()
        await expect(page.get_by_test_id("agent-last-change-ticket_agent")).to_have_count(0)
        await expect(page.get_by_test_id("agent-last-change-kb_agent")).to_have_count(1)
        assert not diagnostics, diagnostics
        await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected


async def test_tenant_member_sees_only_the_role_labels_the_server_sends(site: Site) -> None:
    stub = Stub(services=tenant_view(default_services()))

    async def body(browser: Any) -> None:
        context, page, diagnostics = await open_services(browser, site, stub, tenant=True)
        await expect(line_of(page, "customer_support")).to_have_text(
            "Activated 20 Oct 2026 by a platform administrator"
        )
        await expect(line_of(page, "subscriptions")).to_have_text(
            "Requested 25 Oct 2026 by a member of your team"
        )
        await expect(line_of(page, "hr")).to_have_count(0)
        await (
            row_of(page, "customer_support")
            .get_by_test_id("services-agents-toggle-customer_support")
            .click()
        )
        await expect(page.get_by_test_id("agent-last-change-kb_agent")).to_have_text(
            "Subscription turned on 22 Oct 2026 by a member of your team"
        )
        # No name or id anywhere in the rows: the page only repeats what the server labelled.
        for key in ("customer_support", "subscriptions", "staff_email"):
            text = await row_of(page, key).inner_text()
            for leaked in ("Preview Admin", "Tenant Admin Lee", "Synthetic Admin"):
                assert leaked not in text, (key, leaked)
            assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", text), (key, text)
        assert not diagnostics, diagnostics
        await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected


async def test_actor_text_is_shown_as_text_not_markup(site: Site) -> None:
    stub = Stub()
    hostile = '<img src=x onerror="window.__pwned = 1"> & Co'
    stub.services["hr"]["last_change"] = change("activated", 21, hostile)

    async def body(browser: Any) -> None:
        context, page, diagnostics = await open_services(browser, site, stub)
        await expect(line_of(page, "hr")).to_have_text(f"Activated 21 Oct 2026 by {hostile}")
        await expect(line_of(page, "hr").locator("img")).to_have_count(0)
        assert await page.evaluate("window.__pwned ?? null") is None
        assert not diagnostics, diagnostics
        await context.close()

    await with_browser(body)


async def test_agent_switch_reloads_the_list_and_updates_the_line(site: Site) -> None:
    stub = Stub()

    async def body(browser: Any) -> None:
        context, page, diagnostics = await open_services(browser, site, stub)
        await (
            row_of(page, "customer_support")
            .get_by_test_id("services-agents-toggle-customer_support")
            .click()
        )
        line = page.get_by_test_id("agent-last-change-kb_agent")
        await expect(line).to_have_text("Subscription turned on 22 Oct 2026 by Preview Admin")
        loads = stub.list_gets

        await page.get_by_test_id("agent-switch-kb_agent").click()
        await expect(line).to_have_text("Company switch turned off 28 Oct 2026 by Preview Admin")
        await expect(page.get_by_test_id("agent-pending-kb_agent")).to_be_empty()
        assert stub.patches == [{"name": "kb_agent", "body": {"client_enabled": False}}]
        # The reload that every confirmed action already does is the only fetch: no extra ones.
        await page.wait_for_timeout(500)
        assert stub.list_gets == loads + 1, (loads, stub.list_gets)
        # The card's labelled values still read exactly as before.
        card = page.get_by_test_id("agent-card-kb_agent")
        await expect(card.get_by_text("Subscribed", exact=True)).to_have_count(1)
        await expect(card.get_by_text("Off", exact=True)).to_have_count(1)
        assert not diagnostics, diagnostics
        await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected


async def test_saved_action_with_a_failed_reload_is_not_reported_as_a_failure(site: Site) -> None:
    stub = Stub()

    async def body(browser: Any) -> None:
        context, page, diagnostics = await open_services(browser, site, stub)
        row = row_of(page, "subscriptions")
        avatar = avatar_of(row)
        await expect(avatar).to_have_attribute("data-state", "still")
        await watch_states(row)
        await row.get_by_test_id("services-activate-subscriptions").click()
        await row.get_by_test_id("services-activate-reference").fill("Q-9")
        stub.list_fails = True
        await row.get_by_test_id("services-activate-confirm").click()

        # The POST succeeded; only the reload failed. The row says so, without a failure reason.
        await expect(row.get_by_role("alert")).to_have_text(SAVED)
        await expect(row.get_by_text("synthetic list outage")).to_have_count(0)
        await expect(row.get_by_test_id("services-pending-subscriptions")).to_be_empty()
        # The form is closed (the change is saved), so nothing invites a second activation.
        await expect(row.get_by_test_id("services-activate-confirm")).to_have_count(0)
        # No reloaded status confirms the change, so the avatar never wakes.
        await page.wait_for_timeout(1_800)
        assert await frames(row) == (1, "none")
        assert await seen_states(row) == ["still", "pending", "still"]
        assert [(p["key"], p["verb"]) for p in stub.posts] == [("subscriptions", "activate")]
        assert not diagnostics, diagnostics
        await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected


async def test_saved_agent_switch_with_a_failed_reload_is_not_reported_as_a_failure(
    site: Site,
) -> None:
    stub = Stub()

    async def body(browser: Any) -> None:
        context, page, diagnostics = await open_services(browser, site, stub)
        row = row_of(page, "customer_support")
        await row.get_by_test_id("services-agents-toggle-customer_support").click()
        card = page.get_by_test_id("agent-card-kb_agent")
        avatar = card.locator("[data-agent]")
        await expect(avatar).to_have_attribute("data-state", "idle")
        await avatar.evaluate(
            """el => {
                const list = (window.__kb = [el.dataset.state]);
                new MutationObserver(() => {
                    if (el.dataset.state !== list[list.length - 1]) list.push(el.dataset.state);
                }).observe(el, { attributes: true, attributeFilter: ["data-state"] });
            }"""
        )
        stub.list_fails = True
        await page.get_by_test_id("agent-switch-kb_agent").click()

        await expect(card.get_by_role("alert")).to_have_text(SAVED)
        await expect(page.get_by_text("synthetic list outage")).to_have_count(0)
        await expect(page.get_by_test_id("agent-pending-kb_agent")).to_be_empty()
        await page.wait_for_timeout(1_000)
        assert await page.evaluate("window.__kb") == ["idle", "pending", "idle"]
        assert stub.patches == [{"name": "kb_agent", "body": {"client_enabled": False}}]
        # The next action clears the notice.
        stub.list_fails = False
        await page.get_by_test_id("agent-subscribe-kb_agent").click()
        await expect(card.get_by_role("alert")).to_have_count(0)
        assert not diagnostics, diagnostics
        await context.close()

    await with_browser(body)
    assert not stub.unexpected, stub.unexpected
