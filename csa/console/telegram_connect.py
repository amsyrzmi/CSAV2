"""Connect, replace and read a company's Telegram bot (platform-admin setup, one bot per company).

The order of `connect` is the requirement: prove the token with Telegram, then store, then register
the webhook, then record that it is registered. No network call runs inside a transaction, and a
company shows as connected only once its webhook is registered.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import structlog
from psycopg import errors
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from csa.channels.ingress import (
    TELEGRAM_WEBHOOK_SECRET_AAD_COLUMN,
    TELEGRAM_WEBHOOK_SECRET_META_KEY,
)
from csa.channels.telegram import (
    TelegramAdapter,
    TelegramBotProfile,
    TelegramDeliveryError,
    TelegramPollingError,
)
from csa.console.contracts import ConsoleActor, Contract
from csa.console.kb_uploads import audit
from csa.core import crypto, db
from csa.core.settings import get_settings
from csa.core.tenancy import tx

logger = structlog.get_logger(__name__)

REGISTERED_KEY = "telegram_webhook_registered_at"
BOT_KEY_INDEX = "channel_credentials_telegram_bot_key"
_TOKEN = re.compile(r"[0-9]{5,20}:[A-Za-z0-9_-]{30,64}")
_REJECTED = frozenset({"telegram_http_401", "telegram_http_404", "telegram_api_rejected"})
_CHAT = re.compile(r"-?[0-9]{1,20}")
_TEST_TEXT = (
    "Connected. This chat will now receive escalations and approval requests "
    "for this company's customer support."
)


class TelegramConnectError(Exception):
    """A refusal with the HTTP status and the stable code the console shows."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


class TelegramStaffChat(Contract):
    address: str
    verified: bool


class TelegramStatus(Contract):
    connected: bool
    bot_id: str | None
    bot_username: str | None
    webhook_registered: bool
    connected_at: datetime | None
    staff_chat: TelegramStaffChat | None
    hooks_ready: bool


class TelegramCheck(Contract):
    webhook_url_matches: bool
    pending_update_count: int
    last_error: str | None


def refuse(client_id: UUID, status: int, code: str) -> TelegramConnectError:
    """Log the refusal by code only (never a token) and build the error to raise."""
    logger.warning("telegram_connect_refused", client_id=str(client_id), code=code)
    return TelegramConnectError(status, code)


def _present(row: dict[str, Any] | None, staff: dict[str, Any] | None) -> TelegramStatus:
    chat = None
    if staff is not None:
        chat = TelegramStaffChat(
            address=staff["address"], verified=staff["verified_at"] is not None
        )
    hooks_ready = get_settings().hooks_origin is not None
    if row is None:
        return TelegramStatus(
            connected=False,
            bot_id=None,
            bot_username=None,
            webhook_registered=False,
            connected_at=None,
            staff_chat=chat,
            hooks_ready=hooks_ready,
        )
    meta = row["meta"] or {}
    registered = meta.get(REGISTERED_KEY)
    connected = bool(row["has_token"] and registered and row["is_enabled"])
    return TelegramStatus(
        connected=connected,
        bot_id=row["shop_id"],
        bot_username=meta.get("bot_username"),
        webhook_registered=bool(registered),
        connected_at=datetime.fromisoformat(registered) if connected and registered else None,
        staff_chat=chat,
        hooks_ready=hooks_ready,
    )


async def get_status(pool: AsyncConnectionPool, client_id: UUID) -> TelegramStatus:
    """The company's bot as stored; database only, never a call to Telegram."""
    async with tx(pool, str(client_id)) as conn:
        row = await db.q.fetch_one("console/telegram_get.sql", (client_id,), conn=conn)
        staff = await db.q.fetch_one("console/telegram_staff_get.sql", (client_id,), conn=conn)
    return _present(row, staff)


def _seal_token(credential_id: UUID, token: str) -> str:
    aad = crypto.credential_aad("channel_credentials", "access_token", str(credential_id))
    return crypto.encrypt(token, aad)


def _seal_secret(credential_id: UUID, secret: str) -> str:
    aad = crypto.credential_aad(
        "channel_credentials", TELEGRAM_WEBHOOK_SECRET_AAD_COLUMN, str(credential_id)
    )
    return crypto.encrypt(secret, aad)


async def _prove_token(
    client_id: UUID, token: str, http_client: httpx.AsyncClient | None
) -> TelegramBotProfile:
    try:
        return await TelegramAdapter.get_me(token, http_client=http_client)
    except TelegramPollingError as exc:
        if exc.error_code in _REJECTED:
            raise refuse(client_id, 422, "telegram_token_rejected") from None
        raise refuse(client_id, 502, "telegram_unreachable") from None


