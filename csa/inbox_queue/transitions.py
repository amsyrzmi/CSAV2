"""Queue transitions of a customer conversation (design 2026-11-16, sections 4 and 5.2).

States (``conversations.queue_status``):

- ``bot``: the bot handles it; not in the queue. Sandbox (``web``) and console chat conversations
  stay here for good: :func:`enter`, :func:`replied` and :func:`held_reply_closed` do nothing for
  them and every staff action raises ``QueueError("not_in_queue")``.
- ``needs_staff``: a person has to act. Only :func:`enter` (``public.inbox_queue_enter``, 0622, also
  used by the message and escalation triggers), :func:`wake_snoozed` and :func:`wake_one` put an
  item here; a needs_staff row always has a reason, ``needs_staff_since`` and ``due_at``.
- ``waiting_customer``: a person answered (:func:`replied`).
- ``snoozed``: hidden until ``snoozed_until`` (:func:`snooze`); :func:`wake_snoozed` (the wake
  job) or :func:`wake_one` (the ``inbox.snooze_due`` reminder) returns it.
- ``resolved``: a person resolved it (:func:`resolve`) and the bot has the conversation again.
  It differs from ``bot`` for the Resolved tab only. A later customer message moves it to ``bot``
  through the 0622 trigger when the AI owns the conversation (``needs_staff`` when a person owns
  it or a held reply is pending).

Resolve and hand back: both hand the conversation back to the AI with the columns of
``inbox/set_owner.sql`` (owned_by 'ai', status 'open', mute cleared, ``ai_suppressed_before`` now,
one ``inbox_version`` bump), resolve its open escalations, close its open escalation ticket with a
``ticket_events`` row, and clear the reason, clock, due time, claim and snooze. When the AI
already has the chat (no person holds it: the /release route's ``human_muted`` rule), the owner
columns and ``ai_suppressed_before`` are left alone, so the bot keeps its answer to a message
arriving now; a queue change still bumps ``inbox_version`` once and writes its event, and a call
with nothing to change writes nothing and returns False (``already_handled``). :func:`resolve`
leaves ``resolved`` with ``resolved_at`` = now() and writes a ``resolved`` event; :func:`hand_back`
(the ``/release`` route, the staff-chat "Resume AI" button) leaves ``bot`` with ``resolved_at``
as it was and writes a ``handed_back`` event (a NULL actor is the staff-chat path; the 0622
trigger also writes ``handed_back`` with a NULL actor and ``{"cause": "customer_message"}``).

Rules shared by every function here:

- It runs on the caller's tenant transaction and takes the conversation row lock first
  (:func:`csa.staff.ownership.lock_conversation`); everything else happens under it.
  :func:`wake_snoozed` locks its conversations one by one in ascending id order.
- An unknown conversation (or another company's) raises ``QueueError("not_in_queue")``.
- Each change writes one ``inbox_queue_events`` row whose client and conversation ids come from
  the conversation row; a call that changes nothing writes nothing.
- The staff actions (:func:`claim`, :func:`unclaim`, :func:`assign`, :func:`snooze`,
  :func:`resolve`, :func:`set_priority`, :func:`set_topic`, :func:`hand_back`) and a person's
  answer (:func:`replied`) bump ``inbox_version`` once. :func:`enter`, :func:`held_reply_closed`,
  :func:`wake_snoozed` and :func:`wake_one` do not: they are system moves (a bump there would make
  the next staff action fail with 409), or the caller's own writes cover them (takeover).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from csa.core import db
from csa.inbox_queue import settings as inbox_settings
from csa.staff.ownership import lock_conversation

PRIORITIES = frozenset({"low", "normal", "high", "urgent"})
TOPICS = frozenset(
    {"purchase", "question", "complaint", "payment", "booking", "subscription", "other"}
)
# Bounds the ticket loop of a hand back; a conversation has one open escalation ticket at most.
_MAX_TICKETS = 50


class QueueError(Exception):
    """A queue action the caller asked for and may not have; ``code`` names why.

    Codes: ``not_in_queue`` (unknown conversation, or one outside the queue),
    ``held_reply_pending``, ``customer_wrote``, ``until_in_past``.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --- helpers --------------------------------------------------------------------------------------


