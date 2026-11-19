"""A held inbox.overdue alert is re-checked when quiet hours release it (inbox refinement 3,
task 7; design 5.1). Disposable Postgres, synthetic data, a fake relay and a mock Telegram API.

Conversations are real queue items (the helpers of the inbox queue tests); the check under test
is the real :func:`csa.inbox_queue.overdue_check.still_overdue` unless a test needs a callable that
records or raises. Both senders run the same rule, so most tests run on each.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import psycopg
import pytest
from csa.console.mailer import Mailer, OutgoingMail
from csa.core import db
from csa.core.tenancy import tx
from csa.inbox_queue import transitions
from csa.inbox_queue.overdue_check import still_overdue
from csa.notify.mail_sender import NotificationMailer
from csa.notify.recheck import OverdueCheck
from csa.notify.sender import StaffNotificationSender
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from tests.conftest import requires_docker
from tests.test_console_notifications_schema import member
from tests.test_inbox_queue_triggers import Seeded, _needs_staff, _seed, _state
from tests.test_order_agent import World, world  # noqa: F401
from tests.test_staff_path import (  # noqa: F401
    _crypto_key,
    _quiesce_other_tenants,
    _seed_endpoint,
)
from tests.test_training_schema import _company

pytestmark = [requires_docker, pytest.mark.asyncio]

BASE = "https://console.example.test"
SENDERS = ["telegram", "email"]


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[OutgoingMail] = []

    def send(self, mail: OutgoingMail, sender: str) -> None:
        self.sent.append(mail)


class Check:
    """An injected check that records its calls and answers (or raises) as told."""

    def __init__(self, answer: bool | Exception = True) -> None:
        self.answer = answer
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self, conn: AsyncConnection[Any], client_id: uuid.UUID, payload: dict[str, Any]
    ) -> bool:
        self.calls.append(payload)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


async def _never(conn: AsyncConnection[Any], client_id: uuid.UUID, payload: dict[str, Any]) -> bool:
    raise AssertionError("the check must not run for this row")


@pytest.fixture
async def owner_pool(world: World) -> Any:  # noqa: F811
    pool = db.build_pool(world.owner_dsn, max_size=2)
    await pool.open(wait=True)
    yield pool
    await pool.close()


class Rig:
    """One sender kind on one company: a target, a sender with a check, a count of sends."""

    def __init__(self, world: World, owner_pool: Any, kind: str) -> None:  # noqa: F811
        self.world = world
        self.owner_pool = owner_pool
        self.kind = kind
        self.telegram_sent: list[dict[str, Any]] = []
        self.transport = FakeTransport()
        _quiesce_other_tenants(world)
        if kind == "telegram":
            _seed_endpoint(world)
        else:
            with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
                conn.execute(
                    "UPDATE console_email_outbox SET status='dead' WHERE client_id <> %s"
                    " AND status IN ('pending', 'delivering')",
                    (world.client_id,),
                )
        self.target_id = self._target()

    def _target(self, quiet_hours: dict[str, str] | None = None) -> uuid.UUID:
        target_id = uuid.uuid4()
        email = self.kind == "email"
        with psycopg.connect(self.world.owner_dsn, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO staff_notification_targets (id, client_id, channel, address, events,"
                " verified_at, quiet_hours) VALUES (%s,%s,%s,%s,'{inbox.overdue}'::text[],%s,%s)",
                (
                    target_id,
                    self.world.client_id,
                    self.kind,
                    f"{target_id.hex[:8]}@example.test" if email else "999",
                    datetime.now(UTC) if email else None,
                    Jsonb(quiet_hours) if quiet_hours else None,
                ),
            )
        return target_id

    def sender(self, check: OverdueCheck | None) -> Callable[[], Awaitable[bool]]:
        if self.kind == "telegram":

            def handler(request: httpx.Request) -> httpx.Response:
                self.telegram_sent.append(json.loads(request.content))
                return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            telegram = StaffNotificationSender(
                self.owner_pool, self.world.pool, client, still_overdue=check
            )
            return telegram.process_once
        mailer = NotificationMailer(
            self.owner_pool,
            self.world.pool,
            Mailer(self.transport, "noreply@example.test"),
            BASE,
            still_overdue=check,
        )
        return mailer.do_work

    @property
    def sent(self) -> int:
        return len(self.telegram_sent if self.kind == "telegram" else self.transport.sent)

    def row(
        self,
        payload: dict[str, Any],
        *,
        held: bool,
        event: str = "inbox.overdue",
        priority: str = "normal",
    ) -> uuid.UUID:
        notification_id = uuid.uuid4()
        with psycopg.connect(self.world.owner_dsn, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO staff_notifications (id, client_id, target_id, event, priority,"
                " payload, idempotency_key, held_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    notification_id,
                    self.world.client_id,
                    self.target_id,
                    event,
                    priority,
                    Jsonb(payload),
                    str(notification_id),
                    datetime.now(UTC) - timedelta(hours=1) if held else None,
                ),
            )
        return notification_id

    def state(self, notification_id: uuid.UUID) -> dict[str, Any]:
        with psycopg.connect(self.world.owner_dsn, autocommit=True) as conn:
            row = conn.execute(
                "SELECT status, attempts, last_error, locked_at, next_attempt_at, held_at"
                " FROM staff_notifications WHERE id = %s",
                (notification_id,),
            ).fetchone()
        assert row is not None
        keys = ("status", "attempts", "last_error", "locked_at", "next_attempt_at", "held_at")
        return dict(zip(keys, row, strict=True))


def _item(world: World, **extra: Any) -> tuple[Seeded, str]:  # noqa: F811
    """A needs-staff conversation; returns it and its wait's start as the ISO cycle."""
    s = _seed(
        world.owner_dsn,
        str(world.client_id),
        owned_by="human_support",
        **_needs_staff("customer_message", minutes_ago=30, **extra),
    )
    return s, _state(world.owner_dsn, s)["needs_staff_since"].isoformat()


