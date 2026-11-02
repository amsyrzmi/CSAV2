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
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import pytest
from playwright.async_api import Page, async_playwright, expect

from tests import test_services_redesign_browser as services
from tests.test_services_browser import (
    TENANT,
    TENANT_PROFILE,
    WAIT,
    Upstream,
    expect_navigation,
    make_me,
)
from tests.test_services_redesign_browser import Site

pytestmark = pytest.mark.asyncio

# The staged copy of the console is built once per file by the services fixture.
site = services.site

AISYAH = "33333333-3333-4333-8333-333333333333"
BEN = "33333333-3333-4333-8333-444444444444"
CHEN = "33333333-3333-4333-8333-555555555555"
CHEN_CONTACT = "77777777-7777-4777-8777-999999999999"
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


def tomorrow_in_kuala_lumpur() -> str:
    """The browser context runs in Asia/Kuala_Lumpur, so its calendar's tomorrow."""
    return (datetime.now(ZoneInfo("Asia/Kuala_Lumpur")).date() + timedelta(days=1)).isoformat()


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
        # Chen has no subscription yet; staff add one from the Customers tab.
        self.contacts = [aisyah, ben, customer(CHEN_CONTACT, "Chen Wei", "+60111222333")]
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
        # Overview is the assistant home: its status strip and the sandbox chat history.
        if path == "services/active" and method == "GET":
            return 200, {"packages": self.upstream.packages}
        if path == "inbox/conversations" and method == "GET":
            return 200, {"items": [], "next_cursor": None, "needs_review_count": 0}
        if path == "chat/conversations" and method == "GET":
            return 200, {"items": [], "next_cursor": None}
        # Platform admins must always name the tenant; members never do.
        if path.startswith("subscriptions") and (params.get("client_id") == TENANT) != platform:
            self.unexpected.append(f"{method} {path} with params {params}")
            return 400, {"detail": "tenant mismatch"}
        writer = self.upstream.me["role"] != "read_only"
        if path == "subscriptions/settings" and method == "GET":
            return 200, {"timezone": "Asia/Kuala_Lumpur"}
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
        if path == "subscriptions/customers" and method == "GET":
            q = params.get("q", "").lower()
            return 200, [c for c in self.contacts if q in c["full_name"].lower()]
        if path == "subscriptions" and method == "POST" and writer:
            who = next(c for c in self.contacts if c["id"] == body["contact_id"])
            plan = next(p for p in self.plans if p["code"] == body["plan_code"])
            row = subscription(CHEN, who, plan["code"], plan["price"])
            row["started_at"] = body.get("started_at") or row["started_at"]
            self.subs[CHEN] = row
            self.events[CHEN] = []
            return 201, row
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


