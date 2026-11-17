"""Session-authorized customer inbox, separate from the owner-private sandbox."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import Response
from fastapi.routing import APIRoute
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import Field, ValidationError, model_validator

from csa.approval.service import (
    EditTextError,
    ReplyDecisionResult,
    transition_reply,
    validate_edit_text,
)
from csa.console.access import tenant
from csa.console.contracts import ConsoleActor, Contract, Role
from csa.console.identity import IdentityService
from csa.console.inbox_contact import (
    InboxContact,
    display_name_or_none,
    mask_phone,
    parse_search,
    preview_text,
)
from csa.console.inbox_cursor import QueueOrder
from csa.console.inbox_cursor import decode as decode_cursor
from csa.console.inbox_queue_list import (
    WAITING_OVER_MAX,
    InboxQueueCounts,
    QueueFilter,
    QueuePriority,
    QueueReason,
    QueueState,
    QueueTopic,
    fetch_counts,
    fetch_page,
)
from csa.console.inbox_settings import (
    InboxAssignee,
    InboxSettingsIn,
    InboxSettingsOut,
    assignee_names,
    company_assignees,
    get_settings,
    put_settings,
)
from csa.console.kb_uploads import audit
from csa.console.services import package_gate
from csa.core import db
from csa.core.pii import redact
from csa.core.tenancy import tx
from csa.inbox_queue import transitions as queue
from csa.media.attachments import image_asset_id
from csa.staff.ownership import lock_conversation
from csa.workspace.registry import ProposalError


class InboxLastMessage(Contract):
    preview: str
    at: datetime


class InboxHandoffNote(Contract):
    """The assistant's note to staff, written when it handed the chat over (detail read only)."""

    text: str
    at: datetime


class InboxConversation(Contract):
    """A customer conversation. Action receipts persist this shape: the queue fields are ids,
    states and times only, never a name (the list and detail add names in `InboxConversationView`).
    The queue fields have defaults so receipts stored before they existed still replay."""

    id: UUID
    channel: Literal["telegram", "whatsapp"]
    status: str
    owned_by: str | None
    ai_muted_until: datetime | None
    human_muted: bool
    inbox_version: int
    opened_at: datetime
    queue_status: QueueState = "bot"
    queue_reason: QueueReason | None = None
    topic: QueueTopic | None = None
    priority: QueuePriority = "normal"
    assignee_id: UUID | None = None
    claimed_at: datetime | None = None
    needs_staff_since: datetime | None = None
    due_at: datetime | None = None
    snoozed_until: datetime | None = None
    last_customer_at: datetime | None = None
    last_staff_reply_at: datetime | None = None
    contact: InboxContact | None = None
    last_message: InboxLastMessage | None = None
    pending_approvals: int = 0
    handoff_note: InboxHandoffNote | None = None


class InboxAssigneeRef(Contract):
    """Who holds a queue item. The name is cosmetic: null when it cannot be read."""

    id: UUID
    display_name: str | None


class InboxConversationView(InboxConversation):
    """A list row or the detail read: the conversation and who holds it, by name. Never stored."""

    assignee: InboxAssigneeRef | None = None


class InboxConversationPage(Contract):
    items: list[InboxConversationView]
    # Opaque: pass it back as `before` with the same filters and order.
    next_cursor: str | None
    needs_review_count: int = 0
    counts: InboxQueueCounts
    # The server's time of this read (the database transaction's now(), UTC). The client sends it
    # back as the bulk route's `seen_at`, so resolve and snooze notice a customer who wrote since.
    as_of: datetime


class InboxPicture(Contract):
    """The stored picture an outbound image reply showed; read through the media route."""

    asset_id: UUID


class InboxMessage(Contract):
    id: UUID
    direction: str
    author: str
    body: str
    created_at: datetime
    trace_id: UUID | None
    delivery_status: str | None
    delivery_error: str | None = None
    # WhatsApp status callbacks: 'failed' arrives after a send that returned 200.
    provider_status: Literal["sent", "delivered", "read", "failed"] | None = None
    provider_error: str | None = None
    # Only an outbound image reply carries one; inbound attachments are never exposed here.
    picture: InboxPicture | None = None


class InboxMessagePage(Contract):
    items: list[InboxMessage]
    next_cursor: UUID | None