def _payload(s: Seeded, cycle: str) -> dict[str, Any]:
    return {
        "conversation_id": s.conversation_id,
        "cycle": cycle,
        "customer": "Synthetic Customer",
        "priority": "normal",
        "waited_minutes": 30,
        "repeat": 1,
    }


async def _resolve(world: World, s: Seeded) -> None:  # noqa: F811
    admin = member(world.owner_dsn, str(world.client_id), "tenant_admin")
    async with tx(world.pool, str(world.client_id)) as conn:
        await transitions.resolve(
            conn,
            world.client_id,
            uuid.UUID(s.conversation_id),
            actor_user_id=uuid.UUID(admin),
            seen_customer_at=None,
        )


async def _snooze(world: World, s: Seeded) -> None:  # noqa: F811
    staff = member(world.owner_dsn, str(world.client_id), "tenant_staff")
    async with tx(world.pool, str(world.client_id)) as conn:
        await transitions.snooze(
            conn,
            world.client_id,
            uuid.UUID(s.conversation_id),
            actor_user_id=uuid.UUID(staff),
            until=datetime.now(UTC) + timedelta(hours=3),
            seen_customer_at=None,
        )


def _new_cycle(world: World, s: Seeded) -> None:  # noqa: F811
    """The customer wrote again after a reply: still Needs us, on a new wait."""
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE conversations SET"
            " needs_staff_since = needs_staff_since + interval '20 minutes',"
            " due_at = due_at + interval '20 minutes' WHERE id = %s",
            (s.conversation_id,),
        )


def _outsiders(world: World) -> tuple[int, int]:  # noqa: F811
    """Console notifications and staff notification rows of the company: nothing extra is made."""
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        bells = conn.execute(
            "SELECT count(*) AS n FROM console_notifications WHERE client_id = %s",
            (world.client_id,),
        ).fetchone()
        rows = conn.execute(
            "SELECT count(*) AS n FROM staff_notifications WHERE client_id = %s",
            (world.client_id,),
        ).fetchone()
    assert bells is not None and rows is not None
    return bells[0], rows[0]


