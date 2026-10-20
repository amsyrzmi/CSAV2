"""Synthetic token transport tests; no live networking or credentials."""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, quote_plus

import httpx
import pytest
from csa.agents.ecommerce import token_exchange
from csa.agents.ecommerce.authorization import TikTokAuthorizationError
from csa.agents.ecommerce.token_exchange import exchange_tiktok_authorization_code
from pydantic import SecretStr

NOW = datetime(2026, 10, 18, tzinfo=UTC)
APP_KEY = "synthetic-app-key"
APP_SECRET = "synthetic-app-secret+&"
AUTHORIZATION_CODE = "synthetic-auth-code/+&"
ACCESS_TOKEN = "synthetic-access-token"
REFRESH_TOKEN = "synthetic-refresh-token"


class _BytesStream(httpx.AsyncByteStream):
    def __init__(self, content: bytes) -> None:
        self.content = content

    async def __aiter__(self):
        yield self.content


def response_payload():
    return {
        "code": 0,
        "message": "synthetic-private-provider-message",
        "request_id": "synthetic-private-request-id",
        "data": {
            "access_token": ACCESS_TOKEN,
            "refresh_token": REFRESH_TOKEN,
            "access_token_expire_in": int((NOW + timedelta(hours=1)).timestamp()),
            "refresh_token_expire_in": int((NOW + timedelta(days=30)).timestamp()),
            "user_type": 0,
            "granted_scopes": ["seller.order.info", "seller.authorization.info"],
        },
    }


def streamed_response(status_code, *, content=b"", headers=None):
    return httpx.Response(status_code, stream=_BytesStream(content), headers=headers)


def secret_arguments():
    return {
        "app_key": SecretStr(APP_KEY),
        "app_secret": SecretStr(APP_SECRET),
        "authorization_code": SecretStr(AUTHORIZATION_CODE),
        "now": NOW,
    }


def install_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(token_exchange, "_new_transport", lambda: transport)
    return transport


def assert_secrets_absent(text):
    for secret in (
        APP_KEY,
        APP_SECRET,
        AUTHORIZATION_CODE,
        ACCESS_TOKEN,
        REFRESH_TOKEN,
        quote(APP_SECRET, safe=""),
        quote_plus(APP_SECRET),
        quote(AUTHORIZATION_CODE, safe=""),
        quote_plus(AUTHORIZATION_CODE),
    ):
        assert secret not in text


@pytest.mark.asyncio
async def test_exchange_uses_documented_query_once_and_returns_selected_response(
    monkeypatch, caplog
):
    requests = []

    def handler(request):
        requests.append(request)
        return streamed_response(
            200,
            content=json.dumps(response_payload()).encode(),
            headers={"content-type": "application/json"},
        )

    install_transport(monkeypatch, handler)
    caplog.set_level(logging.DEBUG)
    result = await exchange_tiktok_authorization_code(**secret_arguments())

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url == httpx.URL(
        token_exchange.TOKEN_ENDPOINT,
        params=[
            ("app_key", APP_KEY),
            ("app_secret", APP_SECRET),
            ("auth_code", AUTHORIZATION_CODE),
            ("grant_type", "authorized_code"),
        ],
    )
    assert request.headers["accept-encoding"] == "identity"
    assert not request.extensions
    assert result.access_token.get_secret_value() == ACCESS_TOKEN
    assert result.refresh_token.get_secret_value() == REFRESH_TOKEN
    assert_secrets_absent(caplog.text)


@pytest.mark.parametrize(
    ("handler", "expected_code"),
    [
        (
            lambda request: streamed_response(
                200,
                content=json.dumps(
                    {
                        "code": 41,
                        "message": "synthetic-private-provider-rejection",
                        "request_id": "synthetic-private-request-id",
                    }
                ).encode(),
            ),
            "token_exchange_rejected",
        ),
        (
            lambda request: streamed_response(
                200, content=b'{"code":0,"message":"synthetic-private-malformed'
            ),
            "token_exchange_invalid_response",
        ),
        (
            lambda request: streamed_response(503, content=b"synthetic-private-upstream-error"),
            "token_exchange_http_rejected",
        ),
        (
            lambda request: streamed_response(
                302,
                headers={"location": "https://example.invalid/?secret=synthetic"},
            ),
            "token_exchange_http_rejected",
        ),
    ],
)
@pytest.mark.asyncio
async def test_rejection_malformed_http_and_redirect_errors_are_safe(
    monkeypatch, caplog, handler, expected_code
):
    requests = []

    def counted(request):
        requests.append(request)
        return handler(request)

    install_transport(monkeypatch, counted)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(TikTokAuthorizationError, match=expected_code) as caught:
        await exchange_tiktok_authorization_code(**secret_arguments())

    assert len(requests) == 1
    assert_secrets_absent(str(caught.value) + caplog.text)
    assert "synthetic-private" not in str(caught.value) + caplog.text


@pytest.mark.asyncio
async def test_network_exception_url_and_cause_do_not_escape(monkeypatch, caplog):
    def fail(request):
        raise httpx.ConnectError(
            f"synthetic-private-network-error {request.url}",
            request=request,
        )

    install_transport(monkeypatch, fail)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(TikTokAuthorizationError, match="token_exchange_transport_failed") as caught:
        await exchange_tiktok_authorization_code(**secret_arguments())

    assert_secrets_absent(str(caught.value) + caplog.text)
    assert "synthetic-private-network-error" not in str(caught.value) + caplog.text
    assert caught.value.__suppress_context__


@pytest.mark.asyncio
async def test_response_size_is_bounded(monkeypatch):
    install_transport(
        monkeypatch,
        lambda request: streamed_response(
            200,
            content=b"x" * (token_exchange.MAX_RESPONSE_BYTES + 1),
        ),
    )
    with pytest.raises(TikTokAuthorizationError, match="token_exchange_response_too_large"):
        await exchange_tiktok_authorization_code(**secret_arguments())


@pytest.mark.asyncio
async def test_timeout_is_safe_and_single_attempt(monkeypatch):
    calls = 0
    monkeypatch.setattr(token_exchange, "EXCHANGE_TIMEOUT_SECONDS", 0.01)

    async def slow(request):
        nonlocal calls
        calls += 1
        await asyncio.Event().wait()

    install_transport(monkeypatch, slow)
    with pytest.raises(TikTokAuthorizationError, match="token_exchange_timeout"):
        await exchange_tiktok_authorization_code(**secret_arguments())
    assert calls == 1


@pytest.mark.asyncio
async def test_caller_cancellation_propagates_and_does_not_retry(monkeypatch):
    calls = 0

    async def waiting(request):
        nonlocal calls
        calls += 1
        await asyncio.Event().wait()

    install_transport(monkeypatch, waiting)
    task = asyncio.create_task(exchange_tiktok_authorization_code(**secret_arguments()))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("app_key", ""),
        ("app_secret", ""),
        ("authorization_code", ""),
    ],
)
@pytest.mark.asyncio
async def test_empty_input_rejected_before_transport(monkeypatch, field, value):
    def forbidden():
        pytest.fail("empty credentials must not create or send a transport request")

    monkeypatch.setattr(token_exchange, "_new_transport", forbidden)
    arguments = secret_arguments()
    arguments[field] = SecretStr(value)
    with pytest.raises(TikTokAuthorizationError, match="missing_authorization_input"):
        await exchange_tiktok_authorization_code(**arguments)