class InboxApproval(Contract):
    id: int
    conversation_id: UUID
    trace_id: UUID
    draft: str
    reason: str | None
    status: str
    expires_at: datetime


class InboxApprovalPage(Contract):
    items: list[InboxApproval]
    next_cursor: int | None


class InboxAction(Contract):
    request_key: UUID
    expected_version: int = Field(ge=0)


class HumanReply(InboxAction):
    body: str = Field(min_length=1, max_length=2000)


class ReplyDecision(Contract):
    request_key: UUID
    decision: Literal["approve", "reject", "edit"]
    text: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def edit_only(self) -> ReplyDecision:
        if (self.decision == "edit") != (self.text is not None):
            raise ValueError("only edit requires text")
        return self


class InboxResult(Contract):
    outcome: Literal["applied", "already_handled", "expired", "not_found"] = "applied"
    conversation: InboxConversation | None = None
    message_id: UUID | None = None
    status: str | None = None
    queued: bool = False
    warnings: list[str] = []


class InboxRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def bounded(request: Request) -> Response:
            if request.method in {"POST", "PUT"}:
                data = bytearray()
                async for chunk in request.stream():
                    data.extend(chunk)
                    if len(data) > 32768:
                        raise HTTPException(413, "inbox request is too large")
                request._body = bytes(data)
            return await handler(request)

        return bounded


def _message(row: dict[str, Any]) -> InboxMessage:
    """A message row; only an outbound image reply gets a picture, read from its attachments."""
    asset_id = image_asset_id(row["attachments"]) if row["direction"] == "outbound" else None
    fields = {key: value for key, value in row.items() if key != "attachments"}
    return InboxMessage.model_validate(
        {
            **fields,
            "body": redact(row["body"])[:8000],
            "picture": None if asset_id is None else {"asset_id": asset_id},
        }
    )


def inbox_actor(request: Request) -> ConsoleActor:
    actor = request.state.console_actor
    if not isinstance(actor, ConsoleActor) or actor.role not in {
        Role.PLATFORM_ADMIN,
        Role.TENANT_ADMIN,
        Role.TENANT_STAFF,
    }:
        raise HTTPException(403, "staff inbox permission required")
    return actor


def inbox_admin(request: Request) -> ConsoleActor:
    """The settings are changed by a tenant admin or a platform admin; tenant staff get 403."""
    actor = inbox_actor(request)
    if actor.role not in {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN}:
        raise HTTPException(403, "inbox settings require an administrator")
    return actor


_QUEUE_FIELDS = (
    "queue_status",
    "queue_reason",
    "topic",
    "priority",
    "assignee_id",
    "claimed_at",
    "needs_staff_since",
    "due_at",
    "snoozed_until",
    "last_customer_at",
    "last_staff_reply_at",
)


def conversation_from_row(
    row: dict[str, Any], *, full_phone: bool = False
) -> InboxConversationView:
    """Build the API shape from an inbox row; the full phone only when `full_phone` is set."""
    phone = row["phone"]
    contact = InboxContact(
        display_name=display_name_or_none(row["full_name"], phone),
        telegram_username=row["telegram_username"],
        phone_masked=mask_phone(phone),
        phone=phone if full_phone else None,
        kind=row["kind"],
        known=row["known"],
    )
    last = (
        InboxLastMessage(preview=preview_text(row["last_body"]), at=row["last_at"])
        if row["last_at"] is not None
        else None
    )
    note = (
        InboxHandoffNote(text=row["handoff_note"], at=row["handoff_note_at"])
        if row.get("handoff_note") is not None
        else None
    )
    return InboxConversationView.model_validate(
        {
            "id": row["id"],
            "channel": row["channel"],
            "status": row["status"],
            "owned_by": row["owned_by"],
            "ai_muted_until": row["ai_muted_until"],
            "human_muted": row["human_muted"],
            "inbox_version": row["inbox_version"],
            "opened_at": row["opened_at"],
            **{name: row[name] for name in _QUEUE_FIELDS},
            "contact": contact,
            "last_message": last,
            "pending_approvals": row["pending_approvals"],
            "handoff_note": note,
        }
    )


def _identity_pool(request: Request) -> AsyncConnectionPool | None:
    identity = getattr(request.app.state, "identity_service", None)
    return identity.pool if isinstance(identity, IdentityService) else None


