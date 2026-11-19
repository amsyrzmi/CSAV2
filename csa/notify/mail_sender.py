"""Deliver queued staff notifications to email targets from the worker.

The Telegram sender (:mod:`csa.notify.sender`) never claims an email target's row
(``notify/claim.sql``); this loop claims them through ``notify/claim_email.sql`` and shares its
quiet hours, attempt cap, backoff, lease reaper and failure codes. Mail goes out through the
worker's one :class:`~csa.console.mailer.Mailer`, so the relay switch is the same as for
invitations.
Logs carry the notification id, event and an error category, never an address or a body.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from csa.console.mailer import Mailer, OutgoingMail
from csa.core import db
from csa.core.tenancy import tx
from csa.notify.email_render import render_target_email
from csa.notify.sender import (
    NotificationFailure,
    quiet_until,
    reap_stale_notifications,
    record_failure,
)

logger = structlog.get_logger(__name__)

OverdueCheck = Callable[[AsyncConnection[Any], UUID, dict[str, Any]], Awaitable[bool]]

_REAP_EVERY_SECONDS = 60


class NotificationMailer:
    """Claim due email-target notifications, send each, back off on failure, dead-letter at cap."""

    def __init__(
        self,
        owner_pool: AsyncConnectionPool,
        app_pool: AsyncConnectionPool,
        mailer: Mailer,
        base_url: str,
        *,
        still_overdue: OverdueCheck | None = None,
    ) -> None:
        self._owner_pool = owner_pool
        self._app_pool = app_pool
        self._mailer = mailer
        self._base_url = base_url
        # Not used yet: the person-email path (a later task) asks it whether an overdue
        # conversation is still overdue before sending.
        self._still_overdue = still_overdue
        self._next_reap = 0.0

    @property
    def relay_enabled(self) -> bool:
        return self._mailer.enabled

    async def do_work(self) -> bool:
        """One unit of work; True when something was reaped or sent (the caller skips its sleep)."""
        reaped = await self._maintain()
        # The person-email path joins here: `... or await self._process_person_email()`.
        return await self._process_staff_email() or reaped > 0

    async def _maintain(self) -> int:
        """Reap stale leases on a cadence. The Telegram sender reaps the same rows (the query is
        channel-agnostic and idempotent), so a stuck email row is freed even if either loop
        is down. ``expire_due`` runs only in the Telegram sender."""
        now = time.monotonic()
        if now < self._next_reap:
            return 0
        self._next_reap = now + _REAP_EVERY_SECONDS
        return await reap_stale_notifications(self._owner_pool)

    async def _process_staff_email(self) -> bool:
        async with self._owner_pool.connection() as conn:
            row = await db.q.fetch_one("notify/claim_email.sql", conn=conn)
        if row is None:
            return False
        try:
            await self._send(row)
        except NotificationFailure as exc:
            await record_failure(self._app_pool, row, exc.error_code, exc.retry_after_seconds)
        except Exception as exc:
            await record_failure(self._app_pool, row, type(exc).__name__, None)
        return True

    async def _send(self, row: dict[str, Any]) -> None:
        client_id = row["client_id"]
        async with tx(self._app_pool, str(client_id)) as conn:
            target = await db.q.fetch_one(
                "notify/get_target_address.sql", (client_id, row["target_id"]), conn=conn
            )
            if target is None:  # missing or switched off
                raise NotificationFailure("target_unavailable")
            if target["channel"] != "email":
                raise NotificationFailure("unsupported_target_channel")
            if target["verified_at"] is None:
                # Not dropped: it retries and dies like any failure, and verifying the address
                # later lets a retry through.
                raise NotificationFailure("target_unverified")
            resume_at = quiet_until(target["quiet_hours"], row["priority"], datetime.now(UTC))
            if resume_at is not None:
                await db.q.fetch_one(
                    "notify/defer.sql", (resume_at, row["id"], client_id), conn=conn
                )
                return
        rendered = render_target_email(
            row["event"], row["priority"], row["payload"], str(target["language"]), self._base_url
        )
        outcome = await self._mailer.send(
            OutgoingMail(
                to=str(target["address"]),
                subject=rendered.subject,
                text=rendered.text,
                html=rendered.html,
            )
        )
        if outcome != "sent":
            # The category is the error code. smtp_disabled is retried like the rest: a relay
            # switched on later still delivers the row, and it dies at the cap if it never is.
            raise NotificationFailure(outcome)
        async with tx(self._app_pool, str(client_id)) as conn:
            updated = await db.q.fetch_one(
                "notify/mark_delivered.sql", (row["id"], client_id), conn=conn
            )
            if updated is None:
                raise RuntimeError("claimed notification is no longer delivering")


async def notification_mail_loop(
    mailer_worker: NotificationMailer, *, poll_seconds: float = 2.0
) -> None:
    """Drain the mail queue until cancelled; sleeps only when idle; errors are logged, not fatal."""
    if not mailer_worker.relay_enabled:
        logger.info("notification_mail_relay_disabled")
    while True:
        busy = False
        try:
            busy = await mailer_worker.do_work()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("notification_mail_loop_error")
        if not busy:
            await asyncio.sleep(poll_seconds)
