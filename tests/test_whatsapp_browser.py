"""Real browser acceptance for connecting a WhatsApp number and the channel-aware inbox.

Reuses the stubbed-upstream harness of the services browser test: the real Next.js
shell runs from a disposable staged copy, server-side layout fetches hit a loopback
stub, and every browser-side `/console/v1/*` call is answered by Playwright.
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
from playwright.async_api import async_playwright, expect

from tests.test_services_browser import (
    ROOT,
    WAIT,
    WEB,
    Upstream,
    free_port,
    make_me,
    make_service,
)

pytestmark = pytest.mark.asyncio

ACCESS_TOKEN = "EAAG-synthetic-access-token-0123456789"
GRAPH_DISPLAY = "+60 12-345 6789"
WINDOW_NOTICE = (
    "WhatsApp only allows replies within 24 hours of the customer's last message. "
    "Wait for the customer to message again."
)
TOKEN_REJECTED = (
    "WhatsApp did not accept that access token for this phone number ID. "
    "Check both in your Meta app, then try again."
)
CALLBACK = "https://hooks.invalid/channels/whatsapp"
WHATSAPP_ID = "aaaaaaaa-0000-4000-8000-000000000001"
TELEGRAM_ID = "bbbbbbbb-0000-4000-8000-000000000002"
DISCONNECTED = {
    "connected": False,
    "display_phone": None,
    "phone_number_id_last4": None,
    "waba_id": None,
    "enabled": False,
    "callback_url": CALLBACK,
    "connected_at": None,
}


def conversation(identifier: str, channel: str) -> dict[str, Any]:
    return {
        "id": identifier,
        "channel": channel,
        "status": "open",
        "owned_by": None,
        "ai_muted_until": None,
        "human_muted": False,
        "inbox_version": 1,
        "opened_at": "2026-10-27T00:00:00Z",
    }


async def test_connect_whatsapp_number_and_filter_inbox(tmp_path: Path) -> None:
    node = shutil.which("node")
    assert node, "Node is required for browser WhatsApp acceptance"
    assert (WEB / "node_modules/next/dist/bin/next").is_file(), (
        "Console dependencies are missing; run `npm ci --ignore-scripts` in "
        "CSA_WebConsole/csa-console/web before browser WhatsApp acceptance"
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
                    pytest.fail(f"Synthetic WhatsApp frontend did not start; log: {log_path}")
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
    context = await browser.new_context()
    context.set_default_navigation_timeout(WAIT)
    await context.add_cookies([{"name": "csa_session", "value": "synthetic-session", "url": base}])
    await context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = await context.new_page()
    diagnostics: list[str] = []
    page.on("crash", lambda: diagnostics.append("Chromium page crashed"))
    page.on("pageerror", lambda error: diagnostics.append(str(error)))
    page.on("console", lambda message: diagnostics.append(f"{message.type}: {message.text}"))

    state: dict[str, Any] = {"number": dict(DISCONNECTED), "reject_next_put": True}
    puts: list[dict[str, Any]] = []
    inbox_queries: list[str] = []
    unexpected: list[str] = []
    everything = [conversation(WHATSAPP_ID, "whatsapp"), conversation(TELEGRAM_ID, "telegram")]

    async def route(request_route: Any) -> None:
        request = request_route.request
        path, _, query = request.url.split("/console/v1/")[1].partition("?")
        status, data = 200, None
        if path == "auth/csrf":
            data = {"csrf_token": "synthetic-csrf"}
        elif path == "services" and request.method == "GET":
            data = [make_service("customer_support", "Customer Support", "base", "active")]
        elif path == "channels/whatsapp" and request.method == "GET":
            data = state["number"]
        elif path == "channels/whatsapp" and request.method == "PUT":
            body = request.post_data_json
            puts.append({"csrf": request.headers.get("x-csrf-token"), "body": body})
            if state["reject_next_put"]:
                # The gateway's Graph check refused the token: nothing is stored.
                state["reject_next_put"] = False
                status, data = 422, {"detail": "whatsapp_token_rejected"}
            else:
                state["number"] = {
                    **DISCONNECTED,
                    "connected": True,
                    "enabled": True,
                    "display_phone": GRAPH_DISPLAY,
                    "phone_number_id_last4": body["phone_number_id"][-4:],
                    "waba_id": body["waba_id"],
                    "connected_at": "2026-10-27T00:00:00Z",
                }
                data = state["number"]
        elif path == f"inbox/conversations/{WHATSAPP_ID}/messages":
            data = {
                "items": [
                    {
                        "id": "cccccccc-0000-4000-8000-000000000003",
                        "direction": "outbound",
                        "author": "human_support",
                        "body": "Your parcel left today.",
                        "created_at": "2026-10-27T00:00:00Z",
                        "trace_id": None,
                        "delivery_status": "delivered",
                        "delivery_error": None,
                        "provider_status": "failed",
                        "provider_error": "131047",
                    }
                ],
                "next_cursor": None,
            }
        elif path == f"inbox/conversations/{WHATSAPP_ID}":
            data = everything[0]
        elif path == "inbox/conversations":
            inbox_queries.append(query)
            wanted = [part[8:] for part in query.split("&") if part.startswith("channel=")]
            items = [c for c in everything if not wanted or c["channel"] in wanted]
            data = {"items": items, "next_cursor": None}
        elif path == "inbox/approvals":
            data = {"items": [], "next_cursor": None}
        else:
            unexpected.append(f"{request.method} {path}?{query}")
            status, data = 500, {"detail": "unexpected synthetic call"}
        await request_route.fulfill(status=status, json=data)

    try:
        await page.route("**/console/v1/**", route)
        upstream.me = make_me("tenant_admin")
        upstream.packages = [{"key": "customer_support", "status": "active"}]

        # Connect a number as tenant admin; the token is sent once and never shown again.
        await page.goto(f"{base}/portal/services")
        await expect(page.get_by_test_id("whatsapp-phone-number-id")).to_be_visible(timeout=WAIT)
        await page.get_by_test_id("whatsapp-phone-number-id").fill("109876543210987")
        await page.get_by_test_id("whatsapp-waba-id").fill("208765432109876")
        assert await page.get_by_test_id("whatsapp-display-phone").count() == 0
        await page.get_by_test_id("whatsapp-access-token").fill(ACCESS_TOKEN)
        await page.get_by_test_id("whatsapp-connect").click()
        # A token WhatsApp refuses is explained in plain words, without the code.
        await expect(page.get_by_text(TOKEN_REJECTED)).to_be_visible(timeout=WAIT)
        assert "whatsapp_token_rejected" not in await page.inner_text("body")
        await page.get_by_test_id("whatsapp-connect").click()
        connected = page.get_by_test_id("whatsapp-connected")
        await expect(connected).to_contain_text("Connected", timeout=WAIT)
        await expect(connected).to_contain_text(GRAPH_DISPLAY)
        sent = {
            "csrf": "synthetic-csrf",
            "body": {
                "phone_number_id": "109876543210987",
                "waba_id": "208765432109876",
                "access_token": ACCESS_TOKEN,
            },
        }
        assert puts == [sent, sent]
        assert ACCESS_TOKEN not in await page.inner_text("body")
        assert ACCESS_TOKEN not in await page.content()
        assert await page.get_by_test_id("whatsapp-access-token").count() == 0

        # The inbox shows both channel badges and the filter narrows to WhatsApp.
        await page.goto(f"{base}/portal/inbox")
        badges = page.get_by_test_id("inbox-channel-badge")
        await expect(badges).to_have_count(2, timeout=WAIT)
        await expect(badges.nth(0)).to_have_text("WhatsApp")
        await expect(badges.nth(1)).to_have_text("Telegram")
        await page.get_by_test_id("inbox-channel-filter").select_option("whatsapp")
        await expect(badges).to_have_count(1, timeout=WAIT)
        await expect(badges.first).to_have_text("WhatsApp")
        assert any("channel=whatsapp" in query for query in inbox_queries)
        assert ACCESS_TOKEN not in await page.inner_text("body")

        # A reply Meta later reported as failed reads "not delivered" with the 24-hour rule.
        await badges.first.click()
        notice = page.get_by_test_id("inbox-delivery-notice")
        await expect(notice).to_have_text(WINDOW_NOTICE, timeout=WAIT)
        await expect(page.get_by_text("Staff · not delivered")).to_be_visible()
        assert "131047" not in await page.inner_text("body")

        assert not unexpected, unexpected
        assert not upstream.unexpected, upstream.unexpected
    except Exception as exc:
        exc.add_note(
            f"Browser diagnostics: {diagnostics}; trace: {tmp_path / 'whatsapp-trace.zip'}; "
            f"frontend log: {tmp_path / 'frontend.log'}"
        )
        raise
    finally:
        (tmp_path / "whatsapp-browser.log").write_text("\n".join(diagnostics))
        with contextlib.suppress(Exception):  # Preserve the original failure if Chromium crashed.
            await context.tracing.stop(path=tmp_path / "whatsapp-trace.zip")
        await context.close()
