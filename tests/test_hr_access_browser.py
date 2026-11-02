"""Real browser acceptance for the HR "Who can do what" page and a person's profile.

The real Next.js shell runs unmodified from a disposable staged copy, as in
`test_subscriptions_browser.py`: its layouts fetch `/auth/me` and `/services/active`
server-side from a loopback stub, and Playwright answers every browser-side
`/console/v1/**` call from a synthetic company. The stub applies the owner's saved
switches to what each member's HR context and directory then show, so the second
browser context proves the change is visible to the member. The second scenario has
the HR owner use a profile's Personal and Documents sections and mark the person as
leaving, then undo it. Any call the stub does not expect fails the test.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import httpx
import pytest
from playwright.async_api import Page, async_playwright, expect

from tests.test_services_browser import (
    ROOT,
    TENANT,
    WAIT,
    WEB,
    Upstream,
    free_port,
    make_me,
)

pytestmark = pytest.mark.asyncio

OWNER = "22222222-2222-4222-8222-222222222222"  # the user make_me() signs in as
MANAGER = "44444444-4444-4444-8444-111111111111"
STAFF = "44444444-4444-4444-8444-222222222222"
EMPLOYEE = {
    OWNER: "55555555-5555-4555-8555-000000000001",
    MANAGER: "55555555-5555-4555-8555-000000000002",
    STAFF: "55555555-5555-4555-8555-000000000003",
}
HR_PACKAGE = [{"key": "hr", "status": "active"}]
SWITCHES = [
    "directory",
    "own_requests",
    "approves_team",
    "hr_team",
    "manage_people",
    "sensitive_details",
    "recruitment",
    "attendance",
    "payroll_prepare",
    "payroll_review",
]
DIRECTORY_LABEL = "See everyone's basic details"
TITLE = "Warehouse supervisor"


class World:
    """Synthetic HR company behind the Playwright route."""

    def __init__(self) -> None:
        # name, department, title, manager, switches
        rows = [
            (OWNER, "Nadia Hassan", "Operations", "Owner", None, []),
            (MANAGER, "Farid Ismail", "Warehouse", TITLE, OWNER, []),
            (STAFF, "Lina Wong", "Warehouse", "Packer", MANAGER, ["own_requests"]),
        ]
        self.people = {
            user: {
                "name": name,
                "department": team,
                "title": title,
                "manager": EMPLOYEE.get(boss or ""),
                "on": on,
            }
            for user, name, team, title, boss, on in rows
        }
        self.version = 1
        self.events: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.unexpected: list[str] = []

    def stamp(self) -> str:
        return f"v{self.version}"

    def has(self, user: str, switch: str) -> bool:
        return switch in self.people[user]["on"]

    def person(self, user: str) -> dict[str, Any]:
        row = self.people[user]
        return {
            "user_id": user,
            "display_name": row["name"],
            "employee_id": EMPLOYEE[user],
            "department": row["department"],
            "manager_id": row["manager"],
            "team_size": sum(
                1 for other in self.people.values() if other["manager"] == EMPLOYEE[user]
            ),
            "is_owner": user == OWNER,
            "on": [s for s in SWITCHES if s in row["on"]],
            "included": {},
            "custom": [],
        }

    def apply(self, changes: list[dict[str, Any]], save: bool) -> list[dict[str, Any]]:
        results = []
        for change in changes:
            user, wanted = change["user_id"], set(change["on"])
            if user not in self.people:
                results.append({"user_id": user, "outcome": "refused", "reason": "not_a_member"})
                continue
            before = set(self.people[user]["on"])
            if set(change["base"]) != before:
                results.append(
                    {"user_id": user, "outcome": "refused", "reason": "changed_since_load"}
                )
                continue
            if wanted == before:
                results.append({"user_id": user, "outcome": "unchanged", "reason": None})
                continue
            if save:
                self.people[user]["on"] = [s for s in SWITCHES if s in wanted]
                self.events.append(
                    {
                        "occurred_at": "2026-10-28T03:00:00Z",
                        "actor_user_id": OWNER,
                        "actor_name": self.people[OWNER]["name"],
                        "target_user_id": user,
                        "target_name": self.people[user]["name"],
                        "turned_on": [s for s in SWITCHES if s in wanted - before],
                        "turned_off": [s for s in SWITCHES if s in before - wanted],
                        "area": None,
                        "area_enabled": None,
                    }
                )
            results.append({"user_id": user, "outcome": "saved", "reason": None})
        if save and any(r["outcome"] == "saved" for r in results):
            self.version += 1
        return results

    def route_for(self, caller: str) -> Callable[[Any], Awaitable[None]]:
        async def handle(request_route: Any) -> None:
            request = request_route.request
            path, _, query = request.url.split("/console/v1/")[1].partition("?")
            params = {key: values[0] for key, values in parse_qs(query).items()}
            body: Any = None
            if request.method in {"POST", "PUT"}:
                # An upload is multipart: its raw bytes are kept for the stub to check.
                json_body = "application/json" in request.headers.get("content-type", "")
                body = request.post_data_json if json_body else request.post_data_buffer
            key = request.headers.get("idempotency-key")
            self.calls.append(
                {
                    "caller": caller,
                    "method": request.method,
                    "path": path,
                    "params": params,
                    "body": body,
                    "key": key,
                }
            )
            status, data = self.answer(caller, request.method, path, params, body)
            await request_route.fulfill(status=status, json=data)

        return handle

    def answer(
        self, caller: str, method: str, path: str, params: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        if path == "auth/csrf":
            return 200, {"csrf_token": "synthetic-csrf"}
        if path.startswith("hr/") and params.get("client_id") != TENANT:
            self.unexpected.append(f"{method} {path} with params {params}")
            return 400, {"detail": "tenant mismatch"}
        owner = caller == OWNER
        if path == "hr/context" and method == "GET":
            actions = [
                a
                for s, a in (("directory", "directory.read"), ("own_requests", "training.read"))
                if self.has(caller, s)
            ]
            return 200, {
                "employee": {"tenant_id": TENANT, "employee_id": EMPLOYEE[caller]},
                "client_owner": owner,
                "private_areas": [],
                "actions": ["directory.read", "training.read"] if owner else actions,
                "switches": [s for s in SWITCHES if self.has(caller, s)],
                "workflows": [],
                "hr_duties": owner,
            }
        if path == "hr/training-requests" and method == "GET":
            return 200, {"items": [], "next_cursor": None}
        if path == "hr/people" and method == "GET" and (owner or self.has(caller, "directory")):
            items = [
                {
                    "employee_id": EMPLOYEE[user],
                    "display_name": row["name"],
                    "job_title": row["title"],
                    "department_id": None,
                    "department": row["department"],
                    "manager_id": row["manager"],
                    "manager_name": None,
                    "work_email": None,
                    "work_phone": None,
                    "start_date": None,
                    "employment_type": None,
                    "status": "active",
                    "employment_profile": None,
                    "version": None,
                    "account_linked": None,
                }
                for user, row in self.people.items()
            ]
            return 200, {"items": items, "next_cursor": None, "total": len(items)}
        if (
            path == "hr/departments"
            and method == "GET"
            and (owner or self.has(caller, "directory"))
        ):
            return 200, {"items": []}
        if path == "hr/operations" and method == "POST" and owner:
            return 200, {
                "operation": "automation.run",
                "resource_id": "none",
                "version": 1,
                "status": "ok",
                "duplicate": False,
            }
        if path == "hr/agents" and method == "GET" and owner:
            return 200, [
                {"name": "hr_directory", "enabled": True},
                {"name": "hr_assistant", "enabled": False},
            ]
        if path == "hr/records/employees" and method == "GET" and owner:
            return 200, {
                "items": [
                    {
                        "employee_id": EMPLOYEE[user],
                        "display_name": row["name"],
                        "manager_id": row["manager"],
                        "department": row["department"],
                        "active": True,
                    }
                    for user, row in self.people.items()
                ]
            }
        if path == "hr/records/calendars" and method == "GET" and owner:
            return 200, {"items": []}
        if path == "hr/records/members" and method == "GET" and owner:
            return 200, {
                "items": [
                    {"user_id": user, "display_name": row["name"]}
                    for user, row in self.people.items()
                ]
            }
        if path == "hr/access/people" and method == "GET" and owner:
            needle = params.get("q", "").lower()
            users = sorted(self.people, key=lambda user: self.people[user]["name"])
            return 200, {
                "items": [
                    self.person(u) for u in users if needle in self.people[u]["name"].lower()
                ],
                "next_cursor": None,
                "version": self.stamp(),
                "counts": {s: sum(self.has(u, s) for u in self.people) for s in SWITCHES},
                "warnings": [],
            }
        if path == "hr/access/changes" and method == "POST" and owner:
            if body["version"] != self.stamp():
                return 409, {"detail": "access_changed"}
            results = self.apply(body["changes"], save=not body["dry_run"])
            return 200, {"results": results, "version": self.stamp(), "warnings": []}
        if path == "hr/access/recent" and method == "GET" and owner:
            return 200, {"items": list(reversed(self.events)), "next_cursor": None}
        if path == "hr/access/invitations" and method == "GET" and owner:
            # B4's pending panel loads on this page; this world has no invitations.
            return 200, {"items": []}
        self.unexpected.append(f"{method} {path}?{params}")
        return 500, {"detail": "unexpected synthetic call"}


LEAVER = STAFF  # Lina Wong, whose profile the HR owner works on
IC_MASK = "\u2022\u2022\u2022\u2022 5678"
IC_NUMBER = "900101-14-5678"  # synthetic
FILE_NAME = "offer-letter.pdf"
PDF = b"%PDF-1.4\n% synthetic contract for the browser scenario\n%%EOF\n"
DOCUMENT_ID = "66666666-6666-4666-8666-000000000001"
DOWNLOAD_PATH = f"/console/v1/hr/documents/{DOCUMENT_ID}/download"
SECTIONS = ["overview", "personal", "documents", "access", "history"]
LEAVING_OPERATIONS = {"person.leave", "person.leave_undo"}


class RecordsWorld(World):
    """The same company, plus one person's personal details, documents and leaving date."""

    def __init__(self) -> None:
        super().__init__()
        self.person_version = 3
        self.last_day: str | None = None
        self.documents: list[dict[str, Any]] = []
        self.uploads: list[dict[str, Any]] = []
        self.reveals: list[str] = []

    def full_person(self, user: str) -> dict[str, Any]:
        row = self.people[user]
        boss = next((other for other in self.people if EMPLOYEE[other] == row["manager"]), None)
        leaving = user == LEAVER and self.last_day is not None
        return {
            "employee_id": EMPLOYEE[user],
            "display_name": row["name"],
            "job_title": row["title"],
            "department_id": None,
            "department": row["department"],
            "manager_id": row["manager"],
            "manager_name": self.people[boss]["name"] if boss else None,
            "work_email": None,
            "work_phone": None,
            "start_date": "2025-01-06",
            "employment_type": "full_time",
            "status": "leaving" if leaving else "active",
            "last_day": self.last_day if user == LEAVER else None,
            "leaving": {"reason": "resigned", "note": None} if leaving else None,
            "employment_profile": None,
            "version": self.person_version if user == LEAVER else 1,
            "account_linked": True,
        }

    def answer(
        self, caller: str, method: str, path: str, params: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        if caller != OWNER or params.get("client_id") != TENANT:
            return super().answer(caller, method, path, params, body)
        person = f"hr/people/{EMPLOYEE[LEAVER]}"
        if path == "hr/people" and method == "GET":
            items = [self.full_person(user) for user in self.people]
            return 200, {"items": items, "next_cursor": None, "total": len(items)}
        if path == person and method == "GET":
            return 200, {"person": self.full_person(LEAVER), "sections": SECTIONS, "can_edit": True}
        if path == f"{person}/personal" and method == "GET":
            return 200, {
                "ic_or_passport": IC_MASK,
                "date_of_birth": "1990-01-01",
                "home_address": None,
                "personal_phone": None,
                "emergency_name": None,
                "emergency_relationship": None,
                "emergency_phone": None,
                "bank_name": None,
                "bank_account": None,
                "tax_number": None,
                "epf_number": None,
                "socso_number": None,
                "version": 2,
                "can_edit": "all",
            }
        if path == f"{person}/personal/reveal" and method == "POST":
            self.reveals.append(body["field"])
            return 200, {"value": IC_NUMBER if body == {"field": "ic_or_passport"} else None}
        if path == f"{person}/documents" and method == "GET":
            return 200, {"items": self.documents}
        if path == f"{person}/documents" and method == "POST":
            self.uploads.append({"kind": params.get("kind"), "body": body})
            document = {
                "document_id": DOCUMENT_ID,
                "kind": params.get("kind"),
                "file_name": FILE_NAME,
                "media_type": "application/pdf",
                "byte_size": len(PDF),
                "uploaded_at": "2026-11-02T02:00:00Z",
                "uploaded_by_name": self.people[OWNER]["name"],
            }
            self.documents = [document]
            return 201, document
        if path == "hr/org-chart" and method == "GET":
            nodes = [
                {
                    key: self.full_person(user)[key]
                    for key in (
                        "employee_id",
                        "display_name",
                        "job_title",
                        "department",
                        "manager_id",
                    )
                }
                for user in self.people
            ]
            return 200, {"nodes": nodes, "looped": []}
        if path == "hr/operations" and method == "POST" and body["operation"] in LEAVING_OPERATIONS:
            if body["employee_id"] != EMPLOYEE[LEAVER]:
                return 404, {"detail": "resource_unavailable"}
            if body["expected_version"] != self.person_version:
                return 409, {"detail": "person_changed"}
            self.last_day = body["last_day"] if body["operation"] == "person.leave" else None
            self.person_version += 1
            return 200, {
                "operation": body["operation"],
                "resource_id": EMPLOYEE[LEAVER],
                "version": self.person_version,
                "status": "ok",
                "duplicate": False,
            }
        return super().answer(caller, method, path, params, body)


class RecordsUpstream(Upstream):
    """The server-side stub, which also serves the document download. Chromium sends an
    `<a download>` request past Playwright's routes, so it goes through the console's
    `/console/v1` proxy to this origin, as it would to the gateway."""

    def __init__(self) -> None:
        super().__init__()
        self.downloads: list[str] = []  # the cookie header of each download request
        upstream = self
        server: Any = self.server  # its handler class is built inside Upstream
        base: Any = server.RequestHandlerClass

        class Handler(base):
            def do_GET(self) -> None:
                path, _, query = self.path.partition("?")
                if path != DOWNLOAD_PATH:
                    return super().do_GET()
                if parse_qs(query).get("client_id") != [TENANT]:
                    upstream.unexpected.append(f"GET {self.path}")
                upstream.downloads.append(self.headers.get("cookie", ""))
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition", f'attachment; filename="{FILE_NAME}"')
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(PDF)))
                self.end_headers()
                self.wfile.write(PDF)

        server.RequestHandlerClass = Handler


