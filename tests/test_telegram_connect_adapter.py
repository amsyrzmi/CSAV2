"""getMe, setWebhook and getWebhookInfo on the Telegram adapter; failures never carry the token."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
import structlog
from csa.channels.telegram import (
    TelegramAdapter,
    TelegramBotProfile,
    TelegramPollingError,
    TelegramWebhookInfo,
)

pytestmark = pytest.mark.asyncio

TOKEN = "123456789:" + "synthetic_Token-0123456789_abcdefghij"
Handler = Callable[[httpx.Request], httpx.Response]


def client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def ok(result: object) -> Handler:
    return lambda request: httpx.Response(200, json={"ok": True, "result": result})


async def test_get_me_returns_the_bot_id_and_username() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"ok": True, "result": {"id": 123456789, "is_bot": True, "username": "synth_bot"}},
        )

    async with client(handler) as http:
        profile = await TelegramAdapter.get_me(TOKEN, http_client=http)
    assert profile == TelegramBotProfile(bot_id=123456789, username="synth_bot")
    assert seen[0].url.path == f"/bot{TOKEN}/getMe"


async def test_get_me_refuses_an_account_that_is_not_a_bot() -> None:
    body = {"id": 5, "is_bot": False, "username": "person"}
    async with client(ok(body)) as http:
        with pytest.raises(TelegramPollingError) as caught:
            await TelegramAdapter.get_me(TOKEN, http_client=http)
    assert caught.value.error_code == "telegram_api_rejected"


async def test_set_webhook_sends_the_url_secret_and_the_two_update_types() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "result": True})

    async with client(handler) as http:
        await TelegramAdapter.set_webhook(
            TOKEN,
            url="https://hooks.example.test/hooks/abc",
            secret_token="s3cret_x",
            http_client=http,
        )
    assert seen[0].url.path == f"/bot{TOKEN}/setWebhook"
    assert json.loads(seen[0].content) == {
        "url": "https://hooks.example.test/hooks/abc",
        "secret_token": "s3cret_x",
        "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": False,
    }


async def test_set_webhook_not_accepted_is_a_rejection() -> None:
    handler: Handler = lambda request: httpx.Response(200, json={"ok": False})  # noqa: E731
    async with client(handler) as http:
        with pytest.raises(TelegramPollingError) as caught:
            await TelegramAdapter.set_webhook(
                TOKEN, url="https://h.example.test/x", secret_token="s", http_client=http
            )
    assert caught.value.error_code == "telegram_api_rejected"


async def test_get_webhook_info_reads_url_pending_and_last_error() -> None:
    info = {
        "url": "https://h.example.test/hooks/x",
        "pending_update_count": 3,
        "last_error_message": "boom",
    }
    async with client(ok(info)) as http:
        got = await TelegramAdapter.get_webhook_info(TOKEN, http_client=http)
    assert got == TelegramWebhookInfo(
        url="https://h.example.test/hooks/x", pending_update_count=3, last_error_message="boom"
    )
    async with client(ok({"url": "", "pending_update_count": 0})) as http:
        empty = await TelegramAdapter.get_webhook_info(TOKEN, http_client=http)
    assert empty.url == "" and empty.last_error_message is None


FAILURES: list[tuple[str, Handler, str]] = [
    ("rejected", lambda r: httpx.Response(401, json={"ok": False}), "telegram_http_401"),
    ("server", lambda r: httpx.Response(502, json={"ok": False}), "telegram_http_502"),
    ("not json", lambda r: httpx.Response(200, text="<html>"), "telegram_invalid_response"),
    (
        "shape",
        lambda r: httpx.Response(200, json={"ok": True, "result": "x"}),
        "telegram_invalid_response",
    ),
]


def drop(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError(f"cannot reach {request.url}")


@pytest.mark.parametrize("call", ["get_me", "set_webhook", "get_webhook_info"])
@pytest.mark.parametrize(
    ("name", "handler", "code"),
    [*FAILURES, ("transport", drop, "telegram_transport_error")],
    ids=[name for name, _, _ in FAILURES] + ["transport"],
)
async def test_failures_carry_a_code_and_never_the_token(
    call: str, name: str, handler: Handler, code: str
) -> None:
    async with client(handler) as http:
        with structlog.testing.capture_logs() as logs:
            with pytest.raises(TelegramPollingError) as caught:
                if call == "set_webhook":
                    await TelegramAdapter.set_webhook(
                        TOKEN, url="https://h.example.test/x", secret_token="s", http_client=http
                    )
                else:
                    await getattr(TelegramAdapter, call)(TOKEN, http_client=http)
    exc = caught.value
    assert exc.error_code == code, name
    seen = [
        str(exc),
        repr(exc),
        json.dumps(logs, default=str),
    ]
    # The original error carried the URL; it must not be reachable from this one.
    assert exc.__cause__ is None
    assert exc.__context__ is None or exc.__suppress_context__
    for text in seen:
        assert TOKEN not in text and "api.telegram.org/bot" not in text
