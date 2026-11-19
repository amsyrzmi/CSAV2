"""Staff-target email delivery from the worker (disposable Postgres, synthetic data, fake relay)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import psycopg
import pytest
from csa.console.mailer import Mailer, OutgoingMail
from csa.core import db
from csa.core.tenancy import tx
from csa.notify.email_render import render_target_email
from csa.notify.mail_sender import NotificationMailer, notification_mail_loop
from csa.notify.notifier import notify
from csa.notify.sender import StaffNotificationSender
from psycopg.types.json import Jsonb

from tests.conftest import requires_docker
from tests.test_order_agent import World, world  # noqa: F401
from tests.test_staff_path import _quiesce_other_tenants

pytestmark = [requires_docker, pytest.mark.asyncio]

BASE = "https://console.example.test"
PAYLOAD: dict[str, Any] = {"customer": "Synthetic Customer", "conversation_id": str(uuid.uuid4())}


class FakeTransport:
    def __init__(self, fail: Exception | None = None) -> None:
        self.sent: list[OutgoingMail] = []
        self.fail = fail

    def send(self, mail: OutgoingMail, sender: str) -> None:
        if self.fail is not None:
            raise self.fail
        self.sent.append(mail)


@pytest.fixture
async def owner_pool(world: World) -> Any:  # noqa: F811
    pool = db.build_pool(world.owner_dsn, max_size=2)
    await pool.open(wait=True)
    yield pool
    await pool.close()


def _target(
    world: World,  # noqa: F811
    *,
    channel: str = "email",
    verified: bool = True,
    enabled: bool = True,
    quiet_hours: dict[str, str] | None = None,
    language: str = "en",
) -> uuid.UUID:
    target_id = uuid.uuid4()
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO staff_notification_targets (id, client_id, channel, address, events, "
            "is_enabled, verified_at, quiet_hours, language) "
            "VALUES (%s,%s,%s,%s,'{escalation}'::text[],%s,%s,%s,%s)",
            (
                target_id,
                world.client_id,
                channel,
                f"{target_id.hex[:8]}@example.test" if channel == "email" else "999",
                enabled,
                datetime.now(UTC) if verified else None,
                Jsonb(quiet_hours) if quiet_hours else None,
                language,
            ),
        )
    return target_id


def _row(
    world: World,  # noqa: F811
    target_id: uuid.UUID,
    *,
    priority: str = "normal",
    key: str | None = None,
) -> uuid.UUID:
    notification_id = uuid.uuid4()
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO staff_notifications (id, client_id, target_id, event, priority, payload, "
            "idempotency_key) VALUES (%s,%s,%s,'escalation',%s,%s,%s)",
            (
                notification_id,
                world.client_id,
                target_id,
                priority,
                Jsonb(PAYLOAD),
                key or str(notification_id),
            ),
        )
    return notification_id


def _state(world: World, notification_id: uuid.UUID) -> tuple[Any, ...]:  # noqa: F811
    return world.q(
        "SELECT status, attempts, last_error, next_attempt_at, held_at "
        "FROM staff_notifications WHERE id=%s",
        notification_id,
    )[0]


def _mailer(
    world: World,  # noqa: F811
    owner_pool: Any,
    transport: FakeTransport | None,
) -> NotificationMailer:
    _quiesce_other_tenants(world)
    mailer = Mailer(transport, "noreply@example.test")
    return NotificationMailer(owner_pool, world.pool, mailer, BASE)


def _telegram_sender(world: World, owner_pool: Any) -> StaffNotificationSender:  # noqa: F811
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    )
    return StaffNotificationSender(owner_pool, world.pool, client)


async def test_email_target_row_is_sent_and_marked_delivered(world: World, owner_pool: Any) -> None:  # noqa: F811
    transport = FakeTransport()
    target_id = _target(world, language="ms")
    address = world.q("SELECT address FROM staff_notification_targets WHERE id=%s", target_id)[0][0]
    notification_id = _row(world, target_id, priority="high")
    sender = _mailer(world, owner_pool, transport)
    assert await sender.do_work()
    (mail,) = transport.sent
    expected = render_target_email("escalation", "high", PAYLOAD, "ms", BASE)
    assert (mail.to, mail.subject, mail.text, mail.html) == (
        address,
        expected.subject,
        expected.text,
        expected.html,
    )
    status, attempts, last_error, _, held_at = _state(world, notification_id)
    assert (status, attempts, last_error, held_at) == ("delivered", 1, None, None)
    assert not await sender.do_work()  # nothing left to do


async def test_unverified_email_target_is_not_sent(world: World, owner_pool: Any) -> None:  # noqa: F811
    transport = FakeTransport()
    notification_id = _row(world, _target(world, verified=False))
    sender = _mailer(world, owner_pool, transport)
    assert await sender.do_work()
    assert transport.sent == []
    status, attempts, last_error, _, _ = _state(world, notification_id)
    assert (status, attempts, last_error) == ("pending", 1, "target_unverified")
    # Verifying the address later lets the retry through.
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE staff_notification_targets SET verified_at = now() "
            "WHERE id = (SELECT target_id FROM staff_notifications WHERE id=%s)",
            (notification_id,),
        )
        conn.execute(
            "UPDATE staff_notifications SET next_attempt_at = now() WHERE id=%s",
            (notification_id,),
        )
    assert await sender.do_work()
    assert len(transport.sent) == 1
    assert _state(world, notification_id)[0] == "delivered"


async def test_disabled_target_is_not_sent(world: World, owner_pool: Any) -> None:  # noqa: F811
    transport = FakeTransport()
    notification_id = _row(world, _target(world, enabled=False))
    sender = _mailer(world, owner_pool, transport)
    assert await sender.do_work()
    assert transport.sent == []
    status, _, last_error, _, _ = _state(world, notification_id)
    assert (status, last_error) == ("pending", "target_unavailable")


async def test_failure_retries_with_backoff_then_dead_letters(
    world: World,  # noqa: F811
    owner_pool: Any,
) -> None:
    transport = FakeTransport(fail=RuntimeError("relay down"))
    notification_id = _row(world, _target(world))
    sender = _mailer(world, owner_pool, transport)
    delays: list[int] = []
    for attempt in range(1, 6):
        before = datetime.now(UTC)
        assert await sender.do_work()
        status, attempts, last_error, next_at, _ = _state(world, notification_id)
        assert attempts == attempt and last_error == "smtp_error"
        if attempt < 5:
            assert status == "pending"
            delays.append(round((next_at - before).total_seconds()))
            with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
                conn.execute(
                    "UPDATE staff_notifications SET next_attempt_at = now() WHERE id=%s",
                    (notification_id,),
                )
    assert delays == [1, 2, 4, 8]
    assert _state(world, notification_id)[0] == "dead"


async def test_relay_switched_off_retries_and_a_later_relay_delivers(
    world: World,  # noqa: F811
    owner_pool: Any,
) -> None:
    notification_id = _row(world, _target(world))
    sender = _mailer(world, owner_pool, None)  # smtp_disabled
    assert await sender.do_work()
    status, attempts, last_error, _, _ = _state(world, notification_id)
    assert (status, attempts, last_error) == ("pending", 1, "smtp_disabled")
    transport = FakeTransport()
    sender = _mailer(world, owner_pool, transport)
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE staff_notifications SET next_attempt_at = now() WHERE id=%s",
            (notification_id,),
        )
    assert await sender.do_work()
    assert len(transport.sent) == 1 and _state(world, notification_id)[0] == "delivered"


async def test_replay_does_not_duplicate(world: World, owner_pool: Any) -> None:  # noqa: F811
    transport = FakeTransport()
    target_id = _target(world)
    queued = 0
    for _ in range(2):
        async with tx(world.pool, str(world.client_id)) as conn:
            queued += await notify(
                conn,
                client_id=world.client_id,
                event="escalation",
                priority="normal",
                payload=PAYLOAD,
                dedupe_key="replay-1",
            )
    assert queued == 1
    sender = _mailer(world, owner_pool, transport)
    assert await sender.do_work()
    assert not await sender.do_work()
    assert len(transport.sent) == 1
    assert world.q("SELECT count(*) FROM staff_notifications WHERE target_id=%s", target_id) == [
        (1,)
    ]


async def test_quiet_hours_defers_without_consuming_attempts(
    world: World,  # noqa: F811
    owner_pool: Any,
) -> None:
    transport = FakeTransport()
    now = datetime.now(UTC)
    window = {
        "start": (now - timedelta(minutes=30)).strftime("%H:%M"),
        "end": (now + timedelta(minutes=30)).strftime("%H:%M"),
        "tz": "UTC",
    }
    target_id = _target(world, quiet_hours=window)
    normal = _row(world, target_id)
    sender = _mailer(world, owner_pool, transport)
    assert await sender.do_work()
    status, attempts, _, next_at, held_at = _state(world, normal)
    assert (status, attempts) == ("pending", 0)
    assert held_at is not None and abs((held_at - now).total_seconds()) < 30
    assert timedelta(minutes=29) < next_at - now < timedelta(minutes=31)
    assert transport.sent == []
    urgent = _row(world, target_id, priority="urgent")
    assert await sender.do_work()
    assert len(transport.sent) == 1 and _state(world, urgent)[0] == "delivered"


async def test_telegram_sender_never_claims_email_rows(world: World, owner_pool: Any) -> None:  # noqa: F811
    _quiesce_other_tenants(world)
    notification_id = _row(world, _target(world))
    assert not await _telegram_sender(world, owner_pool).process_once()
    assert _state(world, notification_id)[:2] == ("pending", 0)


async def test_mail_sender_never_claims_telegram_rows(world: World, owner_pool: Any) -> None:  # noqa: F811
    transport = FakeTransport()
    notification_id = _row(world, _target(world, channel="telegram"))
    sender = _mailer(world, owner_pool, transport)
    assert not await sender.do_work()
    assert transport.sent == [] and _state(world, notification_id)[:2] == ("pending", 0)


async def test_unsupported_channel_still_fails_in_the_telegram_path(
    world: World,  # noqa: F811
    owner_pool: Any,
) -> None:
    _quiesce_other_tenants(world)
    notification_id = _row(world, _target(world, channel="webhook"))
    assert await _telegram_sender(world, owner_pool).process_once()
    assert _state(world, notification_id)[2] == "unsupported_target_channel"


async def test_deferred_telegram_row_gets_held_at(world: World, owner_pool: Any) -> None:  # noqa: F811
    _quiesce_other_tenants(world)
    now = datetime.now(UTC)
    window = {
        "start": (now - timedelta(minutes=30)).strftime("%H:%M"),
        "end": (now + timedelta(minutes=30)).strftime("%H:%M"),
        "tz": "UTC",
    }
    notification_id = _row(world, _target(world, channel="telegram", quiet_hours=window))
    assert await _telegram_sender(world, owner_pool).process_once()
    status, attempts, _, _, held_at = _state(world, notification_id)
    assert (status, attempts) == ("pending", 0) and held_at is not None


async def test_stale_claim_is_reaped(world: World, owner_pool: Any) -> None:  # noqa: F811
    transport = FakeTransport()
    notification_id = _row(world, _target(world))
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE staff_notifications SET status='delivering', attempts=1, "
            "locked_at = now() - interval '1 hour' WHERE id=%s",
            (notification_id,),
        )
    sender = _mailer(world, owner_pool, transport)
    assert await sender.do_work()  # reaps, then sends the freed row
    assert len(transport.sent) == 1
    assert _state(world, notification_id)[:2] == ("delivered", 2)


async def test_loop_survives_an_exception_and_stops_when_cancelled(
    world: World,  # noqa: F811
    owner_pool: Any,
) -> None:
    sender = _mailer(world, owner_pool, FakeTransport())
    calls = 0

    async def flaky() -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        return False

    sender.do_work = flaky  # type: ignore[method-assign]
    task = asyncio.create_task(notification_mail_loop(sender, poll_seconds=0.01))
    async with asyncio.timeout(10):  # the first failure is logged with its traceback
        while calls < 2:
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls >= 2