@contextlib.asynccontextmanager
async def console_frontend(
    tmp_path: Path, stub: Upstream | None = None
) -> AsyncIterator[tuple[str, Upstream]]:
    """The staged console under `next dev`, with its server-side upstream stub."""
    node = shutil.which("node")
    assert node, "Node is required for browser HR access acceptance"
    assert (WEB / "node_modules/next/dist/bin/next").is_file(), (
        "Console dependencies are missing; run `npm ci --ignore-scripts` in "
        "CSA_WebConsole/csa-console/web before browser HR access acceptance"
    )
    stage = tmp_path / "web"
    with stub or Upstream() as upstream:
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
                    pytest.fail(f"Synthetic HR access frontend did not start; log: {log_path}")
            yield f"http://127.0.0.1:{port}", upstream
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.close()


async def test_hr_access_owner_changes_two_people_and_staff_sees_it(tmp_path: Path) -> None:
    async with console_frontend(tmp_path) as (base, upstream):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                await exercise(browser, base, upstream, tmp_path)
            finally:
                await browser.close()


async def open_access_page(page: Page, base: str) -> None:
    """People & HR › Settings, which holds People & access."""
    await page.goto(f"{base}/portal/hr/settings")
    await expect(page.get_by_test_id(f"hr-access-row-{STAFF}")).to_be_visible(timeout=WAIT)


