"""The Inbox reminder kinds fired through csa.reminders (plan 2a, task 5; design 7.3; scenarios 4,
5, 6, 13 and 17). Disposable database, synthetic data only.

Reminders are made the way production makes them: the sweep (``schedule_missing``) for the kinds
it owns, ``csa.reminders.schedule`` with the module's key and payload for follow-up and handback
(Task 6 calls it from ``replied()``). They are fired by ``fire_due`` with a fixed job time on an
app-role pool, with the real delivery (``notify_people``): console rows and staff-chat rows are
read back through the owner. The process registry is swapped for a fresh one per test and the
Inbox kinds registered into it, as the worker does at start-up.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
import pytest
from csa.core import db
from csa.core.tenancy import tx
from csa.inbox_queue import reminders as inbox_reminders
from csa.inbox_queue import transitions
from csa.inbox_queue.reminders import (
    KINDS,
    SUBJECT_TYPE,
    cycle_epoch,
    register_inbox_reminder_kinds,
    reminder_key,
    reminder_payload,
    schedule_missing,
)
from csa.jobs.clock import FakeClock
from csa.jobs.inbox_queue import run_inbox_snooze_wake
from csa.notify.sender import quiet_until
from csa.reminders import fire, schedule
from csa.reminders import registry as kinds
from csa.reminders.registry import Decision, HandlerContext, Notice, ReminderRegistry
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from tests.conftest import requires_docker
from tests.test_bookings_schema import _owner
from tests.test_console_notifications_schema import member
from tests.test_inbox_queue_triggers import (
    Seeded,
    _enter,
    _events,
    _inbound,
    _needs_staff,
    _outbound,
    _seed,
    _settings,
    _state,
)
from tests.test_training_schema import _company

PARAM_KEYS = {"conversation_id", "customer", "priority", "waited_minutes", "repeat"}
QUIET = {"start": "22:00", "end": "07:00", "tz": "UTC"}


class Crash(BaseException):
    """A worker dying mid-row: not an Exception, so nothing in the job catches it."""


@dataclass(frozen=True)
class Team:
    client_id: str
    admin: str
    staff_one: str
    staff_two: str
    reader: str

    @property
    def everyone(self) -> list[str]:
        """The Inbox team: the admin and both staff (never the read-only member), sorted."""
        return sorted([self.admin, self.staff_one, self.staff_two])


def _team(dsn: str) -> Team:
    c = _company(dsn)
    return Team(
        c,
        member(dsn, c, "tenant_admin"),
        member(dsn, c, "tenant_staff"),
        member(dsn, c, "tenant_staff"),
        member(dsn, c, "read_only"),
    )


def _target(
    dsn: str,
    client_id: str,
    *,
    min_priority: str = "low",
    quiet_hours: dict[str, str] | None = None,
) -> str:
    """A staff chat subscribed to inbox.overdue."""
    target_id = str(uuid.uuid4())
    with _owner(dsn) as conn:
        conn.execute(
            "INSERT INTO staff_notification_targets"
            " (id, client_id, channel, address, events, min_priority, quiet_hours)"
            " VALUES (%s, %s, 'telegram', %s, '{inbox.overdue}', %s, %s)",
            (
                target_id,
                client_id,
                f"chat-{target_id}",
                min_priority,
                Jsonb(quiet_hours) if quiet_hours else None,
            ),
        )
    return target_id


def _console(dsn: str, client_id: str, kind: str) -> list[dict[str, Any]]:
    with _owner(dsn) as conn:
        return conn.execute(
            "SELECT user_id::text AS user_id, kind, priority, message_key, params, link,"
            " dedupe_key FROM console_notifications WHERE client_id = %s AND kind = %s"
            " ORDER BY created_at, dedupe_key, user_id",
            (client_id, kind),
        ).fetchall()


def _console_for(dsn: str, client_id: str, kind: str, key: str) -> list[dict[str, Any]]:
    return [r for r in _console(dsn, client_id, kind) if r["dedupe_key"] == key]


def _chat(dsn: str, client_id: str) -> list[dict[str, Any]]:
    with _owner(dsn) as conn:
        return conn.execute(
            "SELECT event, priority, payload, idempotency_key FROM staff_notifications"
            " WHERE client_id = %s ORDER BY created_at, idempotency_key",
            (client_id,),
        ).fetchall()


def _reminder(dsn: str, client_id: str, kind: str, s: Seeded) -> dict[str, Any]:
    with _owner(dsn) as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE client_id = %s AND kind = %s AND subject_id = %s ORDER"
            " BY created_at",
            (client_id, kind, s.conversation_id),
        ).fetchall()
    assert len(rows) == 1, rows
    return rows[0]


def _users(rows: list[dict[str, Any]]) -> list[str]:
    return sorted(r["user_id"] for r in rows)


def _owner_exec(dsn: str, query: Any, params: tuple[Any, ...]) -> None:
    with _owner(dsn) as conn:
        conn.execute(query, params)


@pytest.fixture
async def pool(app_dsn: str) -> AsyncIterator[AsyncConnectionPool]:
    async with db.build_pool(app_dsn, max_size=4) as made:
        yield made


@pytest.fixture(autouse=True)
def inbox_kinds(monkeypatch: pytest.MonkeyPatch) -> ReminderRegistry:
    """A fresh process registry holding the Inbox kinds, as the worker registers them."""
    fresh = ReminderRegistry()
    monkeypatch.setattr(kinds, "registry", fresh)
    register_inbox_reminder_kinds()
    return fresh


async def _sweep(pool: AsyncConnectionPool, client_id: str, now: datetime) -> dict[str, int]:
    async with tx(pool, client_id) as conn:
        return await schedule_missing(conn, UUID(client_id), now)


async def _fire(
    pool: AsyncConnectionPool, client_id: str, at: datetime, deliver: Any = None
) -> dict[str, int]:
    return await fire.fire_due(pool, UUID(client_id), FakeClock(at), deliver=deliver)


async def _overdue_item(
    dsn: str, pool: AsyncConnectionPool, t: Team, *, priority: str = "normal", **extra: Any
) -> tuple[Seeded, datetime]:
    """A needs-staff item, its overdue reminder scheduled by the sweep; returns its due time."""
    reason = "escalation" if priority != "normal" else "customer_message"
    s = _seed(
        dsn,
        t.client_id,
        owned_by="human_support",
        **_needs_staff(reason, priority, minutes_ago=10),
        **extra,
    )
    counts = await _sweep(pool, t.client_id, datetime.now(UTC))
    assert counts["inbox.overdue"] >= 1
    due: datetime = _state(dsn, s)["due_at"]
    assert _reminder(dsn, t.client_id, "inbox.overdue", s)["due_at"] == due
    return s, due


# --- registration ---------------------------------------------------------------------------------


def test_the_five_kinds_are_registered_and_a_second_call_changes_nothing(
    inbox_kinds: ReminderRegistry,
) -> None:
    assert KINDS == (
        "inbox.overdue",
        "inbox.snooze_due",
        "inbox.stale_claim",
        "inbox.follow_up",
        "inbox.handback",
    )
    assert inbox_kinds.kinds() == sorted(KINDS)
    before = {kind: inbox_kinds.get(kind) for kind in KINDS}
    register_inbox_reminder_kinds()  # idempotent: the worker and the tests may both call it
    assert {kind: inbox_kinds.get(kind) for kind in KINDS} == before


def test_another_handler_for_an_inbox_kind_is_refused(inbox_kinds: ReminderRegistry) -> None:
    async def other(ctx: HandlerContext) -> Decision:
        return Decision()

    fresh = ReminderRegistry()
    fresh.register("inbox.overdue", other)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(kinds, "registry", fresh)
        with pytest.raises(ValueError, match=r"inbox\.overdue"):
            register_inbox_reminder_kinds()


def test_the_key_names_the_kind_the_conversation_and_the_cycle_in_epoch_microseconds() -> None:
    conversation = UUID("00000000-0000-4000-8000-0000000000aa")
    cycle = datetime(2030, 1, 1, 12, 0, 5, 999_999, tzinfo=UTC)
    micros = 1_893_499_205_999_999  # 2030-01-01 12:00:05.999999 UTC
    assert cycle_epoch(cycle) == micros
    assert reminder_key("inbox.overdue", conversation, cycle) == (
        f"inbox.overdue:{conversation}:{micros}"
    )
    assert reminder_payload(conversation, cycle) == {
        "conversation_id": str(conversation),
        "cycle": micros,
    }
    # The same second, another microsecond: another cycle.
    assert cycle_epoch(cycle.replace(microsecond=1)) == micros - 999_998
    assert SUBJECT_TYPE == "conversation"


# --- inbox.overdue --------------------------------------------------------------------------------


@requires_docker
async def test_scenario_4_overdue_reaches_the_team_repeats_and_the_last_adds_the_admins(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    _target(migrated_dsn, t.client_id)
    s, due = await _overdue_item(migrated_dsn, pool, t)
    since = _state(migrated_dsn, s)["needs_staff_since"]
    reminder_id = _reminder(migrated_dsn, t.client_id, "inbox.overdue", s)["id"]
    at = due + timedelta(seconds=1)
    for number, priority in ((1, "normal"), (2, "high"), (3, "high"), (4, "high")):
        assert (await _fire(pool, t.client_id, at))["fired"] == 1, number
        rows = _console_for(migrated_dsn, t.client_id, "inbox.overdue", f"{reminder_id}:{number}")
        # Unclaimed: the whole team; the read-only member never.
        assert _users(rows) == t.everyone, number
        expected = {
            "conversation_id": s.conversation_id,
            "customer": "Synthetic Customer",
            "priority": priority,
            "waited_minutes": int((at - since).total_seconds() // 60),
            "repeat": number,
        }
        for row in rows:
            assert row["priority"] == priority
            assert row["message_key"] == "inbox.overdue"
            assert row["link"] == {"route": "inbox", "conversation": s.conversation_id}
            assert row["params"] == expected
        chat = _chat(migrated_dsn, t.client_id)
        assert len(chat) == number
        assert chat[-1]["event"] == "inbox.overdue" and chat[-1]["priority"] == priority
        assert chat[-1]["payload"] == expected
        state = _reminder(migrated_dsn, t.client_id, "inbox.overdue", s)
        assert state["fire_count"] == number
        if number < 4:
            assert state["state"] == "scheduled"
            assert state["due_at"] == at + timedelta(minutes=60)
        at += timedelta(minutes=60)
    assert _reminder(migrated_dsn, t.client_id, "inbox.overdue", s)["state"] == "done"
    assert (await _fire(pool, t.client_id, at + timedelta(hours=5)))["fired"] == 0
    assert len(_chat(migrated_dsn, t.client_id)) == 4


@requires_docker
async def test_a_claimed_item_reminds_its_holder_and_the_last_fire_names_each_admin(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    second_admin = member(migrated_dsn, t.client_id, "tenant_admin")
    s, due = await _overdue_item(
        migrated_dsn, pool, t, assignee_user_id=t.staff_one, claimed_at=datetime.now(UTC)
    )
    reminder_id = _reminder(migrated_dsn, t.client_id, "inbox.overdue", s)["id"]
    at = due + timedelta(seconds=1)
    for number in (1, 2, 3, 4):
        await _fire(pool, t.client_id, at)
        rows = _console_for(migrated_dsn, t.client_id, "inbox.overdue", f"{reminder_id}:{number}")
        expected = [t.staff_one] if number < 4 else [t.staff_one, t.admin, second_admin]
        assert _users(rows) == sorted(expected), number
        at += timedelta(minutes=60)


@requires_docker
async def test_max_repeats_zero_fires_once_with_the_admins(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    _settings(migrated_dsn, t.client_id, max_repeats=0)
    s, due = await _overdue_item(
        migrated_dsn, pool, t, assignee_user_id=t.staff_one, claimed_at=datetime.now(UTC)
    )
    assert (await _fire(pool, t.client_id, due + timedelta(seconds=1)))["fired"] == 1
    assert _users(_console(migrated_dsn, t.client_id, "inbox.overdue")) == sorted(
        [t.staff_one, t.admin]
    )
    state = _reminder(migrated_dsn, t.client_id, "inbox.overdue", s)
    assert state["state"] == "done" and state["fire_count"] == 1


@requires_docker
async def test_resolving_after_the_first_repeat_stops_the_rest(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    s, due = await _overdue_item(migrated_dsn, pool, t)
    at = due + timedelta(seconds=1)
    await _fire(pool, t.client_id, at)
    await _fire(pool, t.client_id, at + timedelta(minutes=60))
    assert len(_console(migrated_dsn, t.client_id, "inbox.overdue")) == 6
    async with tx(pool, t.client_id) as conn:
        await transitions.resolve(
            conn,
            UUID(t.client_id),
            UUID(s.conversation_id),
            actor_user_id=UUID(t.admin),
            seen_customer_at=None,
        )
    stats = await _fire(pool, t.client_id, at + timedelta(minutes=120))
    assert stats["dropped"] == 1 and stats["fired"] == 0
    assert len(_console(migrated_dsn, t.client_id, "inbox.overdue")) == 6
    assert _reminder(migrated_dsn, t.client_id, "inbox.overdue", s)["state"] == "done"


@requires_docker
async def test_the_raised_priority_passes_a_chat_min_priority_the_first_fire_did_not(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    _target(migrated_dsn, t.client_id, min_priority="high")
    _, due = await _overdue_item(migrated_dsn, pool, t)
    at = due + timedelta(seconds=1)
    await _fire(pool, t.client_id, at)
    assert _chat(migrated_dsn, t.client_id) == []  # normal is under the chat's floor
    assert len(_console(migrated_dsn, t.client_id, "inbox.overdue")) == 3  # the console has none
    await _fire(pool, t.client_id, at + timedelta(minutes=60))
    [chat] = _chat(migrated_dsn, t.client_id)
    assert chat["priority"] == "high" and chat["payload"]["priority"] == "high"


@requires_docker
async def test_urgent_stays_urgent_on_a_repeat(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    _target(migrated_dsn, t.client_id)
    _, due = await _overdue_item(migrated_dsn, pool, t, priority="urgent")
    at = due + timedelta(seconds=1)
    await _fire(pool, t.client_id, at)
    await _fire(pool, t.client_id, at + timedelta(minutes=60))
    assert [c["priority"] for c in _chat(migrated_dsn, t.client_id)] == ["urgent", "urgent"]


@requires_docker
async def test_scenario_5_quiet_hours_hold_the_chat_only_and_urgent_bypasses(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    _target(migrated_dsn, t.client_id, quiet_hours=QUIET)
    normal, _ = await _overdue_item(migrated_dsn, pool, t)
    urgent, _ = await _overdue_item(migrated_dsn, pool, t, priority="urgent")
    later = datetime.now(UTC) + timedelta(hours=5)
    at = later.replace(hour=23, minute=30, second=0, microsecond=0)
    if at < later:
        at += timedelta(days=1)
    stats = await _fire(pool, t.client_id, at)
    assert stats["fired"] == 2
    # The console rows exist at once (the centre has no quiet hours).
    rows = _console(migrated_dsn, t.client_id, "inbox.overdue")
    assert len(rows) == 6
    chat = {c["payload"]["conversation_id"]: c for c in _chat(migrated_dsn, t.client_id)}
    # The chat rows are written; the sender holds the normal one until 07:00 and sends the urgent.
    held = chat[normal.conversation_id]
    assert held["priority"] == "normal"
    assert quiet_until(QUIET, held["priority"], at) == (at + timedelta(days=1)).replace(
        hour=7, minute=0
    )
    assert chat[urgent.conversation_id]["priority"] == "urgent"
    assert quiet_until(QUIET, chat[urgent.conversation_id]["priority"], at) is None


@requires_docker
async def test_scenario_13_a_takeover_nobody_follows_up_reminds_the_person(
    migrated_dsn: str, app_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    s = _seed(migrated_dsn, t.client_id, owned_by="human_support")
    assert _enter(app_dsn, s, "manual", actor=t.staff_one, claim_for=t.staff_one)
    counts = await _sweep(pool, t.client_id, datetime.now(UTC))
    assert counts["inbox.overdue"] == 1 and counts["inbox.stale_claim"] == 1
    item = _state(migrated_dsn, s)
    assert item["queue_reason"] == "manual"
    await _fire(pool, t.client_id, item["due_at"] + timedelta(seconds=1))
    assert _users(_console(migrated_dsn, t.client_id, "inbox.overdue")) == [t.staff_one]


@requires_docker
async def test_scenario_17_an_automated_message_changes_nothing_and_reminders_continue(
    migrated_dsn: str, app_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    s = _seed(migrated_dsn, t.client_id, owned_by="human_support")
    _inbound(app_dsn, s)  # the 0622 trigger puts it in Needs us
    assert (await _sweep(pool, t.client_id, datetime.now(UTC)))["inbox.overdue"] == 1
    before = _state(migrated_dsn, s)
    _outbound(app_dsn, s, "reviewed")  # an automated payment reminder (payments/notices.py)
    after = _state(migrated_dsn, s)
    assert after["queue_status"] == "needs_staff"
    assert after["needs_staff_since"] == before["needs_staff_since"]
    stats = await _fire(pool, t.client_id, before["due_at"] + timedelta(seconds=1))
    assert stats["fired"] == 1
    assert _users(_console(migrated_dsn, t.client_id, "inbox.overdue")) == t.everyone


# --- recipients -----------------------------------------------------------------------------------


@requires_docker
async def test_an_assignee_who_left_falls_back_to_the_team(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    _, due = await _overdue_item(
        migrated_dsn, pool, t, assignee_user_id=t.staff_one, claimed_at=datetime.now(UTC)
    )
    _owner_exec(
        migrated_dsn,
        "UPDATE console_memberships SET is_enabled = false WHERE user_id = %s",
        (t.staff_one,),
    )
    await _fire(pool, t.client_id, due + timedelta(seconds=1))
    assert _users(_console(migrated_dsn, t.client_id, "inbox.overdue")) == sorted(
        [t.admin, t.staff_two]
    )


@requires_docker
async def test_a_read_only_assignee_falls_back_to_the_team(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    _, due = await _overdue_item(
        migrated_dsn, pool, t, assignee_user_id=t.reader, claimed_at=datetime.now(UTC)
    )
    await _fire(pool, t.client_id, due + timedelta(seconds=1))
    assert _users(_console(migrated_dsn, t.client_id, "inbox.overdue")) == t.everyone


@requires_docker
async def test_no_eligible_recipient_sends_nothing_and_finishes(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    c = _company(migrated_dsn)
    member(migrated_dsn, c, "read_only")
    member(migrated_dsn, c, "tenant_staff", member_enabled=False)
    _target(migrated_dsn, c)
    lonely = Team(c, "", "", "", "")
    s, due = await _overdue_item(migrated_dsn, pool, lonely)
    stats = await _fire(pool, c, due + timedelta(seconds=1))
    assert stats["dropped"] == 1
    assert _console(migrated_dsn, c, "inbox.overdue") == []
    assert _chat(migrated_dsn, c) == []  # the chat is not sent to a company nobody may act for
    state = _reminder(migrated_dsn, c, "inbox.overdue", s)
    assert state["state"] == "done"
    assert (await _fire(pool, c, due + timedelta(hours=3)))["fired"] == 0


@requires_docker
async def test_no_message_text_reaches_a_console_row_or_the_chat(
    migrated_dsn: str, app_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    _target(migrated_dsn, t.client_id)
    s = _seed(migrated_dsn, t.client_id, owned_by="human_support")
    _inbound(app_dsn, s)  # body "Synthetic question"
    await _sweep(pool, t.client_id, datetime.now(UTC))
    due = _state(migrated_dsn, s)["due_at"]
    await _fire(pool, t.client_id, due + timedelta(seconds=1))
    rows = _console(migrated_dsn, t.client_id, "inbox.overdue")
    chat = _chat(migrated_dsn, t.client_id)
    assert rows and chat
    for params in [r["params"] for r in rows] + [c["payload"] for c in chat]:
        assert set(params) <= PARAM_KEYS
        assert "Synthetic question" not in json.dumps(params)


@requires_docker
async def test_a_name_that_is_only_the_phone_number_is_not_shown(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    s, due = await _overdue_item(migrated_dsn, pool, t)
    _owner_exec(
        migrated_dsn,
        "UPDATE contacts SET full_name = '  +60 12-555 0148 ', phone = '+60125550148' WHERE id ="
        " %s",
        (s.contact_id,),
    )
    await _fire(pool, t.client_id, due + timedelta(seconds=1))
    rows = _console(migrated_dsn, t.client_id, "inbox.overdue")
    assert rows and all(r["params"]["customer"] is None for r in rows)


# --- inbox.snooze_due (scenario 6) ----------------------------------------------------------------


def _snoozed(dsn: str, t: Team) -> Seeded:
    """Snoozed by Person 2 (staff_two holds it) until a minute ago, before this PR's reminders."""
    now = datetime.now(UTC)
    return _seed(
        dsn,
        t.client_id,
        owned_by="human_support",
        queue_status="snoozed",
        snoozed_until=now - timedelta(minutes=1),
        assignee_user_id=t.staff_two,
        claimed_at=now - timedelta(hours=2),
    )