async def _locked(
    conn: AsyncConnection[Any], client_id: UUID, conversation_id: UUID
) -> dict[str, Any]:
    """Lock the conversation row, then read its queue state (``get_item.sql``)."""
    try:
        locked = await lock_conversation(conn, client_id, conversation_id)
    except RuntimeError as exc:
        if str(exc) != "conversation_not_found":
            raise
        raise QueueError("not_in_queue") from None
    item = await db.q.fetch_one("inbox_queue/get_item.sql", (client_id, conversation_id), conn=conn)
    if item is None:
        raise RuntimeError("conversation_not_found")
    item["contact_id"] = locked["contact_id"]
    item["human_muted"] = locked["human_muted"]
    return item


async def _staff_item(
    conn: AsyncConnection[Any], client_id: UUID, conversation_id: UUID
) -> dict[str, Any]:
    item = await _locked(conn, client_id, conversation_id)
    if not item["in_queue"]:
        raise QueueError("not_in_queue")
    return item


async def _event(
    conn: AsyncConnection[Any],
    client_id: UUID,
    conversation_id: UUID,
    *,
    actor_user_id: UUID | None,
    action: str,
    from_status: str,
    to_status: str,
    details: dict[str, Any] | None = None,
) -> None:
    row = await db.q.fetch_one(
        "inbox_queue/insert_event.sql",
        {
            "client_id": client_id,
            "conversation_id": conversation_id,
            "actor_user_id": actor_user_id,
            "action": action,
            "from_status": from_status,
            "to_status": to_status,
            "details": Jsonb(details or {}),
        },
        conn=conn,
    )
    if row is None:
        raise RuntimeError("inbox_queue_event_not_written")


async def _changed(path: str, params: Any, conn: AsyncConnection[Any]) -> None:
    """Run an UPDATE that must hit the locked row."""
    if await db.q.fetch_one(path, params, conn=conn) is None:
        raise RuntimeError(f"inbox_queue_update_missed: {path}")


def _refuse_if_unsafe(item: dict[str, Any], seen_customer_at: datetime | None) -> None:
    """The refusals of resolve and snooze.

    ``seen_customer_at`` is the newest customer message time the person saw when loading the item.
    ``None`` means the caller has no such information: the customer check is skipped (it is not read
    as "saw no message"). The compare is exact to the microsecond, like the stored time.
    """
    if item["held_reply_pending"]:
        raise QueueError("held_reply_pending")
    last = item["last_customer_at"]
    if seen_customer_at is not None and last is not None and seen_customer_at < last:
        raise QueueError("customer_wrote")


async def _close_escalation_tickets(
    conn: AsyncConnection[Any],
    client_id: UUID,
    conversation_id: UUID,
    contact_id: UUID | None,
    actor_user_id: UUID | None,
) -> list[int]:
    closed: list[int] = []
    new_value: dict[str, Any] = {"status": "resolved"}
    if actor_user_id is not None:
        new_value["actor_user_id"] = str(actor_user_id)
    for _ in range(_MAX_TICKETS):
        ticket = await db.q.fetch_one(
            "tickets/find_open_escalation.sql", (client_id, conversation_id), conn=conn
        )
        if ticket is None:
            return closed
        before = await db.q.fetch_one(
            "tickets/get.sql", (client_id, contact_id, ticket["id"]), conn=conn
        )
        await db.q.fetch_one(
            "tickets/set_status.sql", ("resolved", True, client_id, ticket["id"]), conn=conn
        )
        await db.q.fetch_one(
            "tickets/insert_event.sql",
            (
                client_id,
                ticket["id"],
                "staff",
                "resolved",
                Jsonb({"status": before["status"]}) if before is not None else None,
                Jsonb(new_value),
            ),
            conn=conn,
        )
        closed.append(ticket["id"])
    raise RuntimeError("too_many_open_escalation_tickets")


