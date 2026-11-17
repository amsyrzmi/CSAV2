"""Schema contract for migration 0620: the queue columns on conversations, their checks, the
indexes from design 6.5 and the backfill function inbox_queue_backfill (disposable database).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from apps.gateway.main import EXPECTED_SCHEMA_VERSION
from psycopg import errors
from scripts import migrate

from tests.conftest import requires_docker
from tests.test_bookings_schema import DictConnection, _app, _owner
from tests.test_training_schema import _company

pytestmark = requires_docker

DAY = 86_400
MINUTE = 60
NEW_COLUMNS = (
    "queue_status",
    "queue_reason",
    "topic",
    "priority",
    "assignee_user_id",
    "claimed_at",
    "needs_staff_since",
    "due_at",
    "snoozed_until",
    "resolved_at",
    "last_activity_at",
    "last_customer_at",
    "last_staff_reply_at",
)


@dataclass
class Seeded:
    client_id: str
    conversation_id: str
    contact_id: str


def _seed(
    dsn: str,
    client_id: str,
    *,
    channel: str = "telegram",
    owned_by: str | None = None,
    status: str = "open",
    messages: tuple[tuple[str, str, str, int], ...] = (),
    escalation: tuple[str, int] | None = None,
    more_escalations: tuple[tuple[str, int], ...] = (),
    approval: tuple[str, int, int] | None = None,
    opened_ago: int = 40 * DAY,
) -> Seeded:
    """One conversation.

    A message is (direction, author, send_authority, seconds ago); an escalation is (priority,
    seconds ago); an approval is (status, seconds ago, expires in).
    """
    contact_id, conversation_id = str(uuid.uuid4()), str(uuid.uuid4())
    with _owner(dsn) as conn:
        conn.execute(
            "INSERT INTO contacts (id, client_id, full_name) VALUES (%s, %s, 'Synthetic Customer')",
            (contact_id, client_id),
        )
        conn.execute(
            "INSERT INTO conversations (id, client_id, contact_id, channel, channel_thread,"
            " owned_by, status, opened_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, now() - make_interval(secs => %s))",
            (
                conversation_id,
                client_id,
                contact_id,
                channel,
                f"thread-{conversation_id}",
                owned_by,
                status,
                opened_ago,
            ),
        )
        for direction, author, authority, ago in messages:
            conn.execute(
                "INSERT INTO messages (client_id, conversation_id, direction, author, body,"
                " send_authority, idempotency_key, created_at)"
                " VALUES (%s, %s, %s, %s, 'synthetic text', %s, %s,"
                " now() - make_interval(secs => %s))",
                (
                    client_id,
                    conversation_id,
                    direction,
                    author,
                    authority,
                    str(uuid.uuid4()),
                    ago,
                ),
            )
        for priority, ago in (*((escalation,) if escalation else ()), *more_escalations):
            conn.execute(
                "INSERT INTO escalations (client_id, trace_id, conversation_id, contact_id,"
                " reason, priority, status, created_at)"
                " VALUES (%s, %s, %s, %s, 'synthetic reason', %s, 'open',"
                " now() - make_interval(secs => %s))",
                (client_id, str(uuid.uuid4()), conversation_id, contact_id, priority, ago),
            )
        if approval is not None:
            status, ago, expires_in = approval
            conn.execute(
                "INSERT INTO approvals (client_id, conversation_id, trace_id, draft, reason,"
                " status, expires_at, created_at)"
                " VALUES (%s, %s, %s, 'synthetic draft', 'synthetic', %s,"
                " now() + make_interval(secs => %s), now() - make_interval(secs => %s))",
                (client_id, conversation_id, str(uuid.uuid4()), status, expires_in, ago),
            )
        # These rows stand for data from before 0622, which is what the backfill meets: the 0622
        # triggers have already queued an escalated conversation and stamped the message times,
        # so every column the backfill derives goes back to what 0620 found (last_activity_at to
        # an old sentinel, as no real value is that old), and the backfill has to work each out.
        conn.execute(
            "UPDATE conversations SET queue_status = 'bot', queue_reason = NULL,"
            " priority = 'normal', needs_staff_since = NULL, due_at = NULL,"
            " assignee_user_id = NULL, claimed_at = NULL, snoozed_until = NULL,"
            " resolved_at = NULL, last_activity_at = '2000-01-01T00:00:00Z',"
            " last_customer_at = NULL, last_staff_reply_at = NULL WHERE id = %s",
            (conversation_id,),
        )
    return Seeded(client_id, conversation_id, contact_id)


def _row(dsn: str, conversation_id: str) -> dict[str, Any]:
    with _owner(dsn) as conn:
        row = conn.execute(
            "SELECT queue_status, queue_reason, priority, needs_staff_since, due_at,"
            " last_activity_at, last_customer_at, last_staff_reply_at, topic, assignee_user_id,"
            " (SELECT max(created_at) FROM messages m WHERE m.conversation_id = c.id) AS newest"
            " FROM conversations c WHERE id = %s",
            (conversation_id,),
        ).fetchone()
    assert row is not None
    return row


def _escalation_times(dsn: str, conversation_id: str) -> list[Any]:
    """The conversation's escalation times, oldest first."""
    with _owner(dsn) as conn:
        rows = conn.execute(
            "SELECT created_at FROM escalations WHERE conversation_id = %s ORDER BY created_at",
            (conversation_id,),
        ).fetchall()
    return [r["created_at"] for r in rows]


