"""Explicit operator registration of one test bot in isolated staging.

Run in the dedicated setup container. The token comes from the mounted secret
file ``/run/secrets/TELEGRAM_BOT_TOKEN`` (or getpass when absent), is encrypted
in the database and never printed. The webhook origin comes from
``CSA_STAGING_HOOKS_ORIGIN`` and must be the HTTPS hooks hostname that forwards
only ``POST /hooks/*`` to the staging gateway. Existing bot/tenant bindings
cannot be moved.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from csa.channels.ingress import (
    TELEGRAM_WEBHOOK_SECRET_AAD_COLUMN,
    TELEGRAM_WEBHOOK_SECRET_META_KEY,
)
from csa.core import crypto, db
from csa.core.tenancy import tx
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, SecretStr

HOOKS_ORIGIN_ENV = "CSA_STAGING_HOOKS_ORIGIN"
TOKEN_SECRET = Path("/run/secrets/TELEGRAM_BOT_TOKEN")
ALLOWED_TENANT_STATUS = frozenset({"onboarding", "pilot", "active"})


class SetupError(Exception):
    """A safe operator-facing setup failure."""


class Tenant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    slug: str
    name: str
    status: str
    config: dict[str, Any]


class Bot(BaseModel):
    id: int
    is_bot: bool


class BotBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: UUID
    public_id: str
    webhook_secret: SecretStr


def hooks_origin(value: str | None) -> str:
    """Require an explicit HTTPS origin with no path, query, fragment or credentials."""
    parsed = urlsplit(value or "")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise SetupError(f"{HOOKS_ORIGIN_ENV} must be an https origin without a path")
    return f"https://{parsed.netloc}"


def read_token() -> SecretStr:
    """Prefer the mounted secret file; fall back to a hidden prompt."""
    if TOKEN_SECRET.exists():
        token = TOKEN_SECRET.read_text().strip()
    else:
        token = getpass.getpass("Test bot token (hidden): ").strip()
    if not token:
        raise SetupError("Telegram bot token is required")
    return SecretStr(token)


def validate_target(dsn: str) -> None:
    """Reject all targets except the staging owner on its private Compose DNS."""
    try:
        parsed = urlsplit(dsn)
        valid = (
            parsed.scheme in {"postgres", "postgresql"}
            and parsed.hostname == "staging-db"
            and parsed.port in {None, 5432}
            and parsed.path == "/csa_staging"
            and parsed.username == "csa"
            and bool(parsed.password)
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        valid = False
    if not valid:
        raise SetupError("Target must be the isolated staging owner/database")


async def bind_bot(
    pool: AsyncConnectionPool,
    tenant_name: str,
    bot: Bot,
    token: SecretStr,
) -> BotBinding:
    """Atomically register a bot, preserve retries and audit a new binding."""
    if not bot.is_bot or bot.id <= 0:
        raise SetupError("Telegram did not identify a bot")
    async with pool.connection() as conn:
        rows = await db.q.fetch_all(
            "staging/find_tenant.sql", (tenant_name, tenant_name), conn=conn
        )
    if len(rows) != 1:
        raise SetupError("Tenant name/slug must match exactly one staging tenant")
    tenant = Tenant.model_validate(rows[0])
    if tenant.status not in ALLOWED_TENANT_STATUS or tenant.config.get("runtime") != "langgraph":
        raise SetupError("Selected tenant must be onboarding/pilot/active with runtime=langgraph")
    async with tx(pool, str(tenant.id)) as conn:
        await db.q.execute("staging/lock_bot.sql", (f"staging-telegram:{bot.id}",), conn=conn)
        existing = await db.q.fetch_all("staging/find_bot.sql", (str(bot.id),), conn=conn)
        if existing:
            if len(existing) != 1 or existing[0]["client_id"] != tenant.id:
                raise SetupError("Bot already has a different or ambiguous tenant binding")
            row = existing[0]
            if not row["public_id"] or not row["is_enabled"]:
                raise SetupError("Existing bot endpoint is absent or disabled")
            stored = crypto.decrypt(
                row["access_token"],
                crypto.credential_aad("channel_credentials", "access_token", str(row["id"])),
            )
            if not secrets.compare_digest(stored, token.get_secret_value()):
                raise SetupError("Existing token differs; rotation requires a separate procedure")
            secret = crypto.decrypt(
                row["meta"][TELEGRAM_WEBHOOK_SECRET_META_KEY],
                crypto.credential_aad(
                    "channel_credentials", TELEGRAM_WEBHOOK_SECRET_AAD_COLUMN, str(row["id"])
                ),
            )
            return BotBinding(
                client_id=tenant.id, public_id=row["public_id"], webhook_secret=SecretStr(secret)
            )
        credential_id = uuid4()
        secret = secrets.token_urlsafe(32)
        public_id = secrets.token_urlsafe(32)
        encrypted_token = crypto.encrypt(
            token.get_secret_value(),
            crypto.credential_aad("channel_credentials", "access_token", str(credential_id)),
        )
        encrypted_secret = crypto.encrypt(
            secret,
            crypto.credential_aad(
                "channel_credentials", TELEGRAM_WEBHOOK_SECRET_AAD_COLUMN, str(credential_id)
            ),
        )
        await db.q.execute(
            "staging/insert_bot.sql",
            (
                credential_id,
                tenant.id,
                str(bot.id),
                encrypted_token,
                Jsonb({TELEGRAM_WEBHOOK_SECRET_META_KEY: encrypted_secret}),
            ),
            conn=conn,
        )
        await db.q.fetch_one(
            "ingress/insert_dev_telegram_endpoint.sql",
            (tenant.id, public_id, credential_id),
            conn=conn,
        )
        await db.q.execute(
            "console/audit.sql",
            (None, tenant.id, "staging.test_bot.registered", str(credential_id), Jsonb({})),
            conn=conn,
        )
        return BotBinding(
            client_id=tenant.id, public_id=public_id, webhook_secret=SecretStr(secret)
        )


async def telegram(
    client: httpx.AsyncClient, token: SecretStr, method: str, payload: dict[str, Any]
) -> Any:
    """Never propagate HTTP exceptions containing the token-bearing URL."""
    try:
        response = await client.post(
            f"https://api.telegram.org/bot{token.get_secret_value()}/{method}", json=payload
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or body.get("ok") is not True or "result" not in body:
            raise SetupError("Telegram rejected the operation")
        return body["result"]
    except (httpx.HTTPError, ValueError):
        raise SetupError("Telegram request failed; no credential details were printed") from None


async def setup(tenant_name: str, replace_webhook: bool) -> str:
    dsn = Path("/run/secrets/STAGING_OWNER_DSN").read_text().strip()
    validate_target(dsn)
    origin = hooks_origin(os.environ.get(HOOKS_ORIGIN_ENV))
    token = read_token()
    async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
        bot = Bot.model_validate(await telegram(client, token, "getMe", {}))
        info = await telegram(client, token, "getWebhookInfo", {})
        if not isinstance(info, dict) or not isinstance(info.get("url"), str):
            raise SetupError("Invalid Telegram webhook response")
        # Do not mutate either system until replacement intent is explicit.
        if info["url"] and not replace_webhook:
            raise SetupError("Bot has an existing webhook; review before --replace-webhook")
        pool = db.build_pool(dsn, max_size=1)
        await pool.open(wait=True)
        try:
            binding = await bind_bot(pool, tenant_name, bot, token)
        finally:
            await pool.close()
        url = f"{origin}/hooks/{binding.public_id}"
        result = await telegram(
            client,
            token,
            "setWebhook",
            {
                "url": url,
                "secret_token": binding.webhook_secret.get_secret_value(),
                "allowed_updates": ["message", "callback_query"],
                "drop_pending_updates": False,
            },
        )
        if result is not True:
            raise SetupError("Webhook registration was not acknowledged; binding is preserved")
        return url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--test-bot", action="store_true", required=True)
    parser.add_argument("--replace-webhook", action="store_true")
    args = parser.parse_args()
    try:
        url = asyncio.run(setup(args.tenant, args.replace_webhook))
    except SetupError as exc:
        print(str(exc))
        return 1
    except Exception as exc:
        # Operator traceback/driver validation errors can contain connection values.
        print(f"Test-bot setup failed ({type(exc).__name__}); no credential details printed.")
        return 1
    print(f"Staging webhook registered: {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