async def exercise(browser: Any, base: str, upstream: Upstream, tmp_path: Path) -> None:
    owner_context = await browser.new_context(timezone_id="Asia/Kuala_Lumpur")
    staff_context = await browser.new_context(timezone_id="Asia/Kuala_Lumpur")
    for context in (owner_context, staff_context):
        context.set_default_navigation_timeout(WAIT)
        await context.add_cookies(
            [{"name": "csa_session", "value": "synthetic-session", "url": base}]
        )
    await owner_context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = await owner_context.new_page()
    stale = await owner_context.new_page()
    staff_page = await staff_context.new_page()
    diagnostics: list[str] = []
    for watched in (page, stale, staff_page):
        watched.on("crash", lambda: diagnostics.append("Chromium page crashed"))
        watched.on("pageerror", lambda error: diagnostics.append(str(error)))
        watched.on("console", lambda message: diagnostics.append(f"{message.type}: {message.text}"))
    world = World()
    try:
        await owner_context.route("**/console/v1/**", world.route_for(OWNER))
        await staff_context.route("**/console/v1/**", world.route_for(STAFF))

        # (a) The owner opens People & access; a second tab is opened now and left to go stale.
        upstream.me, upstream.packages = make_me("tenant_admin"), HR_PACKAGE
        await open_access_page(page, base)
        await expect(page.get_by_role("heading", name="Who can do what")).to_be_visible()
        await open_access_page(stale, base)
        await expect(stale.get_by_test_id(f"hr-access-row-{STAFF}")).to_be_visible()

        # (b) Staff cannot see colleagues' job titles yet.
        staff_me = make_me("tenant_staff")
        staff_me["user"]["id"] = STAFF
        staff_me["user"]["display_name"] = "Lina Wong"
        upstream.me = staff_me
        await staff_page.goto(f"{base}/portal/hr/people")
        await expect(
            staff_page.get_by_text("You don't have access to this part of People & HR.")
        ).to_be_visible(timeout=WAIT)
        await expect(staff_page.get_by_role("link", name="People", exact=True)).to_have_count(0)
        await expect(staff_page.get_by_text(TITLE)).to_have_count(0)
        assert not [c for c in world.calls if c["caller"] == STAFF and c["path"] == "hr/people"], (
            world.calls
        )

        # (c) The owner searches for the staff member and turns on the directory switch.
        row = lambda user: page.get_by_test_id(f"hr-access-row-{user}")  # noqa: E731
        await page.get_by_test_id("hr-access-search").fill("lina")
        await expect(page.locator("[data-testid^='hr-access-row-']")).to_have_count(1, timeout=WAIT)
        await row(STAFF).get_by_role("button").click()
        panel = page.get_by_test_id("hr-access-panel")
        await expect(panel).to_contain_text("Lina Wong")
        await expect(panel.get_by_text(DIRECTORY_LABEL, exact=True)).to_be_visible()
        await panel.get_by_test_id("hr-access-switch-directory").check()
        await expect(row(STAFF)).to_contain_text("Not saved yet")

        # (d) The owner ticks both, then gives only the manager the Manager preset.
        await page.get_by_test_id("hr-access-search").fill("")
        await expect(page.locator("[data-testid^='hr-access-row-']")).to_have_count(3, timeout=WAIT)
        await page.get_by_test_id(f"hr-access-tick-{MANAGER}").check()
        await page.get_by_test_id(f"hr-access-tick-{STAFF}").check()
        await expect(page.get_by_test_id("hr-access-bulk")).to_contain_text("2 members ticked")
        await row(MANAGER).get_by_role("button").click()
        await expect(panel).to_contain_text("Farid Ismail")
        await panel.get_by_test_id("hr-access-preset").select_option("manager")
        await expect(panel.get_by_test_id("hr-access-switch-approves_team")).to_be_checked()
        await expect(row(MANAGER)).to_contain_text("Approves")
        await expect(row(STAFF)).not_to_contain_text("Approves")
        await expect(page.get_by_text("4 changes for 2 people", exact=True)).to_be_visible()

        # (e) Saving shows the dry-run dialog first; confirming saves both rows.
        await page.get_by_test_id("hr-access-save").click()
        dialog = page.get_by_role("dialog")
        await expect(dialog).to_contain_text("Save 4 changes for 2 people?", timeout=WAIT)
        await expect(dialog).to_contain_text("This change adds no new warnings.")
        assert not [
            c for c in world.calls if c["path"] == "hr/access/changes" and not c["body"]["dry_run"]
        ]
        assert world.people[STAFF]["on"] == ["own_requests"], "the dry run must not save"
        await dialog.get_by_test_id("hr-access-confirm").click()
        await expect(page.get_by_test_id(f"hr-access-result-{MANAGER}")).to_have_text(
            "Saved", timeout=WAIT
        )
        await expect(page.get_by_test_id(f"hr-access-result-{STAFF}")).to_have_text("Saved")
        await expect(dialog).to_have_count(0)
        want = {
            MANAGER: ["directory", "own_requests", "approves_team"],
            STAFF: ["directory", "own_requests"],
        }
        assert {u: world.people[u]["on"] for u in want} == want
        saves = [c["body"] for c in world.calls if c["path"] == "hr/access/changes"]
        assert [(b["dry_run"], b["version"]) for b in saves] == [(True, "v1"), (False, "v1")], saves
        assert saves[0]["batch_id"] == saves[1]["batch_id"], (
            "the confirm reuses the dry run's batch"
        )
        for body in saves:
            assert {c["user_id"]: c["on"] for c in body["changes"]} == want, body

        # (f) The save is listed under Recent changes.
        await (
            page.get_by_test_id("hr-access-recent")
            .get_by_text("Recent changes", exact=True)
            .click()
        )
        await expect(page.get_by_test_id("hr-access-recent")).to_contain_text(
            f'Nadia Hassan turned on "{DIRECTORY_LABEL}" for Lina Wong', timeout=WAIT
        )

        # (g) The staff member now sees a colleague's job title in the HR directory.
        await staff_page.goto(f"{base}/portal/hr/people")
        await expect(staff_page.get_by_role("cell", name=TITLE)).to_be_visible(timeout=WAIT)
        assert [c for c in world.calls if c["caller"] == STAFF and c["path"] == "hr/people"], (
            world.calls
        )

        # (h) The stale tab, opened before the save, is told access changed and keeps its change.
        await stale.get_by_test_id(f"hr-access-row-{STAFF}").get_by_role("button").click()
        await stale.get_by_test_id("hr-access-switch-payroll_prepare").check()
        await stale.get_by_test_id("hr-access-save").click()
        await expect(stale.get_by_test_id("hr-access-conflict")).to_be_visible(timeout=WAIT)
        await expect(stale.get_by_test_id("hr-access-confirm")).to_have_count(0)
        await expect(stale.get_by_text("1 change for 1 person", exact=True)).to_be_visible()
        assert "payroll_prepare" not in world.people[STAFF]["on"]
        assert world.version == 2

        # (i) Reloading keeps the other tab's switch on; only this tab's own change stays pending.
        await stale.get_by_role("button", name="Reload access").click()
        await expect(stale.get_by_test_id("hr-access-conflict")).to_have_count(0, timeout=WAIT)
        stale_panel = stale.get_by_test_id("hr-access-panel")
        await expect(stale_panel.get_by_test_id("hr-access-switch-directory")).to_be_checked()
        await expect(stale_panel.get_by_test_id("hr-access-switch-payroll_prepare")).to_be_checked()
        await expect(stale.get_by_text("1 change for 1 person", exact=True)).to_be_visible()
        await stale.get_by_test_id("hr-access-save").click()
        await stale.get_by_role("dialog").get_by_test_id("hr-access-confirm").click()
        await expect(stale.get_by_test_id(f"hr-access-result-{STAFF}")).to_have_text(
            "Saved", timeout=WAIT
        )
        assert world.people[STAFF]["on"] == ["directory", "own_requests", "payroll_prepare"]
        last = [c["body"] for c in world.calls if c["path"] == "hr/access/changes"][-1]
        assert last["changes"] == [
            {
                "user_id": STAFF,
                "on": ["directory", "own_requests", "payroll_prepare"],
                "base": ["directory", "own_requests"],
            }
        ], last

        assert not world.unexpected, world.unexpected
        assert not upstream.unexpected, upstream.unexpected
    except Exception as exc:
        exc.add_note(
            f"Browser diagnostics: {diagnostics}; trace: {tmp_path / 'hr-access-trace.zip'}; "
            f"frontend log: {tmp_path / 'frontend.log'}"
        )
        raise
    finally:
        (tmp_path / "hr-access-browser.log").write_text("\n".join(diagnostics))
        with contextlib.suppress(Exception):  # Preserve the original failure if Chromium crashed.
            await owner_context.tracing.stop(path=tmp_path / "hr-access-trace.zip")
        await owner_context.close()
        await staff_context.close()


