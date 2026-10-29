"""Real browser acceptance for choosing HR access on an invitation and for its delivery.

The real Next.js shell runs unmodified from a disposable staged copy, as in
`test_hr_access_browser.py`, whose synthetic company this file extends: Playwright answers every
browser-side `/console/v1/**` call. The stub decides whether the "server" emailed an invitation or
fell back to a link, and holds one accepted invitation whose HR access is waiting for the owner.
Any call the stub does not expect fails the test.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.async_api import Page, async_playwright, expect

from tests.test_hr_access_browser import (
    HR_PACKAGE,
    OWNER,
    STAFF,
    World,
    open_access_page,
)
from tests.test_services_browser import (
    ROOT,
    TENANT,
    TENANT_PROFILE,
    WAIT,
    WEB,
    Upstream,
    free_port,
    make_me,
)

pytestmark = pytest.mark.asyncio

TOKEN_ID = "66666666-6666-4666-8666-000000000001"
RAW_TOKEN = "synthetic-raw-token-not-a-credential"
PRESET_VALUES = ["none", "staff", "manager", "hr_team", "payroll_preparer", "payroll_reviewer"]
FAILED_NOTE = "We couldn't send the email. Copy this link and send it yourself."


class InviteWorld(World):
    """The HR company plus the team page, issuing invitations and one waiting acceptance."""

    def __init__(self) -> None:
        super().__init__()
        self.client_owner = True
        self.delivery = "email"  # or "send_failed": the relay refused and the link is shown
        self.invites: list[dict[str, Any]] = []
        self.pending = [
            {
                "token_id": TOKEN_ID,
                "user_id": STAFF,
                "display_name": "Lina Wong",
                "preset": "manager",
                "chosen_by": OWNER,
                "chooser_name": "Omar Lee",
                "accepted_at": "2026-10-29T02:00:00Z",
            }
        ]

    def answer(
        self, caller: str, method: str, path: str, params: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        if path == "hr/context" and method == "GET":
            status, data = super().answer(caller, method, path, params, body)
            return status, {**data, "client_owner": self.client_owner}
        if path == "services/active" and method == "GET":
            return 200, {"packages": HR_PACKAGE}
        if path == "profile" and method == "GET":
            return 200, TENANT_PROFILE
        if path == "team" and method == "GET" and params.get("client_id") == TENANT:
            return 200, []
        if path == "admin/invites" and method == "GET" and params.get("client_id") == TENANT:
            return 200, list(self.invites)
        if path == "team/invites" and method == "POST" and body["client_id"] == TENANT:
            emailed = self.delivery == "email"
            self.invites.append(
                {
                    "id": f"77777777-7777-4777-8777-00000000000{len(self.invites) + 1}",
                    "email": body["email"],
                    "display_name": body["display_name"],
                    "role": body["role"],
                    "kind": "invite",
                    "expires_at": "2026-11-05T02:00:00Z",
                    "consumed_at": None,
                    "revoked_at": None,
                    "hr_preset": body.get("hr_preset"),
                }
            )
            return 200, {
                "id": self.invites[-1]["id"],
                "token": None if emailed else RAW_TOKEN,
                "expires_at": "2026-11-05T02:00:00Z",
                "delivery": "email" if emailed else "manual",
                "delivery_note": None if emailed else "send_failed",
            }
        if path == "hr/access/invitations" and method == "GET" and self.client_owner:
            return 200, {"items": list(self.pending)}
        if path == f"hr/access/invitations/{TOKEN_ID}/apply" and method == "POST":
            if not self.pending:
                return 409, {"detail": "invitation_already_handled"}
            self.pending.clear()
            self.people[STAFF]["on"] = ["directory", "own_requests", "approves_team"]
            self.version += 1
            return 200, {"user_id": STAFF, "outcome": "saved", "reason": None}
        return super().answer(caller, method, path, params, body)


async def test_invite_chooses_hr_access_shows_delivery_and_owner_applies_pending(
    tmp_path: Path,
) -> None:
    node = shutil.which("node")
    assert node, "Node is required for browser invitation acceptance"
    assert (WEB / "node_modules/next/dist/bin/next").is_file(), (
        "Console dependencies are missing; run `npm ci --ignore-scripts` in "
        "CSA_WebConsole/csa-console/web before browser invitation acceptance"
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
                    pytest.fail(f"Synthetic invitation frontend did not start; log: {log_path}")
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


async def option_values(page: Page) -> list[str]:
    return (
        await page.get_by_test_id("invite-hr-access")
        .locator("option")
        .evaluate_all("options => options.map((option) => option.value)")
    )


async def invite(page: Page, name: str, email: str) -> None:
    await page.get_by_label("Display name").fill(name)
    await page.get_by_label("Email", exact=True).fill(email)
    await page.get_by_role("button", name="Create invitation").click()


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
    world = InviteWorld()
    try:
        await context.route("**/console/v1/**", world.route_for(OWNER))
        upstream.me, upstream.packages = make_me("tenant_admin"), HR_PACKAGE
        select = page.get_by_test_id("invite-hr-access")
        delivery = page.get_by_test_id("invite-delivery")
        link = page.locator("#issued-link")  # the copy box has an id, not a test id

        # (a) The HR owner is offered all six choices; Staff is the default.
        await page.goto(f"{base}/portal/team")
        await expect(select.locator("option")).to_have_count(6, timeout=WAIT)
        assert await option_values(page) == PRESET_VALUES
        await expect(select).to_have_value("staff")

        # (b) Choosing Manager emails the invitation: no link or copy box is shown.
        await select.select_option("manager")
        await invite(page, "Aiman Yusof", "aiman@example.test")
        await expect(delivery).to_have_text(
            "Invitation emailed to aiman@example.test", timeout=WAIT
        )
        await expect(link).to_have_count(0)
        await expect(page.get_by_text(RAW_TOKEN)).to_have_count(0)
        sent = [c["body"] for c in world.calls if c["path"] == "team/invites"]
        assert [(b["email"], b["hr_preset"]) for b in sent] == [("aiman@example.test", "manager")]

        # (c) A tenant admin who is not the HR owner can choose only none or Staff.
        world.client_owner, world.delivery = False, "send_failed"
        await page.goto(f"{base}/portal/team")
        await expect(select.locator("option")).to_have_count(2, timeout=WAIT)
        assert await option_values(page) == ["none", "staff"]
        await expect(select).to_have_value("staff")
        await expect(
            page.get_by_text("Only the HR owner can give more than Staff.")
        ).to_be_visible()

        # (d) A send the relay refused falls back to the copy box, with an honest sentence.
        await invite(page, "Siti Aminah", "siti@example.test")
        await expect(delivery).to_have_text(FAILED_NOTE, timeout=WAIT)
        await expect(link).to_have_value(f"{base}/invite#token={RAW_TOKEN}")
        sent = [c["body"] for c in world.calls if c["path"] == "team/invites"]
        assert sent[-1]["hr_preset"] == "staff", sent

        # (e) The HR owner sees the one invitation waiting for access and applies it.
        world.client_owner = True
        await open_access_page(page, base)
        pending = page.get_by_test_id("hr-access-pending")
        await expect(pending).to_contain_text("Lina Wong", timeout=WAIT)
        await expect(pending).to_contain_text("chosen by Omar Lee")
        await expect(page.locator("[data-testid^='hr-access-pending-apply-']")).to_have_count(1)
        await page.get_by_test_id(f"hr-access-pending-apply-{TOKEN_ID}").click()
        await expect(page.get_by_test_id(f"hr-access-pending-apply-{TOKEN_ID}")).to_have_count(
            0, timeout=WAIT
        )
        await expect(pending).to_contain_text(
            "Lina Wong now has the access chosen on their invitation."
        )
        assert world.pending == []
        applied = [c for c in world.calls if c["path"].endswith("/apply")]
        assert [(c["method"], c["path"]) for c in applied] == [
            ("POST", f"hr/access/invitations/{TOKEN_ID}/apply")
        ]
        assert world.people[STAFF]["on"] == ["directory", "own_requests", "approves_team"]

        assert not world.unexpected, world.unexpected
        assert not upstream.unexpected, upstream.unexpected
    except Exception as exc:
        exc.add_note(
            f"Browser diagnostics: {diagnostics}; trace: {tmp_path / 'invite-access-trace.zip'}; "
            f"frontend log: {tmp_path / 'frontend.log'}"
        )
        raise
    finally:
        (tmp_path / "invite-access-browser.log").write_text("\n".join(diagnostics))
        with contextlib.suppress(Exception):  # Preserve the original failure if Chromium crashed.
            await context.tracing.stop(path=tmp_path / "invite-access-trace.zip")
        await context.close()
