"""The queue transitions schedule, cancel and announce Inbox reminders (plan 2a, task 6; decisions
2 to 4; design 7.3; scenarios 2, 3 and 13). Disposable database, synthetic data only.

Every transition runs on an app-role company transaction, as the console and the worker call it;
reminders and console notifications are read back through the owner. A cancel made by a
transition never waits for a reminder row another transaction holds (the fire job claims its row
``FOR UPDATE`` and the ``inbox.snooze_due`` handler then locks the conversation, while a
transition holds the conversation lock and then cancels): the lock-order test runs both on two
real connections.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
from csa.core import db
from csa.core.tenancy import tx
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
from csa.reminders import fire, schedule
from csa.reminders import registry as kinds
from csa.reminders.registry import ReminderRegistry
from psycopg_pool import AsyncConnectionPool

from tests.conftest import requires_docker
from tests.test_bookings_schema import _owner
from tests.test_console_notifications_schema import member
from tests.test_inbox_queue_triggers import Seeded, _inbound, _needs_staff, _seed, _settings, _state
from tests.test_training_schema import _company

pytestmark = requires_docker

ASSIGNED = "inbox.assigned"
ASSIGNED_PARAMS = {"conversation_id", "customer", "priority", "assigned_by"}


class Team:
    """A company with an admin, two staff members and a read-only member (synthetic)."""

    def __init__(self, dsn: str) -> None:
        self.client_id = _company(dsn)
        self.admin = member(dsn, self.client_id, "tenant_admin")
        self.staff_one = member(dsn, self.client_id, "tenant_staff")
        self.staff_two = member(dsn, self.client_id, "tenant_staff")


@pytest.fixture
async def pool(app_dsn: str) -> AsyncIterator[AsyncConnectionPool]:
    async with db.build_pool(app_dsn, max_size=4) as made:
        yield made


@pytest.fixture(autouse=True)
def inbox_kinds(monkeypatch: pytest.MonkeyPatch) -> ReminderRegistry:
    fresh = ReminderRegistry()
    monkeypatch.setattr(kinds, "registry", fresh)
    register_inbox_reminder_kinds()
    return fresh


async def _on(
    pool: AsyncConnectionPool, s: Seeded, fn: Callable[..., Awaitable[Any]], **kwargs: Any
) -> Any:
    """One transition on the item, on a committed company transaction of the app role."""
    async with tx(pool, s.client_id) as conn:
        return await fn(conn, UUID(s.client_id), UUID(s.conversation_id), **kwargs)


async def _sweep(pool: AsyncConnectionPool, client_id: str) -> dict[str, int]:
    async with tx(pool, client_id) as conn:
        return await schedule_missing(conn, UUID(client_id), datetime.now(UTC))


def _reminders(dsn: str, s: Seeded, kind: str | None = None) -> list[dict[str, Any]]:
    with _owner(dsn) as conn:
        return conn.execute(
            "SELECT id, kind, state, due_at, payload, dedupe_key, fire_count FROM reminders"
            " WHERE client_id = %s AND subject_id = %s AND (%s::text IS NULL OR kind = %s)"
            " ORDER BY created_at, dedupe_key",
            (s.client_id, s.conversation_id, kind, kind),
        ).fetchall()


def _states(dsn: str, s: Seeded) -> dict[str, list[str]]:
    """Each kind's reminder states, oldest first."""
    found: dict[str, list[str]] = {}
    for row in _reminders(dsn, s):
        found.setdefault(row["kind"], []).append(row["state"])
    return found


def _assigned(dsn: str, client_id: str) -> list[dict[str, Any]]:
    with _owner(dsn) as conn:
        return conn.execute(
            "SELECT user_id::text AS user_id, kind, priority, message_key, params, link, dedupe_key"
            " FROM console_notifications WHERE client_id = %s AND kind = %s"
            " ORDER BY created_at, dedupe_key",
            (client_id, ASSIGNED),
        ).fetchall()


def _chat(dsn: str, client_id: str) -> list[dict[str, Any]]:
    with _owner(dsn) as conn:
        return conn.execute(
            "SELECT event FROM staff_notifications WHERE client_id = %s", (client_id,)
        ).fetchall()


