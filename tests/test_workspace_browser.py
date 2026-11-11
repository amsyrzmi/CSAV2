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
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from csa.email.models import DraftOut, RecipientOut
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

# One `next dev` and one Chromium serve every case in this file, so they share a module loop.
pytestmark = pytest.mark.asyncio(loop_scope="module")

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
EMAIL_DRAFT = UUID("66666666-6666-4666-8666-666666666661")
PLAN_DRAFT = UUID("66666666-6666-4666-8666-666666666662")
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
    summary: str | None = None,
    language: Literal["en", "ms"] = "en",
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
                summary=summary or f"Synthetic {title.lower()}.",
                fields=[CardField(label=label, value=value) for label, value in fields],
                expires_at=expires,
                expected_version=None,
                language=language,
            ),
            "result": result,
            "created_at": now,
            "expires_at": expires,
        }
    )


def email_proposal(proposal_id: UUID, **extra: Any) -> ProposalOut:
    # Counts-only card (csa/workspace/executors.py): the content is read from the draft on open.
    return proposal(
        proposal_id,
        "email_export",
        "Save email to Gmail drafts for 1 person",
        [("Subject", "(shown on open)"), ("Draft", str(EMAIL_DRAFT))],
        **extra,
    )


def email_draft() -> dict[str, Any]:
    """`GET email/drafts/{id}`: the owner's own draft, as `EmailService.draft_by_id` returns it."""
    now = datetime.now(UTC)
    return DraftOut(
        id=EMAIL_DRAFT,
        thread_id=None,
        recipient="ali.bakar@example.test",
        to=["ali.bakar@example.test"],
        cc=[],
        bcc=[],
        recipients=[RecipientOut(address="ali.bakar@example.test", outside=False)],
        subject="Roster update",
        body="Hi Ali,\n\nThe new roster starts on Monday.",
        status="draft",
        source="model",
        gmail_draft_id=None,
        sent_at=None,
        needs_staff_input=False,
        reply_to_message_id=None,
        created_at=now,
        updated_at=now,
    ).model_dump(mode="json")


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
            result={"draft_id": str(EMAIL_DRAFT), "status": "exported", "gmail_draft_id": "g-1"},
        )
    if action == "confirm" and proposal_id == str(TRAINING_PROPOSAL):
        return training_proposal(
            status="executed",
            result={"request": {"title": "Forklift safety", "status": "submitted"}},
        )
    return None


