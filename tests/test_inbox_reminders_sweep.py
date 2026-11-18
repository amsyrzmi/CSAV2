"""The inbox_reminders_sweep job: it schedules the reminders the SQL triggers cannot (plan 2a,
task 5, decisions 1 to 3; design 7.3; scenarios 18 and 19). Disposable database, synthetic data.

Items enter Needs us through the real 0622 triggers (a customer message, an escalation, or
``inbox_queue_enter``) wherever the test is about that path; a backfilled or snoozed item from
before this PR is written as the owner, as the 0620 backfill left it. The sweep runs on an
app-role company transaction with an explicit ``now``. The last test explains the three sweep
queries on 50,000 conversations, 800 of them needing staff: one indexed read each, no sort.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, LiteralString, cast
from uuid import UUID

import psycopg
import pytest
from csa.core import db
from csa.core.tenancy import tx
from csa.inbox_queue import transitions
from csa.inbox_queue.reminders import (
    SUBJECT_TYPE,
    SWEEP_QUERIES,
    cycle_epoch,
    register_inbox_reminder_kinds,
    reminder_key,
    schedule_missing,
    sweep_params,
)
from csa.jobs.clock import FakeClock
from csa.jobs.inbox_reminders import run_inbox_reminders_sweep
from csa.jobs.runner import JobRunner, build_default_registry
from csa.reminders import fire
from csa.reminders import registry as kinds
from csa.reminders.registry import ReminderRegistry
from psycopg_pool import AsyncConnectionPool

from tests.conftest import requires_docker
from tests.test_bookings_schema import _owner
from tests.test_console_notifications_schema import member
from tests.test_inbox_queue_triggers import (
    Seeded,
    _enter,
    _escalate,
    _inbound,
    _needs_staff,
    _seed,
    _settings,
    _state,
    _user,
)
from tests.test_training_schema import _company

pytestmark = requires_docker

NONE = {"inbox.overdue": 0, "inbox.stale_claim": 0, "inbox.snooze_due": 0}
TARGETS = {"urgent": 15, "high": 60, "normal": 240, "low": 1440}


@pytest.fixture
async def pool(app_dsn: str) -> AsyncIterator[AsyncConnectionPool]:
    async with db.build_pool(app_dsn, max_size=2) as made:
        yield made


async def _sweep(
    pool: AsyncConnectionPool, client_id: str, now: datetime, **extra: Any
) -> dict[str, int]:
    async with tx(pool, client_id) as conn:
        return await schedule_missing(conn, UUID(client_id), now, **extra)


def _reminders(dsn: str, client_id: str, kind: str | None = None) -> list[dict[str, Any]]:
    with _owner(dsn) as conn:
        return conn.execute(
            "SELECT * FROM reminders WHERE client_id = %s AND (%s::text IS NULL OR kind = %s)"
            " ORDER BY created_at, dedupe_key",
            (client_id, kind, kind),
        ).fetchall()


def _of(dsn: str, s: Seeded, kind: str) -> list[dict[str, Any]]:
    return [r for r in _reminders(dsn, s.client_id, kind) if r["subject_id"] == s.conversation_id]


def _counts(**changed: int) -> dict[str, int]:
    return {**NONE, **{f"inbox.{k}": v for k, v in changed.items()}}


# --- inbox.overdue --------------------------------------------------------------------------------


async def test_a_trigger_made_item_gets_one_overdue_reminder_and_a_second_sweep_adds_none(
    migrated_dsn: str, app_dsn: str, pool: AsyncConnectionPool
) -> None:
    c = _company(migrated_dsn)
    written = _seed(migrated_dsn, c, owned_by="human_support")
    _inbound(app_dsn, written)  # the message trigger: a person owns it, so Needs us
    escalated = _seed(migrated_dsn, c)
    assert _escalate(app_dsn, escalated, "high")  # the escalation trigger
    now = datetime.now(UTC)
    assert await _sweep(pool, c, now) == _counts(overdue=2)
    for s in (written, escalated):
        item = _state(migrated_dsn, s)
        assert item["queue_status"] == "needs_staff"
        [reminder] = _of(migrated_dsn, s, "inbox.overdue")
        cycle = item["needs_staff_since"]
        assert reminder["state"] == "scheduled" and reminder["fire_count"] == 0
        assert reminder["due_at"] == item["due_at"]  # due in the future: the item's own due time
        assert reminder["subject_type"] == SUBJECT_TYPE == "conversation"
        assert reminder["dedupe_key"] == reminder_key(
            "inbox.overdue", UUID(s.conversation_id), cycle
        )
        assert reminder["payload"] == {
            "conversation_id": s.conversation_id,
            "cycle": cycle_epoch(cycle),
        }
    assert await _sweep(pool, c, now + timedelta(minutes=1)) == NONE
    assert len(_reminders(migrated_dsn, c)) == 2


async def test_scenario_19_backfilled_items_get_one_fresh_clock_reminder_and_no_burst(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    c = _company(migrated_dsn)
    old = {
        priority: _seed(
            migrated_dsn,
            c,
            owned_by="human_support",
            **_needs_staff("escalation", priority, minutes_ago=3 * 24 * 60),
        )
        for priority in TARGETS
    }
    now = datetime.now(UTC)
    assert await _sweep(pool, c, now) == _counts(overdue=4)
    for priority, s in old.items():
        assert _state(migrated_dsn, s)["due_at"] < now  # long overdue
        [reminder] = _of(migrated_dsn, s, "inbox.overdue")
        assert reminder["due_at"] == now + timedelta(minutes=TARGETS[priority]), priority
    # Nothing is due now, and the next sweeps leave the fresh clocks alone.
    assert await fire.fire_due(pool, UUID(c), FakeClock(now), handlers=ReminderRegistry()) == {
        "fired": 0,
        "dropped": 0,
        "failed": 0,
        "dead": 0,
        "unknown": 0,
    }
    assert await _sweep(pool, c, now + timedelta(minutes=5)) == NONE
    for priority, s in old.items():
        [reminder] = _of(migrated_dsn, s, "inbox.overdue")
        assert reminder["due_at"] == now + timedelta(minutes=TARGETS[priority])


async def test_a_priority_rise_moves_a_scheduled_reminder_and_not_a_fired_one(
    migrated_dsn: str, pool: AsyncConnectionPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = _company(migrated_dsn)
    admin = member(migrated_dsn, c, "tenant_admin")
    waiting = _seed(
        migrated_dsn, c, owned_by="human_support", **_needs_staff("customer_message", "normal", 10)
    )
    fired = _seed(
        migrated_dsn, c, owned_by="human_support", **_needs_staff("customer_message", "normal", 20)
    )
    backfilled = _seed(
        migrated_dsn, c, owned_by="human_support", **_needs_staff("escalation", "normal", 2000)
    )
    now = datetime.now(UTC)
    assert await _sweep(pool, c, now) == _counts(overdue=3)
    # One reminder fires (first fire done, a repeat scheduled).
    monkeypatch.setattr(kinds, "registry", ReminderRegistry())
    register_inbox_reminder_kinds()
    fired_due = _of(migrated_dsn, fired, "inbox.overdue")[0]["due_at"]
    assert (await fire.fire_due(pool, UUID(c), FakeClock(fired_due)))["fired"] == 1
    [after_fire] = _of(migrated_dsn, fired, "inbox.overdue")
    assert after_fire["fire_count"] == 1
    for s in (waiting, fired, backfilled):
        async with tx(pool, c) as conn:
            await transitions.set_priority(
                conn, UUID(c), UUID(s.conversation_id), priority="urgent", actor_user_id=UUID(admin)
            )
    later = now + timedelta(minutes=1)
    assert await _sweep(pool, c, later) == _counts(overdue=2)
    # Still in the future: the item's new due time.
    item = _state(migrated_dsn, waiting)
    assert item["due_at"] == item["needs_staff_since"] + timedelta(minutes=15)
    assert _of(migrated_dsn, waiting, "inbox.overdue")[0]["due_at"] == item["due_at"]
    # Long overdue: the fresh clock of the new priority, when that is sooner.
    assert _of(migrated_dsn, backfilled, "inbox.overdue")[0]["due_at"] == later + timedelta(
        minutes=15
    )
    # Fired already: left as it is.
    [unchanged] = _of(migrated_dsn, fired, "inbox.overdue")
    assert unchanged["due_at"] == after_fire["due_at"] and unchanged["fire_count"] == 1
    # And it stays put on the following sweeps.
    assert await _sweep(pool, c, later + timedelta(minutes=1)) == NONE


async def test_a_finished_cycle_is_not_rescheduled_and_a_new_cycle_gets_a_new_key(
    migrated_dsn: str, app_dsn: str, pool: AsyncConnectionPool
) -> None:
    c = _company(migrated_dsn)
    actor = member(migrated_dsn, c, "tenant_admin")
    s = _seed(migrated_dsn, c, owned_by="human_support")
    assert _enter(app_dsn, s, "manual")
    now = datetime.now(UTC)
    assert (await _sweep(pool, c, now))["inbox.overdue"] == 1
    [first] = _of(migrated_dsn, s, "inbox.overdue")
    with _owner(migrated_dsn) as conn:
        conn.execute("UPDATE reminders SET state = 'done' WHERE id = %s", (first["id"],))
    # The same cycle, finished: nothing again.
    assert await _sweep(pool, c, now) == NONE
    async with tx(pool, c) as conn:
        await transitions.resolve(
            conn, UUID(c), UUID(s.conversation_id), actor_user_id=UUID(actor), seen_customer_at=None
        )
    assert await _sweep(pool, c, now) == NONE  # resolved: not in Needs us
    assert _enter(app_dsn, s, "manual")  # back in Needs us with a new clock
    assert (await _sweep(pool, c, now + timedelta(minutes=1)))["inbox.overdue"] == 1
    old, new = _of(migrated_dsn, s, "inbox.overdue")
    assert old["id"] == first["id"] and old["state"] == "done"
    assert new["state"] == "scheduled" and new["dedupe_key"] != old["dedupe_key"]


async def test_the_sql_and_python_cycle_values_are_identical(migrated_dsn: str) -> None:
    """The sweep's SQL and reminder_key make the same key text (Task 6 builds keys in Python)."""
    times = [
        datetime(2030, 1, 1, 12, 0, 5, 999_999, tzinfo=UTC),
        datetime(2030, 1, 1, 12, 0, 5, 1, tzinfo=UTC),
        datetime(2026, 11, 18, 3, 4, 5, 123_457, tzinfo=UTC),
        datetime(2026, 11, 18, 3, 4, 5, tzinfo=UTC),
        datetime(1969, 12, 31, 23, 59, 59, 500_000, tzinfo=UTC),
        datetime.now(UTC),
    ]
    with _owner(migrated_dsn) as conn:
        for at in times:
            [row] = conn.execute(
                "SELECT (extract(epoch FROM %s::timestamptz) * 1000000)::bigint::text AS n", (at,)
            ).fetchall()
            assert row["n"] == str(cycle_epoch(at)), at