async def named(
    identity_pool: AsyncConnectionPool | None,
    client_id: UUID,
    items: list[InboxConversationView],
) -> list[InboxConversationView]:
    """The items with `assignee` filled in from one identity read for all of them."""
    names = await assignee_names(
        identity_pool, client_id, {i.assignee_id for i in items if i.assignee_id}
    )
    return [
        item.model_copy(
            update={
                "assignee": InboxAssigneeRef(
                    id=item.assignee_id, display_name=names.get(item.assignee_id)
                )
                if item.assignee_id
                else None
            }
        )
        for item in items
    ]


async def conversation(
    conn: AsyncConnection[Any], client_id: UUID, conversation_id: UUID, *, lock: bool = False
) -> InboxConversation:
    """Conversation metadata only: no contact, preview or counts.

    Mutation routes persist this in their action receipts, so it must carry no personal data.
    """
    row = await db.q.fetch_one(
        "inbox/get_conversation.sql", (client_id, conversation_id), conn=conn
    )
    if row is None:
        raise HTTPException(404, "customer conversation not found")
    if lock:
        await lock_conversation(conn, client_id, conversation_id)
        return await conversation(conn, client_id, conversation_id)
    return InboxConversation.model_validate(row)


async def conversation_detail(
    conn: AsyncConnection[Any], client_id: UUID, conversation_id: UUID
) -> InboxConversationView:
    """The single-conversation read: contact with the full phone, last message and count."""
    row = await db.q.fetch_one(
        "inbox/get_conversation_detail.sql",
        {"client_id": client_id, "conversation_id": conversation_id},
        conn=conn,
    )
    if row is None:
        raise HTTPException(404, "customer conversation not found")
    return conversation_from_row(row, full_phone=True)


def request_fingerprint(target: str, payload: Contract) -> str:
    """What a request key is bound to: the action's target and the whole request body."""
    return hashlib.sha256((target + payload.model_dump_json()).encode()).hexdigest()


async def stored_receipt(
    conn: AsyncConnection[Any],
    client_id: UUID,
    actor: ConsoleActor,
    request_key: UUID,
    fingerprint: str,
) -> dict[str, Any] | None:
    """The stored result JSON of this request key, under the key's lock; 409 for another body."""
    await db.q.fetch_one(
        "inbox/lock_request.sql", (f"inbox:{client_id}:{actor.user_id}:{request_key}",), conn=conn
    )
    old = await db.q.fetch_one(
        "inbox/get_action.sql", (client_id, actor.user_id, request_key), conn=conn
    )
    if old is not None and old["fingerprint"] != fingerprint:
        raise HTTPException(409, "request key was used with different content")
    return old["result"] if old else None


async def replay(
    conn: AsyncConnection[Any],
    client_id: UUID,
    actor: ConsoleActor,
    payload: InboxAction | ReplyDecision,
    target: str,
) -> tuple[str, InboxResult | None]:
    fingerprint = request_fingerprint(target, payload)
    old = await stored_receipt(conn, client_id, actor, payload.request_key, fingerprint)
    return fingerprint, InboxResult.model_validate(old) if old is not None else None


async def record(
    conn: AsyncConnection[Any],
    client_id: UUID,
    actor: ConsoleActor,
    request_key: UUID,
    fingerprint: str,
    result: InboxResult,
    action: str,
    target: str,
) -> None:
    await db.q.execute(
        "inbox/record_action.sql",
        (client_id, actor.user_id, request_key, fingerprint, Jsonb(result.model_dump(mode="json"))),
        conn=conn,
    )
    await audit(conn, actor, client_id, f"inbox.{action}", target)


