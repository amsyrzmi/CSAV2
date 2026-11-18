"""csa.reminders: the reminders_fire job and the handler registry (disposable database, synthetic).

The job runs on an app-role pool, one company transaction per reminder, as the worker runs it.
Handlers are fake kinds registered by the tests (no Inbox import) and delivery is a recorder, so
what was sent, with which key, is checked here; rows are read back through the owner.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from csa import reminders
from csa.core import db
from csa.jobs.clock import FakeClock
from csa.jobs.reminders import run_reminders_fire
from csa.reminders import fire
from csa.reminders import registry as kinds
from csa.reminders.registry import (
    Decision,
    HandlerContext,
    Notice,
    ReminderRegistry,
)
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from structlog.testing import capture_logs

from tests.conftest import requires_docker
from tests.test_bookings_schema import _owner
from tests.test_training_schema import _company

NOW = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
PERSON = uuid.UUID("00000000-0000-4000-8000-000000000001")


class Crash(BaseException):
    """A worker dying mid-row: not an Exception, so nothing in the job catches it."""


class Recorder:
    """Stands in for notify_people: records each delivery, and can crash once after recording."""

    def __init__(self, *, crash_once: bool = False) -> None:
        self.calls: list[tuple[uuid.UUID, Notice, str]] = []
        self._crash = crash_once

    async def __call__(
        self, conn: psycopg.AsyncConnection[Any], client_id: uuid.UUID, notice: Notice, key: str
    ) -> None:
        self.calls.append((client_id, notice, key))
        if self._crash:
            self._crash = False
            raise Crash

    @property
    def keys(self) -> list[str]:
        return [key for _, _, key in self.calls]


def _notice(**extra: Any) -> Notice:
    return Notice(
        recipients=(PERSON,),
        kind="demo.due",
        priority="normal",
        message_key="demo.due",
        params={"n": 1},
        link={"subject_id": "s-1"},
        **extra,
    )


def _put(
    dsn: str,
    client_id: str,
    *,
    kind: str = "demo.due",
    due_at: datetime = NOW - timedelta(minutes=1),
    subject_id: str = "s-1",
    payload: dict[str, Any] | None = None,
) -> uuid.UUID:
    """A scheduled reminder written as the owner."""
    with _owner(dsn) as conn:
        [row] = conn.execute(
            "INSERT INTO reminders (client_id, kind, subject_type, subject_id, due_at, payload,"
            " dedupe_key) VALUES (%s, %s, 'thing', %s, %s, %s, %s) RETURNING id",
            (client_id, kind, subject_id, due_at, Jsonb(payload or {}), str(uuid.uuid4())),
        )
    return row["id"]


def _row(dsn: str, reminder_id: uuid.UUID) -> dict[str, Any]:
    with _owner(dsn) as conn:
        [row] = conn.execute("SELECT * FROM reminders WHERE id = %s", (reminder_id,))
    return row


def _handlers(kind: str, handler: Any) -> ReminderRegistry:
    made = ReminderRegistry()
    made.register(kind, handler)
    return made


def _stats(**counts: int) -> dict[str, int]:
    return {"fired": 0, "dropped": 0, "failed": 0, "dead": 0, "unknown": 0, **counts}


@pytest.fixture
async def pool(app_dsn: str) -> AsyncIterator[AsyncConnectionPool]:
    async with db.build_pool(app_dsn, max_size=4) as made:
        yield made


async def test_registry_refuses_a_kind_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kinds, "registry", ReminderRegistry())

    async def handler(ctx: HandlerContext) -> Decision:
        return Decision()

    kinds.register_kind("demo.due", handler)
    assert kinds.registry.get("demo.due") is handler
    assert kinds.registry.get("demo.other") is None
    with pytest.raises(ValueError, match=r"demo\.due"):
        kinds.register_kind("demo.due", handler)
    assert kinds.registry.kinds() == ["demo.due"]


@requires_docker
async def test_a_kind_fires_rechecks_its_subject_delivers_once_and_finishes(
    migrated_dsn: str, pool: AsyncConnectionPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 12: a kind registered with no Inbox import fires through the process registry."""
    monkeypatch.setattr(kinds, "registry", ReminderRegistry())
    c = _company(migrated_dsn)
    made = _put(migrated_dsn, c, payload={"step": "first"})
    seen: list[HandlerContext] = []
    still_open = {"s-1"}

    async def handler(ctx: HandlerContext) -> Decision:
        seen.append(ctx)
        # The re-check runs on the company's own transaction.
        cur = await ctx.conn.execute("SELECT current_setting('app.client_id') AS c")
        assert (await cur.fetchone())["c"] == c  # type: ignore[index]
        if ctx.reminder.subject_id not in still_open:
            return Decision()
        return Decision(notices=(_notice(),))

    kinds.register_kind("demo.due", handler)
    sent = Recorder()
    assert await fire.fire_due(pool, uuid.UUID(c), FakeClock(NOW), deliver=sent) == _stats(fired=1)
    [ctx] = seen
    assert ctx.client_id == uuid.UUID(c) and ctx.now == NOW
    r = ctx.reminder
    assert (r.id, r.kind, r.subject_type, r.subject_id) == (made, "demo.due", "thing", "s-1")
    assert r.due_at == NOW - timedelta(minutes=1) and r.fire_count == 0
    assert r.payload == {"step": "first"}
    assert sent.calls == [(uuid.UUID(c), _notice(), f"{made}:1")]
    row = _row(migrated_dsn, made)
    assert row["state"] == "done" and row["fire_count"] == 1 and row["last_fired_at"] == NOW
    assert row["attempts"] == 0 and row["last_error"] is None
    # Done is done: a later run neither calls the handler nor delivers.
    later = FakeClock(NOW + timedelta(hours=1))
    assert await fire.fire_due(pool, uuid.UUID(c), later, deliver=sent) == _stats()
    assert len(seen) == 1 and len(sent.calls) == 1


