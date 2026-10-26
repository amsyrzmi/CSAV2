"""Real browser acceptance for the floating workspace assistant dock.

The real Next.js shell runs unmodified from a disposable staged copy. Its layouts
fetch `/auth/me` and `/services/active` server-side, so a loopback stub origin
answers those; every browser-side `/console/v1/*` call is answered by Playwright.
Stubbed workspace bodies are built from the backend contracts so they cannot drift.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import socket
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from csa.workspace.contracts import (
    CardField,
    DirectoryResult,
    NavigateResult,
    PersonOut,
    ProposalCard,
    ProposalKind,
    ProposalOut,
    ProposalResult,
    WorkspaceActionResult,
    WorkspaceThreadOut,
    WorkspaceTurnOut,
)
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
CSRF = "synthetic-csrf"
THREAD = UUID("33333333-3333-4333-8333-333333333333")
ALI_A = UUID("44444444-4444-4444-8444-444444444441")
ALI_B = UUID("44444444-4444-4444-8444-444444444442")
EMAIL_PROPOSAL = UUID("55555555-5555-4555-8555-555555555551")
TRAINING_PROPOSAL = UUID("55555555-5555-4555-8555-555555555552")
ABANDONED_PROPOSAL = UUID("55555555-5555-4555-8555-555555555553")
SIDEBAR_PROPOSAL = UUID("55555555-5555-4555-8555-555555555554")
RESET_PROPOSAL = UUID("55555555-5555-4555-8555-555555555555")
PACKAGES = [
    {"key": "customer_support", "status": "active"},
    {"key": "hr", "status": "active"},
    {"key": "staff_email", "status": "active"},
]
EMPTY_PAGE: dict[str, Any] = {"items": [], "next_cursor": None}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_me(role: str, *, assistant: bool = True) -> dict[str, Any]:
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
        "csrf_token": CSRF,
        "workspace_assistant": assistant,
    }


class Upstream:
    """Loopback stand-in for the gateway origin used by server-side layout fetches."""

    def __init__(self) -> None:
        self.me = make_me("tenant_admin")
        self.packages: list[dict[str, str]] = []
        self.unexpected: list[str] = []
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = self.path.split("?")[0]
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


def proposal(
    proposal_id: UUID,
    kind: ProposalKind,
    title: str,
    fields: list[tuple[str, str]],
    status: str = "requested",
    result: dict[str, object] | None = None,
) -> ProposalOut:
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=10)
    return ProposalOut.model_validate(
        {
            "id": proposal_id,
            "kind": kind,
            "status": status,
            "card": ProposalCard(
                proposal_id=proposal_id,
                kind=kind,
                title=title,
                summary=f"Synthetic {title.lower()}.",
                fields=[CardField(label=label, value=value) for label, value in fields],
                expires_at=expires,
                expected_version=None,
            ),
            "result": result,
            "created_at": now,
            "expires_at": expires,
        }
    )


def email_proposal(proposal_id: UUID, **extra: Any) -> ProposalOut:
    return proposal(
        proposal_id,
        "email_export",
        "Email draft",
        [("To", "Ali Bakar <ali.bakar@example.test>"), ("Subject", "Roster update")],
        **extra,
    )


def training_proposal(**extra: Any) -> ProposalOut:
    return proposal(
        TRAINING_PROPOSAL,
        "training_submit",
        "Submit training request",
        [("Title", "Forklift safety"), ("Status", "draft")],
        **extra,
    )


def planner(text: str, context: dict[str, Any] | None) -> WorkspaceActionResult | None:
    """The scripted planner: one result per message, keyed on the tab it was sent from."""
    tab = (context or {}).get("tab")
    if text == "email Ali about the roster":
        return DirectoryResult(
            action="directory_lookup",
            candidates=[
                PersonOut(
                    employee_id=ALI_A,
                    display_name="Ali Abdullah",
                    job_title="Driver",
                    work_email="ali.abdullah@example.test",
                ),
                PersonOut(
                    employee_id=ALI_B,
                    display_name="Ali Bakar",
                    job_title="Roster lead",
                    work_email="ali.bakar@example.test",
                ),
            ],
        )
    if text == f"Use employee {ALI_B} for: email Ali about the roster":
        return ProposalResult(action="proposal", proposal=email_proposal(EMAIL_PROPOSAL))
    if text == "submit my training request" and tab != "hr":
        return NavigateResult(action="navigate", tab="hr", resume=True)
    if text == "submit my training request":
        return ProposalResult(action="proposal", proposal=training_proposal())
    if text == "draft the roster follow-up":
        return ProposalResult(action="proposal", proposal=email_proposal(ABANDONED_PROPOSAL))
    if text == "draft the leave note":
        return ProposalResult(action="proposal", proposal=email_proposal(SIDEBAR_PROPOSAL))
    if text == "draft the shift swap":
        return ProposalResult(action="proposal", proposal=email_proposal(RESET_PROPOSAL))
    if text == "open the inbox":
        return NavigateResult(action="navigate", tab="inbox")
    return None


def settled(proposal_id: str, action: str) -> ProposalOut | None:
    for withdrawn in (ABANDONED_PROPOSAL, SIDEBAR_PROPOSAL, RESET_PROPOSAL):
        if action == "cancel" and proposal_id == str(withdrawn):
            return email_proposal(withdrawn, status="cancelled")
    if action == "confirm" and proposal_id == str(EMAIL_PROPOSAL):
        return email_proposal(
            EMAIL_PROPOSAL,
            status="executed",
            result={"recipient": "ali.bakar@example.test", "subject": "Roster update"},
        )
    if action == "confirm" and proposal_id == str(TRAINING_PROPOSAL):
        return training_proposal(
            status="executed",
            result={"request": {"title": "Forklift safety", "status": "submitted"}},
        )
    return None


async def test_workspace_assistant_dock_end_to_end(tmp_path: Path) -> None:
    node = shutil.which("node")
    assert node, "Node is required for browser workspace acceptance"
    assert (WEB / "node_modules/next/dist/bin/next").is_file(), (
        "Console dependencies are missing; run `npm ci --ignore-scripts` in "
        "CSA_WebConsole/csa-console/web before browser workspace acceptance"
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
                    pytest.fail(f"Synthetic workspace frontend did not start; log: {log_path}")
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


async def say(page: Page, text: str) -> None:
    await page.get_by_test_id("assistant-input").fill(text)
    send = page.get_by_test_id("assistant-send")
    await expect(send).to_be_enabled(timeout=WAIT)
    await send.click()


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

    calls: list[dict[str, Any]] = []
    unexpected: list[str] = []
    turns: list[dict[str, Any]] = []
    turns_path = f"workspace/conversations/{THREAD}/turns"
    proposal_path = re.compile(r"workspace/proposals/([0-9a-f-]+)/(confirm|cancel)")

    async def route(request_route: Any) -> None:
        request = request_route.request
        path, _, query = request.url.split("/console/v1/")[1].partition("?")
        body: Any = request.post_data_json if request.method == "POST" else None
        calls.append(
            {
                "method": request.method,
                "path": path,
                "query": query,
                "csrf": request.headers.get("x-csrf-token"),
                "body": body,
            }
        )
        platform = upstream.me["role"] == "platform_admin"
        status, data = 200, None
        now = datetime.now(UTC)
        decided = proposal_path.fullmatch(path)
        if path == "auth/csrf":
            data = {"csrf_token": CSRF}
        elif path == "admin/clients" and platform:
            data = [TENANT_PROFILE]
        elif path in {"inbox/conversations", "inbox/approvals"}:
            data = EMPTY_PAGE
        elif path == "hr/context":
            data = {
                "employee": {"tenant_id": TENANT, "employee_id": str(ALI_B)},
                "client_owner": False,
                "private_areas": [],
                "actions": ["training.read"],
            }
        elif path == "hr/training-requests":
            data = EMPTY_PAGE
        elif path == "workspace/conversations" and request.method == "GET":
            data = EMPTY_PAGE
        elif path == "workspace/conversations" and request.method == "POST":
            thread = WorkspaceThreadOut(id=THREAD, sequence=1, created_at=now)
            data = thread.model_dump(mode="json")
        elif path == turns_path and request.method == "GET":
            data = {"items": turns, "next_cursor": None}
        elif path == turns_path and request.method == "POST":
            result = planner(body["body"], body.get("page_context"))
            if result is None:
                status, data = 500, {"detail": "unexpected synthetic message"}
                unexpected.append(f"turn {body['body']!r}")
            else:
                turn = WorkspaceTurnOut(
                    id=len(turns) + 1,
                    request_key=UUID(body["request_key"]),
                    body=body["body"],
                    result=result,
                    created_at=now,
                )
                data = turn.model_dump(mode="json")
                turns.append(data)
        elif decided and request.method == "POST":
            outcome = settled(decided[1], decided[2])
            if outcome is None:
                status, data = 409, {"detail": "unexpected synthetic decision"}
                unexpected.append(f"POST {path}")
            else:
                data = outcome.model_dump(mode="json")
        else:
            unexpected.append(f"{request.method} {path}?{query}")
            status, data = 404, {"detail": "unexpected synthetic call"}
        await request_route.fulfill(status=status, json=data)

    def posts(suffix: str) -> list[dict[str, Any]]:
        return [c for c in calls if c["method"] == "POST" and c["path"].endswith(suffix)]

    def said(text: str) -> list[dict[str, Any]]:
        return [c for c in posts(turns_path) if c["body"]["body"] == text]

    try:
        await page.route("**/console/v1/**", route)

        # (a) A tenant admin on the Inbox sees the dock; the first turn carries the Inbox tab.
        upstream.me, upstream.packages = make_me("tenant_admin"), PACKAGES
        await page.goto(f"{base}/portal/inbox")
        await page.get_by_test_id("assistant-open").click(timeout=WAIT)
        await expect(page.get_by_test_id("assistant-panel")).to_be_visible()
        await expect(page.get_by_test_id("assistant-tab")).to_have_text("On Customer inbox")
        await say(page, "email Ali about the roster")

        # (b) A directory result lists both Alis; picking one sends the follow-up turn.
        candidates = page.get_by_test_id("assistant-candidate")
        await expect(candidates).to_have_count(2, timeout=WAIT)
        await candidates.filter(has_text="Ali Bakar").click()
        card = page.get_by_test_id("proposal-card")
        await expect(card).to_have_count(1, timeout=WAIT)
        await expect(card).to_contain_text("Ali Bakar <ali.bakar@example.test>")
        await expect(card).to_contain_text("Roster update")
        assert [c["path"] for c in posts("workspace/conversations")] == ["workspace/conversations"]
        first, follow_up = posts(turns_path)
        assert first["csrf"] == CSRF and first["query"] == f"client_id={TENANT}"
        assert first["body"]["body"] == "email Ali about the roster"
        assert first["body"]["page_context"] == {"tab": "inbox"}
        # The pick repeats the originating request so the planner can finish it.
        assert follow_up["body"]["body"] == f"Use employee {ALI_B} for: email Ali about the roster"
        assert follow_up["body"]["page_context"] == {"tab": "inbox"}

        # (c) Confirm posts the decision with CSRF and the executor's receipt is shown.
        await page.get_by_test_id("proposal-confirm").click()
        await expect(page.get_by_test_id("proposal-outcome")).to_have_text(
            "Saved “Roster update” to your Gmail drafts, addressed to "
            "ali.bakar@example.test. Nothing was sent."
        )
        assert [(c["path"], c["query"], c["csrf"]) for c in posts("/confirm")] == [
            (f"workspace/proposals/{EMAIL_PROPOSAL}/confirm", f"client_id={TENANT}", CSRF)
        ]

        # (d) A navigate-with-resume opens HR and re-posts the message once, from HR.
        await say(page, "submit my training request")
        await expect(page).to_have_url(f"{base}/portal/hr", timeout=WAIT)
        training = card.filter(has_text="Forklift safety")
        await expect(training.get_by_test_id("proposal-confirm")).to_be_visible(timeout=WAIT)
        original, resumed = said("submit my training request")
        assert original["body"]["page_context"] == {"tab": "inbox"}
        assert resumed["body"]["page_context"]["tab"] == "hr"
        assert resumed["body"]["request_key"] != original["body"]["request_key"]
        await training.get_by_test_id("proposal-confirm").click()
        await expect(training.get_by_test_id("proposal-outcome")).to_have_text(
            "Submitted “Forklift safety” (now submitted)."
        )
        assert posts("/confirm")[-1]["path"] == f"workspace/proposals/{TRAINING_PROPOSAL}/confirm"
        assert posts("/confirm")[-1]["csrf"] == CSRF

        # (e) Navigating away while a card is still requested withdraws it first.
        await say(page, "draft the roster follow-up")
        abandoned = card.nth(2)
        await expect(abandoned.get_by_test_id("proposal-confirm")).to_be_visible(timeout=WAIT)
        await say(page, "open the inbox")
        await expect(page).to_have_url(f"{base}/portal/inbox", timeout=WAIT)
        await expect(abandoned.get_by_test_id("proposal-outcome")).to_have_text(
            "Cancelled. Nothing was changed."
        )
        (cancel,) = posts("/cancel")
        assert cancel["path"] == f"workspace/proposals/{ABANDONED_PROPOSAL}/cancel"
        assert (cancel["query"], cancel["csrf"]) == (f"client_id={TENANT}", CSRF)
        navigate_at = calls.index(said("open the inbox")[0])
        cancel_at = calls.index(cancel)
        inbox_at = next(
            index
            for index, call in enumerate(calls)
            if index > navigate_at and call["path"] == "inbox/conversations"
        )
        assert navigate_at < cancel_at < inbox_at, "cancel must precede the Inbox load"
        assert len(said("open the inbox")) == 1

        # (e2) Sidebar navigation (no assistant turn) also withdraws a requested card.
        await say(page, "draft the leave note")
        sidebar_card = card.nth(3)
        await expect(sidebar_card.get_by_test_id("proposal-confirm")).to_be_visible(timeout=WAIT)
        await page.get_by_role("link", name="People & HR").click()
        await expect(page).to_have_url(f"{base}/portal/hr", timeout=WAIT)
        await expect(sidebar_card.get_by_test_id("proposal-outcome")).to_have_text(
            "Cancelled. Nothing was changed.", timeout=WAIT
        )
        assert [c["path"] for c in posts("/cancel")][-1] == (
            f"workspace/proposals/{SIDEBAR_PROPOSAL}/cancel"
        )
        assert len(posts("/cancel")) == 2 and not said("People & HR")

        # (e3) Starting a new conversation withdraws the cards it drops.
        await say(page, "draft the shift swap")
        reset_card = card.nth(4)
        await expect(reset_card.get_by_test_id("proposal-confirm")).to_be_visible(timeout=WAIT)
        reset_cancel = re.compile(rf"/workspace/proposals/{RESET_PROPOSAL}/cancel")
        async with page.expect_request(reset_cancel):
            await page.get_by_role("button", name="New conversation").click()
        await expect(card).to_have_count(0, timeout=WAIT)
        assert len(posts("/cancel")) == 3

        # (f) A read-only member never gets the dock (HR's own load proves hydration).
        upstream.me = make_me("read_only")
        async with page.expect_request(re.compile(r"/console/v1/hr/context")):
            await page.goto(f"{base}/portal/hr")
        await expect(page.get_by_test_id("assistant-open")).to_have_count(0)

        # (f2) With WORKSPACE_ASSISTANT_ENABLED off (/auth/me says so) nobody gets the dock.
        upstream.me = make_me("tenant_admin", assistant=False)
        async with page.expect_request(re.compile(r"/console/v1/hr/context")):
            await page.goto(f"{base}/portal/hr")
        await expect(page.get_by_test_id("assistant-open")).to_have_count(0)

        # (g) A platform admin gets the dock only on a page that names the tenant.
        upstream.me, upstream.packages = make_me("platform_admin"), []
        async with page.expect_request(re.compile(r"/console/v1/admin/clients")):
            await page.goto(f"{base}/inbox")
        await expect(page.get_by_test_id("assistant-open")).to_have_count(0)
        await page.goto(f"{base}/inbox?client_id={TENANT}")
        async with page.expect_request(re.compile(r"/console/v1/workspace/conversations\?")):
            await page.get_by_test_id("assistant-open").click(timeout=WAIT)
        listed = [c for c in calls if c["path"] == "workspace/conversations"][-1]
        assert (listed["method"], listed["query"]) == ("GET", f"client_id={TENANT}")

        assert not unexpected, unexpected
        assert not upstream.unexpected, upstream.unexpected
    except Exception as exc:
        exc.add_note(
            f"Browser diagnostics: {diagnostics}; trace: {tmp_path / 'workspace-trace.zip'}; "
            f"frontend log: {tmp_path / 'frontend.log'}"
        )
        raise
    finally:
        (tmp_path / "workspace-browser.log").write_text("\n".join(diagnostics))
        with contextlib.suppress(Exception):  # Preserve the original failure if Chromium crashed.
            await context.tracing.stop(path=tmp_path / "workspace-trace.zip")
        await context.close()