def _chat_target(dsn: str, client_id: str) -> None:
    """A staff chat subscribed to everything Inbox, so a chat row would be seen if one were sent."""
    target = str(uuid.uuid4())
    with _owner(dsn) as conn:
        conn.execute(
            "INSERT INTO staff_notification_targets (id, client_id, channel, address, events,"
            " min_priority) VALUES (%s, %s, 'telegram', %s, %s, 'low')",
            (target, client_id, f"chat-{target}", [*KINDS, ASSIGNED]),
        )


async def _claimed_item(
    dsn: str, pool: AsyncConnectionPool, t: Team, *, holder: str, owned_by: str = "human_support"
) -> Seeded:
    """A needs-us item claimed by ``holder``; the sweep scheduled its overdue and stale claim."""
    s = _seed(dsn, t.client_id, owned_by=owned_by, **_needs_staff("customer_message", "high", 5))
    await _on(pool, s, transitions.claim, user_id=UUID(holder))
    counts = await _sweep(pool, t.client_id)
    assert counts["inbox.overdue"] == 1 and counts["inbox.stale_claim"] == 1
    return s


async def _old_cycle(pool: AsyncConnectionPool, s: Seeded, kind: str) -> None:
    """A pending reminder of ``kind`` for an earlier cycle of the item (as a stale leftover)."""
    cycle = datetime.now(UTC) - timedelta(days=3, microseconds=uuid.uuid4().int % 1000)
    conversation = UUID(s.conversation_id)
    async with tx(pool, s.client_id) as conn:
        await schedule(
            conn,
            UUID(s.client_id),
            kind,
            SUBJECT_TYPE,
            s.conversation_id,
            datetime.now(UTC) + timedelta(hours=5),
            reminder_payload(conversation, cycle),
            reminder_key(kind, conversation, cycle),
        )


async def _every_kind_pending(dsn: str, pool: AsyncConnectionPool, t: Team) -> Seeded:
    s = await _claimed_item(dsn, pool, t, holder=t.staff_one)
    for kind in ("inbox.snooze_due", "inbox.follow_up", "inbox.handback"):
        await _old_cycle(pool, s, kind)
    assert {kind: states for kind, states in _states(dsn, s).items()} == {
        kind: ["scheduled"] for kind in KINDS
    }
    return s


def _check_prompt(
    row: dict[str, Any], kind: str, s: Seeded, replied_at: datetime, hours: int
) -> None:
    conversation = UUID(s.conversation_id)
    assert row["state"] == "scheduled"
    assert row["due_at"] == replied_at + timedelta(hours=hours)
    assert row["dedupe_key"] == reminder_key(kind, conversation, replied_at)
    assert row["payload"] == reminder_payload(conversation, replied_at)


# --- replied --------------------------------------------------------------------------------------


