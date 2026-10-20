"""Synthetic token/app/shop verifier composition; no live API or credentials."""

import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from csa.agents.ecommerce import authorization_verifier
from csa.agents.ecommerce.authorization import (
    TikTokAuthorizationContext,
    TikTokAuthorizationError,
)
from csa.agents.ecommerce.models import TikTokShopCredentials
from csa.agents.ecommerce.resolver import TikTokAppMaterial
from csa.agents.ecommerce.stores import StoreBinding
from csa.agents.ecommerce.token_response import TikTokTokenResponse
from pydantic import SecretStr

NOW = datetime(2026, 10, 18, tzinfo=UTC)
APP_KEY = "synthetic-app-key"
APP_SECRET = "synthetic-app-secret"
AUTHORIZATION_CODE = "synthetic-one-use-code"
ACCESS_TOKEN = "synthetic-access-token"
REFRESH_TOKEN = "synthetic-refresh-token"
SHOP_CIPHER = "synthetic-exact-shop-cipher"


class _BytesStream(httpx.AsyncByteStream):
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def __aiter__(self):
        yield self.body


def _response(status_code: int, body: object, headers: dict[str, str] | None = None):
    return httpx.Response(
        status_code,
        stream=_BytesStream(json.dumps(body).encode()),
        headers=headers,
    )


def _fixtures():
    context = TikTokAuthorizationContext(
        binding=StoreBinding(
            client_id=uuid4(),
            record_id=uuid4(),
            platform="tiktok_shop",
            shop_id="SYNTH-INTENDED-SHOP",
        ),
        app_id="SYNTH-APP-ID",
        app_revision=4,
        credential_generation=9,
    )
    app = TikTokAppMaterial(
        app_id=context.app_id,
        revision=context.app_revision,
        app_key=SecretStr(APP_KEY),
        app_secret=SecretStr(APP_SECRET),
        enabled=True,
    )
    token = TikTokTokenResponse(
        access_token=SecretStr(ACCESS_TOKEN),
        refresh_token=SecretStr(REFRESH_TOKEN),
        access_expires_at=NOW + timedelta(hours=1),
        refresh_expires_at=NOW + timedelta(days=30),
        scopes=frozenset({"seller.order.info", "seller.authorization.info", "other.scope"}),
    )
    return context, app, token


