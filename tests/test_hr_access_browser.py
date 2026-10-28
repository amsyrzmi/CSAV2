"""Real browser acceptance for the HR "Who can do what" page.

The real Next.js shell runs unmodified from a disposable staged copy, as in
`test_subscriptions_browser.py`: its layouts fetch `/auth/me` and `/services/active`
server-side from a loopback stub, and Playwright answers every browser-side
`/console/v1/**` call from a synthetic company. The stub applies the owner's saved
switches to what each member's HR context and directory then show, so the second
browser context proves the change is visible to the member. Any call the stub does
not expect fails the test.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

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
            body = request.post_data_json if request.method in {"POST", "PUT"} else None
            self.calls.append(
                {
                    "caller": caller,
                    "method": request.method,
                    "path": path,
                    "params": params,
                    "body": body,
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
            }
        if path == "hr/training-requests" and method == "GET":
            return 200, {"items": [], "next_cursor": None}
        if path == "hr/employees" and method == "GET" and (owner or self.has(caller, "directory")):
            items = [
                {
                    "employee": {"tenant_id": TENANT, "employee_id": EMPLOYEE[user]},
                    "values": {
                        "display_name": row["name"],
                        "job_title": row["title"],
                        "department": row["department"],
                    },
                }
                for user, row in self.people.items()
            ]
            return 200, {"items": items, "next_cursor": None}
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
        self.unexpected.append(f"{method} {path}?{params}")
        return 500, {"detail": "unexpected synthetic call"}


async def test_hr_access_owner_changes_two_people_and_staff_sees_it(tmp_path: Path) -> None:
    node = shutil.which("node")
    assert node, "Node is required for browser HR access acceptance"
    assert (WEB / "node_modules/next/dist/bin/next").is_file(), (
        "Console dependencies are missing; run `npm ci --ignore-scripts` in "
        "CSA_WebConsole/csa-console/web before browser HR access acceptance"
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
                    pytest.fail(f"Synthetic HR access frontend did not start; log: {log_path}")
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


async def open_access_page(page: Page, base: str) -> None:
    """People & HR, then More HR tools, then People & access."""
    await page.goto(f"{base}/portal/hr")
    await page.get_by_role("button", name="More HR tools", exact=True).click()
    await (
        page.get_by_role("navigation", name="HR tools")
        .get_by_role("button", name="People & access")
        .click()
    )
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
        await staff_page.goto(f"{base}/portal/hr?section=directory")
        await expect(staff_page.get_by_text("Authorized employee directory")).to_be_visible(
            timeout=WAIT
        )
        await expect(staff_page.get_by_text(TITLE)).to_have_count(0)
        assert not [
            c for c in world.calls if c["caller"] == STAFF and c["path"] == "hr/employees"
        ], world.calls

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
        await staff_page.goto(f"{base}/portal/hr?section=directory")
        await expect(staff_page.get_by_role("cell", name=TITLE)).to_be_visible(timeout=WAIT)

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