@contextlib.asynccontextmanager
async def dock_stack(tmp_path: Path) -> AsyncIterator[tuple[str, Upstream, Any]]:
    """The staged console behind a stub upstream, and one Chromium: `(base, upstream, browser)`."""
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
                    yield f"http://127.0.0.1:{port}", upstream, browser
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


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def stack(
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncIterator[tuple[str, Upstream, Any]]:
    async with dock_stack(tmp_path_factory.mktemp("dock")) as shared:
        yield shared


async def test_workspace_assistant_dock_end_to_end(
    stack: tuple[str, Upstream, Any], tmp_path: Path
) -> None:
    base, upstream, browser = stack
    await exercise(browser, base, upstream, tmp_path)


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
        elif path == "services/customer-replies":
            data = {"mode": "auto", "brand_name": "Synthetic", "can_manage": True, "warnings": []}
        elif path in {"inbox/conversations", "inbox/approvals"}:
            data = EMPTY_PAGE
        elif path == "hr/context":
            data = {
                "employee": {"tenant_id": TENANT, "employee_id": str(ALI_B)},
                "client_owner": False,
                "private_areas": [],
                "actions": ["training.read"],
                "switches": ["own_requests"],
                "workflows": [],
                "hr_duties": False,
            }
        elif path == "hr/training-requests":
            data = EMPTY_PAGE
        elif path == f"hr/people/{ALI_B}":
            # HR Home for a member without HR duties shows their own profile and balances.
            data = {
                "person": {
                    "employee_id": str(ALI_B),
                    "display_name": "Ali Bakar",
                    "job_title": "Driver",
                    "department_id": None,
                    "department": None,
                    "manager_id": None,
                    "manager_name": None,
                    "work_email": None,
                    "work_phone": None,
                    "start_date": None,
                    "employment_type": None,
                    "status": "active",
                    "employment_profile": None,
                    "version": None,
                    "account_linked": None,
                },
                "sections": ["overview", "personal", "documents"],
                "can_edit": False,
            }
        elif path == f"hr/people/{ALI_B}/personal":
            # My HR also shows the member's own personal details and documents.
            data = {
                "date_of_birth": None,
                "home_address": None,
                "personal_phone": None,
                "emergency_name": None,
                "emergency_relationship": None,
                "emergency_phone": None,
                "bank_name": None,
                "ic_or_passport": None,
                "bank_account": None,
                "tax_number": None,
                "epf_number": None,
                "socso_number": None,
                "version": None,
                "can_edit": "contact",
            }
        elif path == f"hr/people/{ALI_B}/documents":
            data = {"items": []}
        elif path == "hr/records/balances":
            data = {"items": []}
        elif path == f"email/drafts/{EMAIL_DRAFT}" and request.method == "GET":
            data = email_draft()
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
        # The card stores counts only; the recipients and body come from the draft read.
        await expect(card.get_by_test_id("proposal-email-recipients")).to_contain_text(
            "ali.bakar@example.test", timeout=WAIT
        )
        await expect(card.get_by_test_id("proposal-email-recipients")).not_to_contain_text(
            "Outside your company"
        )
        await expect(card).to_contain_text("Roster update")
        await expect(card.get_by_test_id("proposal-email-body")).to_contain_text(
            "The new roster starts on Monday."
        )
        await expect(card).not_to_contain_text("(shown on open)")
        read = [c for c in calls if c["path"] == f"email/drafts/{EMAIL_DRAFT}"]
        assert [(c["method"], c["query"]) for c in read][:1] == [("GET", f"client_id={TENANT}")]
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
        # The export receipt holds no subject or address, so the outcome line is generic.
        await expect(page.get_by_test_id("proposal-outcome")).to_have_text(
            "Saved to your Gmail drafts. Nothing was sent."
        )
        assert [(c["path"], c["query"], c["csrf"]) for c in posts("/confirm")] == [
            (f"workspace/proposals/{EMAIL_PROPOSAL}/confirm", f"client_id={TENANT}", CSRF)
        ]

        # (d) A navigate-with-resume opens HR and re-posts the message once, from HR.
        # `next dev` compiles the HR route (now every People & HR tab) on first request, which
        # can take longer than the dock's 10 s resume wait. Request it once so the navigate
        # below only routes.
        await page.request.get(f"{base}/portal/hr", timeout=60_000)
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


# --- Plan cards (assistant engine): one card, one confirm, a badge per step, a retry ----------


def plan_id(number: int) -> UUID:
    return UUID(f"77777777-7777-4777-8777-77777777777{number}")


EN_ROWS = [
    ("Send an email", str(EMAIL_DRAFT)),
    ("Send an email", str(PLAN_DRAFT)),
    ("Book, move or cancel a booking", ""),
    ("Decide a held reply", ""),
]
MS_ROWS = [
    ("Hantar e-mel", str(EMAIL_DRAFT)),
    ("Hantar e-mel", str(PLAN_DRAFT)),
    ("Tempah, alih atau batalkan tempahan", ""),
    ("Putuskan balasan yang ditahan", ""),
]
PLAN_SUMMARY = (
    "Each step is checked again when you select Confirm. Sending 2 emails. "
    "Not included: 1 (1 no email address)."
)
STEP_KINDS = ["email_send", "email_send", "booking_action", "inbox_decision", "hr_person"]


def plan_card(
    number: int,
    rows: list[tuple[str, str]],
    *,
    status: str = "requested",
    outcome: Sequence[tuple[str, str | None]] | None = None,
    language: Literal["en", "ms"] = "en",
) -> ProposalOut:
    """A plan card as the server sends it: step titles, draft ids, and (once run) the receipt."""
    result: dict[str, object] | None = None
    if outcome is not None:
        result = {
            "steps": [
                {
                    "index": index,
                    "kind": STEP_KINDS[index],
                    "status": step_status,
                    **({"code": code} if code else {}),
                    **({"receipt": {"outcome": "sent"}} if step_status == "done" else {}),
                }
                for index, (step_status, code) in enumerate(outcome)
            ]
        }
    title = f"Confirm {len(rows)} steps" if language == "en" else f"Sahkan {len(rows)} langkah"
    return proposal(
        plan_id(number),
        "plan",
        title,
        rows,
        status=status,
        result=result,
        summary=PLAN_SUMMARY if language == "en" else None,
        language=language,
    )


class PlanApi:
    """The stubbed console for plan cases: scripted turns and decisions, every call recorded."""

    def __init__(
        self,
        upstream: Upstream,
        turns: dict[str, ProposalOut],
        decisions: dict[tuple[UUID, str], list[tuple[int, Any]]],
    ) -> None:
        self.upstream = upstream
        self.scripted = turns
        self.decisions = decisions
        self.calls: list[dict[str, Any]] = []
        self.unexpected: list[str] = []
        self.turns: list[dict[str, Any]] = []
        self.turns_path = f"workspace/conversations/{THREAD}/turns"
        self.decided = re.compile(r"workspace/proposals/([0-9a-f-]+)/(confirm|cancel|retry)")

    def posts(self, suffix: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["method"] == "POST" and c["path"].endswith(suffix)]

    def drafts_read(self) -> list[str]:
        return [c["path"] for c in self.calls if c["path"].startswith("email/drafts/")]

    async def route(self, request_route: Any) -> None:
        request = request_route.request
        path, _, query = request.url.split("/console/v1/")[1].partition("?")
        body: Any = request.post_data_json if request.method == "POST" else None
        self.calls.append(
            {
                "method": request.method,
                "path": path,
                "query": query,
                "csrf": request.headers.get("x-csrf-token"),
                "body": body,
            }
        )
        status, data = 200, None
        now = datetime.now(UTC)
        decided = self.decided.fullmatch(path)
        if path == "auth/csrf":
            data = {"csrf_token": CSRF}
        elif path == "services/customer-replies":
            data = {"mode": "auto", "brand_name": "Synthetic", "can_manage": True, "warnings": []}
        elif path in {"inbox/conversations", "inbox/approvals"}:
            data = EMPTY_PAGE
        elif path == "hr/context":
            # The shell asks for the member's HR context on every page.
            data = {
                "employee": {"tenant_id": TENANT, "employee_id": str(ALI_B)},
                "client_owner": False,
                "private_areas": [],
                "actions": ["training.read"],
                "switches": ["own_requests"],
                "workflows": [],
                "hr_duties": False,
            }
        elif path == f"email/drafts/{EMAIL_DRAFT}" and request.method == "GET":
            data = email_draft()
        elif path == "workspace/conversations" and request.method == "GET":
            data = EMPTY_PAGE
        elif path == "workspace/conversations" and request.method == "POST":
            data = WorkspaceThreadOut(id=THREAD, sequence=1, created_at=now).model_dump(mode="json")
        elif path == self.turns_path and request.method == "GET":
            data = {"items": self.turns, "next_cursor": None}
        elif path == self.turns_path and request.method == "POST" and body["body"] in self.scripted:
            turn = WorkspaceTurnOut(
                id=len(self.turns) + 1,
                request_key=UUID(body["request_key"]),
                body=body["body"],
                result=ProposalResult(action="proposal", proposal=self.scripted[body["body"]]),
                created_at=now,
            )
            data = turn.model_dump(mode="json")
            self.turns.append(data)
        elif (
            decided
            and request.method == "POST"
            and (queue := self.decisions.get((UUID(decided[1]), decided[2])))
        ):
            status, answer = queue.pop(0) if len(queue) > 1 else queue[0]
            data = answer.model_dump(mode="json") if isinstance(answer, ProposalOut) else answer
        else:
            self.unexpected.append(f"{request.method} {path}?{query}")
            status, data = 404, {"detail": "unexpected synthetic call"}
        await request_route.fulfill(status=status, json=data)


@contextlib.asynccontextmanager
async def plan_dock(
    stack: tuple[str, Upstream, Any],
    tmp_path: Path,
    turns: dict[str, ProposalOut],
    decisions: dict[tuple[UUID, str], list[tuple[int, Any]]],
) -> AsyncIterator[tuple[Page, PlanApi]]:
    """A tenant admin on the Inbox with the dock open and a scripted planner."""
    base, upstream, browser = stack
    upstream.me, upstream.packages = make_me("tenant_admin"), PACKAGES
    context = await browser.new_context()
    context.set_default_navigation_timeout(WAIT)
    await context.add_cookies([{"name": "csa_session", "value": "synthetic-session", "url": base}])
    page = await context.new_page()
    diagnostics: list[str] = []
    page.on("crash", lambda: diagnostics.append("Chromium page crashed"))
    page.on("pageerror", lambda error: diagnostics.append(str(error)))
    page.on("console", lambda message: diagnostics.append(f"{message.type}: {message.text}"))
    api = PlanApi(upstream, turns, decisions)
    try:
        await page.route("**/console/v1/**", api.route)
        await page.goto(f"{base}/portal/inbox")
        await page.get_by_test_id("assistant-open").click(timeout=WAIT)
        await expect(page.get_by_test_id("assistant-panel")).to_be_visible()
        yield page, api
        assert not api.unexpected, api.unexpected
        assert not upstream.unexpected, upstream.unexpected
    except Exception as exc:
        exc.add_note(f"Browser diagnostics: {diagnostics}")
        raise
    finally:
        with contextlib.suppress(Exception):
            await context.close()


async def test_plan_card_shows_one_row_per_step_and_confirms_once(
    stack: tuple[str, Upstream, Any], tmp_path: Path
) -> None:
    plan = plan_card(1, EN_ROWS)
    ran = plan_card(1, EN_ROWS, status="executed", outcome=[("done", None)] * 4)
    async with plan_dock(
        stack, tmp_path, {"run the plan": plan}, {(plan_id(1), "confirm"): [(200, ran)]}
    ) as (page, api):
        await say(page, "run the plan")
        card = page.get_by_test_id("proposal-card")
        await expect(card).to_have_count(1, timeout=WAIT)
        await expect(card.locator("h3")).to_have_text("Confirm 4 steps")
        # The server's count-only notes are shown as given.
        await expect(card).to_contain_text(PLAN_SUMMARY)
        rows = card.get_by_test_id("plan-step")
        await expect(rows).to_have_count(4)
        for index, (label, _) in enumerate(EN_ROWS):
            await expect(rows.nth(index)).to_contain_text(label)
        # Only the email steps carry a draft; nothing is a result yet; nothing is fetched.
        await expect(card.get_by_test_id("plan-step-email")).to_have_count(2)
        await expect(rows.nth(2).get_by_test_id("plan-step-email")).to_have_count(0)
        await expect(card.get_by_test_id("plan-step-status")).to_have_count(0)
        await expect(card.get_by_test_id("plan-retry")).to_have_count(0)
        await expect(card).not_to_contain_text("example.test")
        await expect(card).not_to_contain_text(str(EMAIL_DRAFT))
        assert api.drafts_read() == []
        confirm = card.get_by_test_id("proposal-confirm")
        await expect(confirm).to_have_text("Confirm")
        await expect(card.get_by_test_id("proposal-cancel")).to_have_text("Cancel")
        await confirm.click()
        await expect(card.get_by_test_id("proposal-outcome")).to_have_text("Result: 4 done.")
        # One confirm for the whole plan, with CSRF, and nothing per step.
        assert [(c["path"], c["query"], c["csrf"]) for c in api.posts("/confirm")] == [
            (f"workspace/proposals/{plan_id(1)}/confirm", f"client_id={TENANT}", CSRF)
        ]
        assert not api.posts("/cancel") and not api.posts("/retry")
        await expect(confirm).to_have_count(0)
        await expect(card.get_by_test_id("plan-retry")).to_have_count(0)


async def test_executed_plan_shows_a_badge_per_step(
    stack: tuple[str, Upstream, Any], tmp_path: Path
) -> None:
    rows = [*EN_ROWS, ("Change a person in People", "")]
    outcome: list[tuple[str, str | None]] = [
        ("done", None),
        ("failed", "mailbox_not_connected"),
        ("skipped", "changed"),
        ("not_run", None),
        ("failed", "weird_new_refusal"),
    ]
    plan = plan_card(2, rows)
    ran = plan_card(2, rows, status="executed", outcome=outcome)
    async with plan_dock(
        stack, tmp_path, {"run the plan": plan}, {(plan_id(2), "confirm"): [(200, ran)]}
    ) as (page, api):
        await say(page, "run the plan")
        card = page.get_by_test_id("proposal-card")
        await expect(card.get_by_test_id("proposal-confirm")).to_be_visible(timeout=WAIT)
        await card.get_by_test_id("proposal-confirm").click()
        steps = card.get_by_test_id("plan-step")
        await expect(steps).to_have_count(5, timeout=WAIT)
        for index, (badge, state) in enumerate(
            [
                ("Done", "done"),
                ("Failed", "failed"),
                ("Skipped", "skipped"),
                ("Not run", "not_run"),
                ("Failed", "failed"),
            ]
        ):
            await expect(steps.nth(index).get_by_test_id("plan-step-status")).to_have_text(badge)
            await expect(steps.nth(index)).to_have_attribute("data-status", state)
        reasons = [
            None,
            "Connect your Gmail on the Connections tab first.",
            "It changed since you reviewed it.",
            "It was never started.",
            "This step could not be completed.",
        ]
        for index, reason in enumerate(reasons):
            shown = steps.nth(index).get_by_test_id("plan-step-reason")
            if reason is None:
                await expect(shown).to_have_count(0)
            else:
                await expect(shown).to_have_text(reason)
        # A raw refusal code is never shown, known or not.
        await expect(card).not_to_contain_text("weird_new_refusal")
        await expect(card).not_to_contain_text("mailbox_not_connected")
        await expect(card.get_by_test_id("proposal-outcome")).to_have_text(
            "Result: 1 done, 2 failed, 1 skipped, 1 not run."
        )


async def test_retry_button_appears_only_for_failed_or_not_run_steps_and_opens_a_new_card(
    stack: tuple[str, Upstream, Any], tmp_path: Path
) -> None:
    rows = EN_ROWS[2:]
    all_done = plan_card(
        3, rows, status="executed", outcome=[("done", None), ("skipped", "changed")]
    )
    mixed = plan_card(
        4,
        EN_ROWS[:3],
        status="executed",
        outcome=[("done", None), ("failed", "provider_unreachable"), ("not_run", None)],
    )
    retry_card = plan_card(5, EN_ROWS[:2])
    cancelled = plan_card(5, EN_ROWS[:2], status="cancelled")
    async with plan_dock(
        stack,
        tmp_path,
        {"run plan a": plan_card(3, rows), "run plan b": plan_card(4, EN_ROWS[:3])},
        {
            (plan_id(3), "confirm"): [(200, all_done)],
            (plan_id(4), "confirm"): [(200, mixed)],
            (plan_id(4), "retry"): [
                (409, {"detail": "plan_nothing_to_retry"}),
                (201, retry_card),
            ],
            (plan_id(5), "cancel"): [(200, cancelled)],
        },
    ) as (page, api):
        card = page.get_by_test_id("proposal-card")
        retry = page.get_by_test_id("plan-retry")

        # Every step done or changed since the card: nothing to retry.
        await say(page, "run plan a")
        await expect(card.nth(0).get_by_test_id("proposal-confirm")).to_be_visible(timeout=WAIT)
        await card.nth(0).get_by_test_id("proposal-confirm").click()
        await expect(card.nth(0).get_by_test_id("proposal-outcome")).to_have_text(
            "Result: 1 done, 1 skipped."
        )
        await expect(retry).to_have_count(0)

        # A failed or not-run step offers Retry on that card only.
        await say(page, "run plan b")
        await expect(card.nth(1).get_by_test_id("proposal-confirm")).to_be_visible(timeout=WAIT)
        await card.nth(1).get_by_test_id("proposal-confirm").click()
        await expect(retry).to_have_count(1, timeout=WAIT)
        await expect(card.nth(1).get_by_test_id("plan-retry")).to_have_text("Retry these")

        # A refusal is a sentence on the card, and Retry stays available.
        await retry.click()
        await expect(card.nth(1)).to_contain_text(
            "Nothing is left to retry. The other steps were done, or have changed since you "
            "reviewed them."
        )
        await expect(card).to_have_count(2)
        await expect(retry).to_be_enabled()

        # The new requested plan opens below the old card, bound to the old plan's retry route.
        await retry.click()
        await expect(card).to_have_count(3, timeout=WAIT)
        fresh = card.nth(2)
        await expect(fresh.locator("h3")).to_have_text("Confirm 2 steps")
        await expect(fresh.get_by_test_id("proposal-confirm")).to_be_visible()
        await expect(fresh.get_by_test_id("proposal-cancel")).to_be_visible()
        await expect(retry).to_have_count(0)
        assert [(c["path"], c["query"], c["csrf"]) for c in api.posts("/retry")] == [
            (f"workspace/proposals/{plan_id(4)}/retry", f"client_id={TENANT}", CSRF)
        ] * 2

        # The dock tracks the retry card (no turn holds it), so leaving withdraws it too.
        cancel_route = re.compile(rf"/workspace/proposals/{plan_id(5)}/cancel")
        async with page.expect_request(cancel_route):
            await page.get_by_role("button", name="New conversation").click()
        await expect(card).to_have_count(0, timeout=WAIT)
        assert [c["path"] for c in api.posts("/cancel")] == [
            f"workspace/proposals/{plan_id(5)}/cancel"
        ]


async def test_email_step_opens_its_draft_through_the_existing_preview(
    stack: tuple[str, Upstream, Any], tmp_path: Path
) -> None:
    rows = [EN_ROWS[0], EN_ROWS[1], EN_ROWS[3]]
    async with plan_dock(stack, tmp_path, {"run the plan": plan_card(6, rows)}, {}) as (page, api):
        await say(page, "run the plan")
        card = page.get_by_test_id("proposal-card")
        await expect(card).to_have_count(1, timeout=WAIT)
        steps = card.get_by_test_id("plan-step")
        await expect(steps).to_have_count(3)
        # The plan can hold many drafts; none is read until its row is opened.
        assert api.drafts_read() == []
        first = steps.nth(0)
        toggle = first.get_by_test_id("plan-step-email")
        await expect(toggle).to_have_text("Show email")
        await toggle.click()
        recipients = first.get_by_test_id("plan-step-email-recipients")
        await expect(recipients).to_contain_text("ali.bakar@example.test", timeout=WAIT)
        await expect(first).to_contain_text("Roster update")
        await expect(first.get_by_test_id("plan-step-email-body")).to_contain_text(
            "The new roster starts on Monday."
        )
        await expect(toggle).to_have_text("Hide email")
        await expect(toggle).to_have_attribute("aria-expanded", "true")
        read = [c for c in api.calls if c["path"] == f"email/drafts/{EMAIL_DRAFT}"]
        assert [(c["method"], c["query"]) for c in read] == [("GET", f"client_id={TENANT}")]
        # Only that row opened; the other email step and the non-email step show nothing.
        await expect(steps.nth(1).get_by_test_id("plan-step-email-recipients")).to_have_count(0)
        assert api.drafts_read() == [f"email/drafts/{EMAIL_DRAFT}"]
        await expect(steps.nth(2).get_by_test_id("plan-step-email")).to_have_count(0)
        # Hiding and showing again reuses the draft already read.
        await toggle.click()
        await expect(recipients).to_have_count(0)
        await toggle.click()
        await expect(recipients).to_contain_text("ali.bakar@example.test")
        assert api.drafts_read() == [f"email/drafts/{EMAIL_DRAFT}"]
        # Nothing but the owner's own preview names an address.
        await expect(steps.nth(1)).not_to_contain_text("example.test")
        await expect(card.locator("h3")).not_to_contain_text("example.test")


async def test_malay_plan_card_text(stack: tuple[str, Upstream, Any], tmp_path: Path) -> None:
    rows = [*MS_ROWS]
    outcome: list[tuple[str, str | None]] = [
        ("done", None),
        ("failed", "mailbox_not_connected"),
        ("not_run", None),
        ("failed", "weird_new_refusal"),
    ]
    plan = plan_card(7, rows, language="ms")
    ran = plan_card(7, rows, status="executed", outcome=outcome, language="ms")
    fresh = plan_card(8, MS_ROWS[:2], language="ms")
    fresh_ran = plan_card(
        8, MS_ROWS[:2], status="executed", outcome=[("done", None)] * 2, language="ms"
    )
    async with plan_dock(
        stack,
        tmp_path,
        {"jalankan pelan": plan},
        {
            (plan_id(7), "confirm"): [(200, ran)],
            (plan_id(7), "retry"): [(409, {"detail": "plan_nothing_to_retry"}), (201, fresh)],
            (plan_id(8), "confirm"): [(200, fresh_ran)],
        },
    ) as (page, api):
        await say(page, "jalankan pelan")
        card = page.get_by_test_id("proposal-card")
        await expect(card).to_have_count(1, timeout=WAIT)
        await expect(card.locator("h3")).to_have_text("Sahkan 4 langkah")
        await expect(card.get_by_test_id("plan-step").nth(2)).to_contain_text(
            "Tempah, alih atau batalkan tempahan"
        )
        await expect(card.get_by_test_id("plan-step-email").first).to_have_text("Tunjuk e-mel")
        await expect(card.get_by_test_id("proposal-confirm")).to_have_text("Sahkan")
        await expect(card.get_by_test_id("proposal-cancel")).to_have_text("Batal")
        await card.get_by_test_id("proposal-confirm").click()
        steps = card.get_by_test_id("plan-step")
        for index, badge in enumerate(["Selesai", "Gagal", "Tidak dijalankan", "Gagal"]):
            await expect(steps.nth(index).get_by_test_id("plan-step-status")).to_have_text(
                badge, timeout=WAIT
            )
        await expect(steps.nth(1).get_by_test_id("plan-step-reason")).to_have_text(
            "Sambungkan Gmail anda di tab Email assistant dahulu."
        )
        await expect(steps.nth(2).get_by_test_id("plan-step-reason")).to_have_text(
            "Ia tidak pernah dimulakan."
        )
        await expect(steps.nth(3).get_by_test_id("plan-step-reason")).to_have_text(
            "Langkah ini tidak dapat diselesaikan."
        )
        await expect(card).not_to_contain_text("weird_new_refusal")
        await expect(card.get_by_test_id("proposal-outcome")).to_have_text(
            "Keputusan: 1 selesai, 2 gagal, 1 tidak dijalankan."
        )
        retry = card.get_by_test_id("plan-retry")
        await expect(retry).to_have_text("Cuba semula ini")
        await retry.click()
        await expect(card).to_contain_text(
            "Tiada lagi yang boleh dicuba semula. Langkah lain sudah selesai atau telah berubah "
            "sejak anda menyemaknya."
        )
        await retry.click()
        await expect(card).to_have_count(2, timeout=WAIT)
        again = card.nth(1)
        await expect(again.locator("h3")).to_have_text("Sahkan 2 langkah")
        await expect(again.get_by_test_id("proposal-confirm")).to_have_text("Sahkan")
        await again.get_by_test_id("proposal-confirm").click()
        await expect(again.get_by_test_id("proposal-outcome")).to_have_text(
            "Keputusan: 2 selesai.", timeout=WAIT
        )
        await expect(page.get_by_test_id("plan-retry")).to_have_count(0)
        assert [c["path"] for c in api.posts("/confirm")] == [
            f"workspace/proposals/{plan_id(7)}/confirm",
            f"workspace/proposals/{plan_id(8)}/confirm",
        ]
