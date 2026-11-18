"""The Inbox's reminder kinds, the sweep that schedules them, and what the transitions call (plan
2a, tasks 5 and 6; design 7.3).

Five kinds, registered with :mod:`csa.reminders` when the worker starts
(:func:`register_inbox_reminder_kinds`); ``inbox.assigned`` is sent at once by the assignment
(:func:`notify_assigned`) and has no reminder row. A reminder's subject is the conversation
(``SUBJECT_TYPE``, the id as text); its payload holds the conversation id and the cycle only,
and its key is ``<kind>:<conversation id>:<cycle in epoch microseconds>`` (:func:`reminder_key`),
the cycle being the time the item's current wait began (to the microsecond, so two cycles that
begin in the same second are two reminders):

- ``inbox.overdue`` (cycle ``needs_staff_since``): the item waits past its response target. The
  holder when the team still has them, else the whole team; also the company staff chat. From
  the second fire the priority is one level higher; the last fire (``fire_count ==
  max_repeats``) also names every tenant admin. Fires again every ``repeat_minutes``.
- ``inbox.snooze_due`` (cycle ``snoozed_until``): the snooze ended. Wakes the item with
  :func:`csa.inbox_queue.transitions.wake_one` unless the wake job already did, then tells the
  holder (else the team) once.
- ``inbox.stale_claim`` (cycle ``claimed_at``): claimed and nobody acted for
  ``stale_claim_hours``. The holder, then (``repeat_minutes`` later) the tenant admins.
- ``inbox.follow_up`` and ``inbox.handback`` (cycle ``last_staff_reply_at``): a person answered
  and the customer has been quiet for ``follow_up_hours`` / ``handback_prompt_hours``; handback
  only while a person still holds the chat (the assistant is paused for it). The holder, else the
  team.

Every handler re-reads the item (``reminder_item.sql``) and drops the reminder silently (an
empty decision) when the item has moved on: another state, another cycle, the setting turned
off, or nobody left to tell. Nobody to tell sends nothing at all, the staff chat included.
Only ``inbox.overdue`` reaches the staff chat; the others are console notifications. A notice
names the customer by display name only and never carries message text.

The SQL triggers (0622) put items in Needs us with no Python hook, so a once-a-minute sweep
(:func:`schedule_missing`, the ``inbox_reminders_sweep`` job) schedules what is missing:
``inbox.overdue`` for needs-us items, ``inbox.stale_claim`` for claimed ones, ``inbox.snooze_due``
for items snoozed before this layer existed. Follow-up and handback are scheduled by the
transition that starts their cycle (a person's reply).

The transitions of :mod:`csa.inbox_queue.transitions` keep the reminders in step on their own
transaction (decisions 2 and 4): a reply schedules the prompts (:func:`schedule_follow_up`), a
snooze its wake reminder (:func:`schedule_snooze_due`), and both cancel the item's other pending
reminders; resolve and hand back cancel all of them, release and reassign the stale claim
(:func:`cancel_pending`). A cancel never waits for a reminder row the fire job holds (see
:func:`cancel_pending`); a reminder it skips, or one a SQL trigger made stale (a customer
message), is dropped by its handler's re-check.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from psycopg import AsyncConnection

from csa.core import db
from csa.inbox_queue import settings as inbox_settings
from csa.inbox_queue import transitions
from csa.notify.people import admin_user_ids, notify_people, team_user_ids
from csa.reminders import (
    Decision,
    HandlerContext,
    Notice,
    ReminderHandler,
    cancel_skip_locked,
    schedule,
)
from csa.reminders import registry as kinds
from csa.reminders.registry import Priority

OVERDUE = "inbox.overdue"
SNOOZE_DUE = "inbox.snooze_due"
STALE_CLAIM = "inbox.stale_claim"
FOLLOW_UP = "inbox.follow_up"
HANDBACK = "inbox.handback"
KINDS = (OVERDUE, SNOOZE_DUE, STALE_CLAIM, FOLLOW_UP, HANDBACK)
ASSIGNED = "inbox.assigned"
"""Sent at once to the new holder of an assignment by someone else; console only, no reminder."""
SUBJECT_TYPE = "conversation"

SWEEP_BATCH = 100
"""At most this many reminders scheduled per kind per company per sweep run."""
SWEEP_QUERIES = {
    OVERDUE: "inbox_queue/reminder_sweep_overdue.sql",
    STALE_CLAIM: "inbox_queue/reminder_sweep_stale_claim.sql",
    SNOOZE_DUE: "inbox_queue/reminder_sweep_snooze_due.sql",
}
"""The sweep's reads by kind; each takes the parameters of :func:`sweep_params`."""

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_RAISED = {"low": "normal", "normal": "high", "high": "urgent", "urgent": "urgent"}
_WAKE_RETRY = timedelta(minutes=1)
_WAKE_TRIES = 5