async def test_subscriptions_tab_actions_requests_and_roles(site: Site, tmp_path: Path) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            await exercise(browser, site.base, site.upstream, tmp_path)
        finally:
            await browser.close()


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

        # The drawer is a modal dialog: focus moves into it, Escape closes it and focus returns
        # to the row's name button (the row itself was clicked).
        drawer = await open_customer(page, "Aisyah Rahman")
        await expect(page.get_by_role("dialog", name="Aisyah Rahman")).to_be_visible()
        assert await page.evaluate(
            "() => document.querySelector('[data-testid=subscription-drawer]')"
            ".contains(document.activeElement)"
        )
        await page.keyboard.press("Escape")
        await expect(drawer).to_be_hidden(timeout=WAIT)
        await expect(
            page.get_by_test_id("subscription-row").first.get_by_role("button")
        ).to_be_focused()

        # (c) Pause goes through a card; confirming it calls confirm and the row follows.
        drawer = await open_customer(page, "Aisyah Rahman")
        await expect(drawer.get_by_test_id("subscription-timeline")).to_contain_text(
            "Subscription started on the Basic plan."
        )
        await expect(drawer.get_by_test_id("subscription-action-resume")).to_have_count(0)
        await drawer.get_by_role("button", name="Pause subscription").click()
        # The resume date cannot be today or earlier (the server refuses it as well).
        await expect(drawer.get_by_test_id("action-resume-at")).to_have_attribute(
            "min", tomorrow_in_kuala_lumpur()
        )
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
        await pause.get_by_test_id("request-resume-at").fill("2099-11-15")
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
                "body": {"decision": "approve", "note": None, "resume_at": "2099-11-15"},
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

        # (f) Staff put a customer on a plan from Customers; the archived plan is not offered.
        await page.get_by_test_id("subscriptions-tab-customers").click()
        await expect(rows).to_have_count(2, timeout=WAIT)
        await page.get_by_test_id("subscription-add").click()
        form = page.get_by_test_id("subscription-add-form")
        await expect(form.get_by_test_id("add-submit")).to_be_disabled()
        await form.get_by_test_id("add-customer-search").fill("chen")
        await form.get_by_test_id("add-customer-find").click()
        option = form.get_by_test_id("add-customer-option")
        await expect(option).to_have_count(1, timeout=WAIT)
        await option.click()
        await expect(form.get_by_test_id("add-customer-chosen")).to_contain_text("Chen Wei")
        await expect(form.get_by_test_id("add-plan").locator("option")).to_have_text(
            ["Select a plan", "Pro · RM 99.00 a month"]
        )
        await form.get_by_test_id("add-plan").select_option("pro")
        await form.get_by_test_id("add-start").fill("2026-11-01")
        await form.get_by_test_id("add-submit").click()
        await expect(page.get_by_text("Added. Chen Wei is on the Pro plan.")).to_be_visible()
        await expect(rows).to_have_count(3)
        await expect(page.get_by_test_id("subscription-add-form")).to_have_count(0)
        lookups = [c for c in world.calls if c["path"] == "subscriptions/customers"]
        assert [c["params"] for c in lookups] == [{"q": "chen"}], lookups
        creates = [c for c in world.calls if c["path"] == "subscriptions" and c["method"] == "POST"]
        assert creates == [
            {
                "method": "POST",
                "path": "subscriptions",
                "params": {},
                "csrf": "synthetic-csrf",
                # 1 Nov 2026 00:00 in Kuala Lumpur
                "body": {
                    "contact_id": CHEN_CONTACT,
                    "plan_code": "pro",
                    "started_at": "2026-10-31T16:00:00.000Z",
                },
            }
        ], creates

        # (g) Read-only members see everything with every action control hidden.
        upstream.me = make_me("read_only")
        before = len([c for c in world.calls if c["method"] != "GET"])
        await page.goto(f"{base}/portal/subscriptions")
        await expect(rows).to_have_count(3, timeout=WAIT)
        await expect(page.get_by_test_id("subscription-add")).to_have_count(0)
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

        # (h) Without the package the link is absent.
        upstream.me, upstream.packages = make_me("tenant_staff"), []
        await page.goto(f"{base}/portal")
        await expect_navigation(page, ("Services",), ("Subscriptions",))

        # (i) A platform admin names the tenant with ?client_id= and every call carries it.
        upstream.me = make_me("platform_admin")
        start = len(world.calls)
        await page.goto(f"{base}/subscriptions?client_id={TENANT}")
        await expect_navigation(page, ("Subscriptions", "Tenants"), ())
        await expect(page.get_by_test_id("subscriptions-tenant")).to_have_value(
            TENANT, timeout=WAIT
        )
        await expect(rows).to_have_count(3, timeout=WAIT)
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


# ---------------------------------------------------------------------------
# The clearer Subscriptions page: summary row, chips, help lines, sections, review step.
# ---------------------------------------------------------------------------

THEMES = ("cobalt", "night", "bureau")
WIDTHS = (320, 390, 768, 1024, 1440)
ZONE = "Asia/Kuala_Lumpur"
# The browser clock is fixed (Date only; timers keep running) so "ending soon" is exact.
NOW = datetime(2026, 11, 2, 10, 0, tzinfo=ZoneInfo(ZONE))
DEWI = "33333333-3333-4333-8333-666666666666"
FARID = "33333333-3333-4333-8333-777777777777"
GITA = "33333333-3333-4333-8333-888888888888"
HANA = "33333333-3333-4333-8333-999999999999"
STAFF = "22222222-2222-4222-8222-222222222222"


