"""The Inbox queue actions: claim, release claim, assign, snooze, resolve, priority and topic on one
conversation, and the same actions on up to 50 conversations at once (design 2026-11-16, sections
4, 5.2 and 6.6).

One conversation (``POST /inbox/conversations/{id}/queue/{action}``) follows the takeover route:
the request key's receipt first, then the conversation lock, then the ``expected_version`` check
(409), then the transition of :mod:`csa.inbox_queue.transitions`, then the receipt and the audit
row ``inbox.queue_<action>``. The outcome is ``applied`` when the transition changed the item and
``already_handled`` when it did not. Every staff transition bumps ``inbox_version`` exactly once
when it changes something and writes nothing otherwise, and the conversation lock is held from
the first read to the last, so "changed" is read as "``inbox_version`` moved" for every action.

Bulk (``POST /inbox/bulk``) is one replayable request: it carries no per-item version. The items
are locked in ascending id order (the lock order of the snooze wake job, so two bulk requests and
the job never deadlock), each in its own savepoint on one tenant transaction, so a refused item
leaves the others alone. The refusals a person can act on come back per item; any other error
aborts the whole request. Results come back in the request's order.

Roles: ``inbox_actor`` (the router's dependency) for everything. Assigning to someone is for a
tenant admin or a platform admin acting on the company; staff may claim and release their own
claim. The assignee is checked against the company's enabled admins and staff on the identity pool
before the tenant transaction opens (``csa_app`` cannot read the directory).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import AwareDatetime, Field, model_validator

from csa.console.access import tenant
from csa.console.contracts import ConsoleActor, Contract, Role
from csa.console.identity import IdentityService
from csa.console.inbox import (
    InboxAction,
    InboxConversation,
    InboxResult,
    inbox_actor,
    record,
    replay,
    request_fingerprint,
    stored_receipt,
)
from csa.console.inbox_queue_list import QueuePriority, QueueTopic
from csa.console.inbox_settings import company_assignees
from csa.core import db
from csa.core.tenancy import tx
from csa.inbox_queue import transitions as queue
from csa.staff.ownership import lock_conversation

QueueActionName = Literal["claim", "unclaim", "assign", "snooze", "resolve", "priority", "topic"]
BulkOutcome = Literal[
    "done",
    "already_handled",
    "held_reply_pending",
    "customer_wrote",
    "not_found",
    "not_in_queue",
    "not_allowed",
]
SNOOZE_MAX = timedelta(days=60)
BULK_MAX = 50
# The refusals a bulk item reports; any other QueueError is the whole request's.
_ITEM_REFUSALS = frozenset({"held_reply_pending", "customer_wrote", "not_in_queue"})
_QUEUE_STATUS = {
    "held_reply_pending": 409,
    "customer_wrote": 409,
    "until_in_past": 422,
    "not_in_queue": 404,
}


class QueueClaim(InboxAction):
    """Claim the item, or release the claim."""


class QueueAssign(InboxAction):
    assignee_id: UUID


class QueueSnooze(InboxAction):
    # In the future and at most 60 days ahead.
    until: AwareDatetime
    # The newest customer message time the person saw; null skips that check.
    seen_customer_at: AwareDatetime | None = None


class QueueResolve(InboxAction):
    seen_customer_at: AwareDatetime | None = None


class QueuePrioritySet(InboxAction):
    priority: QueuePriority


class QueueTopicSet(InboxAction):
    topic: QueueTopic


# The field each bulk action needs; the others must be absent.
_BULK_FIELDS: dict[str, str | None] = {
    "claim": None,
    "unclaim": None,
    "assign": "assignee_id",
    "snooze": "until",
    "resolve": None,
    "priority": "priority",
    "topic": "topic",
}


class BulkAction(Contract):
    """One action on up to 50 conversations. `seen_at` is when the client read the list:
    resolve and snooze refuse an item whose customer wrote after it (`customer_wrote`)."""

    request_key: UUID
    action: QueueActionName
    ids: list[UUID] = Field(min_length=1, max_length=BULK_MAX)
    seen_at: AwareDatetime
    assignee_id: UUID | None = None
    until: AwareDatetime | None = None
    priority: QueuePriority | None = None
    topic: QueueTopic | None = None

    @model_validator(mode="after")
    def fields_for_the_action(self) -> BulkAction:
        if len(set(self.ids)) != len(self.ids):
            raise ValueError("ids must be unique")
        needed = _BULK_FIELDS[self.action]
        for name in ("assignee_id", "until", "priority", "topic"):
            if name == needed and getattr(self, name) is None:
                raise ValueError(f"{self.action} requires {name}")
            if name != needed and name in self.model_fields_set:
                raise ValueError(f"{self.action} does not take {name}")
        return self


class BulkItemResult(Contract):
    id: UUID
    outcome: BulkOutcome


class BulkResult(Contract):
    """Per item, in the request's order. Also the bulk request's stored receipt."""

    results: list[BulkItemResult]


@dataclass(frozen=True)
class _Change:
    action: QueueActionName
    assignee_id: UUID | None = None
    until: datetime | None = None
    seen_customer_at: datetime | None = None
    priority: str | None = None
    topic: str | None = None


def _directory(request: Request) -> AsyncConnectionPool | None:
    """The identity pool the member directory is read on; None when the app has none."""
    identity = getattr(request.app.state, "identity_service", None)
    return identity.pool if isinstance(identity, IdentityService) else None


def _queue_refusal(error: queue.QueueError) -> HTTPException:
    return HTTPException(_QUEUE_STATUS.get(error.code, 409), {"code": error.code})


def _is_admin(actor: ConsoleActor) -> bool:
    return actor.role in {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN}


async def _check_assignee(
    request: Request, client_id: UUID, actor: ConsoleActor, user: UUID
) -> None:
    """Admins only (403), and only to an enabled admin or staff member of the company (422).

    Reads the identity pool, so the caller runs it before opening the tenant transaction.
    """
    if not _is_admin(actor):
        raise HTTPException(403, "assigning needs an administrator")
    members = await company_assignees(_directory(request), client_id)
    if user not in {member.id for member in members}:
        raise HTTPException(422, "assignee is not an enabled member of this company")


def _check_until(until: datetime) -> None:
    now = datetime.now(UTC)
    if until <= now:
        raise HTTPException(422, {"code": "until_in_past"})
    if until > now + SNOOZE_MAX:
        raise HTTPException(422, {"code": "until_too_far"})


async def _locked(
    conn: AsyncConnection[Any], client_id: UUID, conversation_id: UUID
) -> InboxConversation | None:
    """Lock and read the conversation's metadata; None when the company has no such one."""
    found = await db.q.fetch_one(
        "inbox/get_conversation.sql", (client_id, conversation_id), conn=conn
    )
    if found is None:
        return None
    await lock_conversation(conn, client_id, conversation_id)
    return await _current(conn, client_id, conversation_id)


