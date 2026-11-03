"""Real browser acceptance for package-driven navigation and the Services tab.

The real Next.js shell runs unmodified from a disposable staged copy. Its layouts
fetch `/auth/me` and `/services/active` server-side, so a loopback stub origin
answers those; every browser-side `/console/v1/*` call is answered by Playwright.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.async_api import Page, async_playwright, expect

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
GATED = (
    "Customer inbox",
    "Business knowledge",
    "Business management",
    "People & HR",
    "Email assistant",
)


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


def make_service(key: str, title: str, kind: str, status: str, **extra: Any) -> dict[str, Any]:
    service: dict[str, Any] = {
        "key": key,
        "title": title,
        "summary": f"Synthetic {title} package.",
        "kind": kind,
        "requires": [] if kind == "base" else ["customer_support"],
        "sellable": True,
        "tabs": [],
        "status": status,
        "reference": None,
        "note": None,
        "requested_at": None,
        "activated_at": None,
        "requested_by": None,
        "activated_by": None,
        "agents": [],
        "inner_switch": None,
        "consistent": True,
        "inconsistencies": [],
        "can_request": False,
        "can_activate": False,
        "can_suspend": False,
    }
    service.update(extra)
    return service


class Upstream:
    """Loopback stand-in for the gateway origin used by server-side layout fetches."""

    def __init__(self) -> None:
        self.me = make_me("tenant_admin")
        self.packages: list[dict[str, str]] = []
        self.seen: list[tuple[str, str]] = []
        self.unexpected: list[str] = []
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = self.path.split("?")[0]
                upstream.seen.append((path, self.headers.get("cookie", "")))
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


async def test_navigation_and_services_tab_follow_package_state(tmp_path: Path) -> None:
    node = shutil.which("node")
    assert node, "Node is required for browser services acceptance"
    assert (WEB / "node_modules/next/dist/bin/next").is_file(), (
        "Console dependencies are missing; run `npm ci --ignore-scripts` in "
        "CSA_WebConsole/csa-console/web before browser services acceptance"
    )
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
            async with httpx.AsyncClient(trust_env=False) as http:
                for _ in range(120):
                    assert process.poll() is None, log_path.read_text()
                    try:
                        if (
                            await http.get(f"http://127.0.0.1:{port}/login", timeout=2)
                        ).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.5)
                else:
                    pytest.fail(f"Synthetic services frontend did not start; log: {log_path}")
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                try:
                    await exercise(browser, f"http://127.0.0.1:{port}", upstream, tmp_path)
                finally:
                    await browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.close()


async def expect_navigation(page: Page, present: tuple[str, ...], absent: tuple[str, ...]) -> None:
    nav = page.get_by_role("navigation", name="Main navigation")
    for label in present:
        await expect(nav.get_by_role("link", name=label, exact=True)).to_have_count(1, timeout=WAIT)
    for label in absent:
        await expect(nav.get_by_role("link", name=label, exact=True)).to_have_count(0, timeout=WAIT)


async def exercise(browser: Any, base: str, upstream: Upstream, tmp_path: Path) -> None:
    context = await browser.new_context()
    context.set_default_navigation_timeout(WAIT)
    await context.add_cookies([{"name": "csa_session", "value": "synthetic-session", "url": base}])
    await context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = await context.new_page()
    diagnostics: list[str] = []
    page.on("crash", lambda: diagnostics.append("Chromium page crashed"))
    page.on("pageerror", lambda error: diagnostics.append(str(error)))
    page.on("console", lambda message: diagnostics.append(f"{message.type}: {message.text}"))

    services = {
        "customer_support": make_service("customer_support", "Customer Support", "base", "active"),
        "orders_products": make_service(
            "orders_products", "Orders and Products", "addon", "available", can_request=True
        ),
    }
    admin_services = {
        "customer_support": make_service(
            "customer_support", "Customer Support", "base", "requested", can_activate=True
        ),
        "orders_products": make_service(
            "orders_products", "Orders and Products", "addon", "available"
        ),
        # Backfilled staging state: an unsellable package can still be active.
        "marketplaces": make_service(
            "marketplaces", "Marketplaces", "addon", "active", sellable=False, can_suspend=True
        ),
        "e_invoicing": make_service(
            "e_invoicing", "E-invoicing", "addon", "available", sellable=False
        ),
    }
    calls: list[dict[str, Any]] = []
    unexpected: list[str] = []
    replies: dict[str, Any] = {
        "mode": "off",
        "brand_name": None,
        "can_manage": True,
        "warnings": ["replies_off", "base_only"],
    }
    replies_status = {"read": 200}

    async def route(request_route: Any) -> None:
        request = request_route.request
        path, _, query = request.url.split("/console/v1/")[1].partition("?")
        calls.append(
            {
                "method": request.method,
                "path": path,
                "query": query,
                "csrf": request.headers.get("x-csrf-token"),
                "body": request.post_data_json if request.method in {"POST", "PUT"} else None,
            }
        )
        platform = upstream.me["role"] == "platform_admin"
        rows = admin_services if platform else services
        status, data = 200, None
        if path == "auth/csrf":
            data = {"csrf_token": "synthetic-csrf"}
        elif path == "admin/clients" and platform:
            data = [TENANT_PROFILE]
        elif path == "hr/context" and request.method == "GET":
            # The rail asks for the HR tabs on pages that name a tenant; unused here.
            status, data = 404, {"detail": "not stubbed"}
        elif path == "services" and request.method == "GET":
            data = list(rows.values())
        elif path == "services/orders_products/request" and not platform:
            rows["orders_products"] = {
                **rows["orders_products"],
                "status": "requested",
                "requested_at": "2026-10-25T00:00:00Z",
                "can_request": False,
            }
            data = rows["orders_products"]
        elif path == "services/customer_support/activate" and platform:
            rows["customer_support"] = {
                **rows["customer_support"],
                "status": "active",
                "reference": request.post_data_json["reference"],
                "activated_at": "2026-10-25T00:00:00Z",
                "can_activate": False,
                "can_suspend": True,
            }
            # The base is active now, so the add-on becomes activatable on the next list.
            rows["orders_products"] = {**rows["orders_products"], "can_activate": True}
            data = rows["customer_support"]
        elif path == "services/customer-replies" and request.method == "GET":
            if {"key": "customer_support", "status": "active"} not in upstream.packages:
                status, data = 403, {"detail": "package_inactive"}
            elif replies_status["read"] != 200:
                status, data = replies_status["read"], {"detail": "synthetic failure"}
            else:
                data = replies
        elif path == "services/customer-replies" and request.method == "PUT":
            body = request.post_data_json
            if body["mode"] != "off" and body["brand_name"] == "Refused":
                status, data = 422, {"detail": "brand_name_required"}
            else:
                replies.update(mode=body["mode"], brand_name=body["brand_name"], warnings=[])
                data = replies
        elif path in {"inbox/conversations", "inbox/approvals"}:
            if {"key": "customer_support", "status": "active"} in upstream.packages:
                data = []
            else:
                status, data = 403, {"detail": "package_inactive"}
        elif path == "services/active" and request.method == "GET":
            # Overview's status strip (Overview is the assistant home).
            data = {"packages": upstream.packages}
        elif path == "chat/conversations" and request.method == "GET":
            # Overview's sandbox chat history.
            data = {"items": [], "next_cursor": None}
        elif path == "channels/whatsapp" and request.method == "GET":
            # The Services WhatsApp card; the gateway gates it on Customer Support.
            if {"key": "customer_support", "status": "active"} in upstream.packages:
                data = {
                    "connected": False,
                    "display_phone": None,
                    "phone_number_id_last4": None,
                    "waba_id": None,
                    "enabled": False,
                    "callback_url": None,
                    "connected_at": None,
                }
            else:
                status, data = 403, {"detail": "package_inactive"}
        else:
            unexpected.append(f"{request.method} {path}?{query}")
            status, data = 500, {"detail": "unexpected synthetic call"}
        await request_route.fulfill(status=status, json=data)

    try:
        await page.route("**/console/v1/**", route)

        # (a) A tenant admin with no active package sees no gated tab; Services replaced Agents.
        upstream.me, upstream.packages = make_me("tenant_admin"), []
        await page.goto(f"{base}/portal")
        await expect_navigation(page, ("Overview", "Services"), (*GATED, "Agents"))
        assert ("/console/v1/services/active", "csa_session=synthetic-session") in upstream.seen

        # (b) Customer Support active unlocks exactly its tabs; Business knowledge is an add-on.
        upstream.packages = [{"key": "customer_support", "status": "active"}]
        await page.reload()
        await expect_navigation(
            page,
            ("Customer inbox", "Services"),
            (
                "Business knowledge",
                "Business management",
                "People & HR",
                "Email assistant",
                "Agents",
            ),
        )
        upstream.packages = [
            {"key": "customer_support", "status": "active"},
            {"key": "knowledge_base", "status": "active"},
        ]
        await page.reload()
        await expect_navigation(
            page,
            ("Customer inbox", "Business knowledge", "Services"),
            ("Business management", "People & HR", "Email assistant", "Agents"),
        )
        upstream.packages = [{"key": "customer_support", "status": "active"}]
        await page.reload()

        # (c) Requesting a package posts with CSRF and the card follows the returned Service.
        await page.goto(f"{base}/portal/services")
        card = page.locator('[data-testid="services-card-orders_products"]')
        await expect(card.get_by_test_id("services-status-orders_products")).to_have_text(
            "Available", timeout=WAIT
        )
        await card.get_by_test_id("services-request-orders_products").click()
        await expect(card.get_by_test_id("services-status-orders_products")).to_have_text(
            "Requested"
        )
        await expect(card.get_by_test_id("services-request-orders_products")).to_have_count(0)
        posts = [c for c in calls if c["method"] == "POST"]
        assert posts == [
            {
                "method": "POST",
                "path": "services/orders_products/request",
                "query": "",
                "csrf": "synthetic-csrf",
                "body": {},
            }
        ]

        # (d) A platform admin sees every tab regardless of packages.
        upstream.me, upstream.packages = make_me("platform_admin"), []
        await page.goto(f"{base}/services?client_id={TENANT}")
        await expect_navigation(page, (*GATED, "Services", "Tenants"), ("Agents",))

        # (e) Activation sends exactly the reference and a null note for the chosen tenant.
        await expect(page.get_by_test_id("services-tenant")).to_have_value(TENANT, timeout=WAIT)
        # A real status wins over "Coming soon"; only an available unsellable package shows it.
        await expect(page.get_by_test_id("services-status-marketplaces")).to_have_text(
            "Active", timeout=WAIT
        )
        await expect(page.get_by_test_id("services-status-e_invoicing")).to_have_text("Coming soon")
        await expect(page.get_by_test_id("services-activate-orders_products")).to_have_count(0)
        card = page.locator('[data-testid="services-card-customer_support"]')
        await card.get_by_test_id("services-activate-customer_support").click(timeout=WAIT)
        await card.get_by_test_id("services-activate-reference").fill("Q-1")
        await card.get_by_test_id("services-activate-confirm").click()
        await expect(card.get_by_test_id("services-status-customer_support")).to_have_text("Active")
        await expect(card.get_by_test_id("services-activation-customer_support")).to_contain_text(
            "Reference: Q-1"
        )
        # The whole list was reloaded, so the add-on card follows the new base state.
        await expect(page.get_by_test_id("services-activate-orders_products")).to_be_visible()
        activations = [c for c in calls if c["path"] == "services/customer_support/activate"]
        assert activations == [
            {
                "method": "POST",
                "path": "services/customer_support/activate",
                "query": f"client_id={TENANT}",
                "csrf": "synthetic-csrf",
                "body": {"reference": "Q-1", "note": None},
            }
        ]

        # (f) A gated page opened directly shows the API's refusal and no conversations.
        upstream.me, upstream.packages = make_me("tenant_admin"), []
        # Overview's status strip also reads the Inbox, so count only this page's calls.
        start = len(calls)
        await page.goto(f"{base}/portal/inbox")
        await expect(
            page.get_by_text("not part of your organisation's active service packages")
        ).to_be_visible(timeout=WAIT)
        await expect(page.get_by_test_id("inbox-channel-badge")).to_have_count(0)
        await expect_navigation(page, ("Services",), GATED)
        assert any(c["path"] == "inbox/conversations" for c in calls[start:])

        # (g) Replies are off: the banner shows on Overview (above the chat) and Inbox, and points
        # to Services, which is where the panel lives.
        support = [{"key": "customer_support", "status": "active"}]
        upstream.me, upstream.packages = make_me("tenant_admin"), support
        banner = page.get_by_test_id("customer-replies-banner")
        chat_heading = page.get_by_role("heading", name="How can I help today?")
        await page.goto(f"{base}/portal")
        await expect(chat_heading).to_be_visible(timeout=WAIT)
        await expect(banner).to_contain_text(
            "Your customers aren't getting replies yet.", timeout=WAIT
        )
        await expect(banner.get_by_role("link", name="Go to Services")).to_have_attribute(
            "href", "/portal/services"
        )
        # A platform admin's link carries the chosen company, and its read names it too.
        upstream.me = make_me("platform_admin")
        await page.goto(f"{base}/?client_id={TENANT}")
        await expect(banner.get_by_role("link", name="Go to Services")).to_have_attribute(
            "href", f"/services?client_id={TENANT}", timeout=WAIT
        )
        assert any(
            c["path"] == "services/customer-replies" and c["query"] == f"client_id={TENANT}"
            for c in calls
        )
        upstream.me = make_me("tenant_admin")
        await page.goto(f"{base}/portal/inbox")
        await expect(banner).to_contain_text(
            "Your customers aren't getting replies yet.", timeout=WAIT
        )

        # (h) The panel: exact choices, the warning, Save blocked until there is a name.
        await page.goto(f"{base}/portal/services")
        panel = page.get_by_test_id("customer-replies-panel")
        # The panel sits in the Core service group, directly under the Customer support card.
        base_group = page.get_by_test_id("services-group-base")
        await expect(base_group.get_by_test_id("customer-replies-panel")).to_be_visible(
            timeout=WAIT
        )
        order = await base_group.locator("> ul > *").evaluate_all(
            "(items) => items.map((item) => item.dataset.testid)"
        )
        assert order == ["services-card-customer_support", "services-customer-replies"], order
        await expect(panel).to_contain_text(
            "With Customer support alone, most customer questions go to your team.", timeout=WAIT
        )
        for label in (
            "Off: customers get no reply",
            "Staff approve each reply: a draft waits up to 24 hours, then your team gets a "
            "ticket and the customer is told you'll follow up",
            "Bot replies by itself: when it can't answer, it hands over to your team",
        ):
            await expect(panel.get_by_text(label)).to_be_visible()
        await expect(panel.get_by_text("The name your bot uses with customers")).to_be_visible()
        save = page.get_by_test_id("customer-replies-save")
        await page.get_by_test_id("customer-replies-auto").check()
        await expect(save).to_be_disabled()
        await expect(panel.get_by_text("Add your brand name first.")).to_be_visible()

        # (i) A refusal shows its sentence, and a good save shows the returned state.
        await page.get_by_test_id("customer-replies-brand").fill("Refused")
        await save.click()
        await expect(
            panel.get_by_text("Add your brand name before turning replies on.")
        ).to_be_visible()
        await page.get_by_test_id("customer-replies-brand").fill("  Team Synthetic ")
        await save.click()
        await expect(panel.get_by_role("status")).to_have_text("Saved.")
        await expect(page.get_by_test_id("customer-replies-brand")).to_have_value("Team Synthetic")
        saves = [c for c in calls if c["path"] == "services/customer-replies" and c["body"]]
        assert saves[-1] == {
            "method": "PUT",
            "path": "services/customer-replies",
            "query": "",
            "csrf": "synthetic-csrf",
            "body": {"mode": "auto", "brand_name": "Team Synthetic"},
        }

        # (j) Replies on: no banner, with no new sign-in.
        await page.goto(f"{base}/portal")
        await expect(chat_heading).to_be_visible(timeout=WAIT)
        await expect(banner).to_have_count(0)

        # (k) Staff see the state and no controls, and are told who can change it.
        replies.update(mode="off", can_manage=False, warnings=["replies_off"])
        upstream.me = make_me("tenant_staff")
        await page.goto(f"{base}/portal")
        await expect(chat_heading).to_be_visible(timeout=WAIT)
        await expect(banner).to_contain_text("Ask your admin to turn replies on.", timeout=WAIT)
        await expect(banner.get_by_role("link")).to_have_count(0)
        await page.goto(f"{base}/portal/services")
        state = page.get_by_test_id("customer-replies-state")
        await expect(state).to_contain_text("Ask your admin to change this.", timeout=WAIT)
        await expect(page.get_by_test_id("customer-replies-save")).to_have_count(0)

        # (l) A refused or failed read shows no banner and no error, and the page still works.
        replies_status["read"] = 500
        await page.goto(f"{base}/portal")
        await expect(chat_heading).to_be_visible(timeout=WAIT)
        await expect(banner).to_have_count(0)
        # A company without Customer support has no customer chat: Overview is the small home,
        # and the replies banner stays out of it.
        upstream.packages = []
        replies_status["read"] = 200
        await page.goto(f"{base}/portal")
        await expect(page.get_by_role("heading", name="Your workspace")).to_be_visible(timeout=WAIT)
        await expect(
            page.get_by_text("Ask the assistant at the side of the screen.")
        ).to_be_visible()
        await expect(chat_heading).to_have_count(0)
        await expect(banner).to_have_count(0)

        assert not unexpected, unexpected
        assert not upstream.unexpected, upstream.unexpected
    except Exception as exc:
        exc.add_note(
            f"Browser diagnostics: {diagnostics}; trace: {tmp_path / 'services-trace.zip'}; "
            f"frontend log: {tmp_path / 'frontend.log'}"
        )
        raise
    finally:
        (tmp_path / "services-browser.log").write_text("\n".join(diagnostics))
        with contextlib.suppress(Exception):  # Preserve the original failure if Chromium crashed.
            await context.tracing.stop(path=tmp_path / "services-trace.zip")
        await context.close()
