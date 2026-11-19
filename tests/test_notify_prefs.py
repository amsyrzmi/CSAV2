"""csa.notify.prefs: a person's email preferences, read and saved on an app-role company
transaction (disposable database, synthetic data).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from csa.core import db
from csa.core.tenancy import tx
from csa.notify.prefs import KINDS, PRIORITY_RANK, Prefs, get_prefs, save_prefs
from psycopg_pool import AsyncConnectionPool

from tests.conftest import requires_docker
from tests.test_bookings_schema import _owner
from tests.test_console_notifications_schema import member
from tests.test_training_schema import _company


@pytest.fixture
async def pool(app_dsn: str) -> AsyncIterator[AsyncConnectionPool]:
    async with db.build_pool(app_dsn, max_size=2) as made:
        yield made


def _rows(conn: Any, client_id: str) -> list[dict[str, Any]]:
    """The company's saved rows (the database is shared across tests)."""
    return conn.execute(
        "SELECT 1 FROM console_notification_prefs WHERE client_id = %s", (client_id,)
    ).fetchall()


def test_the_six_kinds_and_the_priority_ranks() -> None:
    assert KINDS == (
        "inbox.assigned",
        "inbox.overdue",
        "inbox.snooze_due",
        "inbox.stale_claim",
        "inbox.follow_up",
        "inbox.handback",
    )
    assert PRIORITY_RANK == {"low": 0, "normal": 1, "high": 2, "urgent": 3}


@requires_docker
async def test_defaults_when_there_is_no_row(migrated_dsn: str, pool: AsyncConnectionPool) -> None:
    c = _company(migrated_dsn)
    person = member(migrated_dsn, c)
    async with tx(pool, c) as conn:
        got = await get_prefs(conn, uuid.UUID(c), uuid.UUID(person))
    assert got == Prefs(email_enabled=False, kinds=KINDS, min_priority="high")
    with _owner(migrated_dsn) as conn:  # reading wrote nothing
        assert _rows(conn, c) == []


@requires_docker
async def test_save_then_get_round_trips(migrated_dsn: str, pool: AsyncConnectionPool) -> None:
    c = _company(migrated_dsn)
    person = member(migrated_dsn, c, "read_only")  # a read-only member keeps their own settings
    mine = Prefs(
        email_enabled=True, kinds=("inbox.handback", "inbox.assigned"), min_priority="normal"
    )
    async with tx(pool, c) as conn:
        saved = await save_prefs(conn, uuid.UUID(c), uuid.UUID(person), mine)
    assert saved == mine
    async with tx(pool, c) as conn:
        assert await get_prefs(conn, uuid.UUID(c), uuid.UUID(person)) == mine
        # A second save replaces the row; the last write wins.
        later = Prefs(email_enabled=False, kinds=(), min_priority="low")
        assert await save_prefs(conn, uuid.UUID(c), uuid.UUID(person), later) == later
    async with tx(pool, c) as conn:
        assert await get_prefs(conn, uuid.UUID(c), uuid.UUID(person)) == later
    with _owner(migrated_dsn) as conn:
        assert len(_rows(conn, c)) == 1


@requires_docker
async def test_save_rejects_an_unknown_kind_or_priority_before_touching_the_database(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    c = _company(migrated_dsn)
    person = member(migrated_dsn, c)
    async with tx(pool, c) as conn:
        with pytest.raises(ValueError, match="inbox.bogus"):
            await save_prefs(
                conn,
                uuid.UUID(c),
                uuid.UUID(person),
                Prefs(email_enabled=True, kinds=("inbox.bogus",), min_priority="high"),
            )
        with pytest.raises(ValueError, match="critical"):
            await save_prefs(
                conn,
                uuid.UUID(c),
                uuid.UUID(person),
                Prefs(email_enabled=True, kinds=KINDS, min_priority="critical"),
            )
    with _owner(migrated_dsn) as conn:
        assert _rows(conn, c) == []


@requires_docker
async def test_saving_clears_the_failure_flag(migrated_dsn: str, pool: AsyncConnectionPool) -> None:
    c = _company(migrated_dsn)
    person = member(migrated_dsn, c)
    first = Prefs(email_enabled=True, kinds=KINDS, min_priority="high")
    async with tx(pool, c) as conn:
        await save_prefs(conn, uuid.UUID(c), uuid.UUID(person), first)
    with _owner(migrated_dsn) as conn:
        conn.execute(
            "UPDATE console_notification_prefs SET failure_notified_at = now(),"
            " updated_at = now() - interval '1 day' WHERE user_id = %s",
            (person,),
        )
    async with tx(pool, c) as conn:
        await save_prefs(conn, uuid.UUID(c), uuid.UUID(person), first)
    with _owner(migrated_dsn) as conn:
        [row] = conn.execute(
            "SELECT failure_notified_at, updated_at > now() - interval '1 hour' AS fresh"
            " FROM console_notification_prefs WHERE user_id = %s",
            (person,),
        ).fetchall()
    assert row["failure_notified_at"] is None and row["fresh"] is True
