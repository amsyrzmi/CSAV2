"""Real browser acceptance for the Payments page (invoices, mark paid, void, needs attention,
settings and payment methods).

The real Next.js shell runs unmodified from a disposable staged copy, as in
`test_subscriptions_browser.py`: its layouts fetch `/auth/me` and `/services/active`
server-side from a loopback stub, and Playwright answers every browser-side
`/console/v1/**` call from a synthetic world. Any call the world does not expect fails the test.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import pytest
from playwright.async_api import Page, async_playwright, expect

from tests import test_services_redesign_browser as services
from tests.test_services_browser import TENANT, TENANT_PROFILE, WAIT, Upstream, make_me
from tests.test_services_redesign_browser import Site

pytestmark = pytest.mark.asyncio

# The staged copy of the console is built once per file by the services fixture.
site = services.site

ACTIVE = [{"key": "subscriptions", "status": "active"}]
ZONE = "Asia/Kuala_Lumpur"
NOW = datetime(2026, 11, 13, 10, 0, tzinfo=ZoneInfo(ZONE))
STAFF = "22222222-2222-4222-8222-222222222222"

AISYAH = {
    "id": "77777777-7777-4777-8777-777777777777",
    "full_name": "Aisyah Rahman",
    "channels": ["telegram"],
}
BEN = {
    "id": "77777777-7777-4777-8777-888888888888",
    "full_name": "Ben Tan",
    "channels": ["whatsapp"],
}
CHEN = {"id": "77777777-7777-4777-8777-999999999999", "full_name": "Chen Wei", "channels": []}
DEWI = {
    "id": "77777777-7777-4777-8777-aaaaaaaaaaaa",
    "full_name": "Dewi Lim",
    "channels": ["telegram"],
}

CLAIMED = "inv_0004"  # Aisyah says she paid
WAITING = "inv_0003"  # Ben, issued, no channel reached him
OVERDUE = "inv_0002"  # Chen, overdue, a reminder failed
PAID = "inv_0001"  # Dewi, paid


def invoice(
    inv_id: str, ref: str, who: dict[str, Any], status: str, **changes: Any
) -> dict[str, Any]:
    return {
        "id": inv_id,
        "reference": ref,
        "status": status,
        "amount": "59.90",
        "currency": "MYR",
        "issued_at": f"2026-11-0{inv_id[-1]}T01:00:00Z",
        "due_at": "2026-11-10T01:00:00Z",
        "paid_at": None,
        "period_start": None,
        "period_end": None,
        "subscription_id": None,
        "plan_name": "Basic",
        "contact": who,
        "claim_note": None,
        "claimed_at": None,
        "method_kind": None,
        "void_reason": None,
        "voided_at": None,
        "reminded_at": None,
        **changes,
    }


def reminder(step: int, channel: str, status: str, sent_at: str | None = None) -> dict[str, Any]:
    return {
        "step": step,
        "channel": channel,
        "status": status,
        "created_at": sent_at or "2026-11-11T01:00:00Z",
        "sent_at": sent_at,
    }


def method(
    method_id: str, kind: str, label: str, details: dict[str, str], **changes: Any
) -> dict[str, Any]:
    return {
        "id": method_id,
        "kind": kind,
        "label": label,
        "details": details,
        "instructions": "",
        "is_active": True,
        "sort_order": 0,
        "version": 1,
        **changes,
    }


# The synthetic methods of tests/test_payment_copy.py: a made-up bank and account.
BANK = method(
    "aaaaaaaa-0000-4000-8000-000000000011",
    "transfer",
    "Bank",
    {"bank_name": "Bank Contoh", "account_number": "512000000012", "account_name": "Kedai Contoh"},
)
DUITNOW = method(
    "aaaaaaaa-0000-4000-8000-000000000012",
    "qr",
    "DuitNow",
    {"duitnow_id": "DN-5120000099", "account_name": "Kedai Contoh Sdn Bhd"},
    instructions="Any bank app can pay a DuitNow ID.",
    sort_order=1,
)
BANK_LINE = "Bank transfer: Bank Contoh, account 512000000012, name Kedai Contoh."
DUITNOW_LINE = (
    "DuitNow: DN-5120000099, name Kedai Contoh Sdn Bhd. Any bank app can pay a DuitNow ID."
)
CONFLICT = "Someone else changed this. Check and save again."
ROLE_REFUSED = "payments access requires an administrator or staff role"

OPEN = {"issued", "overdue", "proof_submitted", "cash_pending"}
FILTERS = {
    "open": OPEN,
    "overdue": {"overdue"},
    "proof_submitted": {"proof_submitted"},
    "paid": {"paid"},
    "void": {"void"},
    "all": OPEN | {"paid", "void", "draft"},
}


class World:
    """Synthetic payments state behind the Playwright route."""

    def __init__(self, upstream: Upstream) -> None:
        self.upstream = upstream
        self.invoices: dict[str, dict[str, Any]] = {
            CLAIMED: invoice(
                CLAIMED,
                "K7QX-4MF",
                AISYAH,
                "proof_submitted",
                amount="1234.50",
                # Customer-typed: must show as text, never as markup.
                claim_note="Paid by DuitNow <b>this morning</b>",
                claimed_at="2026-11-12T02:00:00Z",
                method_kind="qr",
                subscription_id="33333333-3333-4333-8333-333333333333",
            ),
            WAITING: invoice(WAITING, "B2RT-9PW", BEN, "issued"),
            OVERDUE: invoice(OVERDUE, "H4ND-3XZ", CHEN, "overdue"),
            PAID: invoice(PAID, "M8VC-6TQ", DEWI, "paid", paid_at="2026-11-05T01:00:00Z"),
        }
        self.reminders: dict[str, list[dict[str, Any]]] = {
            CLAIMED: [reminder(1, "telegram", "sent", "2026-11-12T01:00:00Z")],
            WAITING: [reminder(1, "none", "no_channel")],
            OVERDUE: [
                reminder(1, "telegram", "sent", "2026-11-03T01:00:00Z"),
                reminder(2, "telegram", "failed"),
            ],
            PAID: [],
        }
        self.payments: dict[str, dict[str, Any] | None] = {key: None for key in self.invoices}
        self.payments[PAID] = {
            "id": "aaaaaaaa-0000-4000-8000-000000000001",
            "amount": "59.90",
            "currency": "MYR",
            "received_at": "2026-11-05T01:00:00Z",
            "method_kind": "transfer",
            "source": "staff",
            "matched_by": "staff",
            "created_by_name": "Synthetic User",
            "note": None,
            "created_at": "2026-11-05T01:00:00Z",
        }
        self.settings: dict[str, Any] = {
            "enabled": False,
            "lead_days": 7,
            "reminder_days": [-7, 0, 3, 7],
            "suspend_after_days": None,
            "version": 0,
        }
        self.methods: list[dict[str, Any]] = []
        self.page_size = 50
        self.forbidden = False
        # While set, a "Load more" read waits for it before it answers.
        self.hold_more: asyncio.Event | None = None
        self.held_answered = asyncio.Event()
        self.mark_paid_errors: list[tuple[int, str]] = []
        self.keys: dict[str, str] = {}
        self.calls: list[dict[str, Any]] = []
        self.unexpected: list[str] = []

    def detail(self, inv_id: str) -> dict[str, Any]:
        return {
            **self.invoices[inv_id],
            "payment": self.payments[inv_id],
            "reminders": self.reminders[inv_id],
        }

    def attention(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for row in self.invoices.values():
            if row["status"] == "proof_submitted":
                items.append(
                    {"kind": "proof_submitted", "invoice": row, "since": row["claimed_at"]}
                )
        for kind, wanted in (("needs_nudge", "no_channel"), ("reminder_failed", "failed")):
            for inv_id, rows in self.reminders.items():
                row = self.invoices[inv_id]
                if row["status"] in OPEN and any(r["status"] == wanted for r in rows):
                    items.append({"kind": kind, "invoice": row, "since": rows[-1]["created_at"]})
        return items

    def payable(self, methods: list[dict[str, Any]]) -> bool:
        return any(m["is_active"] and m["kind"] in {"qr", "transfer"} for m in methods)

    def setup(self, method: str, path: str, body: Any, admin: bool) -> tuple[int, Any] | None:
        """Settings and methods as csa/payments/settings.py answers them."""
        if path == "payments/settings" and method == "GET":
            return 200, self.settings
        if path == "payments/methods" and method == "GET":
            return 200, self.methods
        writes = {("PUT", "payments/settings"), ("POST", "payments/methods")}
        if (method, path) not in writes and not (
            method == "PATCH" and path.startswith("payments/methods/")
        ):
            return None
        if not admin:
            return 403, {"detail": ROLE_REFUSED}
        if path == "payments/settings":
            if body["version"] != self.settings["version"]:
                return 409, {"detail": "version_conflict"}
            if body["enabled"] and not self.payable(self.methods):
                return 409, {"detail": "no_active_method"}
            self.settings = {
                **body,
                "reminder_days": sorted(body["reminder_days"]),
                "version": body["version"] + 1,
            }
            return 200, self.settings
        if method == "POST":
            if body["kind"] == "transfer" and not body["details"]["account_number"].isdigit():
                return 422, {"detail": "invalid_details"}
            created = {
                "id": f"aaaaaaaa-0000-4000-8000-0000000001{len(self.methods):02d}",
                "instructions": "",
                "is_active": True,
                "sort_order": 0,
                **body,
                "version": 1,
            }
            self.methods.append(created)
            return 201, created
        found = [m for m in self.methods if m["id"] == path.split("/")[2]]
        if not found:
            return 404, {"detail": "method_not_found"}
        current = found[0]
        if body["version"] != current["version"]:
            return 409, {"detail": "version_conflict"}
        others = [m for m in self.methods if m is not current]
        turning_off = body.get("is_active") is False and current["is_active"]
        if turning_off and self.settings["enabled"] and not self.payable(others):
            return 409, {"detail": "last_active_method"}
        current.update({k: v for k, v in body.items() if k != "version"})
        current["version"] += 1
        return 200, current

    async def route(self, request_route: Any) -> None:
        request = request_route.request
        path, _, query = request.url.split("/console/v1/")[1].partition("?")
        params = {key: values[0] for key, values in parse_qs(query).items()}
        body = request.post_data_json if request.method in {"POST", "PATCH", "PUT"} else None
        self.calls.append(
            {
                "method": request.method,
                "path": path,
                "params": params,
                "csrf": request.headers.get("x-csrf-token"),
                "body": body,
            }
        )
        hold = self.hold_more if path == "payments/invoices" and "before" in params else None
        if hold is not None:
            await hold.wait()
        status, data = self.answer(request.method, path, params, body)
        await request_route.fulfill(status=status, json=data)
        if hold is not None:
            self.held_answered.set()

    def answer(self, method: str, path: str, params: dict[str, str], body: Any) -> tuple[int, Any]:
        platform = self.upstream.me["role"] == "platform_admin"
        if path == "auth/csrf":
            return 200, {"csrf_token": "synthetic-csrf"}
        if path == "admin/clients" and platform:
            return 200, [TENANT_PROFILE]
        if path == "hr/context" and method == "GET":
            return 404, {"detail": "not stubbed"}
        if path == "services/active" and method == "GET":
            return 200, {"packages": self.upstream.packages}
        if path == "inbox/conversations" and method == "GET":
            return 200, {"items": [], "next_cursor": None, "needs_review_count": 0}
        if path == "chat/conversations" and method == "GET":
            return 200, {"items": [], "next_cursor": None}
        if path == "services/customer-replies":
            return 200, {
                "mode": "auto",
                "brand_name": "Synthetic",
                "can_manage": True,
                "warnings": [],
            }
        if not path.startswith("payments"):
            self.unexpected.append(f"{method} {path}?{params}")
            return 500, {"detail": "unexpected synthetic call"}
        # Platform admins must always name the tenant; members never do.
        if (params.get("client_id") == TENANT) != platform:
            self.unexpected.append(f"{method} {path} with params {params}")
            return 400, {"detail": "tenant mismatch"}
        if self.forbidden or self.upstream.me["role"] == "read_only":
            return 403, {"detail": ROLE_REFUSED}
        admin = platform or self.upstream.me["role"] == "tenant_admin"
        answered = self.setup(method, path, body, admin)
        if answered is not None:
            return answered
        if path == "payments/attention" and method == "GET":
            return 200, {"items": self.attention()}
        if path == "payments/invoices" and method == "GET":
            wanted = FILTERS[params.get("status", "open")]
            q = params.get("q", "").lower()
            rows = sorted(
                (
                    row
                    for row in self.invoices.values()
                    if row["status"] in wanted
                    and (q in row["reference"].lower() or q in row["contact"]["full_name"].lower())
                ),
                key=lambda row: row["issued_at"],
                reverse=True,
            )
            if "before" in params:
                rows = rows[[r["id"] for r in rows].index(params["before"]) + 1 :]
            page = rows[: self.page_size]
            more = len(rows) > self.page_size
            return 200, {"items": page, "next_before": page[-1]["id"] if more else None}
        parts = path.split("/")
        if len(parts) >= 3 and parts[1] == "invoices" and parts[2] in self.invoices:
            inv_id = parts[2]
            row = self.invoices[inv_id]
            if len(parts) == 3 and method == "GET":
                return 200, self.detail(inv_id)
            if parts[3:] == ["mark-paid"] and method == "POST":
                if self.mark_paid_errors:
                    status, code = self.mark_paid_errors.pop(0)
                    return status, {"detail": code}
                key = body["request_key"]
                if self.keys.get(key) == inv_id:
                    return 200, self.detail(inv_id)
                if row["status"] == "paid":
                    return 409, {"detail": "invoice_already_paid"}
                self.keys[key] = inv_id
                row.update(status="paid", paid_at=body.get("received_at"))
                self.payments[inv_id] = {
                    "id": "aaaaaaaa-0000-4000-8000-000000000002",
                    "amount": row["amount"],
                    "currency": row["currency"],
                    "received_at": body.get("received_at"),
                    "method_kind": body["method_kind"],
                    "source": "staff",
                    "matched_by": "staff",
                    "created_by_name": "Synthetic User",
                    "note": body.get("note"),
                    "created_at": "2026-11-13T02:00:00Z",
                }
                return 200, self.detail(inv_id)
            if parts[3:] == ["void"] and method == "POST":
                if row["status"] == "paid":
                    return 409, {"detail": "invoice_paid"}
                row.update(
                    status="void", void_reason=body["reason"], voided_at="2026-11-13T02:00:00Z"
                )
                return 200, self.detail(inv_id)
        self.unexpected.append(f"{method} {path}?{params}")
        return 500, {"detail": "unexpected synthetic call"}


@asynccontextmanager
async def payments_page(
    browser: Any,
    site: Site,
    world: World,
    *,
    role: str = "tenant_staff",
    path: str = "/portal/payments",
    wait_for_rows: bool = True,
    width: int = 1280,
) -> AsyncIterator[Page]:
    site.upstream.me, site.upstream.packages = make_me(role), ACTIVE
    context = await browser.new_context(viewport={"width": width, "height": 1000}, timezone_id=ZONE)
    context.set_default_navigation_timeout(WAIT)
    context.set_default_timeout(15_000)
    await context.clock.set_fixed_time(NOW)
    await context.add_cookies(
        [{"name": "csa_session", "value": "synthetic-session", "url": site.base}]
    )
    page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.route("**/console/v1/**", world.route)
    await page.goto(f"{site.base}{path}")
    if wait_for_rows:
        await expect(page.get_by_test_id("invoice-row").first).to_be_visible(timeout=WAIT)
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


def list_reads(world: World) -> list[dict[str, str]]:
    return [
        c["params"]
        for c in world.calls
        if c["path"] == "payments/invoices" and c["method"] == "GET"
    ]


def payment_calls(world: World) -> list[dict[str, Any]]:
    return [c for c in world.calls if c["path"].startswith("payments")]


async def open_invoice(page: Page, ref: str) -> Any:
    await page.get_by_test_id("invoice-row").filter(has_text=ref).get_by_role("button").click()
    drawer = page.get_by_test_id("invoice-drawer")
    await expect(drawer.get_by_role("heading", name=f"Invoice {ref}")).to_be_visible(timeout=WAIT)
    return drawer


async def test_invoices_list_filters_searches_and_loads_more(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        async with payments_page(browser, site, world) as page:
            rows = page.get_by_test_id("invoice-row")
            # Open by default: the paid invoice is not listed; newest issued first.
            await expect(page.get_by_test_id("invoices-status-open")).to_have_attribute(
                "aria-pressed", "true"
            )
            await expect(rows).to_have_count(3)
            await expect(rows.first).to_contain_text("K7QX-4MF")
            await expect(rows.first).to_contain_text("Aisyah Rahman")
            await expect(rows.first).to_contain_text("Basic")
            # Money is the server's string, grouped: RM 1,234.50.
            await expect(rows.first.get_by_test_id("invoice-amount")).to_have_text("RM 1,234.50")
            await expect(rows.first.get_by_test_id("invoice-status")).to_have_text(
                "Customer says paid"
            )
            await expect(rows.first).to_contain_text("10 Nov 2026")
            assert list_reads(world)[0] == {"status": "open"}

            chips = page.get_by_test_id("invoices-status").get_by_role("button")
            await expect(chips).to_have_text(
                ["Open", "Overdue", "Customer says paid", "Paid", "Void", "All"]
            )
            await page.get_by_test_id("invoices-status-paid").click()
            await expect(rows).to_have_count(1)
            await expect(rows.first).to_contain_text("M8VC-6TQ")
            await page.get_by_test_id("invoices-status-all").click()
            await expect(rows).to_have_count(4)

            # The search box waits for typing to pause: one read for the whole word.
            before = len(list_reads(world))
            await page.get_by_test_id("invoices-search").press_sequentially("ben", delay=40)
            await expect(rows).to_have_count(1)
            await expect(rows.first).to_contain_text("B2RT-9PW")
            searched = list_reads(world)[before:]
            assert searched == [{"status": "all", "q": "ben"}], searched
            await page.get_by_test_id("invoices-search").fill("")
            await expect(rows).to_have_count(4)

            # Keyset pages: "Load more" sends the last id back as `before`, once per click.
            world.page_size = 3
            await page.get_by_test_id("invoices-status-open").click()
            await page.get_by_test_id("invoices-status-all").click()
            await expect(rows).to_have_count(3)
            more = page.get_by_test_id("invoices-more")
            await expect(more).to_have_text("Load more")
            await more.click()
            await expect(rows).to_have_count(4)
            await expect(more).to_have_count(0)
            assert list_reads(world)[-1] == {"status": "all", "before": OVERDUE}, list_reads(world)

    await with_browser(body)


async def test_a_late_load_more_never_joins_a_new_filter(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        world.page_size = 3
        async with payments_page(browser, site, world) as page:
            rows = page.get_by_test_id("invoice-row")
            await page.get_by_test_id("invoices-status-all").click()
            await expect(rows).to_have_count(3)
            # "Load more" for All is still out when the chip changes to Paid.
            world.hold_more = asyncio.Event()
            await page.get_by_test_id("invoices-more").click()
            await expect(page.get_by_test_id("invoices-more")).to_have_text("Loading…")
            await page.get_by_test_id("invoices-status-paid").click()
            await expect(rows).to_have_count(1)
            await expect(rows.first).to_contain_text("M8VC-6TQ")
            world.hold_more.set()
            world.hold_more = None
            await asyncio.wait_for(world.held_answered.wait(), timeout=15)
            await page.wait_for_timeout(300)
            # The old filter's page arrived late and was dropped: Paid still lists only paid.
            await expect(rows).to_have_count(1)
            await expect(rows.first).to_contain_text("M8VC-6TQ")
            await expect(page.get_by_test_id("invoices-more")).to_have_count(0)

            # A 403 on "Load more" ends the page like one on the first read.
            await page.get_by_test_id("invoices-status-all").click()
            await expect(rows).to_have_count(3)
            world.forbidden = True
            await page.get_by_test_id("invoices-more").click()
            await expect(page.get_by_test_id("payments-no-access")).to_contain_text(
                "Payments is for admins and staff."
            )

    await with_browser(body)


async def test_mark_paid_asks_first_and_a_retry_reuses_the_request_key(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        async with payments_page(browser, site, world) as page:
            drawer = await open_invoice(page, "K7QX-4MF")
            claim = drawer.get_by_test_id("invoice-section-claim")
            await expect(
                claim.get_by_role("heading", name="What the customer said")
            ).to_be_visible()
            # The customer's words are text: the tags show, no markup is made.
            await expect(claim.get_by_test_id("invoice-claim-note")).to_have_text(
                "Paid by DuitNow <b>this morning</b>"
            )
            await expect(claim.locator("b")).to_have_count(0)
            await expect(claim).to_contain_text("by DuitNow")
            await expect(drawer.get_by_test_id("invoice-reminder")).to_have_text(
                ["Telegram · sent 12 Nov"]
            )

            await drawer.get_by_test_id("invoice-mark-paid").click()
            form = drawer.get_by_test_id("mark-paid-form")
            method = form.get_by_test_id("mark-paid-method")
            await expect(method).to_be_focused()
            # The customer said DuitNow, so it is chosen; the choices are the three methods.
            await expect(method).to_have_value("qr")
            await expect(method.locator("option")).to_have_text(
                ["Bank transfer", "DuitNow", "Cash"]
            )
            await expect(form.get_by_test_id("mark-paid-date")).to_have_value("2026-11-13")
            await expect(form).to_contain_text("Only staff see this.")
            await method.select_option("transfer")
            await form.get_by_test_id("mark-paid-note").fill("Seen in the bank app")
            await form.get_by_test_id("mark-paid-continue").click()
            await expect(form.get_by_test_id("mark-paid-question")).to_have_text(
                "Mark invoice K7QX-4MF paid? This starts the subscription if it is waiting."
            )
            assert not [c for c in world.calls if c["path"].endswith("/mark-paid")]

            # The first try fails on the server; the retry from the same form replays its key.
            world.mark_paid_errors = [(503, "synthetic outage")]
            confirm = form.get_by_test_id("mark-paid-confirm")
            await confirm.click()
            await expect(form.get_by_test_id("mark-paid-error")).to_have_text(
                "Something went wrong on our side. Try again."
            )
            await expect(confirm).to_be_enabled()
            await confirm.click()
            await expect(drawer.get_by_test_id("invoice-detail-status")).to_have_text("Paid")
            await expect(drawer.get_by_test_id("invoice-notice")).to_contain_text("Marked paid")
            sent = [c for c in world.calls if c["path"].endswith("/mark-paid")]
            assert len(sent) == 2, sent
            keys = {c["body"]["request_key"] for c in sent}
            assert len(keys) == 1, sent
            assert sent[1]["body"] == {
                "request_key": sent[0]["body"]["request_key"],
                "method_kind": "transfer",
                "received_at": "2026-11-13T02:00:00.000Z",
                "note": "Seen in the bank app",
            }, sent[1]
            assert all(c["csrf"] == "synthetic-csrf" for c in sent)
            payment = drawer.get_by_test_id("invoice-payment")
            await expect(payment).to_contain_text("Bank transfer")
            await expect(payment).to_contain_text("Note (only staff see this)")
            await expect(drawer.get_by_test_id("invoice-payment-note")).to_have_text(
                "Seen in the bank app"
            )
            await expect(drawer.get_by_test_id("invoice-actions")).to_have_count(0)
            # The list and Needs attention read again: the invoice left the open list.
            await page.get_by_test_id("invoice-close").click()
            await expect(page.get_by_test_id("invoice-row")).to_have_count(2)
            await expect(page.get_by_test_id("attention-group-proof_submitted")).to_have_count(0)

    await with_browser(body)


async def test_a_refused_payment_says_why_and_a_new_form_has_a_new_key(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        async with payments_page(browser, site, world) as page:
            drawer = await open_invoice(page, "B2RT-9PW")
            await expect(drawer.get_by_test_id("invoice-reminder")).to_have_text(
                ["No channel: message them yourself"]
            )
            world.mark_paid_errors = [(422, "received_in_future")]
            await drawer.get_by_test_id("invoice-mark-paid").click()
            await drawer.get_by_test_id("mark-paid-continue").click()
            await drawer.get_by_test_id("mark-paid-confirm").click()
            await expect(drawer.get_by_test_id("mark-paid-error")).to_have_text(
                "The payment date can't be in the future."
            )
            await drawer.get_by_role("button", name="Back").click()
            await drawer.get_by_role("button", name="Cancel").click()
            # Opening the form again is a new attempt with a new key.
            await drawer.get_by_test_id("invoice-mark-paid").click()
            await drawer.get_by_test_id("mark-paid-continue").click()
            # Someone else marks it paid while this form is open.
            world.invoices[WAITING].update(status="paid", paid_at="2026-11-13T01:00:00Z")

            def reads() -> int:
                return len([c for c in world.calls if c["path"] == f"payments/invoices/{WAITING}"])

            before = reads()
            await drawer.get_by_test_id("mark-paid-confirm").click()
            # The form closes, the drawer says why, and the invoice is read again once.
            stale = drawer.get_by_test_id("invoice-stale")
            await expect(stale).to_have_text("Someone already marked this invoice paid.")
            sent = [
                c["body"]["request_key"] for c in world.calls if c["path"].endswith("/mark-paid")
            ]
            assert len(sent) == 2 and sent[0] != sent[1], sent
            await expect(stale).to_have_attribute("role", "alert")
            await expect(drawer.get_by_test_id("mark-paid-form")).to_have_count(0)
            await expect(drawer.get_by_test_id("invoice-detail-status")).to_have_text("Paid")
            assert reads() == before + 1, (before, reads())

    await with_browser(body)


async def test_void_needs_a_reason_and_a_paid_invoice_cannot_be_voided(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        async with payments_page(browser, site, world) as page:
            drawer = await open_invoice(page, "H4ND-3XZ")
            await expect(drawer.get_by_test_id("invoice-reminder")).to_have_text(
                ["Telegram · sent 3 Nov", "Telegram · failed 11 Nov"]
            )
            await drawer.get_by_test_id("invoice-void").click()
            form = drawer.get_by_test_id("void-form")
            reason = form.get_by_test_id("void-reason")
            await expect(reason).to_be_focused()
            await expect(form).to_contain_text("only staff see this")
            confirm = form.get_by_test_id("void-confirm")
            await expect(confirm).to_be_disabled()
            await reason.fill("   ")
            await expect(confirm).to_be_disabled()
            await reason.fill("  Sent twice by mistake  ")
            await confirm.click()
            await expect(drawer.get_by_test_id("invoice-detail-status")).to_have_text("Void")
            await expect(drawer.get_by_test_id("invoice-void-reason")).to_have_text(
                "Sent twice by mistake"
            )
            voids = [c for c in world.calls if c["path"].endswith("/void")]
            assert [(c["path"], c["body"]) for c in voids] == [
                (f"payments/invoices/{OVERDUE}/void", {"reason": "Sent twice by mistake"})
            ], voids
            await page.get_by_test_id("invoice-close").click()

            await page.get_by_test_id("invoices-status-paid").click()
            drawer = await open_invoice(page, "M8VC-6TQ")
            await expect(drawer.get_by_test_id("invoice-payment")).to_contain_text("Synthetic User")
            await expect(drawer.get_by_test_id("invoice-actions")).to_have_count(0)
            await expect(drawer.get_by_test_id("invoice-void")).to_have_count(0)
            await page.get_by_test_id("invoice-close").click()

            # Someone else marks it paid while the void form is open: the drawer says why the
            # void was refused and shows the invoice as it now stands.
            await page.get_by_test_id("invoices-status-open").click()
            drawer = await open_invoice(page, "B2RT-9PW")
            await drawer.get_by_test_id("invoice-void").click()
            await drawer.get_by_test_id("void-reason").fill("Customer cancelled")
            world.invoices[WAITING].update(status="paid", paid_at="2026-11-13T01:00:00Z")
            await drawer.get_by_test_id("void-confirm").click()
            await expect(drawer.get_by_test_id("invoice-stale")).to_have_text(
                "A paid invoice can't be voided."
            )
            await expect(drawer.get_by_test_id("invoice-stale")).to_have_attribute("role", "alert")
            await expect(drawer.get_by_test_id("void-form")).to_have_count(0)
            await expect(drawer.get_by_test_id("invoice-detail-status")).to_have_text("Paid")

    await with_browser(body)


async def test_needs_attention_groups_invoices_and_opens_the_drawer(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        async with payments_page(browser, site, world) as page:
            section = page.get_by_test_id("payments-attention")
            await expect(section.get_by_test_id("attention-badge")).to_have_text("Invoices: 3")
            await expect(section.get_by_role("heading", level=3)).to_have_text(
                ["Customer says paid", "Needs a nudge", "Reminder failed"]
            )
            nudge = section.get_by_test_id("attention-group-needs_nudge")
            await expect(nudge.get_by_test_id("attention-row")).to_have_count(1)
            await expect(nudge).to_contain_text("B2RT-9PW · Ben Tan · RM 59.90")
            await nudge.get_by_test_id("attention-row").click()
            drawer = page.get_by_test_id("invoice-drawer")
            await expect(drawer.get_by_role("heading", name="Invoice B2RT-9PW")).to_be_visible()
            await page.keyboard.press("Escape")
            await expect(drawer).to_have_count(0)
            await expect(nudge.get_by_test_id("attention-row")).to_be_focused()

            # With nothing left, the section says so and the badge goes.
            for row in world.invoices.values():
                row["status"] = "paid"
            await page.reload()
            await expect(page.get_by_test_id("attention-empty")).to_have_text(
                "Nothing needs attention right now."
            )
            await expect(page.get_by_test_id("attention-badge")).to_have_count(0)

    await with_browser(body)


async def test_roles_staff_admins_read_only_and_a_refused_read(site: Site) -> None:
    async def body(browser: Any) -> None:
        nav_name = "Main navigation"
        world = World(site.upstream)
        async with payments_page(browser, site, world) as page:
            nav = page.get_by_role("navigation", name=nav_name)
            await expect(nav.get_by_role("link", name="Payments", exact=True)).to_have_attribute(
                "href", "/portal/payments"
            )
            # Staff see the Settings view too (read-only there); it is a view of this page.
            await expect(page.get_by_test_id("payments-settings-link")).to_have_attribute(
                "href", "/portal/payments?view=settings"
            )
            await expect(page.get_by_test_id("payments-invoices-link")).to_have_attribute(
                "aria-current", "page"
            )

        world = World(site.upstream)
        async with payments_page(browser, site, world, role="tenant_admin") as page:
            await expect(page.get_by_test_id("payments-settings-link")).to_have_attribute(
                "href", "/portal/payments?view=settings"
            )

        # Read-only members get no link, and the page itself says no without asking the server.
        world = World(site.upstream)
        async with payments_page(
            browser, site, world, role="read_only", wait_for_rows=False
        ) as page:
            await expect(page.get_by_test_id("payments-no-access")).to_contain_text(
                "Payments is for admins and staff."
            )
            nav = page.get_by_role("navigation", name=nav_name)
            await expect(nav.get_by_role("link", name="Subscriptions", exact=True)).to_be_visible()
            await expect(nav.get_by_role("link", name="Payments", exact=True)).to_have_count(0)
            assert payment_calls(world) == [], payment_calls(world)

        # A 403 from the server (a role change since sign-in) reads the same.
        world = World(site.upstream)
        world.forbidden = True
        async with payments_page(browser, site, world, wait_for_rows=False) as page:
            await expect(page.get_by_test_id("payments-no-access")).to_contain_text(
                "Payments is for admins and staff."
            )
            await expect(page.get_by_test_id("invoices-table")).to_have_count(0)

    await with_browser(body)


async def test_platform_admins_name_the_tenant_and_a_link_opens_the_invoice(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        path = f"/payments?client_id={TENANT}&invoice={CLAIMED}"
        async with payments_page(browser, site, world, role="platform_admin", path=path) as page:
            drawer = page.get_by_test_id("invoice-drawer")
            await expect(drawer.get_by_role("heading", name="Invoice K7QX-4MF")).to_be_visible(
                timeout=WAIT
            )
            await expect(page.get_by_test_id("payments-settings-link")).to_have_attribute(
                "href", f"/payments?client_id={TENANT}&view=settings"
            )
            assert all(c["params"].get("client_id") == TENANT for c in payment_calls(world))

    await with_browser(body)


async def test_the_invoice_drawer_stays_with_the_invoices(site: Site) -> None:
    async def body(browser: Any) -> None:
        # ?invoice= opens nothing on the Settings view.
        world = World(site.upstream)
        path = f"/portal/payments?invoice={CLAIMED}&view=settings"
        async with settings_page(browser, site, world, role="tenant_admin", path=path) as page:
            await expect(page.get_by_test_id("payment-methods")).to_be_visible()
            await expect(page.get_by_test_id("invoice-drawer")).to_have_count(0)

        # An open drawer closes when the view moves to Settings (here with Forward).
        world = World(site.upstream)
        async with payments_page(browser, site, world, role="tenant_admin") as page:
            await page.get_by_test_id("payments-settings-link").click()
            await expect(page.get_by_test_id("payments-settings")).to_be_visible()
            await page.go_back()
            await expect(page).to_have_url(f"{site.base}/portal/payments")
            drawer = await open_invoice(page, "K7QX-4MF")
            await page.go_forward()
            await expect(page).to_have_url(f"{site.base}/portal/payments?view=settings")
            await expect(page.get_by_test_id("payments-settings")).to_be_visible()
            await expect(drawer).to_have_count(0)
            # Back on the invoices, the drawer does not come back by itself.
            await page.go_back()
            await expect(page.get_by_test_id("invoice-row").first).to_be_visible()
            await expect(drawer).to_have_count(0)

    await with_browser(body)


async def no_sideways_scroll(page: Page) -> None:
    measured = await page.evaluate(
        "() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth })"
    )
    assert measured["scroll"] <= measured["inner"], measured


async def test_payments_fits_a_phone(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        async with payments_page(browser, site, world, width=320) as page:
            await no_sideways_scroll(page)

        # The settings view, with a method's long line and the add form open.
        world = World(site.upstream)
        world.methods = [BANK, DUITNOW]
        async with settings_page(browser, site, world, role="tenant_admin", width=320) as page:
            await expect(page.get_by_test_id("method-preview")).to_have_count(2)
            await page.get_by_test_id("method-add").click()
            await expect(page.get_by_test_id("method-form")).to_be_visible()
            await no_sideways_scroll(page)

    await with_browser(body)


@asynccontextmanager
async def settings_page(
    browser: Any,
    site: Site,
    world: World,
    *,
    role: str,
    width: int = 1280,
    path: str = "/portal/payments?view=settings",
) -> AsyncIterator[Page]:
    async with payments_page(
        browser,
        site,
        world,
        role=role,
        path=path,
        wait_for_rows=False,
        width=width,
    ) as page:
        await expect(page.get_by_test_id("payments-settings")).to_be_visible(timeout=WAIT)
        yield page


def writes(world: World, path: str) -> list[dict[str, Any]]:
    return [c for c in world.calls if c["path"] == path and c["method"] in {"POST", "PUT", "PATCH"}]


def lead(days: int) -> str:
    return (
        f"When this is on, invoices are made {days} days before each renewal and customers are"
        " reminded automatically."
    )


async def test_admin_adds_a_transfer_method_and_switches_payments_on(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        async with payments_page(browser, site, world, role="tenant_admin") as page:
            # The Settings link swaps the view in place, and the address says so.
            link = page.get_by_test_id("payments-settings-link")
            await link.click()
            await expect(page).to_have_url(f"{site.base}/portal/payments?view=settings")
            await expect(page.get_by_test_id("payments-settings")).to_be_visible()
            await expect(link).to_have_attribute("aria-current", "page")
            await expect(page.get_by_test_id("payments-invoices")).to_have_count(0)
            await expect(page.get_by_test_id("payments-lead-sentence")).to_have_text(lead(7))
            await expect(page.get_by_test_id("settings-reminders-sentence")).to_have_text(
                "Reminders go out 7 days before, on the due day, 3 and 7 days after."
            )
            await expect(page.get_by_test_id("settings-pause-days")).to_have_value("")
            await expect(page.get_by_test_id("methods-empty")).to_be_visible()
            await expect(page.get_by_test_id("payments-enable-hint")).to_be_visible()

            # Switching on with no method: the server refuses, the page says why and the switch
            # goes back to off, which is what is saved.
            switch = page.get_by_role("switch", name="Send invoices and reminders")
            saved_line = page.get_by_test_id("payments-enabled-saved")
            await expect(saved_line).to_have_count(0)
            await switch.check()
            await expect(saved_line).to_have_text("Not saved yet: payments are currently off.")
            await page.get_by_test_id("settings-save").click()
            await expect(page.get_by_test_id("settings-error")).to_have_text(
                "Add an active bank account or DuitNow ID before switching payments on."
            )
            await expect(switch).not_to_be_checked()
            await expect(saved_line).to_have_count(0)
            assert world.settings["enabled"] is False

            # Add a bank transfer method. Only bank transfer and DuitNow are offered.
            await page.get_by_test_id("method-add").click()
            form = page.get_by_test_id("method-form")
            kind = form.get_by_test_id("method-kind")
            await expect(kind).to_be_focused()
            await expect(kind.locator("option")).to_have_text(["Bank transfer", "DuitNow"])
            await form.get_by_test_id("method-label").fill("Main account")
            await form.get_by_test_id("method-bank-name").fill("Bank Contoh")
            await form.get_by_test_id("method-account-name").fill("Kedai Contoh")
            number = form.get_by_test_id("method-account-number")
            # A pasted number that is too long is kept whole and refused, never cut to 20 digits.
            await number.fill(f"  {'5' * 21} ")
            await expect(number).to_have_value(f"  {'5' * 21} ")
            await form.get_by_test_id("method-save").click()
            await expect(form).to_contain_text("Use digits only, 6 to 20 of them.")
            assert writes(world, "payments/methods") == []
            await number.fill("5120 0000 0012")
            await form.get_by_test_id("method-save").click()
            await expect(form).to_contain_text("Use digits only, 6 to 20 of them.")
            await expect(number).to_have_attribute("aria-invalid", "true")
            assert writes(world, "payments/methods") == []
            await number.fill("512000000012")
            await form.get_by_test_id("method-instructions").fill("Put the invoice reference.")
            await expect(form.get_by_test_id("method-form-preview")).to_have_text(
                f"{BANK_LINE} Put the invoice reference."
            )
            await form.get_by_test_id("method-save").click()
            await expect(page.get_by_test_id("methods-notice")).to_have_text("Added.")
            rows = page.get_by_test_id("payment-method")
            await expect(rows).to_have_count(1)
            await expect(rows.get_by_test_id("method-preview")).to_have_text(
                f"{BANK_LINE} Put the invoice reference."
            )
            posts = writes(world, "payments/methods")
            assert [c["body"] for c in posts] == [
                {
                    "kind": "transfer",
                    "label": "Main account",
                    "details": {
                        "bank_name": "Bank Contoh",
                        "account_name": "Kedai Contoh",
                        "account_number": "512000000012",
                    },
                    "instructions": "Put the invoice reference.",
                    "is_active": True,
                }
            ], posts
            await expect(page.get_by_test_id("payments-enable-hint")).to_have_count(0)
            # The refusal is stale now that a method exists; the switch is still off.
            await expect(page.get_by_test_id("settings-error")).to_have_count(0)
            await expect(switch).not_to_be_checked()

            # Lead days, reminder chips and the pause, then switch on.
            await page.get_by_test_id("settings-lead-days").fill("5")
            await expect(page.get_by_test_id("payments-lead-sentence")).to_have_text(lead(5))
            await page.get_by_role("button", name="Remove 7 days after").click()
            day = page.get_by_test_id("settings-reminder-input")
            await day.fill("3")
            await page.get_by_test_id("settings-reminder-add").click()
            await expect(page.get_by_test_id("settings-reminder-help")).to_have_text(
                "That day is already on the list."
            )
            await day.fill("14")
            await page.get_by_test_id("settings-reminder-add").click()
            await expect(page.get_by_test_id("settings-reminders-sentence")).to_have_text(
                "Reminders go out 7 days before, on the due day, 3 and 14 days after."
            )
            await page.get_by_test_id("settings-pause-days").fill("21")
            await switch.check()
            await expect(saved_line).to_have_text("Not saved yet: payments are currently off.")
            await page.get_by_test_id("settings-save").click()
            await expect(page.get_by_test_id("settings-notice")).to_have_text(
                "Saved. Invoices and reminders are on."
            )
            await expect(saved_line).to_have_count(0)
            puts = writes(world, "payments/settings")
            assert [c["body"] for c in puts] == [
                {
                    "enabled": True,
                    "lead_days": 7,
                    "reminder_days": [-7, 0, 3, 7],
                    "suspend_after_days": None,
                    "version": 0,
                },
                {
                    "enabled": True,
                    "lead_days": 5,
                    "reminder_days": [-7, 0, 3, 14],
                    "suspend_after_days": 21,
                    "version": 0,
                },
            ], puts
            assert all(c["csrf"] == "synthetic-csrf" for c in puts + posts)

            # A reload keeps the view; the Invoices link goes back.
            await page.reload()
            await expect(page.get_by_test_id("payments-settings")).to_be_visible(timeout=WAIT)
            await expect(switch).to_be_checked()
            await expect(page.get_by_test_id("settings-lead-days")).to_have_value("5")
            await page.get_by_test_id("payments-invoices-link").click()
            await expect(page.get_by_test_id("invoice-row").first).to_be_visible()
            await expect(page).to_have_url(f"{site.base}/portal/payments")

    await with_browser(body)


async def test_staff_see_the_settings_without_edit_controls(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        world.settings = {
            "enabled": True,
            "lead_days": 7,
            "reminder_days": [-7, 0, 3, 7],
            "suspend_after_days": 14,
            "version": 3,
        }
        world.methods = [BANK, {**DUITNOW, "is_active": False}]
        async with settings_page(browser, site, world, role="tenant_staff") as page:
            await expect(page.get_by_test_id("payments-settings-link")).to_have_attribute(
                "aria-current", "page"
            )
            await expect(page.get_by_test_id("settings-enabled-value")).to_have_text("On")
            await expect(page.get_by_test_id("settings-reminders-value")).to_have_text(
                "7 days before, on the due day, 3 and 7 days after"
            )
            await expect(page.get_by_test_id("settings-pause-value")).to_have_text(
                "Pause a subscription when an invoice is overdue for 14 days."
            )
            await expect(page.get_by_test_id("payments-lead-sentence")).to_have_text(lead(7))
            # Every method is listed, turned-off ones too, each with the customer's line.
            await expect(page.get_by_test_id("method-preview")).to_have_text(
                [BANK_LINE, DUITNOW_LINE]
            )
            await expect(page.get_by_test_id("method-off")).to_have_count(1)
            # No control that changes anything.
            for test_id in ("payments-enabled", "settings-save", "method-add", "method-edit"):
                await expect(page.get_by_test_id(test_id)).to_have_count(0)
            for section in ("payments-settings", "payment-methods"):
                controls = page.get_by_test_id(section).locator("input, select, textarea, button")
                await expect(controls).to_have_count(0)
            paths = {c["path"] for c in payment_calls(world)}
            assert paths == {"payments/settings", "payments/methods"}, paths
            assert all(c["method"] == "GET" for c in payment_calls(world))

    await with_browser(body)


async def test_the_last_active_method_and_someone_else_saving_first(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        world.settings = {**world.settings, "enabled": True, "version": 1}
        world.methods = [dict(BANK)]
        async with settings_page(browser, site, world, role="tenant_admin") as page:
            row = page.get_by_test_id("payment-method")
            # Turning off the only method while payments are on is refused.
            await row.get_by_test_id("method-edit").click()
            form = row.get_by_test_id("method-form")
            await expect(form.get_by_test_id("method-label")).to_be_focused()
            await expect(form.get_by_test_id("method-save")).to_be_disabled()
            await form.get_by_test_id("method-active").uncheck()
            await form.get_by_test_id("method-save").click()
            await expect(form.get_by_test_id("method-error")).to_have_text(
                "Keep one active method while payments are on."
            )
            assert world.methods[0]["is_active"] is True
            assert [c["body"] for c in writes(world, f"payments/methods/{BANK['id']}")] == [
                {"is_active": False, "version": 1}
            ]
            await form.get_by_role("button", name="Cancel").click()

            # Someone else renames the method while this form is open: the form shows theirs.
            await row.get_by_test_id("method-edit").click()
            world.methods[0].update(label="Renamed elsewhere", version=2)
            label = form.get_by_test_id("method-label")
            await label.fill("Main")
            await form.get_by_test_id("method-save").click()
            await expect(form.get_by_test_id("method-error")).to_have_text(CONFLICT)
            await expect(label).to_have_value("Renamed elsewhere")
            await label.fill("Main")
            await form.get_by_test_id("method-save").click()
            await expect(page.get_by_test_id("methods-notice")).to_have_text("Saved.")
            await expect(row.get_by_test_id("method-label")).to_have_text("Main")
            patches = [c["body"] for c in writes(world, f"payments/methods/{BANK['id']}")]
            assert patches[1:] == [
                {"label": "Main", "version": 1},
                {"label": "Main", "version": 2},
            ], patches

            # Someone else saves the settings first: reload, say so, and the next save works.
            world.settings = {**world.settings, "lead_days": 10, "version": 2}
            await page.get_by_test_id("settings-lead-days").fill("5")
            await page.get_by_test_id("settings-save").click()
            await expect(page.get_by_test_id("settings-error")).to_have_text(CONFLICT)
            await expect(page.get_by_test_id("settings-lead-days")).to_have_value("10")
            await expect(page.get_by_test_id("payments-lead-sentence")).to_have_text(lead(10))
            await page.get_by_test_id("settings-save").click()
            await expect(page.get_by_test_id("settings-notice")).to_be_visible()
            puts = [c["body"] for c in writes(world, "payments/settings")]
            assert [(b["lead_days"], b["version"]) for b in puts] == [(5, 1), (10, 2)], puts
            assert world.settings["version"] == 3

    await with_browser(body)