async def _hand_back(
    conn: AsyncConnection[Any],
    client_id: UUID,
    conversation_id: UUID,
    item: dict[str, Any],
    *,
    actor_user_id: UUID | None,
    status: str,
) -> bool:
    """The shared part of hand_back and resolve; False when there was nothing to do.

    A person holding the chat (``human_muted``, the rule of the /release route) gets the release of
    ``inbox/set_owner.sql``. When the AI already has the chat, set_owner.sql does not run: moving
    ``ai_suppressed_before`` would cost the bot its answer to a customer message arriving now. Then
    only a real change (queue state, claim, clock, an escalation or ticket closed) bumps
    ``inbox_version`` once and writes the event; with none, nothing is written.
    """
    escalations = await db.q.fetch_all(
        "inbox_queue/resolve_escalations.sql", (client_id, conversation_id), conn=conn
    )
    tickets = await _close_escalation_tickets(
        conn, client_id, conversation_id, item["contact_id"], actor_user_id
    )
    human_muted = bool(item["human_muted"])
    queue_changed = item["queue_status"] != status or any(
        item[column] is not None
        for column in (
            "queue_reason",
            "needs_staff_since",
            "due_at",
            "assignee_user_id",
            "claimed_at",
            "snoozed_until",
        )
    )
    if not (human_muted or queue_changed or escalations or tickets):
        return False
    if human_muted:
        # The release of the /release route, inbox_version + 1 included.
        await db.q.execute(
            "inbox/set_owner.sql", ("ai", "open", client_id, conversation_id), conn=conn
        )
    await _changed(
        "inbox_queue/release.sql",
        {
            "status": status,
            "bump": not human_muted,
            "client_id": client_id,
            "conversation_id": conversation_id,
        },
        conn,
    )
    await _event(
        conn,
        client_id,
        conversation_id,
        actor_user_id=actor_user_id,
        action="resolved" if status == "resolved" else "handed_back",
        from_status=item["queue_status"],
        to_status=status,
        details={"ticket_ids": tickets, "escalations_resolved": len(escalations)},
    )
    return True


async def _set_assignee(
    conn: AsyncConnection[Any],
    client_id: UUID,
    conversation_id: UUID,
    item: dict[str, Any],
    *,
    assignee: UUID | None,
    actor_user_id: UUID,
    action: str,
) -> None:
    previous: UUID | None = item["assignee_user_id"]
    if previous == assignee:
        return
    await _changed(
        "inbox_queue/set_assignee.sql",
        {"assignee_user_id": assignee, "client_id": client_id, "conversation_id": conversation_id},
        conn,
    )
    details: dict[str, Any] = {}
    if assignee is not None:
        details["assignee_user_id"] = str(assignee)
    if previous is not None:
        details["previous_assignee_user_id"] = str(previous)
    await _event(
        conn,
        client_id,
        conversation_id,
        actor_user_id=actor_user_id,
        action=action,
        from_status=item["queue_status"],
        to_status=item["queue_status"],
        details=details,
    )


# --- system and caller-side transitions -----------------------------------------------------------


async def enter(
    conn: AsyncConnection[Any],
    client_id: UUID,
    conversation_id: UUID,
    *,
    reason: str,
    priority: str = "normal",
    actor_user_id: UUID | None = None,
    claim_for: UUID | None = None,
) -> bool:
    """Put the item in needs_staff (``public.inbox_queue_enter``); True when something changed.

    The function keeps the clock of an item already in needs_staff, moves the reason and priority
    only up, sets ``due_at`` from the company's target, claims for ``claim_for`` only when the item
    is unclaimed, and returns False for a conversation outside the queue. No version bump.
    """
    await _locked(conn, client_id, conversation_id)
    row = await db.q.fetch_one(
        "inbox_queue/enter.sql",
        (client_id, conversation_id, reason, priority, actor_user_id, claim_for),
        conn=conn,
    )
    return bool(row and row["changed"])