def _approval_time(dsn: str, conversation_id: str) -> Any:
    with _owner(dsn) as conn:
        row = conn.execute(
            "SELECT created_at FROM approvals WHERE conversation_id = %s", (conversation_id,)
        ).fetchone()
    assert row is not None
    return row["created_at"]


def _backfill(dsn: str, client_id: str | None = None) -> int:
    with _owner(dsn) as conn:
        row = conn.execute(
            "SELECT public.inbox_queue_backfill(%s::uuid) AS n", (client_id,)
        ).fetchone()
    assert row is not None
    return row["n"]


def _snapshot(dsn: str) -> list[tuple[Any, ...]]:
    with _owner(dsn) as conn:
        rows = conn.execute(
            "SELECT id, queue_status, queue_reason, priority, needs_staff_since, due_at,"
            " last_activity_at, last_customer_at, last_staff_reply_at FROM conversations"
            " ORDER BY id"
        ).fetchall()
    return [tuple(r.values()) for r in rows]


def _definitions(dsn: str) -> list[tuple[Any, ...]]:
    with _owner(dsn) as conn:
        columns = conn.execute(
            "SELECT column_name, data_type, is_nullable, column_default"
            " FROM information_schema.columns WHERE table_name = 'conversations'"
            " ORDER BY ordinal_position"
        ).fetchall()
        constraints = conn.execute(
            "SELECT conname, convalidated, pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conrelid = 'public.conversations'::regclass ORDER BY conname"
        ).fetchall()
        indexes = conn.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'"
            " AND tablename IN ('conversations', 'contacts') ORDER BY indexname"
        ).fetchall()
        function = conn.execute(
            "SELECT pg_get_functiondef('public.inbox_queue_backfill(uuid)'::regprocedure) AS d"
        ).fetchall()
    return [tuple(r.values()) for r in [*columns, *constraints, *indexes, *function]]


def _human(ago: int, authority: str = "human") -> tuple[str, str, str, int]:
    return ("outbound", "human_support", authority, ago)


def _customer(ago: int) -> tuple[str, str, str, int]:
    return ("inbound", "contact", "automation", ago)


def _bot(ago: int) -> tuple[str, str, str, int]:
    return ("outbound", "assistant", "automation", ago)


def test_migration_stamps_the_expected_version(migrated_dsn: str) -> None:
    assert EXPECTED_SCHEMA_VERSION >= "0620"
    with _owner(migrated_dsn) as conn:
        row = conn.execute("SELECT 1 AS n FROM schema_migrations WHERE version = '0620'").fetchone()
    assert row is not None


