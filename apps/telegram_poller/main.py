"""Development-only Telegram long polling (LG03)."""

from __future__ import annotations

import asyncio
import json
from typing import Protocol
from uuid import UUID

import httpx
import structlog
from csa.channels.ingress import IngressReceipt, TelegramIngress
from csa.channels.telegram import TelegramAdapter, TelegramPollingError
from csa.core import crypto, db
from csa.core.logging import configure_logging, shutdown_logging
from csa.core.settings import get_settings
from csa.core.tracing import configure_tracing
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)
_LOCAL_CLIENT_SLUG = "local-dev-telegram"
_POLL_RETRY_SECONDS = 2


class _LocalPollingCredentials(BaseModel):
    credential_id: UUID
    encrypted_bot_token: str = Field(min_length=1)
    meta: dict[str, object]
    public_id: str = Field(min_length=16, max_length=64)


class IngressReceiver(Protocol):
    async def receive(
        self,
        public_id: str,
        supplied_secret: str | None,
        body: bytes,
    ) -> IngressReceipt: ...


class TelegramPoller:
    """Fetch Telegram updates and persist them through the durable ingress path."""

    def __init__(
        self,
        bot_token: str,
        webhook_secret: str,
        public_id: str,
        ingress: IngressReceiver,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._bot_token = bot_token
        self._webhook_secret = webhook_secret
        self._public_id = public_id
        self._ingress = ingress
        self._http_client = http_client
        self._offset: int | None = None

    async def initialize(self) -> None:
        """Disable webhook delivery without dropping pending updates."""
        await TelegramAdapter.delete_webhook(
            self._bot_token,
            http_client=self._http_client,
        )

    async def poll_once(self) -> int:
        """Persist one batch; advance offset only after each durable acceptance."""
        updates = await TelegramAdapter.get_updates(
            self._bot_token,
            offset=self._offset,
            http_client=self._http_client,
        )
        update_ids: list[int] = []
        for update in updates:
            update_id = update.get("update_id")
            if type(update_id) is not int or update_id < 0:
                raise TelegramPollingError("telegram_invalid_update")
            update_ids.append(update_id)
        if any(left >= right for left, right in zip(update_ids, update_ids[1:], strict=False)):
            raise TelegramPollingError("telegram_updates_out_of_order")

        accepted = 0
        for update_id, update in zip(update_ids, updates, strict=True):
            if self._offset is not None and update_id < self._offset:
                continue
            await self._ingress.receive(
                self._public_id,
                self._webhook_secret,
                json.dumps(update, separators=(",", ":")).encode(),
            )
            self._offset = update_id + 1
            accepted += 1
        return accepted

    async def run(self) -> None:
        """Continuously poll; transient Bot API errors retry without advancing."""
        await self.initialize()
        while True:
            try:
                accepted = await self.poll_once()
            except TelegramPollingError as exc:
                logger.warning("telegram_poll_failed", error=exc.error_code)
                await asyncio.sleep(_POLL_RETRY_SECONDS)
                continue
            if accepted:
                logger.info("telegram_updates_persisted", count=accepted)


def _local_polling_dsn_is_safe(dsn: str) -> bool:
    """Restrict this optional poller to the separately bound server dev DB."""
    from urllib.parse import urlsplit

    parsed = urlsplit(dsn)
    return parsed.hostname == "127.0.0.1" and parsed.port == 5434 and parsed.path == "/csa"


async def _load_local_credentials(owner_pool: AsyncConnectionPool) -> tuple[str, str, str]:
    async with owner_pool.connection() as conn:
        row = await db.q.fetch_one(
            "ingress/get_local_telegram_polling_credentials.sql",
            conn=conn,
            model=_LocalPollingCredentials,
        )
    if row is None:
        raise RuntimeError("local development Telegram credential is not configured")

    secret_ciphertext = row.meta.get("telegram_webhook_secret_enc")
    if not isinstance(secret_ciphertext, str) or not secret_ciphertext:
        raise RuntimeError("local development Telegram webhook secret is not configured")

    credential_id = str(row.credential_id)
    try:
        bot_token = crypto.decrypt(
            row.encrypted_bot_token,
            crypto.credential_aad("channel_credentials", "access_token", credential_id),
        )
        webhook_secret = crypto.decrypt(
            secret_ciphertext,
            crypto.credential_aad(
                "channel_credentials",
                "meta.telegram_webhook_secret_enc",
                credential_id,
            ),
        )
    except crypto.CryptoError as exc:
        raise RuntimeError("local development Telegram credentials cannot be decrypted") from exc
    return bot_token, webhook_secret, row.public_id


async def run_poller() -> None:
    settings = get_settings()
    if settings.ENV != "dev" or settings.CHANNEL_MODE != "polling":
        raise RuntimeError("Telegram polling requires ENV=dev and CHANNEL_MODE=polling")
    if not _local_polling_dsn_is_safe(str(settings.DATABASE_URL)) or not (
        _local_polling_dsn_is_safe(str(settings.LG_DATABASE_URL))
    ):
        raise RuntimeError("Telegram polling requires the isolated 127.0.0.1:5434 dev database")

    configure_logging(env=settings.ENV)
    configure_tracing(
        exporter=settings.OTEL_EXPORTER,
        otlp_endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
    )
    owner_pool = db.build_pool(str(settings.LG_DATABASE_URL), max_size=2)
    app_pool = db.build_pool(str(settings.DATABASE_URL), max_size=2)
    await owner_pool.open(wait=True)
    await app_pool.open(wait=True)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=3, read=35, write=10, pool=3)
    ) as http_client:
        try:
            bot_token, webhook_secret, public_id = await _load_local_credentials(owner_pool)
            poller = TelegramPoller(
                bot_token,
                webhook_secret,
                public_id,
                TelegramIngress(app_pool),
                http_client,
            )
            await poller.run()
        finally:
            await app_pool.close()
            await owner_pool.close()


def main() -> int:
    try:
        asyncio.run(run_poller())
    finally:
        shutdown_logging()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
