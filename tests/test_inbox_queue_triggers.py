"""Migration 0622: inbox_queue_enter and the message and escalation triggers that keep the queue
state of a conversation (disposable database, synthetic data).

Messages and escalations are written with the repo's own SQL (worker/insert_message.sql,
inbox/insert_reply.sql, turn/insert_escalation.sql) through the app role, as the worker and the
console do, so row security and grants are part of every check.
"""

from __future__ import annotations

import typing
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, LiteralString

import pytest
from apps.gateway.main import EXPECTED_SCHEMA_VERSION
from csa.core import db
from psycopg import errors
from psycopg.types.json import Jsonb
from scripts import migrate

from tests.conftest import requires_docker
from tests.test_bookings_schema import DictConnection, _app, _owner
from tests.test_training_schema import _company

pytestmark = requires_docker

QUEUE_COLUMNS = (
    "queue_status",
    "queue_reason",
    "priority",
    "assignee_user_id",
    "claimed_at",
    "needs_staff_since",
    "due_at",
    "snoozed_until",
    "resolved_at",
    "last_staff_reply_at",
    "inbox_version",
)
DEFAULT_TARGETS = {"urgent": 15, "high": 60, "normal": 240, "low": 1440}


@dataclass
class Seeded:
    client_id: str
    conversation_id: str
    contact_id: str


def _q(text: str) -> LiteralString:
    """A query built from fixed names in this file, for the type checker."""
    return typing.cast("LiteralString", text)


def _user(dsn: str) -> str:
    user_id = str(uuid.uuid4())
    with _owner(dsn) as conn:
        conn.execute(
            "INSERT INTO console_users (id, email, display_name) VALUES (%s, %s, 'Synthetic')",
            (user_id, f"{user_id}@example.test"),
        )
    return user_id


def _seed(
    dsn: str,
    client_id: str,
    *,
    channel: str = "telegram",
    owned_by: str | None = None,
    **queue: Any,
) -> Seeded:
    """One conversation; `queue` sets queue columns afterwards, as the owner."""
    contact_id, conversation_id = str(uuid.uuid4()), str(uuid.uuid4())
    with _owner(dsn) as conn:
        conn.execute(
            "INSERT INTO contacts (id, client_id, full_name) VALUES (%s, %s, 'Synthetic Customer')",
            (contact_id, client_id),
        )
        conn.execute(
            "INSERT INTO conversations (id, client_id, contact_id, channel, channel_thread,"
            " owned_by) VALUES (%s, %s, %s, %s, %s, %s)",
            (
                conversation_id,
                client_id,
                contact_id,
                channel,
                f"thread-{conversation_id}",
                owned_by,
            ),
        )
        if queue:
            assignments = ", ".join(f"{name} = %s" for name in queue)
            conn.execute(
                _q(f"UPDATE conversations SET {assignments} WHERE id = %s"),
                (*queue.values(), conversation_id),
            )
    return Seeded(client_id, conversation_id, contact_id)


def _needs_staff(
    reason: str, priority: str = "normal", minutes_ago: int = 30, **extra: Any
) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    due = since + timedelta(minutes=DEFAULT_TARGETS[priority])
    return {
        "queue_status": "needs_staff",
        "queue_reason": reason,
        "priority": priority,
        "needs_staff_since": since,
        "due_at": due,
        **extra,
    }


def _inbound_params(s: Seeded, key: str) -> tuple[Any, ...]:
    return (
        s.client_id,
        s.conversation_id,
        "inbound",
        "contact",
        "Synthetic question",
        "Synthetic question",
        Jsonb([]),
        False,
        key,
        str(uuid.uuid4()),
    )


def _outbound_params(s: Seeded, author: str = "assistant") -> tuple[Any, ...]:
    return (
        s.client_id,
        s.conversation_id,
        "outbound",
        author,
        "Synthetic answer",
        "Synthetic answer",
        Jsonb([]),
        False,
        str(uuid.uuid4()),
        str(uuid.uuid4()),
    )


def _inbound(app_dsn: str, s: Seeded, key: str | None = None) -> dict[str, Any] | None:
    """A customer message through worker/insert_message.sql; None when the key already exists."""
    with _app(app_dsn, s.client_id) as conn:
        row = conn.execute(
            db.q.load("worker/insert_message.sql"), _inbound_params(s, key or str(uuid.uuid4()))
        ).fetchone()
        conn.commit()
    return row


