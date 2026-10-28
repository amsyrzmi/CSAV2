"""Session-authorized customer inbox, separate from the owner-private sandbox."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import Response
from fastapi.routing import APIRoute
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import Field, model_validator

from csa.approval.service import (
    EditTextError,
    ReplyDecisionResult,
    transition_reply,
    validate_edit_text,
)
from csa.console.access import tenant
from csa.console.contracts import ConsoleActor, Contract, Role
from csa.console.inbox_contact import (
    InboxContact,
    display_name_or_none,
    mask_phone,
    parse_search,
    preview_text,
)
from csa.console.kb_uploads import audit
from csa.console.services import package_gate
from csa.core import db
from csa.core.pii import redact
from csa.core.tenancy import tx
from csa.staff.ownership import lock_conversation


class InboxLastMessage(Contract):
    preview: str
    at: datetime


class InboxConversation(Contract):
    id: UUID
    channel: Literal["telegram", "whatsapp"]
    status: str
    owned_by: str | None
    ai_muted_until: datetime | None
    human_muted: bool
    inbox_version: int
    opened_at: datetime
    contact: InboxContact | None = None
    last_message: InboxLastMessage | None = None
    pending_approvals: int = 0


class InboxConversationPage(Contract):
    items: list[InboxConversation]
    next_cursor: UUID | None
    needs_review_count: int = 0


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
            if request.method == "POST":
                data = bytearray()
                async for chunk in request.stream():
                    data.extend(chunk)
                    if len(data) > 32768:
                        raise HTTPException(413, "inbox request is too large")
                request._body = bytes(data)
            return await handler(request)

        return bounded


def inbox_actor(request: Request) -> ConsoleActor:
    actor = request.state.console_actor
    if not isinstance(actor, ConsoleActor) or actor.role not in {
        Role.PLATFORM_ADMIN,
        Role.TENANT_ADMIN,
        Role.TENANT_STAFF,
    }:
        raise HTTPException(403, "staff inbox permission required")
    return actor


def conversation_from_row(row: dict[str, Any], *, full_phone: bool = False) -> InboxConversation:
    """Build the API shape from an inbox row; the full phone only when `full_phone` is set."""
    phone = row["phone"]
    contact = InboxContact(
        display_name=display_name_or_none(row["full_name"], phone),
        telegram_username=None,
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
    return InboxConversation(
        id=row["id"],
        channel=row["channel"],
        status=row["status"],
        owned_by=row["owned_by"],
        ai_muted_until=row["ai_muted_until"],
        human_muted=row["human_muted"],
        inbox_version=row["inbox_version"],
        opened_at=row["opened_at"],
        contact=contact,
        last_message=last,
        pending_approvals=row["pending_approvals"],
    )


async def conversation(
    conn: AsyncConnection[Any],
    client_id: UUID,
    conversation_id: UUID,
    *,
    lock: bool = False,
    full_phone: bool = False,
) -> InboxConversation:
    row = await db.q.fetch_one(
        "inbox/get_conversation.sql",
        {"client_id": client_id, "conversation_id": conversation_id},
        conn=conn,
    )
    if row is None:
        raise HTTPException(404, "customer conversation not found")
    if lock:
        await lock_conversation(conn, client_id, conversation_id)
        return await conversation(conn, client_id, conversation_id, full_phone=full_phone)
    return conversation_from_row(row, full_phone=full_phone)


async def replay(
    conn: AsyncConnection[Any],
    client_id: UUID,
    actor: ConsoleActor,
    payload: InboxAction | ReplyDecision,
    target: str,
) -> tuple[str, InboxResult | None]:
    fingerprint = hashlib.sha256((target + payload.model_dump_json()).encode()).hexdigest()
    await db.q.fetch_one(
        "inbox/lock_request.sql",
        (f"inbox:{client_id}:{actor.user_id}:{payload.request_key}",),
        conn=conn,
    )
    old = await db.q.fetch_one(
        "inbox/get_action.sql", (client_id, actor.user_id, payload.request_key), conn=conn
    )
    if old is not None and old["fingerprint"] != fingerprint:
        raise HTTPException(409, "request key was used with different content")
    return fingerprint, InboxResult.model_validate(old["result"]) if old else None


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
) -> InboxResult:
    """Decide a held reply on the caller's tenant transaction; replays per request key."""
    payload = ReplyDecision(request_key=request_key, decision=decision, text=text)
    fingerprint, old = await replay(conn, client_id, actor, payload, f"approval:{approval_id}")
    if old is not None:
        return old
    reference = await db.q.fetch_one("inbox/get_approval.sql", (client_id, approval_id), conn=conn)
    if reference is None:
        raise HTTPException(404, "reply approval not found")
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
        before: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        channel: Literal["telegram", "whatsapp"] | None = None,
        needs_review: bool = False,
        sender: Literal["known", "new"] | None = None,
        q: Annotated[str | None, Query(max_length=64)] = None,
    ) -> InboxConversationPage:
        # The search text is personal data: it is parsed here and never logged or audited.
        try:
            search = parse_search(q)
        except ValueError:
            raise HTTPException(422, "search needs at least two characters") from None
        async with tx(request.app.state.pool, str(client_id)) as conn:
            rows = await db.q.fetch_all(
                "inbox/list_conversations.sql",
                {
                    "client_id": client_id,
                    "channel": channel,
                    "needs_review": needs_review,
                    "sender": sender,
                    "like": search.like if search else None,
                    "digits": search.digits if search else None,
                    "before": before,
                    "limit": limit + 1,
                },
                conn=conn,
            )
            counted = await db.q.fetch_one(
                "inbox/count_needs_review.sql", {"client_id": client_id}, conn=conn
            )
        items = [conversation_from_row(row) for row in rows[:limit]]
        return InboxConversationPage(
            items=items,
            next_cursor=items[-1].id if len(rows) > limit else None,
            needs_review_count=counted["n"] if counted else 0,
        )

    @router.get("/conversations/{conversation_id}", response_model=InboxConversation)
    async def get_conversation(
        request: Request,
        conversation_id: UUID,
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> InboxConversation:
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await conversation(conn, client_id, conversation_id, full_phone=True)

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
        items = [
            InboxMessage.model_validate({**row, "body": redact(row["body"])[:8000]})
            for row in rows[:limit]
        ]
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
            else:
                if (
                    action == "takeover" and current.human_muted and current.ai_muted_until is None
                ) or (action == "release" and not current.human_muted):
                    result.outcome = "already_handled"
                else:
                    await db.q.execute(
                        "inbox/set_owner.sql",
                        (
                            "human_support" if action == "takeover" else "ai",
                            "escalated" if action == "takeover" else "open",
                            client_id,
                            conversation_id,
                        ),
                        conn=conn,
                    )
                    if action == "takeover":
                        await db.q.execute(
                            "inbox/suppress_pending.sql", (client_id, conversation_id), conn=conn
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
