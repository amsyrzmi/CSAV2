"""Create or refresh the isolated local Telegram webhook endpoint (LG03)."""

from __future__ import annotations

import asyncio
import secrets
from uuid import UUID, uuid4

from csa.channels.ingress import (
    TELEGRAM_WEBHOOK_SECRET_AAD_COLUMN,
    TELEGRAM_WEBHOOK_SECRET_META_KEY,
)
from csa.core import crypto, db
from csa.core.settings import get_settings
from csa.core.tenancy import tx
from psycopg.types.json import Jsonb

_LOCAL_CLIENT_SLUG = "local-dev-telegram"
_DEMO_PRODUCTS = (
    ("A1", "Sample Widget A1", "10.50", 100),
    ("B2", "Sample Gadget B2", "25.00", 50),
)


def _required_local_secret(name: str, value: str | None) -> str:
    if not value or value.startswith("replace-with-"):
        raise ValueError(f"{name} must be set to a development-only value in .env.local")
    return value


async def setup() -> str:
    """Create an isolated client, credential, and webhook endpoint; return its public id."""
    settings = get_settings()
    if settings.ENV != "dev":
        raise ValueError("local Telegram bootstrap requires ENV=dev")
    bot_token = _required_local_secret(
        "TELEGRAM_BOT_TOKEN",
        settings.TELEGRAM_BOT_TOKEN.get_secret_value()
        if settings.TELEGRAM_BOT_TOKEN is not None
        else None,
    )
    webhook_secret = _required_local_secret(
        "TELEGRAM_WEBHOOK_SECRET",
        settings.TELEGRAM_WEBHOOK_SECRET.get_secret_value()
        if settings.TELEGRAM_WEBHOOK_SECRET is not None
        else None,
    )

    pool = db.build_pool(str(settings.DATABASE_URL), max_size=2)
    await pool.open(wait=True)
    try:
        async with pool.connection() as conn:
            client = await db.q.fetch_one(
                "ingress/get_dev_telegram_client.sql",
                (_LOCAL_CLIENT_SLUG,),
                conn=conn,
            )
            if client is None:
                client = await db.q.fetch_one(
                    "ingress/insert_dev_telegram_client.sql",
                    (_LOCAL_CLIENT_SLUG, "Local Telegram Development"),
                    conn=conn,
                )
            if client is None:
                client = await db.q.fetch_one(
                    "ingress/get_dev_telegram_client.sql",
                    (_LOCAL_CLIENT_SLUG,),
                    conn=conn,
                )
            if client is None:
                raise RuntimeError("failed to create or locate local Telegram client")
            client_id: UUID = client["id"]

        async with tx(pool, str(client_id)) as conn:
            existing = await db.q.fetch_one(
                "ingress/get_dev_telegram_credential.sql",
                (client_id,),
                conn=conn,
            )
            credential_id = existing["credential_id"] if existing is not None else uuid4()
            encrypted_token = crypto.encrypt(
                bot_token,
                crypto.credential_aad("channel_credentials", "access_token", str(credential_id)),
            )
            encrypted_secret = crypto.encrypt(
                webhook_secret,
                crypto.credential_aad(
                    "channel_credentials",
                    TELEGRAM_WEBHOOK_SECRET_AAD_COLUMN,
                    str(credential_id),
                ),
            )
            if existing is None:
                credential = await db.q.fetch_one(
                    "ingress/insert_dev_telegram_credential.sql",
                    (
                        credential_id,
                        client_id,
                        encrypted_token,
                        Jsonb({TELEGRAM_WEBHOOK_SECRET_META_KEY: encrypted_secret}),
                    ),
                    conn=conn,
                )
                if credential is None:
                    raise RuntimeError("failed to create local Telegram credential")
                credential_id = credential["id"]
                endpoint_public_id = secrets.token_urlsafe(32)
                endpoint = await db.q.fetch_one(
                    "ingress/insert_dev_telegram_endpoint.sql",
                    (client_id, endpoint_public_id, credential_id),
                    conn=conn,
                )
                if endpoint is None:
                    raise RuntimeError("failed to create local Telegram endpoint")
                result = endpoint_public_id
            else:
                await db.q.execute(
                    "ingress/update_dev_telegram_credential.sql",
                    (
                        encrypted_token,
                        Jsonb({TELEGRAM_WEBHOOK_SECRET_META_KEY: encrypted_secret}),
                        existing["credential_id"],
                        client_id,
                    ),
                    conn=conn,
                )
                result = existing["public_id"]

        async with tx(pool, str(client_id)) as conn:
            for sku, name, price, stock in _DEMO_PRODUCTS:
                await db.q.execute(
                    "ingress/seed_dev_product.sql",
                    (client_id, sku, name, price, stock),
                    conn=conn,
                )
            if settings.DEV_STAFF_CHAT_ID:
                await db.q.execute(
                    "ingress/seed_dev_staff_target.sql",
                    (client_id, settings.DEV_STAFF_CHAT_ID),
                    conn=conn,
                )

        return str(result)
    finally:
        await pool.close()


def main() -> int:
    """Run the bootstrap and print only the non-secret webhook URL."""
    public_id = asyncio.run(setup())
    settings = get_settings()
    base_url = str(settings.PUBLIC_BASE_URL).rstrip("/")
    print(f"Telegram webhook URL: {base_url}/hooks/{public_id}")
    print("Register this URL using the development bot only; do not use a production bot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