async def _current(
    conn: AsyncConnection[Any], client_id: UUID, conversation_id: UUID
) -> InboxConversation:
    row = await db.q.fetch_one(
        "inbox/get_conversation.sql", (client_id, conversation_id), conn=conn
    )
    if row is None:
        raise RuntimeError("conversation_not_found")
    return InboxConversation.model_validate(row)


def _may_release(actor: ConsoleActor, current: InboxConversation) -> bool:
    """Staff release only their own claim; admins any."""
    holder = current.assignee_id
    return _is_admin(actor) or holder is None or holder == actor.user_id


async def _apply(
    conn: AsyncConnection[Any],
    client_id: UUID,
    conversation_id: UUID,
    actor: ConsoleActor,
    change: _Change,
) -> None:
    """Run the transition; called under the conversation lock."""
    me = actor.user_id
    match change.action:
        case "claim":
            await queue.claim(conn, client_id, conversation_id, user_id=me)
        case "unclaim":
            await queue.unclaim(conn, client_id, conversation_id, actor_user_id=me)
        case "assign":
            assert change.assignee_id is not None
            await queue.assign(
                conn, client_id, conversation_id, assignee_id=change.assignee_id, actor_user_id=me
            )
        case "snooze":
            assert change.until is not None
            await queue.snooze(
                conn,
                client_id,
                conversation_id,
                actor_user_id=me,
                until=change.until,
                seen_customer_at=change.seen_customer_at,
            )
        case "resolve":
            await queue.resolve(
                conn,
                client_id,
                conversation_id,
                actor_user_id=me,
                seen_customer_at=change.seen_customer_at,
            )
        case "priority":
            assert change.priority is not None
            await queue.set_priority(
                conn, client_id, conversation_id, priority=change.priority, actor_user_id=me
            )
        case "topic":
            assert change.topic is not None
            await queue.set_topic(
                conn, client_id, conversation_id, topic=change.topic, actor_user_id=me
            )


