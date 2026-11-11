"""Real browser acceptance for the Bookings tab and its setup pages.

The real Next.js shell runs from a disposable staged copy, as in `test_subscriptions_browser.py`:
its layouts fetch `/auth/me` and `/services/active` server-side from a loopback stub, and
Playwright answers every browser-side `/console/v1/**` call. A call the stub does not expect
fails the test.
"""

from __future__ import annotations

import asyncio
import copy
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
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

ACTIVE = [{"key": "bookings", "status": "active"}]
# The company runs on Kuala Lumpur time; every browser context runs on New York time, so a page that
# read the browser's clock (instead of the company's) would put things on the wrong day or hour.
COMPANY_ZONE = "Asia/Kuala_Lumpur"
BROWSER_ZONE = "America/New_York"
TENANT2 = "11111111-1111-4111-8111-222222222222"
TENANT2_PROFILE = TENANT_PROFILE | {
    "id": TENANT2,
    "slug": "second",
    "name": "Second Tenant",
    "timezone": "Pacific/Auckland",
}
ME = "22222222-2222-4222-8222-222222222222"
CUSTOMER_NAME = "Aina Synthetic"
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
    "settings-reminder",
    "settings-reminder-unit",
    "settings-pending-hours",
    "settings-limit",
    "settings-save",
)
DEFAULT_WEEK = [{"weekday": day, "opens": "09:00", "closes": "18:00"} for day in range(5)]


def monday_of(zone: str, weeks: int = 0) -> date:
    """The Monday of the current week on that company's calendar, `weeks` weeks on."""
    today = datetime.now(ZoneInfo(zone)).date()
    return today - timedelta(days=today.weekday()) + timedelta(weeks=weeks)


def utc_at(day: date, clock: str, zone: str = COMPANY_ZONE) -> str:
    """A wall-clock time on the company's clock, as the UTC instant the API sends."""
    hour, minute = (int(part) for part in clock.split(":"))
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(zone))
    return local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def as_sent(instant: str) -> str:
    """The same instant as the browser sends it (`toISOString`, with milliseconds)."""
    return instant.removesuffix("Z") + ".000Z"


def week_title(monday: date) -> str:
    sunday = monday + timedelta(days=6)

    def read(day: date, year: bool) -> str:
        return f"{day.day} {day.strftime('%b')}" + (f" {day.year}" if year else "")

    return f"{read(monday, monday.year != sunday.year)} to {read(sunday, True)}"