async def replied(
    conn: AsyncConnection[Any], client_id: UUID, conversation_id: UUID, *, user_id: UUID | None
) -> None:
    """A person replied, or a held reply was approved or edited: waiting on the customer.

    Works from any state, ``bot`` included (a person replied to a taken-over conversation that never
    entered the queue). Sets ``last_staff_reply_at``, clears the reason, clock and due time, claims
    for ``user_id`` when the item is unclaimed and ``user_id`` is a console user. Does nothing for a
    conversation outside the queue. Bumps ``inbox_version`` once: only a person's answer reaches
    it (a customer message never does), so the version cannot be moved by the customer, and a
    person who read the item before the reply gets 409 instead of resolving over it.
    """
    item = await _locked(conn, client_id, conversation_id)
    if not item["in_queue"]:
        return
    row = await db.q.fetch_one(
        "inbox_queue/replied.sql",
        {"user_id": user_id, "client_id": client_id, "conversation_id": conversation_id},
        conn=conn,
    )
    if row is None:
        raise RuntimeError("inbox_queue_update_missed: inbox_queue/replied.sql")
    claimed = item["assignee_user_id"] is None and row["assignee_user_id"] is not None
    await _event(
        conn,
        client_id,
        conversation_id,
        actor_user_id=user_id,
        action="replied",
        from_status=item["queue_status"],
        to_status="waiting_customer",
        details={"assignee_user_id": str(row["assignee_user_id"])} if claimed else None,
    )


async def held_reply_closed(
    conn: AsyncConnection[Any], client_id: UUID, conversation_id: UUID
) -> None:
    """A held reply was rejected or expired: the customer got no answer.

    A needs_staff item whose reason is ``held_reply`` gets reason ``customer_message`` (same clock
    and due time) unless another held reply is still pending. Any other reason or state is left
    alone. Does nothing for a conversation outside the queue. No version bump.
    """
    item = await _locked(conn, client_id, conversation_id)
    if (
        not item["in_queue"]
        or item["queue_status"] != "needs_staff"
        or item["queue_reason"] != "held_reply"
        or item["held_reply_pending"]
    ):
        return
    await _changed(
        "inbox_queue/set_reason.sql", ("customer_message", client_id, conversation_id), conn
    )
    await _event(
        conn,
        client_id,
        conversation_id,
        actor_user_id=None,
        action="reason_changed",
        from_status="needs_staff",
        to_status="needs_staff",
        details={
            "reason": "customer_message",
            "previous_reason": "held_reply",
            "priority": item["priority"],
        },
    )


async def wake_snoozed(conn: AsyncConnection[Any], client_id: UUID, *, limit: int = 100) -> int:
    """Return the company's snoozed items whose time has come to needs_staff; how many woke.

    Reason ``customer_message`` when the customer wrote after the last staff reply, else ``manual``;
    a fresh clock (``needs_staff_since`` = now()) and ``due_at`` from the company's target for the
    item's priority; the claim stays. Event ``woke`` with no actor; no version bump. Items are
    locked one at a time in ascending id order and re-checked under the lock.
    """
    candidates = await db.q.fetch_all(
        "inbox_queue/list_due_snoozed.sql", (client_id, limit), conn=conn
    )
    if not candidates:
        return 0
    targets = await inbox_settings.load(conn, client_id)
    woke = 0
    for candidate in sorted(candidates, key=lambda r: r["id"]):
        woke += await _wake(conn, client_id, candidate["id"], targets)
    return woke


