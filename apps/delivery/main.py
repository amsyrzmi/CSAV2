"""Telegram outbox delivery process (LG03/LG07)."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import structlog
from csa.channels.telegram import TelegramAdapter, TelegramDeliveryError
from csa.channels.whatsapp import (
    WhatsAppAdapter,
    WhatsAppDeliveryError,
    inside_service_window,
)
from csa.contracts.facts import Ui
from csa.core import crypto, db
from csa.core.liveness import DELIVERY_ALIVE_PATH, liveness_loop
from csa.core.settings import get_settings
from csa.core.tenancy import tx
from csa.notify.notifier import notify
from csa.notify.sender import StaffNotificationSender
from csa.staff.ownership import automation_allowed, lock_conversation
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict

logger = structlog.get_logger(__name__)

MAX_DELIVERY_ATTEMPTS = 5
DELIVERY_REAPER_INTERVAL_SECONDS = 60
DELIVERY_STALE_SECONDS = 180
REAPER_BATCH_SIZE = 100
WHATSAPP_TERMINAL_CODES = frozenset(
    {"outside_service_window", "recipient_not_on_whatsapp", "token_invalid", "provider_rejected"}
)


class ClaimedDelivery(BaseModel):
    """Typed outbox row returned by the delivery claim query."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    client_id: UUID
    endpoint_id: UUID
    trace_id: UUID | None
    conversation_id: UUID | None
    message_id: UUID
    attempts: int