class RichWorld(World):
    """Six customers (four active, one paused, one cancelled) and the knobs the new tests need."""

    def __init__(
        self,
        upstream: Upstream,
        *,
        page_limit: int | None = None,
        empty: bool = False,
        no_plans: bool = False,
    ) -> None:
        super().__init__(upstream)
        self.page_limit = page_limit
        self.requests_fail = False
        self.customers_fail = False
        self.settings_fail = False
        extra = [
            (
                DEWI,
                "Dewi Lim",
                "+60130000001",
                "basic",
                "59.90",
                {"renews_at": "2027-03-01T01:00:00Z"},
            ),
            (
                FARID,
                "Farid Hassan",
                "+60130000002",
                "pro",
                "99.00",
                {"status": "paused", "paused_at": "2026-10-20T01:00:00Z"},
            ),
            (
                GITA,
                "Gita Nair",
                "+60130000003",
                "basic",
                "59.90",
                {"cancel_at_period_end": True, "renews_at": "2027-01-10T01:00:00Z"},
            ),
            (
                HANA,
                "Hana Yusof",
                "+60130000004",
                "pro",
                "99.00",
                {"status": "cancelled", "cancelled_at": "2026-09-30T01:00:00Z", "renews_at": None},
            ),
        ]
        for sub_id, name, phone, plan, price, changes in extra:
            who = customer(f"88888888-8888-4888-8888-{sub_id[-12:]}", name, phone)
            self.subs[sub_id] = {**subscription(sub_id, who, plan, price), **changes}
            self.events[sub_id] = [
                {
                    "id": 1,
                    "actor": STAFF,
                    "action": "created",
                    "from_status": None,
                    "to_status": "active",
                    "payload": {"plan": plan},
                    "created_at": "2026-09-27T01:00:00Z",
                }
            ]
        if empty:
            self.subs.clear()
            self.requests.clear()
        if no_plans:
            self.plans = []

    def answer(self, method: str, path: str, params: dict[str, str], body: Any) -> tuple[int, Any]:
        if path == "subscriptions/settings" and self.settings_fail:
            return 500, {"detail": "synthetic settings outage"}
        if path == "subscriptions/requests" and method == "GET" and self.requests_fail:
            return 503, {"detail": "synthetic requests outage"}
        if path == "subscriptions" and method == "GET":
            if self.customers_fail:
                return 503, {"detail": "synthetic customers outage"}
            status, data = super().answer(method, path, params, body)
            if self.page_limit:
                start = int(params.get("before", "0"))
                items = data["items"]
                more = start + self.page_limit < len(items)
                data = {
                    "items": items[start : start + self.page_limit],
                    "next_cursor": str(start + self.page_limit) if more else None,
                }
            return status, data
        parts = path.split("/")
        if (
            method == "GET"
            and len(parts) == 2
            and parts[0] == "subscriptions"
            and parts[1] in self.subs
            and parts[1] not in {AISYAH, BEN}
        ):
            return 200, {"subscription": self.subs[parts[1]], "events": self.events[parts[1]]}
        return super().answer(method, path, params, body)


@asynccontextmanager
async def subs_page(
    browser: Any,
    site: Site,
    world: World,
    *,
    role: str = "tenant_staff",
    width: int = 1280,
    height: int = 1000,
    theme: str | None = None,
    timezone: str = ZONE,
    wait_for_rows: bool = True,
) -> AsyncIterator[Page]:
    site.upstream.me, site.upstream.packages = make_me(role), ACTIVE
    context = await browser.new_context(
        viewport={"width": width, "height": height}, timezone_id=timezone
    )
    context.set_default_navigation_timeout(WAIT)
    context.set_default_timeout(15_000)
    await context.clock.set_fixed_time(NOW)
    cookies = [{"name": "csa_session", "value": "synthetic-session", "url": site.base}]
    if theme:
        cookies.append({"name": "fs-theme", "value": theme, "url": site.base})
    await context.add_cookies(cookies)
    page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.route("**/console/v1/**", world.route)
    await page.goto(f"{site.base}/portal/subscriptions")
    if wait_for_rows:
        await expect(page.get_by_test_id("subscription-row").first).to_be_visible(timeout=WAIT)
    try:
        yield page
        assert not errors, errors
        assert not world.unexpected, world.unexpected
        assert not site.upstream.unexpected, site.upstream.unexpected
    finally:
        await context.close()


async def with_browser(body: Any) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            await body(browser)
        finally:
            await browser.close()


async def until(
    check: Any, message: str = "condition not met in time", timeout: float = 10
) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, message
        await asyncio.sleep(0.1)


def customer_reads(world: World) -> list[dict[str, Any]]:
    return [c for c in world.calls if c["path"] == "subscriptions" and c["method"] == "GET"]


def request_reads(world: World) -> list[dict[str, Any]]:
    return [
        c for c in world.calls if c["path"] == "subscriptions/requests" and c["method"] == "GET"
    ]