@requires_docker
async def test_a_next_due_at_reschedules_and_counts_the_fire(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    c = _company(migrated_dsn)
    made = _put(migrated_dsn, c)

    async def handler(ctx: HandlerContext) -> Decision:
        again = ctx.now + timedelta(hours=1) if ctx.reminder.fire_count == 0 else None
        return Decision(notices=(_notice(),), next_due_at=again)

    handlers = _handlers("demo.due", handler)
    sent = Recorder()
    clock = FakeClock(NOW)
    ran = await fire.fire_due(pool, uuid.UUID(c), clock, handlers=handlers, deliver=sent)
    assert ran == _stats(fired=1)
    row = _row(migrated_dsn, made)
    assert row["state"] == "scheduled" and row["due_at"] == NOW + timedelta(hours=1)
    assert row["fire_count"] == 1 and row["last_fired_at"] == NOW
    # Not due yet: nothing.
    clock.advance(timedelta(minutes=59))
    assert await fire.fire_due(pool, uuid.UUID(c), clock, handlers=handlers, deliver=sent) == (
        _stats()
    )
    clock.advance(timedelta(minutes=1))
    ran = await fire.fire_due(pool, uuid.UUID(c), clock, handlers=handlers, deliver=sent)
    assert ran == _stats(fired=1)
    assert sent.keys == [f"{made}:1", f"{made}:2"]
    row = _row(migrated_dsn, made)
    assert row["state"] == "done" and row["fire_count"] == 2
    assert row["last_fired_at"] == NOW + timedelta(hours=1)


@requires_docker
async def test_an_empty_decision_is_dropped_silently(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    c = _company(migrated_dsn)
    made = _put(migrated_dsn, c)

    async def handler(ctx: HandlerContext) -> Decision:
        return Decision()

    sent = Recorder()
    ran = await fire.fire_due(
        pool, uuid.UUID(c), FakeClock(NOW), handlers=_handlers("demo.due", handler), deliver=sent
    )
    assert ran == _stats(dropped=1) and sent.calls == []
    row = _row(migrated_dsn, made)
    assert row["state"] == "done" and row["fire_count"] == 0 and row["last_fired_at"] is None


@requires_docker
async def test_an_unknown_kind_waits_for_a_worker_that_knows_it(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    """A process without the kind (an older worker in a rolling deploy) pushes the reminder 15
    minutes ahead and counts no attempt; once the kind is registered it fires as usual."""
    c = _company(migrated_dsn)
    made = _put(migrated_dsn, c, kind="demo.later")
    sent = Recorder()
    clock = FakeClock(NOW)
    unknown = ReminderRegistry()
    ran = await fire.fire_due(pool, uuid.UUID(c), clock, handlers=unknown, deliver=sent)
    assert ran == _stats(unknown=1) and sent.calls == []
    row = _row(migrated_dsn, made)
    assert row["state"] == "scheduled" and row["last_error"] == "unknown kind"
    assert row["due_at"] == NOW + timedelta(minutes=15)
    assert row["attempts"] == 0 and row["fire_count"] == 0
    # Not due again before then.
    clock.advance(timedelta(minutes=14))
    assert await fire.fire_due(pool, uuid.UUID(c), clock, handlers=unknown, deliver=sent) == (
        _stats()
    )

    async def handler(ctx: HandlerContext) -> Decision:
        return Decision(notices=(_notice(),))

    clock.advance(timedelta(minutes=1))
    known = _handlers("demo.later", handler)
    ran = await fire.fire_due(pool, uuid.UUID(c), clock, handlers=known, deliver=sent)
    assert ran == _stats(fired=1) and sent.keys == [f"{made}:1"]
    row = _row(migrated_dsn, made)
    assert row["state"] == "done" and row["fire_count"] == 1 and row["last_error"] is None


@requires_docker
async def test_a_database_error_the_handler_swallowed_is_a_failure(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    """Review focus 2: a handler that catches a database error and returns a decision leaves its
    savepoint aborted; that row counts as a failure and backs off, and a newer row still fires."""
    c = _company(migrated_dsn)
    bad = _put(migrated_dsn, c, kind="demo.swallow", due_at=NOW - timedelta(minutes=2))
    good = _put(migrated_dsn, c, due_at=NOW - timedelta(minutes=1))

    async def swallow(ctx: HandlerContext) -> Decision:
        try:  # the row's own (client_id, dedupe_key) again: a unique violation
            await ctx.conn.execute(
                "INSERT INTO reminders (client_id, kind, subject_type, subject_id, due_at,"
                " dedupe_key) SELECT client_id, kind, subject_type, subject_id, due_at, dedupe_key"
                " FROM reminders WHERE id = %s",
                (ctx.reminder.id,),
            )
        except psycopg.errors.UniqueViolation:
            pass
        return Decision(notices=(_notice(),))

    async def fine(ctx: HandlerContext) -> Decision:
        return Decision(notices=(_notice(),))

    handlers = _handlers("demo.swallow", swallow)
    handlers.register("demo.due", fine)
    sent = Recorder()
    ran = await fire.fire_due(pool, uuid.UUID(c), FakeClock(NOW), handlers=handlers, deliver=sent)
    assert ran == _stats(fired=1, failed=1)
    assert sent.keys == [f"{good}:1"] and _row(migrated_dsn, good)["state"] == "done"
    row = _row(migrated_dsn, bad)
    assert row["state"] == "scheduled" and row["attempts"] == 1 and row["fire_count"] == 0
    assert row["last_error"] == "SwallowedDatabaseError"
    assert row["due_at"] == NOW + timedelta(minutes=1)


@requires_docker
@pytest.mark.parametrize("where", ["python", "database"])
async def test_a_hanging_handler_is_a_failure_and_the_rest_fire(
    migrated_dsn: str,
    pool: AsyncConnectionPool,
    monkeypatch: pytest.MonkeyPatch,
    where: str,
) -> None:
    """Review focus 2: a hang is cut off at the per-row timeout, counted as a failure and backed
    off, instead of the job timeout ending the run on the same row every minute."""
    monkeypatch.setattr(fire, "HANDLER_TIMEOUT", 0.5)
    c = _company(migrated_dsn)
    bad = _put(migrated_dsn, c, kind="demo.hang", due_at=NOW - timedelta(minutes=2))
    good = _put(migrated_dsn, c, due_at=NOW - timedelta(minutes=1))

    async def hang(ctx: HandlerContext) -> Decision:
        if where == "python":
            await asyncio.sleep(30)
        else:
            await ctx.conn.execute("SELECT pg_sleep(30)")
        return Decision(notices=(_notice(),))

    async def fine(ctx: HandlerContext) -> Decision:
        return Decision(notices=(_notice(),))

    handlers = _handlers("demo.hang", hang)
    handlers.register("demo.due", fine)
    sent = Recorder()
    ran = await asyncio.wait_for(
        fire.fire_due(pool, uuid.UUID(c), FakeClock(NOW), handlers=handlers, deliver=sent), 15
    )
    assert ran == _stats(fired=1, failed=1)
    assert sent.keys == [f"{good}:1"] and _row(migrated_dsn, good)["state"] == "done"
    row = _row(migrated_dsn, bad)
    assert row["state"] == "scheduled" and row["attempts"] == 1 and row["fire_count"] == 0
    assert row["last_error"] == "TimeoutError"
    assert row["due_at"] == NOW + timedelta(minutes=1)


@requires_docker
async def test_a_failing_handler_backs_off_does_not_stop_the_rest_and_stops_at_three(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    """Review focus 2: one bad reminder leaves the others fired, backs off 1 then 4 minutes, and
    its third failure marks it fired with the error; it never loops.
    """
    c = _company(migrated_dsn)
    bad = _put(migrated_dsn, c, kind="demo.bad", due_at=NOW - timedelta(minutes=2))
    good = _put(migrated_dsn, c, due_at=NOW - timedelta(minutes=1))
    calls: list[uuid.UUID] = []

    async def boom(ctx: HandlerContext) -> Decision:
        calls.append(ctx.reminder.id)
        raise RuntimeError("synthetic secret text from Siti Aminah")

    async def fine(ctx: HandlerContext) -> Decision:
        return Decision(notices=(_notice(),))

    handlers = _handlers("demo.bad", boom)
    handlers.register("demo.due", fine)
    sent = Recorder()
    clock = FakeClock(NOW)
    with capture_logs() as logs:
        ran = await fire.fire_due(pool, uuid.UUID(c), clock, handlers=handlers, deliver=sent)
    assert ran == _stats(fired=1, failed=1)
    assert sent.keys == [f"{good}:1"] and _row(migrated_dsn, good)["state"] == "done"
    row = _row(migrated_dsn, bad)
    assert row["state"] == "scheduled" and row["attempts"] == 1
    assert row["last_error"] == "RuntimeError" and row["fire_count"] == 0
    assert row["due_at"] == NOW + timedelta(minutes=1)
    [failed] = [log for log in logs if log["event"] == "reminder_failed"]
    assert failed["client_id"] == c and failed["reminder_id"] == str(bad)
    assert failed["error_class"] == "RuntimeError" and failed["attempts"] == 1
    assert "Siti" not in json.dumps(logs, default=str)
    assert "secret" not in json.dumps(logs, default=str)

    # Not yet due again: no call.
    assert await fire.fire_due(pool, uuid.UUID(c), clock, handlers=handlers, deliver=sent) == (
        _stats()
    )
    clock.advance(timedelta(minutes=1))
    ran = await fire.fire_due(pool, uuid.UUID(c), clock, handlers=handlers, deliver=sent)
    assert ran == _stats(failed=1)
    row = _row(migrated_dsn, bad)
    assert row["state"] == "scheduled" and row["attempts"] == 2
    assert row["due_at"] == NOW + timedelta(minutes=1 + 4)
    clock.advance(timedelta(minutes=4))
    ran = await fire.fire_due(pool, uuid.UUID(c), clock, handlers=handlers, deliver=sent)
    assert ran == _stats(dead=1)
    row = _row(migrated_dsn, bad)
    assert row["state"] == "fired" and row["attempts"] == 3
    assert row["last_error"] == "RuntimeError" and row["fire_count"] == 0
    # Stopped: a further run, even much later, does nothing.
    clock.advance(timedelta(days=1))
    ran = await fire.fire_due(pool, uuid.UUID(c), clock, handlers=handlers, deliver=sent)
    assert ran == _stats()
    assert calls == [bad, bad, bad] and sent.keys == [f"{good}:1"]


@requires_docker
async def test_a_failure_undoes_what_the_handler_wrote(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    """The handler runs in a savepoint: a failed delivery rolls back the handler's own writes."""
    c = _company(migrated_dsn)
    made = _put(migrated_dsn, c)

    async def handler(ctx: HandlerContext) -> Decision:
        await reminders.schedule(
            ctx.conn, ctx.client_id, "demo.side", "thing", "s-2", ctx.now, {}, "side-effect"
        )
        return Decision(notices=(_notice(),))

    async def broken(*args: Any) -> None:
        raise RuntimeError("synthetic delivery failure")

    ran = await fire.fire_due(
        pool,
        uuid.UUID(c),
        FakeClock(NOW),
        handlers=_handlers("demo.due", handler),
        deliver=broken,
    )
    assert ran == _stats(failed=1)
    assert _row(migrated_dsn, made)["attempts"] == 1
    with _owner(migrated_dsn) as conn:
        rows = conn.execute(
            "SELECT id FROM reminders WHERE client_id = %s AND dedupe_key = 'side-effect'", (c,)
        ).fetchall()
    assert rows == []


@requires_docker
async def test_two_runs_at_once_for_one_company_deliver_once(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    """Review focus 1: SKIP LOCKED gives each due row to one run only."""
    c = _company(migrated_dsn)
    first = _put(migrated_dsn, c, due_at=NOW - timedelta(minutes=2))
    second = _put(migrated_dsn, c, due_at=NOW - timedelta(minutes=1))

    async def slow(ctx: HandlerContext) -> Decision:
        await asyncio.sleep(0.3)  # both runs are inside a claim at the same time
        return Decision(notices=(_notice(),))

    handlers = _handlers("demo.due", slow)
    sent = Recorder()
    a, b = await asyncio.gather(
        fire.fire_due(pool, uuid.UUID(c), FakeClock(NOW), handlers=handlers, deliver=sent),
        fire.fire_due(pool, uuid.UUID(c), FakeClock(NOW), handlers=handlers, deliver=sent),
    )
    assert a["fired"] + b["fired"] == 2
    assert sorted(sent.keys) == sorted([f"{first}:1", f"{second}:1"])
    assert {_row(migrated_dsn, r)["fire_count"] for r in (first, second)} == {1}


@requires_docker
async def test_a_replay_after_a_crash_before_commit_delivers_with_the_same_key(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    """Review focus 1: the row is claimed again and the delivery key is the same, so the
    notification tables (unique on it) keep one.
    """
    c = _company(migrated_dsn)
    made = _put(migrated_dsn, c)

    async def handler(ctx: HandlerContext) -> Decision:
        return Decision(notices=(_notice(),))

    handlers = _handlers("demo.due", handler)
    sent = Recorder(crash_once=True)
    with pytest.raises(Crash):
        await fire.fire_due(pool, uuid.UUID(c), FakeClock(NOW), handlers=handlers, deliver=sent)
    row = _row(migrated_dsn, made)
    assert row["state"] == "scheduled" and row["fire_count"] == 0 and row["attempts"] == 0
    ran = await fire.fire_due(pool, uuid.UUID(c), FakeClock(NOW), handlers=handlers, deliver=sent)
    assert ran == _stats(fired=1)
    assert sent.keys == [f"{made}:1", f"{made}:1"]
    assert _row(migrated_dsn, made)["state"] == "done"


@requires_docker
async def test_one_run_takes_at_most_batch_size_rows_oldest_first(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    c = _company(migrated_dsn)
    made = [_put(migrated_dsn, c, due_at=NOW - timedelta(minutes=m)) for m in (3, 2, 1)]

    async def handler(ctx: HandlerContext) -> Decision:
        return Decision(notices=(_notice(),))

    handlers = _handlers("demo.due", handler)
    sent = Recorder()
    clock = FakeClock(NOW)
    ran = await fire.fire_due(
        pool, uuid.UUID(c), clock, handlers=handlers, deliver=sent, batch_size=2
    )
    assert ran == _stats(fired=2)
    assert sent.keys == [f"{made[0]}:1", f"{made[1]}:1"]
    assert _row(migrated_dsn, made[2])["state"] == "scheduled"
    ran = await fire.fire_due(
        pool, uuid.UUID(c), clock, handlers=handlers, deliver=sent, batch_size=2
    )
    assert ran == _stats(fired=1) and sent.keys[-1] == f"{made[2]}:1"


@requires_docker
async def test_another_companys_due_row_is_never_claimed(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    c, other = _company(migrated_dsn), _company(migrated_dsn)
    mine = _put(migrated_dsn, c)
    theirs = _put(migrated_dsn, other, due_at=NOW - timedelta(hours=1))
    seen: list[uuid.UUID] = []

    async def handler(ctx: HandlerContext) -> Decision:
        seen.append(ctx.reminder.id)
        return Decision(notices=(_notice(),))

    sent = Recorder()
    ran = await fire.fire_due(
        pool, uuid.UUID(c), FakeClock(NOW), handlers=_handlers("demo.due", handler), deliver=sent
    )
    assert ran == _stats(fired=1) and seen == [mine]
    assert {client for client, _, _ in sent.calls} == {uuid.UUID(c)}
    row = _row(migrated_dsn, theirs)
    assert row["state"] == "scheduled" and row["fire_count"] == 0 and row["attempts"] == 0


@requires_docker
async def test_the_job_body_fires_through_the_process_registry(
    migrated_dsn: str, pool: AsyncConnectionPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(kinds, "registry", ReminderRegistry())
    c = _company(migrated_dsn)
    made = _put(migrated_dsn, c)

    async def handler(ctx: HandlerContext) -> Decision:
        return Decision()

    kinds.register_kind("demo.due", handler)
    ran = await run_reminders_fire(pool, uuid.UUID(c), FakeClock(NOW), batch_size=10)
    assert ran == _stats(dropped=1)
    assert _row(migrated_dsn, made)["state"] == "done"


def test_the_worker_registers_the_inbox_kinds_before_the_jobs_loop_starts() -> None:
    from apps.worker import main as worker

    source = inspect.getsource(worker.main)
    call = source.index("\n    register_inbox_reminder_kinds()\n")
    assert call < source.index("jobs_task = asyncio.create_task(jobs_loop(job_runner))")
