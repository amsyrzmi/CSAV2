"""csa.reminders: schedule and cancel (disposable database, synthetic data).

Every call runs through the app role on a tenant transaction, as the worker and the console call it,
so row security and grants are part of every check. Reading back goes through the owner.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from csa import reminders

from tests.conftest import requires_docker
from tests.test_bookings_schema import _owner
from tests.test_training_schema import _company

NOW = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parent.parent


async def _act[T](
    app_dsn: str, client_id: str, fn: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any
) -> T:
    """One call on a tenant transaction of the app role, committed when it returns."""
    async with await psycopg.AsyncConnection.connect(app_dsn) as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.client_id', %s, true)", (client_id,))
            return await fn(conn, uuid.UUID(client_id), *args, **kwargs)


async def _schedule(
    app_dsn: str,
    client_id: str,
    *,
    kind: str = "synthetic.kind",
    subject_id: str = "subject-1",
    due_at: datetime = NOW,
    payload: dict[str, Any] | None = None,
    dedupe_key: str = "key-1",
    subject_type: str = "thing",
) -> uuid.UUID:
    return await _act(
        app_dsn,
        client_id,
        reminders.schedule,
        kind,
        subject_type,
        subject_id,
        due_at,
        payload or {},
        dedupe_key,
    )


def _row(dsn: str, reminder_id: uuid.UUID) -> dict[str, Any]:
    with _owner(dsn) as conn:
        [row] = conn.execute("SELECT * FROM reminders WHERE id = %s", (reminder_id,))
    return row


def _count(dsn: str, client_id: str) -> int:
    with _owner(dsn) as conn:
        [row] = conn.execute(
            "SELECT count(*) AS n FROM reminders WHERE client_id = %s", (client_id,)
        )
    return row["n"]


@requires_docker
async def test_schedule_makes_a_scheduled_row(migrated_dsn: str, app_dsn: str) -> None:
    c = _company(migrated_dsn)
    made = await _schedule(app_dsn, c, payload={"a": 1})
    row = _row(migrated_dsn, made)
    assert row["state"] == "scheduled" and row["fire_count"] == 0
    assert row["due_at"] == NOW and row["payload"] == {"a": 1}
    assert (row["kind"], row["subject_type"], row["subject_id"]) == (
        "synthetic.kind",
        "thing",
        "subject-1",
    )
    assert row["dedupe_key"] == "key-1" and row["client_id"] == uuid.UUID(c)


@requires_docker
async def test_scheduling_twice_with_one_key_leaves_one_row_and_moves_the_time(
    migrated_dsn: str, app_dsn: str
) -> None:
    c = _company(migrated_dsn)
    first = await _schedule(app_dsn, c, payload={"p": "old"})
    later = NOW + timedelta(hours=3)
    second = await _schedule(app_dsn, c, due_at=later, payload={"p": "new"})
    assert second == first
    assert _count(migrated_dsn, c) == 1
    row = _row(migrated_dsn, first)
    assert row["due_at"] == later and row["payload"] == {"p": "new"}


@requires_docker
async def test_a_second_call_leaves_a_fired_row_untouched(migrated_dsn: str, app_dsn: str) -> None:
    c = _company(migrated_dsn)
    first = await _schedule(app_dsn, c, payload={"p": "old"})
    with _owner(migrated_dsn) as conn:
        conn.execute("UPDATE reminders SET state = 'fired', fire_count = 1 WHERE id = %s", (first,))
    second = await _schedule(app_dsn, c, due_at=NOW + timedelta(hours=3), payload={"p": "new"})
    assert second == first
    row = _row(migrated_dsn, first)
    assert row["state"] == "fired" and row["due_at"] == NOW and row["payload"] == {"p": "old"}
    assert _count(migrated_dsn, c) == 1


@requires_docker
async def test_a_second_call_leaves_a_repeating_row_untouched(
    migrated_dsn: str, app_dsn: str
) -> None:
    """Still scheduled but already fired once (a repeat is waiting): the time is not pulled back."""
    c = _company(migrated_dsn)
    first = await _schedule(app_dsn, c)
    repeat_at = NOW + timedelta(hours=1)
    with _owner(migrated_dsn) as conn:
        conn.execute(
            "UPDATE reminders SET fire_count = 1, due_at = %s WHERE id = %s", (repeat_at, first)
        )
    await _schedule(app_dsn, c, due_at=NOW + timedelta(hours=9))
    assert _row(migrated_dsn, first)["due_at"] == repeat_at


@requires_docker
async def test_a_cancelled_row_stays_cancelled_when_scheduled_again(
    migrated_dsn: str, app_dsn: str
) -> None:
    c = _company(migrated_dsn)
    first = await _schedule(app_dsn, c)
    assert await _act(app_dsn, c, reminders.cancel, "synthetic.kind", "thing", "subject-1") == 1
    again = await _schedule(app_dsn, c, due_at=NOW + timedelta(hours=2))
    assert again == first
    assert _row(migrated_dsn, first)["state"] == "cancelled"


@requires_docker
async def test_cancel_cancels_only_that_kind_and_subject(migrated_dsn: str, app_dsn: str) -> None:
    c = _company(migrated_dsn)
    target = await _schedule(app_dsn, c, kind="k.a", subject_id="s1", dedupe_key="1")
    other_kind = await _schedule(app_dsn, c, kind="k.b", subject_id="s1", dedupe_key="2")
    other_subject = await _schedule(app_dsn, c, kind="k.a", subject_id="s2", dedupe_key="3")
    other_type = await _schedule(
        app_dsn, c, kind="k.a", subject_id="s1", subject_type="other", dedupe_key="4"
    )
    second_cycle = await _schedule(app_dsn, c, kind="k.a", subject_id="s1", dedupe_key="5")
    fired = await _schedule(app_dsn, c, kind="k.a", subject_id="s1", dedupe_key="6")
    with _owner(migrated_dsn) as conn:
        conn.execute("UPDATE reminders SET state = 'fired' WHERE id = %s", (fired,))
    assert await _act(app_dsn, c, reminders.cancel, "k.a", "thing", "s1") == 2
    states = {
        name: _row(migrated_dsn, rid)["state"]
        for name, rid in {
            "target": target,
            "second_cycle": second_cycle,
            "other_kind": other_kind,
            "other_subject": other_subject,
            "other_type": other_type,
            "fired": fired,
        }.items()
    }
    assert states == {
        "target": "cancelled",
        "second_cycle": "cancelled",
        "other_kind": "scheduled",
        "other_subject": "scheduled",
        "other_type": "scheduled",
        "fired": "fired",
    }
    # Nothing left to cancel.
    assert await _act(app_dsn, c, reminders.cancel, "k.a", "thing", "s1") == 0


@requires_docker
async def test_another_companys_reminder_is_never_touched(migrated_dsn: str, app_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    theirs = await _schedule(app_dsn, a)
    # Same key and subject in company b: its own row, not company a's.
    mine = await _schedule(app_dsn, b, due_at=NOW + timedelta(hours=1))
    assert mine != theirs
    assert await _act(app_dsn, b, reminders.cancel, "synthetic.kind", "thing", "subject-1") == 1
    assert _row(migrated_dsn, theirs)["state"] == "scheduled"
    assert _row(migrated_dsn, theirs)["due_at"] == NOW
    assert _row(migrated_dsn, mine)["state"] == "cancelled"


@requires_docker
async def test_cancel_skip_locked_cancels_the_kinds_but_the_kept_keys(
    migrated_dsn: str, app_dsn: str
) -> None:
    c = _company(migrated_dsn)
    a = await _schedule(app_dsn, c, kind="k.a", subject_id="s1", dedupe_key="1")
    b = await _schedule(app_dsn, c, kind="k.b", subject_id="s1", dedupe_key="2")
    kept = await _schedule(app_dsn, c, kind="k.b", subject_id="s1", dedupe_key="3")
    other_kind = await _schedule(app_dsn, c, kind="k.c", subject_id="s1", dedupe_key="4")
    other_subject = await _schedule(app_dsn, c, kind="k.a", subject_id="s2", dedupe_key="5")
    done = await _schedule(app_dsn, c, kind="k.a", subject_id="s1", dedupe_key="6")
    with _owner(migrated_dsn) as conn:
        conn.execute("UPDATE reminders SET state = 'done' WHERE id = %s", (done,))
    cancelled = await _act(
        app_dsn, c, reminders.cancel_skip_locked, ["k.a", "k.b"], "thing", "s1", keep=["3"]
    )
    assert cancelled == 2
    states = {
        name: _row(migrated_dsn, rid)["state"]
        for name, rid in {
            "a": a,
            "b": b,
            "kept": kept,
            "other_kind": other_kind,
            "other_subject": other_subject,
            "done": done,
        }.items()
    }
    assert states == {
        "a": "cancelled",
        "b": "cancelled",
        "kept": "scheduled",
        "other_kind": "scheduled",
        "other_subject": "scheduled",
        "done": "done",
    }
    assert await _act(app_dsn, c, reminders.cancel_skip_locked, ["k.a"], "thing", "s1") == 0


@requires_docker
async def test_cancel_skip_locked_skips_a_row_another_transaction_holds(
    migrated_dsn: str, app_dsn: str
) -> None:
    c = _company(migrated_dsn)
    held = await _schedule(app_dsn, c, kind="k.a", subject_id="s1", dedupe_key="1")
    free = await _schedule(app_dsn, c, kind="k.a", subject_id="s1", dedupe_key="2")
    async with await psycopg.AsyncConnection.connect(app_dsn) as holder:
        async with holder.transaction():
            await holder.execute("SELECT set_config('app.client_id', %s, true)", (c,))
            await holder.execute("SELECT id FROM reminders WHERE id = %s FOR UPDATE", (held,))
            cancelled = await asyncio.wait_for(
                _act(app_dsn, c, reminders.cancel_skip_locked, ["k.a"], "thing", "s1"),
                timeout=10,
            )
            assert cancelled == 1
    assert _row(migrated_dsn, held)["state"] == "scheduled"
    assert _row(migrated_dsn, free)["state"] == "cancelled"


def test_the_package_exports_schedule_and_cancel() -> None:
    assert callable(reminders.cancel_skip_locked)
    assert callable(reminders.schedule) and callable(reminders.cancel)


@pytest.mark.parametrize(
    "forbidden",
    [
        "csa.inbox_queue",
        "csa.console",
        "csa.approval",
        "csa.agents",
        "csa.workspace",
        "csa.staff",
        "csa.notify.sender",
    ],
)
def test_the_contract_names_every_forbidden_module(forbidden: str) -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = text.split("csa.reminders stays generic", 1)[1].split("[[tool.importlinter", 1)[0]
    assert f'"{forbidden}"' in block
