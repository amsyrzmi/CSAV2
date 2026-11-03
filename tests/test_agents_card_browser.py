"""Real browser acceptance for the redesigned `AgentCard` on the Services page.

The real Next.js shell runs unmodified from one disposable staged copy for the whole file
(see `site`). Its layouts fetch `/auth/me` and `/services/active` server-side, so a loopback
stub origin answers those; every browser-side `/console/v1/*` call is answered by Playwright,
and the capability PATCH is held on an event so each state can be asserted while it is in flight.
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
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.async_api import Locator, Page, async_playwright, expect

pytestmark = pytest.mark.asyncio

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "CSA_WebConsole/csa-console/web"
TENANT = "11111111-1111-4111-8111-111111111111"
WAIT = 60_000  # First visit to each route compiles it in `next dev`.
REASON = "Agent changes are locked while billing is reviewed."
ME = {
    "user": {
        "id": "22222222-2222-4222-8222-222222222222",
        "email": "synthetic@example.test",
        "display_name": "Synthetic User",
        "is_platform_admin": False,
        "must_change_password": False,
    },
    "memberships": [{"client_id": TENANT, "role": "tenant_admin"}],
    "active_client_id": TENANT,
    "role": "tenant_admin",
    "csrf_token": "synthetic-csrf",
}
PROFILE = {
    "id": TENANT,
    "slug": "synthetic",
    "name": "Synthetic Tenant",
    "timezone": "Asia/Kuala_Lumpur",
    "status": "active",
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_agent(
    name: str, title: str, *, is_enabled: bool, client_enabled: bool, registered: bool = True
) -> dict[str, Any]:
    capability = {
        "name": name,
        "is_enabled": is_enabled,
        "client_enabled": client_enabled,
        "active": is_enabled and client_enabled,
        "capability_kind": "agent",
        "circuit_state": "closed",
        "fail_count": 0,
        "updated_at": "2026-10-25T00:00:00Z",
    }
    return {
        "name": name,
        "title": title,
        "capability": capability if registered else None,
        "implementation": "local" if registered else "not_implemented",
        "readiness": "available",
        "detail": f"Synthetic {title} detail.",
        "required_system": False,
        "specialized": False,
        "can_view_specialized": False,
        "can_subscribe": registered,
        "can_toggle": registered,
        "embedding_model": None,
        "compatible_chunks": None,
        "incompatible_chunks": None,
    }


def make_service(agents: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "key": "customer_support",
        "title": "Customer Support",
        "summary": "Synthetic Customer Support package.",
        "kind": "base",
        "requires": [],
        "sellable": True,
        "tabs": [],
        "status": "active",
        "reference": "Q-1",
        "note": None,
        "requested_at": None,
        "activated_at": "2026-10-25T00:00:00Z",
        "requested_by": None,
        "activated_by": None,
        "agents": agents,
        "inner_switch": None,
        "consistent": True,
        "inconsistencies": [],
        "can_request": False,
        "can_activate": False,
        "can_suspend": False,
    }


class Upstream:
    """Loopback stand-in for the gateway origin used by server-side layout fetches."""

    def __init__(self) -> None:
        self.unexpected: list[str] = []
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = self.path.split("?")[0]
                if path == "/console/v1/auth/me":
                    self.reply(200, ME)
                elif path == "/console/v1/services/active":
                    self.reply(200, {"packages": [{"key": "customer_support", "status": "active"}]})
                elif path == "/console/v1/profile":
                    self.reply(200, PROFILE)
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


@dataclass
class Site:
    base: str
    upstream: Upstream
    log_path: Path


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Site]:
    node = shutil.which("node")
    assert node, "Node is required for browser agent card acceptance"
    assert (WEB / "node_modules/next/dist/bin/next").is_file(), (
        "Console dependencies are missing; run `npm ci --ignore-scripts` in "
        "CSA_WebConsole/csa-console/web before browser agent card acceptance"
    )
    tmp_path = tmp_path_factory.mktemp("agents-card")
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
                    pytest.fail(f"Synthetic agent card frontend did not start; log: {log_path}")
            yield Site(f"http://127.0.0.1:{port}", upstream, log_path)
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.close()


@dataclass
class Stub:
    """Browser-side answers: the Services list and the capability PATCH, held on `gate`."""

    agents: dict[str, dict[str, Any]]
    patch_status: int = 200
    gate: asyncio.Event = field(default_factory=asyncio.Event)
    patches: list[dict[str, Any]] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)

    async def route(self, request_route: Any) -> None:
        request = request_route.request
        path = request.url.split("/console/v1/")[1].partition("?")[0]
        status, data = 200, None
        if path == "auth/csrf":
            data = {"csrf_token": "synthetic-csrf"}
        elif path == "services" and request.method == "GET":
            data = [make_service(list(self.agents.values()))]
        elif path == "channels/whatsapp" and request.method == "GET":
            data = {
                "connected": False,
                "display_phone": None,
                "phone_number_id_last4": None,
                "waba_id": None,
                "enabled": False,
                "callback_url": None,
                "connected_at": None,
            }
        elif path.startswith("capabilities/") and request.method == "PATCH":
            name = path.removeprefix("capabilities/")
            self.patches.append({"name": name, "body": request.post_data_json})
            await self.gate.wait()
            if self.patch_status == 200:
                agent = self.agents[name]
                capability = {**agent["capability"], **request.post_data_json}
                capability["active"] = capability["is_enabled"] and capability["client_enabled"]
                self.agents[name] = {**agent, "capability": capability}
                data = capability
            else:
                status, data = self.patch_status, {"detail": REASON}
        else:
            self.unexpected.append(f"{request.method} {path}")
            status, data = 500, {"detail": "unexpected synthetic call"}
        await request_route.fulfill(status=status, json=data)


def default_agents() -> dict[str, dict[str, Any]]:
    return {
        "kb_agent": make_agent(
            "kb_agent", "Business knowledge agent", is_enabled=True, client_enabled=True
        ),
        "ticket_agent": make_agent(
            "ticket_agent", "Ticketing & handoff agent", is_enabled=False, client_enabled=True
        ),
        "lhdn_agent": make_agent(
            "lhdn_agent",
            "LHDN e-invoice agent",
            is_enabled=False,
            client_enabled=False,
            registered=False,
        ),
    }


async def open_agents(
    browser: Any, site: Site, stub: Stub, *, theme: str | None = None, **context_options: Any
) -> tuple[Any, Page, list[str]]:
    context = await browser.new_context(**context_options)
    context.set_default_navigation_timeout(WAIT)
    cookies = [{"name": "csa_session", "value": "synthetic-session", "url": site.base}]
    if theme:
        cookies.append({"name": "fs-theme", "value": theme, "url": site.base})
    await context.add_cookies(cookies)
    page = await context.new_page()
    diagnostics: list[str] = []
    page.on("pageerror", lambda error: diagnostics.append(str(error)))
    await page.route("**/console/v1/**", stub.route)
    await page.goto(f"{site.base}/portal/services")
    await page.get_by_test_id("services-agents-toggle-customer_support").click(timeout=WAIT)
    return context, page, diagnostics


def card_of(page: Page, name: str) -> Locator:
    return page.get_by_test_id(f"agent-card-{name}")


async def watch_states(card: Locator) -> None:
    """Record every data-state the card's avatar shows from now on, in order."""
    await card.locator("[data-agent]").evaluate(
        """el => {
            const seen = (window.__seen ??= {});
            const list = (seen[el.dataset.agent] = [el.dataset.state]);
            new MutationObserver(() => {
                if (el.dataset.state !== list[list.length - 1]) list.push(el.dataset.state);
            }).observe(el, { attributes: true, attributeFilter: ["data-state"] });
        }"""
    )