async def decide_approval(
    conn: AsyncConnection[Any],
    client_id: UUID,
    actor: ConsoleActor,
    approval_id: int,
    decision: Literal["approve", "reject", "edit"],
    text: str | None,
    request_key: UUID,
    *,
    expected_version: str | None = None,
) -> InboxResult:
    """Decide a held reply on the caller's tenant transaction; replays per request key.

    ``expected_version`` is the conversation's ``inbox_version`` a workspace card read. It is
    compared after the replay (a key that already decided replays even if the customer wrote
    since) and before acting, under the conversation lock ``transition_reply`` takes anyway;
    a move refuses with ``proposal_stale``, as the card does. The Inbox route passes none.
    """
    payload = ReplyDecision(request_key=request_key, decision=decision, text=text)
    fingerprint, old = await replay(conn, client_id, actor, payload, f"approval:{approval_id}")
    if old is not None:
        return old
    reference = await db.q.fetch_one("inbox/get_approval.sql", (client_id, approval_id), conn=conn)
    if reference is None:
        raise HTTPException(404, "reply approval not found")
    if expected_version is not None:
        current = await conversation(conn, client_id, reference["conversation_id"], lock=True)
        if str(current.inbox_version) != expected_version:
            raise ProposalError("proposal_stale")
    try:
        result: ReplyDecisionResult = await transition_reply(
            conn,
            client_id=client_id,
            approval_id=approval_id,
            decision={"approve": "approved", "reject": "rejected", "edit": "edited"}[
                payload.decision
            ],  # type: ignore[arg-type]
            actor=f"console_user:{actor.user_id}",
            text=payload.text,
        )
    except EditTextError as exc:
        raise HTTPException(422, str(exc)) from None
    out = InboxResult(**result.model_dump())
    await record(
        conn,
        client_id,
        actor,
        payload.request_key,
        fingerprint,
        out,
        "reply_decided",
        str(approval_id),
    )
    return out