# --- released and still true ----------------------------------------------------------------------


@pytest.mark.parametrize("kind", SENDERS)
async def test_released_overdue_is_sent_when_still_open(
    world: World,  # noqa: F811
    owner_pool: Any,
    kind: str,
) -> None:
    rig = Rig(world, owner_pool, kind)
    s, cycle = _item(world)
    notification_id = rig.row(_payload(s, cycle), held=True)
    assert await rig.sender(still_overdue)()
    assert rig.sent == 1
    state = rig.state(notification_id)
    assert (state["status"], state["last_error"]) == ("delivered", None)


@pytest.mark.parametrize("kind", SENDERS)
async def test_a_deferred_alert_is_held_then_rechecked_when_released(
    world: World,  # noqa: F811
    owner_pool: Any,
    kind: str,
) -> None:
    """Quiet hours hold the row (held_at set, nothing checked); on release the check runs once."""
    rig = Rig(world, owner_pool, kind)
    now = datetime.now(UTC)
    window = {
        "start": (now - timedelta(hours=1)).strftime("%H:%M"),
        "end": (now + timedelta(hours=1)).strftime("%H:%M"),
        "tz": "UTC",
    }
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE staff_notification_targets SET quiet_hours = %s WHERE id = %s",
            (Jsonb(window), rig.target_id),
        )
    s, cycle = _item(world)
    notification_id = rig.row(_payload(s, cycle), held=False)
    check = Check(True)
    work = rig.sender(check)
    assert await work()
    assert (rig.sent, check.calls) == (0, [])  # deferred: never held before, so not checked
    state = rig.state(notification_id)
    assert state["status"] == "pending" and state["held_at"] is not None
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE staff_notification_targets SET quiet_hours = NULL WHERE id = %s",
            (rig.target_id,),
        )
        conn.execute(
            "UPDATE staff_notifications SET next_attempt_at = now() WHERE id = %s",
            (notification_id,),
        )
    assert await work()
    assert len(check.calls) == 1 and check.calls[0]["conversation_id"] == s.conversation_id
    assert rig.sent == 1
    assert rig.state(notification_id)["status"] == "delivered"


# --- released and no longer true ------------------------------------------------------------------


@pytest.mark.parametrize("kind", SENDERS)
async def test_released_overdue_is_cancelled_when_resolved(
    world: World,  # noqa: F811
    owner_pool: Any,
    kind: str,
) -> None:
    rig = Rig(world, owner_pool, kind)
    s, cycle = _item(world)
    notification_id = rig.row(_payload(s, cycle), held=True)
    await _resolve(world, s)
    assert await rig.sender(still_overdue)()
    assert rig.sent == 0
    assert rig.state(notification_id)["status"] == "cancelled"


@pytest.mark.parametrize("kind", SENDERS)
async def test_cancelled_when_requeued_new_cycle(
    world: World,  # noqa: F811
    owner_pool: Any,
    kind: str,
) -> None:
    rig = Rig(world, owner_pool, kind)
    s, cycle = _item(world)
    notification_id = rig.row(_payload(s, cycle), held=True)
    _new_cycle(world, s)  # still Needs us, but not the wait this alert was about
    assert await rig.sender(still_overdue)()
    assert rig.sent == 0
    assert rig.state(notification_id)["status"] == "cancelled"


@pytest.mark.parametrize("kind", SENDERS)
async def test_cancelled_when_snoozed(
    world: World,  # noqa: F811
    owner_pool: Any,
    kind: str,
) -> None:
    rig = Rig(world, owner_pool, kind)
    s, cycle = _item(world)
    notification_id = rig.row(_payload(s, cycle), held=True)
    await _snooze(world, s)
    assert await rig.sender(still_overdue)()
    assert rig.sent == 0
    assert rig.state(notification_id)["status"] == "cancelled"