async def _store(
    pool: AsyncConnectionPool, client_id: UUID, token: str, profile: TelegramBotProfile
) -> tuple[UUID, str, str, bool]:
    """Write the sealed token and secret; returns (credential id, public id, secret, replaced)."""
    bot = str(profile.bot_id)
    secret = secrets.token_urlsafe(32)
    try:
        async with tx(pool, str(client_id)) as conn:
            await db.q.execute("console/telegram_lock.sql", (f"telegram-bot:{bot}",), conn=conn)
            existing = await db.q.fetch_one("console/telegram_get.sql", (client_id,), conn=conn)
            if existing is not None and existing["shop_id"] != bot:
                raise refuse(client_id, 409, "telegram_bot_replace")
            meta = {
                "bot_username": profile.username,
                TELEGRAM_WEBHOOK_SECRET_META_KEY: "",
            }
            if existing is None:
                credential_id, public_id = uuid4(), secrets.token_urlsafe(32)
                meta[TELEGRAM_WEBHOOK_SECRET_META_KEY] = _seal_secret(credential_id, secret)
                await db.q.execute(
                    "console/telegram_insert.sql",
                    (credential_id, client_id, bot, _seal_token(credential_id, token), Jsonb(meta)),
                    conn=conn,
                )
                await db.q.execute(
                    "console/telegram_endpoint_insert.sql",
                    (client_id, public_id, credential_id, f"Telegram @{profile.username}"),
                    conn=conn,
                )
                return credential_id, public_id, secret, False
            credential_id, public_id = existing["credential_id"], existing["public_id"]
            meta[TELEGRAM_WEBHOOK_SECRET_META_KEY] = _seal_secret(credential_id, secret)
            await db.q.execute(
                "console/telegram_replace_token.sql",
                (_seal_token(credential_id, token), Jsonb(meta), credential_id, client_id),
                conn=conn,
            )
            replaced = bool((existing["meta"] or {}).get(REGISTERED_KEY))
            return credential_id, public_id, secret, replaced
    except errors.UniqueViolation as exc:
        if exc.diag.constraint_name == BOT_KEY_INDEX:
            raise refuse(client_id, 409, "telegram_bot_in_use") from None
        raise refuse(client_id, 409, "telegram_connect_in_progress") from None


@asynccontextmanager
async def _one_at_a_time(pool: AsyncConnectionPool, client_id: UUID) -> AsyncIterator[None]:
    """Let one connect or disconnect per company run at a time, the whole way through.

    The per-bot lock inside `_store` only covers its own transaction. Two writers for one company
    could otherwise leave Telegram holding one webhook secret and the database another, which shows
    as connected while every update is refused. A session-level lock on a held connection spans the
    network calls too; a second writer is told to wait instead of queueing behind it.
    """
    key = f"telegram-connect:{client_id}"
    async with pool.connection() as conn:
        row = await db.q.fetch_one("console/telegram_try_lock.sql", (key,), conn=conn)
        if not row or not row["got"]:
            raise refuse(client_id, 409, "telegram_connect_in_progress")
        try:
            yield
        finally:
            await db.q.fetch_one("console/telegram_unlock.sql", (key,), conn=conn)


async def connect(
    pool: AsyncConnectionPool,
    actor: ConsoleActor,
    client_id: UUID,
    token: str,
    *,
    replace_webhook: bool = False,
    http_client: httpx.AsyncClient | None = None,
) -> TelegramStatus:
    """Connect the company's bot, or replace the token of the bot it already has."""
    if not _TOKEN.fullmatch(token):
        raise refuse(client_id, 422, "telegram_token_malformed")
    origin = get_settings().hooks_origin
    if origin is None:
        raise refuse(client_id, 503, "hooks_origin_not_configured")
    async with _one_at_a_time(pool, client_id):
        return await _connect_locked(
            pool, actor, client_id, token, origin, replace_webhook, http_client
        )


async def _connect_locked(
    pool: AsyncConnectionPool,
    actor: ConsoleActor,
    client_id: UUID,
    token: str,
    origin: str,
    replace_webhook: bool,
    http_client: httpx.AsyncClient | None,
) -> TelegramStatus:
    profile = await _prove_token(client_id, token, http_client)
    async with tx(pool, str(client_id)) as conn:
        existing = await db.q.fetch_one("console/telegram_get.sql", (client_id,), conn=conn)
    if existing is not None and existing["shop_id"] != str(profile.bot_id):
        raise refuse(client_id, 409, "telegram_bot_replace")
    try:
        info = await TelegramAdapter.get_webhook_info(token, http_client=http_client)
    except TelegramPollingError:
        raise refuse(client_id, 502, "telegram_unreachable") from None
    # A webhook under our own hooks origin is ours (this company's, or a company that held the bot
    # before); only one pointing elsewhere needs the admin's confirmation. The unique index decides
    # whether another company holds the bot.
    ours = f"{origin}/hooks/"
    if info.url and not info.url.startswith(ours) and not replace_webhook:
        raise refuse(client_id, 409, "telegram_webhook_in_use")

    credential_id, public_id, secret, replaced = await _store(pool, client_id, token, profile)
    try:
        await TelegramAdapter.set_webhook(
            token,
            url=f"{origin}/hooks/{public_id}",
            secret_token=secret,
            http_client=http_client,
        )
    except TelegramPollingError:
        raise refuse(client_id, 502, "telegram_webhook_failed") from None
    async with tx(pool, str(client_id)) as conn:
        await db.q.execute(
            "console/telegram_mark_registered.sql",
            (Jsonb({REGISTERED_KEY: datetime.now(UTC).isoformat()}), credential_id, client_id),
            conn=conn,
        )
        action = "token_replaced" if replaced else "connected"
        await audit(conn, actor, client_id, f"channels.telegram.{action}", str(credential_id))
    return await get_status(pool, client_id)


