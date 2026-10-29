"""Real browser acceptance for the Email tab: send with CC, load more, attention, and the send card.

The real Next.js shell runs unmodified from a disposable staged copy, as in
`test_workspace_browser.py`, whose loopback upstream and proposal builder this file reuses:
Playwright answers every browser-side `/console/v1/**` call from a synthetic mailbox. Bodies are
built from the backend contracts (`DraftOut`, `SendResult`, `AttentionPage`) so they cannot
drift, and the send version is checked against `draft_version`. Any call the stub does not
expect fails the test. Nothing here reaches Google.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from csa.email.models import (
    AttentionItem,
    AttentionPage,
    DraftOut,
    RecipientOut,
    SendResult,
    draft_version,
)
from csa.workspace.contracts import (
    ProposalOut,
    ProposalResult,
    WorkspaceThreadOut,
    WorkspaceTurnOut,
)
from playwright.async_api import async_playwright, expect

from tests.test_workspace_browser import (
    CSRF,
    ROOT,
    TENANT,
    WAIT,
    WEB,
    Upstream,
    free_port,
    make_me,
    proposal,
)

pytestmark = pytest.mark.asyncio

PACKAGES = [
    {"key": "customer_support", "status": "active"},
    {"key": "staff_email", "status": "active"},
]
OWNER = "aminah@kedai.test"
MAILBOX = "77777777-7777-4777-8777-777777777771"
REPLY_DRAFT = UUID("66666666-6666-4666-8666-000000000001")
STUCK_DRAFT = UUID("66666666-6666-4666-8666-000000000002")
CARD_DRAFT = UUID("66666666-6666-4666-8666-000000000003")
SEND_PROPOSAL = UUID("55555555-5555-4555-8555-000000000001")
CONVERSATION = UUID("33333333-3333-4333-8333-000000000001")
SENT_AT = "2026-10-29T02:00:00Z"  # 29 Oct 2026, 10:00 in Kuala Lumpur
AS_OF = "2026-10-29T01:30:00Z"  # 29 Oct 2026, 09:30 in Kuala Lumpur
SUPPLIER = "orders@supplier.test"


def thread(n: int) -> dict[str, Any]:
    return {
        "thread_id": f"thread-{n}",
        "subject": f"Synthetic thread {n}",
        "sender": f"Customer {n} <customer{n}@example.test>",
        "snippet": "Synthetic snippet.",
        "received_at": "2026-10-28T02:00:00Z",
        "labels": ["INBOX"],
        "unread": False,
        "category": None,
        "priority": None,
        "summary": None,
        "action_items": [],
        "analysed_at": None,
        "has_body": True,
    }


def outside(address: str) -> bool:
    """The stub's company rule: the owner's own domain is inside, everything else outside."""
    return not address.lower().endswith("@kedai.test")


class MailWorld:
    """One synthetic mailbox: three drafts, a 90-day window of three pages, and the dock."""

    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.calls: list[dict[str, Any]] = []
        self.unexpected: list[str] = []
        self.last_synced_at = (now - timedelta(minutes=10)).isoformat()
        self.more_pages = 2
        self.threads = [thread(1)]
        self.release_attempts = 0
        self.saved: list[dict[str, Any]] = []  # PUT answers, as the console received them
        self.turns: list[dict[str, Any]] = []
        self.drafts: dict[UUID, dict[str, Any]] = {
            REPLY_DRAFT: self.draft(
                REPLY_DRAFT,
                to=["siti@kedai.test"],
                subject="Roster for next week",
                body="Hi Siti,\n\nHere is next week's roster.",
            ),
            STUCK_DRAFT: self.draft(
                STUCK_DRAFT,
                to=["ali@kedai.test"],
                subject="Stock count",
                body="Hi Ali, the stock count is done.",
                status="sending",
            ),
            CARD_DRAFT: self.draft(
                CARD_DRAFT,
                to=[SUPPLIER],
                cc=["siti@kedai.test"],
                subject="Price list",
                body="Hello,\n\nPlease send your latest price list.",
            ),
        }

    @staticmethod
    def draft(
        draft_id: UUID,
        *,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        status: str = "draft",
    ) -> dict[str, Any]:
        cc, bcc = cc or [], bcc or []
        now = datetime.now(UTC)
        return DraftOut(
            id=draft_id,
            thread_id=None,
            recipient=to[0],
            to=to,
            cc=cc,
            bcc=bcc,
            recipients=[RecipientOut(address=a, outside=outside(a)) for a in (*to, *cc, *bcc)],
            subject=subject,
            body=body,
            status=status,  # type: ignore[arg-type]
            source="model",
            gmail_draft_id=None,
            sent_at=None,
            needs_staff_input=False,
            reply_to_message_id=None,
            created_at=now - timedelta(minutes=30),
            updated_at=now - timedelta(minutes=5),
        ).model_dump(mode="json")

    def context(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "can_manage": True,
            "can_connect": True,
            "provider_configured": True,
            "model_configured": True,
            "mailbox": {
                "id": MAILBOX,
                "address": OWNER,
                "status": "connected",
                "scopes": ["gmail.readonly", "gmail.compose"],
                "last_sync_at": self.last_synced_at,
            },
            "disclosure": "Synthetic disclosure.",
            "last_synced_at": self.last_synced_at,
            "more_available": self.more_pages > 0,
        }

    @staticmethod
    def receipt(operation: str, detail: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": operation,
            "resource_id": MAILBOX,
            "status": "synced",
            "duplicate": False,
            "detail": detail,
        }

    def send_card(self, **extra: Any) -> ProposalOut:
        """The counts-only `email_send` card, bound to the draft's server version."""
        built = proposal(
            SEND_PROPOSAL,
            "email_send",
            "Send email to 2 people (1 outside your company)",
            [("Subject", "(shown on open)"), ("Draft", str(CARD_DRAFT))],
            **extra,
        )
        version = DraftOut.model_validate(self.drafts[CARD_DRAFT]).version
        card = built.card.model_copy(update={"expected_version": version})
        return built.model_copy(update={"card": card})

    async def answer(self, method: str, path: str, body: Any) -> tuple[int, Any]:
        draft_path = re.fullmatch(r"email/drafts/([0-9a-f-]+)(/send|/release)?", path)
        if path == "auth/csrf":
            return 200, {"csrf_token": CSRF}
        if path == "email/context" and method == "GET":
            return 200, self.context()
        if path == "email/sync" and method == "POST":
            self.last_synced_at = datetime.now(UTC).isoformat()
            return 200, self.receipt("mailbox.sync", {"threads": 1})
        if path == "email/sync/more" and method == "POST":
            await asyncio.sleep(1)  # long enough to see "Loading…"
            self.more_pages -= 1
            self.threads.append(thread(len(self.threads) + 1))
            return 200, self.receipt(
                "mailbox.sync_more", {"threads": 1, "done": self.more_pages == 0}
            )
        if path == "email/threads" and method == "GET":
            return 200, {"items": list(self.threads), "last_sync_at": self.last_synced_at}
        if path == "email/attention" and method == "GET":
            page = AttentionPage(
                items=[
                    AttentionItem(
                        thread_id="thread-1",
                        sender="Customer 1 <customer1@example.test>",
                        subject="Late delivery to Shah Alam",
                        priority="high",
                        summary="The customer asks when the order will arrive.",
                        next_steps=["Reply with the delivery date"],
                    )
                ],
                as_of=datetime.fromisoformat(AS_OF),
                skipped=1,
            )
            return 200, page.model_dump(mode="json")
        if path == "email/drafts" and method == "GET":
            return 200, {"items": [self.drafts[REPLY_DRAFT], self.drafts[STUCK_DRAFT]]}
        if draft_path:
            draft_id, action = UUID(draft_path[1]), draft_path[2]
            current = self.drafts.get(draft_id)
            if current is None:
                return 404, {"detail": "resource_unavailable"}
            if action is None and method == "GET":
                return 200, current
            if action is None and method == "PUT":
                self.drafts[draft_id] = self.draft(
                    draft_id,
                    to=body["to"],
                    cc=body["cc"],
                    bcc=body["bcc"],
                    subject=body["subject"],
                    body=body["body"],
                )
                self.saved.append(self.drafts[draft_id])
                return 200, self.drafts[draft_id]
            if action == "/send" and method == "POST":
                if body["version"] != draft_version(DraftOut.model_validate(current)):
                    return 409, {"detail": "draft_changed"}
                self.drafts[draft_id] = {**current, "status": "sent", "sent_at": SENT_AT}
                result = SendResult(
                    outcome="sent",
                    sent_at=datetime.fromisoformat(SENT_AT),
                    gmail_message_id="m-1",
                    remaining_this_hour=19,
                )
                return 200, result.model_dump(mode="json")
            if action == "/release" and method == "POST":
                self.release_attempts += 1
                if self.release_attempts == 1:
                    return 409, {"detail": "draft_not_releasable"}
                self.drafts[draft_id] = {
                    **current,
                    "status": "draft",
                    "updated_at": datetime.now(UTC).isoformat(),
                }
                return 200, self.drafts[draft_id]
        if path == "workspace/conversations" and method == "GET":
            return 200, {"items": [], "next_cursor": None}
        if path == "workspace/conversations" and method == "POST":
            now = datetime.now(UTC)
            return 200, WorkspaceThreadOut(id=CONVERSATION, sequence=1, created_at=now).model_dump(
                mode="json"
            )
        turns = f"workspace/conversations/{CONVERSATION}/turns"
        if path == turns and method == "GET":
            return 200, {"items": self.turns, "next_cursor": None}
        if path == turns and method == "POST" and body["body"] == "send the price list request":
            turn = WorkspaceTurnOut(
                id=len(self.turns) + 1,
                request_key=UUID(body["request_key"]),
                body=body["body"],
                result=ProposalResult(action="proposal", proposal=self.send_card()),
                created_at=datetime.now(UTC),
            ).model_dump(mode="json")
            self.turns.append(turn)
            return 200, turn
        if path == f"workspace/proposals/{SEND_PROPOSAL}/confirm" and method == "POST":
            executed = self.send_card(
                status="executed",
                result={
                    "draft_id": str(CARD_DRAFT),
                    "outcome": "sent",
                    "sent_at": SENT_AT,
                    "gmail_message_id": "m-2",
                    "remaining_this_hour": 18,
                },
            )
            return 200, executed.model_dump(mode="json")
        self.unexpected.append(f"{method} {path}")
        return 404, {"detail": "unexpected synthetic call"}

    async def route(self, request_route: Any) -> None:
        request = request_route.request
        path, _, query = request.url.split("/console/v1/")[1].partition("?")
        body: Any = request.post_data_json if request.method in {"POST", "PUT"} else None
        self.calls.append(
            {
                "method": request.method,
                "path": path,
                "query": query,
                "key": request.headers.get("idempotency-key"),
                "body": body,
            }
        )
        status, data = await self.answer(request.method, path, body)
        await request_route.fulfill(status=status, json=data)