def build_inbox_router() -> APIRouter:
    # The package gate runs first so every role sees package_inactive before role errors.
    # A suspended Customer Support package keeps the Inbox reachable: escalation and order
    # approval never switch off, so staff must still be able to resolve held replies.
    router = APIRouter(
        prefix="/inbox",
        dependencies=[
            Depends(package_gate("customer_support", statuses=("active", "suspended"))),
            Depends(inbox_actor),
        ],
        route_class=InboxRoute,
    )

    @router.get("/conversations", response_model=InboxConversationPage)
    async def list_conversations(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        actor: Annotated[ConsoleActor, Depends(inbox_actor)],
        before: Annotated[str | None, Query(max_length=200)] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        channel: Literal["telegram", "whatsapp"] | None = None,
        needs_review: bool = False,
        sender: Literal["known", "new"] | None = None,
        q: Annotated[str | None, Query(max_length=64)] = None,
        state: Annotated[list[QueueState] | None, Query()] = None,
        assignee: Literal["me", "none"] | UUID | None = None,
        priority_min: QueuePriority | None = None,
        reason: Annotated[list[QueueReason] | None, Query()] = None,
        topic: Annotated[list[QueueTopic] | None, Query()] = None,
        overdue: bool = False,
        waiting_over: Annotated[int | None, Query(ge=1, le=WAITING_OVER_MAX)] = None,
        order: QueueOrder | None = None,
    ) -> InboxConversationPage:
        """The work queue: every filter combines with AND; `before` is the `next_cursor` of the
        previous page (a bare conversation id is still read as an activity cursor)."""
        # The search text is personal data: it is parsed here and never logged or audited.
        try:
            search = parse_search(q)
        except ValueError:
            raise HTTPException(422, "search needs at least two characters") from None
        try:
            flt = QueueFilter(
                state=state or [],
                assignee=assignee,
                priority_min=priority_min,
                reason=reason or [],
                topic=topic or [],
                overdue=overdue,
                waiting_over=waiting_over,
                channel=channel,
                needs_review=needs_review,
                sender=sender,
                q=q,
                order=order,
            )
        except ValidationError as invalid:
            # The parameters were validated one by one above; what is left are the rules across
            # them (order=priority). The messages name no input, so the search never shows.
            detail = "; ".join(e["msg"].removeprefix("Value error, ") for e in invalid.errors())
            raise HTTPException(422, detail) from None
        try:
            cursor = decode_cursor(before, flt.effective_order()) if before else None
        except ValueError:
            raise HTTPException(422, "invalid cursor") from None
        async with tx(request.app.state.pool, str(client_id)) as conn:
            rows, next_cursor = await fetch_page(
                conn,
                flt,
                client_id=client_id,
                user_id=actor.user_id,
                search=search,
                cursor=cursor,
                limit=limit,
            )
            counted = await db.q.fetch_one(
                "inbox/count_needs_review.sql", {"client_id": client_id}, conn=conn
            )
            counts = await fetch_counts(conn, client_id, actor.user_id)
            read_at = await db.q.fetch_one("inbox/as_of.sql", conn=conn)
            if read_at is None:
                raise RuntimeError("inbox_as_of_missing")
        items = await named(
            _identity_pool(request), client_id, [conversation_from_row(row) for row in rows]
        )
        return InboxConversationPage(
            items=items,
            next_cursor=next_cursor,
            needs_review_count=counted["n"] if counted else 0,
            counts=counts,
            as_of=read_at["as_of"].astimezone(UTC),
        )

    @router.get("/queue/counts", response_model=InboxQueueCounts)
    async def queue_counts(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        actor: Annotated[ConsoleActor, Depends(inbox_actor)],
    ) -> InboxQueueCounts:
        """The queue tabs' counts; `mine` is the person asking."""
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await fetch_counts(conn, client_id, actor.user_id)

    @router.get("/settings", response_model=InboxSettingsOut)
    async def inbox_settings(
        request: Request, client_id: Annotated[UUID, Depends(tenant)]
    ) -> InboxSettingsOut:
        """Response targets and reminder knobs; the defaults with version 0 until a first save."""
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await get_settings(conn, client_id)

    @router.put("/settings", response_model=InboxSettingsOut)
    async def replace_inbox_settings(
        request: Request,
        payload: InboxSettingsIn,
        client_id: Annotated[UUID, Depends(tenant)],
        actor: Annotated[ConsoleActor, Depends(inbox_admin)],
    ) -> InboxSettingsOut:
        """Replace every setting; 409 `version_conflict` when `version` is not the one stored.
        Items already waiting keep their due time; only later arrivals use the new targets."""
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await put_settings(conn, client_id, actor, payload)

    @router.get("/assignees", response_model=list[InboxAssignee])
    async def assignees(
        request: Request, client_id: Annotated[UUID, Depends(tenant)]
    ) -> list[InboxAssignee]:
        """Enabled administrators and staff of the company, by display name; no addresses."""
        return await company_assignees(_identity_pool(request), client_id)

    @router.get("/conversations/{conversation_id}", response_model=InboxConversationView)
    async def get_conversation(
        request: Request,
        conversation_id: UUID,
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> InboxConversationView:
        async with tx(request.app.state.pool, str(client_id)) as conn:
            item = await conversation_detail(conn, client_id, conversation_id)
        [shown] = await named(_identity_pool(request), client_id, [item])
        return shown

    @router.get("/conversations/{conversation_id}/messages", response_model=InboxMessagePage)
    async def messages(
        request: Request,
        conversation_id: UUID,
        client_id: Annotated[UUID, Depends(tenant)],
        before: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> InboxMessagePage:
        async with tx(request.app.state.pool, str(client_id)) as conn:
            await conversation(conn, client_id, conversation_id)
            rows = await db.q.fetch_all(
                "inbox/list_messages.sql",
                (client_id, conversation_id, before, before, limit + 1),
                conn=conn,
            )
        items = [_message(row) for row in rows[:limit]]
        return InboxMessagePage(
            items=items, next_cursor=items[-1].id if len(rows) > limit else None
        )

    @router.get("/approvals", response_model=InboxApprovalPage)
    async def approvals(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        conversation_id: UUID | None = None,
        include_handled: bool = False,
        before: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> InboxApprovalPage:
        async with tx(request.app.state.pool, str(client_id)) as conn:
            if conversation_id is not None:
                await conversation(conn, client_id, conversation_id)
            rows = await db.q.fetch_all(
                "inbox/list_approvals.sql",
                (
                    client_id,
                    before,
                    before,
                    conversation_id,
                    conversation_id,
                    include_handled,
                    limit + 1,
                ),
                conn=conn,
            )
        items = [
            InboxApproval.model_validate(
                {
                    **row,
                    "draft": redact(row["draft"])[:8000],
                    "reason": redact(row["reason"] or "")[:500],
                }
            )
            for row in rows[:limit]
        ]
        return InboxApprovalPage(
            items=items, next_cursor=items[-1].id if len(rows) > limit else None
        )

    async def mutate_conversation(
        request: Request,
        client_id: UUID,
        conversation_id: UUID,
        payload: InboxAction | HumanReply,
        action: Literal["takeover", "release", "reply"],
    ) -> InboxResult:
        actor = inbox_actor(request)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            fingerprint, old = await replay(
                conn, client_id, actor, payload, f"{action}:{conversation_id}"
            )
            if old is not None:
                return old
            current = await conversation(conn, client_id, conversation_id, lock=True)
            if current.inbox_version != payload.expected_version:
                raise HTTPException(409, "conversation changed; refresh before acting")
            result = InboxResult()
            if action == "reply":
                assert isinstance(payload, HumanReply)
                if not current.human_muted:
                    raise HTTPException(409, "take over the conversation before replying")
                try:
                    text = validate_edit_text(payload.body)
                except EditTextError as exc:
                    raise HTTPException(422, str(exc)) from None
                route = await db.q.fetch_one(
                    "inbox/get_reply_route.sql", (client_id, conversation_id), conn=conn
                )
                if route is None:
                    raise HTTPException(409, "This conversation has no active channel to reply on.")
                message = await db.q.fetch_one(
                    "inbox/insert_reply.sql",
                    (
                        client_id,
                        conversation_id,
                        text,
                        redact(text),
                        f"human:{client_id}:{actor.user_id}:{payload.request_key}",
                        route["trace_id"],
                    ),
                    conn=conn,
                )
                if message is None:
                    raise RuntimeError("human_reply_not_created")
                await db.q.fetch_one(
                    "worker/insert_delivery.sql",
                    (
                        client_id,
                        route["endpoint_id"],
                        route["trace_id"],
                        conversation_id,
                        message["id"],
                    ),
                    conn=conn,
                )
                result.message_id, result.queued = message["id"], True
                await queue.replied(conn, client_id, conversation_id, user_id=actor.user_id)
            elif action == "release":
                # The release columns when a person holds the chat, then the queue, escalation and
                # ticket; an AI-owned chat whose queue item still changed is a real change too.
                if not await queue.hand_back(
                    conn, client_id, conversation_id, actor_user_id=actor.user_id
                ):
                    result.outcome = "already_handled"
            elif current.human_muted and current.ai_muted_until is None:
                result.outcome = "already_handled"
            else:
                await db.q.execute(
                    "inbox/set_owner.sql",
                    ("human_support", "escalated", client_id, conversation_id),
                    conn=conn,
                )
                await db.q.execute(
                    "inbox/suppress_pending.sql", (client_id, conversation_id), conn=conn
                )
                await queue.enter(
                    conn,
                    client_id,
                    conversation_id,
                    reason="manual",
                    priority="normal",
                    actor_user_id=actor.user_id,
                    claim_for=actor.user_id,
                )
            result.conversation = await conversation(conn, client_id, conversation_id)
            await record(
                conn,
                client_id,
                actor,
                payload.request_key,
                fingerprint,
                result,
                action,
                str(conversation_id),
            )
            return result

    @router.post("/conversations/{conversation_id}/takeover", response_model=InboxResult)
    async def takeover(
        request: Request,
        conversation_id: UUID,
        payload: InboxAction,
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> InboxResult:
        return await mutate_conversation(request, client_id, conversation_id, payload, "takeover")

    @router.post("/conversations/{conversation_id}/release", response_model=InboxResult)
    async def release(
        request: Request,
        conversation_id: UUID,
        payload: InboxAction,
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> InboxResult:
        return await mutate_conversation(request, client_id, conversation_id, payload, "release")

    @router.post(
        "/conversations/{conversation_id}/reply", response_model=InboxResult, status_code=202
    )
    async def reply(
        request: Request,
        conversation_id: UUID,
        payload: HumanReply,
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> InboxResult:
        return await mutate_conversation(request, client_id, conversation_id, payload, "reply")

    # The queue actions live in their own module, which imports this one.
    from csa.console.inbox_queue_actions import add_queue_action_routes

    add_queue_action_routes(router)

    # The saved views too (they import this module for inbox_actor).
    from csa.console.inbox_saved_views import add_saved_view_routes

    add_saved_view_routes(router)

    @router.post("/approvals/{approval_id}/decide", response_model=InboxResult)
    async def decide(
        request: Request,
        approval_id: Annotated[int, Path(ge=1, le=2**63 - 1)],
        payload: ReplyDecision,
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> InboxResult:
        actor = inbox_actor(request)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await decide_approval(
                conn,
                client_id,
                actor,
                approval_id,
                payload.decision,
                payload.text,
                payload.request_key,
            )

    return router