# --- keys and payloads ----------------------------------------------------------------------------


def cycle_epoch(cycle: datetime) -> int:
    """A cycle time as UTC epoch microseconds, exactly as the sweep's SQL writes it.

    ``(extract(epoch FROM ts) * 1000000)::bigint`` in SQL; both are exact (a timestamptz holds
    microseconds), so a key made here and one made by the sweep are the same text.
    """
    return (cycle - _EPOCH) // timedelta(microseconds=1)


def reminder_key(kind: str, conversation_id: UUID, cycle: datetime) -> str:
    """The reminder's dedupe key: one reminder per kind, conversation and cycle."""
    return f"{kind}:{conversation_id}:{cycle_epoch(cycle)}"


def reminder_payload(conversation_id: UUID, cycle: datetime) -> dict[str, Any]:
    """The reminder's payload: ids and the cycle, never anything a person wrote."""
    return {"conversation_id": str(conversation_id), "cycle": cycle_epoch(cycle)}


async def _schedule(
    conn: AsyncConnection[Any],
    client_id: UUID,
    kind: str,
    conversation_id: UUID,
    cycle: datetime,
    due_at: datetime,
) -> None:
    await schedule(
        conn,
        client_id,
        kind,
        SUBJECT_TYPE,
        str(conversation_id),
        due_at,
        reminder_payload(conversation_id, cycle),
        reminder_key(kind, conversation_id, cycle),
    )


def _link(conversation_id: UUID) -> dict[str, Any]:
    return {"route": "inbox", "conversation": str(conversation_id)}


# --- what the transitions call (task 6) -----------------------------------------------------------


async def cancel_pending(
    conn: AsyncConnection[Any],
    client_id: UUID,
    conversation_id: UUID,
    kinds: Sequence[str] = KINDS,
    *,
    keep: Sequence[str] = (),
) -> int:
    """Cancel the item's scheduled reminders of ``kinds`` (every Inbox kind by default); how many.

    Runs on the transition's transaction, under its conversation lock, and never waits for a
    reminder row (:func:`csa.reminders.cancel_skip_locked`). The fire job holds the row it fires
    and the ``inbox.snooze_due`` handler then takes the conversation lock (reminder, then
    conversation); a transition holds the conversation and then cancels (conversation, then
    reminder). Waiting here would deadlock, so a row being fired right now is skipped: it stays
    scheduled, and its handler's re-check drops it at its next fire, as for any reminder whose item
    moved on. ``keep`` names keys the caller schedules again in the same breath.
    """
    return await cancel_skip_locked(
        conn, client_id, kinds, SUBJECT_TYPE, str(conversation_id), keep=keep
    )