async def test_two_cycles_in_the_same_second_get_two_overdue_reminders(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    c = _company(migrated_dsn)
    first = datetime.now(UTC).replace(microsecond=100_000) - timedelta(minutes=5)
    s = _seed(
        migrated_dsn,
        c,
        owned_by="human_support",
        **_needs_staff("customer_message"),
    )

    def clock(since: datetime) -> None:
        with _owner(migrated_dsn) as conn:
            conn.execute(
                "UPDATE conversations SET needs_staff_since = %s, due_at = %s WHERE id = %s",
                (since, since + timedelta(minutes=240), s.conversation_id),
            )

    clock(first)
    now = datetime.now(UTC)
    assert (await _sweep(pool, c, now))["inbox.overdue"] == 1
    with _owner(migrated_dsn) as conn:  # the first cycle's reminder was cancelled, as Task 6 does
        conn.execute("UPDATE reminders SET state = 'cancelled' WHERE client_id = %s", (c,))
    second = first.replace(microsecond=600_000)  # the same second, a new clock
    clock(second)
    assert (await _sweep(pool, c, now))["inbox.overdue"] == 1
    old, new = _of(migrated_dsn, s, "inbox.overdue")
    assert old["state"] == "cancelled" and new["state"] == "scheduled"
    conversation = UUID(s.conversation_id)
    assert old["dedupe_key"] == reminder_key("inbox.overdue", conversation, first)
    assert new["dedupe_key"] == reminder_key("inbox.overdue", conversation, second)
    assert new["due_at"] == second + timedelta(minutes=240)


async def test_the_sweep_is_bounded_by_the_batch_size(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    c = _company(migrated_dsn)
    for minutes in range(5):
        _seed(migrated_dsn, c, **_needs_staff("customer_message", minutes_ago=minutes))
    now = datetime.now(UTC)
    assert (await _sweep(pool, c, now, limit=2))["inbox.overdue"] == 2
    assert (await _sweep(pool, c, now, limit=2))["inbox.overdue"] == 2
    assert (await _sweep(pool, c, now, limit=2))["inbox.overdue"] == 1
    assert (await _sweep(pool, c, now, limit=2))["inbox.overdue"] == 0
    assert len(_reminders(migrated_dsn, c, "inbox.overdue")) == 5


# --- inbox.stale_claim and inbox.snooze_due -------------------------------------------------------


async def test_a_claimed_item_gets_a_stale_claim_reminder(
    migrated_dsn: str, app_dsn: str, pool: AsyncConnectionPool
) -> None:
    c = _company(migrated_dsn)
    person = _user(migrated_dsn)
    taken = _seed(migrated_dsn, c, owned_by="human_support")
    assert _enter(app_dsn, taken, "manual", actor=person, claim_for=person)
    long_ago = datetime.now(UTC) - timedelta(hours=20)
    old_claim = _seed(
        migrated_dsn,
        c,
        owned_by="human_support",
        **_needs_staff("customer_message", minutes_ago=30 * 60),
        assignee_user_id=person,
        claimed_at=long_ago,
    )
    now = datetime.now(UTC)
    assert await _sweep(pool, c, now) == _counts(overdue=2, stale_claim=2)
    claimed_at = _state(migrated_dsn, taken)["claimed_at"]
    [fresh] = _of(migrated_dsn, taken, "inbox.stale_claim")
    assert fresh["due_at"] == claimed_at + timedelta(hours=8)
    assert fresh["dedupe_key"] == reminder_key(
        "inbox.stale_claim", UUID(taken.conversation_id), claimed_at
    )
    [old] = _of(migrated_dsn, old_claim, "inbox.stale_claim")
    assert old["due_at"] == now + timedelta(hours=8)  # a fresh clock, no burst
    assert await _sweep(pool, c, now) == NONE


async def test_stale_claim_hours_zero_schedules_none(
    migrated_dsn: str, app_dsn: str, pool: AsyncConnectionPool
) -> None:
    c = _company(migrated_dsn)
    _settings(migrated_dsn, c, stale_claim_hours=0)
    person = _user(migrated_dsn)
    s = _seed(migrated_dsn, c, owned_by="human_support")
    assert _enter(app_dsn, s, "manual", actor=person, claim_for=person)
    assert await _sweep(pool, c, datetime.now(UTC)) == _counts(overdue=1)


async def test_a_snoozed_item_without_a_reminder_gets_one_at_its_wake_time(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    c = _company(migrated_dsn)
    until = datetime.now(UTC) + timedelta(hours=3)
    s = _seed(
        migrated_dsn, c, owned_by="human_support", queue_status="snoozed", snoozed_until=until
    )
    now = datetime.now(UTC)
    assert await _sweep(pool, c, now) == _counts(snooze_due=1)
    [reminder] = _of(migrated_dsn, s, "inbox.snooze_due")
    assert reminder["due_at"] == until
    assert reminder["dedupe_key"] == reminder_key(
        "inbox.snooze_due", UUID(s.conversation_id), until
    )
    assert await _sweep(pool, c, now) == NONE


# --- scenario 18 ----------------------------------------------------------------------------------


def _console_chat(dsn: str, client_id: str, seeded: Seeded, *, thread: bool) -> None:
    user = _user(dsn)
    credential, endpoint = str(uuid.uuid4()), str(uuid.uuid4())
    with _owner(dsn) as conn:
        conn.execute(
            "INSERT INTO channel_credentials (id, client_id, channel) VALUES (%s, %s, 'webhook')",
            (credential, client_id),
        )
        conn.execute(
            "INSERT INTO webhook_endpoints (id, client_id, public_id, credential_id) VALUES (%s,"
            " %s, %s, %s)",
            (endpoint, client_id, f"endpoint-{uuid.uuid4().hex}", credential),
        )
        conn.execute(
            "INSERT INTO console_chat_participants (client_id, user_id, contact_id,"
            " endpoint_id) VALUES (%s, %s, %s, %s)",
            (client_id, user, seeded.contact_id, endpoint),
        )
        if thread:
            conn.execute(
                "INSERT INTO console_chat_threads (id, client_id, user_id, contact_id,"
                " endpoint_id, request_key) VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    seeded.conversation_id,
                    client_id,
                    user,
                    seeded.contact_id,
                    endpoint,
                    str(uuid.uuid4()),
                ),
            )


async def test_scenario_18_sandbox_and_console_chat_conversations_never_get_reminders(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    c = _company(migrated_dsn)
    person = _user(migrated_dsn)
    now = datetime.now(UTC)
    # Written past the triggers on purpose: even a row that somehow holds a queue state is skipped.
    states = (
        {**_needs_staff("manual"), "assignee_user_id": person, "claimed_at": now},
        {"queue_status": "snoozed", "snoozed_until": now + timedelta(hours=1)},
    )
    for state in states:
        _seed(migrated_dsn, c, channel="web", owned_by="human_support", **state)
        thread = _seed(migrated_dsn, c, owned_by="human_support", **state)
        _console_chat(migrated_dsn, c, thread, thread=True)
        participant = _seed(migrated_dsn, c, owned_by="human_support", **state)
        _console_chat(migrated_dsn, c, participant, thread=False)
    assert await _sweep(pool, c, now) == NONE
    assert _reminders(migrated_dsn, c) == []


# --- the job --------------------------------------------------------------------------------------


async def test_the_job_is_registered_every_minute_for_each_company() -> None:
    job = build_default_registry().get("inbox_reminders_sweep")
    assert job is not None
    assert job.every == timedelta(minutes=1)
    assert job.per_client is True and job.own_transactions is False
    assert job.fn is run_inbox_reminders_sweep


async def test_the_runner_runs_the_sweep_and_records_its_counts(
    migrated_dsn: str, app_dsn: str
) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:  # the runner serves pilot and active companies only
        conn.execute("UPDATE clients SET status = 'active' WHERE id = %s", (c,))
    s = _seed(migrated_dsn, c, owned_by="human_support")
    assert _enter(app_dsn, s, "manual")
    async with (
        db.build_pool(migrated_dsn, max_size=2, autocommit=True) as owner_pool,
        db.build_pool(app_dsn, max_size=2, autocommit=True) as app_pool,
    ):
        runner = JobRunner(owner_pool, app_pool, lock_conninfo=migrated_dsn, clock=FakeClock())
        assert await runner.run_job_once("inbox_reminders_sweep") is True
    with _owner(migrated_dsn) as conn:
        [run] = conn.execute(
            "SELECT ok, stats FROM job_runs WHERE job = 'inbox_reminders_sweep' AND client_id = %s",
            (c,),
        ).fetchall()
    assert run["ok"] is True and run["stats"] == _counts(overdue=1)
    assert len(_of(migrated_dsn, s, "inbox.overdue")) == 1


# --- the plan at scale ----------------------------------------------------------------------------

TOTAL = 50_000
NEEDS = 800
CLAIMED = 100
SNOOZED = 200
QUEUE_INDEXES = frozenset(
    {
        "conversations_queue_due_idx",
        "conversations_queue_since_idx",
        "conversations_queue_mine_idx",
        "conversations_queue_snoozed_idx",
    }
)

SEED_CONTACTS: LiteralString = """
INSERT INTO public.contacts (id, client_id, full_name)
SELECT md5('sweep-contact-' || %(client)s::text || '-' || i)::uuid, %(client)s,
       'Synthetic Person ' || i
FROM generate_series(1, %(n)s) AS i
"""
SEED_CONVERSATIONS: LiteralString = """
INSERT INTO public.conversations (id, client_id, contact_id, channel, channel_thread)
SELECT md5('sweep-conversation-' || %(client)s::text || '-' || i)::uuid, %(client)s,
       md5('sweep-contact-' || %(client)s::text || '-' || i)::uuid,
       CASE WHEN i %% 2 = 0 THEN 'telegram' ELSE 'whatsapp' END,
       'sweep-thread-' || %(client)s::text || '-' || i
FROM generate_series(1, %(n)s) AS i
"""
SEED_QUEUE: LiteralString = """
UPDATE public.conversations c
SET queue_status = CASE WHEN g.i <= %(needs)s THEN 'needs_staff' ELSE 'snoozed' END,
    queue_reason = CASE WHEN g.i <= %(needs)s THEN 'customer_message' END,
    needs_staff_since = CASE WHEN g.i <= %(needs)s THEN now() - g.i * interval '1 minute' END,
    due_at = CASE WHEN g.i <= %(needs)s
                  THEN now() - g.i * interval '1 minute' + interval '240 minutes' END,
    assignee_user_id = CASE WHEN g.i <= %(claimed)s THEN %(person)s::uuid END,
    claimed_at = CASE WHEN g.i <= %(claimed)s THEN now() END,
    snoozed_until = CASE WHEN g.i > %(needs)s THEN now() + g.i * interval '1 minute' END
FROM generate_series(1, %(needs)s + %(snoozed)s) AS g (i)
WHERE c.id = md5('sweep-conversation-' || %(client)s::text || '-' || g.i)::uuid
"""
# Earlier cycles' finished reminders, one per conversation: the sweep must probe the key of the
# current cycle, not read the table.
SEED_REMINDERS: LiteralString = """
INSERT INTO public.reminders
    (client_id, kind, subject_type, subject_id, due_at, state, fire_count, dedupe_key)
SELECT %(client)s, 'inbox.overdue', 'conversation', c.id::text, now() - interval '30 days',
       'done', 1, 'inbox.overdue:' || c.id::text || ':1700000000'
FROM public.conversations c
WHERE c.client_id = %(client)s
"""


def _nodes(plan: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield plan
    for child in plan.get("Plans", []):
        yield from _nodes(child)


def _summary(plan: dict[str, Any]) -> str:
    return " > ".join(
        " ".join(
            p for p in (n["Node Type"], n.get("Relation Name", ""), n.get("Index Name", "")) if p
        )
        for n in _nodes(plan)
    )


@pytest.mark.slow
async def test_each_sweep_query_reads_a_queue_index_and_sorts_nothing_at_scale(
    migrated_dsn: str, app_dsn: str
) -> None:
    client, person = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clients (id, slug, name, platform_version) VALUES (%s, %s, 'Synthetic"
            " Co', 'test')",
            (client, f"sweep-{client}"),
        )
        conn.execute(
            "INSERT INTO console_users (id, email, display_name) VALUES (%s, %s, 'Synthetic')",
            (person, f"{person}@example.test"),
        )
        for statement in (SEED_CONTACTS, SEED_CONVERSATIONS, SEED_QUEUE, SEED_REMINDERS):
            conn.execute(
                statement,
                {
                    "client": client,
                    "n": TOTAL,
                    "needs": NEEDS,
                    "claimed": CLAIMED,
                    "snoozed": SNOOZED,
                    "person": person,
                },
            )
        conn.execute("ANALYZE public.contacts, public.conversations, public.reminders")
    now = datetime.now(UTC)
    async with db.build_pool(app_dsn, max_size=1) as pool:
        async with tx(pool, str(client)) as conn:
            for kind, path in SWEEP_QUERIES.items():
                params = await sweep_params(conn, client, now, limit=100)
                text = cast(LiteralString, "EXPLAIN (FORMAT JSON) " + db.q.load(path))
                row = await (await conn.execute(text, params)).fetchone()
                assert row is not None
                plan = row["QUERY PLAN"][0]["Plan"]
                nodes = list(_nodes(plan))
                where = (kind, _summary(plan))
                print(where)  # the report (``-s``)
                assert not [n for n in nodes if "Sort" in n["Node Type"]], where
                assert not [
                    n
                    for n in nodes
                    if n["Node Type"] == "Seq Scan"
                    and n.get("Relation Name") in ("conversations", "reminders")
                ], where
                used = {
                    n.get("Index Name") for n in nodes if n.get("Relation Name") == "conversations"
                }
                assert used & QUEUE_INDEXES, where
        # And the sweep itself schedules all of them across runs, 100 at a time.
        totals = dict(NONE)
        for _ in range(12):
            async with tx(pool, str(client)) as conn:
                counts = await schedule_missing(conn, client, now, limit=100)
            for kind, n in counts.items():
                totals[kind] += n
            if not any(counts.values()):
                break
    assert totals == {
        "inbox.overdue": NEEDS,
        "inbox.stale_claim": CLAIMED,
        "inbox.snooze_due": SNOOZED,
    }