@pytest.mark.parametrize("kind", SENDERS)
async def test_both_senders_cancel_the_same_way(
    world: World,  # noqa: F811
    owner_pool: Any,
    kind: str,
) -> None:
    """Cancelled: no send, no failure, no dead-letter notice, no bell, nothing left to claim."""
    rig = Rig(world, owner_pool, kind)
    s, cycle = _item(world)
    notification_id = rig.row(_payload(s, cycle), held=True)
    before = _outsiders(world)
    work = rig.sender(Check(False))
    assert await work()
    state = rig.state(notification_id)
    assert (state["status"], state["attempts"], state["last_error"], state["locked_at"]) == (
        "cancelled",
        1,
        None,
        None,
    )
    assert state["held_at"] is not None
    assert rig.sent == 0
    assert _outsiders(world) == before  # no bell, no dead-letter notice, no new row
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        failures = conn.execute(
            "SELECT failure_notified_at FROM staff_notification_targets WHERE id = %s",
            (rig.target_id,),
        ).fetchone()
    assert failures is not None and failures[0] is None
    assert not await work()  # nothing left: a cancelled row is not claimed again


# --- never re-checked -----------------------------------------------------------------------------


@pytest.mark.parametrize("kind", SENDERS)
async def test_row_never_held_is_not_rechecked(
    world: World,  # noqa: F811
    owner_pool: Any,
    kind: str,
) -> None:
    rig = Rig(world, owner_pool, kind)
    s, cycle = _item(world)
    await _resolve(world, s)  # it would be cancelled if it were asked
    notification_id = rig.row(_payload(s, cycle), held=False)
    assert await rig.sender(_never)()
    assert rig.sent == 1
    assert rig.state(notification_id)["status"] == "delivered"


@pytest.mark.parametrize("kind", SENDERS)
async def test_other_events_never_rechecked(
    world: World,  # noqa: F811
    owner_pool: Any,
    kind: str,
) -> None:
    rig = Rig(world, owner_pool, kind)
    # A held row of another event: quiet hours held it, but only inbox.overdue is re-checked.
    notification_id = rig.row(
        {"conversation_id": str(uuid.uuid4()), "customer": "Synthetic Customer"},
        held=True,
        event="test",
    )
    assert await rig.sender(_never)()
    assert rig.sent == 1
    assert rig.state(notification_id)["status"] == "delivered"


@pytest.mark.parametrize("kind", SENDERS)
async def test_urgent_bypass_not_rechecked(
    world: World,  # noqa: F811
    owner_pool: Any,
    kind: str,
) -> None:
    """Urgent goes through quiet hours, is never held, and so is never re-checked."""
    rig = Rig(world, owner_pool, kind)
    now = datetime.now(UTC)
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE staff_notification_targets SET quiet_hours = %s WHERE id = %s",
            (
                Jsonb(
                    {
                        "start": (now - timedelta(hours=1)).strftime("%H:%M"),
                        "end": (now + timedelta(hours=1)).strftime("%H:%M"),
                        "tz": "UTC",
                    }
                ),
                rig.target_id,
            ),
        )
    s, cycle = _item(world)
    await _resolve(world, s)  # a check would say no; urgent is sent regardless
    notification_id = rig.row(_payload(s, cycle), held=False, priority="urgent")
    assert await rig.sender(_never)()
    assert rig.sent == 1
    state = rig.state(notification_id)
    assert (state["status"], state["held_at"]) == ("delivered", None)


@pytest.mark.parametrize("kind", SENDERS)
async def test_without_an_injected_check_a_held_row_is_sent_as_before(
    world: World,  # noqa: F811
    owner_pool: Any,
    kind: str,
) -> None:
    rig = Rig(world, owner_pool, kind)
    s, cycle = _item(world)
    await _resolve(world, s)
    notification_id = rig.row(_payload(s, cycle), held=True)
    assert await rig.sender(None)()
    assert rig.sent == 1
    assert rig.state(notification_id)["status"] == "delivered"