async def wake_one(conn: AsyncConnection[Any], client_id: UUID, conversation_id: UUID) -> bool:
    """Wake one snoozed item whose time has come, as :func:`wake_snoozed` does; True if it woke.

    The ``inbox.snooze_due`` reminder calls it when it fires (plan 2a, decision 12). The same
    effect as the wake job for this row: reason, fresh clock, claim kept, event ``woke`` with no
    actor, no version bump. False when the item is not snoozed (the wake job or a customer
    message moved it first), is outside the queue, or its ``snoozed_until`` has not passed by the
    database clock. An unknown conversation raises ``QueueError("not_in_queue")``.
    """
    targets = await inbox_settings.load(conn, client_id)
    return await _wake(conn, client_id, conversation_id, targets)


async def _wake(
    conn: AsyncConnection[Any],
    client_id: UUID,
    conversation_id: UUID,
    targets: inbox_settings.InboxSettings,
) -> bool:
    """Lock the item, and wake it when it is still snoozed and due; True when it woke."""
    item = await _locked(conn, client_id, conversation_id)
    if not item["in_queue"] or item["queue_status"] != "snoozed":
        return False
    last_customer, last_staff = item["last_customer_at"], item["last_staff_reply_at"]
    customer_wrote_last = last_customer is not None and (
        last_staff is None or last_customer > last_staff
    )
    reason = "customer_message" if customer_wrote_last else "manual"
    row = await db.q.fetch_one(
        "inbox_queue/wake.sql",
        (reason, targets.target_minutes(item["priority"]), client_id, conversation_id),
        conn=conn,
    )
    if row is None:  # its time moved on meanwhile
        return False
    await _event(
        conn,
        client_id,
        conversation_id,
        actor_user_id=None,
        action="woke",
        from_status="snoozed",
        to_status="needs_staff",
        details={"reason": reason, "priority": item["priority"]},
    )
    return True


# --- staff actions (each bumps inbox_version once) ------------------------------------------------


async def hand_back(
    conn: AsyncConnection[Any],
    client_id: UUID,
    conversation_id: UUID,
    *,
    actor_user_id: UUID | None,
) -> bool:
    """Return the conversation to the bot: state ``bot``, ``resolved_at`` kept (see the module).

    False when there was nothing to do (the AI already has the chat, the item is ``bot`` with no
    claim, and no escalation or ticket was open): the route's ``already_handled``.
    """
    item = await _staff_item(conn, client_id, conversation_id)
    return await _hand_back(
        conn, client_id, conversation_id, item, actor_user_id=actor_user_id, status="bot"
    )


async def resolve(
    conn: AsyncConnection[Any],
    client_id: UUID,
    conversation_id: UUID,
    *,
    actor_user_id: UUID,
    seen_customer_at: datetime | None,
) -> bool:
    """End the item and hand the conversation back to the bot: state ``resolved``.

    Refused with ``held_reply_pending`` while a live held reply waits (decide it first), and with
    ``customer_wrote`` when ``seen_customer_at`` is earlier than the newest customer message.
    ``seen_customer_at=None`` means the caller has no information and skips that check. False when
    the item was already ``resolved`` with nothing open and the AI has the chat (no event, no bump).
    """
    item = await _staff_item(conn, client_id, conversation_id)
    _refuse_if_unsafe(item, seen_customer_at)
    return await _hand_back(
        conn, client_id, conversation_id, item, actor_user_id=actor_user_id, status="resolved"
    )


async def snooze(
    conn: AsyncConnection[Any],
    client_id: UUID,
    conversation_id: UUID,
    *,
    actor_user_id: UUID,
    until: datetime,
    seen_customer_at: datetime | None,
) -> None:
    """Hide the item until ``until`` (timezone-aware, in the future); the claim stays.

    Refusals: ``until_in_past``, then those of :func:`resolve`.
    """
    if until.tzinfo is None:
        raise ValueError("until must be timezone-aware")
    item = await _staff_item(conn, client_id, conversation_id)
    if until <= datetime.now(UTC):
        raise QueueError("until_in_past")
    _refuse_if_unsafe(item, seen_customer_at)
    await _changed("inbox_queue/snooze.sql", (until, client_id, conversation_id), conn)
    await _event(
        conn,
        client_id,
        conversation_id,
        actor_user_id=actor_user_id,
        action="snoozed",
        from_status=item["queue_status"],
        to_status="snoozed",
        details={"snoozed_until": until.isoformat()},
    )