def names(page: Page) -> Any:
    return page.get_by_test_id("subscription-row").locator("button")


async def test_summary_row_shows_and_hides_on_failure(site: Site) -> None:
    async def body(browser: Any) -> None:
        # Four active customers, three of them ending soon (two renew within 30 days, one is set
        # to cancel), two requests waiting.
        world = RichWorld(site.upstream)
        async with subs_page(browser, site, world) as page:
            summary = page.get_by_test_id("subscriptions-summary")
            await expect(summary).to_be_visible(timeout=WAIT)
            await expect(page.get_by_test_id("summary-active-value")).to_have_text("4")
            await expect(page.get_by_test_id("summary-ending-value")).to_have_text("3")
            await expect(page.get_by_test_id("summary-requests-value")).to_have_text("2")
            await expect(summary).to_contain_text("Customers active")
            await expect(summary).to_contain_text("Ending soon")
            await expect(summary).to_contain_text("Requests waiting")
            # The row stays above every tab.
            await page.get_by_test_id("subscriptions-tab-plans").click()
            await expect(summary).to_be_visible()
            await page.get_by_test_id("subscriptions-tab-requests").click()
            await expect(summary).to_be_visible()

        # The requests read fails: no figures at all, not a zero.
        failing = RichWorld(site.upstream)
        failing.requests_fail = True
        async with subs_page(browser, site, failing) as page:
            await page.get_by_test_id("subscriptions-tab-requests").click()
            await expect(page.get_by_text("synthetic requests outage").first).to_be_visible()
            await until(lambda: len(request_reads(failing)) >= 2, "requests were read twice")
            await expect(page.get_by_test_id("subscriptions-summary")).to_have_count(0)
            await expect(page.get_by_test_id("summary-active-value")).to_have_count(0)

        # The customers read fails: the same.
        failing = RichWorld(site.upstream)
        failing.customers_fail = True
        async with subs_page(browser, site, failing, wait_for_rows=False) as page:
            await expect(page.get_by_text("synthetic customers outage").first).to_be_visible(
                timeout=WAIT
            )
            await until(lambda: len(customer_reads(failing)) >= 2, "customers were read twice")
            await expect(page.get_by_test_id("subscriptions-summary")).to_have_count(0)

    await with_browser(body)


async def test_summary_row_adds_a_plus_where_a_further_page_exists(site: Site) -> None:
    async def body(browser: Any) -> None:
        # Three customers per page: Aisyah, Ben and Dewi are active, two of them ending soon.
        world = RichWorld(site.upstream, page_limit=3)
        async with subs_page(browser, site, world) as page:
            await expect(page.get_by_test_id("summary-active-value")).to_have_text("3+")
            await expect(page.get_by_test_id("summary-ending-value")).to_have_text("2+")
            # The requests list has no further page, so its figure is exact.
            await expect(page.get_by_test_id("summary-requests-value")).to_have_text("2")
            await expect(page.get_by_test_id("subscriptions-summary-note")).to_contain_text(
                "plus sign"
            )

    await with_browser(body)