async def seen_states(card: Locator) -> list[str]:
    agent = await card.locator("[data-agent]").get_attribute("data-agent")
    return await card.page.evaluate("agent => window.__seen[agent]", agent)


async def frames(card: Locator) -> tuple[int, str]:
    """Rendered sprite frames and the animation name of the first one."""
    frame = card.locator("[data-agent] svg g.fs-px-frame")
    return await frame.count(), await frame.first.evaluate(
        "el => getComputedStyle(el).animationName"
    )


async def test_agent_switch_shows_pending_then_confirms(site: Site) -> None:
    stub = Stub(default_agents())
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context, page, diagnostics = await open_agents(browser, site, stub)
            card = card_of(page, "ticket_agent")
            avatar = card.locator("[data-agent]")
            await expect(card.get_by_test_id("agent-plan-ticket_agent")).to_have_text(
                "No", timeout=WAIT
            )
            await expect(avatar).to_have_attribute("data-state", "still")
            await expect(avatar).to_have_attribute("data-agent", "tickets")
            steady = card_of(page, "kb_agent").locator("[data-agent]")
            await expect(steady).to_have_attribute("data-state", "idle")
            await watch_states(card)
            await watch_states(card_of(page, "kb_agent"))
            # The status region exists before anything happens, so a screen reader announces it.
            await expect(card.get_by_role("status")).to_be_empty()

            await card.get_by_role("button", name="Add to my plan", exact=True).click()

            # In flight: a pending label, the clicked button disabled, a still dimmed avatar.
            await expect(card.get_by_test_id("agent-pending-ticket_agent")).to_have_text(
                "Saving..."
            )
            await expect(
                card.get_by_role("button", name="Add to my plan", exact=True)
            ).to_be_disabled()
            await expect(avatar).to_have_attribute("data-state", "pending")
            assert await frames(card) == (1, "none")
            await expect(card.get_by_test_id("agent-plan-ticket_agent")).to_have_text("No")
            assert len(stub.patches) == 1
            assert stub.patches[0]["body"] == {"is_enabled": True}

            stub.gate.set()

            # Only now, with the server answer and the reloaded list, does the card change and wake.
            await expect(card.get_by_test_id("agent-plan-ticket_agent")).to_have_text("Yes")
            await expect(card.get_by_test_id("agent-pending-ticket_agent")).to_be_empty()
            await expect(avatar).to_have_attribute("data-state", "wake", timeout=5_000)
            count, animation = await frames(card)
            assert count > 1 and animation != "none"
            await expect(avatar).to_have_attribute("data-state", "idle", timeout=10_000)
            seen = await seen_states(card)
            assert seen[0] == "still" and seen[1] == "pending", seen
            assert seen.index("pending") < seen.index("wake") < seen.index("idle"), seen
            # A list reload alone never wakes a card nobody switched.
            assert await seen_states(card_of(page, "kb_agent")) == ["idle"]
            assert len(stub.patches) == 1
            assert not diagnostics, diagnostics
            await context.close()
        finally:
            await browser.close()
    assert not stub.unexpected, stub.unexpected
    assert not site.upstream.unexpected, site.upstream.unexpected


