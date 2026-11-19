"""Real browser acceptance for the Notifications settings page.

The real Next.js shell runs from the staged copy the services fixture builds; its layouts fetch
`/auth/me` from a loopback stub, and Playwright answers every browser-side `/console/v1/**` call
from the synthetic `World` below. The page runs under `next dev`, so effects mount twice: reads are
asserted by content (never by count), and every write (a PUT, POST or DELETE) is asserted exactly
because only a click makes one. A call the stub does not expect fails the test.
"""

from __future__ import annotations

import copy
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qs

import pytest
from playwright.async_api import Page, async_playwright, expect

from tests import test_services_redesign_browser as services
from tests.shell_counts_stub import fulfill_shell_counts
from tests.test_services_browser import TENANT, TENANT_PROFILE, WAIT, make_me
from tests.test_services_redesign_browser import Site

pytestmark = pytest.mark.asyncio

# The staged copy of the console is built once per file by the services fixture.
site = services.site

TELEGRAM = "aaaaaaaa-aaaa-4aaa-8aaa-000000000001"
EMAIL = "aaaaaaaa-aaaa-4aaa-8aaa-000000000002"
UNVERIFIED = "aaaaaaaa-aaaa-4aaa-8aaa-000000000003"
EVENTS = [
    "escalation",
    "approval_needed",
    "order_submitted",
    "dlq",
    "circuit_opened",
    "booking_pending",
    "inbox.overdue",
]
KINDS = [
    "inbox.assigned",
    "inbox.overdue",
    "inbox.snooze_due",
    "inbox.stale_claim",
    "inbox.follow_up",
    "inbox.handback",
]
TIMING = {
    "target_urgent_min": 15,
    "target_high_min": 60,
    "target_normal_min": 240,
    "target_low_min": 1440,
    "repeat_minutes": 60,
    "max_repeats": 3,
    "follow_up_hours": 24,
    "stale_claim_hours": 4,
    "handback_prompt_hours": 0,
}
ADMINS = {"tenant_admin", "platform_admin"}
WRITES = ("PUT", "POST", "DELETE")
# The page has already read the latest when a save loses a race, so it says so (no "Reload").
CONFLICT = (
    "Someone else changed this, so your change was not saved. "
    "The latest is shown; check it and save again."
)


def target(target_id: str, channel: str, masked: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": target_id,
        "channel": channel,
        "address_masked": masked,
        "events": list(EVENTS),
        "min_priority": "normal",
        "quiet_hours": None,
        "language": "en",
        "is_enabled": True,
        "verified": True,
        "version": 1,
        "last_failure": None,
    } | extra


