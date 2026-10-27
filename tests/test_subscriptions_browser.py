"""Real browser acceptance for the Subscriptions tab.

The real Next.js shell runs unmodified from a disposable staged copy, as in
`test_services_browser.py`: its layouts fetch `/auth/me` and `/services/active`
server-side from a loopback stub, and Playwright answers every browser-side
`/console/v1/**` call. Any call the stub does not expect fails the test.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from playwright.async_api import Page, async_playwright, expect

from tests.test_services_browser import (
    ROOT,
    TENANT,
    TENANT_PROFILE,
    WAIT,
    WEB,
    Upstream,
    expect_navigation,
    free_port,
    make_me,
)

pytestmark = pytest.mark.asyncio

AISYAH = "33333333-3333-4333-8333-333333333333"
BEN = "33333333-3333-4333-8333-444444444444"
PROPOSAL = "55555555-5555-4555-8555-555555555555"
REQUEST_PAUSE = "66666666-6666-4666-8666-666666666666"
REQUEST_ORPHAN = "66666666-6666-4666-8666-777777777777"
ACTIVE = [{"key": "subscriptions", "status": "active"}]

PLANS = [
    {
        "code": "basic",
        "name": "Basic",
        "price": "59.90",
        "currency": "MYR",
        "billing_interval": "month",
        "features": ["Weekly delivery"],
        "sort_order": 0,
        "is_active": True,
    },
    {
        "code": "pro",
        "name": "Pro",
        "price": "99.00",
        "currency": "MYR",
        "billing_interval": "month",
        "features": [],
        "sort_order": 1,
        "is_active": True,
    },
]


def customer(contact: str, name: str, phone: str) -> dict[str, Any]:
    return {"id": contact, "full_name": name, "phone": phone, "email": None}


def subscription(sub_id: str, who: dict[str, Any], plan: str, price: str) -> dict[str, Any]:
    return {
        "id": sub_id,
        "customer": who,
        "plan": plan,
        "plan_name": plan.title(),
        "status": "active",
        "price": price,
        "currency": "MYR",
        "started_at": "2026-09-27T01:00:00Z",
        "renews_at": "2026-11-27T01:00:00Z",
        "paused_at": None,
        "cancelled_at": None,
        "resume_at": None,
        "pending_plan": None,
        "pending_effective_at": None,
        "cancel_at_period_end": False,
        "version": 1,
    }


def request_row(req_id: str, sub_id: str | None, who: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": req_id,
        "subscription_id": sub_id,
        "customer": who,
        "action": "request_pause",
        "plan_code": None,
        "reason": "Travelling in November",
        "requested_at": "2026-10-26T02:30:00Z",
        "decision": None,
        "decided_at": None,
        "decided_by": None,
        "note": None,
        "proposal_id": None,
    }


class World:
    """Synthetic subscriptions state behind the Playwright route."""

    def __init__(self, upstream: Upstream) -> None:
        self.upstream = upstream
        aisyah = customer("77777777-7777-4777-8777-777777777777", "Aisyah Rahman", "+60123456789")
        ben = customer("77777777-7777-4777-8777-888888888888", "Ben Tan", "+60198765432")
        self.subs = {
            AISYAH: subscription(AISYAH, aisyah, "basic", "59.90"),
            BEN: subscription(BEN, ben, "pro", "99.00"),
        }
        self.events: dict[str, list[dict[str, Any]]] = {
            sub_id: [
                {
                    "id": index + 1,
                    "actor": "22222222-2222-4222-8222-222222222222",
                    "action": "created",
                    "from_status": None,
                    "to_status": "active",
                    "payload": {"plan": row["plan"]},
                    "created_at": "2026-09-27T01:00:00Z",
                }
            ]
            for index, (sub_id, row) in enumerate(self.subs.items())
        }
        self.requests = [
            request_row(REQUEST_PAUSE, BEN, ben),
            request_row(REQUEST_ORPHAN, None, aisyah),
        ]
        self.plans = copy.deepcopy(PLANS)
        self.proposal: dict[str, Any] | None = None
        self.calls: list[dict[str, Any]] = []
        self.unexpected: list[str] = []

    async def route(self, request_route: Any) -> None:
        request = request_route.request
        path, _, query = request.url.split("/console/v1/")[1].partition("?")
        params = {key: values[0] for key, values in parse_qs(query).items()}
        body = request.post_data_json if request.method in {"POST", "PATCH"} else None
        self.calls.append(
            {
                "method": request.method,
                "path": path,
                "params": params,
                "csrf": request.headers.get("x-csrf-token"),
                "body": body,
            }
        )
        status, data = self.answer(request.method, path, params, body)
        await request_route.fulfill(status=status, json=data)

    def answer(self, method: str, path: str, params: dict[str, str], body: Any) -> tuple[int, Any]:
        platform = self.upstream.me["role"] == "platform_admin"
        if path == "auth/csrf":
            return 200, {"csrf_token": "synthetic-csrf"}
        if path == "admin/clients" and platform:
            return 200, [TENANT_PROFILE]
        # Platform admins must always name the tenant; members never do.
        if path.startswith("subscriptions") and (params.get("client_id") == TENANT) != platform:
            self.unexpected.append(f"{method} {path} with params {params}")
            return 400, {"detail": "tenant mismatch"}
        writer = self.upstream.me["role"] != "read_only"
        if path == "subscriptions/plans" and method == "GET":
            return 200, self.plans
        if path.startswith("subscriptions/plans/") and method == "PATCH" and writer:
            plan = next(p for p in self.plans if p["code"] == path.rsplit("/", 1)[1])
            plan.update({k: v for k, v in body.items() if v is not None})
            return 200, plan
        if path == "subscriptions/requests" and method == "GET":
            decided = params.get("status") == "decided"
            rows = [r for r in self.requests if (r["decision"] is not None) == decided]
            return 200, {"items": rows, "next_cursor": None}
        if path == f"subscriptions/requests/{REQUEST_PAUSE}/decide" and method == "POST" and writer:
            row = self.requests[0]
            row.update(
                decision="approved",
                decided_at="2026-10-27T03:00:00Z",
                note=body.get("note"),
                proposal_id=PROPOSAL,
            )
            self.subs[BEN].update(status="paused", resume_at=body.get("resume_at"), version=2)
            return 200, row
        if path == "subscriptions" and method == "GET":
            q = params.get("q", "").lower()
            rows = [
                row
                for row in self.subs.values()
                if q in row["customer"]["full_name"].lower()
                and params.get("status", row["status"]) == row["status"]
                and params.get("plan", row["plan"]) == row["plan"]
            ]
            return 200, {"items": rows, "next_cursor": None}
        if path in {f"subscriptions/{AISYAH}", f"subscriptions/{BEN}"} and method == "GET":
            sub_id = path.split("/")[1]
            return 200, {"subscription": self.subs[sub_id], "events": self.events[sub_id]}
        if path == f"subscriptions/{AISYAH}/actions" and method == "POST" and writer:
            self.proposal = {
                "id": PROPOSAL,
                "kind": "subscription_action",
                "status": "requested",
                "card": {
                    "proposal_id": PROPOSAL,
                    "kind": "subscription_action",
                    "title": "Pause subscription",
                    "summary": "Aisyah Rahman · Basic · RM 59.90 a month",
                    "fields": [
                        {"label": "Customer", "value": "Aisyah Rahman"},
                        {"label": "Change", "value": "Pause now until someone resumes it."},
                    ],
                    "expires_at": "2026-10-27T10:00:00Z",
                    "expected_version": "1",
                },
                "result": None,
                "created_at": "2026-10-27T01:00:00Z",
                "expires_at": "2026-10-27T10:00:00Z",
            }
            return 201, self.proposal
        if (
            path == f"workspace/proposals/{PROPOSAL}/confirm"
            and method == "POST"
            and self.proposal is not None
        ):
            self.subs[AISYAH].update(status="paused", paused_at="2026-10-27T01:05:00Z", version=2)
            self.events[AISYAH].append(
                {
                    "id": 10,
                    "actor": "22222222-2222-4222-8222-222222222222",
                    "action": "paused",
                    "from_status": "active",
                    "to_status": "paused",
                    "payload": {"resume_at": None, "reason": None, "version": 2},
                    "created_at": "2026-10-27T01:05:00Z",
                }
            )
            self.proposal = {
                **self.proposal,
                "status": "executed",
                "result": {"subscription_id": AISYAH, "status": "paused", "version": 2},
            }
            return 200, self.proposal
        self.unexpected.append(f"{method} {path}?{params}")
        return 500, {"detail": "unexpected synthetic call"}


async def test_subscriptions_tab_actions_requests_and_roles(tmp_path: Path) -> None:
    node = shutil.which("node")
    assert node, "Node is required for browser subscriptions acceptance"
    assert (WEB / "node_modules/next/dist/bin/next").is_file(), (
        "Console dependencies are missing; run `npm ci --ignore-scripts` in "
        "CSA_WebConsole/csa-console/web before browser subscriptions acceptance"
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
                    pytest.fail(f"Synthetic subscriptions frontend did not start; log: {log_path}")
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


async def open_customer(page: Page, name: str) -> Any:
    await page.get_by_test_id("subscription-row").filter(has_text=name).click()
    drawer = page.get_by_test_id("subscription-drawer")
    await expect(drawer.get_by_role("heading", name=name)).to_be_visible(timeout=WAIT)
    return drawer


async def exercise(browser: Any, base: str, upstream: Upstream, tmp_path: Path) -> None:
    context = await browser.new_context(timezone_id="Asia/Kuala_Lumpur")
    context.set_default_navigation_timeout(WAIT)
    await context.add_cookies([{"name": "csa_session", "value": "synthetic-session", "url": base}])
    await context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = await context.new_page()
    diagnostics: list[str] = []
    page.on("crash", lambda: diagnostics.append("Chromium page crashed"))
    page.on("pageerror", lambda error: diagnostics.append(str(error)))
    page.on("console", lambda message: diagnostics.append(f"{message.type}: {message.text}"))
    world = World(upstream)
    try:
        await page.route("**/console/v1/**", world.route)

        # (a) Staff with the package see the link and land on the Customers tab.
        upstream.me, upstream.packages = make_me("tenant_staff"), ACTIVE
        await page.goto(f"{base}/portal")
        await expect_navigation(page, ("Subscriptions", "Services"), ())
        nav = page.get_by_role("navigation", name="Main navigation")
        await nav.get_by_role("link", name="Subscriptions", exact=True).click()
        await page.wait_for_url(f"{base}/portal/subscriptions", timeout=WAIT)
        rows = page.get_by_test_id("subscription-row")
        await expect(rows).to_have_count(2, timeout=WAIT)
        await expect(rows.first).to_contain_text("RM 59.90")
        await expect(rows.first).to_contain_text("27 Nov 2026")

        # (b) Searching narrows the table to one customer.
        search = page.get_by_placeholder("Search by name, phone or email")
        await search.fill("aisyah")
        await page.get_by_test_id("subscriptions-search-submit").click()
        await expect(rows).to_have_count(1)
        await expect(rows.first).to_contain_text("Aisyah Rahman")
        searches = [c for c in world.calls if c["path"] == "subscriptions" and c["method"] == "GET"]
        assert searches[-1]["params"] == {"q": "aisyah"}, searches

        # (c) Pause goes through a card; confirming it calls confirm and the row follows.
        drawer = await open_customer(page, "Aisyah Rahman")
        await expect(drawer.get_by_test_id("subscription-timeline")).to_contain_text(
            "Subscription started on the Basic plan."
        )
        await expect(drawer.get_by_test_id("subscription-action-resume")).to_have_count(0)
        await drawer.get_by_role("button", name="Pause subscription").click()
        await drawer.get_by_test_id("action-submit").click()
        card = drawer.get_by_test_id("proposal-card")
        await expect(card).to_contain_text("Pause now until someone resumes it.")
        assert not any(c["path"].endswith("/confirm") for c in world.calls)
        await card.get_by_test_id("proposal-confirm").click()
        await expect(card.get_by_test_id("proposal-outcome")).to_contain_text("now paused")
        await expect(rows.first.get_by_test_id("subscription-status")).to_have_text("Paused")
        await expect(drawer.get_by_test_id("subscription-detail-status")).to_have_text("Paused")
        await expect(
            drawer.get_by_test_id("subscription-timeline").locator("li").first
        ).to_contain_text("Paused until someone resumes it.")
        await expect(drawer.get_by_test_id("subscription-action-resume")).to_be_visible()
        writes = [c for c in world.calls if c["method"] != "GET" and c["path"] != "auth/csrf"]
        assert writes == [
            {
                "method": "POST",
                "path": f"subscriptions/{AISYAH}/actions",
                "params": {},
                "csrf": "synthetic-csrf",
                "body": {"action": "pause", "reason": None},
            },
            {
                "method": "POST",
                "path": f"workspace/proposals/{PROPOSAL}/confirm",
                "params": {},
                "csrf": "synthetic-csrf",
                "body": None,
            },
        ], writes
        await drawer.get_by_test_id("subscription-close").click()

        # (d) The Requests tab approves a pause with a resume date in one decide call.
        await page.get_by_test_id("subscriptions-tab-requests").click()
        open_list = page.get_by_test_id("requests-open")
        await expect(open_list.get_by_test_id("request-open")).to_have_count(2, timeout=WAIT)
        orphan = open_list.get_by_test_id("request-open").filter(has_text="Aisyah Rahman")
        await expect(orphan.get_by_test_id("request-approve")).to_have_count(0)
        await expect(orphan).to_contain_text("can only be declined")
        pause = open_list.get_by_test_id("request-open").filter(has_text="Ben Tan")
        await pause.get_by_test_id("request-approve").click()
        await pause.get_by_test_id("request-resume-at").fill("2026-11-15")
        await pause.get_by_role("button", name="Approve request").click()
        await expect(
            page.get_by_text("Approved. Ben Tan's subscription has been updated.")
        ).to_be_visible()
        await expect(open_list.get_by_test_id("request-open")).to_have_count(1)
        decided = page.get_by_test_id("requests-decided").get_by_test_id("request-decided")
        await expect(decided).to_have_count(1)
        await expect(decided).to_contain_text("Approved")
        decides = [c for c in world.calls if c["path"].endswith("/decide")]
        assert decides == [
            {
                "method": "POST",
                "path": f"subscriptions/requests/{REQUEST_PAUSE}/decide",
                "params": {},
                "csrf": "synthetic-csrf",
                "body": {"decision": "approve", "note": None, "resume_at": "2026-11-15"},
            }
        ], decides

        # (e) Plans list the price in the guide's format; Archive patches is_active only.
        await page.get_by_test_id("subscriptions-tab-plans").click()
        basic = page.get_by_test_id("plan-row-basic")
        await expect(basic).to_contain_text("RM 59.90 a month", timeout=WAIT)
        await basic.get_by_test_id("plan-archive-basic").click()
        await expect(basic).to_contain_text("Archived")
        patches = [c for c in world.calls if c["method"] == "PATCH"]
        assert [(c["path"], c["body"]) for c in patches] == [
            ("subscriptions/plans/basic", {"is_active": False})
        ]

        # (f) Read-only members see everything with every action control hidden.
        upstream.me = make_me("read_only")
        before = len([c for c in world.calls if c["method"] != "GET"])
        await page.goto(f"{base}/portal/subscriptions")
        await expect(rows).to_have_count(2, timeout=WAIT)
        drawer = await open_customer(page, "Ben Tan")
        await expect(drawer.get_by_test_id("subscription-timeline")).to_be_visible()
        await expect(drawer.get_by_test_id("subscription-actions")).to_have_count(0)
        await expect(drawer.get_by_role("button", name="Pause subscription")).to_have_count(0)
        await drawer.get_by_test_id("subscription-close").click()
        await page.get_by_test_id("subscriptions-tab-requests").click()
        await expect(page.get_by_test_id("request-open")).to_have_count(1, timeout=WAIT)
        await expect(page.get_by_test_id("request-approve")).to_have_count(0)
        await expect(page.get_by_test_id("request-decline")).to_have_count(0)
        await page.get_by_test_id("subscriptions-tab-plans").click()
        await expect(page.get_by_test_id("plan-row-pro")).to_be_visible(timeout=WAIT)
        await expect(page.get_by_test_id("plan-add")).to_have_count(0)
        await expect(page.get_by_test_id("plan-edit-pro")).to_have_count(0)
        assert len([c for c in world.calls if c["method"] != "GET"]) == before

        # (g) Without the package the link is absent.
        upstream.me, upstream.packages = make_me("tenant_staff"), []
        await page.goto(f"{base}/portal")
        await expect_navigation(page, ("Services",), ("Subscriptions",))

        # (h) A platform admin names the tenant with ?client_id= and every call carries it.
        upstream.me = make_me("platform_admin")
        start = len(world.calls)
        await page.goto(f"{base}/subscriptions?client_id={TENANT}")
        await expect_navigation(page, ("Subscriptions", "Tenants"), ())
        await expect(page.get_by_test_id("subscriptions-tenant")).to_have_value(
            TENANT, timeout=WAIT
        )
        await expect(rows).to_have_count(2, timeout=WAIT)
        admin_calls = [c for c in world.calls[start:] if c["path"].startswith("subscriptions")]
        assert admin_calls and all(c["params"].get("client_id") == TENANT for c in admin_calls)

        assert not world.unexpected, world.unexpected
        assert not upstream.unexpected, upstream.unexpected
    except Exception as exc:
        exc.add_note(
            f"Browser diagnostics: {diagnostics}; trace: {tmp_path / 'subscriptions-trace.zip'}; "
            f"frontend log: {tmp_path / 'frontend.log'}"
        )
        raise
    finally:
        (tmp_path / "subscriptions-browser.log").write_text("\n".join(diagnostics))
        with contextlib.suppress(Exception):  # Preserve the original failure if Chromium crashed.
            await context.tracing.stop(path=tmp_path / "subscriptions-trace.zip")
        await context.close()