def _install_exchange(monkeypatch, *, token=None, calls=None):
    async def exchange(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        assert kwargs["app_key"] == SecretStr(APP_KEY)
        assert kwargs["app_secret"] == SecretStr(APP_SECRET)
        assert kwargs["authorization_code"] == SecretStr(AUTHORIZATION_CODE)
        assert kwargs["now"] == NOW
        return token

    monkeypatch.setattr(authorization_verifier, "exchange_tiktok_authorization_code", exchange)


def _install_shop_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(authorization_verifier, "_new_shop_transport", lambda: transport)
    return transport


@pytest.mark.asyncio
async def test_verifier_pins_app_and_exact_shop_and_returns_trusted_result(monkeypatch, caplog):
    context, app, token = _fixtures()
    exchange_calls = []
    requests = []
    _install_exchange(monkeypatch, token=token, calls=exchange_calls)

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/authorization/202309/shops"
        assert request.headers["x-tts-access-token"] == ACCESS_TOKEN
        assert "shop_cipher" not in request.url.params
        assert ACCESS_TOKEN not in str(request.url)
        assert APP_SECRET not in str(request.url)
        params = dict(request.url.params)
        assert set(params) == {"app_key", "timestamp", "sign"}
        credentials = TikTokShopCredentials(
            app_key=APP_KEY,
            app_secret=APP_SECRET,
            access_token=ACCESS_TOKEN,
            shop_id=context.binding.shop_id,
        )
        signer = authorization_verifier.TikTokShopAdapter(
            credentials,
            http_transport=httpx.MockTransport(lambda _: _response(200, {})),
        )
        assert params["sign"] == signer.sign_request(request.url.path, params, "")
        return _response(
            200,
            {
                "code": 0,
                "data": {
                    "shops": [
                        {"id": "SYNTH-OTHER-SHOP", "cipher": "synthetic-other-cipher"},
                        {
                            "id": context.binding.shop_id,
                            "cipher": SHOP_CIPHER,
                            "name": "discarded-synthetic-name",
                        },
                    ]
                },
            },
        )

    _install_shop_transport(monkeypatch, handler)
    caplog.set_level(logging.DEBUG)

    async def load_app(app_id):
        assert app_id == context.app_id
        return app

    verifier = authorization_verifier.TikTokAuthorizationVerifier(
        load_app=load_app, clock=lambda: NOW
    )
    result = await verifier(context, SecretStr(AUTHORIZATION_CODE))

    assert len(exchange_calls) == 1
    assert len(requests) == 1
    assert result.context == context
    assert result.access_token == token.access_token
    assert result.refresh_token == token.refresh_token
    assert result.shop_cipher == SecretStr(SHOP_CIPHER)
    assert result.access_expires_at == token.access_expires_at
    assert result.grant_expires_at == token.access_expires_at
    assert result.verified_at == NOW
    assert result.scopes == token.scopes
    assert "discarded-synthetic-name" not in result.model_dump_json()
    for secret in (APP_SECRET, AUTHORIZATION_CODE, ACCESS_TOKEN, REFRESH_TOKEN, SHOP_CIPHER):
        assert secret not in caplog.text


@pytest.mark.parametrize("failure", ["missing", "disabled", "wrong_id", "wrong_revision"])
@pytest.mark.asyncio
async def test_app_mismatch_fails_before_exchange_or_shop_request(monkeypatch, failure):
    context, app, _ = _fixtures()
    calls = []

    async def forbidden_exchange(**_):
        calls.append("exchange")
        pytest.fail("app mismatch must fail before code exchange")

    monkeypatch.setattr(
        authorization_verifier, "exchange_tiktok_authorization_code", forbidden_exchange
    )

    async def load_app(app_id):
        assert app_id == context.app_id
        if failure == "missing":
            return None
        if failure == "disabled":
            return app.model_copy(update={"enabled": False})
        if failure == "wrong_id":
            return app.model_copy(update={"app_id": "SYNTH-OTHER-APP"})
        return app.model_copy(update={"revision": app.revision + 1})

    def forbidden_transport():
        pytest.fail("app mismatch must fail before authorized-shop lookup")

    monkeypatch.setattr(authorization_verifier, "_new_shop_transport", forbidden_transport)
    verifier = authorization_verifier.TikTokAuthorizationVerifier(
        load_app=load_app, clock=lambda: NOW
    )
    with pytest.raises(TikTokAuthorizationError, match="app_binding_unavailable"):
        await verifier(context, SecretStr(AUTHORIZATION_CODE))
    assert calls == []


@pytest.mark.parametrize(
    ("body", "status", "expected"),
    [
        (
            {"code": 0, "data": {"shops": [{"id": "SYNTH-OTHER", "cipher": "other"}]}},
            200,
            "authorized_shop_verification_failed",
        ),
        (
            {
                "code": 0,
                "data": {
                    "shops": [
                        {"id": "SYNTH-INTENDED-SHOP", "cipher": "one"},
                        {"id": "SYNTH-INTENDED-SHOP", "cipher": "two"},
                    ]
                },
            },
            200,
            "authorized_shop_verification_failed",
        ),
        (
            {"code": 41, "message": "synthetic-private-provider-error"},
            200,
            "authorized_shop_verification_failed",
        ),
        ({"private": "synthetic-private-http-body"}, 403, "authorized_shop_verification_failed"),
        (
            {"private": "synthetic-private-redirect-body"},
            302,
            "authorized_shop_verification_failed",
        ),
    ],
)
@pytest.mark.asyncio
async def test_invalid_shop_evidence_fails_safely_without_provider_data(
    monkeypatch, caplog, body, status, expected
):
    context, app, token = _fixtures()
    calls = []
    _install_exchange(monkeypatch, token=token, calls=calls)
    requests = []

    def handler(request):
        requests.append(request)
        return _response(
            status,
            body,
            {"location": "https://example.invalid/private"} if status == 302 else None,
        )

    _install_shop_transport(monkeypatch, handler)
    caplog.set_level(logging.DEBUG)

    async def load_app(_):
        return app

    verifier = authorization_verifier.TikTokAuthorizationVerifier(
        load_app=load_app, clock=lambda: NOW
    )
    with pytest.raises(TikTokAuthorizationError, match=expected) as caught:
        await verifier(context, SecretStr(AUTHORIZATION_CODE))

    assert len(calls) == 1
    assert len(requests) == 1
    assert "synthetic-private" not in str(caught.value) + caplog.text
    assert AUTHORIZATION_CODE not in str(caught.value) + caplog.text
    assert ACCESS_TOKEN not in str(caught.value) + caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry_kind", ["access", "refresh"])
@pytest.mark.asyncio
async def test_token_expiry_during_shop_check_rejects_result(monkeypatch, expiry_kind):
    context, app, token = _fixtures()
    _install_exchange(monkeypatch, token=token)
    _install_shop_transport(
        monkeypatch,
        lambda _: _response(
            200,
            {
                "code": 0,
                "data": {"shops": [{"id": context.binding.shop_id, "cipher": SHOP_CIPHER}]},
            },
        ),
    )
    expiry = token.access_expires_at if expiry_kind == "access" else token.refresh_expires_at
    clock_values = iter([NOW, expiry])

    async def load_app(_):
        return app

    verifier = authorization_verifier.TikTokAuthorizationVerifier(
        load_app=load_app, clock=lambda: next(clock_values)
    )
    with pytest.raises(TikTokAuthorizationError, match="expired_exchange_token"):
        await verifier(context, SecretStr(AUTHORIZATION_CODE))


@pytest.mark.asyncio
async def test_shop_transport_url_exception_is_suppressed(monkeypatch, caplog):
    context, app, token = _fixtures()
    _install_exchange(monkeypatch, token=token)

    def fail(request):
        raise httpx.ConnectError(
            f"synthetic-private-network-error {request.url}",
            request=request,
        )

    _install_shop_transport(monkeypatch, fail)
    caplog.set_level(logging.DEBUG)

    async def load_app(_):
        return app

    verifier = authorization_verifier.TikTokAuthorizationVerifier(
        load_app=load_app, clock=lambda: NOW
    )
    with pytest.raises(
        TikTokAuthorizationError, match="authorized_shop_verification_failed"
    ) as caught:
        await verifier(context, SecretStr(AUTHORIZATION_CODE))

    assert "synthetic-private-network-error" not in str(caught.value) + caplog.text
    assert APP_SECRET not in str(caught.value) + caplog.text
    assert ACCESS_TOKEN not in str(caught.value) + caplog.text
    assert caught.value.__suppress_context__