async def schedule_follow_up(
    conn: AsyncConnection[Any],
    client_id: UUID,
    conversation_id: UUID,
    replied_at: datetime,
    *,
    human_muted: bool,
) -> None:
    """A person's reply starts a wait on the customer (decision 2): schedule its prompts.

    First cancels the item's other pending reminders: its clock and claim (``inbox.overdue``,
    ``inbox.stale_claim``), a snooze's wake and an earlier reply's prompts (decision 4). Then
    ``inbox.follow_up`` at ``replied_at + follow_up_hours`` and, while a person holds the chat
    (``human_muted``, decision 11), ``inbox.handback`` at ``replied_at + handback_prompt_hours``;
    a setting of 0 schedules none of its kind. The cycle is ``replied_at`` (the item's
    ``last_staff_reply_at``), so two replies in one transaction keep one prompt of each kind.
    """
    settings = await inbox_settings.load(conn, client_id)
    plan: list[tuple[str, int]] = []
    if settings.follow_up_hours > 0:
        plan.append((FOLLOW_UP, settings.follow_up_hours))
    if human_muted and settings.handback_prompt_hours > 0:
        plan.append((HANDBACK, settings.handback_prompt_hours))
    keep = [reminder_key(kind, conversation_id, replied_at) for kind, _ in plan]
    await cancel_pending(conn, client_id, conversation_id, keep=keep)
    for kind, hours in plan:
        due = replied_at + timedelta(hours=hours)
        await _schedule(conn, client_id, kind, conversation_id, replied_at, due)


async def schedule_snooze_due(
    conn: AsyncConnection[Any], client_id: UUID, conversation_id: UUID, snoozed_until: datetime
) -> None:
    """A snooze stops the clock (decision 2): schedule ``inbox.snooze_due`` at ``snoozed_until``.

    First cancels the item's other pending reminders: its clock and claim, an earlier snooze's
    wake and a reply's prompts (decision 4). A snooze to the same time keeps its reminder.
    """
    key = reminder_key(SNOOZE_DUE, conversation_id, snoozed_until)
    await cancel_pending(conn, client_id, conversation_id, keep=[key])
    await _schedule(conn, client_id, SNOOZE_DUE, conversation_id, snoozed_until, snoozed_until)


async def notify_assigned(
    conn: AsyncConnection[Any],
    client_id: UUID,
    conversation_id: UUID,
    *,
    assignee: UUID,
    actor: UUID,
    claimed_at: datetime,
) -> int:
    """Tell ``assignee`` that ``actor`` gave them the item (``inbox.assigned``); rows written.

    The caller sends it only when the new holder is someone other than the actor. One console
    notification, never the staff chat, on the caller's transaction; its key is
    ``inbox.assigned:<conversation id>:<claimed_at in epoch microseconds>``, so the same
    assignment written twice is one row. Params are ids, the priority and the customer's display
    name, never message text. An assignee who is not an enabled member gets no row.
    """
    item = await db.q.fetch_one(
        "inbox_queue/reminder_item.sql",
        {"client_id": client_id, "conversation_id": conversation_id},
        conn=conn,
    )
    if item is None:
        return 0
    result = await notify_people(
        conn,
        client_id,
        recipients=[assignee],
        kind=ASSIGNED,
        priority=item["priority"],
        message_key=ASSIGNED,
        params={
            "conversation_id": str(conversation_id),
            "customer": item["customer"],
            "priority": item["priority"],
            "assigned_by": str(actor),
        },
        link=_link(conversation_id),
        dedupe_key=reminder_key(ASSIGNED, conversation_id, claimed_at),
    )
    return result.console_rows


# --- the sweep ------------------------------------------------------------------------------------


def _sweep_params(
    client_id: UUID, now: datetime, limit: int, settings: inbox_settings.InboxSettings
) -> dict[str, Any]:
    return {
        "client_id": client_id,
        "now": now,
        "limit": limit,
        "target_urgent": settings.target_urgent_min,
        "target_high": settings.target_high_min,
        "target_normal": settings.target_normal_min,
        "target_low": settings.target_low_min,
    }


async def sweep_params(
    conn: AsyncConnection[Any], client_id: UUID, now: datetime, *, limit: int = SWEEP_BATCH
) -> dict[str, Any]:
    """The parameters every query of ``SWEEP_QUERIES`` takes, with the company's targets."""
    return _sweep_params(client_id, now, limit, await inbox_settings.load(conn, client_id))