def test_new_columns_exist_with_their_defaults(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        rows = {
            r["column_name"]: r
            for r in conn.execute(
                "SELECT column_name, data_type, is_nullable, column_default"
                " FROM information_schema.columns WHERE table_name = 'conversations'"
            ).fetchall()
        }
    assert set(NEW_COLUMNS) <= set(rows)
    assert rows["queue_status"]["is_nullable"] == "NO"
    assert rows["queue_status"]["column_default"] == "'bot'::text"
    assert rows["priority"]["is_nullable"] == "NO"
    assert rows["priority"]["column_default"] == "'normal'::text"
    assert rows["last_activity_at"]["is_nullable"] == "NO"
    assert rows["last_activity_at"]["data_type"] == "timestamp with time zone"
    assert rows["assignee_user_id"]["data_type"] == "uuid"
    for name in ("queue_reason", "topic", "assignee_user_id", "needs_staff_since", "due_at"):
        assert rows[name]["is_nullable"] == "YES"


@pytest.mark.parametrize(
    "column, good, bad",
    [
        ("queue_status", ["bot", "needs_staff", "waiting_customer", "snoozed", "resolved"], "x"),
        ("queue_reason", ["escalation", "held_reply", "customer_message", "manual"], "x"),
        (
            "topic",
            ["purchase", "question", "complaint", "payment", "booking", "subscription", "other"],
            "x",
        ),
        ("priority", ["low", "normal", "high", "urgent"], "x"),
    ],
)
def test_value_checks_accept_the_edges_and_refuse_the_rest(
    migrated_dsn: str, column: str, good: list[str], bad: str
) -> None:
    client_id = _company(migrated_dsn)

    def update(conn: DictConnection, conversation_id: str, value: str) -> None:
        extra = ""
        if column == "queue_status" and value == "needs_staff":
            extra = ", queue_reason = 'manual', needs_staff_since = now(), due_at = now()"
        conn.execute(
            f"UPDATE conversations SET {column} = %s{extra} WHERE id = %s",  # type: ignore[arg-type]
            (value, conversation_id),
        )

    seeded = _seed(migrated_dsn, client_id)
    with _owner(migrated_dsn) as conn:
        for value in good:
            update(conn, seeded.conversation_id, value)
        with pytest.raises(errors.CheckViolation):
            update(conn, seeded.conversation_id, bad)


def test_a_needs_staff_row_needs_reason_since_and_due(migrated_dsn: str) -> None:
    client_id = _company(migrated_dsn)
    seeded = _seed(migrated_dsn, client_id)
    complete = "queue_reason = 'manual', needs_staff_since = now(), due_at = now()"
    with _owner(migrated_dsn) as conn:
        for missing in ("queue_reason", "needs_staff_since", "due_at"):
            assignments = ", ".join(p for p in complete.split(", ") if not p.startswith(missing))
            with pytest.raises(errors.CheckViolation):
                conn.execute(
                    "UPDATE conversations SET queue_status = 'needs_staff', "  # type: ignore[arg-type]
                    + assignments
                    + " WHERE id = %s",
                    (seeded.conversation_id,),
                )
        with pytest.raises(errors.CheckViolation):
            conn.execute(
                "UPDATE conversations SET queue_status = 'needs_staff' WHERE id = %s",
                (seeded.conversation_id,),
            )
        conn.execute(
            "UPDATE conversations SET queue_status = 'needs_staff', "  # type: ignore[arg-type]
            + complete
            + " WHERE id = %s",
            (seeded.conversation_id,),
        )
        # Other states need none of the three.
        conn.execute(
            "UPDATE conversations SET queue_status = 'waiting_customer', queue_reason = NULL,"
            " needs_staff_since = NULL, due_at = NULL WHERE id = %s",
            (seeded.conversation_id,),
        )


def test_the_assignee_foreign_key_refuses_an_unknown_user(migrated_dsn: str) -> None:
    client_id = _company(migrated_dsn)
    seeded = _seed(migrated_dsn, client_id)
    with _owner(migrated_dsn) as conn:
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(
                "UPDATE conversations SET assignee_user_id = %s WHERE id = %s",
                (str(uuid.uuid4()), seeded.conversation_id),
            )
        user_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO console_users (id, email, display_name) VALUES (%s, %s, 'Synthetic')",
            (user_id, f"{user_id}@example.test"),
        )
        conn.execute(
            "UPDATE conversations SET assignee_user_id = %s WHERE id = %s",
            (user_id, seeded.conversation_id),
        )