async def claim(
    conn: AsyncConnection[Any], client_id: UUID, conversation_id: UUID, *, user_id: UUID
) -> None:
    """Take the item.

    Replaces another holder (the event records the previous holder): the caller decides who may
    (the Inbox routes allow it to administrators only). A claim by the current holder changes
    nothing.
    """
    item = await _staff_item(conn, client_id, conversation_id)
    await _set_assignee(
        conn,
        client_id,
        conversation_id,
        item,
        assignee=user_id,
        actor_user_id=user_id,
        action="claimed",
    )


async def unclaim(
    conn: AsyncConnection[Any], client_id: UUID, conversation_id: UUID, *, actor_user_id: UUID
) -> None:
    """Release the claim (the assignee fields only); an unclaimed item is left alone."""
    item = await _staff_item(conn, client_id, conversation_id)
    await _set_assignee(
        conn,
        client_id,
        conversation_id,
        item,
        assignee=None,
        actor_user_id=actor_user_id,
        action="unclaimed",
    )


async def assign(
    conn: AsyncConnection[Any],
    client_id: UUID,
    conversation_id: UUID,
    *,
    assignee_id: UUID,
    actor_user_id: UUID,
) -> None:
    """Give the item to a member (the assignee fields only). Who may assign is the route's rule."""
    item = await _staff_item(conn, client_id, conversation_id)
    await _set_assignee(
        conn,
        client_id,
        conversation_id,
        item,
        assignee=assignee_id,
        actor_user_id=actor_user_id,
        action="assigned",
    )


async def set_priority(
    conn: AsyncConnection[Any],
    client_id: UUID,
    conversation_id: UUID,
    *,
    priority: str,
    actor_user_id: UUID,
) -> None:
    """Raise or lower the priority; a needs_staff item's ``due_at`` follows from its clock."""
    if priority not in PRIORITIES:
        raise ValueError(f"unknown priority: {priority!r}")
    item = await _staff_item(conn, client_id, conversation_id)
    if item["priority"] == priority:
        return
    targets = await inbox_settings.load(conn, client_id)
    await _changed(
        "inbox_queue/set_priority.sql",
        {
            "priority": priority,
            "target_min": targets.target_minutes(priority),
            "client_id": client_id,
            "conversation_id": conversation_id,
        },
        conn,
    )
    await _event(
        conn,
        client_id,
        conversation_id,
        actor_user_id=actor_user_id,
        action="priority_set",
        from_status=item["queue_status"],
        to_status=item["queue_status"],
        details={"priority": priority, "previous_priority": item["priority"]},
    )


async def set_topic(
    conn: AsyncConnection[Any],
    client_id: UUID,
    conversation_id: UUID,
    *,
    topic: str,
    actor_user_id: UUID,
) -> None:
    """Set the topic; the same topic again changes nothing."""
    if topic not in TOPICS:
        raise ValueError(f"unknown topic: {topic!r}")
    item = await _staff_item(conn, client_id, conversation_id)
    if item["topic"] == topic:
        return
    await _changed("inbox_queue/set_topic.sql", (topic, client_id, conversation_id), conn)
    details: dict[str, Any] = {"topic": topic}
    if item["topic"] is not None:
        details["previous_topic"] = item["topic"]
    await _event(
        conn,
        client_id,
        conversation_id,
        actor_user_id=actor_user_id,
        action="topic_set",
        from_status=item["queue_status"],
        to_status=item["queue_status"],
        details=details,
    )
