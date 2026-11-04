"""Real browser acceptance for the Bookings tab and its setup pages.

The real Next.js shell runs from a disposable staged copy, as in `test_subscriptions_browser.py`:
its layouts fetch `/auth/me` and `/services/active` server-side from a loopback stub, and
Playwright answers every browser-side `/console/v1/**` call. A call the stub does not expect
fails the test.
"""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qs

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

ACTIVE = [{"key": "bookings", "status": "active"}]
THEMES = ("cobalt", "night", "bureau")
WIDTHS = (320, 390, 768)
AIMAN = "44444444-4444-4444-8444-000000000001"
SITI = "44444444-4444-4444-8444-000000000002"
ROOM = "55555555-5555-4555-8555-000000000001"
TYPE = "66666666-6666-4666-8666-000000000001"
HR_RECORD = "77777777-7777-4777-8777-000000000001"
EDIT_CONTROLS = (
    "types-add",
    "types-starters",
    "type-edit",
    "people-from-team",
    "people-add",
    "person-edit",
    "room-add",
    "room-edit",
    "closed-day-add",
    "block-add",
)
DEFAULT_WEEK = [{"weekday": day, "opens": "09:00", "closes": "18:00"} for day in range(5)]


def person(person_id: str, name: str, email: str | None, **extra: Any) -> dict[str, Any]:
    return {
        "id": person_id,
        "display_name": name,
        "user_id": None,
        "employee_id": None,
        "email": email,
        "is_active": True,
        "version": 1,
    } | extra


def booking_type(type_id: str, name: str, minutes: int, **extra: Any) -> dict[str, Any]:
    return {
        "id": type_id,
        "name": name,
        "duration_minutes": minutes,
        "buffer_minutes": 0,
        "audience": "customers",
        "delivery": "any",
        "resource_rule": "none",
        "location_kind": "phone",
        "address": None,
        "meeting_link": None,
        "needs_confirmation": False,
        "min_notice_minutes": 60,
        "max_advance_days": 60,
        "change_cutoff_minutes": 120,
        "is_active": True,
        "version": 1,
        "person_ids": [],
        "resource_ids": [],
    } | extra


TEAM = [
    person(
        "44444444-4444-4444-8444-000000000011",
        "Aiman Hakim",
        "aiman@example.test",
        employee_id=HR_RECORD,
    ),
    person("44444444-4444-4444-8444-000000000012", "Siti Nur", "siti@example.test"),
]