def test_all_constraints_are_validated(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        rows = conn.execute(
            "SELECT conname FROM pg_constraint WHERE NOT convalidated"
            " AND conrelid = 'public.conversations'::regclass"
        ).fetchall()
    assert rows == []


def _index_definitions(dsn: str) -> dict[str, str]:
    with _owner(dsn) as conn:
        return {
            r["indexname"]: r["indexdef"]
            for r in conn.execute(
                "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'"
                " AND tablename IN ('conversations', 'contacts')"
            ).fetchall()
        }


def _find(defs: dict[str, str], *needles: str) -> str:
    matches = [d for d in defs.values() if all(n in d for n in needles)]
    assert len(matches) == 1, (needles, matches)
    return matches[0]


def test_queue_indexes_exist_with_their_predicates(migrated_dsn: str) -> None:
    defs = _index_definitions(migrated_dsn)
    on = "ON public.conversations"
    activity = defs["conversations_activity_idx"]
    assert on in activity and "(client_id, last_activity_at DESC, id DESC)" in activity
    assert "WHERE" not in activity
    # The Snoozed and Waiting on customer tabs in their default (activity) order, one per state.
    for name, state in (
        ("conversations_queue_snoozed_activity_idx", "snoozed"),
        ("conversations_queue_waiting_activity_idx", "waiting_customer"),
    ):
        tab = defs[name]
        assert on in tab and "(client_id, last_activity_at DESC, id DESC)" in tab, name
        assert f"WHERE (queue_status = '{state}'::text)" in tab, name
    assert "conversations_queue_activity_idx" not in defs
    due = _find(defs, on, "(client_id, due_at, id)")
    assert "WHERE (queue_status = 'needs_staff'::text)" in due
    mine = _find(defs, on, "(client_id, assignee_user_id, due_at, id)")
    assert "queue_status = ANY (ARRAY['needs_staff'::text, 'waiting_customer'::text])" in mine
    assert "assignee_user_id IS NOT NULL" in mine
    since = _find(defs, on, "(client_id, needs_staff_since, id)")
    assert "WHERE (queue_status = 'needs_staff'::text)" in since
    snoozed = _find(defs, on, "(client_id, snoozed_until)")
    assert "WHERE (queue_status = 'snoozed'::text)" in snoozed
    replied = _find(defs, on, "(client_id, last_staff_reply_at)")
    assert "WHERE (queue_status = 'waiting_customer'::text)" in replied


def test_no_trigram_index_is_built_for_the_contact_search(migrated_dsn: str) -> None:
    # The app role reads contacts under row security and the ILIKE operators are not
    # leakproof, so it could never use one: search is a bounded per-company scan.
    defs = _index_definitions(migrated_dsn)
    for name in (
        "contacts_full_name_trgm_idx",
        "contacts_telegram_username_trgm_idx",
        "contacts_phone_digits_trgm_idx",
    ):
        assert name not in defs
    assert [d for d in defs.values() if "gin_trgm_ops" in d] == []


def test_an_ordinary_bot_conversation_stays_bot_with_its_newest_message_time(
    migrated_dsn: str,
) -> None:
    client_id = _company(migrated_dsn)
    seeded = _seed(
        migrated_dsn, client_id, messages=(_customer(2 * DAY), _bot(2 * DAY - 5), _customer(DAY))
    )
    _backfill(migrated_dsn)
    row = _row(migrated_dsn, seeded.conversation_id)
    assert row["queue_status"] == "bot"
    assert row["queue_reason"] is None
    assert row["last_activity_at"] == row["newest"]
    assert row["last_customer_at"] == row["newest"]
    assert row["last_staff_reply_at"] is None


def test_a_conversation_without_messages_takes_its_opened_time(migrated_dsn: str) -> None:
    client_id = _company(migrated_dsn)
    seeded = _seed(migrated_dsn, client_id, opened_ago=3 * DAY)
    _backfill(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        row = conn.execute(
            "SELECT last_activity_at = opened_at AS same, last_customer_at"
            " FROM conversations WHERE id = %s",
            (seeded.conversation_id,),
        ).fetchone()
    assert row == {"same": True, "last_customer_at": None}


def test_an_escalated_open_conversation_needs_staff_with_the_escalation_priority(
    migrated_dsn: str,
) -> None:
    client_id = _company(migrated_dsn)
    seeded = _seed(
        migrated_dsn,
        client_id,
        owned_by="human_support",
        messages=(_customer(2 * DAY), _bot(2 * DAY - 5), _customer(DAY)),
        escalation=("urgent", 2 * DAY),
    )
    _backfill(migrated_dsn)
    row = _row(migrated_dsn, seeded.conversation_id)
    assert row["queue_status"] == "needs_staff"
    assert row["queue_reason"] == "escalation"
    assert row["priority"] == "urgent"
    with _owner(migrated_dsn) as conn:
        checks = conn.execute(
            "SELECT needs_staff_since = (SELECT created_at FROM escalations WHERE"
            " conversation_id = c.id) AS since_is_escalation,"
            " due_at = needs_staff_since + interval '240 minutes' AS due_is_target"
            " FROM conversations c WHERE id = %s",
            (seeded.conversation_id,),
        ).fetchone()
    assert checks == {"since_is_escalation": True, "due_is_target": True}


def test_the_highest_priority_of_several_open_escalations_wins(migrated_dsn: str) -> None:
    client_id = _company(migrated_dsn)
    seeded = _seed(
        migrated_dsn,
        client_id,
        owned_by="human_support",
        messages=(_customer(DAY),),
        escalation=("normal", 3 * DAY),
        more_escalations=(("high", DAY),),
    )
    _backfill(migrated_dsn)
    row = _row(migrated_dsn, seeded.conversation_id)
    assert (row["queue_status"], row["priority"]) == ("needs_staff", "high")
    oldest, _ = _escalation_times(migrated_dsn, seeded.conversation_id)
    assert row["needs_staff_since"] == oldest


def test_a_human_owned_conversation_with_an_inbound_newest_message_needs_staff(
    migrated_dsn: str,
) -> None:
    client_id = _company(migrated_dsn)
    seeded = _seed(
        migrated_dsn,
        client_id,
        owned_by="human_support",
        messages=(_human(2 * DAY), _customer(3 * 3600)),
    )
    _backfill(migrated_dsn)
    row = _row(migrated_dsn, seeded.conversation_id)
    assert row["queue_status"] == "needs_staff"
    assert row["queue_reason"] == "customer_message"
    assert row["priority"] == "normal"
    assert row["needs_staff_since"] == row["last_customer_at"] == row["newest"]
    assert row["due_at"] - row["needs_staff_since"] == timedelta(minutes=240)
    assert row["last_staff_reply_at"] is not None


def test_a_live_pending_approval_makes_a_held_reply(migrated_dsn: str) -> None:
    client_id = _company(migrated_dsn)
    seeded = _seed(
        migrated_dsn,
        client_id,
        messages=(_customer(3 * 3600),),
        approval=("pending", 2 * 3600, 3600),
    )
    _backfill(migrated_dsn)
    row = _row(migrated_dsn, seeded.conversation_id)
    assert row["queue_status"] == "needs_staff"
    assert row["queue_reason"] == "held_reply"
    assert row["priority"] == "normal"
    with _owner(migrated_dsn) as conn:
        same = conn.execute(
            "SELECT needs_staff_since = (SELECT created_at FROM approvals WHERE"
            " conversation_id = c.id) AS same FROM conversations c WHERE id = %s",
            (seeded.conversation_id,),
        ).fetchone()
    assert same == {"same": True}


def test_an_expired_or_decided_approval_does_not_count(migrated_dsn: str) -> None:
    client_id = _company(migrated_dsn)
    expired = _seed(
        migrated_dsn, client_id, messages=(_customer(3 * 3600),), approval=("pending", 7200, -60)
    )
    decided = _seed(
        migrated_dsn, client_id, messages=(_customer(3 * 3600),), approval=("approved", 7200, 3600)
    )
    _backfill(migrated_dsn)
    assert _row(migrated_dsn, expired.conversation_id)["queue_status"] == "bot"
    assert _row(migrated_dsn, decided.conversation_id)["queue_status"] == "bot"


def test_the_reason_precedence_is_escalation_then_held_reply_then_customer_message(
    migrated_dsn: str,
) -> None:
    client_id = _company(migrated_dsn)
    everything = _seed(
        migrated_dsn,
        client_id,
        owned_by="human_support",
        messages=(_customer(3 * 3600),),
        escalation=("high", 5 * 3600),
        approval=("pending", 2 * 3600, 3600),
    )
    held_and_owned = _seed(
        migrated_dsn,
        client_id,
        owned_by="human_support",
        messages=(_customer(3 * 3600),),
        approval=("pending", 2 * 3600, 3600),
    )
    _backfill(migrated_dsn)
    row = _row(migrated_dsn, everything.conversation_id)
    assert row["queue_reason"] == "escalation"
    assert row["priority"] == "high"
    assert [row["needs_staff_since"]] == _escalation_times(migrated_dsn, everything.conversation_id)
    row = _row(migrated_dsn, held_and_owned.conversation_id)
    assert row["queue_reason"] == "held_reply"
    assert row["needs_staff_since"] == _approval_time(migrated_dsn, held_and_owned.conversation_id)


def test_a_human_owned_conversation_whose_newest_message_is_a_human_reply_waits(
    migrated_dsn: str,
) -> None:
    client_id = _company(migrated_dsn)
    seeded = _seed(
        migrated_dsn,
        client_id,
        owned_by="human_support",
        messages=(_customer(2 * 3600), _human(3600)),
    )
    reviewed = _seed(
        migrated_dsn,
        client_id,
        owned_by="human_support",
        messages=(_customer(2 * 3600), ("outbound", "assistant", "reviewed", 3600)),
    )
    _backfill(migrated_dsn)
    for conversation in (seeded, reviewed):
        row = _row(migrated_dsn, conversation.conversation_id)
        assert row["queue_status"] == "waiting_customer"
        assert row["queue_reason"] is None
        assert row["needs_staff_since"] is None
        assert row["due_at"] is None
        assert row["last_staff_reply_at"] == row["newest"]
        assert row["last_customer_at"] is not None
        assert row["last_staff_reply_at"] > row["last_customer_at"]


def test_a_bot_reply_is_not_a_staff_reply(migrated_dsn: str) -> None:
    client_id = _company(migrated_dsn)
    seeded = _seed(
        migrated_dsn,
        client_id,
        owned_by="human_support",
        messages=(_customer(2 * 3600), _bot(3600)),
    )
    _backfill(migrated_dsn)
    row = _row(migrated_dsn, seeded.conversation_id)
    assert row["queue_status"] == "bot"
    assert row["last_staff_reply_at"] is None


def test_an_escalation_older_than_thirty_days_stays_bot(migrated_dsn: str) -> None:
    client_id = _company(migrated_dsn)
    seeded = _seed(
        migrated_dsn,
        client_id,
        owned_by="human_support",
        messages=(_customer(32 * DAY),),
        escalation=("urgent", 31 * DAY),
        opened_ago=35 * DAY,
    )
    inside = _seed(
        migrated_dsn,
        client_id,
        owned_by="human_support",
        messages=(_customer(29 * DAY),),
        escalation=("urgent", 29 * DAY),
        opened_ago=35 * DAY,
    )
    _backfill(migrated_dsn)
    assert _row(migrated_dsn, seeded.conversation_id)["queue_status"] == "bot"
    assert _row(migrated_dsn, inside.conversation_id)["queue_status"] == "needs_staff"


def test_an_escalation_answered_by_a_person_is_not_needs_staff(migrated_dsn: str) -> None:
    client_id = _company(migrated_dsn)
    owned = _seed(
        migrated_dsn,
        client_id,
        owned_by="human_support",
        status="escalated",
        messages=(_customer(3 * 3600), _human(3600)),
        escalation=("urgent", 2 * 3600),
    )
    _backfill(migrated_dsn)
    row = _row(migrated_dsn, owned.conversation_id)
    assert row["queue_status"] == "waiting_customer"
    assert row["queue_reason"] is None
    assert row["priority"] == "normal"


def test_an_escalated_conversation_the_ai_owns_again_is_bot(migrated_dsn: str) -> None:
    # Released (or its mute expired): status open, the AI owns it. Nothing tells the two apart
    # in the data, so it stays reachable in All instead of filling Needs us.
    client_id = _company(migrated_dsn)
    released = _seed(
        migrated_dsn,
        client_id,
        owned_by="ai",
        messages=(_customer(3 * 3600), _bot(3 * 3600 - 5)),
        escalation=("urgent", 3 * 3600),
    )
    _backfill(migrated_dsn)
    row = _row(migrated_dsn, released.conversation_id)
    assert row["queue_status"] == "bot"
    assert row["queue_reason"] is None
    assert row["priority"] == "normal"


def test_a_held_unanswered_escalation_needs_staff(migrated_dsn: str) -> None:
    # Held by the escalation mute (status escalated) without a person owning it.
    client_id = _company(migrated_dsn)
    held = _seed(
        migrated_dsn,
        client_id,
        status="escalated",
        messages=(_customer(3 * 3600), _bot(3 * 3600 - 5)),
        escalation=("high", 3 * 3600),
    )
    _backfill(migrated_dsn)
    row = _row(migrated_dsn, held.conversation_id)
    assert (row["queue_status"], row["queue_reason"], row["priority"]) == (
        "needs_staff",
        "escalation",
        "high",
    )
    assert [row["needs_staff_since"]] == _escalation_times(migrated_dsn, held.conversation_id)


def test_waiting_on_the_customer_for_over_thirty_days_is_bot(migrated_dsn: str) -> None:
    client_id = _company(migrated_dsn)
    old = _seed(
        migrated_dsn,
        client_id,
        owned_by="human_support",
        messages=(_customer(32 * DAY), _human(31 * DAY)),
        opened_ago=35 * DAY,
    )
    recent = _seed(
        migrated_dsn,
        client_id,
        owned_by="human_support",
        messages=(_customer(30 * DAY), _human(29 * DAY)),
        opened_ago=35 * DAY,
    )
    _backfill(migrated_dsn)
    row = _row(migrated_dsn, old.conversation_id)
    assert row["queue_status"] == "bot"
    assert row["last_staff_reply_at"] == row["newest"]
    assert _row(migrated_dsn, recent.conversation_id)["queue_status"] == "waiting_customer"


def test_the_priority_ignores_an_escalation_older_than_thirty_days(migrated_dsn: str) -> None:
    client_id = _company(migrated_dsn)
    seeded = _seed(
        migrated_dsn,
        client_id,
        owned_by="human_support",
        messages=(_customer(DAY),),
        escalation=("urgent", 40 * DAY),
        more_escalations=(("normal", DAY),),
        opened_ago=45 * DAY,
    )
    _backfill(migrated_dsn)
    row = _row(migrated_dsn, seeded.conversation_id)
    assert (row["queue_status"], row["queue_reason"], row["priority"]) == (
        "needs_staff",
        "escalation",
        "normal",
    )
    _, recent = _escalation_times(migrated_dsn, seeded.conversation_id)
    assert row["needs_staff_since"] == recent


def test_a_console_chat_thread_alone_keeps_a_conversation_bot(migrated_dsn: str) -> None:
    # The thread row points at the conversation, but the conversation's own contact is no
    # participant: only the thread exclusion can keep it out.
    client_id = _company(migrated_dsn)
    seeded = _seed(
        migrated_dsn,
        client_id,
        owned_by="human_support",
        messages=(_customer(3600),),
        escalation=("high", 3600),
    )
    stand_in = _seed(migrated_dsn, client_id)
    user, endpoint, credential = (str(uuid.uuid4()) for _ in range(3))
    with _owner(migrated_dsn) as conn:
        conn.execute(
            "INSERT INTO console_users (id, email, display_name) VALUES (%s, %s, 'Synthetic')",
            (user, f"{user}@example.test"),
        )
        conn.execute(
            "INSERT INTO channel_credentials (id, client_id, channel) VALUES (%s, %s, 'webhook')",
            (credential, client_id),
        )
        conn.execute(
            "INSERT INTO webhook_endpoints (id, client_id, public_id, credential_id)"
            " VALUES (%s, %s, %s, %s)",
            (endpoint, client_id, f"endpoint-{uuid.uuid4().hex}", credential),
        )
        conn.execute(
            "INSERT INTO console_chat_participants (client_id, user_id, contact_id, endpoint_id)"
            " VALUES (%s, %s, %s, %s)",
            (client_id, user, stand_in.contact_id, endpoint),
        )
        conn.execute(
            "INSERT INTO console_chat_threads (id, client_id, user_id, contact_id, endpoint_id,"
            " request_key) VALUES (%s, %s, %s, %s, %s, %s)",
            (seeded.conversation_id, client_id, user, stand_in.contact_id, endpoint, uuid.uuid4()),
        )
    _backfill(migrated_dsn)
    row = _row(migrated_dsn, seeded.conversation_id)
    assert (row["queue_status"], row["queue_reason"]) == ("bot", None)


def test_the_app_role_cannot_run_the_backfill(migrated_dsn: str, app_dsn: str) -> None:
    client_id = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        rights = conn.execute(
            "SELECT has_function_privilege(r, 'public.inbox_queue_backfill(uuid)', 'EXECUTE') AS x"
            " FROM unnest(ARRAY['csa_app', 'csa_ro', 'csa_identity']) AS r"
        ).fetchall()
    assert [r["x"] for r in rights] == [False, False, False]
    with _app(app_dsn, client_id) as conn:
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("SELECT public.inbox_queue_backfill(%s::uuid)", (client_id,))


def test_sandbox_and_console_chat_conversations_stay_bot(migrated_dsn: str) -> None:
    client_id = _company(migrated_dsn)
    web = _seed(
        migrated_dsn,
        client_id,
        channel="web",
        owned_by="human_support",
        messages=(_customer(3600),),
        escalation=("high", 3600),
    )
    thread = _seed(
        migrated_dsn,
        client_id,
        owned_by="human_support",
        messages=(_customer(3600),),
        escalation=("high", 3600),
    )
    participant = _seed(
        migrated_dsn,
        client_id,
        owned_by="human_support",
        messages=(_customer(3600),),
        escalation=("high", 3600),
    )
    endpoint = str(uuid.uuid4())
    user_one, user_two = str(uuid.uuid4()), str(uuid.uuid4())
    with _owner(migrated_dsn) as conn:
        for user in (user_one, user_two):
            conn.execute(
                "INSERT INTO console_users (id, email, display_name) VALUES (%s, %s, 'Synthetic')",
                (user, f"{user}@example.test"),
            )
        for n, (ep, user, seed) in enumerate(
            ((endpoint, user_one, thread), (str(uuid.uuid4()), user_two, participant))
        ):
            credential = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO channel_credentials (id, client_id, channel)"
                " VALUES (%s, %s, 'webhook')",
                (credential, client_id),
            )
            conn.execute(
                "INSERT INTO webhook_endpoints (id, client_id, public_id, credential_id)"
                " VALUES (%s, %s, %s, %s)",
                (ep, client_id, f"endpoint-{uuid.uuid4().hex}", credential),
            )
            conn.execute(
                "INSERT INTO console_chat_participants"
                " (client_id, user_id, contact_id, endpoint_id)"
                " VALUES (%s, %s, %s, %s)",
                (client_id, user, seed.contact_id, ep),
            )
            if n == 0:
                conn.execute(
                    "INSERT INTO console_chat_threads (id, client_id, user_id, contact_id,"
                    " endpoint_id, request_key) VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        seed.conversation_id,
                        client_id,
                        user,
                        seed.contact_id,
                        ep,
                        str(uuid.uuid4()),
                    ),
                )
    _backfill(migrated_dsn)
    for conversation in (web, thread, participant):
        row = _row(migrated_dsn, conversation.conversation_id)
        assert row["queue_status"] == "bot"
        assert row["queue_reason"] is None
        assert row["needs_staff_since"] is None


