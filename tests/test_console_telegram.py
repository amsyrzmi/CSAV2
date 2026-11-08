"""Console routes: a platform admin sets up a company's Telegram bot; everyone else only reads."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import structlog
from csa.console.channels import build_channels_router
from csa.core import crypto

from tests.conftest import requires_docker
from tests.test_console_services import activate
from tests.test_console_sessions import SessionWorld
from tests.test_telegram_connect import TelegramStub

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

PATH = "/console/v1/channels/telegram"
ORIGIN = "https://hooks.example.test"
CHAT = "-1001234567890"


@pytest.fixture
async def telegram(sessions: SessionWorld) -> AsyncIterator[TelegramStub]:
    stub = TelegramStub()
    async with httpx.AsyncClient(transport=httpx.MockTransport(stub.handle)) as client:
        sessions.app.state.telegram_http = client
        yield stub


@pytest.fixture(autouse=True)
async def routes(
    sessions: SessionWorld, telegram: TelegramStub, monkeypatch: pytest.MonkeyPatch
) -> None:
    for statement in (
        "DELETE FROM webhook_endpoints WHERE credential_id IN "
        "(SELECT id FROM channel_credentials WHERE channel='telegram')",
        "DELETE FROM staff_notifications WHERE target_id IN "
        "(SELECT id FROM staff_notification_targets WHERE channel='telegram')",
        "DELETE FROM staff_notification_targets WHERE channel='telegram'",
        "DELETE FROM channel_credentials WHERE channel='telegram'",
    ):
        await sessions.q(statement)  # type: ignore[arg-type]
    sessions.app.include_router(build_channels_router())
    monkeypatch.setattr(crypto, "_key", lambda: b"\x07" * 32)
    monkeypatch.setattr(
        "csa.console.telegram_connect.get_settings", lambda: SimpleNamespace(hooks_origin=ORIGIN)
    )
    for tenant in sessions.tenants:
        await activate(sessions, tenant, "customer_support")


def url(w: SessionWorld, tenant: int = 0, suffix: str = "") -> str:
    return f"{PATH}{suffix}?client_id={w.tenants[tenant]}"


async def test_a_platform_admin_connects_a_company_and_nothing_returns_the_token(
    sessions: SessionWorld, telegram: TelegramStub
) -> None:
    w = sessions
    token = telegram.add(123456789, "synth_bot")
    with structlog.testing.capture_logs() as logs:
        response = await w.mutate(w.admin, url(w), {"bot_token": token}, method="PUT")
        got = await w.admin.get(url(w))
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["connected"] and out["bot_username"] == "synth_bot" and out["hooks_ready"]
    assert got.status_code == 200 and got.json() == out
    secret = json.loads(telegram.calls("setWebhook")[0].content)["secret_token"]
    audits = await w.q(
        "SELECT details::text d FROM console_audit WHERE client_id=%s AND action LIKE %s",
        w.tenants[0],
        "channels.telegram.%",
    )
    assert len(audits) == 1
    for text in (response.text, got.text, json.dumps(logs, default=str), audits[0]["d"]):
        assert token not in text and secret not in text


@pytest.mark.parametrize("role", ["tenant_admin", "tenant_staff", "read_only"])
async def test_company_members_read_but_never_write(
    sessions: SessionWorld, telegram: TelegramStub, role: str
) -> None:
    w = sessions
    token = telegram.add(123456789, "synth_bot")
    await w.mutate(w.admin, url(w), {"bot_token": token}, method="PUT")
    telegram.requests.clear()
    member, _ = await w.member(role=role)
    got = await member.get(PATH)
    assert got.status_code == 200 and got.json()["connected"] is True
    assert token not in got.text
    attempts = [
        ("PUT", PATH, {"bot_token": token}),
        ("PUT", f"{PATH}/staff-chat", {"chat_id": CHAT}),
        ("POST", f"{PATH}/check", None),
        ("DELETE", PATH, None),
    ]
    for method, path, body in attempts:
        denied = await w.mutate(member, path, body, method=method)
        assert denied.status_code == 403, (method, path)
        # A read-only member is stopped earlier by the console's own write guard.
        expected = {"platform_admin_only", "write permission required"}
        assert denied.json()["detail"] in expected, (method, path)
        if role != "read_only":
            assert denied.json()["detail"] == "platform_admin_only", (method, path)
    assert telegram.requests == []
    assert (await w.admin.get(url(w))).json()["connected"] is True


async def test_a_member_cannot_read_another_company(sessions: SessionWorld) -> None:
    w = sessions
    member, _ = await w.member(role="tenant_admin")
    assert (await member.get(url(w, 1))).status_code == 403


async def test_a_platform_admin_must_name_the_company(sessions: SessionWorld) -> None:
    assert (await sessions.admin.get(PATH)).status_code == 400


async def test_the_customer_support_package_gates_the_routes(sessions: SessionWorld) -> None:
    w = sessions
    created = await w.mutate(
        w.admin, "/console/v1/admin/clients", {"slug": f"synthetic-{uuid4()}", "name": "No pkg"}
    )
    response = await w.admin.get(f"{PATH}?client_id={created.json()['id']}")
    assert response.status_code == 403 and "package_inactive" in response.text


async def test_the_machine_token_is_refused(sessions: SessionWorld) -> None:
    w = sessions
    response = await w.admin.get(url(w), headers={"Authorization": "Bearer synthetic"})
    assert response.status_code == 403


async def test_a_connect_can_carry_the_staff_chat_and_a_bad_chat_leaves_the_bot_connected(
    sessions: SessionWorld, telegram: TelegramStub
) -> None:
    w = sessions
    good = telegram.add(111111111, "good_bot")
    body = {"bot_token": good, "staff_chat_id": CHAT}
    response = await w.mutate(w.admin, url(w, 0), body, method="PUT")
    assert response.status_code == 200, response.text
    assert response.json()["staff_chat"] == {"address": CHAT, "verified": True}
    other = telegram.add(222222222, "other_bot")
    telegram.send_status = 400
    bad = await w.mutate(
        w.admin, url(w, 1), {"bot_token": other, "staff_chat_id": CHAT}, method="PUT"
    )
    assert bad.status_code == 422 and bad.json()["detail"] == "telegram_chat_unreachable"
    status = (await w.admin.get(url(w, 1))).json()
    assert status["connected"] is True and status["staff_chat"] is None


@pytest.mark.parametrize(
    ("setup", "status", "detail"),
    [
        ("malformed", 422, "telegram_token_malformed"),
        ("rejected", 422, "telegram_token_rejected"),
        ("outage", 502, "telegram_unreachable"),
        ("in_use", 409, "telegram_bot_in_use"),
        ("foreign", 409, "telegram_webhook_in_use"),
    ],
)
async def test_each_refusal_reaches_the_client_with_its_status_and_code(
    sessions: SessionWorld, telegram: TelegramStub, setup: str, status: int, detail: str
) -> None:
    w = sessions
    token = telegram.add(123456789, "synth_bot")
    if setup == "malformed":
        token = "x" * 25
    elif setup == "rejected":
        telegram.getme_status = 401
    elif setup == "outage":
        telegram.getme_status = 0
    elif setup == "in_use":
        assert (
            await w.mutate(w.admin, url(w, 1), {"bot_token": token}, method="PUT")
        ).status_code == 200
    else:
        telegram.webhooks[123456789] = "https://elsewhere.example.test/x"
    response = await w.mutate(w.admin, url(w, 0), {"bot_token": token}, method="PUT")
    assert (response.status_code, response.json()["detail"]) == (status, detail)
    assert token not in response.text


async def test_staff_chat_check_and_disconnect_routes(
    sessions: SessionWorld, telegram: TelegramStub
) -> None:
    w = sessions
    token = telegram.add(123456789, "synth_bot")
    await w.mutate(w.admin, url(w), {"bot_token": token}, method="PUT")
    chat = await w.mutate(w.admin, url(w, 0, "/staff-chat"), {"chat_id": CHAT}, method="PUT")
    assert chat.status_code == 200 and chat.json()["staff_chat"]["verified"] is True
    check = await w.mutate(w.admin, url(w, 0, "/check"), None, method="POST")
    assert check.status_code == 200
    assert check.json() == {
        "webhook_url_matches": True,
        "pending_update_count": 0,
        "last_error": None,
    }
    gone = await w.mutate(w.admin, url(w), None, method="DELETE")
    assert gone.status_code == 204
    after = (await w.admin.get(url(w))).json()
    assert after["connected"] is False and after["staff_chat"] is None
    again = await w.mutate(w.admin, url(w, 0, "/check"), None, method="POST")
    assert again.status_code == 409 and again.json()["detail"] == "telegram_not_connected"


async def test_the_connect_body_refuses_unknown_fields(sessions: SessionWorld) -> None:
    w = sessions
    response = await w.mutate(
        w.admin, url(w), {"bot_token": "x" * 30, "display_name": "nope"}, method="PUT"
    )
    assert response.status_code == 422
