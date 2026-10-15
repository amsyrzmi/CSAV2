"""Tests for marketplace ingress and OAuth endpoints (TikTok Shop, Shopee, Lazada)."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from csa.channels.ingress import EndpointRateLimiter, IngressError
from csa.channels.marketplace.ingress import TikTokWebhookIngress, verify_tiktok_signature
from csa.channels.marketplace.oauth import (
    OAuthError,
    build_tiktok_shop_auth_url,
    create_oauth_state,
    exchange_tiktok_shop_token,
    save_tiktok_shop_credentials,
    verify_oauth_state,
)
from csa.channels.marketplace.router import build_marketplace_router
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ==============================================================================
# OAuth State Tests
# ==============================================================================


def test_oauth_state_roundtrip() -> None:
    client_id = uuid4()
    secret = "test-secret-key-32-chars-long-abcde"
    state = create_oauth_state(client_id, secret, expires_in_secs=300)

    recovered = verify_oauth_state(state, secret)
    assert recovered == client_id


def test_oauth_state_invalid_signature() -> None:
    client_id = uuid4()
    secret = "test-secret-key-32-chars-long-abcde"
    state = create_oauth_state(client_id, secret)

    parts = state.split(".", 1)
    tampered = f"{parts[0]}.badsignature123"
    with pytest.raises(OAuthError) as exc_info:
        verify_oauth_state(tampered, secret)
    assert exc_info.value.status_code == 400
    assert "Invalid OAuth state signature" in exc_info.value.detail


def test_oauth_state_expired() -> None:
    client_id = uuid4()
    secret = "test-secret-key-32-chars-long-abcde"
    # expired 10 seconds ago
    state = create_oauth_state(client_id, secret, expires_in_secs=-10)

    with pytest.raises(OAuthError) as exc_info:
        verify_oauth_state(state, secret)
    assert exc_info.value.status_code == 400
    assert "expired" in exc_info.value.detail


def test_build_tiktok_shop_auth_url() -> None:
    url = build_tiktok_shop_auth_url(
        service_id="test_service_key",
        state="xyz123",
        redirect_uri="https://csa-dev.nexusmandiri.com/callback",
    )
    assert "https://services.tiktokshop.com/open/authorize?" in url
    assert "service_id=test_service_key" in url
    assert "state=xyz123" in url
    assert "redirect_uri=" in url


# ==============================================================================
# Token Exchange Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_exchange_tiktok_shop_token_success() -> None:
    mock_response_data = {
        "code": 0,
        "message": "success",
        "data": {
            "access_token": "mock_tts_access_token_123",
            "access_token_expire_in": 604800,
            "refresh_token": "mock_tts_refresh_token_456",
            "refresh_token_expire_in": 2592000,
            "open_id": "shop_seller_789",
            "seller_name": "Acme Store Malaysia",
        },
    }

    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_response_data
    mock_client.get = AsyncMock(return_value=mock_resp)

    token_data = await exchange_tiktok_shop_token(
        app_key="test_app_key",
        app_secret="test_app_secret",
        auth_code="code_from_tiktok",
        http_client=mock_client,
    )

    assert token_data["access_token"] == "mock_tts_access_token_123"
    assert token_data["open_id"] == "shop_seller_789"
    assert token_data["seller_name"] == "Acme Store Malaysia"


@pytest.mark.asyncio
async def test_exchange_tiktok_shop_token_rejected_by_api() -> None:
    mock_response_data = {
        "code": 105001,
        "message": "Authorization code expired",
    }

    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_response_data
    mock_client.get = AsyncMock(return_value=mock_resp)

    with pytest.raises(OAuthError) as exc_info:
        await exchange_tiktok_shop_token(
            app_key="test_app_key",
            app_secret="test_app_secret",
            auth_code="expired_code",
            http_client=mock_client,
        )
    assert exc_info.value.status_code == 400
    assert "Authorization code expired" in exc_info.value.detail


# ==============================================================================
# Webhook Signature Verification Tests
# ==============================================================================


def test_verify_tiktok_signature() -> None:
    secret = "my_tts_app_secret_123"
    body = b'{"shop_id":"123","type":1,"timestamp":1700000000}'
    valid_sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    assert verify_tiktok_signature(body, valid_sig, secret) is True
    assert verify_tiktok_signature(body, f"sha256={valid_sig}", secret) is True
    assert verify_tiktok_signature(body, f"Bearer {valid_sig}", secret) is True
    assert verify_tiktok_signature(body, "wrong_sig", secret) is False
    assert verify_tiktok_signature(b'{"different":"body"}', valid_sig, secret) is False


# ==============================================================================
# Ingress Service Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_webhook_ingress_missing_signature() -> None:
    pool = MagicMock()
    ingress = TikTokWebhookIngress(pool, app_secret="secret")
    with pytest.raises(IngressError) as exc_info:
        await ingress.receive(
            raw_body=b'{"shop_id":"123"}',
            signature=None,
        )
    assert exc_info.value.status_code == 401
    assert "Missing webhook signature" in exc_info.value.detail


@pytest.mark.asyncio
async def test_webhook_ingress_invalid_signature() -> None:
    pool = MagicMock()
    ingress = TikTokWebhookIngress(pool, app_secret="secret")
    with pytest.raises(IngressError) as exc_info:
        await ingress.receive(
            raw_body=b'{"shop_id":"123"}',
            signature="bad_signature",
        )
    assert exc_info.value.status_code == 401
    assert "Invalid webhook signature" in exc_info.value.detail


@pytest.mark.asyncio
async def test_save_tiktok_shop_credentials_persists_encrypted() -> None:
    client_id = uuid4()
    pool = MagicMock()
    token_data = {
        "access_token": "raw_access_token_xyz",
        "refresh_token": "raw_refresh_token_uvw",
        "access_token_expire_in": 3600,
        "refresh_token_expire_in": 7200,
        "open_id": "shop_12345",
        "seller_name": "Test Seller MY",
    }

    mock_conn = AsyncMock()
    captured_inserts: list[tuple[str, Any]] = []

    async def mock_fetch_one(path: str, params: Any = None, **kwargs: Any) -> Any:
        captured_inserts.append((path, params))
        if path == "channels/get_credential.sql":
            return None  # new credential
        if path == "channels/get_endpoint_by_public_id.sql":
            return None  # new endpoint
        return {"id": uuid4()}

    with (
        patch("csa.channels.marketplace.oauth.tx") as mock_tx,
        patch("csa.channels.marketplace.oauth.db.q.fetch_one", side_effect=mock_fetch_one),
        patch("csa.channels.marketplace.oauth.crypto.encrypt", return_value="v1:encrypted_token"),
    ):
        mock_tx.return_value.__aenter__.return_value = mock_conn

        cred_id, shop_id = await save_tiktok_shop_credentials(
            pool,
            client_id=client_id,
            token_data=token_data,
        )

        assert shop_id == "shop_12345"
        assert isinstance(cred_id, UUID)
        paths = [p for p, _ in captured_inserts]
        assert "channels/insert_credential.sql" in paths
        assert "channels/insert_endpoint.sql" in paths


@pytest.mark.asyncio
async def test_webhook_ingress_persists_event() -> None:
    secret = "app_secret"
    body = b'{"shop_id":"shop_100","type":1,"event_id":"evt_555"}'
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    client_id = uuid4()
    endpoint_id = uuid4()
    pool = MagicMock()

    ingress = TikTokWebhookIngress(pool, app_secret=secret)
    ingress._resolve_endpoint = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "endpoint_id": endpoint_id,
            "client_id": client_id,
            "is_enabled": True,
            "rate_limit_rpm": 100,
        }
    )

    mock_conn = AsyncMock()
    with (
        patch("csa.channels.marketplace.ingress.tx") as mock_tx,
        patch(
            "csa.channels.marketplace.ingress.db.q.fetch_one",
            new=AsyncMock(return_value={"id": uuid4()}),
        ) as mock_fetch,
    ):
        mock_tx.return_value.__aenter__.return_value = mock_conn

        receipt = await ingress.receive(
            raw_body=body,
            signature=sig,
        )

        assert receipt.accepted is True
        assert receipt.duplicate is False
        assert receipt.shop_id == "shop_100"
        mock_fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_webhook_ingress_duplicate_handling() -> None:
    secret = "app_secret"
    body = b'{"shop_id":"shop_100","type":1,"event_id":"evt_duplicate"}'
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    client_id = uuid4()
    endpoint_id = uuid4()
    existing_trace = uuid4()
    pool = MagicMock()

    ingress = TikTokWebhookIngress(pool, app_secret=secret)
    ingress._resolve_endpoint = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "endpoint_id": endpoint_id,
            "client_id": client_id,
            "is_enabled": True,
            "rate_limit_rpm": 100,
        }
    )

    mock_conn = AsyncMock()

    async def mock_fetch_one(path: str, params: Any = None, **kwargs: Any) -> Any:
        if path == "ingress/insert_event.sql":
            return None  # duplicate conflict!
        if path == "ingress/get_event_hash.sql":
            return {"trace_id": existing_trace, "payload_hash": "xyz"}
        return None

    with (
        patch("csa.channels.marketplace.ingress.tx") as mock_tx,
        patch("csa.channels.marketplace.ingress.db.q.fetch_one", side_effect=mock_fetch_one),
    ):
        mock_tx.return_value.__aenter__.return_value = mock_conn

        receipt = await ingress.receive(
            raw_body=body,
            signature=sig,
        )

        assert receipt.accepted is True
        assert receipt.duplicate is True
        assert receipt.trace_id == existing_trace


@pytest.mark.asyncio
async def test_webhook_ingress_endpoint_not_found() -> None:
    secret = "app_secret"
    body = b'{"shop_id":"nonexistent_shop","type":1}'
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    pool = MagicMock()
    ingress = TikTokWebhookIngress(pool, app_secret=secret)
    ingress._resolve_endpoint = AsyncMock(return_value=None)  # type: ignore[method-assign]

    with pytest.raises(IngressError) as exc_info:
        await ingress.receive(
            raw_body=body,
            signature=sig,
        )
    assert exc_info.value.status_code == 404
    assert "Endpoint not found" in exc_info.value.detail


@pytest.mark.asyncio
async def test_webhook_ingress_rate_limiting() -> None:
    secret = "app_secret"
    body = b'{"shop_id":"shop_1","type":1}'
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    endpoint_id = uuid4()
    pool = MagicMock()
    limiter = EndpointRateLimiter()
    # Fill up rate limit
    for _ in range(5):
        limiter.allow(endpoint_id, limit_per_minute=5)

    ingress = TikTokWebhookIngress(pool, app_secret=secret, rate_limiter=limiter)
    ingress._resolve_endpoint = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "endpoint_id": endpoint_id,
            "client_id": uuid4(),
            "is_enabled": True,
            "rate_limit_rpm": 5,
        }
    )

    with pytest.raises(IngressError) as exc_info:
        await ingress.receive(
            raw_body=body,
            signature=sig,
        )
    assert exc_info.value.status_code == 429
    assert "rate limit exceeded" in exc_info.value.detail


# ==============================================================================
# Router / Gateway Route Tests
# ==============================================================================


def test_gateway_marketplace_draft_endpoints() -> None:
    app = FastAPI()
    app.include_router(build_marketplace_router())

    with TestClient(app) as client:
        # Shopee
        resp = client.post("/api/v1/channels/shopee/webhook")
        assert resp.status_code == 501
        assert resp.json()["platform"] == "shopee"

        resp = client.get("/api/v1/channels/shopee/oauth/callback")
        assert resp.status_code == 501

        # Lazada
        resp = client.post("/api/v1/channels/lazada/webhook")
        assert resp.status_code == 501
        assert resp.json()["platform"] == "lazada"

        resp = client.get("/api/v1/channels/lazada/oauth/callback")
        assert resp.status_code == 501


def test_gateway_tiktok_webhook_content_type_enforcement() -> None:
    app = FastAPI()
    app.include_router(build_marketplace_router())

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/channels/tiktok_shop/webhook",
            content="text/plain data",
            headers={"content-type": "text/plain"},
        )
        assert resp.status_code == 415


def test_gateway_tiktok_oauth_authorize() -> None:
    app = FastAPI()
    app.include_router(build_marketplace_router())

    client_id = uuid4()
    secret_key = "base64:MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
    with TestClient(app) as client:
        with patch("csa.channels.marketplace.router.get_settings") as mock_settings:
            mock_s = MagicMock()
            mock_s.TIKTOK_APP_KEY = "test_key_123"
            mock_s.CREDENTIALS_KEY.get_secret_value.return_value = secret_key
            mock_s.PUBLIC_BASE_URL = "https://csa-dev.nexusmandiri.com"
            mock_settings.return_value = mock_s

            resp = client.get(
                f"/api/v1/channels/tiktok_shop/oauth/authorize?client_id={client_id}&json_response=true"
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "authorization_url" in data
            assert "test_key_123" in data["authorization_url"]
            assert "state=" in data["authorization_url"]
