"""The gateway no longer serves the unauthenticated TikTok connect flow."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from csa.channels.marketplace.router import build_marketplace_router
from fastapi import FastAPI
from fastapi.testclient import TestClient

_BASE = "/api/v1/channels"


def test_legacy_oauth_and_endpoint_webhook_routes_are_gone() -> None:
    app = FastAPI()
    app.include_router(build_marketplace_router())

    # Made-up settings, so a route that still existed would answer rather than fail on config.
    fake_settings = MagicMock()
    fake_settings.TIKTOK_APP_KEY = None
    fake_settings.TIKTOK_APP_SECRET = None

    with (
        patch("csa.channels.marketplace.router.get_settings", return_value=fake_settings),
        TestClient(app) as client,
    ):
        assert (
            client.get(
                f"{_BASE}/tiktok_shop/oauth/authorize",
                params={"client_id": "00000000-0000-0000-0000-000000000000"},
                follow_redirects=False,
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"{_BASE}/tiktok_shop/oauth/callback",
                params={"code": "made-up-code", "state": "made-up-state"},
                follow_redirects=False,
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"{_BASE}/tiktok_shop/webhook/made-up-public-id",
                content=b"{}",
                headers={"content-type": "application/json"},
            ).status_code
            == 404
        )

        # The Shopee and Lazada drafts are untouched.
        assert client.post(f"{_BASE}/shopee/webhook").status_code == 501
        assert client.get(f"{_BASE}/shopee/oauth/callback").status_code == 501
        assert client.post(f"{_BASE}/lazada/webhook").status_code == 501
        assert client.get(f"{_BASE}/lazada/oauth/callback").status_code == 501

        # The global webhook stays until it is rewired: it answers (415 for non-JSON), never 404.
        assert (
            client.post(
                f"{_BASE}/tiktok_shop/webhook",
                content=b"not json",
                headers={"content-type": "text/plain"},
            ).status_code
            == 415
        )
