"""csa.inbox_queue: the queue transitions made by the code that knows who acted, and the company's
inbox settings (disposable database, synthetic data).

Every transition runs through the app role on a tenant transaction, as the console and the worker
call it, so row security and grants are part of every check. Seeding and reading back go through
the owner (helpers shared with tests/test_inbox_queue_triggers.py).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from csa.inbox_queue import settings as inbox_settings
from csa.inbox_queue import transitions as queue
from csa.inbox_queue.transitions import QueueError

from tests.conftest import requires_docker
from tests.test_bookings_schema import _owner
from tests.test_inbox_queue_triggers import (
    Seeded,
    _approval,
    _escalate,
    _events,
    _inbound,
    _needs_staff,
    _seed,
    _settings,
    _state,
    _user,
)
from tests.test_training_schema import _company

pytestmark = requires_docker

TAKEN_OVER = {
    "owned_by": "human_support",
}


async def _act[T](
    app_dsn: str, client_id: str, fn: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any
) -> T:
    """One transition on a tenant transaction of the app role, committed when it returns."""
    async with await psycopg.AsyncConnection.connect(app_dsn) as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.client_id', %s, true)", (client_id,))
            return await fn(conn, uuid.UUID(client_id), *args, **kwargs)


async def _on(app_dsn: str, s: Seeded, fn: Callable[..., Awaitable[Any]], **kwargs: Any) -> Any:
    return await _act(app_dsn, s.client_id, fn, uuid.UUID(s.conversation_id), **kwargs)


def _taken_over(dsn: str, client_id: str, **queue_columns: Any) -> Seeded:
    """A conversation a person took over: escalated status and a running mute, as /takeover."""
    return _seed(
        dsn,
        client_id,
        owned_by="human_support",
        status="escalated",
        ai_muted_until=datetime.now(UTC) + timedelta(days=1),
        **queue_columns,
    )


def _conversation(dsn: str, s: Seeded) -> dict[str, Any]:
    with _owner(dsn) as conn:
        row = conn.execute(
            "SELECT owned_by, status, ai_muted_until, ai_suppressed_before, topic, inbox_version"
            " FROM conversations WHERE id = %s",
            (s.conversation_id,),
        ).fetchone()
    assert row is not None
    return row


def _ticket(dsn: str, s: Seeded, status: str = "open", category: str = "escalation") -> int:
    with _owner(dsn) as conn:
        row = conn.execute(
            "INSERT INTO tickets (client_id, conversation_id, contact_id, subject, category,"
            " status) VALUES (%s, %s, %s, 'Synthetic subject', %s, %s) RETURNING id",
            (s.client_id, s.conversation_id, s.contact_id, category, status),
        ).fetchone()
    assert row is not None
    return row["id"]


def _tickets(dsn: str, s: Seeded) -> list[dict[str, Any]]:
    with _owner(dsn) as conn:
        return conn.execute(
            "SELECT t.id, t.status, t.closed_at,"
            " (SELECT count(*) FROM ticket_events e WHERE e.ticket_id = t.id) AS events"
            " FROM tickets t WHERE t.conversation_id = %s ORDER BY t.id",
            (s.conversation_id,),
        ).fetchall()


def _ticket_events(dsn: str, ticket_id: int) -> list[dict[str, Any]]:
    with _owner(dsn) as conn:
        return conn.execute(
            "SELECT actor, event_type, old_value, new_value FROM ticket_events"
            " WHERE ticket_id = %s ORDER BY id",
            (ticket_id,),
        ).fetchall()


def _escalations(dsn: str, s: Seeded) -> list[str]:
    with _owner(dsn) as conn:
        rows = conn.execute(
            "SELECT status FROM escalations WHERE conversation_id = %s ORDER BY created_at",
            (s.conversation_id,),
        ).fetchall()
    return [r["status"] for r in rows]


def _participant(dsn: str, client_id: str) -> Seeded:
    """A conversation of a console chat participant (outside the queue for good)."""
    s = _seed(dsn, client_id, owned_by="human_support")
    user = _user(dsn)
    credential, endpoint = str(uuid.uuid4()), str(uuid.uuid4())
    with _owner(dsn) as conn:
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
            (client_id, user, s.contact_id, endpoint),
        )
    return s


def _uid(value: str) -> uuid.UUID:
    return uuid.UUID(value)


# --- enter and replied ----------------------------------------------------------------------------


async def test_enter_then_replied_waits_on_the_customer_and_claims_for_the_author(
    migrated_dsn: str, app_dsn: str
) -> None:
    c = _company(migrated_dsn)
    user = _user(migrated_dsn)
    s = _taken_over(migrated_dsn, c)
    version = _conversation(migrated_dsn, s)["inbox_version"]

    assert await _on(app_dsn, s, queue.enter, reason="manual") is True
    row = _state(migrated_dsn, s)
    assert row["queue_status"] == "needs_staff" and row["queue_reason"] == "manual"
    assert row["due_at"] - row["needs_staff_since"] == timedelta(minutes=240)
    assert row["inbox_version"] == version

    await _on(app_dsn, s, queue.replied, user_id=_uid(user))
    row = _state(migrated_dsn, s)
    assert row["queue_status"] == "waiting_customer"
    assert row["queue_reason"] is None
    assert row["needs_staff_since"] is None and row["due_at"] is None
    assert row["assignee_user_id"] == _uid(user) and row["claimed_at"] is not None
    assert row["last_staff_reply_at"] is not None
    # The reply route bumps the version itself; the queue change adds nothing.
    assert row["inbox_version"] == version

    entered, replied = _events(migrated_dsn, s)
    assert entered["action"] == "entered"
    assert replied == {
        "client_id": _uid(c),
        "conversation_id": _uid(s.conversation_id),
        "actor_user_id": _uid(user),
        "action": "replied",
        "from_status": "needs_staff",
        "to_status": "waiting_customer",
        "details": {"assignee_user_id": user},
    }


async def test_enter_claims_for_the_person_who_took_over(migrated_dsn: str, app_dsn: str) -> None:
    c = _company(migrated_dsn)
    user = _user(migrated_dsn)
    s = _taken_over(migrated_dsn, c)
    changed = await _on(
        app_dsn,
        s,
        queue.enter,
        reason="manual",
        priority="high",
        actor_user_id=_uid(user),
        claim_for=_uid(user),
    )
    assert changed is True
    row = _state(migrated_dsn, s)
    assert row["assignee_user_id"] == _uid(user) and row["priority"] == "high"
    assert row["due_at"] - row["needs_staff_since"] == timedelta(minutes=60)
    [event] = _events(migrated_dsn, s)
    assert event["actor_user_id"] == _uid(user) and event["action"] == "entered"


async def test_replied_without_a_user_does_not_claim(migrated_dsn: str, app_dsn: str) -> None:
    c = _company(migrated_dsn)
    s = _taken_over(migrated_dsn, c, **_needs_staff("held_reply"))
    await _on(app_dsn, s, queue.replied, user_id=None)
    row = _state(migrated_dsn, s)
    assert row["queue_status"] == "waiting_customer"
    assert row["assignee_user_id"] is None and row["claimed_at"] is None
    [event] = _events(migrated_dsn, s)
    assert event["actor_user_id"] is None and event["details"] == {}


async def test_replied_keeps_someone_elses_claim(migrated_dsn: str, app_dsn: str) -> None:
    c = _company(migrated_dsn)
    holder, author = _user(migrated_dsn), _user(migrated_dsn)
    claimed_at = datetime.now(UTC) - timedelta(hours=1)
    s = _taken_over(
        migrated_dsn,
        c,
        **_needs_staff("manual", assignee_user_id=holder, claimed_at=claimed_at),
    )
    await _on(app_dsn, s, queue.replied, user_id=_uid(author))
    row = _state(migrated_dsn, s)
    assert row["assignee_user_id"] == _uid(holder) and row["claimed_at"] == claimed_at
    [event] = _events(migrated_dsn, s)
    assert event["actor_user_id"] == _uid(author) and event["details"] == {}


async def test_replied_on_a_bot_conversation_waits_on_the_customer(
    migrated_dsn: str, app_dsn: str
) -> None:
    c = _company(migrated_dsn)
    user = _user(migrated_dsn)
    s = _taken_over(migrated_dsn, c)
    await _on(app_dsn, s, queue.replied, user_id=_uid(user))
    row = _state(migrated_dsn, s)
    assert row["queue_status"] == "waiting_customer"
    assert row["assignee_user_id"] == _uid(user)
    [event] = _events(migrated_dsn, s)
    assert (event["from_status"], event["to_status"]) == ("bot", "waiting_customer")


# --- held_reply_closed ----------------------------------------------------------------------------


async def test_a_closed_held_reply_becomes_a_customer_message(
    migrated_dsn: str, app_dsn: str
) -> None:
    c = _company(migrated_dsn)
    s = _seed(migrated_dsn, c, **_needs_staff("held_reply", "high"))
    before = _state(migrated_dsn, s)
    await _on(app_dsn, s, queue.held_reply_closed)
    row = _state(migrated_dsn, s)
    assert row["queue_status"] == "needs_staff"
    assert row["queue_reason"] == "customer_message"
    # The clock and due time stay: the customer has waited all along.
    assert row["needs_staff_since"] == before["needs_staff_since"]
    assert row["due_at"] == before["due_at"]
    assert row["inbox_version"] == before["inbox_version"]
    [event] = _events(migrated_dsn, s)
    assert event["action"] == "reason_changed" and event["actor_user_id"] is None
    assert (event["from_status"], event["to_status"]) == ("needs_staff", "needs_staff")
    assert event["details"] == {
        "reason": "customer_message",
        "previous_reason": "held_reply",
        "priority": "high",
    }


@pytest.mark.parametrize("reason", ["escalation", "manual", "customer_message"])
async def test_a_closed_held_reply_keeps_any_other_reason(
    migrated_dsn: str, app_dsn: str, reason: str
) -> None:
    c = _company(migrated_dsn)
    s = _seed(migrated_dsn, c, **_needs_staff(reason))
    before = _state(migrated_dsn, s)
    await _on(app_dsn, s, queue.held_reply_closed)
    assert _state(migrated_dsn, s) == before
    assert _events(migrated_dsn, s) == []


async def test_a_closed_held_reply_keeps_the_reason_while_another_is_pending(
    migrated_dsn: str, app_dsn: str
) -> None:
    c = _company(migrated_dsn)
    s = _seed(migrated_dsn, c, **_needs_staff("held_reply"))
    _approval(migrated_dsn, s)
    await _on(app_dsn, s, queue.held_reply_closed)
    assert _state(migrated_dsn, s)["queue_reason"] == "held_reply"
    assert _events(migrated_dsn, s) == []


# --- hand_back and resolve ------------------------------------------------------------------------


def _escalated_with_ticket(dsn: str, app_dsn: str, holder: str) -> tuple[Seeded, int]:
    c = _company(dsn)
    s = _taken_over(dsn, c)
    assert _escalate(app_dsn, s, "high")
    assert _escalate(app_dsn, s, "normal")
    with _owner(dsn) as conn:
        conn.execute(
            "UPDATE conversations SET assignee_user_id = %s, claimed_at = now() WHERE id = %s",
            (holder, s.conversation_id),
        )
    ticket = _ticket(dsn, s)
    _ticket(dsn, s, status="closed")
    return s, ticket


async def test_hand_back_returns_the_conversation_to_the_bot(
    migrated_dsn: str, app_dsn: str
) -> None:
    user = _user(migrated_dsn)
    s, ticket = _escalated_with_ticket(migrated_dsn, app_dsn, user)
    before = _conversation(migrated_dsn, s)
    entered = len(_events(migrated_dsn, s))

    assert await _on(app_dsn, s, queue.hand_back, actor_user_id=_uid(user)) is True

    row = _state(migrated_dsn, s)
    assert row["queue_status"] == "bot"
    for cleared in (
        "queue_reason",
        "needs_staff_since",
        "due_at",
        "assignee_user_id",
        "claimed_at",
        "snoozed_until",
        "resolved_at",
    ):
        assert row[cleared] is None, cleared
    conv = _conversation(migrated_dsn, s)
    assert conv["owned_by"] == "ai" and conv["status"] == "open"
    assert conv["ai_muted_until"] is None and conv["ai_suppressed_before"] is not None
    assert before["ai_suppressed_before"] is None
    assert conv["inbox_version"] == before["inbox_version"] + 1
    assert _escalations(migrated_dsn, s) == ["resolved", "resolved"]
    open_ticket, closed_ticket = _tickets(migrated_dsn, s)
    assert open_ticket["id"] == ticket
    assert open_ticket["status"] == "resolved" and open_ticket["closed_at"] is not None
    assert open_ticket["events"] == 1
    assert closed_ticket["status"] == "closed" and closed_ticket["events"] == 0
    assert _ticket_events(migrated_dsn, ticket) == [
        {
            "actor": "staff",
            "event_type": "resolved",
            "old_value": {"status": "open"},
            "new_value": {"status": "resolved", "actor_user_id": user},
        }
    ]
    events = _events(migrated_dsn, s)
    assert len(events) == entered + 1
    assert events[-1] == {
        "client_id": _uid(s.client_id),
        "conversation_id": _uid(s.conversation_id),
        "actor_user_id": _uid(user),
        "action": "handed_back",
        "from_status": "needs_staff",
        "to_status": "bot",
        "details": {"ticket_ids": [ticket], "escalations_resolved": 2},
    }


async def test_hand_back_by_no_person_and_without_a_ticket(migrated_dsn: str, app_dsn: str) -> None:
    c = _company(migrated_dsn)
    s = _taken_over(migrated_dsn, c, **_needs_staff("manual"))
    await _on(app_dsn, s, queue.hand_back, actor_user_id=None)
    assert _state(migrated_dsn, s)["queue_status"] == "bot"
    assert _conversation(migrated_dsn, s)["owned_by"] == "ai"
    [event] = _events(migrated_dsn, s)
    assert event["action"] == "handed_back" and event["actor_user_id"] is None
    assert event["details"] == {"ticket_ids": [], "escalations_resolved": 0}


async def test_hand_back_keeps_an_earlier_resolved_time(migrated_dsn: str, app_dsn: str) -> None:
    c = _company(migrated_dsn)
    earlier = datetime.now(UTC) - timedelta(days=2)
    s = _taken_over(migrated_dsn, c, queue_status="waiting_customer", resolved_at=earlier)
    await _on(app_dsn, s, queue.hand_back, actor_user_id=None)
    assert _state(migrated_dsn, s)["resolved_at"] == earlier


def _ai_owned(dsn: str, client_id: str, **queue_columns: Any) -> Seeded:
    """A conversation the AI owns, with a known ai_suppressed_before (no person holds the chat)."""
    return _seed(
        dsn,
        client_id,
        owned_by="ai",
        ai_suppressed_before=datetime.now(UTC) - timedelta(days=1),
        **queue_columns,
    )


async def test_hand_back_of_an_ai_owned_item_leaves_the_release_columns(
    migrated_dsn: str, app_dsn: str
) -> None:
    c = _company(migrated_dsn)
    user = _user(migrated_dsn)
    s = _ai_owned(
        migrated_dsn,
        c,
        **_needs_staff("customer_message", assignee_user_id=user, claimed_at=datetime.now(UTC)),
    )
    before = _conversation(migrated_dsn, s)

    assert await _on(app_dsn, s, queue.hand_back, actor_user_id=_uid(user)) is True

    row = _state(migrated_dsn, s)
    assert row["queue_status"] == "bot" and row["assignee_user_id"] is None
    conv = _conversation(migrated_dsn, s)
    for kept in ("owned_by", "status", "ai_muted_until", "ai_suppressed_before"):
        assert conv[kept] == before[kept], kept
    assert conv["inbox_version"] == before["inbox_version"] + 1
    [event] = _events(migrated_dsn, s)
    assert event["action"] == "handed_back" and event["actor_user_id"] == _uid(user)
    assert (event["from_status"], event["to_status"]) == ("needs_staff", "bot")

    # Pressed again: nothing left to do, so no event and no bump (the route's already_handled).
    assert await _on(app_dsn, s, queue.hand_back, actor_user_id=_uid(user)) is False
    assert await _on(app_dsn, s, queue.hand_back, actor_user_id=None) is False
    assert _conversation(migrated_dsn, s) == conv
    assert len(_events(migrated_dsn, s)) == 1


async def test_hand_back_of_an_ai_owned_bot_item_still_closes_its_ticket(
    migrated_dsn: str, app_dsn: str
) -> None:
    c = _company(migrated_dsn)
    s = _ai_owned(migrated_dsn, c)
    ticket = _ticket(migrated_dsn, s)
    before = _conversation(migrated_dsn, s)
    assert await _on(app_dsn, s, queue.hand_back, actor_user_id=None) is True
    assert _tickets(migrated_dsn, s)[0]["status"] == "resolved"
    conv = _conversation(migrated_dsn, s)
    assert conv["ai_suppressed_before"] == before["ai_suppressed_before"]
    assert conv["inbox_version"] == before["inbox_version"] + 1
    [event] = _events(migrated_dsn, s)
    assert (event["from_status"], event["to_status"]) == ("bot", "bot")
    assert event["details"] == {"ticket_ids": [ticket], "escalations_resolved": 0}


async def test_resolve_of_an_ai_owned_item_leaves_the_release_columns(
    migrated_dsn: str, app_dsn: str
) -> None:
    c = _company(migrated_dsn)
    user = _user(migrated_dsn)
    s = _ai_owned(migrated_dsn, c, **_needs_staff("customer_message"))
    before = _conversation(migrated_dsn, s)

    resolved = await _on(app_dsn, s, queue.resolve, actor_user_id=_uid(user), seen_customer_at=None)
    assert resolved is True
    row = _state(migrated_dsn, s)
    assert row["queue_status"] == "resolved" and row["resolved_at"] is not None
    conv = _conversation(migrated_dsn, s)
    for kept in ("owned_by", "status", "ai_muted_until", "ai_suppressed_before"):
        assert conv[kept] == before[kept], kept
    assert conv["inbox_version"] == before["inbox_version"] + 1
    [event] = _events(migrated_dsn, s)
    assert event["action"] == "resolved"
    assert (event["from_status"], event["to_status"]) == ("needs_staff", "resolved")

    # Resolving a resolved item the AI owns again changes nothing, resolved_at included.
    again = await _on(app_dsn, s, queue.resolve, actor_user_id=_uid(user), seen_customer_at=None)
    assert again is False
    assert _state(migrated_dsn, s) == row
    assert _conversation(migrated_dsn, s) == conv
    assert len(_events(migrated_dsn, s)) == 1


async def test_resolve_leaves_resolved_and_hands_back_to_the_bot(
    migrated_dsn: str, app_dsn: str
) -> None:
    user = _user(migrated_dsn)
    s, ticket = _escalated_with_ticket(migrated_dsn, app_dsn, user)
    before = _conversation(migrated_dsn, s)

    await _on(app_dsn, s, queue.resolve, actor_user_id=_uid(user), seen_customer_at=None)

    row = _state(migrated_dsn, s)
    assert row["queue_status"] == "resolved" and row["resolved_at"] is not None
    for cleared in ("queue_reason", "needs_staff_since", "due_at", "assignee_user_id"):
        assert row[cleared] is None, cleared
    assert row["claimed_at"] is None and row["snoozed_until"] is None
    conv = _conversation(migrated_dsn, s)
    assert conv["owned_by"] == "ai" and conv["status"] == "open"
    assert conv["inbox_version"] == before["inbox_version"] + 1
    assert _escalations(migrated_dsn, s) == ["resolved", "resolved"]
    assert _tickets(migrated_dsn, s)[0]["status"] == "resolved"
    event = _events(migrated_dsn, s)[-1]
    assert event["action"] == "resolved" and event["actor_user_id"] == _uid(user)
    assert (event["from_status"], event["to_status"]) == ("needs_staff", "resolved")
    assert event["details"] == {"ticket_ids": [ticket], "escalations_resolved": 2}

    # The bot has it: the customer's next message moves the item to bot (0622 trigger).
    _inbound(app_dsn, s)
    assert _state(migrated_dsn, s)["queue_status"] == "bot"


async def test_resolve_is_refused_while_a_held_reply_is_pending(
    migrated_dsn: str, app_dsn: str
) -> None:
    c = _company(migrated_dsn)
    user = _user(migrated_dsn)
    s = _taken_over(migrated_dsn, c, **_needs_staff("held_reply"))
    _approval(migrated_dsn, s)
    before, conv = _state(migrated_dsn, s), _conversation(migrated_dsn, s)
    with pytest.raises(QueueError) as refused:
        await _on(app_dsn, s, queue.resolve, actor_user_id=_uid(user), seen_customer_at=None)
    assert refused.value.code == "held_reply_pending"
    assert _state(migrated_dsn, s) == before
    assert _conversation(migrated_dsn, s) == conv
    assert _events(migrated_dsn, s) == []


async def test_an_expired_pending_approval_does_not_block_resolve(
    migrated_dsn: str, app_dsn: str
) -> None:
    c = _company(migrated_dsn)
    user = _user(migrated_dsn)
    s = _taken_over(migrated_dsn, c, **_needs_staff("held_reply"))
    _approval(migrated_dsn, s, expires_in=-60)
    await _on(app_dsn, s, queue.resolve, actor_user_id=_uid(user), seen_customer_at=None)
    assert _state(migrated_dsn, s)["queue_status"] == "resolved"


async def test_resolve_is_refused_when_the_customer_wrote_since_the_person_looked(
    migrated_dsn: str, app_dsn: str
) -> None:
    c = _company(migrated_dsn)
    user = _user(migrated_dsn)
    s = _taken_over(migrated_dsn, c)
    _inbound(app_dsn, s)
    before, conv = _state(migrated_dsn, s), _conversation(migrated_dsn, s)
    last = before["last_customer_at"]
    assert before["queue_status"] == "bot" and last is not None
    with pytest.raises(QueueError) as refused:
        await _on(
            app_dsn,
            s,
            queue.resolve,
            actor_user_id=_uid(user),
            seen_customer_at=last - timedelta(microseconds=1),
        )
    assert refused.value.code == "customer_wrote"
    assert _state(migrated_dsn, s) == before
    assert _conversation(migrated_dsn, s) == conv
    assert _events(migrated_dsn, s) == []

    await _on(app_dsn, s, queue.resolve, actor_user_id=_uid(user), seen_customer_at=last)
    assert _state(migrated_dsn, s)["queue_status"] == "resolved"


async def test_resolve_without_a_seen_time_does_not_check_the_customer(
    migrated_dsn: str, app_dsn: str
) -> None:
    """None means the caller has no information: no customer check (documented)."""
    c = _company(migrated_dsn)
    user = _user(migrated_dsn)
    s = _taken_over(migrated_dsn, c)
    _inbound(app_dsn, s)
    await _on(app_dsn, s, queue.resolve, actor_user_id=_uid(user), seen_customer_at=None)
    assert _state(migrated_dsn, s)["queue_status"] == "resolved"


# --- snooze and wake ------------------------------------------------------------------------------


async def test_snooze_refuses_a_time_not_in_the_future(migrated_dsn: str, app_dsn: str) -> None:
    c = _company(migrated_dsn)
    user = _user(migrated_dsn)
    s = _taken_over(migrated_dsn, c, **_needs_staff("manual"))
    before = _state(migrated_dsn, s)
    with pytest.raises(QueueError) as refused:
        await _on(
            app_dsn,
            s,
            queue.snooze,
            actor_user_id=_uid(user),
            until=datetime.now(UTC) - timedelta(seconds=1),
            seen_customer_at=None,
        )
    assert refused.value.code == "until_in_past"
    assert _state(migrated_dsn, s) == before
    assert _events(migrated_dsn, s) == []


async def test_snooze_has_the_refusals_of_resolve(migrated_dsn: str, app_dsn: str) -> None:
    c = _company(migrated_dsn)
    user = _user(migrated_dsn)
    held = _taken_over(migrated_dsn, c, **_needs_staff("held_reply"))
    _approval(migrated_dsn, held)
    wrote = _taken_over(migrated_dsn, c)
    _inbound(app_dsn, wrote)
    seen = _state(migrated_dsn, wrote)["last_customer_at"] - timedelta(microseconds=1)
    until = datetime.now(UTC) + timedelta(hours=1)
    for s, seen_at, code in ((held, None, "held_reply_pending"), (wrote, seen, "customer_wrote")):
        before = _state(migrated_dsn, s)
        with pytest.raises(QueueError) as refused:
            await _on(
                app_dsn,
                s,
                queue.snooze,
                actor_user_id=_uid(user),
                until=until,
                seen_customer_at=seen_at,
            )
        assert refused.value.code == code
        assert _state(migrated_dsn, s) == before


async def test_snooze_hides_the_item_and_wake_returns_it_with_a_fresh_clock(
    migrated_dsn: str, app_dsn: str
) -> None:
    c = _company(migrated_dsn)
    user = _user(migrated_dsn)
    s = _taken_over(
        migrated_dsn,
        c,
        **_needs_staff(
            "escalation",
            "high",
            minutes_ago=600,
            assignee_user_id=user,
            claimed_at=datetime.now(UTC),
        ),
    )
    before = _state(migrated_dsn, s)
    until = datetime.now(UTC) + timedelta(hours=2)
    await _on(
        app_dsn, s, queue.snooze, actor_user_id=_uid(user), until=until, seen_customer_at=None
    )
    row = _state(migrated_dsn, s)
    assert row["queue_status"] == "snoozed" and row["snoozed_until"] == until
    assert row["queue_reason"] is None and row["needs_staff_since"] is None
    assert row["due_at"] is None
    assert row["assignee_user_id"] == _uid(user)
    assert row["inbox_version"] == before["inbox_version"] + 1
    [snoozed] = _events(migrated_dsn, s)
    assert snoozed["action"] == "snoozed" and snoozed["actor_user_id"] == _uid(user)
    assert (snoozed["from_status"], snoozed["to_status"]) == ("needs_staff", "snoozed")

    # Not yet time: wake leaves it.
    assert await _act(app_dsn, c, queue.wake_snoozed) == 0
    assert _state(migrated_dsn, s)["queue_status"] == "snoozed"

    with _owner(migrated_dsn) as conn:
        conn.execute(
            "UPDATE conversations SET snoozed_until = now() - interval '1 minute' WHERE id = %s",
            (s.conversation_id,),
        )
    assert await _act(app_dsn, c, queue.wake_snoozed) == 1
    row = _state(migrated_dsn, s)
    assert row["queue_status"] == "needs_staff"
    assert row["queue_reason"] == "manual"
    assert row["needs_staff_since"] > before["needs_staff_since"] + timedelta(minutes=500)
    assert row["due_at"] - row["needs_staff_since"] == timedelta(minutes=60)
    assert row["snoozed_until"] is None and row["priority"] == "high"
    assert row["assignee_user_id"] == _uid(user)
    # A system move: the version is the staff UI's counter and stays.
    assert row["inbox_version"] == before["inbox_version"] + 1
    woke = _events(migrated_dsn, s)[-1]
    assert woke["action"] == "woke" and woke["actor_user_id"] is None
    assert (woke["from_status"], woke["to_status"]) == ("snoozed", "needs_staff")
    assert woke["details"] == {"reason": "manual", "priority": "high"}

    assert await _act(app_dsn, c, queue.wake_snoozed) == 0


async def test_wake_uses_customer_message_when_the_customer_wrote_last(
    migrated_dsn: str, app_dsn: str
) -> None:
    c = _company(migrated_dsn)
    _settings(migrated_dsn, c, target_normal_min=90)
    past = datetime.now(UTC) - timedelta(minutes=5)
    wrote = _taken_over(
        migrated_dsn,
        c,
        queue_status="snoozed",
        snoozed_until=past,
        last_customer_at=past - timedelta(hours=1),
        last_staff_reply_at=past - timedelta(hours=2),
    )
    answered = _taken_over(
        migrated_dsn,
        c,
        queue_status="snoozed",
        snoozed_until=past,
        last_customer_at=past - timedelta(hours=2),
        last_staff_reply_at=past - timedelta(hours=1),
    )
    other_company = _taken_over(
        migrated_dsn, _company(migrated_dsn), queue_status="snoozed", snoozed_until=past
    )
    assert await _act(app_dsn, c, queue.wake_snoozed) == 2
    assert _state(migrated_dsn, wrote)["queue_reason"] == "customer_message"
    row = _state(migrated_dsn, answered)
    assert row["queue_reason"] == "manual"
    assert row["due_at"] - row["needs_staff_since"] == timedelta(minutes=90)
    assert _state(migrated_dsn, other_company)["queue_status"] == "snoozed"


async def test_wake_takes_at_most_the_limit(migrated_dsn: str, app_dsn: str) -> None:
    c = _company(migrated_dsn)
    past = datetime.now(UTC) - timedelta(minutes=5)
    items = [
        _taken_over(migrated_dsn, c, queue_status="snoozed", snoozed_until=past) for _ in range(3)
    ]
    assert await _act(app_dsn, c, queue.wake_snoozed, limit=2) == 2
    assert await _act(app_dsn, c, queue.wake_snoozed, limit=2) == 1
    assert all(_state(migrated_dsn, s)["queue_status"] == "needs_staff" for s in items)


# --- claim, unclaim, assign, priority, topic ------------------------------------------------------


async def test_claim_takes_an_unclaimed_item_and_records_the_previous_holder(
    migrated_dsn: str, app_dsn: str
) -> None:
    c = _company(migrated_dsn)
    first, second = _user(migrated_dsn), _user(migrated_dsn)
    s = _taken_over(migrated_dsn, c, **_needs_staff("manual"))
    version = _state(migrated_dsn, s)["inbox_version"]

    await _on(app_dsn, s, queue.claim, user_id=_uid(first))
    row = _state(migrated_dsn, s)
    assert row["assignee_user_id"] == _uid(first) and row["claimed_at"] is not None
    assert row["inbox_version"] == version + 1

    await _on(app_dsn, s, queue.claim, user_id=_uid(second))
    row = _state(migrated_dsn, s)
    assert row["assignee_user_id"] == _uid(second)
    assert row["inbox_version"] == version + 2
    assert row["queue_status"] == "needs_staff"

    # The holder claiming again changes nothing.
    await _on(app_dsn, s, queue.claim, user_id=_uid(second))
    assert _state(migrated_dsn, s)["inbox_version"] == version + 2

    one, two = _events(migrated_dsn, s)
    assert one["action"] == "claimed" and one["actor_user_id"] == _uid(first)
    assert one["details"] == {"assignee_user_id": first}
    assert two["actor_user_id"] == _uid(second)
    assert two["details"] == {"assignee_user_id": second, "previous_assignee_user_id": first}
    assert (two["from_status"], two["to_status"]) == ("needs_staff", "needs_staff")


async def test_unclaim_and_assign_change_only_the_assignee(migrated_dsn: str, app_dsn: str) -> None:
    c = _company(migrated_dsn)
    holder, admin, member = _user(migrated_dsn), _user(migrated_dsn), _user(migrated_dsn)
    s = _taken_over(
        migrated_dsn,
        c,
        **_needs_staff(
            "escalation", "urgent", assignee_user_id=holder, claimed_at=datetime.now(UTC)
        ),
    )
    before = _state(migrated_dsn, s)

    await _on(app_dsn, s, queue.unclaim, actor_user_id=_uid(holder))
    row = _state(migrated_dsn, s)
    assert row["assignee_user_id"] is None and row["claimed_at"] is None
    assert row["inbox_version"] == before["inbox_version"] + 1
    unchanged = ("queue_status", "queue_reason", "priority", "needs_staff_since", "due_at")
    assert {k: row[k] for k in unchanged} == {k: before[k] for k in unchanged}

    await _on(app_dsn, s, queue.assign, assignee_id=_uid(member), actor_user_id=_uid(admin))
    row = _state(migrated_dsn, s)
    assert row["assignee_user_id"] == _uid(member) and row["claimed_at"] is not None
    assert row["inbox_version"] == before["inbox_version"] + 2
    assert {k: row[k] for k in unchanged} == {k: before[k] for k in unchanged}

    unclaimed, assigned = _events(migrated_dsn, s)
    assert unclaimed["action"] == "unclaimed" and unclaimed["actor_user_id"] == _uid(holder)
    assert unclaimed["details"] == {"previous_assignee_user_id": holder}
    assert assigned["action"] == "assigned" and assigned["actor_user_id"] == _uid(admin)
    assert assigned["details"] == {"assignee_user_id": member}

    # A second release finds the item unclaimed and changes nothing.
    await _on(app_dsn, s, queue.unclaim, actor_user_id=_uid(holder))
    await _on(app_dsn, s, queue.unclaim, actor_user_id=_uid(holder))
    assert len(_events(migrated_dsn, s)) == 3


async def test_set_priority_recomputes_the_due_time_from_the_clock(
    migrated_dsn: str, app_dsn: str
) -> None:
    c = _company(migrated_dsn)
    user = _user(migrated_dsn)
    _settings(migrated_dsn, c, target_urgent_min=10)
    s = _taken_over(migrated_dsn, c, **_needs_staff("manual", minutes_ago=45))
    before = _state(migrated_dsn, s)
    await _on(app_dsn, s, queue.set_priority, priority="urgent", actor_user_id=_uid(user))
    row = _state(migrated_dsn, s)
    assert row["priority"] == "urgent"
    assert row["needs_staff_since"] == before["needs_staff_since"]
    assert row["due_at"] == before["needs_staff_since"] + timedelta(minutes=10)
    assert row["inbox_version"] == before["inbox_version"] + 1
    [event] = _events(migrated_dsn, s)
    assert event["action"] == "priority_set" and event["actor_user_id"] == _uid(user)
    assert event["details"] == {"priority": "urgent", "previous_priority": "normal"}


async def test_set_priority_outside_needs_staff_leaves_the_due_time(
    migrated_dsn: str, app_dsn: str
) -> None:
    c = _company(migrated_dsn)
    user = _user(migrated_dsn)
    s = _taken_over(migrated_dsn, c, queue_status="waiting_customer")
    await _on(app_dsn, s, queue.set_priority, priority="low", actor_user_id=_uid(user))
    row = _state(migrated_dsn, s)
    assert row["priority"] == "low" and row["due_at"] is None
    with pytest.raises(ValueError):
        await _on(app_dsn, s, queue.set_priority, priority="whenever", actor_user_id=_uid(user))


async def test_set_topic(migrated_dsn: str, app_dsn: str) -> None:
    c = _company(migrated_dsn)
    user = _user(migrated_dsn)
    s = _taken_over(migrated_dsn, c, **_needs_staff("manual"))
    before = _conversation(migrated_dsn, s)
    await _on(app_dsn, s, queue.set_topic, topic="complaint", actor_user_id=_uid(user))
    conv = _conversation(migrated_dsn, s)
    assert conv["topic"] == "complaint"
    assert conv["inbox_version"] == before["inbox_version"] + 1
    [event] = _events(migrated_dsn, s)
    assert event["action"] == "topic_set"
    assert event["details"] == {"topic": "complaint"}
    await _on(app_dsn, s, queue.set_topic, topic="complaint", actor_user_id=_uid(user))
    assert len(_events(migrated_dsn, s)) == 1
    with pytest.raises(ValueError):
        await _on(app_dsn, s, queue.set_topic, topic="gossip", actor_user_id=_uid(user))


# --- outside the queue ----------------------------------------------------------------------------


async def test_sandbox_and_console_chat_conversations_never_leave_bot(
    migrated_dsn: str, app_dsn: str
) -> None:
    c = _company(migrated_dsn)
    user = _uid(_user(migrated_dsn))
    web = _seed(migrated_dsn, c, channel="web", owned_by="human_support")
    participant = _participant(migrated_dsn, c)
    future = datetime.now(UTC) + timedelta(hours=1)
    staff_actions: list[tuple[Callable[..., Awaitable[Any]], dict[str, Any]]] = [
        (queue.hand_back, {"actor_user_id": user}),
        (queue.resolve, {"actor_user_id": user, "seen_customer_at": None}),
        (queue.snooze, {"actor_user_id": user, "until": future, "seen_customer_at": None}),
        (queue.claim, {"user_id": user}),
        (queue.unclaim, {"actor_user_id": user}),
        (queue.assign, {"assignee_id": user, "actor_user_id": user}),
        (queue.set_priority, {"priority": "high", "actor_user_id": user}),
        (queue.set_topic, {"topic": "other", "actor_user_id": user}),
    ]
    for s in (web, participant):
        before, conv = _state(migrated_dsn, s), _conversation(migrated_dsn, s)
        assert await _on(app_dsn, s, queue.enter, reason="manual", claim_for=user) is False
        await _on(app_dsn, s, queue.replied, user_id=user)
        await _on(app_dsn, s, queue.held_reply_closed)
        for fn, kwargs in staff_actions:
            with pytest.raises(QueueError) as refused:
                await _on(app_dsn, s, fn, **kwargs)
            assert refused.value.code == "not_in_queue", fn.__name__
        assert _state(migrated_dsn, s) == before
        assert _conversation(migrated_dsn, s) == conv
        assert _events(migrated_dsn, s) == []


async def test_another_companys_conversation_is_not_in_queue(
    migrated_dsn: str, app_dsn: str
) -> None:
    mine, theirs = _company(migrated_dsn), _company(migrated_dsn)
    user = _uid(_user(migrated_dsn))
    s = _taken_over(migrated_dsn, theirs, **_needs_staff("manual"))
    before = _state(migrated_dsn, s)
    conversation_id = _uid(s.conversation_id)
    for fn, kwargs in (
        (queue.claim, {"user_id": user}),
        (queue.replied, {"user_id": user}),
        (queue.enter, {"reason": "manual"}),
    ):
        with pytest.raises(QueueError) as refused:
            await _act(app_dsn, mine, fn, conversation_id, **kwargs)
        assert refused.value.code == "not_in_queue"
    assert _state(migrated_dsn, s) == before


# --- settings -------------------------------------------------------------------------------------


async def test_settings_load_defaults_without_a_row_and_the_row_with_one(
    migrated_dsn: str, app_dsn: str
) -> None:
    c = _company(migrated_dsn)
    assert await _act(app_dsn, c, inbox_settings.load) == inbox_settings.InboxSettings()
    _settings(migrated_dsn, c, target_high_min=45, max_repeats=0, follow_up_hours=0)
    loaded = await _act(app_dsn, c, inbox_settings.load)
    assert loaded == inbox_settings.InboxSettings(
        target_high_min=45, max_repeats=0, follow_up_hours=0
    )
    assert loaded.target_minutes("high") == 45
    assert loaded.target_minutes("urgent") == 15


def test_settings_defaults_equal_the_column_defaults(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        rows = conn.execute(
            "SELECT column_name, column_default FROM information_schema.columns"
            " WHERE table_schema = 'public' AND table_name = 'inbox_settings'"
            " AND column_name NOT IN ('client_id', 'version', 'updated_at')"
        ).fetchall()
    columns = {r["column_name"]: int(r["column_default"]) for r in rows}
    assert columns == inbox_settings.InboxSettings().model_dump()
    assert len(columns) == 9