async def test_agent_switch_failure_keeps_old_state_and_shows_reason(site: Site) -> None:
    stub = Stub(default_agents(), patch_status=409)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context, page, diagnostics = await open_agents(browser, site, stub)
            card = card_of(page, "ticket_agent")
            avatar = card.locator("[data-agent]")
            await expect(avatar).to_have_attribute("data-state", "still", timeout=WAIT)
            await watch_states(card)

            await card.get_by_role("button", name="Add to my plan", exact=True).click()
            await expect(avatar).to_have_attribute("data-state", "pending")
            stub.gate.set()

            alert = card.get_by_role("alert")
            await expect(alert).to_have_text(REASON)
            await expect(card.get_by_test_id("agent-plan-ticket_agent")).to_have_text("No")
            await expect(avatar).to_have_attribute("data-state", "still")
            await expect(card.get_by_test_id("agent-pending-ticket_agent")).to_be_empty()
            await expect(
                card.get_by_role("button", name="Add to my plan", exact=True)
            ).to_be_enabled()
            await page.wait_for_timeout(1_800)  # a wake would have started and ended by now
            assert await frames(card) == (1, "none")
            seen = await seen_states(card)
            assert seen == ["still", "pending", "still"], seen
            assert len(stub.patches) == 1
            assert not diagnostics, diagnostics
            await context.close()
        finally:
            await browser.close()
    assert not stub.unexpected, stub.unexpected


async def test_double_click_sends_one_request(site: Site) -> None:
    stub = Stub(default_agents())
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context, page, _ = await open_agents(browser, site, stub)
            card = card_of(page, "ticket_agent")
            button = card.get_by_role("button", name="Add to my plan", exact=True)
            await expect(button).to_be_enabled(timeout=WAIT)
            # Two clicks inside one task: the second lands before any request has been answered.
            await button.evaluate("el => { el.click(); el.click(); }")
            await expect(button).to_be_disabled()
            for other in await page.get_by_role(
                "button", name=re.compile(r"^Turn (on|off)$")
            ).all():
                await expect(other).to_be_disabled()
            stub.gate.set()
            await expect(card.get_by_test_id("agent-plan-ticket_agent")).to_have_text("Yes")
            await page.wait_for_timeout(500)
            assert len(stub.patches) == 1, stub.patches
            await context.close()
        finally:
            await browser.close()
    assert not stub.unexpected, stub.unexpected