async def _act(
    request: Request,
    client_id: UUID,
    conversation_id: UUID,
    payload: InboxAction,
    change: _Change,
) -> InboxResult:
    actor = inbox_actor(request)
    if change.action == "assign":
        assert change.assignee_id is not None
        await _check_assignee(request, client_id, actor, change.assignee_id)
    name = f"queue_{change.action}"
    async with tx(request.app.state.pool, str(client_id)) as conn:
        fingerprint, old = await replay(
            conn, client_id, actor, payload, f"{name}:{conversation_id}"
        )
        if old is not None:
            return old
        current = await _locked(conn, client_id, conversation_id)
        if current is None:
            raise HTTPException(404, "customer conversation not found")
        if current.inbox_version != payload.expected_version:
            raise HTTPException(409, "conversation changed; refresh before acting")
        if change.action == "unclaim" and not _may_release(actor, current):
            raise HTTPException(403, "only the holder or an administrator can release this claim")
        if change.until is not None:
            _check_until(change.until)
        try:
            await _apply(conn, client_id, conversation_id, actor, change)
        except queue.QueueError as refused:
            raise _queue_refusal(refused) from None
        after = await _current(conn, client_id, conversation_id)
        result = InboxResult(
            outcome="applied"
            if after.inbox_version != current.inbox_version
            else "already_handled",
            conversation=after,
        )
        await record(
            conn,
            client_id,
            actor,
            payload.request_key,
            fingerprint,
            result,
            name,
            str(conversation_id),
        )
        return result


async def _bulk_item(
    conn: AsyncConnection[Any],
    client_id: UUID,
    actor: ConsoleActor,
    conversation_id: UUID,
    change: _Change,
) -> BulkOutcome:
    """One item in its own savepoint: a refusal rolls back this item only."""
    try:
        async with conn.transaction():
            current = await _locked(conn, client_id, conversation_id)
            if current is None:
                return "not_found"
            if change.action == "unclaim" and not _may_release(actor, current):
                return "not_allowed"
            await _apply(conn, client_id, conversation_id, actor, change)
            after = await _current(conn, client_id, conversation_id)
            return "done" if after.inbox_version != current.inbox_version else "already_handled"
    except queue.QueueError as refused:
        if refused.code in _ITEM_REFUSALS:
            return refused.code  # type: ignore[return-value]
        raise _queue_refusal(refused) from None


async def _bulk(request: Request, client_id: UUID, payload: BulkAction) -> BulkResult:
    actor = inbox_actor(request)
    if payload.action == "assign":
        assert payload.assignee_id is not None
        await _check_assignee(request, client_id, actor, payload.assignee_id)
    seen = payload.seen_at if payload.action in {"resolve", "snooze"} else None
    change = _Change(
        action=payload.action,
        assignee_id=payload.assignee_id,
        until=payload.until,
        seen_customer_at=seen,
        priority=payload.priority,
        topic=payload.topic,
    )
    fingerprint = request_fingerprint("queue_bulk", payload)
    async with tx(request.app.state.pool, str(client_id)) as conn:
        old = await stored_receipt(conn, client_id, actor, payload.request_key, fingerprint)
        if old is not None:
            return BulkResult.model_validate(old)
        if payload.until is not None:
            _check_until(payload.until)
        outcomes: dict[UUID, BulkOutcome] = {}
        for conversation_id in sorted(payload.ids):
            outcomes[conversation_id] = await _bulk_item(
                conn, client_id, actor, conversation_id, change
            )
        result = BulkResult(
            results=[BulkItemResult(id=i, outcome=outcomes[i]) for i in payload.ids]
        )
        await db.q.execute(
            "inbox/record_action.sql",
            (
                client_id,
                actor.user_id,
                payload.request_key,
                fingerprint,
                Jsonb(result.model_dump(mode="json")),
            ),
            conn=conn,
        )
        counts = Counter(outcomes.values())
        await db.q.execute(
            "console/audit.sql",
            (
                actor.user_id,
                client_id,
                "inbox.queue_bulk",
                str(payload.request_key),
                Jsonb(
                    {
                        "action": payload.action,
                        "items": len(payload.ids),
                        "outcomes": dict(sorted(counts.items())),
                    }
                ),
            ),
            conn=conn,
        )
        return result


