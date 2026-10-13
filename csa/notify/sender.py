"""Deliver queued staff notifications to their targets (LG05 notifier sender)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import structlog
from psycopg_pool import AsyncConnectionPool

from csa.approval.service import expire_due
from csa.channels.telegram import TelegramAdapter, TelegramDeliveryError
from csa.contracts.callbacks import StaffCallbackCodec
from csa.core import crypto, db
from csa.core.tenancy import tx

logger = structlog.get_logger(__name__)

MAX_NOTIFICATION_ATTEMPTS = 5
NOTIFICATION_STALE_SECONDS = 180
ACTION_TTL = timedelta(hours=24)
_HEADLINES = {
    "escalation": "Escalation",
    "approval_needed": "Order needs approval",
    "order_submitted": "Order submitted",
    "dlq": "Dead-letter item",
    "circuit_opened": "Agent circuit opened",
}


class NotificationFailure(Exception):
    def __init__(self, error_code: str, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.retry_after_seconds = retry_after_seconds


def format_notification(event: str, priority: str, payload: dict[str, Any]) -> str:
    """Render a short staff message; never includes raw customer text."""
    headline = _HEADLINES.get(event, event)
    if event == "approval_needed" and payload.get("approval_id"):
        headline = "AI reply needs approval"
    lines = [f"[{priority.upper()}] {headline}"]
    for key in ("text", "order_id", "ticket_id", "reason", "agent", "stage", "trace_id"):
        value = payload.get(key)
        if value:
            lines.append(f"{key}: {value}")
    return "\n".join(lines)[:4000]


def quiet_until(quiet_hours: Any, priority: str, now: datetime) -> datetime | None:
    """Return when quiet hours end, or None if sending is allowed now.

    Shape: ``{"start": "22:00", "end": "07:00", "tz": "Asia/Kuala_Lumpur"}``.
    Urgent notifications always go through.
    """
    if not quiet_hours or priority == "urgent":
        return None
    zone = ZoneInfo(str(quiet_hours.get("tz", "UTC")))
    local = now.astimezone(zone)
    start_h, start_m = map(int, str(quiet_hours["start"]).split(":"))
    end_h, end_m = map(int, str(quiet_hours["end"]).split(":"))
    start = local.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    end = local.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    if start <= end:
        inside = start <= local < end
        end_at = end
    else:
        inside = local >= start or local < end
        end_at = end if local < end else end + timedelta(days=1)
    return end_at.astimezone(UTC) if inside else None


class StaffNotificationSender:
    """Claim due notifications, send via Telegram, retry with backoff, dead-letter at the cap."""

    def __init__(
        self,
        owner_pool: AsyncConnectionPool,
        app_pool: AsyncConnectionPool,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._owner_pool = owner_pool
        self._app_pool = app_pool
        self._http_client = http_client
        self._next_reap = 0.0
        self._next_expiry = 0.0

    async def process_once(self) -> bool:
        async with self._owner_pool.connection() as conn:
            row = await db.q.fetch_one("notify/claim.sql", conn=conn)
        if row is None:
            return False
        try:
            await self._send(row)
        except TelegramDeliveryError as exc:
            await self._fail(row, exc.error_code, exc.retry_after_seconds)
        except NotificationFailure as exc:
            await self._fail(row, exc.error_code, exc.retry_after_seconds)
        except Exception as exc:
            await self._fail(row, type(exc).__name__, None)
        return True

    async def reap(self) -> int:
        async with self._owner_pool.connection() as conn:
            async with conn.transaction():
                rows = await db.q.fetch_all(
                    "notify/reap_stale.sql",
                    (MAX_NOTIFICATION_ATTEMPTS, NOTIFICATION_STALE_SECONDS),
                    conn=conn,
                )
        for item in rows:
            if item["status"] == "dead":
                logger.error("staff_notification_dead", notification_id=str(item["id"]))
        return len(rows)

    async def _send(self, row: dict[str, Any]) -> None:
        client_id = row["client_id"]
        async with tx(self._app_pool, str(client_id)) as conn:
            target = await db.q.fetch_one(
                "notify/get_target_address.sql", (client_id, row["target_id"]), conn=conn
            )
            if target is None:
                raise NotificationFailure("target_unavailable")
            if target["channel"] != "telegram":
                raise NotificationFailure("unsupported_target_channel")
            resume_at = quiet_until(target["quiet_hours"], row["priority"], datetime.now(UTC))
            if resume_at is not None:
                await db.q.fetch_one(
                    "notify/defer.sql", (resume_at, row["id"], client_id), conn=conn
                )
                return
            credential = await db.q.fetch_one(
                "notify/get_telegram_credential.sql", (client_id,), conn=conn
            )
            if credential is None:
                raise NotificationFailure("telegram_credential_unavailable")
            try:
                bot_token = crypto.decrypt(
                    credential["access_token"],
                    crypto.credential_aad(
                        "channel_credentials", "access_token", str(credential["credential_id"])
                    ),
                )
            except crypto.CryptoError as exc:
                raise NotificationFailure("telegram_bot_token_unavailable") from exc

            buttons: list[tuple[str, str]] | None = None
            payload = row["payload"]
            if row["event"] == "approval_needed" and payload.get("approval_id"):
                buttons = []
                for action, label in (("approve_reply", "Send reply"), ("reject_reply", "Reject")):
                    created = await db.q.fetch_one(
                        "staff/upsert_reply_actions.sql",
                        (
                            client_id,
                            row["id"],
                            row["target_id"],
                            payload["approval_id"],
                            action,
                            datetime.now(UTC) + ACTION_TTL,
                        ),
                        conn=conn,
                    )
                    if created is None:
                        raise RuntimeError("staff action was not created")
                    buttons.append((label, StaffCallbackCodec.encode(created["id"])))

            if row["event"] == "approval_needed" and payload.get("order_id"):
                buttons = []
                for action, label in (("approve", "Approve"), ("reject", "Reject")):
                    created = await db.q.fetch_one(
                        "staff/upsert_action.sql",
                        (
                            client_id,
                            row["id"],
                            row["target_id"],
                            str(payload["order_id"]),
                            action,
                            datetime.now(UTC) + ACTION_TTL,
                        ),
                        conn=conn,
                    )
                    if created is None:
                        raise RuntimeError("staff action was not created")
                    buttons.append((label, StaffCallbackCodec.encode(created["id"])))

            if row["event"] == "escalation" and payload.get("conversation_id"):
                created = await db.q.fetch_one(
                    "staff/upsert_resume_action.sql",
                    (
                        client_id,
                        row["id"],
                        row["target_id"],
                        payload["conversation_id"],
                        datetime.now(UTC) + ACTION_TTL,
                    ),
                    conn=conn,
                )
                if created is None:
                    raise RuntimeError("staff action was not created")
                buttons = [("Resume AI", StaffCallbackCodec.encode(created["id"]))]

        await TelegramAdapter.send_message(
            bot_token,
            chat_id=str(target["address"]),
            text=format_notification(row["event"], row["priority"], payload),
            http_client=self._http_client,
            buttons=buttons,
        )
        async with tx(self._app_pool, str(client_id)) as conn:
            updated = await db.q.fetch_one(
                "notify/mark_delivered.sql", (row["id"], client_id), conn=conn
            )
            if updated is None:
                raise RuntimeError("claimed notification is no longer delivering")

    async def _fail(self, row: dict[str, Any], code: str, retry_after: int | None) -> None:
        dead = row["attempts"] >= MAX_NOTIFICATION_ATTEMPTS
        delay = (
            min(3600, max(1, retry_after))
            if retry_after is not None
            else min(300, 2 ** max(row["attempts"] - 1, 0))
        )
        async with tx(self._app_pool, str(row["client_id"])) as conn:
            await db.q.fetch_one(
                "notify/mark_failed.sql",
                (
                    "dead" if dead else "pending",
                    code,
                    datetime.now(UTC) + timedelta(seconds=delay),
                    row["id"],
                    row["client_id"],
                ),
                conn=conn,
            )
        logger.error(
            "staff_notification_failed",
            notification_id=str(row["id"]),
            notification_event=row["event"],
            error_code=code,
            attempt=row["attempts"],
            dead=dead,
        )

    async def do_work(self) -> bool:
        now = time.monotonic()
        reaped = 0
        if now >= self._next_reap:
            reaped = await self.reap()
            self._next_reap = now + 60
        expired = 0
        if now >= self._next_expiry:
            expired = await expire_due(self._owner_pool, self._app_pool)
            self._next_expiry = now + 30
        return await self.process_once() or reaped > 0 or expired > 0
