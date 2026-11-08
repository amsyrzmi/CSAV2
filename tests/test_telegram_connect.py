"""Connect, replace and read a company's Telegram bot; the token is proven first and never leaks."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
import structlog
from csa.channels.ingress import IngressError, TelegramIngress
from csa.console import telegram_connect as tc
from csa.console.contracts import ConsoleActor
from csa.core import crypto, db
from csa.core.tenancy import tx

from tests.conftest import requires_docker
from tests.test_console_sessions import SessionWorld

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

ORIGIN = "https://hooks.example.test"
SECRET_COLUMN = "meta.telegram_webhook_secret_enc"
TAIL = "synthetic_Token-0123456789_abcdefghij"


def token_for(bot_id: int, variant: str = "") -> str:
    return f"{bot_id}:{variant}{TAIL}"[: len(str(bot_id)) + 1 + 36]


@dataclass
class TelegramStub:
    """A tiny Bot API: tokens map to bots, a webhook belongs to the bot, not the token."""

    bots: dict[str, tuple[int, str]] = field(default_factory=dict)
    webhooks: dict[int, str] = field(default_factory=dict)
    getme_status: int = 200
    set_webhook_failures: int = 0
    send_status: int = 200
    delete_fails: bool = False
    pending: int = 0
    webhook_error: str | None = None
    sent: list[dict] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)

    def add(self, bot_id: int, username: str, variant: str = "") -> str:
        token = token_for(bot_id, variant)
        self.bots[token] = (bot_id, username)
        return token

    def calls(self, method: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith(f"/{method}")]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        _, rest = request.url.path.split("/bot", 1)
        token, method = rest.rsplit("/", 1)
        if self.getme_status == 0:
            raise httpx.ConnectError("synthetic outage")
        if self.getme_status != 200 or token not in self.bots:
            return httpx.Response(401, json={"ok": False})
        bot_id, username = self.bots[token]
        body = json.loads(request.content or b"{}")
        if method == "getMe":
            result: object = {"id": bot_id, "is_bot": True, "username": username}
        elif method == "getWebhookInfo":
            result = {
                "url": self.webhooks.get(bot_id, ""),
                "pending_update_count": self.pending,
                "last_error_message": self.webhook_error,
            }
        elif method == "setWebhook":
            if self.set_webhook_failures:
                self.set_webhook_failures -= 1
                return httpx.Response(500, json={"ok": False})
            self.webhooks[bot_id] = body["url"]
            result = True
        elif method == "deleteWebhook":
            if self.delete_fails:
                return httpx.Response(500, json={"ok": False})
            self.webhooks.pop(bot_id, None)
            result = True
        elif method == "sendMessage":
            if self.send_status != 200:
                return httpx.Response(self.send_status, json={"ok": False})
            self.sent.append({"token": token, **body})
            result = {"message_id": 7}
        else:
            return httpx.Response(404, json={"ok": False})
        return httpx.Response(200, json={"ok": True, "result": result})


@pytest.fixture
async def telegram() -> TelegramStub:
    stub = TelegramStub()
    return stub


@pytest.fixture
async def http(telegram: TelegramStub):
    async with httpx.AsyncClient(transport=httpx.MockTransport(telegram.handle)) as client:
        yield client


@pytest.fixture(autouse=True)
async def clean(sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    await sessions.q(
        "DELETE FROM webhook_endpoints WHERE credential_id IN "
        "(SELECT id FROM channel_credentials WHERE channel='telegram')"
    )
    await sessions.q(
        "DELETE FROM staff_notifications WHERE target_id IN "
        "(SELECT id FROM staff_notification_targets WHERE channel='telegram')"
    )
    await sessions.q("DELETE FROM staff_notification_targets WHERE channel='telegram'")
    await sessions.q("DELETE FROM channel_credentials WHERE channel='telegram'")
    monkeypatch.setattr(crypto, "_key", lambda: b"\x07" * 32)
    monkeypatch.setattr(tc, "get_settings", lambda: SimpleNamespace(hooks_origin=ORIGIN))


def admin(w: SessionWorld) -> ConsoleActor:
    return ConsoleActor(
        user_id=UUID(w.admin_id),
        email="synthetic-admin@example.test",
        display_name="Synthetic Admin",
        platform_admin=True,
        must_change_password=False,
        session_id=uuid4(),
        client_id=None,
        role=None,
        memberships=[],
        csrf_hash="x",
    )


async def connect(
    w: SessionWorld,
    http: httpx.AsyncClient,
    token: str,
    tenant: int = 0,
    *,
    replace_webhook: bool = False,
) -> tc.TelegramStatus:
    return await tc.connect(
        w.pool,
        admin(w),
        UUID(w.tenants[tenant]),
        token,
        replace_webhook=replace_webhook,
        http_client=http,
    )


async def refused(coro: object) -> tc.TelegramConnectError:
    with pytest.raises(tc.TelegramConnectError) as caught:
        await coro  # type: ignore[misc]
    return caught.value


async def rows(w: SessionWorld, tenant: int = 0) -> list[dict]:
    return await w.q(
        "SELECT id,shop_id,access_token,meta,disconnected_at FROM channel_credentials "
        "WHERE channel='telegram' AND client_id=%s",
        w.tenants[tenant],
    )


async def test_connect_stores_an_encrypted_token_and_registers_the_webhook(
    sessions: SessionWorld, telegram: TelegramStub, http: httpx.AsyncClient
) -> None:
    w = sessions
    token = telegram.add(123456789, "synth_bot")
    status = await connect(w, http, token)
    assert status.connected and status.webhook_registered and status.hooks_ready
    assert status.bot_id == "123456789" and status.bot_username == "synth_bot"
    [row] = await rows(w)
    assert row["shop_id"] == "123456789" and row["access_token"] != token
    aad = crypto.credential_aad("channel_credentials", "access_token", str(row["id"]))
    assert crypto.decrypt(row["access_token"], aad) == token
    sealed = row["meta"]["telegram_webhook_secret_enc"]
    secret_aad = crypto.credential_aad("channel_credentials", SECRET_COLUMN, str(row["id"]))
    secret = crypto.decrypt(sealed, secret_aad)
    [endpoint] = await w.q(
        "SELECT public_id,is_enabled FROM webhook_endpoints WHERE credential_id=%s", row["id"]
    )
    assert endpoint["is_enabled"]
    [call] = telegram.calls("setWebhook")
    assert json.loads(call.content) == {
        "url": f"{ORIGIN}/hooks/{endpoint['public_id']}",
        "secret_token": secret,
        "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": False,
    }
    [audit] = await w.q(
        "SELECT action,target_id,details::text d FROM console_audit WHERE client_id=%s "
        "AND action LIKE 'channels.telegram.%%'",
        w.tenants[0],
    )
    assert audit["action"] == "channels.telegram.connected"
    assert audit["target_id"] == str(row["id"])
    assert token not in audit["d"] and secret not in audit["d"]


async def test_the_same_bot_with_a_new_token_replaces_it_and_keeps_the_endpoint(
    sessions: SessionWorld, telegram: TelegramStub, http: httpx.AsyncClient
) -> None:
    w = sessions
    old = telegram.add(123456789, "synth_bot")
    await connect(w, http, old)
    [before] = await rows(w)
    [endpoint_before] = await w.q(
        "SELECT public_id FROM webhook_endpoints WHERE credential_id=%s", before["id"]
    )
    new = telegram.add(123456789, "synth_bot", variant="N")
    status = await connect(w, http, new)
    assert status.connected
    [after] = await rows(w)
    assert after["id"] == before["id"]
    aad = crypto.credential_aad("channel_credentials", "access_token", str(after["id"]))
    assert crypto.decrypt(after["access_token"], aad) == new
    assert (
        after["meta"]["telegram_webhook_secret_enc"]
        != before["meta"]["telegram_webhook_secret_enc"]
    )
    [endpoint_after] = await w.q(
        "SELECT public_id FROM webhook_endpoints WHERE credential_id=%s", after["id"]
    )
    assert endpoint_after["public_id"] == endpoint_before["public_id"]
    actions = await w.q(
        "SELECT action FROM console_audit WHERE client_id=%s "
        "AND action LIKE 'channels.telegram.%%' ORDER BY created_at",
        w.tenants[0],
    )
    assert [a["action"] for a in actions] == [
        "channels.telegram.connected",
        "channels.telegram.token_replaced",
    ]


async def test_a_different_bot_must_be_disconnected_first(
    sessions: SessionWorld, telegram: TelegramStub, http: httpx.AsyncClient
) -> None:
    w = sessions
    await connect(w, http, telegram.add(111111111, "first_bot"))
    [before] = await rows(w)
    error = await refused(connect(w, http, telegram.add(222222222, "second_bot")))
    assert (error.status, error.code) == (409, "telegram_bot_replace")
    assert await rows(w) == [before]


async def test_a_bot_another_company_holds_is_refused_without_naming_it(
    sessions: SessionWorld, telegram: TelegramStub, http: httpx.AsyncClient
) -> None:
    w = sessions
    token = telegram.add(123456789, "synth_bot")
    await connect(w, http, token, tenant=0)
    error = await refused(connect(w, http, token, tenant=1))
    assert (error.status, error.code) == (409, "telegram_bot_in_use")
    assert w.tenants[0] not in str(error) and w.tenants[0] not in error.code
    assert await rows(w, tenant=1) == []


async def test_a_webhook_pointing_elsewhere_needs_the_admin_to_confirm(
    sessions: SessionWorld, telegram: TelegramStub, http: httpx.AsyncClient
) -> None:
    w = sessions
    token = telegram.add(123456789, "synth_bot")
    telegram.webhooks[123456789] = "https://elsewhere.example.test/x?key=private"
    error = await refused(connect(w, http, token))
    assert (error.status, error.code) == (409, "telegram_webhook_in_use")
    assert "private" not in str(error) and "/x" not in str(error)
    assert await rows(w) == []
    assert telegram.calls("setWebhook") == []
    status = await connect(w, http, token, replace_webhook=True)
    assert status.connected


@pytest.mark.parametrize(
    ("setup", "status", "code"),
    [
        ("malformed", 422, "telegram_token_malformed"),
        ("rejected", 422, "telegram_token_rejected"),
        ("outage", 502, "telegram_unreachable"),
        ("no_origin", 503, "hooks_origin_not_configured"),
    ],
)
async def test_a_connect_that_cannot_be_proven_stores_nothing(
    sessions: SessionWorld,
    telegram: TelegramStub,
    http: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    setup: str,
    status: int,
    code: str,
) -> None:
    w = sessions
    token = telegram.add(123456789, "synth_bot")
    if setup == "malformed":
        token = "not-a-token"
    elif setup == "rejected":
        telegram.getme_status = 401
    elif setup == "outage":
        telegram.getme_status = 0
    else:
        monkeypatch.setattr(tc, "get_settings", lambda: SimpleNamespace(hooks_origin=None))
    error = await refused(connect(w, http, token))
    assert (error.status, error.code) == (status, code)
    assert await rows(w) == []
    if setup in {"malformed", "no_origin"}:
        assert telegram.requests == []


async def test_a_webhook_that_fails_leaves_it_unregistered_and_a_retry_completes_it(
    sessions: SessionWorld, telegram: TelegramStub, http: httpx.AsyncClient
) -> None:
    w = sessions
    token = telegram.add(123456789, "synth_bot")
    telegram.set_webhook_failures = 1
    error = await refused(connect(w, http, token))
    assert (error.status, error.code) == (502, "telegram_webhook_failed")
    status = await tc.get_status(w.pool, UUID(w.tenants[0]))
    assert not status.connected and not status.webhook_registered
    assert (
        await w.q(
            "SELECT 1 FROM console_audit WHERE client_id=%s AND action LIKE 'channels.telegram.%%'",
            w.tenants[0],
        )
        == []
    )
    assert (await connect(w, http, token)).connected
    assert len(await rows(w)) == 1
    assert len(await w.q("SELECT id FROM webhook_endpoints WHERE client_id=%s", w.tenants[0])) == 1


async def test_two_companies_connecting_one_bot_at_once_leave_one_winner(
    sessions: SessionWorld, telegram: TelegramStub, http: httpx.AsyncClient
) -> None:
    w = sessions
    token = telegram.add(123456789, "synth_bot")
    results = await asyncio.gather(
        connect(w, http, token, tenant=0),
        connect(w, http, token, tenant=1),
        return_exceptions=True,
    )
    wins = [r for r in results if isinstance(r, tc.TelegramStatus)]
    losses = [r for r in results if isinstance(r, tc.TelegramConnectError)]
    assert len(wins) == 1 and len(losses) == 1
    assert losses[0].code == "telegram_bot_in_use"


async def test_no_log_audit_or_error_carries_the_token_or_the_secret(
    sessions: SessionWorld,
    telegram: TelegramStub,
    http: httpx.AsyncClient,
    capsys: pytest.CaptureFixture[str],
) -> None:
    w = sessions
    token = telegram.add(123456789, "synth_bot")
    with structlog.testing.capture_logs() as logs:
        await connect(w, http, token)
        telegram.getme_status = 401
        error = await refused(connect(w, http, token, tenant=1))
    [call] = telegram.calls("setWebhook")
    secret = json.loads(call.content)["secret_token"]
    audits = await w.q(
        "SELECT details::text d, target_id FROM console_audit WHERE client_id=%s", w.tenants[0]
    )
    seen = [json.dumps(logs, default=str), capsys.readouterr().out, str(error), repr(error)]
    seen += [f"{a['d']} {a['target_id']}" for a in audits]
    for text in seen:
        assert token not in text and secret not in text


CHAT = "-1001234567890"
UPDATE = json.dumps(
    {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "date": 1700000000,
            "chat": {"id": 55, "type": "private"},
            "from": {"id": 55, "first_name": "Aina"},
            "text": "hello",
        },
    }
).encode()


async def targets(w: SessionWorld, tenant: int = 0) -> list[dict]:
    return await w.q(
        "SELECT id,address,is_enabled,verified_at,events FROM staff_notification_targets "
        "WHERE channel='telegram' AND client_id=%s ORDER BY created_at",
        w.tenants[tenant],
    )


async def set_chat(
    w: SessionWorld, http: httpx.AsyncClient, chat_id: str, tenant: int = 0
) -> tc.TelegramStatus:
    return await tc.set_staff_chat(
        w.pool, admin(w), UUID(w.tenants[tenant]), chat_id, http_client=http
    )


async def test_the_staff_chat_is_verified_only_by_a_delivered_test_message(
    sessions: SessionWorld, telegram: TelegramStub, http: httpx.AsyncClient
) -> None:
    w = sessions
    token = telegram.add(123456789, "synth_bot")
    await connect(w, http, token)
    status = await set_chat(w, http, CHAT)
    assert status.staff_chat == tc.TelegramStaffChat(address=CHAT, verified=True)
    [sent] = telegram.sent
    assert sent["token"] == token and sent["chat_id"] == CHAT and sent["text"]
    [target] = await targets(w)
    assert target["address"] == CHAT and target["verified_at"] and target["is_enabled"]
    assert "escalation" in target["events"]
    [audit] = await w.q(
        "SELECT target_id FROM console_audit WHERE client_id=%s AND action=%s",
        w.tenants[0],
        "channels.telegram.staff_chat_set",
    )
    assert audit["target_id"] == str((await rows(w))[0]["id"])


@pytest.mark.parametrize(
    ("send_status", "code"),
    [
        (400, "telegram_chat_unreachable"),
        (403, "telegram_chat_forbidden"),
        (502, "telegram_unreachable"),
    ],
)
async def test_a_chat_the_bot_cannot_reach_is_refused_and_keeps_the_old_one(
    sessions: SessionWorld,
    telegram: TelegramStub,
    http: httpx.AsyncClient,
    send_status: int,
    code: str,
) -> None:
    w = sessions
    await connect(w, http, telegram.add(123456789, "synth_bot"))
    await set_chat(w, http, CHAT)
    telegram.send_status = send_status
    error = await refused(set_chat(w, http, "-100999"))
    assert error.code == code and error.status in {422, 502}
    [only] = await targets(w)
    assert only["address"] == CHAT and only["is_enabled"]


@pytest.mark.parametrize("bad", ["abc", "0", "-", "12 34", "1" * 30, ""])
async def test_a_malformed_chat_id_is_refused_before_anything_is_sent(
    sessions: SessionWorld, telegram: TelegramStub, http: httpx.AsyncClient, bad: str
) -> None:
    w = sessions
    await connect(w, http, telegram.add(123456789, "synth_bot"))
    error = await refused(set_chat(w, http, bad))
    assert (error.status, error.code) == (422, "telegram_chat_invalid")
    assert telegram.sent == []


async def test_a_new_staff_chat_replaces_the_old_without_losing_its_history(
    sessions: SessionWorld, telegram: TelegramStub, http: httpx.AsyncClient
) -> None:
    w = sessions
    await connect(w, http, telegram.add(123456789, "synth_bot"))
    await set_chat(w, http, CHAT)
    [old] = await targets(w)
    await w.q(
        "INSERT INTO staff_notifications (client_id,target_id,event,payload,idempotency_key)"
        " VALUES (%s,%s,'escalation','{}'::jsonb,%s)",
        w.tenants[0],
        old["id"],
        f"k-{uuid4()}",
    )
    status = await set_chat(w, http, "424242")
    assert status.staff_chat is not None and status.staff_chat.address == "424242"
    by_address = {t["address"]: t for t in await targets(w)}
    assert by_address[CHAT]["is_enabled"] is False and by_address["424242"]["is_enabled"] is True
    await set_chat(w, http, CHAT)
    assert {t["address"]: t["is_enabled"] for t in await targets(w)} == {
        CHAT: True,
        "424242": False,
    }


async def test_a_staff_chat_needs_a_connected_bot(
    sessions: SessionWorld, telegram: TelegramStub, http: httpx.AsyncClient
) -> None:
    error = await refused(set_chat(sessions, http, CHAT))
    assert (error.status, error.code) == (409, "telegram_not_connected")
    assert telegram.requests == []


async def test_check_reports_the_live_webhook(
    sessions: SessionWorld, telegram: TelegramStub, http: httpx.AsyncClient
) -> None:
    w, client = sessions, UUID(sessions.tenants[0])
    error = await refused(tc.check(w.pool, client, http_client=http))
    assert error.code == "telegram_not_connected"
    await connect(w, http, telegram.add(123456789, "synth_bot"))
    telegram.pending, telegram.webhook_error = 4, "Connection timed out"
    got = await tc.check(w.pool, client, http_client=http)
    assert got == tc.TelegramCheck(
        webhook_url_matches=True, pending_update_count=4, last_error="Connection timed out"
    )
    telegram.webhooks[123456789] = "https://elsewhere.example.test/x"
    assert (await tc.check(w.pool, client, http_client=http)).webhook_url_matches is False


async def test_disconnect_frees_everything_and_is_safe_to_repeat(
    sessions: SessionWorld, telegram: TelegramStub, http: httpx.AsyncClient
) -> None:
    w, client = sessions, UUID(sessions.tenants[0])
    await connect(w, http, telegram.add(123456789, "synth_bot"))
    await set_chat(w, http, CHAT)
    [before] = await rows(w)
    telegram.delete_fails = True  # the webhook call may fail; the disconnect still completes
    await tc.disconnect(w.pool, admin(w), client, http_client=http)
    [after] = await rows(w)
    assert after["access_token"] is None and after["disconnected_at"] is not None
    # The baseline's UNIQUE (client_id, channel, shop_id) would otherwise block a reconnect.
    assert after["shop_id"] is None
    assert "telegram_webhook_secret_enc" not in after["meta"]
    [endpoint] = await w.q(
        "SELECT is_enabled,disabled_reason FROM webhook_endpoints WHERE credential_id=%s",
        before["id"],
    )
    assert endpoint == {"is_enabled": False, "disabled_reason": "disconnected"}
    assert [t["is_enabled"] for t in await targets(w)] == [False]
    status = await tc.get_status(w.pool, client)
    assert not status.connected and status.bot_id is None and status.staff_chat is None
    await tc.disconnect(w.pool, admin(w), client, http_client=http)
    audits = await w.q(
        "SELECT action FROM console_audit WHERE client_id=%s AND action=%s",
        w.tenants[0],
        "channels.telegram.disconnected",
    )
    assert len(audits) == 1


async def test_after_a_disconnect_the_bot_can_move_and_the_old_address_stays_dead(
    sessions: SessionWorld, telegram: TelegramStub, http: httpx.AsyncClient
) -> None:
    w = sessions
    token = telegram.add(123456789, "synth_bot")
    await connect(w, http, token)
    [old] = await rows(w)
    [old_endpoint] = await w.q(
        "SELECT public_id FROM webhook_endpoints WHERE credential_id=%s", old["id"]
    )
    await tc.disconnect(w.pool, admin(w), UUID(w.tenants[0]), http_client=http)
    # Another company takes the bot, and the first company can connect again too.
    assert (await connect(w, http, token, tenant=1)).connected
    await tc.disconnect(w.pool, admin(w), UUID(w.tenants[1]), http_client=http)
    assert (await connect(w, http, token, tenant=0)).connected
    fresh = [r for r in await rows(w) if r["disconnected_at"] is None]
    assert len(fresh) == 1 and fresh[0]["id"] != old["id"]
    [new_endpoint] = await w.q(
        "SELECT public_id FROM webhook_endpoints WHERE credential_id=%s", fresh[0]["id"]
    )
    assert new_endpoint["public_id"] != old_endpoint["public_id"]
    with pytest.raises(IngressError) as caught:
        await TelegramIngress(w.pool).receive(old_endpoint["public_id"], "anything", UPDATE)
    assert caught.value.status_code == 404
    assert await w.q("SELECT 1 FROM ingress_events WHERE client_id=%s", w.tenants[0]) == []


async def test_the_notifier_picks_only_a_connected_bot(
    sessions: SessionWorld, telegram: TelegramStub, http: httpx.AsyncClient
) -> None:
    w, client = sessions, UUID(sessions.tenants[0])

    async def credential() -> dict | None:
        async with tx(w.pool, str(client)) as conn:
            return await db.q.fetch_one("notify/get_telegram_credential.sql", (client,), conn=conn)

    await connect(w, http, telegram.add(123456789, "synth_bot"))
    assert await credential() is not None
    await tc.disconnect(w.pool, admin(w), client, http_client=http)
    assert await credential() is None