def instant(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def booking(booking_id: int, start: str, end: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": f"99999999-9999-4999-8999-{booking_id:012d}",
        "status": "confirmed",
        "kind": "appointment",
        "title": "Consultation",
        "private": False,
        "redacted": False,
        "starts_at": start,
        "ends_at": end,
        "version": 1,
        "type_id": TYPE,
        "customer": None,
        "people": [{"id": AIMAN, "display_name": "Aiman Hakim", "is_active": True}],
        "resources": [],
        "guests": [],
        "location_kind": "phone",
        "address": None,
        "meeting_link": None,
        "conflict": None,
        "can_change": True,
        "email": None,
        "customer_told": None,
        "source": "staff",
        "replaces_booking_id": None,
        "replaces_starts_at": None,
        "replaces_ends_at": None,
        "pending_change_id": None,
    } | extra


def person(person_id: str, name: str, email: str | None, **extra: Any) -> dict[str, Any]:
    return {
        "id": person_id,
        "display_name": name,
        "user_id": None,
        "employee_id": None,
        "linked_to_hr": extra.get("employee_id") is not None,
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
        self.tenants = [TENANT_PROFILE]
        self.bookings: list[dict[str, Any]] = []
        self.busy: list[dict[str, Any]] = []
        self.customers: list[dict[str, Any]] = []
        self.slots: list[str] = []
        # Queued refusals, each answers one booking or move: (status, body).
        self.refusals: list[tuple[int, dict[str, Any]]] = []
        self.mail = "sent"
        self.customer_told: bool | None = None
        # Whether asking Google again for a Meet link makes one.
        self.meet_works = True
        self.profile_status = 200
        self.profile_delay = 0.0
        # The settings an Admin has saved (version 0 until the first save), and whether the
        # company's customer replies are Off.
        self.settings = {
            "reminder_minutes": 1440,
            "pending_hours": 24,
            "customer_limit": 5,
            "version": 0,
        }
        self.replies_off = False
        # Customer support is suspended: replies are Off, and Services cannot turn them on.
        self.replies_suspended = False
        self.settings_status = 200
        # Google Calendar: switched off unless a test turns it on. Connecting answers with the
        # address Google would send the member to; disconnecting puts them back to not connected.
        self.calendar: dict[str, Any] = {
            "available": False,
            "can_connect": False,
            "status": None,
            "people": None,
        }
        self.calendar_status = 200
        self.connect_url = "https://accounts.example.test/auth?state=synthetic"
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
        if path == "profile" and self.profile_delay:
            await asyncio.sleep(self.profile_delay)
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
            return 200, self.tenants
        if path == "profile" and method == "GET" and not platform:
            if self.profile_status != 200:
                return self.profile_status, {"detail": "unavailable"}
            return 200, TENANT_PROFILE
        # The rail asks for the HR tabs on pages that name a tenant; unused here.
        if path == "hr/context" and method == "GET":
            return 404, {"detail": "not stubbed"}
        if path == "services/active" and method == "GET":
            return 200, {"packages": self.upstream.packages}
        if path == "services/customer-replies" and method == "GET":
            # The same read the Inbox banner makes: `replies_off` is only said while Customer
            # support is active, because a suspended company can't turn replies on.
            return 200, {
                "mode": "off",
                "brand_name": None,
                "can_manage": role in {"tenant_admin", "platform_admin"},
                "warnings": [] if self.replies_suspended else ["replies_off"],
            }
        if not path.startswith("bookings/"):
            self.unexpected.append(f"{method} {path}?{params}")
            return 500, {"detail": "unexpected synthetic call"}
        # Platform admins must always name a company; members never do.
        named = params.get("client_id")
        if (named in {tenant["id"] for tenant in self.tenants}) != platform or (
            not platform and named is not None
        ):
            self.unexpected.append(f"{method} {path} with params {params}")
            return 400, {"detail": "tenant mismatch"}
        route = path.removeprefix("bookings/")
        # Booking changes are open to staff; setup changes are for an Admin.
        is_booking = (
            route == "bookings" or route.startswith("bookings/") or route.startswith("calendar/")
        )
        if method != "GET" and role == "read_only":
            return 403, {"detail": "bookings changes require staff or an administrator"}
        if method != "GET" and not is_booking and role not in {"tenant_admin", "platform_admin"}:
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

    def answer_google(
        self, method: str, route: str, params: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        if self.calendar_status != 200:
            return self.calendar_status, {"detail": "unavailable"}
        if route == "calendar/status":
            return 200, self.calendar
        if route == "calendar/connect":
            return 200, {"url": self.connect_url}
        if route == "calendar/disconnect":
            self.calendar = self.calendar | {"status": "not_connected"}
            return 204, None
        self.unexpected.append(f"{method} {route}")
        return 500, {"detail": "unexpected synthetic call"}

    def do_settings(
        self, method: str, route: str, params: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        if method == "GET":
            if self.settings_status != 200:
                return self.settings_status, {"detail": "unavailable"}
            return 200, self.settings | {"replies_off": self.replies_off}
        if body["version"] != self.settings["version"]:
            return 409, {"detail": "version_conflict"}
        self.settings = body | {"version": body["version"] + 1}
        return 200, self.settings | {"replies_off": self.replies_off}

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

    def in_window(self, row: dict[str, Any], params: dict[str, str]) -> bool:
        return instant(row["ends_at"]) > instant(params["from"]) and instant(
            row["starts_at"]
        ) < instant(params["to"])

    def do_calendar(
        self, method: str, route: str, params: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        if route.startswith("calendar/"):  # the Google Calendar routes share the prefix
            return self.answer_google(method, route, params, body)
        rows = [
            row
            for row in self.bookings
            if row["status"] != "cancelled" and self.in_window(row, params)
        ]
        person, resource = params.get("person"), params.get("resource")
        filtered = bool(person or resource)
        if person:
            rows = [r for r in rows if any(p["id"] == person for p in r["people"])]
        if resource:
            rows = [r for r in rows if any(x["id"] == resource for x in r["resources"])]
        # A private meeting the viewer is not part of is a bare busy time when filtered.
        rows = [r for r in rows if not (filtered and r["redacted"])]
        if self.upstream.me["role"] == "read_only":
            rows = [r | {"customer": None, "guests": [], "can_change": False} for r in rows]
        busy = [b for b in self.busy if self.in_window(b, params)] if filtered else []
        return 200, {"items": rows, "busy": busy}

    def do_availability(
        self, method: str, route: str, params: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        found = [
            s for s in self.slots if instant(params["from"]) <= instant(s) < instant(params["to"])
        ]
        return 200, [
            {
                "starts_at": s,
                "ends_at": (instant(s) + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "people": [],
                "resources": [],
            }
            for s in found
        ]

    def do_customers(
        self, method: str, route: str, params: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        if self.upstream.me["role"] == "read_only":
            return 403, {"detail": "customer details are not available to read-only members"}
        q = params.get("q", "").lower()
        return 200, [c for c in self.customers if q in (c["full_name"] or "").lower()]

    def do_bookings(
        self, method: str, route: str, params: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        # A booking or a move can be refused; the other actions are not queued for refusal.
        if (route == "bookings" or method == "PATCH") and self.refusals:
            return self.refusals.pop(0)
        if route == "bookings":
            minutes = body.get("minutes") or next(
                (t["duration_minutes"] for t in self.types if t["id"] == body.get("type_id")), 30
            )
            row = booking(
                100 + len(self.bookings),
                body["starts_at"],
                (instant(body["starts_at"]) + timedelta(minutes=minutes)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                title=body.get("title") or "Consultation",
                type_id=body.get("type_id"),
                kind="meeting" if body.get("title") else "appointment",
                email=self.mail,
                customer_told=self.customer_told,
            )
            self.bookings.append(row)
            return 201, row
        row = next(b for b in self.bookings if b["id"] == route.split("/")[1])
        if method == "PATCH":
            if body["version"] != row["version"]:
                return 409, {
                    "detail": "stale",
                    "message": "This booking was changed by someone else.",
                }
            length = instant(row["ends_at"]) - instant(row["starts_at"])
            row.update(
                starts_at=body["starts_at"],
                ends_at=(instant(body["starts_at"]) + length).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        else:
            action = route.rsplit("/", 1)[1]
            replaced = next(
                (b for b in self.bookings if b["id"] == row["replaces_booking_id"]), None
            )
            if replaced is not None:
                # Either answer closes the request, so its booking waits for nothing.
                replaced["pending_change_id"] = None
            if action == "confirm" and replaced is not None:
                # Confirming a request moves the booking it replaces, which keeps its id.
                replaced.update(
                    starts_at=row["starts_at"],
                    ends_at=row["ends_at"],
                    version=replaced["version"] + 1,
                )
                row["status"] = "cancelled"
                return 200, replaced | {"email": self.mail, "customer_told": self.customer_told}
            if action == "meet":
                if self.meet_works:
                    row.update(meeting_link="https://meet.example.test/made", meet_missing=False)
                return 200, row
            status = {"cancel": "cancelled", "decline": "cancelled", "confirm": "confirmed"}
            row["status"] = status.get(action, row["status"])
        row["version"] += 1
        return 200, row | {"email": self.mail, "customer_told": self.customer_told}

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
    timezone: str = BROWSER_ZONE,
) -> AsyncIterator[Page]:
    site.upstream.me, site.upstream.packages = make_me(role), packages
    context = await browser.new_context(
        viewport={"width": width, "height": height}, timezone_id=timezone
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
            await expect(page.get_by_test_id("calendar-week")).to_be_visible(timeout=WAIT)

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


def rich_world(upstream: Upstream) -> World:
    """The rich company, with its second person active too."""
    world = World(upstream, rich=True)
    world.people[1]["is_active"] = True
    return world


async def fill_meeting(
    dialog: Any, title: str, day: str = "2099-11-10", clock: str = "10:00"
) -> None:
    await dialog.get_by_test_id("booking-type").select_option(value="")
    await dialog.get_by_test_id("booking-title").fill(title)
    await dialog.get_by_test_id("booking-colleagues").get_by_label("Aiman Hakim").check()
    await dialog.get_by_test_id("booking-day").fill(day)
    await dialog.get_by_test_id("booking-time").fill(clock)


def availability_asked(world: World) -> dict[str, str]:
    """What the last free-times request asked for, or nothing yet."""
    asked = [c["params"] for c in world.calls if c["path"] == "bookings/availability"]
    return asked[-1] if asked else {}


async def until(check: Any, seconds: float = 10) -> None:
    for _ in range(int(seconds * 10)):
        if check():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("the page did not get there")


def calendar_reads(world: World) -> list[dict[str, str]]:
    return [c["params"] for c in world.calls if c["path"] == "bookings/calendar"]


async def test_staff_book_a_meeting_and_a_walk_in_customer(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = rich_world(site.upstream)
        world.mail = "off"
        day = date(2099, 11, 10)
        world.slots = [utc_at(day, "10:00"), utc_at(day, "11:00")]
        monday = monday_of(COMPANY_ZONE)
        # Three bookings this week. The server says which ones this staff member may change: one
        # they attend, one they made for someone else (not on it), and one that is not theirs.
        siti = {"id": SITI, "display_name": "Siti Nur", "is_active": True}
        world.bookings = [
            booking(1, utc_at(monday, "09:00"), utc_at(monday, "10:00"), title="Attending"),
            booking(
                2,
                utc_at(monday, "11:00"),
                utc_at(monday, "12:00"),
                title="Made by me",
                people=[siti],
            ),
            booking(
                3,
                utc_at(monday, "13:00"),
                utc_at(monday, "14:00"),
                title="Theirs",
                people=[siti],
                can_change=False,
            ),
        ]
        async with bookings_page(
            browser, site, world, role="tenant_staff", path="/portal/bookings"
        ) as page:
            await expect(page.get_by_test_id("calendar-week")).to_be_visible(timeout=WAIT)

            # Staff get Move and Cancel where the server says they may: on what they attend and on
            # what they made for someone else, also after a reload and a visit to another page.
            for mine in ("Attending", "Made by me"):
                await page.get_by_test_id("booking").filter(has_text=mine).click()
                await expect(page.get_by_test_id("details-move")).to_be_visible()
                await expect(page.get_by_test_id("details-cancel")).to_be_visible()
                await page.keyboard.press("Escape")
                await expect(page.get_by_test_id("booking-details")).to_have_count(0)
            await page.reload()
            await expect(page.get_by_test_id("calendar-week")).to_be_visible(timeout=WAIT)
            await page.get_by_test_id("bookings-subnav-types").click()
            await expect(page.get_by_test_id("type-card")).to_have_count(1, timeout=WAIT)
            await page.get_by_test_id("bookings-subnav-calendar").click()
            await expect(page.get_by_test_id("calendar-week")).to_be_visible(timeout=WAIT)
            await page.get_by_test_id("booking").filter(has_text="Made by me").click()
            await expect(page.get_by_test_id("details-move")).to_be_visible()
            await expect(page.get_by_test_id("details-cancel")).to_be_visible()
            await page.keyboard.press("Escape")
            await page.get_by_test_id("booking").filter(has_text="Theirs").click()
            await expect(page.get_by_test_id("details-readonly")).to_have_text(
                "Only the person who made a booking, or someone on it, can change it."
            )
            await expect(page.get_by_test_id("details-move")).to_have_count(0)
            await expect(page.get_by_test_id("details-cancel")).to_have_count(0)
            await page.keyboard.press("Escape")

            # An internal meeting with a room, a colleague, a guest and an online link.
            await page.get_by_test_id("booking-new").click()
            dialog = page.get_by_test_id("booking-dialog")
            await fill_meeting(dialog, "Planning")
            await dialog.get_by_test_id("booking-minutes").fill("45")
            await dialog.get_by_test_id("booking-rooms").get_by_label("Meeting room").check()
            await dialog.get_by_test_id("booking-location").select_option("online")
            await dialog.get_by_test_id("booking-link").fill("https://meet.example.test/plan")
            await dialog.get_by_test_id("booking-guest-add").click()
            await dialog.get_by_test_id("booking-guest-email").fill("guest@example.test")
            # Free times come from the people, the room and the length, read on the company's clock.
            await until(lambda: availability_asked(world).get("resources") == ROOM)
            asked = availability_asked(world)
            assert asked["minutes"] == "45" and asked["people"] == AIMAN
            await dialog.get_by_test_id("free-times-11:00").click()
            await expect(dialog.get_by_test_id("booking-time")).to_have_value("11:00")
            await dialog.get_by_test_id("booking-save").click()
            await expect(page.get_by_test_id("booking-dialog")).to_have_count(0)
            await expect(page.get_by_test_id("calendar-notice")).to_have_text(
                "Booked. Mail is off, so nobody was emailed."
            )
            assert writes(world, "POST", "bookings/bookings") == [
                {
                    "starts_at": as_sent(utc_at(day, "11:00")),
                    "people": [AIMAN],
                    "resources": [ROOM],
                    "guests": [{"name": None, "email": "guest@example.test"}],
                    "acknowledged": [],
                    "title": "Planning",
                    "minutes": 45,
                    "private": False,
                    "location_kind": "online",
                    "meeting_link": "https://meet.example.test/plan",
                }
            ]

            # A walk-in customer, from an empty customer list, for a booking type.
            await page.get_by_test_id("booking-new").click()
            dialog = page.get_by_test_id("booking-dialog")
            await expect(dialog.get_by_test_id("booking-customer-none")).to_have_text(
                "No customers yet. Add the first one below."
            )
            await dialog.get_by_test_id("booking-people").get_by_label("Aiman Hakim").check()
            await dialog.get_by_test_id("booking-walk-name").fill(CUSTOMER_NAME)
            await dialog.get_by_test_id("booking-walk-phone").fill("012-345 6789")
            await dialog.get_by_test_id("booking-day").fill("2099-11-10")
            await dialog.get_by_test_id("free-times-10:00").click()
            await dialog.get_by_test_id("booking-save").click()
            await expect(page.get_by_test_id("booking-dialog")).to_have_count(0)
            second = writes(world, "POST", "bookings/bookings")[1]
            assert second["type_id"] == TYPE
            assert second["people"] == [AIMAN]
            assert second["walk_in"] == {
                "name": CUSTOMER_NAME,
                "phone": "012-345 6789",
                "email": None,
            }
            assert second["starts_at"] == as_sent(utc_at(day, "10:00"))
            assert [c for c in world.calls if c["path"] == "bookings/customers"]

    await with_browser(body)


async def test_a_warning_is_book_anyway_and_a_refusal_has_no_override(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = rich_world(site.upstream)
        async with bookings_page(
            browser, site, world, role="tenant_staff", path="/portal/bookings"
        ) as page:
            await expect(page.get_by_test_id("calendar-week")).to_be_visible(timeout=WAIT)
            await page.get_by_test_id("booking-new").click()
            dialog = page.get_by_test_id("booking-dialog")
            await fill_meeting(dialog, "Late meeting", clock="21:00")
            findings = ["closed_day", "outside_hours"]
            world.refusals = [
                (
                    409,
                    {
                        "detail": "needs_acknowledging",
                        "message": "This is a closed day. Outside working hours.",
                        "findings": findings,
                    },
                )
            ]
            await dialog.get_by_test_id("booking-save").click()
            warning = dialog.get_by_test_id("booking-warning")
            await expect(warning).to_contain_text("This is a closed day.")
            await expect(warning).to_contain_text("Outside working hours.")
            assert "leave" not in (await dialog.inner_text()).lower()
            await expect(dialog.get_by_test_id("booking-anyway")).to_have_text("Book anyway")
            assert [b["acknowledged"] for b in writes(world, "POST", "bookings/bookings")] == [[]]
            await dialog.get_by_test_id("booking-anyway").click()
            await expect(page.get_by_test_id("booking-dialog")).to_have_count(0)
            sent = writes(world, "POST", "bookings/bookings")
            assert [b["acknowledged"] for b in sent] == [[], findings]
            assert sent[1]["title"] == "Late meeting"

            # The time was taken by someone else: the form stays as it was.
            await page.get_by_test_id("booking-new").click()
            dialog = page.get_by_test_id("booking-dialog")
            await fill_meeting(dialog, "Busy slot")
            world.refusals = [
                (409, {"detail": "slot_taken", "message": "That time was just taken."})
            ]
            await dialog.get_by_test_id("booking-save").click()
            await expect(dialog.get_by_test_id("booking-taken")).to_contain_text(
                "That time was just taken."
            )
            await expect(dialog.get_by_test_id("booking-title")).to_have_value("Busy slot")
            await expect(dialog.get_by_test_id("booking-time")).to_have_value("10:00")
            await dialog.get_by_role("button", name="Pick another time").click()
            await expect(dialog.get_by_test_id("booking-time")).to_be_focused()
            await expect(dialog.get_by_test_id("booking-taken")).to_have_count(0)

            # Not available that day: the sentence, and no way past it.
            world.refusals = [
                (
                    409,
                    {
                        "detail": "refused",
                        "message": "Not available that day.",
                        "findings": ["unavailable"],
                    },
                )
            ]
            await dialog.get_by_test_id("booking-save").click()
            await expect(dialog.get_by_test_id("booking-refused")).to_have_text(
                "Not available that day."
            )
            await expect(dialog.get_by_test_id("booking-anyway")).to_have_count(0)
            assert "leave" not in (await dialog.inner_text()).lower()

            # Another time works, and nothing is acknowledged.
            await dialog.get_by_test_id("booking-time").fill("11:00")
            await dialog.get_by_test_id("booking-save").click()
            await expect(page.get_by_test_id("booking-dialog")).to_have_count(0)
            assert writes(world, "POST", "bookings/bookings")[-1]["acknowledged"] == []

    await with_browser(body)


async def test_calendar_and_blocks_follow_the_company_zone_not_the_browser(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = rich_world(site.upstream)
        monday = monday_of(COMPANY_ZONE)
        tuesday, wednesday, thursday = (monday + timedelta(days=n) for n in (1, 2, 3))
        customer = {
            "id": "33333333-3333-4333-8333-000000000001",
            "full_name": CUSTOMER_NAME,
            "phone": "012-345 6789",
            "email": None,
        }
        siti = {"id": SITI, "display_name": "Siti Nur", "is_active": True}
        world.bookings = [
            # 10:00 on the company's Monday is Sunday evening in New York.
            booking(1, utc_at(monday, "10:00"), utc_at(monday, "11:00"), customer=customer),
            booking(
                2,
                utc_at(monday, "10:30"),
                utc_at(monday, "11:30"),
                title="Site visit",
                people=[siti],
            ),
            booking(
                3,
                utc_at(tuesday, "14:00"),
                utc_at(tuesday, "15:00"),
                kind="meeting",
                title="Busy",
                private=True,
                redacted=True,
                people=[],
            ),
            booking(
                4,
                utc_at(wednesday, "09:00"),
                utc_at(wednesday, "09:30"),
                title="Clash",
                conflict="unavailable",
            ),
            booking(
                5,
                utc_at(thursday, "09:00"),
                utc_at(thursday, "10:00"),
                title="Waiting",
                status="pending",
            ),
        ]
        # Sunday 22:00 to Monday 02:00: it began in the week before, and goes on into this one.
        sunday = monday - timedelta(days=1)
        world.bookings.append(
            booking(6, utc_at(sunday, "22:00"), utc_at(monday, "02:00"), title="Overnight")
        )
        world.busy = [
            {
                "starts_at": utc_at(monday, "14:00"),
                "ends_at": utc_at(monday, "15:00"),
                "label": "Busy",
            }
        ]
        world.blocks = [
            {
                "id": "88888888-8888-4888-8888-000000000009",
                "subject_kind": "resource",
                "subject_id": ROOM,
                "starts_at": utc_at(date(2099, 11, 10), "09:00"),
                "ends_at": utc_at(date(2099, 11, 10), "18:00"),
                "note": None,
                "created_by": ME,
            }
        ]
        async with bookings_page(browser, site, world, path="/portal/bookings") as page:
            await expect(page.get_by_test_id("calendar-week")).to_be_visible(timeout=WAIT)
            await expect(page.get_by_test_id("calendar-range")).to_have_text(week_title(monday))
            first = calendar_reads(world)[0]
            assert first["from"] == as_sent(utc_at(monday, "00:00"))
            assert first["to"] == as_sent(utc_at(monday + timedelta(days=7), "00:00"))
            assert "person" not in first and "resource" not in first

            column = page.locator(f'[data-testid="calendar-day"][data-day="{monday}"]')
            monday_bookings = column.get_by_test_id("booking")
            await expect(monday_bookings).to_have_count(3)
            first = monday_bookings.filter(has_text="Consultation")
            second = monday_bookings.filter(has_text="Site visit")
            await expect(first).to_contain_text("10:00-11:00")
            # Overlapping bookings of different people sit side by side.
            one, two = await first.bounding_box(), await second.bounding_box()
            assert one and two and one["x"] + one["width"] <= two["x"] + 1, (one, two)

            # The part of the overnight booking after midnight is drawn at the top of Monday,
            # with its real start and end, and opens the same booking.
            overnight = monday_bookings.filter(has_text="Overnight")
            await expect(overnight).to_contain_text("22:00-02:00")
            top, held = await column.bounding_box(), await overnight.bounding_box()
            assert top and held and abs(held["y"] - top["y"]) <= 3, (top, held)
            assert 90 <= held["height"] <= 100, held  # two hours at 48 px an hour
            await overnight.click()
            await expect(page.get_by_test_id("details-when")).to_have_text(
                f"{sunday.day} {sunday.strftime('%b')} {sunday.year}, 22:00 to "
                f"{monday.day} {monday.strftime('%b')} {monday.year}, 02:00"
            )
            await page.keyboard.press("Escape")

            # A pending booking says so, and a conflict is flagged.
            await expect(
                page.get_by_test_id("booking").filter(has_text="Waiting")
            ).to_have_attribute("aria-label", re.compile("waiting to be confirmed"))
            await expect(page.get_by_test_id("booking-conflict")).to_have_count(1)
            assert "leave" not in (await page.get_by_test_id("calendar").inner_text()).lower()
            # The flag is on a 30-minute block's first line, inside its box, with its sentence.
            clash = page.get_by_test_id("booking").filter(has_text="Clash")
            flag = clash.get_by_test_id("booking-conflict")
            await expect(flag).to_have_attribute(
                "aria-label", "Someone on this booking is unavailable"
            )
            block, mark = await clash.bounding_box(), await flag.bounding_box()
            assert block and mark and block["height"] < 30, (block, mark)
            assert mark["width"] > 0 and mark["height"] > 0, mark
            assert block["x"] <= mark["x"] and block["y"] <= mark["y"], (block, mark)
            assert mark["x"] + mark["width"] <= block["x"] + block["width"], (block, mark)
            assert mark["y"] + mark["height"] <= block["y"] + block["height"], (block, mark)

            # A redacted private meeting is "Busy" with no details; an Admin can still change it.
            hidden = page.get_by_test_id("booking-busy")
            await expect(hidden).to_have_count(1)
            await expect(hidden).to_contain_text("Busy")
            await hidden.click()
            details = page.get_by_test_id("booking-details")
            await expect(details).to_contain_text("This is a private meeting.")
            await expect(details.get_by_test_id("details-customer")).to_have_count(0)
            await expect(details.get_by_test_id("details-people")).to_have_count(0)
            await expect(details.get_by_test_id("details-move")).to_be_visible()
            await expect(details.get_by_test_id("details-cancel")).to_be_visible()
            await page.keyboard.press("Escape")

            # Filtering asks for that person or room; other private meetings are plain "Busy".
            await page.get_by_test_id("calendar-filter").select_option(f"person:{AIMAN}")
            await expect(page.get_by_test_id("busy")).to_have_count(1)
            await expect(page.get_by_test_id("busy")).to_contain_text("Busy")
            await expect(page.get_by_test_id("booking-busy")).to_have_count(0)
            assert calendar_reads(world)[-1]["person"] == AIMAN
            await page.get_by_test_id("calendar-filter").select_option(f"resource:{ROOM}")
            await expect(page.get_by_test_id("booking")).to_have_count(0)
            assert calendar_reads(world)[-1]["resource"] == ROOM
            await page.get_by_test_id("calendar-filter").select_option("")

            # The next week is the company's next week.
            await page.get_by_test_id("calendar-next").click()
            await expect(page.get_by_test_id("calendar-range")).to_have_text(
                week_title(monday + timedelta(days=7))
            )
            assert calendar_reads(world)[-1]["from"] == as_sent(
                utc_at(monday + timedelta(days=7), "00:00")
            )
            await page.get_by_test_id("calendar-today").click()
            await expect(page.get_by_test_id("calendar-range")).to_have_text(week_title(monday))

            # The first part of the overnight booking is on the Sunday of the week before.
            await page.get_by_test_id("calendar-prev").click()
            await expect(page.get_by_test_id("calendar-range")).to_have_text(
                week_title(monday - timedelta(days=7))
            )
            before = page.locator(f'[data-testid="calendar-day"][data-day="{sunday}"]')
            piece = before.get_by_test_id("booking").filter(has_text="Overnight")
            await expect(piece).to_contain_text("22:00-02:00")
            night, held = await before.bounding_box(), await piece.bounding_box()
            assert night and held
            assert abs(held["y"] + held["height"] - (night["y"] + night["height"])) <= 3, (
                night,
                held,
            )
            await page.get_by_test_id("calendar-today").click()
            await expect(page.get_by_test_id("calendar-range")).to_have_text(week_title(monday))

            # The unavailable times panel reads a stored time on the company's clock too.
            await page.get_by_test_id("bookings-subnav-people").click()
            await expect(page.get_by_test_id("block")).to_contain_text(
                "Meeting room: 10 Nov 2099, 09:00 to 18:00", timeout=WAIT
            )
            await page.get_by_test_id("block-who").select_option(f"resource:{ROOM}")
            await page.get_by_test_id("block-start-day").fill("2099-11-11")
            await page.get_by_test_id("block-add").click()
            await expect(page.get_by_test_id("block")).to_have_count(2)
            added = writes(world, "POST", "bookings/blocks")[0]
            assert added["starts_at"] == as_sent(utc_at(date(2099, 11, 11), "09:00"))
            assert added["ends_at"] == as_sent(utc_at(date(2099, 11, 11), "18:00"))

    await with_browser(body)


async def test_booking_details_links_and_the_admin_changes(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = rich_world(site.upstream)
        monday = monday_of(COMPANY_ZONE)
        tuesday, wednesday = (monday + timedelta(days=n) for n in (1, 2))
        address = "1 Jalan Contoh, Synthetic City"
        world.bookings = [
            booking(
                1,
                utc_at(monday, "09:00"),
                utc_at(monday, "10:00"),
                title="Site visit",
                location_kind="in_person",
                address=address,
            ),
            booking(
                2,
                utc_at(tuesday, "09:00"),
                utc_at(tuesday, "10:00"),
                title="Online chat",
                location_kind="online",
                meeting_link="https://meet.example.test/chat",
            ),
            booking(
                3,
                utc_at(wednesday, "09:00"),
                utc_at(wednesday, "10:00"),
                title="Unsafe link",
                location_kind="online",
                meeting_link="http://meet.example.test/plain",
            ),
        ]
        async with bookings_page(browser, site, world, path="/portal/bookings") as page:
            await expect(page.get_by_test_id("calendar-week")).to_be_visible(timeout=WAIT)
            details = page.get_by_test_id("booking-details")

            # In person: Maps and Waze, both opened safely, and no meeting link.
            await page.get_by_test_id("booking").filter(has_text="Site visit").click()
            maps = details.get_by_test_id("details-maps")
            waze = details.get_by_test_id("details-waze")
            await expect(maps).to_have_attribute(
                "href",
                "https://www.google.com/maps/search/?api=1&query=1%20Jalan%20Contoh%2C%20Synthetic%20City",
            )
            await expect(waze).to_have_attribute(
                "href",
                "https://waze.com/ul?q=1%20Jalan%20Contoh%2C%20Synthetic%20City&navigate=yes",
            )
            for link in (maps, waze):
                await expect(link).to_have_attribute("rel", "noopener noreferrer")
                await expect(link).to_have_attribute("target", "_blank")
            await expect(details.get_by_test_id("details-link")).to_have_count(0)
            await expect(details.get_by_test_id("details-when")).to_have_text(
                f"{monday.day} {monday.strftime('%b')} {monday.year}, 09:00 to 10:00"
            )

            # Cancelling asks first, sends the booking's version, and reports the mail.
            world.mail = "none"
            await details.get_by_test_id("details-cancel").click()
            await expect(details.get_by_test_id("details-ask")).to_contain_text(
                "Cancel this booking?"
            )
            assert not writes(world, "POST", f"bookings/bookings/{world.bookings[0]['id']}/cancel")
            await details.get_by_test_id("details-ask-yes").click()
            await expect(page.get_by_test_id("booking-details")).to_have_count(0)
            await expect(page.get_by_test_id("calendar-notice")).to_have_text(
                "Cancelled. Nobody on this booking has an email address."
            )
            assert writes(world, "POST", f"bookings/bookings/{world.bookings[0]['id']}/cancel") == [
                {"version": 1}
            ]
            await expect(page.get_by_test_id("booking")).to_have_count(2)

            # Online: the link only when it is https, opened safely, and no map.
            await page.get_by_test_id("booking").filter(has_text="Online chat").click()
            link = details.get_by_test_id("details-link")
            await expect(link).to_have_attribute("href", "https://meet.example.test/chat")
            await expect(link).to_have_attribute("rel", "noopener noreferrer")
            await expect(details.get_by_test_id("details-maps")).to_have_count(0)

            # Moving: a stale booking says so and offers a reload; then the move goes through.
            await details.get_by_test_id("details-move").click()
            await details.get_by_test_id("move-day").fill(str(tuesday + timedelta(days=7)))
            await details.get_by_test_id("move-time").fill("14:00")
            world.refusals = [
                (409, {"detail": "stale", "message": "This booking was changed by someone else."})
            ]
            await details.get_by_test_id("move-save").click()
            await expect(details.get_by_test_id("booking-stale")).to_contain_text(
                "This booking was changed by someone else."
            )
            await details.get_by_role("button", name="Reload the calendar").click()
            await expect(page.get_by_test_id("booking-details")).to_have_count(0)
            await page.get_by_test_id("booking").filter(has_text="Online chat").click()
            await details.get_by_test_id("details-move").click()
            await details.get_by_test_id("move-day").fill(str(tuesday + timedelta(days=7)))
            await details.get_by_test_id("move-time").fill("14:00")
            world.mail = "failed"
            await details.get_by_test_id("move-save").click()
            await expect(page.get_by_test_id("calendar-notice")).to_have_text(
                "Moved. We couldn't email everyone. Some may not have got it."
            )
            moves = writes(world, "PATCH", f"bookings/bookings/{world.bookings[1]['id']}")
            assert moves[-1] == {
                "version": 1,
                "starts_at": as_sent(utc_at(tuesday + timedelta(days=7), "14:00")),
                "acknowledged": [],
            }
            await expect(page.get_by_test_id("booking")).to_have_count(1)

            # A link that is not https is never shown as a link.
            await page.get_by_test_id("booking").filter(has_text="Unsafe link").click()
            await expect(details.get_by_test_id("details-where")).to_contain_text("Online")
            await expect(details.get_by_test_id("details-link")).to_have_count(0)
            assert "http://meet.example.test" not in await details.inner_text()

    await with_browser(body)


async def test_read_only_member_sees_the_calendar_without_customers_or_buttons(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = rich_world(site.upstream)
        monday = monday_of(COMPANY_ZONE)
        customer = {
            "id": "33333333-3333-4333-8333-000000000001",
            "full_name": CUSTOMER_NAME,
            "phone": "012-345 6789",
            "email": "aina@example.test",
        }
        world.bookings = [
            booking(1, utc_at(monday, "09:00"), utc_at(monday, "10:00"), customer=customer)
        ]
        async with bookings_page(
            browser, site, world, role="read_only", path="/portal/bookings"
        ) as page:
            await expect(page.get_by_test_id("booking")).to_have_count(1, timeout=WAIT)
            await expect(page.get_by_test_id("booking-new")).to_have_count(0)
            await page.get_by_test_id("booking").click()
            details = page.get_by_test_id("booking-details")
            await expect(details).to_be_visible()
            await expect(details.get_by_test_id("details-customer")).to_have_count(0)
            await expect(details.get_by_test_id("details-actions")).to_have_count(0)
            await expect(details.get_by_test_id("details-readonly")).to_have_count(0)
            content = await page.content()
            assert CUSTOMER_NAME not in content and "aina@example.test" not in content
            assert not [c for c in world.calls if c["path"] == "bookings/customers"]
        assert not world.writes

    await with_browser(body)


async def test_an_unreadable_zone_shows_a_message_and_no_form(site: Site) -> None:
    async def body(browser: Any) -> None:
        # While the zone is being read there is no complaint, only a quiet loading line.
        world = rich_world(site.upstream)
        world.profile_delay = 2.0
        async with bookings_page(browser, site, world, path="/portal/bookings") as page:
            await expect(page.get_by_text("Loading…").first).to_be_visible(timeout=WAIT)
            await expect(page.get_by_test_id("zone-failed")).to_have_count(0)
            await expect(page.get_by_test_id("booking-new")).to_have_count(0)
            assert not [c for c in world.calls if c["path"] == "bookings/calendar"]
            await expect(page.get_by_test_id("calendar-week")).to_be_visible(timeout=WAIT)
            await expect(page.get_by_test_id("zone-failed")).to_have_count(0)

        # The profile fails: the message, no calendar, no booking button, and no blocks form.
        world = rich_world(site.upstream)
        world.profile_status = 500
        async with bookings_page(browser, site, world, path="/portal/bookings") as page:
            failed = page.get_by_test_id("zone-failed")
            await expect(failed).to_contain_text(
                "We could not read your company's time zone", timeout=WAIT
            )
            await expect(page.get_by_test_id("calendar-week")).to_have_count(0)
            await expect(page.get_by_test_id("calendar-agenda")).to_have_count(0)
            await expect(page.get_by_test_id("booking-new")).to_have_count(0)
            await page.get_by_test_id("bookings-subnav-people").click()
            await expect(page.get_by_test_id("blocks")).to_contain_text(
                "We could not read your company's time zone", timeout=WAIT
            )
            await expect(page.get_by_test_id("block-who")).to_have_count(0)
            await expect(page.get_by_test_id("block-add")).to_have_count(0)
            assert not [
                c for c in world.calls if c["path"] in {"bookings/calendar", "bookings/blocks"}
            ]
            assert not world.writes

    await with_browser(body)


async def test_platform_admin_calendar_follows_the_chosen_company(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = rich_world(site.upstream)
        world.tenants = [TENANT_PROFILE, TENANT2_PROFILE]
        async with bookings_page(
            browser, site, world, role="platform_admin", path="/bookings", packages=[]
        ) as page:
            select = page.get_by_test_id("bookings-tenant")
            await expect(select).to_have_value("", timeout=WAIT)
            assert not calendar_reads(world)
            await select.select_option(TENANT)
            kl = monday_of(COMPANY_ZONE)
            await expect(page.get_by_test_id("calendar-range")).to_have_text(
                week_title(kl), timeout=WAIT
            )
            reads = [c for c in world.calls if c["path"] == "bookings/calendar"]
            assert reads[-1]["params"]["client_id"] == TENANT
            assert reads[-1]["params"]["from"] == as_sent(utc_at(kl, "00:00"))
            assert page.url.endswith(f"/bookings?client_id={TENANT}")
            await expect(page.get_by_test_id("booking-new")).to_be_visible()

            # The other company has another clock, and the calendar reads it.
            await select.select_option(TENANT2)
            nz = monday_of("Pacific/Auckland")
            await expect(page.get_by_test_id("calendar-range")).to_have_text(week_title(nz))
            # The weeks may read alike, so wait for the other company's read itself.
            await until(lambda: calendar_reads(world)[-1].get("client_id") == TENANT2)
            assert calendar_reads(world)[-1]["from"] == as_sent(
                utc_at(nz, "00:00", "Pacific/Auckland")
            )
            assert page.url.endswith(f"/bookings?client_id={TENANT2}")
            assert all(
                c["params"].get("client_id") in {TENANT, TENANT2}
                for c in world.calls
                if c["path"].startswith("bookings")
            )

    await with_browser(body)


@pytest.mark.parametrize("theme", THEMES)
async def test_calendar_is_an_agenda_at_390_and_a_week_from_768(site: Site, theme: str) -> None:
    async def body(browser: Any) -> None:
        world = rich_world(site.upstream)
        monday = monday_of(COMPANY_ZONE)
        tuesday = monday + timedelta(days=1)
        long_title = "A synthetic booking with a very long title that must wrap and never overflow"
        world.bookings = [
            booking(
                0,
                utc_at(monday - timedelta(days=1), "22:00"),
                utc_at(monday, "02:00"),
                title="Overnight",
            ),
            booking(1, utc_at(monday, "10:00"), utc_at(monday, "11:00"), title=long_title),
            booking(
                2,
                utc_at(monday, "10:30"),
                utc_at(monday, "11:30"),
                title="Site visit",
                people=[{"id": SITI, "display_name": "Siti Nur", "is_active": True}],
            ),
            booking(
                3,
                utc_at(tuesday, "09:00"),
                utc_at(tuesday, "10:00"),
                title="Clash",
                conflict="unavailable",
                status="pending",
            ),
            booking(
                4,
                utc_at(tuesday, "14:00"),
                utc_at(tuesday, "15:00"),
                kind="meeting",
                title="Busy",
                redacted=True,
                people=[],
            ),
        ]
        async with bookings_page(
            browser, site, world, width=390, theme=theme, path="/portal/bookings"
        ) as page:
            await expect(page.get_by_test_id("calendar-agenda")).to_be_visible(timeout=WAIT)
            await expect(page.get_by_test_id("calendar-week")).to_have_count(0)
            await expect(page.get_by_test_id("booking")).to_have_count(4)
            await expect(page.get_by_test_id("calendar-agenda")).to_contain_text(
                "Continues from the day before"
            )
            await expect(page.get_by_test_id("booking-conflict")).to_have_count(1)
            for width in (320, 390):
                await page.set_viewport_size({"width": width, "height": 900})
                measured = await overflow(page)
                assert measured["scroll"] <= measured["inner"], ("agenda", theme, width, measured)

            await page.get_by_test_id("booking").first.click()
            details = page.get_by_test_id("booking-details")
            await expect(details).to_be_visible()
            inside = await details.evaluate("el => ({s: el.scrollWidth, c: el.clientWidth})")
            assert inside["s"] <= inside["c"], ("details", theme, inside)
            await page.keyboard.press("Escape")
            await expect(details).to_have_count(0)

            await page.get_by_test_id("booking-new").click()
            form = page.get_by_test_id("booking-dialog")
            await expect(form.get_by_test_id("booking-type")).to_be_visible()
            inside = await form.evaluate("el => ({s: el.scrollWidth, c: el.clientWidth})")
            assert inside["s"] <= inside["c"], ("form", theme, inside)
            measured = await overflow(page)
            assert measured["scroll"] <= measured["inner"], ("form page", theme, measured)
            await page.keyboard.press("Escape")
            await expect(form).to_have_count(0)

            await page.set_viewport_size({"width": 768, "height": 900})
            await expect(page.get_by_test_id("calendar-week")).to_be_visible()
            await expect(page.get_by_test_id("calendar-agenda")).to_have_count(0)
            measured = await overflow(page)
            assert measured["scroll"] <= measured["inner"], ("week", theme, measured)

    await with_browser(body)


# --- the review's fixes: flags, free times after a refusal, sentences, the customer ------------


async def test_a_booking_with_someone_switched_off_is_flagged_everywhere(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = rich_world(site.upstream)
        world.people[1]["is_active"] = False
        world.resources[0]["is_active"] = False
        monday = monday_of(COMPANY_ZONE)
        siti = {"id": SITI, "display_name": "Siti Nur", "is_active": False}
        room = {"id": ROOM, "name": "Meeting room", "is_active": False}
        world.bookings = [
            booking(
                1, utc_at(monday, "09:00"), utc_at(monday, "10:00"),
                title="Gone person", people=[siti],
            ),
            booking(
                2, utc_at(monday, "11:00"), utc_at(monday, "12:00"),
                title="Closed room", resources=[room],
            ),
            booking(3, utc_at(monday, "13:00"), utc_at(monday, "14:00"), title="All well"),
            booking(
                4, utc_at(monday, "15:00"), utc_at(monday, "16:00"),
                title="Busy", redacted=True, private=True, people=[], customer=None,
            ),
        ]  # fmt: skip
        sentence = "Siti Nur isn't taking bookings any more."
        async with bookings_page(browser, site, world, path="/portal/bookings") as page:
            await expect(page.get_by_test_id("calendar-week")).to_be_visible(timeout=WAIT)
            block = page.get_by_test_id("booking").filter(has_text="Gone person")
            flag = block.get_by_test_id("booking-inactive")
            await expect(flag).to_have_attribute("aria-label", sentence)
            await expect(
                page.get_by_test_id("booking")
                .filter(has_text="Closed room")
                .get_by_test_id("booking-inactive")
            ).to_have_attribute("aria-label", "Meeting room isn't available any more.")
            await expect(page.get_by_test_id("booking-inactive")).to_have_count(2)
            # An ordinary booking and a redacted private meeting carry no flag and name nobody.
            busy = page.get_by_test_id("booking-busy")
            await expect(busy.get_by_test_id("booking-inactive")).to_have_count(0)
            await expect(busy).to_have_attribute(
                "aria-label", re.compile(r"^\d\d:\d\d to \d\d:\d\d, Busy$")
            )
            # Their bookings can still be listed: both stay in the filter, marked.
            filter_ = page.get_by_test_id("calendar-filter")
            await expect(filter_.locator(f'option[value="person:{SITI}"]')).to_have_text(
                "Siti Nur (not taking bookings)"
            )
            await expect(filter_.locator(f'option[value="resource:{ROOM}"]')).to_have_text(
                "Meeting room (not available)"
            )
            await block.click()
            note = page.get_by_test_id("booking-details").get_by_test_id("booking-inactive-note")
            await expect(note).to_have_text(sentence)
            await page.keyboard.press("Escape")
            await page.get_by_test_id("booking").filter(has_text="All well").click()
            await expect(page.get_by_test_id("booking-inactive-note")).to_have_count(0)
            await page.keyboard.press("Escape")
            await busy.click()
            await expect(page.get_by_test_id("booking-inactive-note")).to_have_count(0)

        async with bookings_page(browser, site, world, path="/portal/bookings", width=390) as page:
            agenda = page.get_by_test_id("calendar-agenda")
            await expect(agenda).to_be_visible(timeout=WAIT)
            await expect(agenda).to_contain_text(sentence)
            await expect(agenda).to_contain_text("Meeting room isn't available any more.")
            await expect(agenda.get_by_test_id("booking-inactive")).to_have_count(2)

    await with_browser(body)


async def test_after_a_taken_time_the_free_times_are_read_again(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = rich_world(site.upstream)
        day = date(2099, 11, 10)
        ten, eleven = utc_at(day, "10:00"), utc_at(day, "11:00")
        world.slots = [ten, eleven]
        monday = monday_of(COMPANY_ZONE)
        world.bookings = [
            booking(
                1, utc_at(monday, "09:00"), utc_at(monday, "10:00"), title="To move",
                resources=[{"id": ROOM, "name": "Meeting room", "is_active": True}],
            )
        ]  # fmt: skip
        taken = (409, {"detail": "slot_taken", "message": "That time was just taken."})

        def reads() -> int:
            return len([c for c in world.calls if c["path"] == "bookings/availability"])

        async with bookings_page(
            browser, site, world, role="tenant_staff", path="/portal/bookings"
        ) as page:
            await expect(page.get_by_test_id("calendar-week")).to_be_visible(timeout=WAIT)
            # Booking: someone else takes 10:00 while the form is open.
            await page.get_by_test_id("booking-new").click()
            dialog = page.get_by_test_id("booking-dialog")
            await fill_meeting(dialog, "Race")
            await expect(dialog.get_by_test_id("free-times-10:00")).to_be_visible()
            before = reads()
            world.slots = [eleven]
            world.refusals = [taken]
            await dialog.get_by_test_id("booking-save").click()
            await expect(dialog.get_by_test_id("booking-taken")).to_be_visible()
            await expect(dialog.get_by_test_id("free-times-10:00")).to_have_count(0)
            await expect(dialog.get_by_test_id("free-times-11:00")).to_be_visible()
            assert reads() > before
            await page.keyboard.press("Escape")

            # Moving: the same, and the read names the booking's own people and rooms.
            world.slots = [ten, eleven]
            await page.get_by_test_id("booking").filter(has_text="To move").click()
            details = page.get_by_test_id("booking-details")
            await details.get_by_test_id("details-move").click()
            await details.get_by_test_id("move-day").fill(str(day))
            await expect(details.get_by_test_id("free-times-10:00")).to_be_visible()
            asked = availability_asked(world)
            assert asked["type_id"] == TYPE
            assert asked["people"] == AIMAN and asked["resources"] == ROOM
            before = reads()
            world.slots = [eleven]
            world.refusals = [taken]
            await details.get_by_test_id("move-time").fill("10:00")
            await details.get_by_test_id("move-save").click()
            await expect(details.get_by_test_id("booking-taken")).to_be_visible()
            await expect(details.get_by_test_id("free-times-10:00")).to_have_count(0)
            await expect(details.get_by_test_id("free-times-11:00")).to_be_visible()
            assert reads() > before

    await with_browser(body)


async def test_refusals_for_switched_off_things_are_sentences_and_the_form_says_so(
    site: Site,
) -> None:
    async def body(browser: Any) -> None:
        world = rich_world(site.upstream)
        world.people[1]["is_active"] = False
        world.types = [
            booking_type(TYPE, "Consultation", 60, delivery="named", person_ids=[AIMAN, SITI]),
            booking_type(
                "66666666-6666-4666-8666-000000000009",
                "Team session",
                45,
                delivery="several",
                audience="staff",
                person_ids=[AIMAN, SITI],
            ),
        ]
        async with bookings_page(
            browser, site, world, role="tenant_staff", path="/portal/bookings"
        ) as page:
            await expect(page.get_by_test_id("calendar-week")).to_be_visible(timeout=WAIT)
            await page.get_by_test_id("booking-new").click()
            dialog = page.get_by_test_id("booking-dialog")
            # A named person who is switched off is not offered.
            people = dialog.get_by_test_id("booking-people")
            await expect(people.get_by_label("Aiman Hakim")).to_be_visible()
            await expect(people.get_by_label("Siti Nur")).to_have_count(0)
            # A several-people type that includes them says so before anything is filled in.
            await dialog.get_by_test_id("booking-type").select_option(
                value="66666666-6666-4666-8666-000000000009"
            )
            await expect(dialog.get_by_test_id("booking-type-inactive")).to_have_text(
                "Siti Nur isn't taking bookings any more, so this booking type can't be booked "
                "until an Admin changes it."
            )
            # And a refusal that comes back is a sentence, never its code.
            world.refusals = [(422, {"detail": "person_inactive"})]
            await dialog.get_by_test_id("booking-day").fill("2099-11-10")
            await dialog.get_by_test_id("booking-time").fill("10:00")
            await dialog.get_by_test_id("booking-save").click()
            await expect(dialog.get_by_test_id("booking-error")).to_have_text(
                "That person isn't taking bookings any more. Choose someone else."
            )
            assert "person_inactive" not in await dialog.inner_text()

    await with_browser(body)


async def test_a_change_says_when_the_customer_was_not_told(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = rich_world(site.upstream)
        monday = monday_of(COMPANY_ZONE)
        world.bookings = [
            booking(1, utc_at(monday, "09:00"), utc_at(monday, "10:00"), title="Phone walk-in"),
            booking(2, utc_at(monday, "11:00"), utc_at(monday, "12:00"), title="Emailed one"),
            booking(3, utc_at(monday, "13:00"), utc_at(monday, "14:00"), title="No customer"),
        ]
        async with bookings_page(
            browser, site, world, role="tenant_staff", path="/portal/bookings"
        ) as page:
            await expect(page.get_by_test_id("calendar-week")).to_be_visible(timeout=WAIT)
            details = page.get_by_test_id("booking-details")
            notice = page.get_by_test_id("calendar-notice")
            later = str(monday + timedelta(days=7))

            async def move(title: str) -> None:
                await page.get_by_test_id("booking").filter(has_text=title).click()
                await details.get_by_test_id("details-move").click()
                await details.get_by_test_id("move-day").fill(later)
                await details.get_by_test_id("move-time").fill("15:00")
                await details.get_by_test_id("move-save").click()
                await expect(details).to_have_count(0)

            # Phone-only customer: the person delivering it was emailed, the customer was not.
            world.mail, world.customer_told = "sent", False
            await move("Phone walk-in")
            await expect(notice).to_have_text("Moved. Customer wasn't told.")
            # Said whatever the mail summary says.
            world.mail, world.customer_told = "none", False
            await move("Emailed one")
            await expect(notice).to_have_text(
                "Moved. Nobody on this booking has an email address. Customer wasn't told."
            )
            # Told, or no customer: nothing more to say.
            world.mail, world.customer_told = "sent", None
            await move("No customer")
            await expect(notice).to_have_text("Moved.")

    await with_browser(body)


# --- customers' own bookings, requests to move, replies Off, and the settings -----------------

REPLIES_OFF = "Customer replies are off, so customers get no automatic messages from Bookings."


def moment(day: date, clock: str) -> str:
    return f"{day.day} {day.strftime('%b')} {day.year}, {clock}"


def with_request(world: World, monday: date) -> tuple[dict[str, Any], dict[str, Any]]:
    """A confirmed booking on Monday, and the customer's pending request to move it to Wednesday."""
    original = booking(1, utc_at(monday, "09:00"), utc_at(monday, "10:00"), title="Consultation")
    asked = booking(
        2,
        utc_at(monday + timedelta(days=2), "14:00"),
        utc_at(monday + timedelta(days=2), "15:00"),
        title="Consultation",
        status="pending",
        source="bot",
        replaces_booking_id=original["id"],
        replaces_starts_at=original["starts_at"],
        replaces_ends_at=original["ends_at"],
        customer={
            "id": "33333333-3333-4333-8333-000000000001",
            "full_name": CUSTOMER_NAME,
            "phone": None,
            "email": None,
            "given_email": "given@example.test",
        },
    )
    original["pending_change_id"] = asked["id"]
    world.bookings = [original, asked]
    return original, asked


@pytest.mark.parametrize("role", ["tenant_admin", "tenant_staff", "read_only", "platform_admin"])
async def test_replies_off_says_so_on_every_page_and_links_only_for_those_who_can_change_it(
    site: Site, role: str
) -> None:
    async def body(browser: Any) -> None:
        world = rich_world(site.upstream)
        world.replies_off = True
        platform = role == "platform_admin"
        async with bookings_page(
            browser,
            site,
            world,
            role=role,
            path=f"/bookings?client_id={TENANT}" if platform else "/portal/bookings",
            packages=[] if platform else ACTIVE,
        ) as page:
            banner = page.get_by_test_id("bookings-replies-off")
            link = page.get_by_test_id("bookings-replies-link")
            for subnav in (None, "types", "people"):
                if subnav:
                    await page.get_by_test_id(f"bookings-subnav-{subnav}").click()
                await expect(banner).to_be_visible(timeout=WAIT)
                if role in {"tenant_admin", "platform_admin"}:
                    await expect(banner).to_have_text(f"{REPLIES_OFF} Turn replies on")
                    await expect(link).to_have_text("Turn replies on")
                    await expect(link).to_have_attribute(
                        "href", f"/services?client_id={TENANT}" if platform else "/portal/services"
                    )
                else:
                    await expect(banner).to_have_text(REPLIES_OFF)
                    await expect(link).to_have_count(0)
        assert not world.writes

    await with_browser(body)


async def test_no_banner_when_replies_are_on_or_the_company_has_no_replies_setting(
    site: Site,
) -> None:
    async def body(browser: Any) -> None:
        # A Bookings-only company, or one whose replies are on, is told `replies_off: false`.
        # The People & rooms page shows the settings once they are read, so the absence is checked
        # after the answer has been drawn, never before it arrived.
        world = rich_world(site.upstream)
        path = "/portal/bookings/people-and-rooms"
        async with bookings_page(browser, site, world, path=path) as page:
            await expect(page.get_by_test_id("settings-form")).to_be_visible(timeout=WAIT)
            await expect(page.get_by_test_id("bookings-replies-off")).to_have_count(0)
            assert "Customer replies are off" not in await page.content()

        # If the settings cannot be read, the page still works and nothing is claimed.
        world = rich_world(site.upstream)
        world.replies_off, world.settings_status = True, 500
        async with bookings_page(browser, site, world, path=path) as page:
            await expect(page.get_by_test_id("settings-load-error")).to_be_visible(timeout=WAIT)
            await expect(page.get_by_test_id("person-card")).to_have_count(2)
            await expect(page.get_by_test_id("bookings-replies-off")).to_have_count(0)

    await with_browser(body)


@pytest.mark.parametrize("role", ["tenant_admin", "platform_admin"])
async def test_a_suspended_company_gets_the_banner_without_a_dead_end_link(
    site: Site, role: str
) -> None:
    async def body(browser: Any) -> None:
        world = rich_world(site.upstream)
        world.replies_off, world.replies_suspended = True, True
        platform = role == "platform_admin"
        async with bookings_page(
            browser,
            site,
            world,
            role=role,
            path=f"/bookings?client_id={TENANT}" if platform else "/portal/bookings",
            packages=[] if platform else ACTIVE,
        ) as page:
            banner = page.get_by_test_id("bookings-replies-off")
            await expect(banner).to_have_text(REPLIES_OFF, timeout=WAIT)
            # Move to a page that reads again, and wait for the answer that could add the link.
            async with page.expect_response("**/console/v1/services/customer-replies*"):
                await page.get_by_test_id("bookings-subnav-people").click()
            await expect(page.get_by_test_id("settings-form")).to_be_visible(timeout=WAIT)
            await page.wait_for_timeout(300)  # the page has drawn what that answer says
            await expect(banner).to_have_text(REPLIES_OFF)
            await expect(page.get_by_test_id("bookings-replies-link")).to_have_count(0)

    await with_browser(body)


async def test_a_change_the_customer_asked_for_is_shown_and_answered(site: Site) -> None:
    async def body(browser: Any) -> None:
        monday = monday_of(COMPANY_ZONE)
        wednesday = monday + timedelta(days=2)
        sentence = f"Asked to move from {moment(monday, '09:00')} to {moment(wednesday, '14:00')}"

        world = rich_world(site.upstream)
        original, asked = with_request(world, monday)
        async with bookings_page(browser, site, world, path="/portal/bookings") as page:
            await expect(page.get_by_test_id("calendar-week")).to_be_visible(timeout=WAIT)
            blocks = page.get_by_test_id("booking")
            await expect(blocks).to_have_count(2)
            request_block = blocks.filter(has_text="14:00")
            await expect(request_block).to_have_attribute("aria-label", re.compile(sentence))
            await expect(page.get_by_test_id("booking-change-waiting")).to_have_count(1)
            details = page.get_by_test_id("booking-details")

            # The original says a change is waiting, and is otherwise as it was.
            await blocks.filter(has_text="09:00").click()
            await expect(details.get_by_test_id("details-change-waiting")).to_have_text(
                "A change is waiting."
            )
            await expect(details.get_by_test_id("details-change")).to_have_count(0)
            await expect(details.get_by_test_id("details-source")).to_have_count(0)
            # Moving or cancelling it would withdraw the customer's request, and says so first.
            withdraws = "This also withdraws the customer's request to move it."
            await details.get_by_test_id("details-move").click()
            await expect(details.get_by_test_id("details-move-form")).to_contain_text(withdraws)
            await details.get_by_role("button", name="Keep the time").click()
            await details.get_by_test_id("details-cancel").click()
            await expect(details.get_by_test_id("details-ask")).to_contain_text(withdraws)
            await details.get_by_role("button", name="No, keep it").click()
            await page.keyboard.press("Escape")
            assert not world.writes

            # The request: what it asks, who made it, and only Confirm and Decline.
            await request_block.click()
            await expect(details.get_by_test_id("details-change")).to_have_text(sentence)
            await expect(details.get_by_test_id("details-source")).to_have_text(
                "Booked by the customer"
            )
            await expect(details.get_by_test_id("details-given-email")).to_contain_text(
                "given@example.test"
            )
            await expect(details.get_by_test_id("details-given-email")).to_contain_text(
                "Customer gave this address"
            )
            await expect(details.get_by_test_id("details-confirm")).to_be_visible()
            await expect(details.get_by_test_id("details-decline")).to_be_visible()
            await expect(details.get_by_test_id("details-move")).to_have_count(0)
            await expect(details.get_by_test_id("details-cancel")).to_have_count(0)

            # Confirming moves the original, which keeps its id.
            await details.get_by_test_id("details-confirm").click()
            await expect(details).to_have_count(0)
            await expect(page.get_by_test_id("calendar-notice")).to_have_text("Moved.")
            assert writes(world, "POST", f"bookings/bookings/{asked['id']}/confirm") == [
                {"version": 1}
            ]
            await expect(blocks).to_have_count(1)
            await expect(blocks).to_have_attribute("aria-label", re.compile(r"14:00 to 15:00"))
            await expect(page.get_by_test_id("booking-change-waiting")).to_have_count(0)
            assert world.bookings[0]["id"] == original["id"]

        # Declining leaves the original where it was.
        world = rich_world(site.upstream)
        original, asked = with_request(world, monday)
        async with bookings_page(browser, site, world, path="/portal/bookings") as page:
            blocks = page.get_by_test_id("booking")
            await expect(blocks).to_have_count(2, timeout=WAIT)
            await blocks.filter(has_text="14:00").click()
            details = page.get_by_test_id("booking-details")
            await details.get_by_test_id("details-decline").click()
            await expect(details.get_by_test_id("details-ask")).to_contain_text(
                "Decline this request? The booking stays on its time."
            )
            await details.get_by_test_id("details-ask-yes").click()
            await expect(page.get_by_test_id("calendar-notice")).to_have_text("Declined.")
            assert writes(world, "POST", f"bookings/bookings/{asked['id']}/decline") == [
                {"version": 1}
            ]
            await expect(blocks).to_have_count(1)
            await expect(blocks).to_have_attribute("aria-label", re.compile(r"09:00 to 10:00"))
            await expect(page.get_by_test_id("booking-change-waiting")).to_have_count(0)

        # Staff who may not change it are offered no button, and the server decides which.
        world = rich_world(site.upstream)
        original, asked = with_request(world, monday)
        asked["can_change"] = False
        async with bookings_page(
            browser, site, world, role="tenant_staff", path="/portal/bookings"
        ) as page:
            await page.get_by_test_id("booking").filter(has_text="14:00").click(timeout=WAIT)
            details = page.get_by_test_id("booking-details")
            await expect(details.get_by_test_id("details-change")).to_have_text(sentence)
            await expect(details.get_by_test_id("details-actions")).to_have_count(0)
        assert not world.writes

    await with_browser(body)


async def test_a_change_to_another_week_shows_on_both_weeks(site: Site) -> None:
    """A move from this week to next: this week's booking says a change waits, next week's request
    says what it replaces, though the other week is not on screen.
    """

    async def body(browser: Any) -> None:
        monday = monday_of(COMPANY_ZONE)
        next_monday = monday + timedelta(days=7)
        world = rich_world(site.upstream)
        original = booking(1, utc_at(monday, "09:00"), utc_at(monday, "10:00"), title="Review")
        asked = booking(
            2,
            utc_at(next_monday + timedelta(days=1), "14:00"),
            utc_at(next_monday + timedelta(days=1), "15:00"),
            title="Review",
            status="pending",
            source="bot",
            replaces_booking_id=original["id"],
            replaces_starts_at=original["starts_at"],
            replaces_ends_at=original["ends_at"],
        )
        original["pending_change_id"] = asked["id"]
        world.bookings = [original, asked]
        sentence = (
            f"Asked to move from {moment(monday, '09:00')} "
            f"to {moment(next_monday + timedelta(days=1), '14:00')}"
        )
        async with bookings_page(browser, site, world, path="/portal/bookings") as page:
            blocks = page.get_by_test_id("booking")
            details = page.get_by_test_id("booking-details")
            await expect(blocks).to_have_count(1, timeout=WAIT)
            # This week holds only the original, and it says a change is waiting.
            await expect(page.get_by_test_id("booking-change-waiting")).to_have_count(1)
            await blocks.click()
            await expect(details.get_by_test_id("details-change-waiting")).to_have_text(
                "A change is waiting."
            )
            await details.get_by_test_id("details-cancel").click()
            await expect(details.get_by_test_id("details-ask")).to_contain_text(
                "This also withdraws the customer's request to move it."
            )
            await page.keyboard.press("Escape")

            # Next week holds only the request, and it names the time it replaces.
            await page.get_by_test_id("calendar-next").click()
            await expect(page.get_by_test_id("calendar-range")).to_have_text(
                week_title(next_monday)
            )
            await expect(blocks).to_have_count(1)
            await expect(blocks).to_have_attribute("aria-label", re.compile(sentence))
            await expect(blocks).to_contain_text("Asked to move")
            await blocks.click()
            await expect(details.get_by_test_id("details-change")).to_have_text(sentence)
            assert "Asked to move to" not in await page.content()

    await with_browser(body)


async def test_the_read_only_member_never_sees_the_address_a_customer_gave(site: Site) -> None:
    async def body(browser: Any) -> None:
        monday = monday_of(COMPANY_ZONE)
        world = rich_world(site.upstream)
        with_request(world, monday)
        async with bookings_page(
            browser, site, world, role="read_only", path="/portal/bookings"
        ) as page:
            await expect(page.get_by_test_id("booking")).to_have_count(2, timeout=WAIT)
            await page.get_by_test_id("booking").filter(has_text="14:00").click()
            details = page.get_by_test_id("booking-details")
            await expect(details.get_by_test_id("details-source")).to_have_text(
                "Booked by the customer"
            )
            await expect(details.get_by_test_id("details-given-email")).to_have_count(0)
            content = await page.content()
            assert "given@example.test" not in content
            assert "Customer gave this address" not in content

    await with_browser(body)


async def test_admin_changes_the_booking_settings(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        path = "/portal/bookings/people-and-rooms"
        async with bookings_page(browser, site, world, path=path) as page:
            panel = page.get_by_test_id("booking-settings")
            await expect(panel).to_be_visible(timeout=WAIT)
            reminder = panel.get_by_label("Remind people before a booking")
            pending = panel.get_by_label("How long a request waits for staff")
            limit = panel.get_by_label("Most upcoming bookings one customer can hold")
            unit = panel.get_by_test_id("settings-reminder-unit")
            error = panel.get_by_test_id("settings-error")
            await expect(reminder).to_have_value("1")
            await expect(unit).to_have_value("days")
            await expect(pending).to_have_value("24")
            await expect(limit).to_have_value("5")

            # Something the server would refuse is said first, and nothing is sent.
            await unit.select_option("hours")
            await reminder.fill("0.1")
            await panel.get_by_test_id("settings-save").click()
            await expect(error).to_have_text("Remind people at least 15 minutes before a booking.")
            await reminder.fill("2")
            await unit.select_option("days")
            await pending.fill("200")
            await panel.get_by_test_id("settings-save").click()
            await expect(error).to_contain_text("from 1 to 168")
            await pending.fill("48")
            await limit.fill("0")
            await panel.get_by_test_id("settings-save").click()
            await expect(error).to_contain_text("from 1 to 50")
            assert not writes(world, "PUT", "bookings/settings")

            # Days and hours are shown; minutes are stored, with the version that was read.
            await limit.fill("10")
            await panel.get_by_test_id("settings-save").click()
            await expect(panel.get_by_test_id("settings-notice")).to_have_text("Saved.")
            assert writes(world, "PUT", "bookings/settings") == [
                {"reminder_minutes": 2880, "pending_hours": 48, "customer_limit": 10, "version": 0}
            ]
            await unit.select_option("hours")
            await reminder.fill("1.5")
            await panel.get_by_test_id("settings-save").click()
            await expect(panel.get_by_test_id("settings-notice")).to_have_text("Saved.")
            assert writes(world, "PUT", "bookings/settings")[-1] == {
                "reminder_minutes": 90,
                "pending_hours": 48,
                "customer_limit": 10,
                "version": 1,
            }
            await page.reload()
            await expect(reminder).to_have_value("1.5", timeout=WAIT)
            await expect(unit).to_have_value("hours")

            # Someone else saved first: the sentence, and the form shows what is now saved.
            world.settings = world.settings | {"customer_limit": 3, "version": 7}
            await limit.fill("8")
            await panel.get_by_test_id("settings-save").click()
            await expect(error).to_contain_text("Someone else changed this while you were editing")
            await expect(limit).to_have_value("3")
            await panel.get_by_test_id("settings-save").click()
            await expect(panel.get_by_test_id("settings-notice")).to_have_text("Saved.")
            assert writes(world, "PUT", "bookings/settings")[-1]["version"] == 7

    await with_browser(body)


@pytest.mark.parametrize("role", ["tenant_staff", "read_only"])
async def test_staff_and_read_only_read_the_settings_without_controls(
    site: Site, role: str
) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        world.settings = {
            "reminder_minutes": 120,
            "pending_hours": 12,
            "customer_limit": 3,
            "version": 2,
        }
        async with bookings_page(
            browser, site, world, role=role, path="/portal/bookings/people-and-rooms"
        ) as page:
            values = page.get_by_test_id("settings-values")
            await expect(values).to_be_visible(timeout=WAIT)
            await expect(values).to_contain_text("Remind people before a booking")
            await expect(values).to_contain_text("2 hours")
            await expect(values).to_contain_text("How long a request waits for staff")
            await expect(values).to_contain_text("12 hours")
            await expect(values).to_contain_text("Most upcoming bookings one customer can hold")
            await expect(values).to_contain_text("3")
            for test_id in EDIT_CONTROLS:
                await expect(page.get_by_test_id(test_id)).to_have_count(0)
        assert not world.writes

    await with_browser(body)


@pytest.mark.parametrize("theme", THEMES)
async def test_replies_off_requests_and_settings_fit_320_390_and_768_in_every_theme(
    site: Site, theme: str
) -> None:
    async def body(browser: Any) -> None:
        monday = monday_of(COMPANY_ZONE)
        world = rich_world(site.upstream)
        world.replies_off = True
        with_request(world, monday)
        world.bookings[1]["title"] = "A synthetic request with a very long title that must wrap"
        async with bookings_page(
            browser, site, world, width=320, theme=theme, path="/portal/bookings"
        ) as page:
            await expect(page.get_by_test_id("bookings-replies-off")).to_be_visible(timeout=WAIT)
            await expect(page.get_by_test_id("booking")).to_have_count(2)
            for width in WIDTHS:
                await page.set_viewport_size({"width": width, "height": 900})
                await expect(page.get_by_test_id("booking")).to_have_count(2)
                measured = await overflow(page)
                assert measured["scroll"] <= measured["inner"], ("calendar", theme, width, measured)
            await page.set_viewport_size({"width": 320, "height": 900})
            await page.get_by_test_id("booking").filter(has_text="14:00").click()
            details = page.get_by_test_id("booking-details")
            await expect(details.get_by_test_id("details-change")).to_be_visible()
            inside = await details.evaluate("el => ({s: el.scrollWidth, c: el.clientWidth})")
            assert inside["s"] <= inside["c"], ("details", theme, inside)
            await page.keyboard.press("Escape")
            await page.get_by_test_id("bookings-subnav-people").click()
            await expect(page.get_by_test_id("booking-settings")).to_be_visible(timeout=WAIT)
            for width in WIDTHS:
                await page.set_viewport_size({"width": width, "height": 900})
                measured = await overflow(page)
                assert measured["scroll"] <= measured["inner"], ("settings", theme, width, measured)
                panel = page.get_by_test_id("booking-settings")
                inside = await panel.evaluate("el => ({s: el.scrollWidth, c: el.clientWidth})")
                assert inside["s"] <= inside["c"], ("settings panel", theme, width, inside)

    await with_browser(body)


# --- Google Calendar --------------------------------------------------------------------------
# The connect, reconnect and disconnect flows live on the Connections tab (tests/
# test_connections_browser.py); this page keeps a status line, the link and the team's view.

CONNECTABLE = {"available": True, "can_connect": True, "status": "not_connected", "people": None}


@pytest.mark.parametrize(
    ("role", "status", "word"),
    [
        ("tenant_staff", "not_connected", "Not connected"),
        ("tenant_admin", "connected", "Connected"),
    ],
)
async def test_the_calendar_page_shows_a_status_line_and_the_link(
    site: Site, role: str, status: str, word: str
) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        world.calendar = CONNECTABLE | {"status": status}
        async with bookings_page(browser, site, world, role=role, path="/portal/bookings") as page:
            line = page.get_by_test_id("google-calendar-line")
            await expect(line).to_be_visible(timeout=WAIT)
            await expect(page.get_by_test_id("google-calendar-line-status")).to_have_text(
                f"Google Calendar: {word}."
            )
            link = page.get_by_test_id("google-calendar-line-link")
            await expect(link).to_have_text("Manage in Connections")
            await expect(link).to_have_attribute("href", "/portal/connections")
            await expect(page.get_by_test_id("google-calendar-banner")).to_have_count(0)
            # No control of any kind is left here.
            panel = page.get_by_test_id("google-calendar")
            await expect(panel.get_by_role("button")).to_have_count(0)
            for gone in ("connect", "reconnect", "disconnect", "status"):
                await expect(page.get_by_test_id(f"google-calendar-{gone}")).to_have_count(0)
            await link.click()
            await page.wait_for_url(re.compile(r"/portal/connections$"), timeout=WAIT)
        assert not world.writes

    await with_browser(body)


async def test_needs_reconnect_keeps_the_banner_with_the_link(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        world.calendar = CONNECTABLE | {"status": "needs_reconnect"}
        async with bookings_page(
            browser, site, world, role="tenant_staff", path="/portal/bookings"
        ) as page:
            await expect(page.get_by_test_id("google-calendar-line-status")).to_have_text(
                "Google Calendar: Needs reconnecting.", timeout=WAIT
            )
            banner = page.get_by_test_id("google-calendar-banner")
            await expect(banner).to_contain_text(
                "Google stopped accepting the connection, so new bookings aren't reaching "
                "your calendar. Reconnect it on Connections."
            )
            await expect(banner).not_to_contain_text("Reconnect to start them again")
            link = banner.get_by_test_id("google-calendar-banner-link")
            await expect(link).to_have_attribute("href", "/portal/connections")
            await expect(
                page.get_by_test_id("google-calendar").get_by_role("button")
            ).to_have_count(0)

    await with_browser(body)


@pytest.mark.parametrize(
    ("role", "calendar"),
    [
        (
            "tenant_staff",
            {"available": False, "can_connect": False, "status": None, "people": None},
        ),
        ("read_only", {"available": True, "can_connect": False, "status": None, "people": None}),
        ("tenant_admin", {"available": True, "can_connect": False, "status": None, "people": []}),
    ],
)
async def test_the_line_is_absent_unless_the_feature_is_on_and_the_member_can_connect(
    site: Site, role: str, calendar: dict[str, Any]
) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        world.calendar = calendar
        async with bookings_page(browser, site, world, role=role, path="/portal/bookings") as page:
            await expect(page.get_by_test_id("bookings-page-heading")).to_be_visible(timeout=WAIT)
            await page.wait_for_load_state("networkidle")
            await expect(page.get_by_test_id("google-calendar")).to_have_count(0)
            await expect(page.get_by_test_id("google-calendar-line")).to_have_count(0)
            await expect(page.get_by_test_id("google-calendar-load-error")).to_have_count(0)

    await with_browser(body)


async def test_people_and_rooms_still_shows_the_team_connections(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        world.calendar = {
            "available": True,
            "can_connect": False,
            "status": None,
            "people": [
                {"person_id": AIMAN, "name": "Aiman Hakim", "status": "connected"},
                {"person_id": SITI, "name": "Siti Nur", "status": "needs_reconnect"},
            ],
        }
        path = "/portal/bookings/people-and-rooms"
        async with bookings_page(browser, site, world, role="tenant_admin", path=path) as page:
            people = page.get_by_test_id("google-calendar-people")
            await expect(people).to_be_visible(timeout=WAIT)
            await expect(people.get_by_test_id(f"google-calendar-person-{AIMAN}")).to_have_text(
                "Connected"
            )
            await expect(people.get_by_test_id(f"google-calendar-person-{SITI}")).to_have_text(
                "Needs reconnecting"
            )
            await expect(people).to_contain_text("Aiman Hakim")
            await expect(people.get_by_role("button")).to_have_count(0)  # a status, no controls
            # The one-line status is the Calendar page's, not this page's.
            await expect(page.get_by_test_id("google-calendar-line")).to_have_count(0)
        # Staff see no such list.
        world.calendar = world.calendar | {
            "people": None,
            "can_connect": True,
            "status": "connected",
        }
        async with bookings_page(browser, site, world, role="tenant_staff", path=path) as page:
            await expect(page.get_by_test_id("booking-settings")).to_be_visible(timeout=WAIT)
            await expect(page.get_by_test_id("google-calendar-people")).to_have_count(0)

    await with_browser(body)


async def test_a_failed_status_read_says_so_and_the_page_still_works(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        world.calendar_status = 500
        async with bookings_page(
            browser, site, world, role="tenant_staff", path="/portal/bookings"
        ) as page:
            await expect(page.get_by_test_id("google-calendar-load-error")).to_be_visible(
                timeout=WAIT
            )
            await expect(page.get_by_test_id("bookings-page-heading")).to_be_visible()
            await expect(page.get_by_test_id("google-calendar-line")).to_have_count(0)

    await with_browser(body)


async def test_the_old_page_no_longer_reads_the_return_parameters(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World(site.upstream)
        world.calendar = CONNECTABLE | {"status": "connected"}
        async with bookings_page(
            browser,
            site,
            world,
            role="tenant_staff",
            path="/portal/bookings?calendar_error=provider_denied",
        ) as page:
            await expect(page.get_by_test_id("google-calendar-line")).to_be_visible(timeout=WAIT)
            # The tab is the one reader: nothing is said here and the address is left alone.
            await expect(page.get_by_test_id("google-calendar-result")).to_have_count(0)
            assert "calendar_error=provider_denied" in page.url

    await with_browser(body)


async def test_an_online_booking_without_a_link_says_so_and_staff_can_ask_again(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = rich_world(site.upstream)
        tuesday = monday_of(COMPANY_ZONE) + timedelta(days=1)
        world.bookings = [
            booking(
                1,
                utc_at(tuesday, "09:00"),
                utc_at(tuesday, "10:00"),
                title="Online chat",
                location_kind="online",
                meet_missing=True,
            )
        ]
        world.meet_works = False
        async with bookings_page(browser, site, world, path="/portal/bookings") as page:
            await expect(page.get_by_test_id("calendar-week")).to_be_visible(timeout=WAIT)
            details = page.get_by_test_id("booking-details")
            await page.get_by_test_id("booking").click()
            await expect(details.get_by_test_id("details-meet-missing")).to_contain_text(
                "No Meet link yet."
            )
            await expect(details.get_by_test_id("details-link")).to_have_count(0)

            # Google still won't: the line stays, with a sentence, and the dialog is not closed.
            await details.get_by_test_id("details-meet-retry").click()
            await expect(details.get_by_test_id("details-meet-note")).to_have_text(
                "Google didn't make a link this time. Try again later."
            )
            assert writes(world, "POST", f"bookings/bookings/{world.bookings[0]['id']}/meet") == [
                {}
            ]

            # It works the second time: the dialog closes and the page says so.
            world.meet_works = True
            await details.get_by_test_id("details-meet-retry").click()
            await expect(page.get_by_test_id("calendar-notice")).to_have_text("Meet link added.")
            await expect(page.get_by_test_id("booking-details")).to_have_count(0)
            await page.get_by_test_id("booking").click()
            await expect(details.get_by_test_id("details-link")).to_have_attribute(
                "href", "https://meet.example.test/made"
            )
            await expect(details.get_by_test_id("details-meet-missing")).to_have_count(0)

    await with_browser(body)


async def test_someone_who_cannot_change_the_booking_sees_the_line_without_a_retry(
    site: Site,
) -> None:
    async def body(browser: Any) -> None:
        world = rich_world(site.upstream)
        tuesday = monday_of(COMPANY_ZONE) + timedelta(days=1)
        world.bookings = [
            booking(
                1,
                utc_at(tuesday, "09:00"),
                utc_at(tuesday, "10:00"),
                title="Online chat",
                location_kind="online",
                meet_missing=True,
            )
        ]
        async with bookings_page(
            browser, site, world, role="read_only", path="/portal/bookings"
        ) as page:
            await expect(page.get_by_test_id("calendar-week")).to_be_visible(timeout=WAIT)
            await page.get_by_test_id("booking").click()
            details = page.get_by_test_id("booking-details")
            await expect(details.get_by_test_id("details-meet-missing")).to_be_visible()
            await expect(details.get_by_test_id("details-meet-retry")).to_have_count(0)

    await with_browser(body)


async def test_a_booking_with_a_link_or_with_meet_links_off_shows_no_missing_line(
    site: Site,
) -> None:
    async def body(browser: Any) -> None:
        world = rich_world(site.upstream)
        tuesday = monday_of(COMPANY_ZONE) + timedelta(days=1)
        world.bookings = [
            booking(
                1,
                utc_at(tuesday, "09:00"),
                utc_at(tuesday, "10:00"),
                title="Online chat",
                location_kind="online",
                meeting_link="https://meet.example.test/chat",
            ),
            booking(
                2,
                utc_at(tuesday, "11:00"),
                utc_at(tuesday, "12:00"),
                title="Online, links off",
                location_kind="online",
            ),
        ]
        async with bookings_page(browser, site, world, path="/portal/bookings") as page:
            await expect(page.get_by_test_id("calendar-week")).to_be_visible(timeout=WAIT)
            details = page.get_by_test_id("booking-details")
            for title in ("Online chat", "Online, links off"):
                await page.get_by_test_id("booking").filter(has_text=title).click()
                await expect(details).to_be_visible()
                await expect(details.get_by_test_id("details-meet-missing")).to_have_count(0)
                await page.keyboard.press("Escape")
                await expect(details).to_have_count(0)

    await with_browser(body)