class World:
    """Synthetic Bookings state behind the Playwright route."""

    def __init__(
        self, upstream: Upstream, *, rich: bool = False, stale_people: bool = False
    ) -> None:
        self.upstream = upstream
        self.people: list[dict[str, Any]] = []
        self.resources: list[dict[str, Any]] = []
        self.types: list[dict[str, Any]] = []
        self.company_hours: list[dict[str, Any]] | None = None
        self.subject_hours: dict[str, list[dict[str, Any]]] = {}
        self.closed_days: list[dict[str, Any]] = []
        self.blocks: list[dict[str, Any]] = []
        self.stale_people = stale_people
        if rich:
            self.people = [
                person(AIMAN, "Aiman Hakim", "aiman@example.test", employee_id=HR_RECORD),
                person(SITI, "Siti Nur", "siti@example.test", is_active=False),
            ]
            self.resources = [
                {"id": ROOM, "name": "Meeting room", "capacity": 6, "is_active": True, "version": 1}
            ]
            self.types = [
                booking_type(
                    TYPE,
                    "Consultation",
                    60,
                    audience="both",
                    delivery="named",
                    location_kind="in_person",
                    address="1 Jalan Contoh, Synthetic City",
                    needs_confirmation=True,
                    person_ids=[AIMAN],
                    resource_rule="any",
                    resource_ids=[ROOM],
                )
            ]
            self.subject_hours[AIMAN] = [
                {"weekday": 1, "opens": "10:00", "closes": "16:00"},
            ]
        self.calls: list[dict[str, Any]] = []
        self.writes: list[dict[str, Any]] = []
        self.unexpected: list[str] = []

    async def route(self, request_route: Any) -> None:
        request = request_route.request
        path, _, query = request.url.split("/console/v1/")[1].partition("?")
        params = {key: values[0] for key, values in parse_qs(query).items()}
        body = request.post_data_json if request.method in {"POST", "PATCH", "PUT"} else None
        call = {"method": request.method, "path": path, "params": params, "body": body}
        self.calls.append(call)
        if request.method != "GET" and path.startswith("bookings"):
            self.writes.append(call)
        status, data = self.answer(request.method, path, params, body)
        await request_route.fulfill(status=status, json=data)

    def answer(self, method: str, path: str, params: dict[str, str], body: Any) -> tuple[int, Any]:
        role = self.upstream.me["role"]
        platform = role == "platform_admin"
        if path == "auth/csrf":
            return 200, {"csrf_token": "synthetic-csrf"}
        if path == "admin/clients" and platform:
            return 200, [TENANT_PROFILE]
        if path == "profile" and method == "GET" and not platform:
            return 200, TENANT_PROFILE
        # The rail asks for the HR tabs on pages that name a tenant; unused here.
        if path == "hr/context" and method == "GET":
            return 404, {"detail": "not stubbed"}
        if path == "services/active" and method == "GET":
            return 200, {"packages": self.upstream.packages}
        if not path.startswith("bookings/"):
            self.unexpected.append(f"{method} {path}?{params}")
            return 500, {"detail": "unexpected synthetic call"}
        # Platform admins must always name the company; members never do.
        if (params.get("client_id") == TENANT) != platform:
            self.unexpected.append(f"{method} {path} with params {params}")
            return 400, {"detail": "tenant mismatch"}
        route = path.removeprefix("bookings/")
        if method != "GET" and role not in {"tenant_admin", "platform_admin"}:
            return 403, {"detail": "bookings setup requires an administrator"}
        handler = getattr(self, "do_" + route.split("/")[0].replace("-", "_"), None)
        if handler is None:
            self.unexpected.append(f"{method} {path}")
            return 500, {"detail": "unexpected synthetic call"}
        return handler(method, route, params, body)

    def do_people(
        self, method: str, route: str, params: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        if method == "GET":
            return 200, self.people
        if route == "people/from-team":
            known = {p["display_name"] for p in self.people}
            added = [copy.deepcopy(p) for p in TEAM if p["display_name"] not in known]
            self.people += added
            return 200, added
        if method == "POST":
            row = person(
                f"44444444-4444-4444-8444-0000000001{len(self.people):02d}",
                body["display_name"],
                body.get("email"),
            )
            self.people.append(row)
            return 201, row
        row = next(p for p in self.people if p["id"] == route.rsplit("/", 1)[1])
        if self.stale_people or body["version"] != row["version"]:
            return 409, {"detail": "version_conflict"}
        row.update({k: v for k, v in body.items() if k != "version"}, version=row["version"] + 1)
        return 200, row

    def do_resources(
        self, method: str, route: str, params: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        if method == "GET":
            return 200, self.resources
        row = {
            "id": f"55555555-5555-4555-8555-0000000001{len(self.resources):02d}",
            "name": body["name"],
            "capacity": body.get("capacity"),
            "is_active": True,
            "version": 1,
        }
        self.resources.append(row)
        return 201, row

    def do_types(
        self, method: str, route: str, params: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        if method == "GET":
            return 200, self.types
        if route == "types/starters":
            if self.types:
                return 200, self.types
            self.types = [
                booking_type("66666666-6666-4666-8666-000000000101", "Appointment", 30),
                booking_type("66666666-6666-4666-8666-000000000102", "Consultation", 60),
            ]
            return 201, self.types
        if method == "POST":
            row = booking_type(
                f"66666666-6666-4666-8666-0000000002{len(self.types):02d}",
                body["name"],
                body["duration_minutes"],
            ) | {k: v for k, v in body.items() if k in booking_type("x", "x", 1)}
            self.types.append(row)
            return 201, row
        row = next(t for t in self.types if t["id"] == route.rsplit("/", 1)[1])
        row.update({k: v for k, v in body.items() if k not in {"version", "is_active"}})
        row.update(is_active=body["is_active"], version=row["version"] + 1)
        return 200, row

    def do_hours(
        self, method: str, route: str, params: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        subject = params.get("subject_id") if method == "GET" else body.get("subject_id")
        kind = (
            (params.get("subject_kind") or "company")
            if method == "GET"
            else body.get("subject_kind", "company")
        )
        if method == "PUT":
            if subject:
                self.subject_hours[subject] = body["periods"]
            else:
                self.company_hours = body["periods"] or None
        stored = self.subject_hours.get(subject, []) if subject else self.company_hours
        periods = stored if stored else ([] if subject else DEFAULT_WEEK)
        # `is_default` is true for the default week AND for a person or room that follows the
        # company, as the real route does.
        return 200, {
            "subject_kind": kind,
            "subject_id": subject,
            "is_default": not stored,
            "periods": periods,
        }

    def do_closed_days(
        self, method: str, route: str, params: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        if method == "GET":
            return 200, self.closed_days
        if method == "POST":
            row = {"day": body["day"], "note": body.get("note")}
            self.closed_days = [d for d in self.closed_days if d["day"] != row["day"]] + [row]
            return 201, row
        day = route.rsplit("/", 1)[1]
        self.closed_days = [d for d in self.closed_days if d["day"] != day]
        return 204, None

    def do_blocks(
        self, method: str, route: str, params: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        if method == "GET":
            return 200, self.blocks
        if method == "POST":
            row = body | {
                "id": "88888888-8888-4888-8888-000000000001",
                "note": body.get("note"),
                "created_by": "22222222-2222-4222-8222-222222222222",
            }
            self.blocks.append(row)
            return 201, row
        self.blocks = []
        return 204, None


@asynccontextmanager
async def bookings_page(
    browser: Any,
    site: Site,
    world: World,
    *,
    role: str = "tenant_admin",
    path: str = "/portal/bookings/types",
    width: int = 1280,
    height: int = 1000,
    theme: str | None = None,
    packages: list[dict[str, str]] = ACTIVE,
) -> AsyncIterator[Page]:
    site.upstream.me, site.upstream.packages = make_me(role), packages
    context = await browser.new_context(
        viewport={"width": width, "height": height}, timezone_id="Asia/Kuala_Lumpur"
    )
    context.set_default_navigation_timeout(WAIT)
    context.set_default_timeout(15_000)
    cookies = [{"name": "csa_session", "value": "synthetic-session", "url": site.base}]
    if theme:
        cookies.append({"name": "fs-theme", "value": theme, "url": site.base})
    await context.add_cookies(cookies)
    page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.route("**/console/v1/**", world.route)
    await page.goto(f"{site.base}{path}")
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


async def overflow(page: Page) -> dict[str, int]:
    return await page.evaluate(
        "() => ({scroll: document.documentElement.scrollWidth, inner: window.innerWidth})"
    )


def writes(world: World, method: str, path: str) -> list[Any]:
    return [c["body"] for c in world.writes if c["method"] == method and c["path"] == path]


async def test_admin_sets_up_types_people_rooms_and_hours(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        # A company with Bookings alone: the rail has the tab, and no Customer support entries.
        async with bookings_page(browser, site, world, path="/portal/bookings") as page:
            await expect_navigation(page, ("Bookings",), ("Customer inbox", "Subscriptions"))
            await expect(page.get_by_test_id("calendar-placeholder")).to_be_visible(timeout=WAIT)

            # Empty company: one clear first step, the starter types.
            await page.get_by_test_id("bookings-subnav-types").click()
            empty = page.get_by_test_id("types-empty")
            await expect(empty).to_be_visible(timeout=WAIT)
            await expect(page.get_by_test_id("types-starters")).to_have_text(
                "Create the starter types"
            )
            await page.get_by_test_id("types-starters").click()
            await expect(page.get_by_test_id("type-card")).to_have_count(2)
            await expect(page.get_by_test_id("types-notice")).to_have_text(
                "Added Appointment and Consultation."
            )
            await expect(page.get_by_test_id("types-need-people")).to_contain_text(
                "Nobody can take these bookings yet."
            )
            assert len(writes(world, "POST", "bookings/types/starters")) == 1

            # Add a type of their own: the form says what is missing first.
            await page.get_by_test_id("types-add").click()
            dialog = page.get_by_test_id("type-dialog")
            await dialog.get_by_test_id("type-name").fill("Site visit")
            await dialog.get_by_test_id("type-location").select_option("in_person")
            await dialog.get_by_test_id("type-save").click()
            await expect(dialog.get_by_test_id("type-error")).to_have_text(
                "Enter the address where people meet."
            )
            assert not writes(world, "POST", "bookings/types")
            await dialog.get_by_test_id("type-address").fill("2 Jalan Contoh")
            await dialog.get_by_test_id("type-save").click()
            await expect(page.get_by_test_id("type-card")).to_have_count(3)
            sent = writes(world, "POST", "bookings/types")
            assert len(sent) == 1
            assert sent[0]["name"] == "Site visit"
            assert sent[0]["address"] == "2 Jalan Contoh"
            assert sent[0]["meeting_link"] is None
            assert sent[0]["duration_minutes"] == 30

            # People and rooms.
            await page.get_by_test_id("bookings-subnav-people").click()
            await expect(page.get_by_test_id("people-empty")).to_be_visible(timeout=WAIT)
            hours = page.get_by_test_id("company-hours-line")
            await expect(hours).to_have_text(["Monday to Friday, 09:00 to 18:00 (default)"])
            await page.get_by_test_id("people-from-team").click()
            cards = page.get_by_test_id("person-card")
            await expect(cards).to_have_count(2)
            await expect(page.get_by_test_id("people-notice")).to_have_text(
                "Added 2 people from your team."
            )
            await expect(cards.first).to_contain_text("aiman@example.test")
            await expect(cards.first).to_contain_text("Linked to HR")
            await expect(cards.nth(1)).not_to_contain_text("Linked to HR")
            assert HR_RECORD not in await page.content()
            await page.get_by_test_id("people-from-team").click()
            await expect(page.get_by_test_id("people-notice")).to_have_text(
                "Everyone on your team is already on the list."
            )

            await page.get_by_test_id("room-add").click()
            room = page.get_by_test_id("room-dialog")
            await room.get_by_test_id("room-name").fill("Meeting room")
            await room.get_by_test_id("room-capacity").fill("six")
            await room.get_by_test_id("room-dialog-save").click()
            await expect(room.get_by_test_id("room-dialog-error")).to_contain_text("whole number")
            await room.get_by_test_id("room-capacity").fill("6")
            await room.get_by_test_id("room-dialog-save").click()
            await expect(page.get_by_test_id("room-card")).to_have_count(1)
            await expect(page.get_by_test_id("room-card")).to_contain_text("Holds 6")
            assert writes(world, "POST", "bookings/resources") == [
                {"name": "Meeting room", "capacity": 6}
            ]

            # A person with nothing stored follows the company, though the flag says is_default.
            await page.get_by_role("button", name="Working hours for Aiman Hakim").click()
            current = page.get_by_test_id("hours-current")
            await expect(current).to_contain_text("Same as the company")
            await expect(current).not_to_contain_text("(default)")
            await page.get_by_test_id("hours-own").check()
            monday = page.get_by_test_id("hours-day-0")
            # They start from the company's week; change Monday to close at midnight.
            await monday.get_by_label("Monday closes").fill("00:00")
            await page.get_by_test_id("hours-save").click()
            await expect(page.get_by_test_id("hours-error")).to_contain_text("23:59")
            assert not writes(world, "PUT", "bookings/hours")
            await monday.get_by_label("Monday closes").fill("17:30")
            await page.get_by_test_id("hours-save").click()
            await expect(page.get_by_test_id("hours-dialog")).to_have_count(0)
            put = writes(world, "PUT", "bookings/hours")
            assert len(put) == 1
            assert put[0]["subject_kind"] == "person" and put[0]["subject_id"] == TEAM[0]["id"]
            assert put[0]["periods"][0] == {"weekday": 0, "opens": "09:00", "closes": "17:30"}

            # The company's own week, and a closed day.
            await page.get_by_test_id("company-hours-open").click()
            await expect(page.get_by_test_id("hours-editor")).to_contain_text(
                "latest closing time is 23:59"
            )
            await expect(page.get_by_test_id("hours-editor")).to_contain_text("Asia/Kuala_Lumpur")
            await (
                page.get_by_test_id("hours-day-5")
                .get_by_role("button", name="Open this day")
                .click()
            )
            await page.get_by_test_id("hours-save").click()
            await expect(page.get_by_test_id("hours-dialog")).to_have_count(0)
            await expect(hours).to_have_text(["Monday to Saturday, 09:00 to 18:00"])
            await page.get_by_test_id("closed-day-date").fill("2099-12-25")
            await page.get_by_test_id("closed-day-note").fill("Synthetic holiday")
            await page.get_by_test_id("closed-day-add").click()
            await expect(page.get_by_test_id("closed-day")).to_contain_text(
                "25 Dec 2099: Synthetic holiday"
            )
            await page.get_by_role("button", name="Open again on 25 Dec 2099").click()
            await expect(page.get_by_test_id("closed-days-empty")).to_be_visible()

            # An unavailable time is entered on the company's clock and sent as UTC.
            await page.get_by_test_id("block-who").select_option(
                f"resource:{world.resources[0]['id']}"
            )
            await page.get_by_test_id("block-start-day").fill("2099-11-10")
            await page.get_by_test_id("block-add").click()
            await expect(page.get_by_test_id("block")).to_contain_text(
                "Meeting room: 10 Nov 2099, 09:00 to 18:00"
            )
            block = writes(world, "POST", "bookings/blocks")[0]
            assert block["starts_at"] == "2099-11-10T01:00:00.000Z"
            assert block["ends_at"] == "2099-11-10T10:00:00.000Z"

            # A stale edit shows a sentence, not a code.
            world.stale_people = True
            await page.get_by_role("button", name="Change Siti Nur").click()
            await page.get_by_test_id("person-name").fill("Siti Nur Aini")
            await page.get_by_test_id("person-dialog-save").click()
            await expect(page.get_by_test_id("person-dialog-error")).to_contain_text(
                "Someone else changed this while you were editing."
            )

    await with_browser(body)


@pytest.mark.parametrize("role", ["tenant_staff", "read_only"])
async def test_staff_and_read_only_see_setup_without_edit_controls(site: Site, role: str) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream, rich=True)
        async with bookings_page(browser, site, world, role=role) as page:
            await expect(page.get_by_test_id("type-card")).to_have_count(1, timeout=WAIT)
            await expect(page.get_by_test_id("type-card")).to_contain_text("1 hour")
            await expect(page.get_by_test_id("type-card")).to_contain_text("Staff confirm")
            await page.get_by_test_id("bookings-subnav-people").click()
            await expect(page.get_by_test_id("person-card")).to_have_count(2, timeout=WAIT)
            await expect(page.get_by_test_id("room-card")).to_have_count(1)
            await expect(page.get_by_test_id("company-hours-open")).to_have_text("See each day")
            await expect(page.get_by_test_id("blocks")).to_be_visible()
            # Emails are for an Admin; the HR record is never shown.
            assert "example.test" not in await page.get_by_test_id("people").inner_text()
            assert HR_RECORD not in await page.content()
            for test_id in EDIT_CONTROLS:
                await expect(page.get_by_test_id(test_id)).to_have_count(0)
            await expect(page.get_by_role("button", name="Open again")).to_have_count(0)
            # Their own hours are readable, with nothing to change.
            await page.get_by_role("button", name="Working hours for Aiman Hakim").click()
            await expect(page.get_by_test_id("hours-current")).to_contain_text(
                "Tuesday, 10:00 to 16:00"
            )
            await expect(page.get_by_test_id("hours-save")).to_have_count(0)
            await page.keyboard.press("Escape")
            await page.get_by_role("button", name="Working hours for Meeting room").click()
            await expect(page.get_by_test_id("hours-current")).to_contain_text(
                "Same as the company"
            )
        assert not world.writes, world.writes

        # A company with nothing yet: staff are told who sets it up, with no button to press.
        world = World(site.upstream)
        async with bookings_page(browser, site, world, role=role) as page:
            await expect(page.get_by_test_id("types-empty")).to_contain_text(
                "An Admin adds the booking types for your company."
            )
            for test_id in EDIT_CONTROLS:
                await expect(page.get_by_test_id(test_id)).to_have_count(0)
            await page.get_by_test_id("bookings-subnav-people").click()
            await expect(page.get_by_test_id("people-empty")).to_contain_text(
                "An Admin adds the people"
            )
            for test_id in EDIT_CONTROLS:
                await expect(page.get_by_test_id(test_id)).to_have_count(0)

    await with_browser(body)


async def test_platform_admin_follows_the_chosen_company(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream, rich=True)
        async with bookings_page(
            browser, site, world, role="platform_admin", path="/bookings/types", packages=[]
        ) as page:
            await expect_navigation(page, ("Bookings",), ())
            select = page.get_by_test_id("bookings-tenant")
            await expect(select).to_have_value("", timeout=WAIT)
            await expect(page.get_by_text("Select a tenant to see its bookings.")).to_be_visible()
            assert not [c for c in world.calls if c["path"].startswith("bookings")]
            await select.select_option(TENANT)
            await expect(page.get_by_test_id("type-card")).to_have_count(1, timeout=WAIT)
            assert page.url.endswith(f"/bookings/types?client_id={TENANT}")
            # The section bar keeps the company; the people page asks for it too.
            await page.get_by_test_id("bookings-subnav-people").click()
            await expect(page.get_by_test_id("person-card")).to_have_count(2, timeout=WAIT)
            assert page.url.endswith(f"/bookings/people-and-rooms?client_id={TENANT}")
            await expect(page.get_by_test_id("person-card").first).to_contain_text(
                "aiman@example.test"
            )
            await expect(page.get_by_test_id("room-add")).to_be_visible()
            reads = [
                c for c in world.calls if c["path"].startswith("bookings") and c["method"] == "GET"
            ]
            assert reads and all(c["params"].get("client_id") == TENANT for c in reads)
            # The company's time zone comes from the tenant list, so blocks can be shown.
            await expect(page.get_by_test_id("block-who")).to_be_visible()

    await with_browser(body)


@pytest.mark.parametrize("theme", THEMES)
async def test_setup_pages_fit_320_390_and_768_in_every_theme(site: Site, theme: str) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream, rich=True)
        world.closed_days = [{"day": "2099-12-25", "note": "A holiday with a long synthetic note"}]
        async with bookings_page(browser, site, world, width=320, theme=theme) as page:
            await expect(page.get_by_test_id("type-card")).to_have_count(1, timeout=WAIT)
            for width in WIDTHS:
                await page.set_viewport_size({"width": width, "height": 900})
                measured = await overflow(page)
                assert measured["scroll"] <= measured["inner"], ("types", theme, width, measured)
                await page.get_by_test_id("type-edit").click()
                dialog = page.get_by_test_id("type-dialog")
                await expect(dialog.get_by_test_id("type-name")).to_be_visible()
                inside = await dialog.evaluate("el => ({s: el.scrollWidth, c: el.clientWidth})")
                assert inside["s"] <= inside["c"], ("type dialog", theme, width, inside)
                await page.keyboard.press("Escape")
                await expect(dialog).to_have_count(0)
            await page.get_by_test_id("bookings-subnav-people").click()
            await expect(page.get_by_test_id("person-card")).to_have_count(2, timeout=WAIT)
            for width in WIDTHS:
                await page.set_viewport_size({"width": width, "height": 900})
                await expect(page.get_by_test_id("closed-day")).to_have_count(1)
                measured = await overflow(page)
                assert measured["scroll"] <= measured["inner"], ("people", theme, width, measured)
                await page.get_by_test_id("company-hours-open").click()
                dialog = page.get_by_test_id("hours-dialog")
                await expect(page.get_by_test_id("hours-editor")).to_be_visible()
                measured = await overflow(page)
                assert measured["scroll"] <= measured["inner"], ("hours", theme, width, measured)
                inside = await dialog.evaluate("el => ({s: el.scrollWidth, c: el.clientWidth})")
                assert inside["s"] <= inside["c"], ("hours dialog", theme, width, inside)
                await page.keyboard.press("Escape")
                await expect(dialog).to_have_count(0)

    await with_browser(body)