async def schedule_missing(
    conn: AsyncConnection[Any], client_id: UUID, now: datetime, *, limit: int = SWEEP_BATCH
) -> dict[str, int]:
    """Schedule the reminders the queue's items lack; count them by kind (plan 2a, decisions 1-3).

    One indexed read per kind, at most ``limit`` rows each; the rest wait for the next run.

    - ``inbox.overdue`` for each needs-us item with no reminder for its cycle, due at its
      ``due_at`` while that is ahead, else ``now`` plus the response target of its priority (a fresh
      clock: items already late at rollout get no burst). A reminder that has not fired yet is moved
      when the item's due time changed (a priority change); counted too.
    - ``inbox.stale_claim`` for each claimed needs-us item with none for its claim, due
      ``stale_claim_hours`` after the claim (or after ``now`` when that has passed); none while the
      setting is 0.
    - ``inbox.snooze_due`` for each snoozed item with none for its snooze, due at ``snoozed_until``.

    A cycle that already has a reminder row in any state is left alone, so a finished reminder is
    never made again. Sandbox and console chat conversations are never read.
    """
    settings = await inbox_settings.load(conn, client_id)
    params = _sweep_params(client_id, now, limit, settings)
    counts = dict.fromkeys(SWEEP_QUERIES, 0)
    for row in await db.q.fetch_all(SWEEP_QUERIES[OVERDUE], params, conn=conn):
        due: datetime = row["due_at"]
        if due <= now:
            due = now + timedelta(minutes=settings.target_minutes(row["priority"]))
        await _schedule(conn, client_id, OVERDUE, row["id"], row["cycle"], due)
        counts[OVERDUE] += 1
    if settings.stale_claim_hours > 0:
        after = timedelta(hours=settings.stale_claim_hours)
        for row in await db.q.fetch_all(SWEEP_QUERIES[STALE_CLAIM], params, conn=conn):
            due = row["cycle"] + after
            if due <= now:
                due = now + after
            await _schedule(conn, client_id, STALE_CLAIM, row["id"], row["cycle"], due)
            counts[STALE_CLAIM] += 1
    for row in await db.q.fetch_all(SWEEP_QUERIES[SNOOZE_DUE], params, conn=conn):
        await _schedule(conn, client_id, SNOOZE_DUE, row["id"], row["cycle"], row["cycle"])
        counts[SNOOZE_DUE] += 1
    return counts


# --- what every handler shares --------------------------------------------------------------------


@dataclass(frozen=True)
class _Subject:
    conversation_id: UUID
    cycle: int
    item: dict[str, Any]

    def same(self, column: str) -> bool:
        """The item's ``column`` is still the time this reminder's cycle began."""
        at: datetime | None = self.item[column]
        return at is not None and cycle_epoch(at) == self.cycle


async def _subject(ctx: HandlerContext) -> _Subject | None:
    """The reminder's conversation in the queue, or None (malformed, gone or outside the queue)."""
    try:
        conversation_id = UUID(ctx.reminder.subject_id)
    except ValueError:
        return None
    cycle = ctx.reminder.payload.get("cycle")
    if not isinstance(cycle, int) or isinstance(cycle, bool):
        return None
    item = await db.q.fetch_one(
        "inbox_queue/reminder_item.sql",
        {"client_id": ctx.client_id, "conversation_id": conversation_id},
        conn=ctx.conn,
    )
    if item is None or not item["in_queue"]:
        return None
    return _Subject(conversation_id, cycle, item)


async def _holder_or_team(ctx: HandlerContext, holder: UUID | None) -> list[UUID]:
    """The holder while the team still has them (enabled, not read-only), else the whole team."""
    team = await team_user_ids(ctx.conn, ctx.client_id)
    if holder is not None and holder in team:
        return [holder]
    return team