def _outbound(app_dsn: str, s: Seeded, kind: str) -> None:
    """An outbound message: the bot's reply, a person's reply, or an approved held reply."""
    with _app(app_dsn, s.client_id) as conn:
        if kind == "human":
            conn.execute(
                db.q.load("inbox/insert_reply.sql"),
                (
                    s.client_id,
                    s.conversation_id,
                    "Synthetic reply",
                    "Synthetic reply",
                    str(uuid.uuid4()),
                    str(uuid.uuid4()),
                ),
            )
        else:
            [row] = conn.execute(db.q.load("worker/insert_message.sql"), _outbound_params(s))
            if kind == "reviewed":
                conn.execute(db.q.load("staff/set_reviewed.sql"), (s.client_id, row["id"]))
        conn.commit()


def _escalate(app_dsn: str, s: Seeded, priority: str, trace_id: str | None = None) -> bool:
    """An escalation through turn/insert_escalation.sql; False when the trace already has one."""
    with _app(app_dsn, s.client_id) as conn:
        row = conn.execute(
            db.q.load("turn/insert_escalation.sql"),
            (
                s.client_id,
                trace_id or str(uuid.uuid4()),
                s.conversation_id,
                s.contact_id,
                "synthetic reason",
                priority,
            ),
        ).fetchone()
        conn.commit()
    return row is not None


def _enter(
    app_dsn: str,
    s: Seeded,
    reason: str,
    priority: str = "normal",
    actor: str | None = None,
    claim_for: str | None = None,
    *,
    as_company: str | None = None,
) -> bool:
    with _app(app_dsn, as_company or s.client_id) as conn:
        [row] = conn.execute(
            "SELECT public.inbox_queue_enter(%s, %s, %s, %s, %s, %s) AS changed",
            (s.client_id, s.conversation_id, reason, priority, actor, claim_for),
        )
        conn.commit()
    return row["changed"]


def _approval(dsn: str, s: Seeded, expires_in: int = 3600) -> None:
    with _owner(dsn) as conn:
        conn.execute(
            "INSERT INTO approvals (client_id, conversation_id, trace_id, draft, reason, status,"
            " expires_at) VALUES (%s, %s, %s, 'synthetic draft', 'synthetic', 'pending',"
            " now() + make_interval(secs => %s))",
            (s.client_id, s.conversation_id, str(uuid.uuid4()), expires_in),
        )


def _state(dsn: str, s: Seeded) -> dict[str, Any]:
    with _owner(dsn) as conn:
        row = conn.execute(
            _q(
                f"SELECT {', '.join(QUEUE_COLUMNS)}, last_activity_at, last_customer_at,"
                " turn_count,"
                " (SELECT max(created_at) FROM messages m WHERE m.conversation_id = c.id) AS newest"
                " FROM conversations c WHERE id = %s"
            ),
            (s.conversation_id,),
        ).fetchone()
    assert row is not None
    return row


def _queue(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in QUEUE_COLUMNS}


def _events(dsn: str, s: Seeded) -> list[dict[str, Any]]:
    with _owner(dsn) as conn:
        return conn.execute(
            "SELECT client_id, conversation_id, actor_user_id, action, from_status, to_status,"
            " details FROM inbox_queue_events WHERE conversation_id = %s ORDER BY id",
            (s.conversation_id,),
        ).fetchall()


def _settings(dsn: str, client_id: str, **targets: int) -> None:
    columns = ["client_id", *targets]
    with _owner(dsn) as conn:
        conn.execute(
            _q(
                f"INSERT INTO inbox_settings ({', '.join(columns)})"
                f" VALUES ({', '.join(['%s'] * len(columns))})"
            ),
            (client_id, *targets.values()),
        )


def test_migration_stamps_the_expected_version(migrated_dsn: str) -> None:
    assert EXPECTED_SCHEMA_VERSION >= "0622"
    with _owner(migrated_dsn) as conn:
        row = conn.execute("SELECT 1 AS n FROM schema_migrations WHERE version = '0622'").fetchone()
    assert row is not None