class World:
    """Synthetic notification settings behind the Playwright route."""

    def __init__(self, upstream: Any, *, role: str = "tenant_admin") -> None:
        self.upstream = upstream
        self.role = role
        self.targets = [
            target(TELEGRAM, "telegram", "4821"),
            target(EMAIL, "email", "o***@example.test", min_priority="high"),
        ]
        self.email_available = True
        self.prefs: dict[str, Any] = {
            "email_enabled": False,
            "kinds": list(KINDS),
            "min_priority": "high",
        }
        self.timing = TIMING | {"version": 3}
        # Statuses that make the next call of that kind fail: key is "METHOD path-kind".
        self.fail: dict[str, tuple[int, dict[str, Any]]] = {}
        # The next target PUT finds someone else has saved first.
        self.clash_target = False
        self.clash_timing = False
        self.calls: list[dict[str, Any]] = []
        self.unexpected: list[str] = []

    @property
    def writes(self) -> list[dict[str, Any]]:
        return [
            call for call in self.calls if call["method"] in WRITES and call["path"] != "auth/csrf"
        ]

    def wrote(self, method: str, path: str) -> list[dict[str, Any]]:
        return [call for call in self.writes if call["method"] == method and call["path"] == path]

    async def route(self, request_route: Any) -> None:
        if await fulfill_shell_counts(request_route):
            return
        request = request_route.request
        path, _, query = request.url.split("/console/v1/")[1].partition("?")
        params = {key: values[0] for key, values in parse_qs(query).items()}
        body = (
            request.post_data_json
            if request.method in {"POST", "PUT"} and request.post_data
            else None
        )
        call = {"method": request.method, "path": path, "params": params, "body": body}
        self.calls.append(call)
        status, data = self.answer(request.method, path, params, body)
        if data is None:
            await request_route.fulfill(status=status)
        else:
            await request_route.fulfill(status=status, json=data)

    def answer(self, method: str, path: str, params: dict[str, str], body: Any) -> tuple[int, Any]:
        platform = self.role == "platform_admin"
        if path == "auth/csrf":
            return 200, {"csrf_token": "synthetic-csrf"}
        if path == "admin/clients" and platform:
            return 200, [TENANT_PROFILE]
        if path == "profile" and method == "GET" and not platform:
            return 200, TENANT_PROFILE
        if path == "hr/context" and method == "GET":
            return 404, {"detail": "not stubbed"}
        if path == "services/active" and method == "GET":
            return 200, {"packages": self.upstream.packages}
        if not path.startswith(("notifications/", "inbox/settings")):
            self.unexpected.append(f"{method} {path}?{params}")
            return 500, {"detail": "unexpected synthetic call"}
        # Platform admins always name the company; members never do.
        if (params.get("client_id") == TENANT) != platform or (
            platform is False and "client_id" in params
        ):
            self.unexpected.append(f"{method} {path} with params {params}")
            return 400, {"detail": "tenant mismatch"}
        key = f"{method} {path.split('/')[1] if path.startswith('notifications/') else path}"
        if key in self.fail:
            return self.fail.pop(key)
        if path == "notifications/preferences":
            return self.preferences(method, platform, body)
        if path == "inbox/settings":
            return self.inbox_settings(method, body)
        if method != "GET" and self.role not in ADMINS:
            return 403, {"detail": "notification targets require an administrator"}
        return self.targets_route(method, path, body)

    def preferences(self, method: str, platform: bool, body: Any) -> tuple[int, Any]:
        if platform:
            return 404, {"detail": "a platform admin has no notification preferences"}
        if method == "PUT":
            if body["email_enabled"] and not self.email_available:
                return 503, {"detail": "email_not_configured"}
            self.prefs = dict(body)
        return 200, self.prefs

    def inbox_settings(self, method: str, body: Any) -> tuple[int, Any]:
        if method == "PUT":
            if self.role not in ADMINS:
                return 403, {"detail": "inbox settings require an administrator"}
            if self.clash_timing or body["version"] != self.timing["version"]:
                self.clash_timing = False
                self.timing = self.timing | {
                    "target_urgent_min": 20,
                    "version": self.timing["version"] + 1,
                }
                return 409, {"detail": "version_conflict"}
            self.timing = body | {"version": self.timing["version"] + 1}
        return 200, self.timing

    def find(self, target_id: str) -> dict[str, Any] | None:
        return next((row for row in self.targets if row["id"] == target_id), None)

    def targets_route(self, method: str, path: str, body: Any) -> tuple[int, Any]:
        if path == "notifications/targets" and method == "GET":
            return 200, {
                "targets": copy.deepcopy(self.targets),
                "email_available": self.email_available,
                "events_offered": EVENTS,
            }
        if path == "notifications/targets/email" and method == "POST":
            if not self.email_available:
                return 503, {"detail": "email_not_configured"}
            address = body["address"]
            if any(
                row["channel"] == "email"
                and row["address_masked"][0] == address[0]
                and row["address_masked"].endswith(address.partition("@")[2])
                for row in self.targets
            ):
                return 409, {"detail": "duplicate_address"}
            masked = f"{address[0]}***@{address.partition('@')[2]}"
            created = target(
                f"aaaaaaaa-aaaa-4aaa-8aaa-{len(self.targets) + 1:012d}",
                "email",
                masked,
                min_priority="normal",
            )
            self.targets.append(created)
            return 201, created
        target_id = path.removeprefix("notifications/targets/").removesuffix("/test")
        row = self.find(target_id)
        if row is None:
            return 404, {"detail": "notification target not found"}
        if path.endswith("/test") and method == "POST":
            if row["channel"] != "email":
                return 400, {"detail": "test_not_supported"}
            if not self.email_available:
                return 503, {"detail": "email_not_configured"}
            # The real route only verifies the address (and clears the failure flag): the version
            # and is_enabled stay as they were.
            row.update(verified=True, last_failure=None)
            return 200, row
        if method == "DELETE":
            if row["channel"] != "email":
                return 400, {"detail": "telegram_target_not_removable"}
            self.targets.remove(row)
            return 204, None
        if method == "PUT":
            if self.clash_target or body["version"] != row["version"]:
                self.clash_target = False
                row.update(language="ms", version=row["version"] + 1)
                return 409, {"detail": "version_conflict"}
            row.update({key: body[key] for key in body if key != "version"})
            row.update(version=row["version"] + 1, last_failure=None)
            return 200, row
        self.unexpected.append(f"{method} {path}")
        return 500, {"detail": "unexpected synthetic call"}