# --- a check that fails ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", SENDERS)
async def test_callable_raising_fails_the_row_with_backoff_and_sends_nothing(
    world: World,  # noqa: F811
    owner_pool: Any,
    kind: str,
) -> None:
    rig = Rig(world, owner_pool, kind)
    s, cycle = _item(world)
    notification_id = rig.row(_payload(s, cycle), held=True)
    check = Check(RuntimeError("database away"))
    before = datetime.now(UTC)
    assert await rig.sender(check)()
    assert len(check.calls) == 1 and rig.sent == 0
    state = rig.state(notification_id)
    assert (state["status"], state["attempts"], state["last_error"]) == (
        "pending",
        1,
        "RuntimeError",
    )
    assert before < state["next_attempt_at"] <= datetime.now(UTC) + timedelta(seconds=2)
    assert state["held_at"] is not None  # still held: the retry is checked again
    check.answer = True
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE staff_notifications SET next_attempt_at = now() WHERE id = %s",
            (notification_id,),
        )
    assert await rig.sender(check)()
    assert rig.sent == 1
    assert rig.state(notification_id)["status"] == "delivered"


# --- the real check on its own --------------------------------------------------------------------


async def _ask(world: World, client_id: str, payload: dict[str, Any]) -> bool:  # noqa: F811
    async with tx(world.pool, client_id) as conn:
        return await still_overdue(conn, uuid.UUID(client_id), payload)


async def test_still_overdue_true_only_for_the_same_wait(world: World) -> None:  # noqa: F811
    client = str(world.client_id)
    s, cycle = _item(world)
    payload = _payload(s, cycle)
    assert await _ask(world, client, payload)
    # The same instant written as UTC "Z" and as another offset is still the same wait.
    instant = datetime.fromisoformat(cycle).astimezone(UTC)
    assert await _ask(
        world, client, {**payload, "cycle": instant.isoformat().replace("+00:00", "Z")}
    )
    plus8 = instant.astimezone(datetime.now().astimezone().tzinfo).isoformat()
    assert await _ask(world, client, {**payload, "cycle": plus8})
    later = (instant + timedelta(microseconds=1)).isoformat()
    assert not await _ask(world, client, {**payload, "cycle": later})


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: {k: v for k, v in p.items() if k != "cycle"},
        lambda p: {k: v for k, v in p.items() if k != "conversation_id"},
        lambda p: {**p, "conversation_id": "not-a-uuid"},
        lambda p: {**p, "conversation_id": None},
        lambda p: {**p, "conversation_id": 12},
        lambda p: {**p, "cycle": "yesterday"},
        lambda p: {**p, "cycle": None},
        lambda p: {**p, "cycle": 1763500000},
        lambda p: {**p, "conversation_id": str(uuid.uuid4())},
        lambda p: {},
    ],
)
async def test_still_overdue_is_false_for_missing_or_bad_keys(
    world: World,  # noqa: F811
    mutate: Callable[[dict[str, Any]], dict[str, Any]],
) -> None:
    s, cycle = _item(world)
    assert not await _ask(world, str(world.client_id), mutate(_payload(s, cycle)))


async def test_still_overdue_never_sees_another_companys_conversation(
    world: World,  # noqa: F811
    migrated_dsn: str,
) -> None:
    other = _company(migrated_dsn)
    s = _seed(
        world.owner_dsn,
        other,
        owned_by="human_support",
        **_needs_staff("customer_message", minutes_ago=30),
    )
    payload = _payload(s, _state(world.owner_dsn, s)["needs_staff_since"].isoformat())
    assert await _ask(world, other, payload)  # its own company: true
    assert not await _ask(world, str(world.client_id), payload)  # ours: false
