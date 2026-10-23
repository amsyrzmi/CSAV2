"""Explicit operator registration of one development bot in isolated staging.

Run in the dedicated setup container. Tokens are entered with getpass, encrypted
in the database and never printed. Existing bot/tenant bindings cannot be moved.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
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

WEBHOOK_ORIGIN = "https://csa-dev.nexusmandiri.com"


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
    if tenant.status not in {"active", "pilot"} or tenant.config.get("runtime") != "langgraph":
        raise SetupError("Selected tenant must be active/pilot with runtime=langgraph")
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
    token = SecretStr(getpass.getpass("Development bot token (hidden): ").strip())
    if not token.get_secret_value():
        raise SetupError("Development bot token is required")
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
        url = f"{WEBHOOK_ORIGIN}/hooks/{binding.public_id}"
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
    parser.add_argument("--development-bot", action="store_true", required=True)
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
    print(f"Development webhook registered: {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