@asynccontextmanager
async def notifications_page(
    browser: Any,
    site: Site,
    world: World,
    *,
    path: str | None = None,
    width: int = 1280,
    height: int = 1400,
    timezone: str = "UTC",
    watch_console: bool = False,
) -> AsyncIterator[Page]:
    site.upstream.me, site.upstream.packages = make_me(world.role), []
    context = await browser.new_context(
        viewport={"width": width, "height": height}, timezone_id=timezone
    )
    context.set_default_navigation_timeout(WAIT)
    context.set_default_timeout(15_000)
    await context.add_cookies(
        [{"name": "csa_session", "value": "synthetic-session", "url": site.base}]
    )
    page = await context.new_page()
    problems: list[str] = []
    page.on("pageerror", lambda error: problems.append(f"pageerror: {error}"))
    if watch_console:
        # Off by default: Chromium logs every 4xx/5xx a test makes on purpose.
        page.on(
            "console",
            lambda message: (
                problems.append(f"console: {message.text}") if message.type == "error" else None
            ),
        )
    await page.route("**/console/v1/**", world.route)
    if path is None:
        path = (
            f"/notifications?client_id={TENANT}"
            if world.role == "platform_admin"
            else "/portal/notifications"
        )
    await page.goto(f"{site.base}{path}")
    try:
        yield page
        assert not world.unexpected, world.unexpected
        assert not site.upstream.unexpected, site.upstream.unexpected
        assert not problems, problems
    finally:
        await context.close()


async def with_browser(body: Callable[[Any], Any]) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            await body(browser)
        finally:
            await browser.close()


async def ready(page: Page) -> None:
    """The three sections have read their data."""
    await expect(page.get_by_test_id(f"target-card-{TELEGRAM}")).to_be_visible(timeout=WAIT)
    await expect(
        page.get_by_test_id("timing-form").or_(page.get_by_test_id("timing-values"))
    ).to_be_visible()


def card(page: Page, target_id: str) -> Any:
    return page.get_by_test_id(f"target-card-{target_id}")


async def test_staff_see_every_section_read_only_and_no_company_save(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream, role="tenant_staff")
        async with notifications_page(browser, site, world) as page:
            await ready(page)
            await expect(page.get_by_role("heading", name="Notifications", level=1)).to_be_visible()
            targets = page.get_by_test_id("notifications-targets")
            timing = page.get_by_test_id("notifications-timing")
            await expect(targets).to_contain_text("o***@example.test")
            await expect(targets).to_contain_text("Customer needs a person")
            await expect(targets).to_contain_text("No quiet hours")
            await expect(targets).to_contain_text("Only an Admin can change them.")
            await expect(timing.get_by_test_id("timing-value-target_urgent_min")).to_have_text(
                "15 minutes"
            )
            await expect(timing.get_by_test_id("timing-value-handback_prompt_hours")).to_have_text(
                "Off"
            )
            await expect(timing.get_by_test_id("timing-value-max_repeats")).to_have_text("3")
            for panel in (targets, timing):
                await expect(panel.locator("input, select, textarea, button")).to_have_count(0)
            await expect(page.get_by_test_id("email-add")).to_have_count(0)
            await expect(page.get_by_test_id("timing-save")).to_have_count(0)
            await expect(page.get_by_test_id(f"target-save-{EMAIL}")).to_have_count(0)
            # A member's own email choices are theirs to change, whatever the role.
            await expect(page.get_by_test_id("my-save")).to_be_enabled()
            assert not world.writes

    await with_browser(body)