async def test_scenario_2_claim_take_over_and_reply_waits_with_both_prompts(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = Team(migrated_dsn)
    _chat_target(migrated_dsn, t.client_id)
    # Person 1 claims it (a self-claim announces nothing) and has taken the chat over.
    s = await _claimed_item(migrated_dsn, pool, t, holder=t.staff_one)
    await _on(pool, s, transitions.replied, user_id=UUID(t.staff_one))
    item = _state(migrated_dsn, s)
    assert item["queue_status"] == "waiting_customer"
    assert item["assignee_user_id"] == UUID(t.staff_one)  # the claim stays
    replied_at: datetime = item["last_staff_reply_at"]
    states = _states(migrated_dsn, s)
    assert states["inbox.overdue"] == ["cancelled"]
    assert states["inbox.stale_claim"] == ["cancelled"]
    [follow_up] = _reminders(migrated_dsn, s, "inbox.follow_up")
    _check_prompt(follow_up, "inbox.follow_up", s, replied_at, 48)
    [handback] = _reminders(migrated_dsn, s, "inbox.handback")
    _check_prompt(handback, "inbox.handback", s, replied_at, 24)
    assert _assigned(migrated_dsn, t.client_id) == []
    assert _chat(migrated_dsn, t.client_id) == []


async def test_a_reply_with_the_assistant_in_charge_schedules_no_handback(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = Team(migrated_dsn)
    s = await _claimed_item(migrated_dsn, pool, t, holder=t.staff_one, owned_by="ai")
    await _on(pool, s, transitions.replied, user_id=UUID(t.staff_one))
    states = _states(migrated_dsn, s)
    assert states["inbox.follow_up"] == ["scheduled"]
    assert "inbox.handback" not in states


async def test_zero_hours_schedule_no_prompt_and_the_reply_still_cancels(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = Team(migrated_dsn)
    _settings(migrated_dsn, t.client_id, follow_up_hours=0, handback_prompt_hours=0)
    s = await _claimed_item(migrated_dsn, pool, t, holder=t.staff_one)
    await _on(pool, s, transitions.replied, user_id=UUID(t.staff_one))
    assert _states(migrated_dsn, s) == {
        "inbox.overdue": ["cancelled"],
        "inbox.stale_claim": ["cancelled"],
    }


async def test_a_second_reply_starts_a_new_cycle_and_cancels_the_first(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = Team(migrated_dsn)
    s = await _claimed_item(migrated_dsn, pool, t, holder=t.staff_one)
    await _on(pool, s, transitions.replied, user_id=UUID(t.staff_one))
    await _on(pool, s, transitions.replied, user_id=UUID(t.staff_one))
    replied_at = _state(migrated_dsn, s)["last_staff_reply_at"]
    for kind, hours in (("inbox.follow_up", 48), ("inbox.handback", 24)):
        first, second = _reminders(migrated_dsn, s, kind)
        assert first["state"] == "cancelled"
        _check_prompt(second, kind, s, replied_at, hours)


async def test_two_replies_in_one_transaction_keep_one_scheduled_prompt_each(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = Team(migrated_dsn)
    s = await _claimed_item(migrated_dsn, pool, t, holder=t.staff_one)
    async with tx(pool, t.client_id) as conn:
        for _ in range(2):
            await transitions.replied(
                conn, UUID(t.client_id), UUID(s.conversation_id), user_id=UUID(t.staff_one)
            )
    states = _states(migrated_dsn, s)
    assert states["inbox.follow_up"] == ["scheduled"]
    assert states["inbox.handback"] == ["scheduled"]


async def test_a_reply_cancels_every_other_pending_kind(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = Team(migrated_dsn)
    s = await _every_kind_pending(migrated_dsn, pool, t)
    await _on(pool, s, transitions.replied, user_id=UUID(t.staff_one))
    states = _states(migrated_dsn, s)
    for kind in ("inbox.overdue", "inbox.stale_claim", "inbox.snooze_due"):
        assert states[kind] == ["cancelled"], kind
    for kind in ("inbox.follow_up", "inbox.handback"):  # the old cycle's, then the new one
        assert states[kind] == ["cancelled", "scheduled"], kind


# --- scenario 3 -----------------------------------------------------------------------------------


async def test_scenario_3_the_customer_answers_and_the_new_clock_gets_its_reminder(
    migrated_dsn: str, app_dsn: str, pool: AsyncConnectionPool
) -> None:
    """Spec scenario 3's "Person 1 gets a console notification" is the overdue reminder of the new
    clock (plan 2a, decision 13): the return to Needs us happens in the 0622 trigger with no Python
    hook, so nothing is sent at once; the sweep schedules the new cycle's reminder and it reaches
    the holder when the new clock runs out. The old cycle's prompts are left to their handlers'
    re-check (decision 4: a customer message is SQL), which drops them.
    """
    t = Team(migrated_dsn)
    s = await _claimed_item(migrated_dsn, pool, t, holder=t.staff_one)
    await _on(pool, s, transitions.replied, user_id=UUID(t.staff_one))
    replied_at = _state(migrated_dsn, s)["last_staff_reply_at"]
    _inbound(app_dsn, s)
    item = _state(migrated_dsn, s)
    assert item["queue_status"] == "needs_staff" and item["queue_reason"] == "customer_message"
    assert item["needs_staff_since"] > replied_at  # a new clock
    assert item["assignee_user_id"] == UUID(t.staff_one)
    assert (await _sweep(pool, t.client_id))["inbox.overdue"] == 1
    overdue = _reminders(migrated_dsn, s, "inbox.overdue")
    assert [r["state"] for r in overdue] == ["cancelled", "scheduled"]
    new = overdue[-1]
    assert new["dedupe_key"] == reminder_key(
        "inbox.overdue", UUID(s.conversation_id), item["needs_staff_since"]
    )
    assert new["due_at"] == item["due_at"]
    # The holder is the one reminded when the new clock runs out; the old prompts drop.
    stats = await fire.fire_due(
        pool, UUID(t.client_id), FakeClock(replied_at + timedelta(hours=49))
    )
    assert stats["fired"] == 1 and stats["dropped"] == 2
    with _owner(migrated_dsn) as conn:
        rows = conn.execute(
            "SELECT user_id::text AS user_id FROM console_notifications WHERE client_id = %s"
            " AND kind = 'inbox.overdue'",
            (t.client_id,),
        ).fetchall()
    assert [r["user_id"] for r in rows] == [t.staff_one]


# --- snooze ---------------------------------------------------------------------------------------


async def test_snooze_cancels_the_clock_and_schedules_the_wake_reminder(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = Team(migrated_dsn)
    s = await _claimed_item(migrated_dsn, pool, t, holder=t.staff_one)
    until = datetime.now(UTC) + timedelta(hours=2)
    await _on(
        pool,
        s,
        transitions.snooze,
        actor_user_id=UUID(t.staff_one),
        until=until,
        seen_customer_at=None,
    )
    states = _states(migrated_dsn, s)
    assert states["inbox.overdue"] == ["cancelled"]
    assert states["inbox.stale_claim"] == ["cancelled"]
    [due] = _reminders(migrated_dsn, s, "inbox.snooze_due")
    conversation = UUID(s.conversation_id)
    assert due["state"] == "scheduled" and due["due_at"] == until
    assert due["dedupe_key"] == reminder_key("inbox.snooze_due", conversation, until)
    assert due["payload"] == reminder_payload(conversation, until)
    # The sweep sees it has one.
    assert (await _sweep(pool, t.client_id))["inbox.snooze_due"] == 0


async def test_a_new_snooze_time_replaces_the_wake_reminder_and_the_same_time_keeps_it(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = Team(migrated_dsn)
    s = await _claimed_item(migrated_dsn, pool, t, holder=t.staff_one)
    first = datetime.now(UTC) + timedelta(hours=2)
    later = first + timedelta(hours=1)
    for until in (first, first, later):
        await _on(
            pool,
            s,
            transitions.snooze,
            actor_user_id=UUID(t.staff_one),
            until=until,
            seen_customer_at=None,
        )
    rows = _reminders(migrated_dsn, s, "inbox.snooze_due")
    assert [(r["state"], r["due_at"]) for r in rows] == [
        ("cancelled", first),
        ("scheduled", later),
    ]


async def test_snooze_and_its_reminder_roll_back_together(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = Team(migrated_dsn)
    s = await _claimed_item(migrated_dsn, pool, t, holder=t.staff_one)
    before = _states(migrated_dsn, s)

    class Abort(Exception):
        pass

    with pytest.raises(Abort):
        async with tx(pool, t.client_id) as conn:
            await transitions.snooze(
                conn,
                UUID(t.client_id),
                UUID(s.conversation_id),
                actor_user_id=UUID(t.staff_one),
                until=datetime.now(UTC) + timedelta(hours=2),
                seen_customer_at=None,
            )
            raise Abort
    assert _states(migrated_dsn, s) == before
    assert _state(migrated_dsn, s)["queue_status"] == "needs_staff"


# --- resolve and hand back ------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["resolve", "hand_back"])
async def test_resolve_and_hand_back_cancel_every_pending_kind(
    migrated_dsn: str, pool: AsyncConnectionPool, action: str
) -> None:
    t = Team(migrated_dsn)
    s = await _every_kind_pending(migrated_dsn, pool, t)
    if action == "resolve":
        done = await _on(
            pool, s, transitions.resolve, actor_user_id=UUID(t.admin), seen_customer_at=None
        )
    else:
        done = await _on(pool, s, transitions.hand_back, actor_user_id=UUID(t.admin))
    assert done is True
    assert _states(migrated_dsn, s) == {kind: ["cancelled"] for kind in KINDS}


async def test_cancels_touch_only_this_item(migrated_dsn: str, pool: AsyncConnectionPool) -> None:
    t = Team(migrated_dsn)
    s = await _claimed_item(migrated_dsn, pool, t, holder=t.staff_one)
    other = await _claimed_item(migrated_dsn, pool, t, holder=t.staff_one)
    await _on(pool, s, transitions.resolve, actor_user_id=UUID(t.admin), seen_customer_at=None)
    assert _states(migrated_dsn, other) == {
        "inbox.overdue": ["scheduled"],
        "inbox.stale_claim": ["scheduled"],
    }


# --- release, reassign, takeover of a claim -------------------------------------------------------


@pytest.mark.parametrize("change", ["unclaim", "assign", "takeover"])
async def test_release_or_reassign_cancels_the_stale_claim_only(
    migrated_dsn: str, pool: AsyncConnectionPool, change: str
) -> None:
    t = Team(migrated_dsn)
    s = await _claimed_item(migrated_dsn, pool, t, holder=t.staff_one)
    if change == "unclaim":
        await _on(pool, s, transitions.unclaim, actor_user_id=UUID(t.staff_one))
    elif change == "assign":
        await _on(
            pool, s, transitions.assign, assignee_id=UUID(t.staff_two), actor_user_id=UUID(t.admin)
        )
    else:  # an administrator takes the claim over
        await _on(pool, s, transitions.claim, user_id=UUID(t.admin))
    assert _states(migrated_dsn, s) == {
        "inbox.overdue": ["scheduled"],  # the clock goes on, for the new holder or the team
        "inbox.stale_claim": ["cancelled"],
    }
    # A new holder's claim is a new cycle: the sweep schedules its own stale-claim reminder.
    swept = (await _sweep(pool, t.client_id))["inbox.stale_claim"]
    assert swept == (0 if change == "unclaim" else 1)


async def test_an_unchanged_claim_cancels_nothing(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = Team(migrated_dsn)
    s = await _claimed_item(migrated_dsn, pool, t, holder=t.staff_one)
    await _on(pool, s, transitions.claim, user_id=UUID(t.staff_one))
    await _on(
        pool, s, transitions.assign, assignee_id=UUID(t.staff_one), actor_user_id=UUID(t.admin)
    )
    assert _states(migrated_dsn, s) == {
        "inbox.overdue": ["scheduled"],
        "inbox.stale_claim": ["scheduled"],
    }
    assert _assigned(migrated_dsn, t.client_id) == []


# --- inbox.assigned -------------------------------------------------------------------------------


async def test_an_assignment_by_someone_else_tells_the_assignee_once(
    migrated_dsn: str, app_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = Team(migrated_dsn)
    _chat_target(migrated_dsn, t.client_id)
    s = _seed(
        migrated_dsn, t.client_id, owned_by="human_support", **_needs_staff("escalation", "high")
    )
    _inbound(app_dsn, s)  # body "Synthetic question": never in a notification
    await _on(
        pool, s, transitions.assign, assignee_id=UUID(t.staff_two), actor_user_id=UUID(t.admin)
    )
    claimed_at: datetime = _state(migrated_dsn, s)["claimed_at"]
    [row] = _assigned(migrated_dsn, t.client_id)
    assert row["user_id"] == t.staff_two
    assert row["message_key"] == ASSIGNED and row["priority"] == "high"
    assert row["dedupe_key"] == f"{ASSIGNED}:{s.conversation_id}:{cycle_epoch(claimed_at)}"
    assert row["params"] == {
        "conversation_id": s.conversation_id,
        "customer": "Synthetic Customer",
        "priority": "high",
        "assigned_by": t.admin,
    }
    assert set(row["params"]) == ASSIGNED_PARAMS
    assert "Synthetic question" not in json.dumps(row["params"])
    assert row["link"] == {"route": "inbox", "conversation": s.conversation_id}
    assert _chat(migrated_dsn, t.client_id) == []  # console only
    # The same assignment again changes nothing and sends nothing.
    await _on(
        pool, s, transitions.assign, assignee_id=UUID(t.staff_two), actor_user_id=UUID(t.admin)
    )
    assert len(_assigned(migrated_dsn, t.client_id)) == 1
    # A reassignment is a new claim: the new assignee is told, under a new key.
    await _on(
        pool, s, transitions.assign, assignee_id=UUID(t.staff_one), actor_user_id=UUID(t.admin)
    )
    rows = _assigned(migrated_dsn, t.client_id)
    assert [r["user_id"] for r in rows] == [t.staff_two, t.staff_one]
    assert rows[0]["dedupe_key"] != rows[1]["dedupe_key"]


async def test_no_announcement_for_a_self_claim_a_self_assignment_a_reply_or_a_takeover(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = Team(migrated_dsn)
    claimed = _seed(migrated_dsn, t.client_id, owned_by="human_support", **_needs_staff("manual"))
    await _on(pool, claimed, transitions.claim, user_id=UUID(t.staff_one))
    assigned = _seed(migrated_dsn, t.client_id, owned_by="human_support", **_needs_staff("manual"))
    await _on(
        pool, assigned, transitions.assign, assignee_id=UUID(t.admin), actor_user_id=UUID(t.admin)
    )
    replied = _seed(migrated_dsn, t.client_id, owned_by="human_support", **_needs_staff("manual"))
    await _on(pool, replied, transitions.replied, user_id=UUID(t.staff_one))
    assert _state(migrated_dsn, replied)["assignee_user_id"] == UUID(t.staff_one)  # auto-claim
    taken = _seed(migrated_dsn, t.client_id, owned_by="human_support")
    assert await _on(
        pool,
        taken,
        transitions.enter,
        reason="manual",
        actor_user_id=UUID(t.staff_two),
        claim_for=UUID(t.staff_two),
    )
    assert _state(migrated_dsn, taken)["assignee_user_id"] == UUID(t.staff_two)
    assert _assigned(migrated_dsn, t.client_id) == []


async def test_an_assignment_rolled_back_sends_nothing(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    t = Team(migrated_dsn)
    s = _seed(migrated_dsn, t.client_id, owned_by="human_support", **_needs_staff("manual"))

    class Abort(Exception):
        pass

    with pytest.raises(Abort):
        async with tx(pool, t.client_id) as conn:
            await transitions.assign(
                conn,
                UUID(t.client_id),
                UUID(s.conversation_id),
                assignee_id=UUID(t.staff_one),
                actor_user_id=UUID(t.admin),
            )
            raise Abort
    assert _assigned(migrated_dsn, t.client_id) == []


# --- lock order and import order ------------------------------------------------------------------


async def test_a_transition_does_not_wait_for_a_reminder_the_fire_job_holds(
    migrated_dsn: str, app_dsn: str, pool: AsyncConnectionPool
) -> None:
    """The fire job holds its reminder row (claim_due.sql, FOR UPDATE) and the snooze_due handler
    then locks the conversation; a transition holds the conversation and then cancels. The cancel
    skips the held row, so resolve returns at once and the held row is left as it is (its
    handler's re-check drops it at the next fire).
    """
    t = Team(migrated_dsn)
    s = await _claimed_item(migrated_dsn, pool, t, holder=t.staff_one)
    [held] = _reminders(migrated_dsn, s, "inbox.overdue")
    async with await psycopg.AsyncConnection.connect(app_dsn) as fire_conn:
        async with fire_conn.transaction():
            await fire_conn.execute("SELECT set_config('app.client_id', %s, true)", (t.client_id,))
            await fire_conn.execute(
                "SELECT id FROM reminders WHERE id = %s FOR UPDATE", (held["id"],)
            )
            resolved = await asyncio.wait_for(
                _on(
                    pool,
                    s,
                    transitions.resolve,
                    actor_user_id=UUID(t.admin),
                    seen_customer_at=None,
                ),
                timeout=10,
            )
            assert resolved is True
            # The fire job can now take the conversation lock too: nothing waits on it.
            await asyncio.wait_for(
                fire_conn.execute(
                    "SELECT id FROM conversations WHERE id = %s FOR UPDATE", (s.conversation_id,)
                ),
                timeout=10,
            )
    states = _states(migrated_dsn, s)
    assert states["inbox.overdue"] == ["scheduled"]  # held: skipped, untouched
    assert states["inbox.stale_claim"] == ["cancelled"]
    assert _state(migrated_dsn, s)["queue_status"] == "resolved"
    # The skipped reminder drops itself when it fires.
    stats = await fire.fire_due(
        pool, UUID(t.client_id), FakeClock(datetime.now(UTC) + timedelta(days=1))
    )
    assert stats["dropped"] == 1 and stats["fired"] == 0


@pytest.mark.parametrize(
    "order",
    [
        ("csa.inbox_queue.transitions", "csa.inbox_queue.reminders"),
        ("csa.inbox_queue.reminders", "csa.inbox_queue.transitions"),
    ],
)
def test_transitions_and_reminders_import_in_either_order(order: tuple[str, str]) -> None:
    code = "import importlib\n" + "".join(f"importlib.import_module({m!r})\n" for m in order)
    code += (
        "from csa.inbox_queue import reminders, transitions\n"
        "assert callable(transitions.wake_one) and callable(reminders.schedule_follow_up)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