def test_a_second_call_returns_zero_and_changes_nothing(migrated_dsn: str) -> None:
    client_id = _company(migrated_dsn)
    _seed(
        migrated_dsn,
        client_id,
        owned_by="human_support",
        messages=(_customer(3600),),
        escalation=("high", 3600),
    )
    _seed(migrated_dsn, client_id, messages=(_customer(DAY), _bot(DAY - 5)))
    _seed(migrated_dsn, client_id)
    assert _backfill(migrated_dsn) >= 3
    before = _snapshot(migrated_dsn)
    assert _backfill(migrated_dsn) == 0
    assert _snapshot(migrated_dsn) == before


def test_the_company_argument_limits_the_backfill(migrated_dsn: str) -> None:
    mine, other = _company(migrated_dsn), _company(migrated_dsn)
    wanted = _seed(migrated_dsn, mine, owned_by="human_support", messages=(_customer(3600),))
    left = _seed(migrated_dsn, other, owned_by="human_support", messages=(_customer(3600),))
    assert _backfill(migrated_dsn, mine) == 1
    assert _row(migrated_dsn, wanted.conversation_id)["queue_status"] == "needs_staff"
    assert _row(migrated_dsn, left.conversation_id)["queue_status"] == "bot"
    assert _backfill(migrated_dsn, other) == 1