async def test_customer_chips_filter(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = RichWorld(site.upstream)
        async with subs_page(browser, site, world) as page:
            rows = page.get_by_test_id("subscription-row")
            await expect(rows).to_have_count(6)
            # Visible labels on the search and the two selects.
            for label in ("Search customers", "Status", "Plan"):
                await expect(page.get_by_text(label, exact=True).first).to_be_visible()
            labels = {
                "all": "All (6)",
                "active": "Active (4)",
                "paused": "Paused (1)",
                "cancelled": "Cancelled (1)",
                "ending": "Ending soon (3)",
            }
            for key, text in labels.items():
                await expect(page.get_by_test_id(f"customers-view-{key}")).to_have_text(text)
            await expect(page.get_by_test_id("customers-view-all")).to_have_attribute(
                "aria-pressed", "true"
            )
            await expect(page.get_by_test_id("customers-view-note")).to_have_count(0)

            reads = len(customer_reads(world))
            await page.get_by_test_id("customers-view-paused").click()
            await expect(rows).to_have_count(1)
            await expect(rows.first).to_contain_text("Farid Hassan")
            await expect(page.get_by_test_id("customers-view-paused")).to_have_attribute(
                "aria-pressed", "true"
            )
            await expect(page.get_by_test_id("customers-view-all")).to_have_attribute(
                "aria-pressed", "false"
            )
            await page.get_by_test_id("customers-view-ending").click()
            await expect(rows).to_have_count(3)
            await expect(rows.filter(has_text="Gita Nair")).to_have_count(1)
            await expect(rows.filter(has_text="Dewi Lim")).to_have_count(0)
            await page.get_by_test_id("customers-view-cancelled").click()
            await expect(rows).to_have_count(1)
            await expect(rows.first).to_contain_text("Hana Yusof")
            # Chips work on the rows already loaded: no new read.
            assert len(customer_reads(world)) == reads, customer_reads(world)

            # A chip that matches nothing says so and offers one way back.
            await page.get_by_test_id("subscriptions-status").select_option("paused")
            await expect(page.get_by_test_id("customers-view-paused")).to_have_text("Paused (1)")
            await expect(page.get_by_test_id("customers-view-active")).to_have_text("Active (0)")
            await page.get_by_test_id("customers-view-active").click()
            await expect(rows).to_have_count(0)
            await expect(page.get_by_test_id("customers-view-empty")).to_be_visible()
            await page.get_by_test_id("customers-show-all").click()
            await expect(rows).to_have_count(1)
            await expect(page.get_by_test_id("customers-view-all")).to_have_attribute(
                "aria-pressed", "true"
            )

    await with_browser(body)


async def test_customer_chips_say_when_they_cover_only_the_loaded_page(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = RichWorld(site.upstream, page_limit=3)
        async with subs_page(browser, site, world) as page:
            rows = page.get_by_test_id("subscription-row")
            await expect(rows).to_have_count(3)
            note = page.get_by_test_id("customers-view-note")
            await expect(note).to_contain_text("only the customers loaded so far")
            # Figures over a partial page would mislead, so the chips carry none.
            await expect(page.get_by_test_id("customers-view-active")).to_have_text("Active")
            await page.get_by_test_id("customers-view-ending").click()
            await expect(rows).to_have_count(2)
            await page.get_by_role("button", name="Show more customers").click()
            await expect(note).to_have_count(0)
            await expect(page.get_by_test_id("customers-view-ending")).to_have_text(
                "Ending soon (3)"
            )
            await expect(rows).to_have_count(3)

    await with_browser(body)


async def test_timeline_uses_the_tenant_time_zone(site: Site) -> None:
    extended = {
        "id": 20,
        "actor": STAFF,
        "action": "renewal_extended",
        "from_status": None,
        "to_status": None,
        "payload": {
            "old_renews_at": "2026-11-27T18:00:00Z",
            "new_renews_at": "2026-12-27T18:00:00Z",
        },
        "created_at": "2026-11-01T17:30:00Z",
    }

    async def body(browser: Any) -> None:
        # The company is in Kuala Lumpur (UTC+8); this browser is in Los Angeles (UTC-8).
        world = RichWorld(site.upstream)
        world.events[AISYAH].append(extended)
        async with subs_page(browser, site, world, timezone="America/Los_Angeles") as page:
            await expect(page.get_by_test_id("subscription-row").first).to_contain_text(
                "27 Nov 2026"
            )
            drawer = await open_customer(page, "Aisyah Rahman")
            newest = drawer.get_by_test_id("subscription-timeline").locator("li").first
            await expect(newest).to_contain_text("Renewal moved from 28 Nov 2026 to 28 Dec 2026.")
            await expect(newest.locator("time")).to_have_text("2 Nov 2026, 01:30")
            await expect(drawer.get_by_test_id("timeline-zone")).to_contain_text(
                "Asia/Kuala_Lumpur"
            )

        # When the company's zone cannot be read, the browser's zone is used and the page says so.
        world = RichWorld(site.upstream)
        world.settings_fail = True
        world.events[AISYAH].append(extended)
        async with subs_page(browser, site, world, timezone="America/Los_Angeles") as page:
            drawer = await open_customer(page, "Aisyah Rahman")
            newest = drawer.get_by_test_id("subscription-timeline").locator("li").first
            await expect(newest).to_contain_text("Renewal moved from 27 Nov 2026 to 27 Dec 2026.")
            await expect(newest.locator("time")).to_have_text("1 Nov 2026, 09:30")
            await expect(drawer.get_by_test_id("timeline-zone")).to_contain_text("this device")

    await with_browser(body)


async def test_help_lines_beside_jargon_fields(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = RichWorld(site.upstream)
        async with subs_page(browser, site, world) as page:
            drawer = await open_customer(page, "Aisyah Rahman")
            await drawer.get_by_role("button", name="Change plan").click()
            form = drawer.get_by_test_id("subscription-action-form")
            # The label is the old one, word for word; the new help line sits under the field.
            await expect(
                form.get_by_text("Special price in RM (optional; starts now)")
            ).to_be_visible()
            price = form.get_by_test_id("action-price")
            help_line = form.get_by_test_id("help-special-price")
            await expect(help_line).to_contain_text(
                "Leave this empty to use the plan's normal price"
            )
            assert await price.get_attribute("aria-describedby") == await help_line.get_attribute(
                "id"
            )
            price_box, help_box = await price.bounding_box(), await help_line.bounding_box()
            assert price_box and help_box
            assert 0 <= help_box["y"] - (price_box["y"] + price_box["height"]) < 24, (
                price_box,
                help_box,
            )
            await expect(form.get_by_test_id("help-change-plan")).to_contain_text(
                "Moves the customer to another plan"
            )
            await form.get_by_role("button", name="Cancel", exact=True).click()

            await drawer.get_by_role("button", name="Cancel at period end").click()
            await expect(drawer.get_by_test_id("help-cancel-at-period-end")).to_contain_text(
                "stays active until the current period ends"
            )
            await (
                drawer.get_by_test_id("subscription-action-form")
                .get_by_role("button", name="Cancel", exact=True)
                .click()
            )
            await drawer.get_by_role("button", name="Cancel now").click()
            await expect(drawer.get_by_test_id("help-cancel-now")).to_contain_text(
                "cannot be started again"
            )
            await drawer.get_by_test_id("subscription-close").click()

            await page.get_by_test_id("subscriptions-tab-plans").click()
            await page.get_by_test_id("plan-add").click()
            code_label = "Short code (lowercase letters, numbers, - or _; cannot change later)"
            await expect(page.get_by_text(code_label)).to_be_visible()
            code_help = page.get_by_test_id("help-plan-code")
            await expect(code_help).to_contain_text("You cannot change it after you save")
            assert await page.get_by_test_id("plan-code").get_attribute("aria-describedby") == (
                await code_help.get_attribute("id")
            )

    await with_browser(body)


async def test_review_step_shows_a_plain_sentence_and_raw_values_inside_the_arrow(
    site: Site,
) -> None:
    async def body(browser: Any) -> None:
        world = RichWorld(site.upstream)
        async with subs_page(browser, site, world) as page:
            drawer = await open_customer(page, "Aisyah Rahman")
            await drawer.get_by_role("button", name="Pause subscription").click()
            await drawer.get_by_test_id("action-resume-at").fill("2099-11-15")
            await drawer.get_by_test_id("action-submit").click()
            step = drawer.get_by_test_id("review-step")
            sentence = step.get_by_test_id("review-sentence")
            await expect(sentence).to_have_text(
                "This will pause Aisyah Rahman's subscription until 15 Nov 2099."
            )
            # The sentence comes first, then the card with the confirm button.
            card = drawer.get_by_test_id("proposal-card")
            await expect(card).to_contain_text("Pause now until someone resumes it.")
            sentence_box, card_box = await sentence.bounding_box(), await card.bounding_box()
            assert sentence_box and card_box and sentence_box["y"] < card_box["y"]
            await expect(card.get_by_test_id("proposal-confirm")).to_be_visible()
            # The values the page sends sit behind a closed arrow.
            raw = step.get_by_test_id("review-raw-details")
            await expect(raw).to_be_hidden()
            await expect(raw).to_contain_text("resume_at")
            await expect(raw).to_contain_text("2099-11-15")
            await step.get_by_test_id("review-raw-toggle").click()
            await expect(raw).to_be_visible()
            await expect(raw).to_contain_text("pause")
            await expect(raw).to_contain_text("2099-11-15")
            # Confirming still goes through the card, and the plain review goes away with it.
            await card.get_by_test_id("proposal-confirm").click()
            await expect(card.get_by_test_id("proposal-outcome")).to_contain_text("now paused")
            await expect(step).to_have_count(0)

    await with_browser(body)


async def test_drawer_is_sectioned_into_who_plan_price_and_when(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = RichWorld(site.upstream)
        async with subs_page(browser, site, world) as page:
            drawer = await open_customer(page, "Gita Nair")
            details = drawer.get_by_test_id("subscription-details")
            for section in ("who", "plan", "price", "when"):
                await expect(
                    details.get_by_test_id(f"subscription-section-{section}")
                ).to_be_visible()
            who = details.get_by_test_id("subscription-section-who")
            await expect(who).to_contain_text("+60130000003")
            plan = details.get_by_test_id("subscription-section-plan")
            await expect(plan.get_by_test_id("subscription-detail-status")).to_have_text("Active")
            await expect(plan).to_contain_text("Cancels when the period ends on 10 Jan 2027")
            price = details.get_by_test_id("subscription-section-price")
            await expect(price).to_contain_text("RM 59.90")
            await expect(price).to_contain_text("Every month")
            when = details.get_by_test_id("subscription-section-when")
            await expect(when).to_contain_text("27 Sep 2026")
            await expect(when).to_contain_text("10 Jan 2027")

    await with_browser(body)


async def test_empty_states_keep_their_sentence_and_offer_one_next_action(site: Site) -> None:
    async def body(browser: Any) -> None:
        # No customers yet, plans exist: the old sentence stays and a button starts the first one.
        world = RichWorld(site.upstream, empty=True)
        async with subs_page(browser, site, world, wait_for_rows=False) as page:
            await expect(
                page.get_by_text(
                    "Subscriptions will appear here. "
                    "Add a subscription to put a customer on one of your plans."
                )
            ).to_be_visible(timeout=WAIT)
            await expect(page.get_by_test_id("subscriptions-summary")).to_be_visible()
            await expect(page.get_by_test_id("summary-active-value")).to_have_text("0")
            await expect(page.get_by_test_id("customers-view-all")).to_have_count(0)
            await page.get_by_test_id("customers-empty-add").click()
            await expect(page.get_by_test_id("subscription-add-form")).to_be_visible()
            await expect(page.get_by_test_id("customers-empty-add")).to_have_count(0)

        # No plans either: the next step is a plan, and the Plans tab offers its own first step.
        world = RichWorld(site.upstream, empty=True, no_plans=True)
        async with subs_page(browser, site, world, wait_for_rows=False) as page:
            await expect(page.get_by_test_id("customers-empty-plans")).to_be_visible(timeout=WAIT)
            await expect(page.get_by_test_id("customers-empty-add")).to_have_count(0)
            await page.get_by_test_id("customers-empty-plans").click()
            await expect(
                page.get_by_text("No plans yet. Add your first plan so customers can subscribe.")
            ).to_be_visible()
            await page.get_by_test_id("plans-empty-add").click()
            await expect(page.get_by_test_id("plan-form")).to_be_visible()

        # Nothing matches a search: the old sentence stays and one button clears the filters.
        world = RichWorld(site.upstream)
        async with subs_page(browser, site, world) as page:
            search = page.get_by_placeholder("Search by name, phone or email")
            await search.fill("zzz")
            await page.get_by_test_id("subscriptions-search-submit").click()
            await expect(
                page.get_by_text(
                    "No subscriptions match. "
                    "Try another name, phone or email, or clear the filters."
                )
            ).to_be_visible()
            await page.get_by_test_id("customers-clear-filters").click()
            await expect(page.get_by_test_id("subscription-row")).to_have_count(6)
            await expect(search).to_have_value("")

        # Read-only members see the sentence and no button to start something they cannot do.
        world = RichWorld(site.upstream, empty=True)
        async with subs_page(browser, site, world, role="read_only", wait_for_rows=False) as page:
            await expect(
                page.get_by_text(
                    "Subscriptions will appear here once your team adds a customer to a plan."
                )
            ).to_be_visible(timeout=WAIT)
            await expect(page.get_by_test_id("customers-empty-add")).to_have_count(0)
            await expect(page.get_by_test_id("customers-empty-plans")).to_have_count(0)

    await with_browser(body)


async def overflow(page: Page) -> dict[str, int]:
    return await page.evaluate(
        "() => ({scroll: document.documentElement.scrollWidth, inner: window.innerWidth})"
    )


# Text contrast of the new elements: the same maths as the kit's contrast test.
CONTRAST_JS = """async (names) => {
    const parse = (value) => {
        const m = value.match(/rgba?\\(([^)]+)\\)/);
        if (!m) throw new Error('unsupported colour ' + value);
        const parts = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
        return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
    };
    const over = (top, below) => ({
        r: top.r * top.a + below.r * (1 - top.a), g: top.g * top.a + below.g * (1 - top.a),
        b: top.b * top.a + below.b * (1 - top.a), a: 1,
    });
    const backdrop = (el) => {
        const own = parse(getComputedStyle(el).backgroundColor);
        if (own.a >= 1) return own;
        const parent = el.parentElement;
        const below = parent ? backdrop(parent) : { r: 255, g: 255, b: 255, a: 1 };
        return own.a === 0 ? below : over(own, below);
    };
    const channel = (v) => {
        v /= 255;
        return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    };
    const lum = ({ r, g, b }) => 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
    const ratio = (a, b) => {
        const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
        return (hi + 0.05) / (lo + 0.05);
    };
    return names.map((name) => {
        const el = document.querySelector(`[data-testid=${name}]`);
        const below = backdrop(el);
        return { name, ratio: ratio(over(parse(getComputedStyle(el).color), below), below) };
    });
}"""

# Does the placeholder fit inside the search box? (Measured with the box's own font.)
PLACEHOLDER_FITS_JS = """() => {
    const input = document.querySelector('[data-testid=subscriptions-search]');
    const style = getComputedStyle(input);
    const context = document.createElement('canvas').getContext('2d');
    context.font = style.font;
    const room = input.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    return { need: context.measureText(input.placeholder).width, room };
}"""


@pytest.mark.parametrize("theme", THEMES)
async def test_subscriptions_at_five_widths_in_three_themes(site: Site, theme: str) -> None:
    async def body(browser: Any) -> None:
        world = RichWorld(site.upstream)
        async with subs_page(browser, site, world, theme=theme) as page:
            await expect(page.get_by_test_id("subscriptions-summary")).to_be_visible(timeout=WAIT)
            for width in WIDTHS:
                await page.set_viewport_size({"width": width, "height": 900})
                await expect(page.get_by_test_id("customers-view-ending")).to_be_visible()
                measured = await overflow(page)
                assert measured["scroll"] <= measured["inner"], ("page", theme, width, measured)
                # The search box shows its whole placeholder (it was cut at 768).
                fit = await page.evaluate(PLACEHOLDER_FITS_JS)
                assert fit["need"] <= fit["room"], ("placeholder", theme, width, fit)
                # The summary tiles and the chips stay inside the viewport.
                outside = await page.evaluate(
                    """() => [...document.querySelectorAll(
                        '[data-testid^="summary-"], [data-testid^="customers-view-"]')]
                        .filter(e => e.getBoundingClientRect().right > window.innerWidth + 0.5)
                        .map(e => e.dataset.testid)"""
                )
                assert not outside, (theme, width, outside)
                if width == 1440:
                    texts = await page.evaluate(
                        CONTRAST_JS,
                        [
                            "summary-active-value",
                            "summary-active-label",
                            "subscriptions-summary-note",
                        ],
                    )
                    for item in texts:
                        assert item["ratio"] >= 4.5, (theme, item)

                # The drawer and its review step fit too.
                drawer = await open_customer(page, "Aisyah Rahman")
                await drawer.get_by_role("button", name="Pause subscription").click()
                await expect(drawer.get_by_test_id("help-pause")).to_be_visible()
                if width == 1440:
                    texts = await page.evaluate(CONTRAST_JS, ["help-pause"])
                    for item in texts:
                        assert item["ratio"] >= 4.5, (theme, item)
                await drawer.get_by_test_id("action-submit").click()
                await expect(drawer.get_by_test_id("review-step")).to_be_visible()
                await drawer.get_by_test_id("review-raw-toggle").click()
                await expect(drawer.get_by_test_id("review-raw-details")).to_be_visible()
                measured = await overflow(page)
                assert measured["scroll"] <= measured["inner"], ("drawer", theme, width, measured)
                inside = await drawer.evaluate("el => ({s: el.scrollWidth, c: el.clientWidth})")
                assert inside["s"] <= inside["c"], ("drawer box", theme, width, inside)
                if width == 1440:
                    texts = await page.evaluate(CONTRAST_JS, ["review-sentence", "timeline-zone"])
                    for item in texts:
                        assert item["ratio"] >= 4.5, (theme, item)
                await page.keyboard.press("Escape")
                await expect(drawer).to_be_hidden(timeout=WAIT)

    await with_browser(body)