async def test_email_tab_sends_loads_more_and_the_card_shows_the_draft(tmp_path: Path) -> None:
    node = shutil.which("node")
    assert node, "Node is required for browser email acceptance"
    assert (WEB / "node_modules/next/dist/bin/next").is_file(), (
        "Console dependencies are missing; run `npm ci --ignore-scripts` in "
        "CSA_WebConsole/csa-console/web before browser email acceptance"
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
                    pytest.fail(f"Synthetic email frontend did not start; log: {log_path}")
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
    world = MailWorld()

    def called(method: str, path: str) -> list[dict[str, Any]]:
        return [c for c in world.calls if c["method"] == method and c["path"] == path]

    try:
        await page.route("**/console/v1/**", world.route)
        upstream.me, upstream.packages = make_me("tenant_admin"), PACKAGES

        # (a) Opening the tab syncs (the last sync is 10 minutes old), then shows the attention.
        await page.goto(f"{base}/portal/email")
        attention = page.get_by_test_id("email-attention")
        await expect(attention).to_contain_text("Late delivery to Shah Alam", timeout=WAIT)
        await expect(attention).to_contain_text("as of 29 Oct 2026, 09:30")
        await expect(attention).to_contain_text("Some emails couldn't be summarised.")
        assert len(called("POST", "email/sync")) == 1
        assert called("POST", "email/sync")[0]["key"]

        # (b) Load more shows "Loading…", adds older threads, and says when the window is done.
        more = page.get_by_test_id("email-load-more")
        await more.click()
        await expect(more).to_have_text("Loading…")
        await expect(page.get_by_text("Synthetic thread 2")).to_be_visible(timeout=WAIT)
        await more.click()
        await expect(page.get_by_text("Synthetic thread 3")).to_be_visible(timeout=WAIT)
        await expect(more).to_have_text("No older email in the last 90 days")
        loads = called("POST", "email/sync/more")
        assert len(loads) == 2 and loads[0]["key"] != loads[1]["key"]
        # Attention loads once on open (after the sync) and never after load more.
        assert len(called("GET", "email/attention")) == 1
        assert world.calls.index(called("GET", "email/attention")[0]) > world.calls.index(
            called("POST", "email/sync")[0]
        )

        # (c) Add a CC, review exactly what goes out, send, and the draft locks with its time.
        await page.get_by_role("button", name=re.compile(r"^Drafts")).click()
        forms = page.locator("form").filter(has=page.get_by_test_id("email-to"))
        first = forms.nth(0)
        await expect(first.get_by_test_id("email-to")).to_have_value("siti@kedai.test")
        await first.get_by_test_id("email-cc").fill(SUPPLIER)
        await first.get_by_test_id("email-send").click()
        dialog = page.get_by_role("dialog")
        await expect(dialog).to_contain_text(SUPPLIER, timeout=WAIT)
        await expect(dialog).to_contain_text("Outside your company")
        await expect(dialog).to_contain_text("Here is next week's roster.")
        (saved,) = called("PUT", f"email/drafts/{REPLY_DRAFT}")
        assert saved["body"]["to"] == ["siti@kedai.test"] and saved["body"]["cc"] == [SUPPLIER]
        await page.get_by_test_id("email-send-confirm").click()
        await expect(page.get_by_test_id("email-sent-at")).to_have_text(
            "Sent at 29 Oct 2026, 10:00", timeout=WAIT
        )
        (send,) = called("POST", f"email/drafts/{REPLY_DRAFT}/send")
        # The tab sends the version the server gave with the saved draft; it hashes nothing.
        (put_reply,) = [c for c in world.saved if c["id"] == str(REPLY_DRAFT)]
        expected = draft_version(DraftOut.model_validate(world.drafts[REPLY_DRAFT]))
        assert send["body"] == {"version": put_reply["version"]} and send["key"]
        assert put_reply["version"] == expected
        await expect(forms.nth(0).get_by_test_id("email-to")).to_be_disabled()
        await expect(forms.nth(0).get_by_test_id("email-send")).to_have_count(0)

        # (d) A send whose outcome is unknown offers the release; too early it asks to wait.
        stuck = forms.nth(1)
        await expect(stuck).to_contain_text("Check your Gmail Sent folder before trying again.")
        await stuck.get_by_test_id("email-release").click()
        await expect(page.get_by_text("Wait a couple of minutes, then try again.")).to_be_visible(
            timeout=WAIT
        )
        await page.get_by_test_id("email-release").click()
        await expect(page.get_by_test_id("email-release")).to_have_count(0, timeout=WAIT)
        await expect(forms.nth(1).get_by_test_id("email-to")).to_be_enabled()
        releases = called("POST", f"email/drafts/{STUCK_DRAFT}/release")
        assert len(releases) == 2 and releases[0]["key"] == releases[1]["key"]

        # (d2) The manual Sync button loads the attention panel exactly once more.
        async with page.expect_request(re.compile(r"/console/v1/email/attention")):
            await page.get_by_role("button", name="Sync inbox").click()
        await expect(page.get_by_role("button", name="Sync inbox")).to_be_enabled(timeout=WAIT)
        assert len(called("POST", "email/sync")) == 2
        assert len(called("GET", "email/attention")) == 2

        # (e) The assistant's send card reads the draft: recipients with the outside flag, body.
        await page.get_by_test_id("assistant-open").click(timeout=WAIT)
        await page.get_by_test_id("assistant-input").fill("send the price list request")
        await page.get_by_test_id("assistant-send").click()
        card = page.get_by_test_id("proposal-card")
        recipients = card.get_by_test_id("proposal-email-recipients")
        await expect(recipients).to_contain_text(SUPPLIER, timeout=WAIT)
        await expect(recipients).to_contain_text("Outside your company")
        await expect(recipients).to_contain_text("siti@kedai.test")
        await expect(card.get_by_test_id("proposal-email-body")).to_contain_text(
            "Please send your latest price list."
        )
        await expect(card).not_to_contain_text("(shown on open)")
        confirm = card.get_by_test_id("proposal-confirm")
        await expect(confirm).to_have_text("Send")
        await confirm.click()
        await expect(card.get_by_test_id("proposal-outcome")).to_have_text(
            "Sent at 29 Oct 2026, 10:00.", timeout=WAIT
        )
        # Dev-mode React may run the card's effect twice; every read is the tenant's own GET.
        reads = called("GET", f"email/drafts/{CARD_DRAFT}")
        assert reads and {read["query"] for read in reads} == {f"client_id={TENANT}"}

        assert not world.unexpected, world.unexpected
        assert not upstream.unexpected, upstream.unexpected
    except Exception as exc:
        exc.add_note(
            f"Browser diagnostics: {diagnostics}; calls: {world.calls}; "
            f"trace: {tmp_path / 'email-trace.zip'}; frontend log: {tmp_path / 'frontend.log'}"
        )
        raise
    finally:
        (tmp_path / "email-browser.log").write_text("\n".join(diagnostics))
        with contextlib.suppress(Exception):  # Preserve the original failure if Chromium crashed.
            await context.tracing.stop(path=tmp_path / "email-trace.zip")
        await context.close()
