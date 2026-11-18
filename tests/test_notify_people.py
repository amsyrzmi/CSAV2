"""csa.notify.people: console notifications per person and the staff chat, and the reminders fire
job's default delivery through it (disposable database, synthetic data).

``notify_people`` runs on an app-role company transaction, as the fire job and the Inbox call it;
rows are read back through the owner.
"""

from __future__ import annotations

import ast
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from csa.core import db
from csa.core.tenancy import tx
from csa.jobs.clock import FakeClock
from csa.notify.people import NotifyResult, admin_user_ids, notify_people, team_user_ids
from csa.reminders import fire
from csa.reminders.registry import Decision, HandlerContext, Notice, ReminderRegistry
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from tests.conftest import requires_docker
from tests.test_bookings_schema import _owner
from tests.test_console_notifications_schema import member
from tests.test_training_schema import _company

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
LINK = {"route": "inbox", "conversation": "c-1"}


@pytest.fixture
async def pool(app_dsn: str) -> AsyncIterator[AsyncConnectionPool]:
    async with db.build_pool(app_dsn, max_size=2) as made:
        yield made


def _target(dsn: str, client_id: str, events: str) -> str:
    target_id = str(uuid.uuid4())
    with _owner(dsn) as conn:
        conn.execute(
            "INSERT INTO staff_notification_targets (id, client_id, channel, address, events)"
            " VALUES (%s, %s, 'telegram', %s, %s::text[])",
            (target_id, client_id, f"chat-{target_id}", events),
        )
    return target_id


def _console_rows(dsn: str, client_id: str) -> list[dict[str, Any]]:
    with _owner(dsn) as conn:
        return conn.execute(
            "SELECT user_id, kind, priority, message_key, params, link, dedupe_key, read_at"
            " FROM console_notifications WHERE client_id = %s ORDER BY dedupe_key, user_id",
            (client_id,),
        ).fetchall()


def _chat_rows(dsn: str, client_id: str) -> list[dict[str, Any]]:
    with _owner(dsn) as conn:
        return conn.execute(
            "SELECT target_id, event, priority, payload, idempotency_key"
            " FROM staff_notifications WHERE client_id = %s ORDER BY idempotency_key",
            (client_id,),
        ).fetchall()


async def _notify(
    pool: AsyncConnectionPool, client_id: str, recipients: list[str], **extra: Any
) -> NotifyResult:
    args: dict[str, Any] = {
        "kind": "inbox.overdue",
        "priority": "high",
        "message_key": "inbox.overdue",
        "params": {"customer": "Synthetic Customer", "waited_minutes": 240},
        "link": LINK,
        "dedupe_key": "r-1:1",
        **extra,
    }
    async with tx(pool, client_id) as conn:
        return await notify_people(
            conn, uuid.UUID(client_id), recipients=[uuid.UUID(r) for r in recipients], **args
        )