async def test_unlinked_agents_page_still_renders(site: Site) -> None:
    """`/portal/agents` redirects to Services; the cards it shows are the shared AgentCard."""
    # An agent with no avatar that is not on the planned list (a future one without art yet).
    unplanned = make_agent(
        "mystery_agent", "Synthetic unplanned agent", is_enabled=False, client_enabled=False
    )
    stub = Stub({**default_agents(), "mystery_agent": unplanned})
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context()
            context.set_default_navigation_timeout(WAIT)
            await context.add_cookies(
                [{"name": "csa_session", "value": "synthetic-session", "url": site.base}]
            )
            page = await context.new_page()
            await page.route("**/console/v1/**", stub.route)
            await page.goto(f"{site.base}/portal/agents")
            await expect(page).to_have_url(f"{site.base}/portal/services")
            await page.get_by_test_id("services-agents-toggle-customer_support").click(timeout=WAIT)
            for name in ("kb_agent", "ticket_agent", "lhdn_agent"):
                await expect(card_of(page, name)).to_be_visible()
            kb = card_of(page, "kb_agent")
            await expect(
                kb.get_by_role("button", name="Remove from my plan", exact=True)
            ).to_be_enabled()
            await expect(kb.get_by_role("button", name="Turn off", exact=True)).to_be_enabled()
            await expect(
                card_of(page, "ticket_agent").get_by_role(
                    "button", name="Add to my plan", exact=True
                )
            ).to_be_enabled()
            for label in (
                "Included in your plan",
                "Turned on for your company",
                "Availability",
                "Setup",
                "Automatic safety pause",
                "Recent failures",
            ):
                await expect(kb.locator("dt", has_text=label).first).to_be_visible()
            # The planned agent has its own avatar and the word "Planned" beside the title.
            planned = card_of(page, "lhdn_agent")
            await expect(planned.get_by_text("Planned", exact=True)).to_be_visible()
            await expect(planned.locator("[data-agent]")).to_have_attribute(
                "data-agent", "einvoicing"
            )
            await expect(planned.get_by_role("button")).to_have_count(0)
            # "Planned" comes from an explicit list, and an unknown agent keeps the plain icon.
            other = card_of(page, "mystery_agent")
            await expect(other.locator("[data-agent]")).to_have_count(0)
            await expect(other.get_by_text("Planned", exact=True)).to_have_count(0)
            await expect(other.get_by_test_id("agent-status-mystery_agent")).to_have_text(
                "Not active"
            )
            # Each card title is an h4 under the service row's h3.
            await expect(kb.get_by_role("heading", level=4)).to_have_text(
                "Business knowledge agent"
            )
            await context.close()
        finally:
            await browser.close()
    assert not stub.unexpected, stub.unexpected


async def test_agent_activation_under_reduced_motion_never_animates(site: Site) -> None:
    stub = Stub(default_agents())
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context, page, _ = await open_agents(browser, site, stub, reduced_motion="reduce")
            card = card_of(page, "ticket_agent")
            avatar = card.locator("[data-agent]")
            await expect(avatar).to_have_attribute("data-state", "still", timeout=WAIT)
            await watch_states(card)
            await card.get_by_role("button", name="Add to my plan", exact=True).click()
            await expect(avatar).to_have_attribute("data-state", "pending")
            stub.gate.set()
            await expect(card.get_by_test_id("agent-plan-ticket_agent")).to_have_text("Yes")
            await expect(avatar).to_have_attribute("data-state", "idle")
            await page.wait_for_timeout(1_800)
            assert await frames(card) == (1, "none")
            seen = await seen_states(card)
            assert "wake" not in seen and seen[-1] == "idle", seen
            await context.close()
        finally:
            await browser.close()


THEMES = ("cobalt", "night", "bureau")
HALO = {"night": "rgb(219, 230, 251)", "cobalt": "rgb(21, 32, 58)", "bureau": "rgb(21, 32, 58)"}
TRANSPARENT = "rgba(0, 0, 0, 0)"