async def test_an_admin_edits_a_target_and_a_clash_reloads_what_is_saved(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        async with notifications_page(browser, site, world) as page:
            await ready(page)
            mine = card(page, EMAIL)
            await expect(mine.get_by_test_id(f"target-verified-{EMAIL}")).to_have_text("Verified")
            await expect(mine.get_by_test_id(f"target-min-{EMAIL}")).to_have_value("high")
            await mine.get_by_test_id(f"target-event-{EMAIL}-dlq").uncheck()
            await mine.get_by_test_id(f"target-min-{EMAIL}").select_option("urgent")
            await mine.get_by_test_id(f"target-language-{EMAIL}").select_option("ms")
            await mine.get_by_test_id(f"target-quiet-{EMAIL}").check()
            # The window starts from a usual night; the zone is chosen from a fixed list.
            await expect(mine.get_by_test_id(f"target-start-{EMAIL}")).to_have_value("22:00")
            await expect(mine.get_by_test_id(f"target-end-{EMAIL}")).to_have_value("07:00")
            zones = mine.get_by_test_id(f"target-tz-{EMAIL}")
            for zone in ("Asia/Kuala_Lumpur", "Asia/Singapore", "UTC"):
                await expect(zones.locator(f'option[value="{zone}"]')).to_have_count(1)
            await mine.get_by_test_id(f"target-start-{EMAIL}").fill("23:30")
            await mine.get_by_test_id(f"target-end-{EMAIL}").fill("06:15")
            await zones.select_option("Asia/Kuala_Lumpur")
            await mine.get_by_test_id(f"target-save-{EMAIL}").click()
            await expect(mine.get_by_test_id(f"target-notice-{EMAIL}")).to_have_text("Saved.")
            sent = world.wrote("PUT", f"notifications/targets/{EMAIL}")
            assert len(sent) == 1
            assert sent[0]["params"] == {}
            assert sent[0]["body"] == {
                "events": [event for event in EVENTS if event != "dlq"],
                "min_priority": "urgent",
                "quiet_hours": {"start": "23:30", "end": "06:15", "tz": "Asia/Kuala_Lumpur"},
                "language": "ms",
                "is_enabled": True,
                "version": 1,
            }

            # Someone else saved first: the page reads what is saved now, and the next save uses it.
            world.clash_target = True
            await mine.get_by_test_id(f"target-min-{EMAIL}").select_option("low")
            await mine.get_by_test_id(f"target-save-{EMAIL}").click()
            await expect(mine.get_by_test_id(f"target-error-{EMAIL}")).to_have_text(CONFLICT)
            await expect(mine.get_by_test_id(f"target-min-{EMAIL}")).to_have_value("urgent")
            await mine.get_by_test_id(f"target-min-{EMAIL}").select_option("normal")
            await mine.get_by_test_id(f"target-save-{EMAIL}").click()
            await expect(mine.get_by_test_id(f"target-notice-{EMAIL}")).to_have_text("Saved.")
            sent = world.wrote("PUT", f"notifications/targets/{EMAIL}")
            assert [call["body"]["version"] for call in sent] == [1, 2, 3]
            assert sent[-1]["body"]["min_priority"] == "normal"

    await with_browser(body)


async def test_quiet_hours_are_checked_before_anything_is_sent(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        async with notifications_page(browser, site, world) as page:
            await ready(page)
            mine = card(page, EMAIL)
            await mine.get_by_test_id(f"target-quiet-{EMAIL}").check()
            await mine.get_by_test_id(f"target-end-{EMAIL}").fill("22:00")
            await mine.get_by_test_id(f"target-save-{EMAIL}").click()
            await expect(mine.get_by_test_id(f"target-error-{EMAIL}")).to_have_text(
                "The start and end times must be different."
            )
            assert not world.writes

    await with_browser(body)


async def test_an_admin_adds_an_email_address_and_a_relay_failure_saves_nothing(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        async with notifications_page(browser, site, world) as page:
            await ready(page)
            address = page.get_by_test_id("email-add-address")
            await page.get_by_test_id("email-add-submit").click()
            await expect(page.get_by_test_id("email-add-error")).to_have_text(
                "Enter a full email address, like name@example.com."
            )
            await address.fill("not-an-address")
            await page.get_by_test_id("email-add-submit").click()
            await expect(page.get_by_test_id("email-add-error")).to_be_visible()
            assert not world.writes

            world.fail["POST targets"] = (502, {"detail": "email_test_failed"})
            await address.fill("  Bounce@Example.test ")
            await page.get_by_test_id("email-add-submit").click()
            await expect(page.get_by_test_id("email-add-error")).to_have_text(
                "The test email could not be delivered, so nothing was saved. "
                "Check the address and try again."
            )
            await expect(page.get_by_test_id("targets-list").locator("> li")).to_have_count(2)

            await address.fill("  New.Person@Example.test ")
            await page.get_by_test_id("email-add-submit").click()
            await expect(page.get_by_test_id("email-add-notice")).to_have_text(
                "Added n***@example.test. We sent it a test email."
            )
            await expect(page.get_by_test_id("targets-list").locator("> li")).to_have_count(3)
            await expect(page.get_by_test_id("targets-list")).to_contain_text("n***@example.test")
            await expect(address).to_have_value("")
            sent = world.wrote("POST", "notifications/targets/email")
            # Trimmed and lowercased before it is sent; the relay failure's try was the first.
            assert [call["body"] for call in sent] == [
                {"address": "bounce@example.test"},
                {"address": "new.person@example.test"},
            ]

    await with_browser(body)


async def test_send_test_verifies_the_address_and_remove_asks_first(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        world.targets.append(
            target(UNVERIFIED, "email", "u***@example.test", verified=False, is_enabled=False)
        )
        async with notifications_page(browser, site, world) as page:
            await ready(page)
            chip = page.get_by_test_id(f"target-verified-{UNVERIFIED}")
            await expect(chip).to_have_text("Not verified")
            await page.get_by_role("button", name="Send test email to u***@example.test").click()
            await expect(chip).to_have_text("Verified")
            await expect(page.get_by_test_id(f"target-notice-{UNVERIFIED}")).to_have_text(
                "Test email sent. This address is verified."
            )
            assert len(world.wrote("POST", f"notifications/targets/{UNVERIFIED}/test")) == 1
            # Verifying does not switch the target on.
            await expect(page.get_by_test_id(f"target-enabled-{UNVERIFIED}")).not_to_be_checked()

            # The Telegram chat can neither be tested nor removed here; it says where to change it.
            telegram = card(page, TELEGRAM)
            await expect(
                telegram.get_by_text("Connect or change the chat on Channels.")
            ).to_be_visible()
            await expect(telegram.get_by_role("link", name="Channels")).to_have_attribute(
                "href", "/portal/services"
            )
            await expect(
                telegram.get_by_role("button", name=re.compile("Remove|Send test"))
            ).to_have_count(0)

            # Remove asks first, and "Keep it" changes nothing.
            await page.get_by_role("button", name="Remove u***@example.test").click()
            confirm = page.get_by_test_id(f"target-confirm-{UNVERIFIED}")
            await expect(confirm).to_contain_text(
                "Remove u***@example.test? Alerts will stop going to it."
            )
            # Keyboard focus moves into the confirm box, so the next Tab reaches "Yes, remove it".
            await expect(confirm).to_be_focused()
            await page.keyboard.press("Tab")
            await expect(page.get_by_test_id(f"target-remove-confirm-{UNVERIFIED}")).to_be_focused()
            assert not world.wrote("DELETE", f"notifications/targets/{UNVERIFIED}")
            await confirm.get_by_test_id(f"target-remove-cancel-{UNVERIFIED}").click()
            await expect(confirm).to_have_count(0)
            # "Keep it" returns focus to the Remove button it replaced.
            await expect(page.get_by_test_id(f"target-remove-{UNVERIFIED}")).to_be_focused()
            assert not world.wrote("DELETE", f"notifications/targets/{UNVERIFIED}")
            await page.get_by_role("button", name="Remove u***@example.test").click()
            await page.get_by_test_id(f"target-remove-confirm-{UNVERIFIED}").click()
            await expect(card(page, UNVERIFIED)).to_have_count(0)
            # After the removal focus is on a stable spot, not lost to the page body.
            await expect(page.get_by_test_id("email-add-heading")).to_be_focused()
            assert len(world.wrote("DELETE", f"notifications/targets/{UNVERIFIED}")) == 1

    await with_browser(body)


async def test_a_failed_target_says_when_in_the_browsers_zone(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        world.targets[1]["last_failure"] = {"at": "2026-11-18T14:02:00Z", "attempts": 5}
        async with notifications_page(browser, site, world, timezone="Asia/Kuala_Lumpur") as page:
            await ready(page)
            await expect(page.get_by_test_id(f"target-failure-{EMAIL}")).to_have_text(
                "Last delivery failed, 5 attempts, 18 Nov 22:02"
            )
            await expect(page.get_by_test_id(f"target-failure-{TELEGRAM}")).to_have_count(0)
            # Saving clears the flag.
            await card(page, EMAIL).get_by_test_id(f"target-save-{EMAIL}").click()
            await expect(page.get_by_test_id(f"target-failure-{EMAIL}")).to_have_count(0)

    await with_browser(body)


async def test_the_person_section_saves_their_own_choices_with_a_high_floor(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream, role="read_only")
        async with notifications_page(browser, site, world) as page:
            await ready(page)
            mine = page.get_by_test_id("notifications-my")
            await expect(mine).to_contain_text(
                "Emails name the customer. They never contain the customer's message."
            )
            await expect(page.get_by_test_id("my-floor")).to_have_value("high")
            await expect(page.get_by_test_id("my-floor").locator("option:checked")).to_have_text(
                "High"
            )
            for kind in KINDS:
                await expect(page.get_by_test_id(f"my-kind-{kind}")).to_be_checked()
            await page.get_by_test_id("my-email").check()
            await page.get_by_test_id("my-kind-inbox.stale_claim").uncheck()
            await page.get_by_test_id("my-floor").select_option("normal")
            await page.get_by_test_id("my-save").click()
            await expect(page.get_by_test_id("my-notice")).to_have_text("Saved.")
            sent = world.wrote("PUT", "notifications/preferences")
            assert [call["body"] for call in sent] == [
                {
                    "email_enabled": True,
                    "kinds": [kind for kind in KINDS if kind != "inbox.stale_claim"],
                    "min_priority": "normal",
                }
            ]
            # A reader still reads the company sections, with nothing to change in them.
            await expect(
                page.get_by_test_id("notifications-targets").locator("input, select, button")
            ).to_have_count(0)
            await expect(
                page.get_by_test_id("notifications-timing").locator("input, select, button")
            ).to_have_count(0)
            assert [
                call["path"] for call in world.writes if call["path"] != "notifications/preferences"
            ] == []

    await with_browser(body)


async def test_a_platform_admin_sees_the_company_sections_for_the_chosen_company(
    site: Site,
) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream, role="platform_admin")
        async with notifications_page(browser, site, world) as page:
            await ready(page)
            await expect(page.get_by_test_id("notifications-my")).to_have_count(0)
            await expect(page.get_by_test_id("notifications-targets")).to_be_visible()
            await expect(page.get_by_test_id("notifications-timing")).to_be_visible()
            await expect(page.get_by_test_id("notifications-tenant")).to_have_value(TENANT)
            # No personal read at all; every company call names the company.
            assert not [call for call in world.calls if call["path"] == "notifications/preferences"]
            reads = [
                call
                for call in world.calls
                if call["path"].startswith(("notifications/targets", "inbox/settings"))
            ]
            assert reads and all(call["params"] == {"client_id": TENANT} for call in reads)
            await expect(
                card(page, TELEGRAM).get_by_role("link", name="Channels")
            ).to_have_attribute("href", f"/services?client_id={TENANT}")
            await page.get_by_test_id("timing-target_urgent_min").fill("30")
            await page.get_by_test_id("timing-save").click()
            await expect(page.get_by_test_id("timing-notice")).to_have_text("Saved.")
            sent = world.wrote("PUT", "inbox/settings")
            assert len(sent) == 1 and sent[0]["params"] == {"client_id": TENANT}
            await card(page, EMAIL).get_by_test_id(f"target-save-{EMAIL}").click()
            await expect(page.get_by_test_id(f"target-notice-{EMAIL}")).to_have_text("Saved.")
            assert world.wrote("PUT", f"notifications/targets/{EMAIL}")[0]["params"] == {
                "client_id": TENANT
            }

    await with_browser(body)


async def test_a_platform_admin_without_a_company_is_asked_to_choose(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream, role="platform_admin")
        async with notifications_page(browser, site, world, path="/notifications") as page:
            await expect(
                page.get_by_text("Select a tenant to see its notification settings.")
            ).to_be_visible(timeout=WAIT)
            await expect(page.get_by_test_id("notifications-targets")).to_have_count(0)
            await page.get_by_test_id("notifications-tenant").select_option(TENANT)
            await ready(page)
            await expect(page).to_have_url(re.compile(rf"/notifications\?client_id={TENANT}$"))

    await with_browser(body)


async def test_the_inbox_timing_validates_saves_and_reloads_after_a_clash(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        async with notifications_page(browser, site, world) as page:
            await ready(page)
            for key in ("follow_up_hours", "stale_claim_hours", "handback_prompt_hours"):
                await expect(page.locator(f"#timing-hint-{key}")).to_have_text("0 turns it off")
            await expect(page.locator("#timing-hint-target_urgent_min")).to_have_count(0)
            # The unit is part of each field's description: "minutes" or "hours", then any hint.
            await expect(page.get_by_test_id("timing-target_urgent_min")).to_have_attribute(
                "aria-describedby", "timing-unit-target_urgent_min"
            )
            await expect(page.get_by_test_id("timing-follow_up_hours")).to_have_attribute(
                "aria-describedby", "timing-unit-follow_up_hours timing-hint-follow_up_hours"
            )
            await expect(page.get_by_test_id("timing-unit-follow_up_hours")).to_have_text("hours")
            await expect(page.get_by_test_id("timing-max_repeats")).not_to_have_attribute(
                "aria-describedby", re.compile(".")
            )
            await page.get_by_test_id("timing-target_urgent_min").fill("3")
            await page.get_by_test_id("timing-save").click()
            await expect(page.get_by_test_id("timing-error-target_urgent_min")).to_have_text(
                "Enter a whole number of minutes, from 5 to 10080."
            )
            assert not world.writes
            await page.get_by_test_id("timing-target_urgent_min").fill("30")
            await page.get_by_test_id("timing-handback_prompt_hours").fill("12")
            await page.get_by_test_id("timing-save").click()
            await expect(page.get_by_test_id("timing-notice")).to_have_text("Saved.")
            assert world.wrote("PUT", "inbox/settings")[0]["body"] == TIMING | {
                "target_urgent_min": 30,
                "handback_prompt_hours": 12,
                "version": 3,
            }

            world.clash_timing = True
            await page.get_by_test_id("timing-repeat_minutes").fill("90")
            await page.get_by_test_id("timing-save").click()
            await expect(page.get_by_test_id("timing-error")).to_have_text(CONFLICT)
            await expect(page.get_by_test_id("timing-target_urgent_min")).to_have_value("20")
            await expect(page.get_by_test_id("timing-repeat_minutes")).to_have_value("60")
            await page.get_by_test_id("timing-save").click()
            await expect(page.get_by_test_id("timing-notice")).to_have_text("Saved.")
            versions = [call["body"]["version"] for call in world.wrote("PUT", "inbox/settings")]
            assert versions == [3, 4, 5]

    await with_browser(body)


async def test_without_a_mail_relay_only_the_email_controls_are_off(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        world.email_available = False
        async with notifications_page(browser, site, world) as page:
            await ready(page)
            await expect(page.get_by_test_id("my-email")).to_be_disabled()
            await expect(page.get_by_test_id("my-email-off")).to_have_text(
                "Email is not set up on this deployment."
            )
            await expect(page.get_by_test_id("email-add-address")).to_be_disabled()
            await expect(page.get_by_test_id("email-add-submit")).to_be_disabled()
            await expect(page.get_by_test_id("email-add")).to_contain_text(
                "Email is not set up on this deployment"
            )
            await expect(page.get_by_test_id(f"target-test-{EMAIL}")).to_be_disabled()
            # Everything else still works.
            await expect(page.get_by_test_id(f"target-save-{EMAIL}")).to_be_enabled()
            await expect(page.get_by_test_id(f"target-save-{TELEGRAM}")).to_be_enabled()
            await expect(page.get_by_test_id("timing-save")).to_be_enabled()
            await expect(page.get_by_test_id("my-save")).to_be_enabled()
            await page.get_by_test_id(f"target-min-{TELEGRAM}").select_option("urgent")
            await page.get_by_test_id(f"target-save-{TELEGRAM}").click()
            await expect(page.get_by_test_id(f"target-notice-{TELEGRAM}")).to_have_text("Saved.")
            await page.get_by_test_id("my-kind-inbox.handback").uncheck()
            await page.get_by_test_id("my-save").click()
            await expect(page.get_by_test_id("my-notice")).to_have_text("Saved.")

    await with_browser(body)


async def test_with_email_on_and_no_relay_the_switch_can_be_unchecked_and_rechecked(
    site: Site,
) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        world.email_available = False
        world.prefs["email_enabled"] = True  # saved on, then the relay went away
        async with notifications_page(browser, site, world) as page:
            await ready(page)
            switch = page.get_by_test_id("my-email")
            await expect(switch).to_be_checked()
            await expect(switch).to_be_enabled()
            await expect(page.get_by_test_id("my-email-off")).to_have_count(0)
            await switch.uncheck()
            # Locked by what is saved, not by the box: it can be switched back on before saving.
            await expect(switch).not_to_be_checked()
            await expect(switch).to_be_enabled()
            await switch.check()
            await expect(switch).to_be_checked()
            await switch.uncheck()
            await page.get_by_test_id("my-save").click()
            await expect(page.get_by_test_id("my-notice")).to_have_text("Saved.")
            # Saved off, with no relay: now it cannot be turned on.
            await expect(switch).to_be_disabled()
            await expect(page.get_by_test_id("my-email-off")).to_be_visible()

    await with_browser(body)


async def test_a_section_that_fails_does_not_blank_the_others(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        # Every read of those two sections fails (effects mount twice under next dev).
        original = world.answer

        def answer(method: str, path: str, params: dict[str, str], body: Any) -> tuple[int, Any]:
            if method == "GET" and (
                path == "notifications/preferences" or path == "notifications/targets"
            ):
                return 500, {"detail": "synthetic outage"}
            return original(method, path, params, body)

        world.answer = answer  # type: ignore[method-assign]
        async with notifications_page(browser, site, world) as page:
            await expect(page.get_by_test_id("my-load-error")).to_be_visible(timeout=WAIT)
            await expect(page.get_by_test_id("targets-load-error")).to_be_visible()
            await expect(page.get_by_test_id("timing-form")).to_be_visible()
            # Try again reads the section anew.
            world.answer = original  # type: ignore[method-assign]
            await (
                page.get_by_test_id("targets-load-error")
                .locator("xpath=..")
                .get_by_role("button", name="Try again")
                .click()
            )
            await expect(card(page, TELEGRAM)).to_be_visible()
            await expect(page.get_by_test_id("my-load-error")).to_be_visible()

    await with_browser(body)


async def test_an_empty_company_says_so(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        world.targets = []
        async with notifications_page(browser, site, world) as page:
            await expect(page.get_by_test_id("targets-empty")).to_have_text(
                "No alerts are set up yet. Add an email address to start.", timeout=WAIT
            )
            await expect(page.get_by_test_id("email-add")).to_be_visible()

    await with_browser(body)


async def test_every_control_is_reachable_by_keyboard_and_save_buttons_are_named(
    site: Site,
) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        async with notifications_page(browser, site, world) as page:
            await ready(page)
            # Real names, so a screen reader can tell the saves apart.
            for name in (
                "Save my notifications",
                "Save changes to 4821",
                "Save changes to o***@example.test",
                "Save timing",
                "Add email address",
            ):
                await expect(page.get_by_role("button", name=name, exact=True)).to_have_count(1)
            # Tab through the page: every control in the three sections takes focus, in order.
            controls = await page.evaluate(
                """() => [...document.querySelectorAll(
                    '[data-testid="notifications-my"], [data-testid="notifications-targets"], '
                    + '[data-testid="notifications-timing"]'
                )].flatMap((panel) => [...panel.querySelectorAll('input, select, button, a[href]')]
                    .filter((el) => !el.disabled)
                    .map((el) => ({ tab: el.tabIndex, name: el.getAttribute('data-testid') })))"""
            )
            assert len(controls) > 30
            assert all(control["tab"] >= 0 for control in controls), controls
            await page.get_by_test_id("my-email").focus()
            seen: list[str] = []
            for _ in range(len(controls)):
                await page.keyboard.press("Tab")
                seen.append(
                    await page.evaluate(
                        "() => document.activeElement?.getAttribute('data-testid') || ''"
                    )
                )
            for expected in (
                "my-save",
                f"target-save-{EMAIL}",
                f"target-test-{EMAIL}",
                "email-add-address",
                "timing-save",
            ):
                assert expected in seen, (expected, seen)
            # Enter in a field saves the section.
            await page.get_by_test_id("timing-max_repeats").fill("5")
            await page.keyboard.press("Enter")
            await expect(page.get_by_test_id("timing-notice")).to_have_text("Saved.")
            assert world.wrote("PUT", "inbox/settings")[0]["body"]["max_repeats"] == 5

    await with_browser(body)


async def test_the_page_fits_375_px_with_every_card_open(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        world.targets[1] = target(
            EMAIL,
            "email",
            "a-very-long-address-for-a-person-with-a-long-name@subdomain.example.test",
            quiet_hours={
                "start": "22:00",
                "end": "07:00",
                "tz": "America/Argentina/ComodRivadavia",
            },
            last_failure={"at": "2026-11-18T14:02:00Z", "attempts": 5},
        )
        async with notifications_page(browser, site, world, width=375, height=900) as page:
            await ready(page)
            await page.get_by_test_id(f"target-remove-{EMAIL}").click()
            await page.get_by_test_id("email-add-address").fill("x" * 60)
            await page.get_by_test_id("timing-target_urgent_min").fill("1")
            await page.get_by_test_id("timing-save").click()
            # Wait for every animation to end before measuring, as 2b's mobile test does.
            # (Only the finite ones: an endless animation, such as a spinner, never finishes.)
            await page.evaluate(
                """() => Promise.all(
                    document.body.getAnimations({ subtree: true })
                        .filter((a) => a.effect?.getComputedTiming().iterations !== Infinity)
                        .map((a) => a.finished)
                )"""
            )
            sizes = await page.evaluate(
                "() => ({scroll: document.documentElement.scrollWidth, inner: window.innerWidth})"
            )
            assert sizes["scroll"] <= sizes["inner"], sizes
            for test_id in ("notifications-my", "notifications-targets", "notifications-timing"):
                box = await page.get_by_test_id(test_id).bounding_box()
                assert (
                    box and box["x"] >= -0.5 and box["x"] + box["width"] <= sizes["inner"] + 0.5
                ), (test_id, box)
            # Controls keep a 40 px touch height.
            for test_id in ("my-save", "timing-save", f"target-save-{EMAIL}"):
                box = await page.get_by_test_id(test_id).bounding_box()
                assert box and box["height"] >= 40 - 0.5, (test_id, box)

    await with_browser(body)


async def test_the_happy_path_logs_no_console_errors(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        async with notifications_page(browser, site, world, watch_console=True) as page:
            await ready(page)
            await page.get_by_test_id("my-save").click()
            await expect(page.get_by_test_id("my-notice")).to_be_visible()
            await page.get_by_test_id(f"target-save-{EMAIL}").click()
            await expect(page.get_by_test_id(f"target-notice-{EMAIL}")).to_be_visible()
            await page.get_by_test_id("timing-save").click()
            await expect(page.get_by_test_id("timing-notice")).to_be_visible()

    await with_browser(body)