async def _active(pool: AsyncConnectionPool, client_id: UUID) -> dict[str, Any] | None:
    async with tx(pool, str(client_id)) as conn:
        return await db.q.fetch_one("console/telegram_get.sql", (client_id,), conn=conn)


def _token_of(client_id: UUID, row: dict[str, Any]) -> str:
    aad = crypto.credential_aad("channel_credentials", "access_token", str(row["credential_id"]))
    try:
        return crypto.decrypt(row["access_token"], aad)
    except crypto.CryptoError:
        logger.error("telegram_token_unreadable", client_id=str(client_id))
        raise TelegramConnectError(503, "telegram_credential_unavailable") from None


def _is_connected(row: dict[str, Any] | None) -> bool:
    if row is None or not row["has_token"] or not row["is_enabled"]:
        return False
    return bool((row["meta"] or {}).get(REGISTERED_KEY))


async def set_staff_chat(
    pool: AsyncConnectionPool,
    actor: ConsoleActor,
    client_id: UUID,
    chat_id: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> TelegramStatus:
    """Save the company's staff chat, but only after the bot has delivered a test message to it."""
    if not _CHAT.fullmatch(chat_id) or int(chat_id) == 0:
        raise refuse(client_id, 422, "telegram_chat_invalid")
    row = await _active(pool, client_id)
    if row is None or not _is_connected(row):
        raise refuse(client_id, 409, "telegram_not_connected")
    token = _token_of(client_id, row)
    try:
        await TelegramAdapter.send_message(
            token, chat_id=chat_id, text=_TEST_TEXT, http_client=http_client
        )
    except TelegramDeliveryError as exc:
        if exc.error_code == "telegram_http_400":
            raise refuse(client_id, 422, "telegram_chat_unreachable") from None
        if exc.error_code == "telegram_http_403":
            raise refuse(client_id, 422, "telegram_chat_forbidden") from None
        raise refuse(client_id, 502, "telegram_unreachable") from None
    async with tx(pool, str(client_id)) as conn:
        # Targets are retired, not deleted: delivered and queued notices still point at them.
        await db.q.fetch_one(
            "console/telegram_staff_target_replace.sql",
            (client_id, chat_id, client_id, chat_id),
            conn=conn,
        )
        await audit(
            conn, actor, client_id, "channels.telegram.staff_chat_set", str(row["credential_id"])
        )
    return await get_status(pool, client_id)


async def check(
    pool: AsyncConnectionPool, client_id: UUID, *, http_client: httpx.AsyncClient | None = None
) -> TelegramCheck:
    """Ask Telegram what webhook the bot holds now, and whether it is the one we registered."""
    row = await _active(pool, client_id)
    if row is None or not row["has_token"]:
        raise refuse(client_id, 409, "telegram_not_connected")
    token = _token_of(client_id, row)
    try:
        info = await TelegramAdapter.get_webhook_info(token, http_client=http_client)
    except TelegramPollingError:
        raise refuse(client_id, 502, "telegram_unreachable") from None
    origin = get_settings().hooks_origin
    expected = f"{origin}/hooks/{row['public_id']}" if origin else None
    return TelegramCheck(
        webhook_url_matches=expected is not None and info.url == expected,
        pending_update_count=info.pending_update_count,
        last_error=info.last_error_message,
    )


async def disconnect(
    pool: AsyncConnectionPool,
    actor: ConsoleActor,
    client_id: UUID,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> None:
    """Stop the company's bot: drop its webhook (best effort), erase the token, retire the chat."""
    async with _one_at_a_time(pool, client_id):
        await _disconnect_locked(pool, actor, client_id, http_client)


async def _disconnect_locked(
    pool: AsyncConnectionPool,
    actor: ConsoleActor,
    client_id: UUID,
    http_client: httpx.AsyncClient | None,
) -> None:
    row = await _active(pool, client_id)
    if row is None:
        return
    if row["has_token"]:
        try:
            await TelegramAdapter.delete_webhook(_token_of(client_id, row), http_client=http_client)
        except (TelegramPollingError, TelegramConnectError) as exc:
            code = exc.code if isinstance(exc, TelegramConnectError) else exc.error_code
            logger.warning("telegram_delete_webhook_failed", client_id=str(client_id), code=code)
    async with tx(pool, str(client_id)) as conn:
        await db.q.fetch_all(
            "console/telegram_disconnect.sql",
            (row["credential_id"], client_id, client_id),
            conn=conn,
        )
        await db.q.fetch_all("console/telegram_staff_targets_disable.sql", (client_id,), conn=conn)
        await audit(
            conn, actor, client_id, "channels.telegram.disconnected", str(row["credential_id"])
        )