async def test_no_avatar_has_a_background_or_radius(site: Site) -> None:
    """Every avatar and every parent up to the card is transparent, square and unclipped."""
    stub = Stub(default_agents())
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            for theme in THEMES:
                context, page, diagnostics = await open_agents(browser, site, stub, theme=theme)
                await expect(page.locator("html")).to_have_attribute("data-theme", theme)
                found = await page.evaluate(
                    """() => {
                        const card = '[data-testid^="agent-card-"]';
                        return [...document.querySelectorAll(card + ' [data-agent]')]
                        .map(el => {
                            const boxes = [];
                            for (let node = el; node && !node.matches(card);
                                 node = node.parentElement) {
                                const style = getComputedStyle(node);
                                const id = node.dataset.agent;
                                const agent = id ? '[' + id + ']' : '';
                                boxes.push({
                                    tag: node.tagName.toLowerCase() + agent,
                                    background: style.backgroundColor + ' ' + style.backgroundImage,
                                    radius: style.borderRadius,
                                    overflow: style.overflow,
                                    border: style.borderTopWidth,
                                });
                            }
                            return boxes;
                        });
                    }"""
                )
                assert len(found) >= 2, (theme, found)
                for boxes in found:
                    assert boxes[0]["tag"].startswith("span["), boxes
                    for box in boxes:
                        assert box["background"] == f"{TRANSPARENT} none", (theme, box)
                        assert box["radius"] == "0px", (theme, box)
                        assert box["overflow"] == "visible", (theme, box)
                        assert box["border"] == "0px", (theme, box)
                assert not diagnostics, diagnostics
                await context.close()
        finally:
            await browser.close()
    assert not stub.unexpected, stub.unexpected


async def test_avatar_halo_present_on_every_theme_but_not_when_dim(site: Site) -> None:
    """Every theme outlines the sprite with four one-cell drop-shadows (light on night, navy on
    cobalt and bureau); the dim state has none."""
    stub = Stub(default_agents())
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            for theme in THEMES:
                context, page, diagnostics = await open_agents(browser, site, stub, theme=theme)
                await expect(page.locator("html")).to_have_attribute("data-theme", theme)
                live = card_of(page, "kb_agent").locator("[data-agent]")
                dim = card_of(page, "ticket_agent").locator("[data-agent]")
                await expect(live).to_have_attribute("data-state", "idle", timeout=WAIT)
                await expect(dim).to_have_attribute("data-state", "still")
                halo = await live.evaluate("el => getComputedStyle(el).filter")
                shadows = re.findall(r"drop-shadow\((rgba?\([^)]*\)) (\S+) (\S+) (\S+)\)", halo)
                assert len(shadows) == 4, (theme, halo)
                # 48 px on the 48 grid: one cell is one pixel; the four offsets point four ways,
                # crisp (no blur).
                assert sorted(f"{x} {y}" for _, x, y, _ in shadows) == sorted(
                    ["1px 0px", "-1px 0px", "0px 1px", "0px -1px"]
                ), (theme, halo)
                assert {blur for *_, blur in shadows} == {"0px"}, (theme, halo)
                assert {c for c, *_ in shadows} == {HALO[theme]}, (theme, halo)
                # Dimmed (not active): no halo at all on any theme.
                assert await dim.evaluate("el => getComputedStyle(el).filter") == "none", theme
                assert not diagnostics, diagnostics
                await context.close()
        finally:
            await browser.close()
    assert not stub.unexpected, stub.unexpected


async def test_einvoicing_avatar_renders_in_agent_card(site: Site) -> None:
    """The planned LHDN agent card draws the twelfth avatar (the Services row is tested apart)."""
    stub = Stub(agents=default_agents())
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context, page, diagnostics = await open_agents(browser, site, stub)
            card = card_of(page, "lhdn_agent")
            avatar = card.locator("[data-agent]")
            await expect(avatar).to_have_attribute("data-agent", "einvoicing", timeout=WAIT)
            await expect(avatar.locator("svg g.fs-px-frame").first).to_be_attached()
            assert await avatar.locator("svg rect").count() > 40, "the sprite has real cells"
            box = await avatar.bounding_box()
            assert box is not None and box["width"] == 48 and box["height"] == 48, box
            await expect(card.get_by_test_id("agent-planned-lhdn_agent")).to_have_text("Planned")
            assert not diagnostics, diagnostics
            await context.close()
        finally:
            await browser.close()