def add_queue_action_routes(router: APIRouter) -> None:
    """Add the queue action routes to the Inbox router (its prefix, gate and role dependency)."""

    @router.post("/conversations/{conversation_id}/queue/claim", response_model=InboxResult)
    async def queue_claim(
        request: Request,
        conversation_id: UUID,
        payload: QueueClaim,
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> InboxResult:
        """Take the item; allowed over another holder (the event records them)."""
        return await _act(request, client_id, conversation_id, payload, _Change("claim"))

    @router.post("/conversations/{conversation_id}/queue/unclaim", response_model=InboxResult)
    async def queue_unclaim(
        request: Request,
        conversation_id: UUID,
        payload: QueueClaim,
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> InboxResult:
        """Release the claim. Tenant staff release only their own (403 otherwise)."""
        return await _act(request, client_id, conversation_id, payload, _Change("unclaim"))

    @router.post("/conversations/{conversation_id}/queue/assign", response_model=InboxResult)
    async def queue_assign(
        request: Request,
        conversation_id: UUID,
        payload: QueueAssign,
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> InboxResult:
        """Give the item to an enabled admin or staff member (422 otherwise). Administrators
        only (403 for tenant staff); 503 when the member directory is unavailable."""
        change = _Change("assign", assignee_id=payload.assignee_id)
        return await _act(request, client_id, conversation_id, payload, change)

    @router.post("/conversations/{conversation_id}/queue/snooze", response_model=InboxResult)
    async def queue_snooze(
        request: Request,
        conversation_id: UUID,
        payload: QueueSnooze,
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> InboxResult:
        """Hide the item until `until` (future, at most 60 days ahead: 422 `until_in_past` or
        `until_too_far`). 409 `held_reply_pending` or `customer_wrote` as for resolve."""
        change = _Change("snooze", until=payload.until, seen_customer_at=payload.seen_customer_at)
        return await _act(request, client_id, conversation_id, payload, change)

    @router.post("/conversations/{conversation_id}/queue/resolve", response_model=InboxResult)
    async def queue_resolve(
        request: Request,
        conversation_id: UUID,
        payload: QueueResolve,
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> InboxResult:
        """End the item and hand the conversation back to the bot. 409 `held_reply_pending`
        while a held reply waits; 409 `customer_wrote` when the customer wrote after
        `seen_customer_at`."""
        change = _Change("resolve", seen_customer_at=payload.seen_customer_at)
        return await _act(request, client_id, conversation_id, payload, change)

    @router.post("/conversations/{conversation_id}/queue/priority", response_model=InboxResult)
    async def queue_priority(
        request: Request,
        conversation_id: UUID,
        payload: QueuePrioritySet,
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> InboxResult:
        """Set the priority; a waiting item's due time follows from its clock."""
        change = _Change("priority", priority=payload.priority)
        return await _act(request, client_id, conversation_id, payload, change)

    @router.post("/conversations/{conversation_id}/queue/topic", response_model=InboxResult)
    async def queue_topic(
        request: Request,
        conversation_id: UUID,
        payload: QueueTopicSet,
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> InboxResult:
        """Set the topic."""
        change = _Change("topic", topic=payload.topic)
        return await _act(request, client_id, conversation_id, payload, change)

    @router.post("/bulk", response_model=BulkResult)
    async def bulk(
        request: Request,
        payload: BulkAction,
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> BulkResult:
        """One action on 1 to 50 distinct conversations; 200 with an outcome per item, in the
        request's order. Replayable per `request_key`; assign is for administrators only."""
        return await _bulk(request, client_id, payload)
