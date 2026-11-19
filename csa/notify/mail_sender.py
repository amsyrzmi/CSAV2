"""Deliver queued notification emails from the worker: staff email targets and people.

The Telegram sender (:mod:`csa.notify.sender`) never claims an email target's row
(``notify/claim.sql``); this loop claims them through ``notify/claim_email.sql`` and shares its
quiet hours, attempt cap, backoff, lease reaper and failure codes. Mail goes out through the
worker's one :class:`~csa.console.mailer.Mailer`, so the relay switch is the same as for
invitations.
A person's own Inbox notification email is a row of ``console_email_outbox`` (queued by
:func:`csa.notify.people.notify_people` for people who switched email on). It carries ids and keys
only; the sign-in address is looked up through ``console_member_emails`` at send time and lives
only in the in-memory :class:`~csa.console.mailer.OutgoingMail`. A recipient with no address (left
the company, disabled, a platform admin) is terminal: the row dies at once, nothing is retried.
Logs carry the notification id, event and an error category, never an address or a body.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from psycopg_pool import AsyncConnectionPool

from csa.console.mailer import Mailer, OutgoingMail
from csa.core import db
from csa.core.tenancy import tx
from csa.notify.email_render import render_person_email, render_target_email
from csa.notify.failures import (
    clear_person_failure_safely,
    clear_target_failure_safely,
    report_dead_person_email_safely,
)
from csa.notify.recheck import OverdueCheck, cancelled_as_moved_on
from csa.notify.sender import (
    MAX_NOTIFICATION_ATTEMPTS,
    NOTIFICATION_STALE_SECONDS,
    NotificationFailure,
    quiet_until,
    reap_stale_notifications,
    record_failure,
)

logger = structlog.get_logger(__name__)

_REAP_EVERY_SECONDS = 60
RECIPIENT_UNAVAILABLE = "recipient_unavailable"


async def reap_stale_person_emails(
    owner_pool: AsyncConnectionPool, app_pool: AsyncConnectionPool | None = None
) -> int:
    """Return stale ``delivering`` person-email rows to ``pending`` (or ``dead`` at the cap).

    Returns how many moved. Idempotent, like :func:`csa.notify.sender.reap_stale_notifications`.
    With ``app_pool`` the person of each row this call killed gets one bell notice per failure
    episode (:mod:`csa.notify.failures`).
    """
    async with owner_pool.connection() as conn:
        async with conn.transaction():
            rows = await db.q.fetch_all(
                "notify/email_outbox_reap.sql",
                (MAX_NOTIFICATION_ATTEMPTS, NOTIFICATION_STALE_SECONDS),
                conn=conn,
            )
    for item in rows:
        if item["status"] == "dead":
            logger.error("person_email_dead", outbox_id=str(item["id"]), error_code="lease_expired")
            if app_pool is not None:
                await _report_reaped_person(app_pool, item["client_id"], item["user_id"])
    return len(rows)


async def _report_reaped_person(
    app_pool: AsyncConnectionPool, client_id: Any, user_id: Any
) -> None:
    """Report a person whose email the reaper just killed; an error is logged and swallowed."""
    try:
        async with tx(app_pool, str(client_id)) as conn:
            await report_dead_person_email_safely(conn, client_id, user_id)
    except Exception:
        logger.exception("person_failure_report_failed", user_id=str(user_id))


async def record_person_failure(
    app_pool: AsyncConnectionPool, row: dict[str, Any], code: str
) -> None:
    """Mark a claimed person-email row pending with backoff, or dead at the attempt cap."""
    dead = row["attempts"] >= MAX_NOTIFICATION_ATTEMPTS
    delay = min(300, 2 ** max(row["attempts"] - 1, 0))
    async with tx(app_pool, str(row["client_id"])) as conn:
        marked = await db.q.fetch_one(
            "notify/email_outbox_mark_failed.sql",
            (
                "dead" if dead else "pending",
                code,
                datetime.now(UTC) + timedelta(seconds=delay),
                row["id"],
                row["client_id"],
            ),
            conn=conn,
        )
        if dead and marked is not None:
            # Only when this UPDATE killed the row: not a death another worker caused.
            await report_dead_person_email_safely(conn, row["client_id"], row["user_id"])
    logger.error(
        "person_email_failed",
        outbox_id=str(row["id"]),
        notification_kind=row["kind"],
        error_code=code,
        attempt=row["attempts"],
        dead=dead,
    )


async def record_person_unavailable(app_pool: AsyncConnectionPool, row: dict[str, Any]) -> None:
    """Kill a claimed row whose recipient has no address, whatever its attempt count (terminal)."""
    async with tx(app_pool, str(row["client_id"])) as conn:
        await db.q.fetch_one(
            "notify/email_outbox_mark_dead.sql",
            (RECIPIENT_UNAVAILABLE, row["id"], row["client_id"]),
            conn=conn,
        )
    logger.warning(
        "person_email_recipient_unavailable",
        outbox_id=str(row["id"]),
        notification_kind=row["kind"],
        error_code=RECIPIENT_UNAVAILABLE,
    )


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
        # Asked about a released inbox.overdue staff row before it is mailed (csa.notify.recheck).
        self._still_overdue = still_overdue
        self._next_reap = 0.0

    @property
    def relay_enabled(self) -> bool:
        return self._mailer.enabled

    async def do_work(self) -> bool:
        """One unit of work; True when something was reaped or sent (the caller skips its sleep)."""
        reaped = await self._maintain()
        # Both paths run every call, so a busy staff queue never starves person email.
        staff = await self._process_staff_email()
        person = await self._process_person_email()
        return staff or person or reaped > 0

    async def _maintain(self) -> int:
        """Reap stale leases on a cadence, staff rows and the person outbox. The Telegram sender
        reaps the staff rows too (the query is channel-agnostic and idempotent), so a stuck email
        row is freed even if either loop is down. ``expire_due`` runs only in the Telegram
        sender."""
        now = time.monotonic()
        if now < self._next_reap:
            return 0
        self._next_reap = now + _REAP_EVERY_SECONDS
        return await reap_stale_notifications(
            self._owner_pool, self._app_pool
        ) + await reap_stale_person_emails(self._owner_pool, self._app_pool)

    async def _process_staff_email(self) -> bool:
        async with self._owner_pool.connection() as conn:
            row = await db.q.fetch_one("notify/claim_email.sql", conn=conn)
        if row is None:
            return False
        try:
            if await cancelled_as_moved_on(self._app_pool, row, self._still_overdue):
                return True
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
            await clear_target_failure_safely(conn, client_id, row["target_id"])

    async def _process_person_email(self) -> bool:
        async with self._owner_pool.connection() as conn:
            row = await db.q.fetch_one("notify/email_outbox_claim.sql", conn=conn)
        if row is None:
            return False
        try:
            await self._send_person(row)
        except NotificationFailure as exc:
            await record_person_failure(self._app_pool, row, exc.error_code)
        except Exception as exc:
            await record_person_failure(self._app_pool, row, type(exc).__name__)
        return True

    async def _send_person(self, row: dict[str, Any]) -> None:
        client_id = row["client_id"]
        async with tx(self._app_pool, str(client_id)) as conn:
            recipient = await db.q.fetch_one(
                "notify/member_emails.sql", (client_id, row["user_id"]), conn=conn
            )
        if recipient is None:  # left, disabled or a platform admin: terminal, never retried
            await record_person_unavailable(self._app_pool, row)
            return
        rendered = render_person_email(row["kind"], row["params"], row["link"], self._base_url)
        outcome = await self._mailer.send(
            OutgoingMail(
                to=str(recipient["email"]),
                subject=rendered.subject,
                text=rendered.text,
                html=rendered.html,
            )
        )
        if outcome != "sent":
            # smtp_disabled retries like the rest: a relay switched on later still delivers.
            raise NotificationFailure(outcome)
        async with tx(self._app_pool, str(client_id)) as conn:
            updated = await db.q.fetch_one(
                "notify/email_outbox_mark_delivered.sql", (row["id"], client_id), conn=conn
            )
            if updated is None:
                raise RuntimeError("claimed email is no longer delivering")
            await clear_person_failure_safely(conn, client_id, row["user_id"])


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