async def test_hr_owner_personal_documents_and_leaving(tmp_path: Path) -> None:
    stub = RecordsUpstream()
    async with console_frontend(tmp_path, stub) as (base, _):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                await exercise_records(browser, base, stub, tmp_path)
            finally:
                await browser.close()


async def exercise_records(
    browser: Any, base: str, upstream: RecordsUpstream, tmp_path: Path
) -> None:
    context = await browser.new_context(timezone_id="Asia/Kuala_Lumpur", accept_downloads=True)
    context.set_default_navigation_timeout(WAIT)
    await context.add_cookies([{"name": "csa_session", "value": "synthetic-session", "url": base}])
    await context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = await context.new_page()
    diagnostics: list[str] = []
    page.on("crash", lambda: diagnostics.append("Chromium page crashed"))
    page.on("pageerror", lambda error: diagnostics.append(str(error)))
    page.on("console", lambda message: diagnostics.append(f"{message.type}: {message.text}"))
    world = RecordsWorld()
    profile = f"{base}/portal/hr/people/{EMPLOYEE[LEAVER]}"
    tab = lambda name: page.get_by_role("tab", name=name, exact=True)  # noqa: E731
    operations = lambda: [c for c in world.calls if c["path"] == "hr/operations"]  # noqa: E731
    try:
        await context.route("**/console/v1/**", world.route_for(OWNER))
        upstream.me, upstream.packages = make_me("tenant_admin"), HR_PACKAGE

        # (a) Personal shows the IC number masked; Show asks the server for it, and Hide drops it.
        await page.goto(profile)
        await tab("Personal").click(timeout=WAIT)
        await expect(page.get_by_text(IC_MASK, exact=True)).to_be_visible(timeout=WAIT)
        await expect(page.get_by_text(IC_NUMBER)).to_have_count(0)
        assert world.reveals == []
        await page.get_by_role("button", name="Show IC or passport number").click()
        await expect(page.get_by_text(IC_NUMBER, exact=True)).to_be_visible(timeout=WAIT)
        await expect(page.get_by_text(IC_MASK, exact=True)).to_have_count(0)
        assert world.reveals == ["ic_or_passport"]
        await page.get_by_role("button", name="Hide IC or passport number").click()
        await expect(page.get_by_text(IC_MASK, exact=True)).to_be_visible()
        await expect(page.get_by_text(IC_NUMBER)).to_have_count(0)

        # (b) Documents: upload a small PDF, see it listed, download the same bytes.
        await tab("Documents").click()
        await expect(page.get_by_text("No documents yet.")).to_be_visible(timeout=WAIT)
        await page.locator("input[type='file']").set_input_files(
            {"name": FILE_NAME, "mimeType": "application/pdf", "buffer": PDF}
        )
        await page.get_by_role("button", name="Upload", exact=True).click()
        await expect(page.get_by_text("Document uploaded.")).to_be_visible(timeout=WAIT)
        await expect(page.get_by_role("cell", name=FILE_NAME, exact=True)).to_be_visible()
        [upload] = world.uploads
        assert upload["kind"] == "contract", upload["kind"]
        assert f'filename="{FILE_NAME}"'.encode() in upload["body"] and PDF in upload["body"]
        [sent] = [
            c for c in world.calls if c["method"] == "POST" and c["path"].endswith("documents")
        ]
        assert sent["key"], "the upload carries an Idempotency-Key"
        async with page.expect_download() as downloading:
            await page.get_by_role("link", name=f"Download {FILE_NAME}").click()
        download = await downloading.value
        assert download.suggested_filename == FILE_NAME
        saved = tmp_path / "downloaded.pdf"
        await download.save_as(saved)
        assert saved.read_bytes() == PDF
        assert upstream.downloads == ["csa_session=synthetic-session"], upstream.downloads

        # (c) Mark as leaving with a last day a week ahead (Kuala Lumpur).
        last_day = datetime.now(ZoneInfo("Asia/Kuala_Lumpur")).date() + timedelta(days=7)
        months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
        last_day_text = f"{last_day.day} {months[last_day.month - 1]} {last_day.year}"
        await tab("Overview").click()
        await page.get_by_role("button", name="Mark as leaving", exact=True).click()
        await expect(page.get_by_text("Mark Lina Wong as leaving")).to_be_visible(timeout=WAIT)
        await page.locator("input[name='last_day']").fill(last_day.isoformat())
        await page.get_by_role("button", name="Mark as leaving", exact=True).click()
        await expect(page.get_by_text("Marked as leaving.")).to_be_visible(timeout=WAIT)
        await expect(page.get_by_text(f"Leaving on {last_day_text} (Resigned)")).to_be_visible()
        [leave] = operations()
        assert leave["body"] == {
            "operation": "person.leave",
            "employee_id": EMPLOYEE[LEAVER],
            "expected_version": 3,
            "last_day": last_day.isoformat(),
            "reason": "resigned",
            "reports_new_manager": None,
        }, leave["body"]
        assert leave["key"], "the leave carries an Idempotency-Key"

        # (d) The People list shows them as Leaving.
        await page.goto(f"{base}/portal/hr/people")
        lina = page.get_by_role("row").filter(has=page.get_by_role("link", name="Lina Wong"))
        await expect(lina).to_contain_text("Leaving", timeout=WAIT)
        farid = page.get_by_role("row").filter(has=page.get_by_role("link", name="Farid Ismail"))
        await expect(farid).to_contain_text("Active")

        # (e) Undo from the profile: they stay on and can be marked again.
        await page.get_by_role("link", name="Lina Wong").click()
        await page.get_by_role("button", name="Undo leaving").click(timeout=WAIT)
        await expect(page.get_by_text("Leaving undone. They stay on.")).to_be_visible(timeout=WAIT)
        await expect(page.get_by_role("button", name="Mark as leaving", exact=True)).to_be_visible()
        await expect(page.get_by_text("Leaving on")).to_have_count(0)
        assert [c["body"] for c in operations()][1:] == [
            {
                "operation": "person.leave_undo",
                "employee_id": EMPLOYEE[LEAVER],
                "expected_version": 4,
            }
        ]
        assert world.last_day is None and world.person_version == 5

        assert not world.unexpected, world.unexpected
        assert not upstream.unexpected, upstream.unexpected
    except Exception as exc:
        exc.add_note(
            f"Browser diagnostics: {diagnostics}; trace: {tmp_path / 'hr-records-trace.zip'}; "
            f"frontend log: {tmp_path / 'frontend.log'}"
        )
        raise
    finally:
        (tmp_path / "hr-records-browser.log").write_text("\n".join(diagnostics))
        with contextlib.suppress(Exception):  # Preserve the original failure if Chromium crashed.
            await context.tracing.stop(path=tmp_path / "hr-records-trace.zip")
        await context.close()
