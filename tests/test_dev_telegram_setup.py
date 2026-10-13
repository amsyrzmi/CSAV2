"""Local Telegram credential bootstrap integration test (LG03)."""

from __future__ import annotations

import base64

import psycopg
import pytest
from csa.channels.ingress import (
    TELEGRAM_WEBHOOK_SECRET_AAD_COLUMN,
    TELEGRAM_WEBHOOK_SECRET_META_KEY,
)
from csa.core import crypto, settings
from scripts.setup_dev_telegram import setup

from tests.conftest import owner_dsn_for, requires_docker

_KEY = "base64:" + base64.b64encode(b"\x05" * 32).decode("ascii")
_BOT_TOKEN = "12345:local-development-token"
_WEBHOOK_SECRET = "local-development-secret"


@requires_docker
async def test_setup_dev_telegram_encrypts_and_reuses_local_endpoint(
    pg_container: object,
    app_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in {
        "ENV": "dev",
        "DATABASE_URL": app_dsn,
        "LG_DATABASE_URL": app_dsn,
        "GROQ_API_KEY": "test-key",
        "PUBLIC_BASE_URL": "https://dev-tunnel.example",
        "CREDENTIALS_KEY": _KEY,
        "TELEGRAM_BOT_TOKEN": _BOT_TOKEN,
        "TELEGRAM_WEBHOOK_SECRET": _WEBHOOK_SECRET,
    }.items():
        monkeypatch.setenv(name, value)
    settings.get_settings.cache_clear()
    try:
        public_id = await setup()
        assert await setup() == public_id

        with psycopg.connect(owner_dsn_for(pg_container)) as conn:
            rows = conn.execute(
                "SELECT c.id, c.access_token, c.meta, e.public_id "
                "FROM clients client "
                "JOIN channel_credentials c ON c.client_id = client.id "
                "JOIN webhook_endpoints e ON e.client_id = c.client_id "
                "AND e.credential_id = c.id "
                "WHERE client.slug = 'local-dev-telegram' "
                "AND c.shop_id = 'local-dev-telegram'"
            ).fetchall()

        assert len(rows) == 1
        credential_id, encrypted_token, meta, stored_public_id = rows[0]
        assert stored_public_id == public_id
        assert encrypted_token != _BOT_TOKEN
        assert meta[TELEGRAM_WEBHOOK_SECRET_META_KEY] != _WEBHOOK_SECRET
        assert (
            crypto.decrypt(
                encrypted_token,
                crypto.credential_aad("channel_credentials", "access_token", str(credential_id)),
            )
            == _BOT_TOKEN
        )
        assert (
            crypto.decrypt(
                meta[TELEGRAM_WEBHOOK_SECRET_META_KEY],
                crypto.credential_aad(
                    "channel_credentials",
                    TELEGRAM_WEBHOOK_SECRET_AAD_COLUMN,
                    str(credential_id),
                ),
            )
            == _WEBHOOK_SECRET
        )
    finally:
        settings.get_settings.cache_clear()