def _notice(
    kind: str,
    subject: _Subject,
    recipients: list[UUID],
    *,
    priority: str,
    waited_since: datetime | None,
    now: datetime,
    repeat: int | None = None,
    staff_chat: bool = False,
) -> Notice:
    """The notice of ``kind``: its message key is the kind, its link opens the conversation."""
    params: dict[str, Any] = {
        "conversation_id": str(subject.conversation_id),
        "customer": subject.item["customer"],
        "priority": priority,
    }
    if waited_since is not None:
        params["waited_minutes"] = max(0, (now - waited_since) // timedelta(minutes=1))
    if repeat is not None:
        params["repeat"] = repeat
    return Notice(
        recipients=tuple(recipients),
        kind=kind,
        priority=cast(Priority, priority),  # a queue priority: the 0620 check holds it
        message_key=kind,
        params=params,
        link=_link(subject.conversation_id),
        staff_chat=staff_chat,
        staff_event=kind if staff_chat else None,
    )


# --- the handlers ---------------------------------------------------------------------------------


async def handle_overdue(ctx: HandlerContext) -> Decision:
    """``inbox.overdue``: the item still needs us on the same clock (see the module).

    Before its first notice, a reminder that finds the item due later (a priority lowered since
    the sweep scheduled it) waits for the item's due time instead of notifying early.
    """
    subject = await _subject(ctx)
    if subject is None:
        return Decision()
    item = subject.item
    if item["queue_status"] != "needs_staff" or not subject.same("needs_staff_since"):
        return Decision()
    fired = ctx.reminder.fire_count
    due: datetime | None = item["due_at"]
    if fired == 0 and due is not None and due > ctx.now:
        # The priority was lowered before the sweep moved this reminder: not overdue yet. A wait
        # is not a fire (fire.py), so the first notice is still fire 1.
        return Decision(next_due_at=due)
    settings = await inbox_settings.load(ctx.conn, ctx.client_id)
    priority: str = item["priority"]
    if fired >= 1:
        priority = _RAISED.get(priority, priority)
    recipients = await _holder_or_team(ctx, item["assignee_user_id"])
    if not recipients:
        return Decision()  # nobody may act for this company: no console row and no chat
    last = fired >= settings.max_repeats
    if last:
        admins = await admin_user_ids(ctx.conn, ctx.client_id)
        recipients += [admin for admin in admins if admin not in recipients]
    notice = _notice(
        OVERDUE,
        subject,
        recipients,
        priority=priority,
        waited_since=item["needs_staff_since"],
        now=ctx.now,
        repeat=fired + 1,
        staff_chat=True,
    )
    again = None if last else ctx.now + timedelta(minutes=settings.repeat_minutes)
    return Decision(notices=(notice,), next_due_at=again)


async def handle_snooze_due(ctx: HandlerContext) -> Decision:
    """``inbox.snooze_due``: wake the item if the wake job has not, then tell its holder once.

    Plan 2a decision 12. Still snoozed on this cycle: :func:`transitions.wake_one` wakes it (with
    the notice in one transaction, so a crash replays both). Already woken by the wake job: the
    item's current clock began with a ``woke`` event at or after this snooze's end. Anything else (a
    customer message brought it back, a person moved it, a new snooze) drops it. When the database
    clock is still behind the job's (``wake_one`` says not yet), it tries again each minute and
    gives up on the fifth try; the wake job still wakes the item then.
    """
    subject = await _subject(ctx)
    if subject is None:
        return Decision()
    item = subject.item
    if item["queue_status"] == "snoozed":
        if not subject.same("snoozed_until"):
            return Decision()
        if not await transitions.wake_one(ctx.conn, ctx.client_id, subject.conversation_id):
            # Due by the job's clock and not yet by the database's: try again each minute. A wait
            # is not a fire (fire.py), so the bound is time: the fifth try, four minutes on, drops.
            if ctx.now >= item["snoozed_until"] + _WAKE_RETRY * (_WAKE_TRIES - 1):
                return Decision()
            return Decision(next_due_at=ctx.now + _WAKE_RETRY)
    elif not (
        item["queue_status"] == "needs_staff"
        and item["woke_this_cycle"]
        and cycle_epoch(item["needs_staff_since"]) >= subject.cycle
    ):
        return Decision()
    recipients = await _holder_or_team(ctx, item["assignee_user_id"])
    if not recipients:
        return Decision()
    notice = _notice(
        SNOOZE_DUE, subject, recipients, priority=item["priority"], waited_since=None, now=ctx.now
    )
    return Decision(notices=(notice,))


async def handle_stale_claim(ctx: HandlerContext) -> Decision:
    """``inbox.stale_claim``: claimed, still needing us, and nobody acted since the claim.

    The first fire tells the holder and comes back ``repeat_minutes`` later; the second tells the
    tenant admins and finishes. A holder who is no longer on the team is skipped: the admins are
    told at once.
    """
    subject = await _subject(ctx)
    if subject is None:
        return Decision()
    item = subject.item
    holder: UUID | None = item["assignee_user_id"]
    if (
        item["queue_status"] != "needs_staff"
        or holder is None
        or not subject.same("claimed_at")
        or item["staff_acted_since_claim"]
    ):
        return Decision()
    settings = await inbox_settings.load(ctx.conn, ctx.client_id)
    if settings.stale_claim_hours == 0:
        return Decision()
    if ctx.reminder.fire_count == 0 and holder in await team_user_ids(ctx.conn, ctx.client_id):
        notice = _notice(
            STALE_CLAIM,
            subject,
            [holder],
            priority=item["priority"],
            waited_since=item["needs_staff_since"],
            now=ctx.now,
        )
        return Decision(
            notices=(notice,), next_due_at=ctx.now + timedelta(minutes=settings.repeat_minutes)
        )
    admins = await admin_user_ids(ctx.conn, ctx.client_id)
    if not admins:
        return Decision()
    notice = _notice(
        STALE_CLAIM,
        subject,
        admins,
        priority=item["priority"],
        waited_since=item["needs_staff_since"],
        now=ctx.now,
    )
    return Decision(notices=(notice,))


async def _waiting_prompt(ctx: HandlerContext, kind: str) -> Decision:
    """``inbox.follow_up`` and ``inbox.handback``: still waiting on the customer since the reply."""
    subject = await _subject(ctx)
    if subject is None:
        return Decision()
    item = subject.item
    if item["queue_status"] != "waiting_customer" or not subject.same("last_staff_reply_at"):
        return Decision()
    if kind == HANDBACK and not item["human_muted"]:
        return Decision()  # the assistant has the chat again: nothing to hand back
    settings = await inbox_settings.load(ctx.conn, ctx.client_id)
    hours = settings.handback_prompt_hours if kind == HANDBACK else settings.follow_up_hours
    if hours == 0:
        return Decision()
    recipients = await _holder_or_team(ctx, item["assignee_user_id"])
    if not recipients:
        return Decision()
    notice = _notice(
        kind,
        subject,
        recipients,
        priority=item["priority"],
        waited_since=item["last_staff_reply_at"],
        now=ctx.now,
    )
    return Decision(notices=(notice,))


async def handle_follow_up(ctx: HandlerContext) -> Decision:
    """``inbox.follow_up``: follow up or resolve (console only, never the staff chat)."""
    return await _waiting_prompt(ctx, FOLLOW_UP)


async def handle_handback(ctx: HandlerContext) -> Decision:
    """``inbox.handback``: a person still holds a quiet chat; hand it back to the assistant."""
    return await _waiting_prompt(ctx, HANDBACK)


HANDLERS: dict[str, ReminderHandler] = {
    OVERDUE: handle_overdue,
    SNOOZE_DUE: handle_snooze_due,
    STALE_CLAIM: handle_stale_claim,
    FOLLOW_UP: handle_follow_up,
    HANDBACK: handle_handback,
}


def register_inbox_reminder_kinds() -> None:
    """Register the Inbox's reminder kinds in the process registry (the worker, at start-up).

    Safe to call again: a kind that already has this module's handler is skipped. A kind held by
    another handler raises ``ValueError``, as the registry does for any duplicate.
    """
    for kind, handler in HANDLERS.items():
        if kinds.registry.get(kind) is handler:
            continue
        kinds.register_kind(kind, handler)