def _csa_imports(tree: ast.Module) -> set[str]:
    """Every csa module a file imports; ``from csa.x import y`` counts as ``csa.x.y``."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names |= {f"{node.module}.{alias.name}" for alias in node.names}
    return {n for n in names if n.split(".")[0] == "csa"}


def test_people_imports_only_the_notifier_and_core() -> None:
    """The light-module rule: no console, Inbox, approval or sender import (plan constraint)."""
    for name in ("people.py", "format.py"):
        tree = ast.parse((ROOT / "csa" / "notify" / name).read_text(encoding="utf-8"))
        inside = _csa_imports(tree)
        allowed = {n for n in inside if n == "csa.notify.notifier" or n.startswith("csa.core.")}
        assert inside == allowed, (name, inside - allowed)


def test_the_contract_keeps_notify_people_light() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = text.split("csa.notify.people and csa.notify.format stay light", 1)[1]
    block = block.split("[[tool.importlinter", 1)[0]
    assert '"csa.notify.people"' in block and '"csa.notify.format"' in block
    for forbidden in ("csa.notify.sender", "csa.inbox_queue", "csa.console", "csa.reminders"):
        assert f'"{forbidden}"' in block


@requires_docker
async def test_three_recipients_make_three_rows_and_a_replay_none(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    c = _company(migrated_dsn)
    three = [
        member(migrated_dsn, c, role) for role in ("tenant_admin", "tenant_staff", "read_only")
    ]
    first = await _notify(pool, c, three)
    assert first == NotifyResult(console_rows=3, chat_rows=0)
    rows = _console_rows(migrated_dsn, c)
    assert sorted(str(r["user_id"]) for r in rows) == sorted(three)
    for row in rows:
        assert row["kind"] == "inbox.overdue" and row["message_key"] == "inbox.overdue"
        assert row["priority"] == "high" and row["dedupe_key"] == "r-1:1"
        assert row["params"] == {"customer": "Synthetic Customer", "waited_minutes": 240}
        assert row["link"] == LINK and row["read_at"] is None
    # Scenario 20: the same key again writes nothing.
    assert await _notify(pool, c, three) == NotifyResult(console_rows=0, chat_rows=0)
    assert len(_console_rows(migrated_dsn, c)) == 3
    # A new key is a new notification.
    assert (await _notify(pool, c, three, dedupe_key="r-1:2")).console_rows == 3


@requires_docker
async def test_a_recipient_who_is_not_a_member_is_skipped_without_error(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    c, other = _company(migrated_dsn), _company(migrated_dsn)
    kept = member(migrated_dsn, c)
    skipped = [
        str(uuid.uuid4()),  # no account at all
        member(migrated_dsn, other),  # another company's member
        member(migrated_dsn, c, member_enabled=False),  # left the company
        member(migrated_dsn, c, account_enabled=False),  # account switched off
        member(migrated_dsn, c, "tenant_admin", platform_admin=True),  # platform admin
    ]
    result = await _notify(pool, c, [skipped[0], kept, *skipped[1:], kept])
    assert result == NotifyResult(console_rows=1, chat_rows=0)
    assert [str(r["user_id"]) for r in _console_rows(migrated_dsn, c)] == [kept]


@requires_docker
async def test_no_recipients_writes_no_console_row(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    c = _company(migrated_dsn)
    assert await _notify(pool, c, []) == NotifyResult(console_rows=0, chat_rows=0)
    assert _console_rows(migrated_dsn, c) == []


@requires_docker
async def test_a_staff_event_writes_one_chat_row_per_matching_target_once(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    c = _company(migrated_dsn)
    person = member(migrated_dsn, c)
    matching = _target(migrated_dsn, c, "{escalation,inbox.overdue}")
    _target(migrated_dsn, c, "{escalation}")  # not subscribed to the event
    payload: dict[str, object] = {"customer": "Synthetic Customer", "waited_minutes": 240}
    result = await _notify(pool, c, [person], staff_event="inbox.overdue", staff_payload=payload)
    assert result == NotifyResult(console_rows=1, chat_rows=1)
    [chat] = _chat_rows(migrated_dsn, c)
    assert str(chat["target_id"]) == matching
    assert (chat["event"], chat["priority"], chat["payload"]) == ("inbox.overdue", "high", payload)
    assert chat["idempotency_key"] == f"notify:inbox.overdue:r-1:1:{matching}"
    # Scenario 20: a replay writes neither a console nor a chat row.
    again = await _notify(pool, c, [person], staff_event="inbox.overdue", staff_payload=payload)
    assert again == NotifyResult(console_rows=0, chat_rows=0)
    assert len(_chat_rows(migrated_dsn, c)) == 1 and len(_console_rows(migrated_dsn, c)) == 1


@requires_docker
async def test_no_staff_event_writes_no_chat_row(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    c = _company(migrated_dsn)
    person = member(migrated_dsn, c)
    _target(migrated_dsn, c, "{escalation,inbox.overdue}")
    assert await _notify(pool, c, [person]) == NotifyResult(console_rows=1, chat_rows=0)
    assert _chat_rows(migrated_dsn, c) == []


@requires_docker
async def test_an_unknown_priority_raises_and_writes_nothing(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    c = _company(migrated_dsn)
    person = member(migrated_dsn, c)
    with pytest.raises(ValueError, match="critical"):
        await _notify(pool, c, [person], priority="critical")
    assert _console_rows(migrated_dsn, c) == []


@requires_docker
async def test_team_and_admin_ids_come_from_the_recipient_functions(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    c = _company(migrated_dsn)
    admin = member(migrated_dsn, c, "tenant_admin")
    staff = member(migrated_dsn, c, "tenant_staff")
    member(migrated_dsn, c, "read_only")
    member(migrated_dsn, c, "tenant_staff", member_enabled=False)
    async with tx(pool, c) as conn:
        team = await team_user_ids(conn, uuid.UUID(c))
        admins = await admin_user_ids(conn, uuid.UUID(c))
    assert sorted(team) == sorted([uuid.UUID(admin), uuid.UUID(staff)])
    assert all(isinstance(u, uuid.UUID) for u in team)
    assert admins == [uuid.UUID(admin)]


def _reminder(dsn: str, client_id: str) -> uuid.UUID:
    with _owner(dsn) as conn:
        [row] = conn.execute(
            "INSERT INTO reminders (client_id, kind, subject_type, subject_id, due_at, payload,"
            " dedupe_key) VALUES (%s, 'demo.due', 'thing', 's-1', %s, %s, %s) RETURNING id",
            (client_id, NOW - timedelta(minutes=1), Jsonb({}), str(uuid.uuid4())),
        )
    return row["id"]


@requires_docker
async def test_the_fire_job_delivers_through_notify_people_by_default(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    """Task 2's seam: with no ``deliver`` the fire job writes console rows and the staff chat."""
    c = _company(migrated_dsn)
    one, two = member(migrated_dsn, c), member(migrated_dsn, c)
    target = _target(migrated_dsn, c, "{inbox.overdue,demo.chat}")
    made = _reminder(migrated_dsn, c)
    params = {"customer": "Synthetic Customer", "waited_minutes": 60}

    def notice(**extra: Any) -> Notice:
        base: dict[str, Any] = {
            "recipients": (uuid.UUID(one), uuid.UUID(two)),
            "kind": "demo.due",
            "priority": "normal",
            "message_key": "demo.due",
            "params": params,
            "link": LINK,
        }
        return Notice(**{**base, **extra})

    async def handler(ctx: HandlerContext) -> Decision:
        return Decision(
            notices=(
                notice(staff_chat=True, staff_event="inbox.overdue"),
                notice(recipients=(uuid.UUID(one),), message_key="demo.second"),
                notice(recipients=(uuid.UUID(two),), staff_chat=True, kind="demo.chat"),
            )
        )

    handlers = ReminderRegistry()
    handlers.register("demo.due", handler)
    ran = await fire.fire_due(pool, uuid.UUID(c), FakeClock(NOW), handlers=handlers)
    assert ran["fired"] == 1 and ran["failed"] == 0
    keys = [(str(r["user_id"]), r["dedupe_key"]) for r in _console_rows(migrated_dsn, c)]
    assert sorted(keys) == sorted(
        [
            (one, f"{made}:1"),
            (two, f"{made}:1"),
            (one, f"{made}:1:1"),
            (two, f"{made}:1:2"),
        ]
    )
    chats = _chat_rows(migrated_dsn, c)
    # The first notice names its event; the third has none and goes out as its kind.
    assert sorted((r["event"], r["idempotency_key"]) for r in chats) == sorted(
        [
            ("inbox.overdue", f"notify:inbox.overdue:{made}:1:{target}"),
            ("demo.chat", f"notify:demo.chat:{made}:1:2:{target}"),
        ]
    )
    assert all(r["payload"] == params for r in chats)


@requires_docker
async def test_the_default_delivery_replays_without_a_second_row(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    c = _company(migrated_dsn)
    person = member(migrated_dsn, c)
    _target(migrated_dsn, c, "{inbox.overdue}")
    sent = Notice(
        recipients=(uuid.UUID(person),),
        kind="inbox.overdue",
        priority="high",
        message_key="inbox.overdue",
        params={"customer": "Synthetic Customer"},
        link=LINK,
        staff_chat=True,
        staff_event="inbox.overdue",
    )
    for _ in range(2):
        async with tx(pool, c) as conn:
            await fire.deliver_notice(conn, uuid.UUID(c), sent, "r-9:1")
    assert len(_console_rows(migrated_dsn, c)) == 1 and len(_chat_rows(migrated_dsn, c)) == 1