def test_a_conversation_already_moved_on_is_not_touched(migrated_dsn: str) -> None:
    client_id = _company(migrated_dsn)
    seeded = _seed(migrated_dsn, client_id, owned_by="human_support", messages=(_customer(3600),))
    with _owner(migrated_dsn) as conn:
        conn.execute(
            "UPDATE conversations SET queue_status = 'resolved', resolved_at = now() WHERE id = %s",
            (seeded.conversation_id,),
        )
    _backfill(migrated_dsn)
    assert _row(migrated_dsn, seeded.conversation_id)["queue_status"] == "resolved"


def test_running_0620_again_changes_nothing(migrated_dsn: str) -> None:
    client_id = _company(migrated_dsn)
    seeded = _seed(migrated_dsn, client_id, owned_by="human_support", messages=(_customer(3600),))
    _backfill(migrated_dsn)
    before = _definitions(migrated_dsn)
    rows = _snapshot(migrated_dsn)
    [path] = [p for v, p in migrate.discover_migrations() if v == "0620"]
    with _owner(migrated_dsn) as conn:
        conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
    assert _definitions(migrated_dsn) == before
    assert _snapshot(migrated_dsn) == rows
    assert _row(migrated_dsn, seeded.conversation_id)["queue_status"] == "needs_staff"