def _check_woke_once(dsn: str, t: Team, s: Seeded, until: datetime) -> None:
    item = _state(dsn, s)
    assert item["queue_status"] == "needs_staff"
    assert item["assignee_user_id"] == UUID(t.staff_two)  # the claim stays
    assert item["needs_staff_since"] > until  # a fresh clock
    assert item["due_at"] == item["needs_staff_since"] + timedelta(minutes=240)
    woke = [e for e in _events(dsn, s) if e["action"] == "woke"]
    assert len(woke) == 1 and woke[0]["actor_user_id"] is None
    rows = _console(dsn, t.client_id, "inbox.snooze_due")
    assert _users(rows) == [t.staff_two]
    assert rows[0]["message_key"] == "inbox.snooze_due"
    assert rows[0]["link"] == {"route": "inbox", "conversation": s.conversation_id}
    assert set(rows[0]["params"]) <= PARAM_KEYS


@requires_docker
async def test_scenario_6_the_snooze_ends_with_a_fresh_clock_and_one_notice(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    s = _snoozed(migrated_dsn, t)
    until = _state(migrated_dsn, s)["snoozed_until"]
    assert (await _sweep(pool, t.client_id, datetime.now(UTC)))["inbox.snooze_due"] == 1
    assert _reminder(migrated_dsn, t.client_id, "inbox.snooze_due", s)["due_at"] == until
    assert (await _fire(pool, t.client_id, datetime.now(UTC)))["fired"] == 1
    _check_woke_once(migrated_dsn, t, s, until)
    assert _reminder(migrated_dsn, t.client_id, "inbox.snooze_due", s)["state"] == "done"
    await _fire(pool, t.client_id, datetime.now(UTC) + timedelta(minutes=5))
    _check_woke_once(migrated_dsn, t, s, until)


@requires_docker
async def test_scenario_6_the_wake_job_ran_first(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    s = _snoozed(migrated_dsn, t)
    until = _state(migrated_dsn, s)["snoozed_until"]
    await _sweep(pool, t.client_id, datetime.now(UTC))
    async with tx(pool, t.client_id) as conn:
        woke = await run_inbox_snooze_wake(conn, UUID(t.client_id), FakeClock())
    assert woke == {"woken_count": 1}
    assert (await _fire(pool, t.client_id, datetime.now(UTC)))["fired"] == 1
    _check_woke_once(migrated_dsn, t, s, until)


@requires_docker
async def test_scenario_6_a_crash_after_notifying_replays_once(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    s = _snoozed(migrated_dsn, t)
    until = _state(migrated_dsn, s)["snoozed_until"]
    await _sweep(pool, t.client_id, datetime.now(UTC))

    async def crash_after(
        conn: psycopg.AsyncConnection[Any], client_id: UUID, notice: Notice, key: str
    ) -> None:
        await fire.deliver_notice(conn, client_id, notice, key)
        raise Crash

    with pytest.raises(Crash):
        await _fire(pool, t.client_id, datetime.now(UTC), deliver=crash_after)
    assert _state(migrated_dsn, s)["queue_status"] == "snoozed"  # all of it rolled back
    assert await _fire(pool, t.client_id, datetime.now(UTC)) == {
        "fired": 1,
        "dropped": 0,
        "failed": 0,
        "dead": 0,
        "unknown": 0,
    }
    _check_woke_once(migrated_dsn, t, s, until)


@requires_docker
async def test_a_snooze_reminder_drops_when_the_customer_brought_the_item_back(
    migrated_dsn: str, app_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    now = datetime.now(UTC)
    s = _seed(
        migrated_dsn,
        t.client_id,
        owned_by="human_support",
        queue_status="snoozed",
        snoozed_until=now + timedelta(hours=1),
        assignee_user_id=t.staff_two,
        claimed_at=now,
    )
    await _sweep(pool, t.client_id, now)
    _inbound(app_dsn, s)  # the customer wrote: Needs us through the trigger, not a wake
    assert _state(migrated_dsn, s)["queue_status"] == "needs_staff"
    stats = await _fire(pool, t.client_id, now + timedelta(hours=1, seconds=1))
    assert stats["dropped"] >= 1
    assert _console(migrated_dsn, t.client_id, "inbox.snooze_due") == []


@requires_docker
async def test_wake_one_wakes_a_due_item_once_and_refuses_others(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    s = _snoozed(migrated_dsn, t)
    later = _seed(
        migrated_dsn,
        t.client_id,
        owned_by="human_support",
        queue_status="snoozed",
        snoozed_until=datetime.now(UTC) + timedelta(hours=1),
    )
    client = UUID(t.client_id)
    async with tx(pool, t.client_id) as conn:
        assert await transitions.wake_one(conn, client, UUID(s.conversation_id)) is True
        assert await transitions.wake_one(conn, client, UUID(s.conversation_id)) is False
        assert await transitions.wake_one(conn, client, UUID(later.conversation_id)) is False
    item = _state(migrated_dsn, s)
    assert item["queue_status"] == "needs_staff" and item["queue_reason"] == "manual"
    [woke] = [e for e in _events(migrated_dsn, s) if e["action"] == "woke"]
    assert woke["actor_user_id"] is None
    assert woke["details"] == {"reason": "manual", "priority": "normal"}
    assert _state(migrated_dsn, later)["queue_status"] == "snoozed"


# --- inbox.stale_claim ----------------------------------------------------------------------------


async def _claimed(
    dsn: str, pool: AsyncConnectionPool, t: Team, holder: str
) -> tuple[Seeded, datetime]:
    s = _seed(
        dsn,
        t.client_id,
        owned_by="human_support",
        **_needs_staff("customer_message", minutes_ago=10),
    )
    async with tx(pool, t.client_id) as conn:
        await transitions.claim(
            conn, UUID(t.client_id), UUID(s.conversation_id), user_id=UUID(holder)
        )
    assert (await _sweep(pool, t.client_id, datetime.now(UTC)))["inbox.stale_claim"] == 1
    claimed_at: datetime = _state(dsn, s)["claimed_at"]
    reminder = _reminder(dsn, t.client_id, "inbox.stale_claim", s)
    assert reminder["due_at"] == claimed_at + timedelta(hours=8)
    return s, reminder["due_at"]


@requires_docker
async def test_a_stale_claim_reminds_the_holder_then_the_admins(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    s, due = await _claimed(migrated_dsn, pool, t, t.staff_one)
    at = due + timedelta(seconds=1)
    await _fire(pool, t.client_id, at)
    rows = _console(migrated_dsn, t.client_id, "inbox.stale_claim")
    assert _users(rows) == [t.staff_one]
    assert rows[0]["message_key"] == "inbox.stale_claim"
    assert set(rows[0]["params"]) <= PARAM_KEYS
    assert _reminder(migrated_dsn, t.client_id, "inbox.stale_claim", s)["state"] == "scheduled"
    await _fire(pool, t.client_id, at + timedelta(minutes=60))
    rows = _console(migrated_dsn, t.client_id, "inbox.stale_claim")
    assert _users(rows) == sorted([t.staff_one, t.admin])
    assert _reminder(migrated_dsn, t.client_id, "inbox.stale_claim", s)["state"] == "done"
    assert _chat(migrated_dsn, t.client_id) == []  # console only


@pytest.mark.parametrize("change", ["unclaim", "assign", "topic"])
@requires_docker
async def test_a_stale_claim_is_cleared_by_release_reassign_or_a_staff_action(
    migrated_dsn: str, pool: AsyncConnectionPool, change: str
) -> None:
    t = _team(migrated_dsn)
    s, due = await _claimed(migrated_dsn, pool, t, t.staff_one)
    client, conversation = UUID(t.client_id), UUID(s.conversation_id)
    async with tx(pool, t.client_id) as conn:
        if change == "unclaim":
            await transitions.unclaim(conn, client, conversation, actor_user_id=UUID(t.staff_one))
        elif change == "assign":
            await transitions.assign(
                conn,
                client,
                conversation,
                assignee_id=UUID(t.staff_two),
                actor_user_id=UUID(t.admin),
            )
        else:
            await transitions.set_topic(
                conn, client, conversation, topic="question", actor_user_id=UUID(t.staff_one)
            )
    await _fire(pool, t.client_id, due + timedelta(seconds=1))
    assert _console(migrated_dsn, t.client_id, "inbox.stale_claim") == []
    with _owner(migrated_dsn) as conn:
        [first] = conn.execute(
            "SELECT state FROM reminders WHERE client_id = %s AND kind = 'inbox.stale_claim'"
            " ORDER BY created_at LIMIT 1",
            (t.client_id,),
        ).fetchall()
    assert first["state"] == "done"


@requires_docker
async def test_stale_claim_hours_zero_turns_it_off(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    s, due = await _claimed(migrated_dsn, pool, t, t.staff_one)
    _settings(migrated_dsn, t.client_id, stale_claim_hours=0)
    await _fire(pool, t.client_id, due + timedelta(seconds=1))
    assert _console(migrated_dsn, t.client_id, "inbox.stale_claim") == []
    assert _reminder(migrated_dsn, t.client_id, "inbox.stale_claim", s)["state"] == "done"


# --- inbox.follow_up and inbox.handback -----------------------------------------------------------


async def _replied(
    dsn: str, pool: AsyncConnectionPool, t: Team, *, owned_by: str = "human_support"
) -> tuple[Seeded, datetime]:
    """Person 1 answered: waiting on the customer, both prompts scheduled as Task 6 will."""
    s = _seed(
        dsn,
        t.client_id,
        owned_by=owned_by,
        **_needs_staff("customer_message", minutes_ago=10),
    )
    client, conversation = UUID(t.client_id), UUID(s.conversation_id)
    async with tx(pool, t.client_id) as conn:
        await transitions.replied(conn, client, conversation, user_id=UUID(t.staff_one))
    replied_at: datetime = _state(dsn, s)["last_staff_reply_at"]
    async with tx(pool, t.client_id) as conn:
        for kind, hours in (("inbox.follow_up", 48), ("inbox.handback", 24)):
            await schedule(
                conn,
                client,
                kind,
                SUBJECT_TYPE,
                s.conversation_id,
                replied_at + timedelta(hours=hours),
                reminder_payload(conversation, replied_at),
                reminder_key(kind, conversation, replied_at),
            )
    return s, replied_at


@requires_docker
async def test_follow_up_and_handback_prompt_the_holder_while_waiting(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    _target(migrated_dsn, t.client_id)
    s, replied_at = await _replied(migrated_dsn, pool, t)
    await _fire(pool, t.client_id, replied_at + timedelta(hours=24, seconds=1))
    handback = _console(migrated_dsn, t.client_id, "inbox.handback")
    assert _users(handback) == [t.staff_one]
    assert handback[0]["message_key"] == "inbox.handback"
    assert _console(migrated_dsn, t.client_id, "inbox.follow_up") == []
    await _fire(pool, t.client_id, replied_at + timedelta(hours=48, seconds=1))
    follow_up = _console(migrated_dsn, t.client_id, "inbox.follow_up")
    assert _users(follow_up) == [t.staff_one]
    assert follow_up[0]["link"] == {"route": "inbox", "conversation": s.conversation_id}
    for row in handback + follow_up:
        assert set(row["params"]) <= PARAM_KEYS
    assert _chat(migrated_dsn, t.client_id) == []  # console only, never the staff chat
    for kind in ("inbox.follow_up", "inbox.handback"):
        assert _reminder(migrated_dsn, t.client_id, kind, s)["state"] == "done"


@requires_docker
async def test_follow_up_drops_when_the_customer_returned_the_item_to_the_bot(
    migrated_dsn: str, app_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    s, replied_at = await _replied(migrated_dsn, pool, t, owned_by="ai")
    _inbound(app_dsn, s)
    assert _state(migrated_dsn, s)["queue_status"] == "bot"
    stats = await _fire(pool, t.client_id, replied_at + timedelta(hours=49))
    assert stats["dropped"] == 2
    assert _console(migrated_dsn, t.client_id, "inbox.follow_up") == []
    assert _console(migrated_dsn, t.client_id, "inbox.handback") == []


@requires_docker
async def test_follow_up_and_handback_drop_after_resolve(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    s, replied_at = await _replied(migrated_dsn, pool, t)
    async with tx(pool, t.client_id) as conn:
        await transitions.resolve(
            conn,
            UUID(t.client_id),
            UUID(s.conversation_id),
            actor_user_id=UUID(t.staff_one),
            seen_customer_at=None,
        )
    stats = await _fire(pool, t.client_id, replied_at + timedelta(hours=49))
    assert stats["dropped"] == 2 and stats["fired"] == 0


@requires_docker
async def test_handback_drops_after_the_mute_expired_and_follow_up_still_fires(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    s, replied_at = await _replied(migrated_dsn, pool, t)
    _owner_exec(
        migrated_dsn,
        "UPDATE conversations SET ai_muted_until = now() - interval '1 minute' WHERE id = %s",
        (s.conversation_id,),
    )
    await _fire(pool, t.client_id, replied_at + timedelta(hours=49))
    assert _console(migrated_dsn, t.client_id, "inbox.handback") == []
    assert _users(_console(migrated_dsn, t.client_id, "inbox.follow_up")) == [t.staff_one]


@requires_docker
async def test_follow_up_and_handback_hours_zero_turn_them_off(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    _, replied_at = await _replied(migrated_dsn, pool, t)
    _settings(migrated_dsn, t.client_id, follow_up_hours=0, handback_prompt_hours=0)
    stats = await _fire(pool, t.client_id, replied_at + timedelta(hours=49))
    assert stats["dropped"] == 2
    assert _console(migrated_dsn, t.client_id, "inbox.follow_up") == []
    assert _console(migrated_dsn, t.client_id, "inbox.handback") == []


@requires_docker
async def test_a_follow_up_for_a_released_holder_goes_to_the_team(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    s, replied_at = await _replied(migrated_dsn, pool, t)
    _owner_exec(
        migrated_dsn,
        "UPDATE conversations SET assignee_user_id = NULL, claimed_at = NULL WHERE id = %s",
        (s.conversation_id,),
    )
    await _fire(pool, t.client_id, replied_at + timedelta(hours=49))
    assert _users(_console(migrated_dsn, t.client_id, "inbox.follow_up")) == t.everyone


@requires_docker
async def test_a_malformed_payload_or_unknown_conversation_is_dropped(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = _team(migrated_dsn)
    client = UUID(t.client_id)
    now = datetime.now(UTC)
    async with tx(pool, t.client_id) as conn:
        for n, (subject, payload) in enumerate(
            (
                (str(uuid.uuid4()), {"conversation_id": "x", "cycle": 1}),
                ("not-a-uuid", {"cycle": 1}),
                (str(uuid.uuid4()), {"cycle": "soon"}),
            )
        ):
            for kind in KINDS:
                await schedule(
                    conn, client, kind, SUBJECT_TYPE, subject, now, payload, f"bad-{kind}-{n}"
                )
    stats = await _fire(pool, t.client_id, now + timedelta(seconds=1))
    assert stats["dropped"] == 3 * len(KINDS) and stats["failed"] == 0
    assert inbox_reminders.HANDLERS.keys() == set(KINDS)