def test_the_triggers_sit_beside_the_existing_ones(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        rows = conn.execute(
            "SELECT tgrelid::regclass::text AS tab, tgname, pg_get_triggerdef(oid) AS def"
            " FROM pg_trigger WHERE NOT tgisinternal"
            " AND tgrelid IN ('public.messages'::regclass, 'public.escalations'::regclass,"
            " 'public.conversations'::regclass)"
        ).fetchall()
    found = {(r["tab"], r["tgname"]): r["def"] for r in rows}
    assert ("messages", "trg_webhook_delivery") in found
    assert ("conversations", "trg_keep_escalated") in found
    message = found[("messages", "inbox_queue_after_message")]
    assert "AFTER INSERT ON public.messages FOR EACH ROW" in message
    escalation = found[("escalations", "inbox_queue_after_escalation")]
    assert "AFTER INSERT ON public.escalations FOR EACH ROW" in escalation
    assert "WHEN ((new.conversation_id IS NOT NULL))" in escalation


def test_an_inbound_customer_message_sets_the_activity_and_customer_times(
    migrated_dsn: str, app_dsn: str
) -> None:
    s = _seed(migrated_dsn, _company(migrated_dsn))
    before = _state(migrated_dsn, s)
    assert _inbound(app_dsn, s) is not None
    after = _state(migrated_dsn, s)
    assert after["last_activity_at"] == after["newest"]
    assert after["last_customer_at"] == after["newest"]
    assert _queue(after) == _queue(before)
    assert after["queue_status"] == "bot"
    assert _events(migrated_dsn, s) == []


@pytest.mark.parametrize("state", ["waiting_customer", "snoozed", "resolved"])
def test_a_customer_writing_to_a_person_owned_item_needs_staff(
    migrated_dsn: str, app_dsn: str, state: str
) -> None:
    c = _company(migrated_dsn)
    user_id = _user(migrated_dsn)
    s = _seed(
        migrated_dsn,
        c,
        owned_by="human_support",
        queue_status=state,
        assignee_user_id=user_id,
        claimed_at=datetime.now(UTC) - timedelta(hours=2),
        snoozed_until=datetime.now(UTC) + timedelta(hours=1) if state == "snoozed" else None,
        resolved_at=datetime.now(UTC) - timedelta(hours=1) if state == "resolved" else None,
    )
    _inbound(app_dsn, s)
    row = _state(migrated_dsn, s)
    assert row["queue_status"] == "needs_staff"
    assert row["queue_reason"] == "customer_message"
    assert row["priority"] == "normal"
    assert row["needs_staff_since"] is not None
    assert row["needs_staff_since"] <= row["newest"]
    assert row["due_at"] - row["needs_staff_since"] == timedelta(minutes=240)
    assert row["snoozed_until"] is None and row["resolved_at"] is None
    # The claim is the person's, kept across the return to the queue.
    assert row["assignee_user_id"] == uuid.UUID(user_id)
    assert row["last_customer_at"] == row["newest"]
    [event] = _events(migrated_dsn, s)
    assert event == {
        "client_id": uuid.UUID(c),
        "conversation_id": uuid.UUID(s.conversation_id),
        "actor_user_id": None,
        "action": "entered",
        "from_status": state,
        "to_status": "needs_staff",
        "details": {"reason": "customer_message", "priority": "normal"},
    }


@pytest.mark.parametrize("owned_by", ["ai", None])
@pytest.mark.parametrize("state", ["waiting_customer", "snoozed", "resolved"])
def test_a_customer_writing_to_a_bot_owned_item_returns_it_to_the_bot(
    migrated_dsn: str, app_dsn: str, state: str, owned_by: str | None
) -> None:
    user_id = _user(migrated_dsn)
    s = _seed(
        migrated_dsn,
        _company(migrated_dsn),
        owned_by=owned_by,
        queue_status=state,
        priority="high",
        assignee_user_id=user_id,
        claimed_at=datetime.now(UTC),
        snoozed_until=datetime.now(UTC) + timedelta(hours=1),
        resolved_at=datetime.now(UTC),
    )
    _inbound(app_dsn, s)
    row = _state(migrated_dsn, s)
    assert row["queue_status"] == "bot"
    for cleared in (
        "assignee_user_id",
        "claimed_at",
        "due_at",
        "queue_reason",
        "needs_staff_since",
        "snoozed_until",
        "resolved_at",
    ):
        assert row[cleared] is None, cleared
    assert row["priority"] == "high"
    assert row["last_customer_at"] == row["newest"]


def test_a_live_held_reply_keeps_a_bot_owned_item_in_the_queue(
    migrated_dsn: str, app_dsn: str
) -> None:
    c = _company(migrated_dsn)
    live = _seed(migrated_dsn, c, owned_by="ai", queue_status="waiting_customer")
    _approval(migrated_dsn, live)
    expired = _seed(migrated_dsn, c, owned_by="ai", queue_status="waiting_customer")
    _approval(migrated_dsn, expired, expires_in=-60)
    _inbound(app_dsn, live)
    _inbound(app_dsn, expired)
    row = _state(migrated_dsn, live)
    assert (row["queue_status"], row["queue_reason"]) == ("needs_staff", "customer_message")
    assert _state(migrated_dsn, expired)["queue_status"] == "bot"


def test_an_item_already_needing_staff_keeps_its_reason_and_clock(
    migrated_dsn: str, app_dsn: str
) -> None:
    s = _seed(
        migrated_dsn,
        _company(migrated_dsn),
        owned_by="human_support",
        **_needs_staff("escalation", "high", minutes_ago=90),
    )
    before = _state(migrated_dsn, s)
    _inbound(app_dsn, s)
    after = _state(migrated_dsn, s)
    assert _queue(after) == _queue(before)
    assert after["last_customer_at"] == after["newest"]
    assert _events(migrated_dsn, s) == []


@pytest.mark.parametrize("kind", ["assistant", "human", "reviewed"])
@pytest.mark.parametrize("state", ["bot", "needs_staff", "waiting_customer", "resolved"])
def test_outbound_messages_change_the_activity_time_only(
    migrated_dsn: str, app_dsn: str, kind: str, state: str
) -> None:
    queue: dict[str, Any] = (
        _needs_staff("held_reply") if state == "needs_staff" else {"queue_status": state}
    )
    s = _seed(migrated_dsn, _company(migrated_dsn), owned_by="human_support", **queue)
    if state == "bot":
        _inbound(app_dsn, s)
    before = _state(migrated_dsn, s)
    _outbound(app_dsn, s, kind)
    after = _state(migrated_dsn, s)
    assert _queue(after) == _queue(before)
    assert after["last_customer_at"] == before["last_customer_at"]
    assert after["last_activity_at"] == after["newest"]
    assert after["last_activity_at"] > before["last_activity_at"]
    assert _events(migrated_dsn, s) == []


def test_an_older_message_never_moves_the_activity_time_backwards(
    migrated_dsn: str, app_dsn: str
) -> None:
    s = _seed(migrated_dsn, _company(migrated_dsn))
    _inbound(app_dsn, s)
    before = _state(migrated_dsn, s)
    with _owner(migrated_dsn) as conn:
        for direction, author in (("inbound", "contact"), ("outbound", "assistant")):
            conn.execute(
                "INSERT INTO messages (client_id, conversation_id, direction, author, body,"
                " idempotency_key, created_at) VALUES (%s, %s, %s, %s, 'synthetic', %s,"
                " now() - interval '1 day')",
                (s.client_id, s.conversation_id, direction, author, str(uuid.uuid4())),
            )
    after = _state(migrated_dsn, s)
    assert after["last_activity_at"] == before["last_activity_at"]
    assert after["last_customer_at"] == before["last_customer_at"]


def test_inbox_version_never_moves(migrated_dsn: str, app_dsn: str) -> None:
    c = _company(migrated_dsn)
    owned = _seed(migrated_dsn, c, owned_by="human_support", queue_status="waiting_customer")
    bot = _seed(migrated_dsn, c)
    with _owner(migrated_dsn) as conn:
        conn.execute(
            "UPDATE conversations SET inbox_version = 7 WHERE id = ANY(%s::uuid[])",
            ([owned.conversation_id, bot.conversation_id],),
        )
    _inbound(app_dsn, owned)
    assert _state(migrated_dsn, owned)["queue_status"] == "needs_staff"
    _inbound(app_dsn, bot)
    _escalate(app_dsn, bot, "urgent")
    assert _state(migrated_dsn, bot)["queue_status"] == "needs_staff"
    _escalate(app_dsn, owned, "urgent")
    _outbound(app_dsn, owned, "human")
    assert _state(migrated_dsn, owned)["inbox_version"] == 7
    assert _state(migrated_dsn, bot)["inbox_version"] == 7


def test_a_replayed_inbound_insert_changes_nothing(migrated_dsn: str, app_dsn: str) -> None:
    s = _seed(
        migrated_dsn, _company(migrated_dsn), owned_by="human_support", queue_status="resolved"
    )
    key = f"synthetic-replay-{uuid.uuid4()}"
    assert _inbound(app_dsn, s, key) is not None
    before = _state(migrated_dsn, s)
    events = _events(migrated_dsn, s)
    assert before["queue_status"] == "needs_staff" and len(events) == 1
    assert _inbound(app_dsn, s, key) is None
    assert _state(migrated_dsn, s) == before
    assert _events(migrated_dsn, s) == events


def test_an_escalation_enters_with_its_priority_and_the_default_target(
    migrated_dsn: str, app_dsn: str
) -> None:
    c = _company(migrated_dsn)
    s = _seed(migrated_dsn, c, owned_by="human_support")
    assert _escalate(app_dsn, s, "high")
    row = _state(migrated_dsn, s)
    assert (row["queue_status"], row["queue_reason"], row["priority"]) == (
        "needs_staff",
        "escalation",
        "high",
    )
    assert row["due_at"] - row["needs_staff_since"] == timedelta(minutes=60)
    [event] = _events(migrated_dsn, s)
    assert (event["action"], event["from_status"], event["to_status"]) == (
        "entered",
        "bot",
        "needs_staff",
    )
    assert event["details"] == {
        "reason": "escalation",
        "priority": "high",
        "previous_priority": "normal",
    }
    assert event["client_id"] == uuid.UUID(c)


def test_an_escalation_uses_the_company_target(migrated_dsn: str, app_dsn: str) -> None:
    c = _company(migrated_dsn)
    _settings(migrated_dsn, c, target_high_min=30)
    s = _seed(migrated_dsn, c)
    _escalate(app_dsn, s, "high")
    row = _state(migrated_dsn, s)
    assert row["due_at"] - row["needs_staff_since"] == timedelta(minutes=30)
    # Another company keeps the default.
    other = _seed(migrated_dsn, _company(migrated_dsn))
    _escalate(app_dsn, other, "high")
    row = _state(migrated_dsn, other)
    assert row["due_at"] - row["needs_staff_since"] == timedelta(minutes=60)


def test_the_built_in_targets_equal_the_settings_column_defaults(
    migrated_dsn: str, app_dsn: str
) -> None:
    with _owner(migrated_dsn) as conn:
        defaults = {
            r["column_name"]: int(r["column_default"])
            for r in conn.execute(
                "SELECT column_name, column_default FROM information_schema.columns"
                " WHERE table_name = 'inbox_settings' AND column_name LIKE 'target_%%_min'"
            ).fetchall()
        }
    assert defaults == {f"target_{p}_min": m for p, m in DEFAULT_TARGETS.items()}
    c = _company(migrated_dsn)
    for priority, minutes in DEFAULT_TARGETS.items():
        s = _seed(migrated_dsn, c)
        assert _enter(app_dsn, s, "manual", priority)
        row = _state(migrated_dsn, s)
        assert row["due_at"] - row["needs_staff_since"] == timedelta(minutes=minutes), priority
        assert row["priority"] == priority


def test_a_higher_priority_escalation_raises_priority_from_the_same_clock(
    migrated_dsn: str, app_dsn: str
) -> None:
    s = _seed(migrated_dsn, _company(migrated_dsn))
    _escalate(app_dsn, s, "normal")
    first = _state(migrated_dsn, s)
    assert first["due_at"] - first["needs_staff_since"] == timedelta(minutes=240)
    _escalate(app_dsn, s, "urgent")
    second = _state(migrated_dsn, s)
    assert second["priority"] == "urgent"
    assert second["needs_staff_since"] == first["needs_staff_since"]
    assert second["due_at"] == first["needs_staff_since"] + timedelta(minutes=15)
    entered, raised = _events(migrated_dsn, s)
    assert entered["action"] == "entered"
    assert raised["action"] == "reason_changed"
    assert (raised["from_status"], raised["to_status"]) == ("needs_staff", "needs_staff")
    assert raised["details"] == {
        "reason": "escalation",
        "priority": "urgent",
        "previous_priority": "normal",
    }


def test_a_lower_priority_escalation_changes_nothing(migrated_dsn: str, app_dsn: str) -> None:
    s = _seed(migrated_dsn, _company(migrated_dsn))
    _escalate(app_dsn, s, "high")
    before = _state(migrated_dsn, s)
    _escalate(app_dsn, s, "low")
    _escalate(app_dsn, s, "high")
    assert _state(migrated_dsn, s) == before
    assert len(_events(migrated_dsn, s)) == 1


def test_an_escalation_skipped_by_on_conflict_does_not_fire(
    migrated_dsn: str, app_dsn: str
) -> None:
    s = _seed(migrated_dsn, _company(migrated_dsn))
    trace_id = str(uuid.uuid4())
    assert _escalate(app_dsn, s, "normal", trace_id)
    before = _state(migrated_dsn, s)
    assert not _escalate(app_dsn, s, "urgent", trace_id)
    assert _state(migrated_dsn, s) == before
    assert len(_events(migrated_dsn, s)) == 1


def test_a_held_reply_item_receiving_an_escalation_becomes_an_escalation(
    migrated_dsn: str, app_dsn: str
) -> None:
    s = _seed(migrated_dsn, _company(migrated_dsn), **_needs_staff("held_reply", minutes_ago=45))
    before = _state(migrated_dsn, s)
    _escalate(app_dsn, s, "normal")
    after = _state(migrated_dsn, s)
    assert (after["queue_reason"], after["priority"]) == ("escalation", "normal")
    assert after["needs_staff_since"] == before["needs_staff_since"]
    assert after["due_at"] == before["due_at"]
    [event] = _events(migrated_dsn, s)
    assert event["action"] == "reason_changed"
    assert event["details"] == {
        "reason": "escalation",
        "priority": "normal",
        "previous_reason": "held_reply",
    }


@pytest.mark.parametrize(
    ("held", "asked", "kept"),
    [
        ("escalation", "held_reply", "escalation"),
        ("escalation", "manual", "escalation"),
        ("escalation", "customer_message", "escalation"),
        ("held_reply", "manual", "held_reply"),
        ("held_reply", "escalation", "escalation"),
        ("manual", "held_reply", "held_reply"),
        ("manual", "customer_message", "manual"),
        ("customer_message", "manual", "manual"),
    ],
)
def test_the_reason_only_moves_up(
    migrated_dsn: str, app_dsn: str, held: str, asked: str, kept: str
) -> None:
    s = _seed(migrated_dsn, _company(migrated_dsn), **_needs_staff(held, "high"))
    before = _state(migrated_dsn, s)
    changed = _enter(app_dsn, s, asked, "low")
    after = _state(migrated_dsn, s)
    assert changed is (kept != held)
    assert after["queue_reason"] == kept
    assert after["priority"] == "high"
    assert after["needs_staff_since"] == before["needs_staff_since"]
    assert len(_events(migrated_dsn, s)) == (1 if changed else 0)


def test_sandbox_and_console_chat_conversations_stay_bot(migrated_dsn: str, app_dsn: str) -> None:
    c = _company(migrated_dsn)
    web = _seed(migrated_dsn, c, channel="web", owned_by="human_support")
    thread = _seed(migrated_dsn, c, owned_by="human_support")
    participant = _seed(migrated_dsn, c, owned_by="human_support")
    user_one, user_two = _user(migrated_dsn), _user(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        for n, (user, seeded) in enumerate(((user_one, thread), (user_two, participant))):
            credential, endpoint = str(uuid.uuid4()), str(uuid.uuid4())
            conn.execute(
                "INSERT INTO channel_credentials (id, client_id, channel)"
                " VALUES (%s, %s, 'webhook')",
                (credential, c),
            )
            conn.execute(
                "INSERT INTO webhook_endpoints (id, client_id, public_id, credential_id)"
                " VALUES (%s, %s, %s, %s)",
                (endpoint, c, f"endpoint-{uuid.uuid4().hex}", credential),
            )
            conn.execute(
                "INSERT INTO console_chat_participants"
                " (client_id, user_id, contact_id, endpoint_id)"
                " VALUES (%s, %s, %s, %s)",
                (c, user, seeded.contact_id, endpoint),
            )
            if n == 0:
                conn.execute(
                    "INSERT INTO console_chat_threads (id, client_id, user_id, contact_id,"
                    " endpoint_id, request_key) VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        seeded.conversation_id,
                        c,
                        user,
                        seeded.contact_id,
                        endpoint,
                        str(uuid.uuid4()),
                    ),
                )
    for seeded in (web, thread, participant):
        _inbound(app_dsn, seeded)
        assert _escalate(app_dsn, seeded, "urgent")
        assert _enter(app_dsn, seeded, "manual", "high", claim_for=user_one) is False
        _outbound(app_dsn, seeded, "human")
        row = _state(migrated_dsn, seeded)
        assert row["queue_status"] == "bot"
        assert row["queue_reason"] is None and row["assignee_user_id"] is None
        assert row["priority"] == "normal"
        assert _events(migrated_dsn, seeded) == []


def test_a_claim_is_taken_only_when_the_item_is_unclaimed(migrated_dsn: str, app_dsn: str) -> None:
    c = _company(migrated_dsn)
    actor, holder = _user(migrated_dsn), _user(migrated_dsn)
    free = _seed(migrated_dsn, c)
    assert _enter(app_dsn, free, "manual", "normal", actor=actor, claim_for=actor)
    row = _state(migrated_dsn, free)
    assert row["assignee_user_id"] == uuid.UUID(actor)
    assert row["claimed_at"] is not None
    [event] = _events(migrated_dsn, free)
    assert event["actor_user_id"] == uuid.UUID(actor)
    assert event["details"] == {
        "reason": "manual",
        "priority": "normal",
        "assignee_user_id": actor,
    }
    claimed_at = datetime.now(UTC) - timedelta(hours=1)
    held = _seed(
        migrated_dsn,
        c,
        **_needs_staff("escalation", assignee_user_id=holder, claimed_at=claimed_at),
    )
    assert _enter(app_dsn, held, "escalation", "normal", actor=actor, claim_for=actor) is False
    row = _state(migrated_dsn, held)
    assert (row["assignee_user_id"], row["claimed_at"]) == (uuid.UUID(holder), claimed_at)
    # An item already in the queue and unclaimed: the claim alone is a change, recorded as such.
    open_item = _seed(migrated_dsn, c, **_needs_staff("escalation"))
    assert _enter(app_dsn, open_item, "escalation", "normal", actor=actor, claim_for=actor)
    assert _state(migrated_dsn, open_item)["assignee_user_id"] == uuid.UUID(actor)
    [event] = _events(migrated_dsn, open_item)
    assert (event["action"], event["details"]["assignee_user_id"]) == ("claimed", actor)


def test_enter_refuses_an_unknown_reason_or_priority(migrated_dsn: str, app_dsn: str) -> None:
    s = _seed(migrated_dsn, _company(migrated_dsn))
    for reason, priority in (("invented", "normal"), ("manual", "invented"), ("manual", None)):
        with pytest.raises(errors.InvalidParameterValue):
            _enter(app_dsn, s, reason, priority)  # type: ignore[arg-type]
    assert _state(migrated_dsn, s)["queue_status"] == "bot"


def test_enter_finds_nothing_of_another_company(migrated_dsn: str, app_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    s = _seed(migrated_dsn, a)
    # Row security: company b cannot see the conversation.
    assert _enter(app_dsn, s, "manual", as_company=b) is False
    # A mismatched company id finds nothing even for the owner of the row.
    with _owner(migrated_dsn) as conn:
        [row] = conn.execute(
            "SELECT public.inbox_queue_enter(%s, %s, 'manual') AS changed", (b, s.conversation_id)
        )
    assert row["changed"] is False
    assert _state(migrated_dsn, s)["queue_status"] == "bot"
    assert _events(migrated_dsn, s) == []


def test_function_grants(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        [row] = conn.execute(
            "SELECT has_function_privilege('csa_app', %(f)s, 'EXECUTE') AS app,"
            " has_function_privilege('csa_ro', %(f)s, 'EXECUTE') AS ro,"
            " has_function_privilege('csa_identity', %(f)s, 'EXECUTE') AS identity",
            {"f": "public.inbox_queue_enter(uuid, uuid, text, text, uuid, uuid)"},
        )
    assert row == {"app": True, "ro": False, "identity": False}


def _updates(conn: DictConnection) -> int:
    """Rows of conversations this transaction has updated so far.

    pg_stat_xact_user_tables is the transaction-local view: it counts this backend's own work in
    the open transaction and needs no stats flush, so the count is exact.
    """
    [row] = conn.execute(
        "SELECT n_tup_upd AS n FROM pg_stat_xact_user_tables"
        " WHERE relid = 'public.conversations'::regclass"
    )
    return row["n"]


def test_each_message_updates_its_conversation_once(migrated_dsn: str, app_dsn: str) -> None:
    """The trigger's cost: one UPDATE of the conversation per message, inbound or outbound.

    The worker adds its existing turn_count update (worker/increment_turn_count.sql) for a fresh
    inbound message, so a real inbound turn costs two; this test does not call it. The rare
    re-entry of a waiting, snoozed or resolved item into the queue costs one more (the call to
    inbox_queue_enter), pinned in the next test.
    """
    c = _company(migrated_dsn)
    owned = {"owned_by": "human_support"}
    seeded = [
        (_seed(migrated_dsn, c), "inbound"),
        (_seed(migrated_dsn, c, **owned, **_needs_staff("escalation")), "inbound"),
        # The first message returns this one to the bot inside the same single update.
        (_seed(migrated_dsn, c, owned_by="ai", queue_status="waiting_customer"), "inbound"),
        (_seed(migrated_dsn, c, **owned, queue_status="waiting_customer"), "outbound"),
    ]
    insert = db.q.load("worker/insert_message.sql")
    with _app(app_dsn, c) as conn:
        for n in range(200):
            s, direction = seeded[n % len(seeded)]
            params = (
                _inbound_params(s, str(uuid.uuid4()))
                if direction == "inbound"
                else _outbound_params(s)
            )
            before = _updates(conn)
            assert conn.execute(insert, params).fetchone() is not None
            assert _updates(conn) - before == 1, (n, direction)
        conn.commit()
    assert _state(migrated_dsn, seeded[2][0])["queue_status"] == "bot"
    for s, _ in seeded:
        assert _state(migrated_dsn, s)["turn_count"] == 0


def test_a_return_to_the_queue_costs_one_more_update(migrated_dsn: str, app_dsn: str) -> None:
    s = _seed(
        migrated_dsn, _company(migrated_dsn), owned_by="human_support", queue_status="snoozed"
    )
    with _app(app_dsn, s.client_id) as conn:
        before = _updates(conn)
        conn.execute(
            db.q.load("worker/insert_message.sql"), _inbound_params(s, str(uuid.uuid4()))
        ).fetchone()
        assert _updates(conn) - before == 2
        conn.commit()
    assert _state(migrated_dsn, s)["queue_status"] == "needs_staff"


def _shape(dsn: str) -> list[tuple[Any, ...]]:
    with _owner(dsn) as conn:
        functions = conn.execute(
            "SELECT p.oid::regprocedure::text AS name, pg_get_functiondef(p.oid) AS def,"
            " p.proacl::text AS acl FROM pg_proc p"
            " WHERE p.pronamespace = 'public'::regnamespace AND p.proname LIKE 'inbox_queue_%%'"
            " ORDER BY 1"
        ).fetchall()
        triggers = conn.execute(
            "SELECT tgname, pg_get_triggerdef(oid) AS def FROM pg_trigger WHERE NOT tgisinternal"
            " AND tgrelid IN ('public.messages'::regclass, 'public.escalations'::regclass,"
            " 'public.conversations'::regclass) ORDER BY tgname"
        ).fetchall()
    return [tuple(r.values()) for r in [*functions, *triggers]]


def test_running_0622_again_changes_nothing(migrated_dsn: str, app_dsn: str) -> None:
    s = _seed(migrated_dsn, _company(migrated_dsn), owned_by="human_support")
    _escalate(app_dsn, s, "high")
    before, row = _shape(migrated_dsn), _state(migrated_dsn, s)
    assert len([t for t in before if t[0] in ("inbox_queue_after_message",)]) == 1
    paths = dict(migrate.discover_migrations())
    text = paths["0622"].read_text(encoding="utf-8")
    with _owner(migrated_dsn) as conn:
        conn.execute(text)  # type: ignore[arg-type]
        conn.execute(text)  # type: ignore[arg-type]
    assert _shape(migrated_dsn) == before
    assert _state(migrated_dsn, s) == row
    assert len(_events(migrated_dsn, s)) == 1
    # The triggers still fire once each after the re-run.
    _escalate(app_dsn, s, "urgent")
    assert len(_events(migrated_dsn, s)) == 2