class DeliveryFailure(Exception):
    """Secret-free failure details used for retries and delivery attempts."""

    def __init__(
        self,
        error_code: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: int | None = None,
        terminal: bool = False,
    ) -> None:
        super().__init__(error_code)
        self.terminal = terminal
        self.error_code = error_code
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class DeliveryWorker:
    """Drain Telegram and private web outbox rows with durable retry/lease handling."""

    def __init__(
        self,
        owner_pool: AsyncConnectionPool,
        app_pool: AsyncConnectionPool,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._owner_pool = owner_pool
        self._app_pool = app_pool
        self._http_client = http_client
        self._next_reap_at = 0.0
        self._next_global_send_at = 0.0
        self._next_chat_send_at: dict[str, float] = {}
        self._staff = StaffNotificationSender(owner_pool, app_pool, http_client)

    async def do_work(self) -> bool:
        """Run one delivery and periodically reclaim abandoned sends."""
        now = time.monotonic()
        reaped = 0
        if now >= self._next_reap_at:
            reaped = await self.reap()
            self._next_reap_at = now + DELIVERY_REAPER_INTERVAL_SECONDS
        sent = await self.process_once()
        notified = await self._staff.do_work()
        return sent or notified or reaped > 0

    async def process_once(self) -> bool:
        """Claim and send one due Telegram or WhatsApp outbox row."""
        async with self._owner_pool.connection() as conn:
            row = await db.q.fetch_one("delivery/claim.sql", conn=conn)
        if row is None:
            return False
        delivery = ClaimedDelivery.model_validate(row)
        started_at = time.monotonic()
        try:
            if await self._deliver_web(delivery, started_at):
                return True
            details, bot_token, channel = await self._load_delivery(delivery)
            chat_id = str(details["channel_thread"])
            async with tx(self._app_pool, str(delivery.client_id)) as conn:
                if delivery.conversation_id is None:
                    raise DeliveryFailure("delivery_conversation_missing")
                await lock_conversation(conn, delivery.client_id, delivery.conversation_id)
                if details["send_authority"] in {
                    "automation",
                    "handoff",
                } and not await automation_allowed(
                    conn,
                    delivery.client_id,
                    delivery.conversation_id,
                    delivery.trace_id,
                    handoff=details["send_authority"] == "handoff",
                ):
                    await db.q.fetch_one(
                        "delivery/mark_suppressed.sql", (delivery.client_id, delivery.id), conn=conn
                    )
                    await db.q.execute(
                        "delivery/insert_attempt.sql",
                        (
                            delivery.id,
                            delivery.attempts,
                            None,
                            "human_takeover",
                            self._duration_ms(started_at),
                        ),
                        conn=conn,
                    )
                    return True
                provider_message_id: str | None = None
                if channel == "whatsapp":
                    if not inside_service_window(datetime.now(UTC), details["last_inbound_at"]):
                        raise DeliveryFailure("outside_service_window", terminal=True)
                    await self._throttle(chat_id)
                    wa_receipt = await WhatsAppAdapter.send_text(
                        str(details["phone_number_id"]),
                        bot_token,
                        to=chat_id,
                        text=str(details["body"]),
                        http_client=self._http_client,
                        base_url=str(get_settings().WHATSAPP_GRAPH_BASE_URL),
                    )
                    status_code = wa_receipt.status_code
                    provider_message_id = wa_receipt.provider_message_id
                else:
                    await self._throttle(chat_id)
                    receipt = await TelegramAdapter.send_message(
                        bot_token,
                        chat_id=chat_id,
                        text=str(details["body"]),
                        http_client=self._http_client,
                        buttons=_buttons(details.get("ui")),
                    )
                    status_code = receipt.status_code
                updated = await db.q.fetch_one(
                    "delivery/mark_delivered.sql",
                    (status_code, provider_message_id, delivery.id, delivery.client_id),
                    conn=conn,
                )
                if updated is None:
                    raise RuntimeError("claimed delivery is no longer in delivering state")
                await db.q.execute(
                    "delivery/insert_attempt.sql",
                    (
                        delivery.id,
                        delivery.attempts,
                        status_code,
                        None,
                        self._duration_ms(started_at),
                    ),
                    conn=conn,
                )
        except WhatsAppDeliveryError as exc:
            await self._record_failure(
                delivery,
                error_code=exc.error_code,
                status_code=exc.status_code,
                retry_after_seconds=exc.retry_after_seconds,
                duration_ms=self._duration_ms(started_at),
                terminal=exc.error_code in WHATSAPP_TERMINAL_CODES,
            )
            return True
        except TelegramDeliveryError as exc:
            await self._record_failure(
                delivery,
                error_code=exc.error_code,
                status_code=exc.status_code,
                retry_after_seconds=exc.retry_after_seconds,
                duration_ms=self._duration_ms(started_at),
            )
            return True
        except DeliveryFailure as exc:
            await self._record_failure(
                delivery,
                error_code=exc.error_code,
                status_code=exc.status_code,
                retry_after_seconds=exc.retry_after_seconds,
                duration_ms=self._duration_ms(started_at),
                terminal=exc.terminal,
            )
            return True
        except Exception as exc:
            error_code = type(exc).__name__
            await self._record_failure(
                delivery,
                error_code=error_code,
                status_code=None,
                retry_after_seconds=None,
                duration_ms=self._duration_ms(started_at),
            )
            logger.error(
                "telegram_delivery_failed",
                delivery_id=str(delivery.id),
                trace_id=str(delivery.trace_id) if delivery.trace_id else None,
                error_type=error_code,
                attempt=delivery.attempts,
            )
            return True

        return True

    async def _deliver_web(self, delivery: ClaimedDelivery, started_at: float) -> bool:
        async with tx(self._app_pool, str(delivery.client_id)) as conn:
            details = await db.q.fetch_one(
                "delivery/get_message.sql", (delivery.id, delivery.client_id), conn=conn
            )
            if details is None:
                raise DeliveryFailure("delivery_message_unavailable")
            if details["channel"] != "web":
                return False
            participant = await db.q.fetch_one(
                "delivery/get_web_participant.sql",
                (
                    delivery.client_id,
                    delivery.conversation_id,
                    delivery.endpoint_id,
                    delivery.message_id,
                    delivery.trace_id,
                ),
                conn=conn,
            )
            if participant is None:
                raise DeliveryFailure("web_participant_unavailable")
            updated = await db.q.fetch_one(
                "delivery/mark_delivered.sql",
                (200, None, delivery.id, delivery.client_id),
                conn=conn,
            )
            if updated is None:
                raise RuntimeError("claimed web delivery is no longer delivering")
            await db.q.execute(
                "delivery/insert_attempt.sql",
                (delivery.id, delivery.attempts, 200, None, self._duration_ms(started_at)),
                conn=conn,
            )
        return True

    async def _load_delivery(
        self,
        delivery: ClaimedDelivery,
    ) -> tuple[dict[str, Any], str, str]:
        async with tx(self._app_pool, str(delivery.client_id)) as conn:
            details = await db.q.fetch_one(
                "delivery/get_message.sql",
                (delivery.id, delivery.client_id),
                conn=conn,
            )
            if details is None:
                raise DeliveryFailure("delivery_message_unavailable")
            channel = str(details["channel"])
            token_missing = (
                "telegram_bot_token_unavailable"
                if channel == "telegram"
                else "whatsapp_access_token_unavailable"
            )
            if channel not in {"telegram", "whatsapp"}:
                raise DeliveryFailure("unsupported_delivery_channel")
            if channel == "whatsapp" and not get_settings().whatsapp_enabled:
                raise DeliveryFailure("channel_not_configured")

            binding = await db.q.fetch_one(
                "delivery/check_route.sql",
                (delivery.client_id, delivery.trace_id, delivery.endpoint_id),
                conn=conn,
            )
            if binding is None:
                raise DeliveryFailure("telegram_route_binding_invalid")
            route = await db.q.fetch_one(
                "ingress/get_channel_credentials.sql",
                (delivery.endpoint_id, delivery.client_id),
                conn=conn,
            )
            if route is None or not route["is_enabled"] or route["channel"] != channel:
                raise DeliveryFailure(f"{channel}_endpoint_unavailable")
            encrypted_token = route["access_token"]
            if not isinstance(encrypted_token, str) or not encrypted_token:
                raise DeliveryFailure(token_missing)
            credential_id = route["credential_id"]
            try:
                bot_token = crypto.decrypt(
                    encrypted_token,
                    crypto.credential_aad(
                        "channel_credentials",
                        "access_token",
                        str(credential_id),
                    ),
                )
            except crypto.CryptoError as exc:
                raise DeliveryFailure(token_missing) from exc
            if channel == "whatsapp":
                details = {**details, "phone_number_id": route["shop_id"]}
        return details, bot_token, channel

    async def _record_failure(
        self,
        delivery: ClaimedDelivery,
        *,
        error_code: str,
        status_code: int | None,
        retry_after_seconds: int | None,
        duration_ms: int,
        terminal: bool = False,
    ) -> None:
        dead = terminal or delivery.attempts >= MAX_DELIVERY_ATTEMPTS
        retry_seconds = (
            min(3600, max(1, retry_after_seconds))
            if retry_after_seconds is not None
            else min(300, 2 ** max(delivery.attempts - 1, 0))
        )
        next_attempt_at = datetime.now(UTC) + timedelta(seconds=retry_seconds)
        async with tx(self._app_pool, str(delivery.client_id)) as conn:
            updated = await db.q.fetch_one(
                "delivery/mark_failed.sql",
                (
                    "dead" if dead else "pending",
                    status_code,
                    error_code,
                    next_attempt_at,
                    delivery.id,
                    delivery.client_id,
                ),
                conn=conn,
            )
            if updated is None:
                raise RuntimeError("claimed delivery is no longer in delivering state")
            await db.q.execute(
                "delivery/insert_attempt.sql",
                (
                    delivery.id,
                    delivery.attempts,
                    status_code,
                    error_code,
                    duration_ms,
                ),
                conn=conn,
            )
            if dead:
                envelope = await db.q.fetch_one(
                    "delivery/get_dlq_envelope.sql",
                    (delivery.id, delivery.client_id),
                    conn=conn,
                )
                if envelope is None:
                    raise RuntimeError("cannot dead-letter delivery without its envelope")
                await db.q.execute(
                    "worker/insert_dlq.sql",
                    (
                        delivery.client_id,
                        delivery.trace_id,
                        "delivery",
                        Jsonb(envelope["envelope"]),
                        Jsonb({"error_code": error_code, "retryable": False}),
                    ),
                    conn=conn,
                )
                await notify(
                    conn,
                    client_id=delivery.client_id,
                    event="dlq",
                    priority="high",
                    payload={
                        "stage": "delivery",
                        "reason": error_code,
                        "trace_id": str(delivery.trace_id) if delivery.trace_id else "",
                        "delivery_id": str(delivery.id),
                    },
                    dedupe_key=f"delivery:{delivery.id}",
                )
        logger.error(
            "telegram_delivery_attempt_failed",
            delivery_id=str(delivery.id),
            trace_id=str(delivery.trace_id) if delivery.trace_id else None,
            error_code=error_code,
            status_code=status_code,
            attempt=delivery.attempts,
            dead=dead,
        )

    async def reap(self) -> int:
        """Requeue expired delivery leases and dead-letter exhausted sends."""
        reaped = 0
        async with self._owner_pool.connection() as conn:
            async with conn.transaction():
                candidates = await db.q.fetch_all(
                    "delivery/reap_candidates.sql",
                    (DELIVERY_STALE_SECONDS, REAPER_BATCH_SIZE),
                    conn=conn,
                )
                for delivery in candidates:
                    dead = delivery["attempts"] >= MAX_DELIVERY_ATTEMPTS
                    if dead:
                        updated = await db.q.fetch_one(
                            "delivery/dead_stale.sql",
                            (delivery["id"], delivery["client_id"]),
                            conn=conn,
                        )
                        if updated is None:
                            continue
                        envelope = await db.q.fetch_one(
                            "delivery/get_dlq_envelope.sql",
                            (delivery["id"], delivery["client_id"]),
                            conn=conn,
                        )
                        if envelope is None:
                            raise RuntimeError("cannot dead-letter delivery without its envelope")
                        await db.q.execute(
                            "worker/insert_dlq.sql",
                            (
                                delivery["client_id"],
                                delivery["trace_id"],
                                "delivery",
                                Jsonb(envelope["envelope"]),
                                Jsonb(
                                    {
                                        "error_code": "delivery_lease_expired",
                                        "retryable": False,
                                    }
                                ),
                            ),
                            conn=conn,
                        )
                        await notify(
                            conn,
                            client_id=delivery["client_id"],
                            event="dlq",
                            priority="high",
                            payload={
                                "stage": "delivery",
                                "reason": "delivery_lease_expired",
                                "trace_id": str(delivery["trace_id"])
                                if delivery["trace_id"]
                                else "",
                                "delivery_id": str(delivery["id"]),
                            },
                            dedupe_key=f"delivery:{delivery['id']}",
                        )
                    else:
                        retry_at = datetime.now(UTC) + timedelta(
                            seconds=min(300, 2 ** max(delivery["attempts"] - 1, 0))
                        )
                        updated = await db.q.fetch_one(
                            "delivery/requeue_stale.sql",
                            (retry_at, delivery["id"], delivery["client_id"]),
                            conn=conn,
                        )
                        if updated is None:
                            continue
                    await db.q.execute(
                        "delivery/insert_attempt.sql",
                        (
                            delivery["id"],
                            delivery["attempts"],
                            delivery["last_status_code"],
                            "delivery lease expired",
                            None,
                        ),
                        conn=conn,
                    )
                    reaped += 1
        return reaped

    @staticmethod
    def _duration_ms(started_at: float) -> int:
        return max(0, round((time.monotonic() - started_at) * 1000))

    async def _throttle(self, chat_id: str) -> None:
        """Respect Telegram's per-chat and global bot send rates."""
        now = time.monotonic()
        ready_at = max(
            self._next_global_send_at,
            self._next_chat_send_at.get(chat_id, 0.0),
        )
        if ready_at > now:
            await asyncio.sleep(ready_at - now)
        sent_at = time.monotonic()
        self._next_global_send_at = sent_at + (1 / 30)
        self._next_chat_send_at[chat_id] = sent_at + 1


def _buttons(raw: object) -> list[tuple[str, str]]:
    """Render a stored ``Ui`` as Telegram inline buttons (customer callbacks are compact ids)."""
    if not raw:
        return []
    ui = Ui.model_validate(raw)
    options = (
        [ui.confirmation.accept, ui.confirmation.reject] if ui.confirmation else list(ui.options)
    )
    result: list[tuple[str, str]] = []
    for option in options:
        if option.legacy_callback_data:
            data = option.legacy_callback_data
        elif option.action_id:
            data = option.action_id
        else:
            continue
        result.append((option.label, data))
    return result


async def main() -> None:  # pragma: no cover - process entrypoint
    """Start the outbox drain/reaper and its liveness heartbeat."""
    settings = get_settings()
    owner_pool = db.build_pool(str(settings.LG_DATABASE_URL), max_size=4)
    app_pool = db.build_pool(str(settings.DATABASE_URL), max_size=4)
    await owner_pool.open(wait=True)
    await app_pool.open(wait=True)
    timeout = httpx.Timeout(connect=3, read=10, write=10, pool=3)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            worker = DeliveryWorker(owner_pool, app_pool, client)
            await liveness_loop(DELIVERY_ALIVE_PATH, do_work=worker.do_work)
    finally:
        await app_pool.close()
        await owner_pool.close()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
