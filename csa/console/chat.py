"""Private, read-only assistant sandbox over the durable runtime queues."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import Field, ValidationError, field_validator

from csa.console.access import tenant
from csa.console.contracts import ConsoleActor, Contract
from csa.console.kb_uploads import audit, human
from csa.contracts.envelope import Channel, Contact, ConversationRef, Envelope, Message, Meta
from csa.core import db
from csa.core.pii import redact
from csa.core.tenancy import tx


class CreateThread(Contract):
    request_key: UUID


class SendTurn(CreateThread):
    body: str = Field(min_length=1, max_length=4000)

    @field_validator("body")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


class ThreadOut(Contract):
    id: UUID
    sequence: int
    archived_at: datetime | None
    created_at: datetime


class ThreadPage(Contract):
    items: list[ThreadOut]
    next_cursor: int | None


class QueuedTurn(Contract):
    id: int
    trace_id: UUID
    duplicate: bool


class ReplyOut(Contract):
    id: UUID
    body: str


class TurnOut(Contract):
    id: int
    trace_id: UUID
    request_key: UUID
    body: str
    created_at: datetime
    status: Literal[
        "queued", "processing", "held", "awaiting_delivery", "available", "failed", "no_reply"
    ]
    replies: list[ReplyOut]


class TurnPage(Contract):
    items: list[TurnOut]
    next_cursor: int | None


async def _payload[Payload: Contract](request: Request, model: type[Payload]) -> Payload:
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 32768:
            raise HTTPException(413, "sandbox request is too large")
    try:
        return model.model_validate_json(data)
    except ValidationError:
        raise HTTPException(422, "invalid sandbox text request") from None


async def _thread(
    conn: AsyncConnection[Any], client_id: UUID, actor: ConsoleActor, conversation_id: UUID
) -> dict[str, Any]:
    return await _owned_thread(conn, client_id, actor.user_id, conversation_id)


async def _owned_thread(
    conn: AsyncConnection[Any], client_id: UUID, user_id: UUID, conversation_id: UUID
) -> dict[str, Any]:
    row = await db.q.fetch_one(
        "console/chat_thread_get.sql", (client_id, user_id, conversation_id), conn=conn
    )
    if row is None:
        raise HTTPException(404, "sandbox conversation not found")
    return row


async def enqueue_sandbox_turn(
    conn: AsyncConnection[Any],
    client_id: UUID,
    user_id: UUID,
    conversation_id: UUID,
    payload: SendTurn,
) -> QueuedTurn:
    """Persist one turn in the caller's authorized transaction; media uses the same path."""
    fingerprint = hashlib.sha256(payload.body.encode()).hexdigest()
    thread = await _owned_thread(conn, client_id, user_id, conversation_id)
    existing = await db.q.fetch_one(
        "console/chat_turn_by_key.sql",
        (client_id, conversation_id, payload.request_key),
        conn=conn,
    )
    if existing is not None:
        if existing["fingerprint"] != fingerprint:
            raise HTTPException(409, "request key was already used with different text")
        return QueuedTurn(id=existing["id"], trace_id=existing["trace_id"], duplicate=True)
    if thread["archived_at"] is not None:
        raise HTTPException(409, "sandbox conversation is archived")
    trace_id = uuid4()
    text = redact(payload.body)
    if len(text) > 8000:
        raise HTTPException(422, "redacted sandbox text is too large")
    envelope = Envelope(
        trace_id=trace_id,
        received_at=datetime.now(UTC),
        channel=Channel.WEB,
        idempotency_key=f"web:{user_id}:{conversation_id}:{payload.request_key}",
        client_id=client_id,
        contact=Contact(
            id=thread["contact_id"],
            external_id=str(user_id),
            channel=Channel.WEB,
            display_name="Sandbox participant",
        ),
        conversation=ConversationRef(
            id=conversation_id,
            channel_thread=str(conversation_id),
            turn_count=thread["turn_count"],
            status=thread["status"],
        ),
        message=Message(body_raw=text, body_redacted=text, language="en"),
        meta=Meta(is_first_contact=thread["turn_count"] == 0),
    )
    inserted = await db.q.fetch_one(
        "ingress/insert_event.sql",
        (
            client_id,
            thread["endpoint_id"],
            thread["contact_id"],
            envelope.idempotency_key,
            fingerprint,
            trace_id,
            Jsonb(
                {
                    "envelope": envelope.model_dump(mode="json"),
                    "source": {"channel": "web", "kind": "sandbox"},
                }
            ),
        ),
        conn=conn,
    )
    if inserted is None:
        raise RuntimeError("sandbox ingress conflicts with another request")
    row = await db.q.fetch_one(
        "console/chat_turn_insert.sql",
        (client_id, conversation_id, payload.request_key, fingerprint, trace_id, text),
        conn=conn,
    )
    await db.q.execute(
        "console/audit.sql",
        (user_id, client_id, "chat.queued", str(trace_id), Jsonb({})),
        conn=conn,
    )
    if row is None:
        raise RuntimeError("sandbox turn was not persisted")
    return QueuedTurn(id=row["id"], trace_id=row["trace_id"], duplicate=False)


def build_chat_router() -> APIRouter:
    router = APIRouter(prefix="/chat", dependencies=[Depends(human)])

    @router.post(
        "/conversations",
        response_model=ThreadOut,
        status_code=201,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": CreateThread.model_json_schema()}},
            }
        },
    )
    async def create(
        request: Request,
        actor: Annotated[ConsoleActor, Depends(human)],
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> ThreadOut:
        payload = await _payload(request, CreateThread)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            await db.q.execute(
                "console/chat_lock.sql",
                (f"web:create:{client_id}:{actor.user_id}",),
                conn=conn,
            )
            existing = await db.q.fetch_one(
                "console/chat_thread_by_key.sql",
                (client_id, actor.user_id, payload.request_key),
                conn=conn,
            )
            if existing is not None:
                return ThreadOut.model_validate(existing)
            participant = await db.q.fetch_one(
                "console/chat_participant_get.sql",
                (client_id, actor.user_id),
                conn=conn,
            )
            if participant is None:
                contact_id, credential_id, endpoint_id = uuid4(), uuid4(), uuid4()
                await db.q.execute(
                    "console/chat_contact_insert.sql", (contact_id, client_id), conn=conn
                )
                await db.q.execute(
                    "console/chat_credential_insert.sql", (credential_id, client_id), conn=conn
                )
                await db.q.execute(
                    "console/chat_endpoint_insert.sql",
                    (endpoint_id, client_id, f"sandbox_{endpoint_id.hex}", credential_id),
                    conn=conn,
                )
                await db.q.execute(
                    "console/chat_participant_insert.sql",
                    (client_id, actor.user_id, contact_id, endpoint_id),
                    conn=conn,
                )
            else:
                contact_id, endpoint_id = participant["contact_id"], participant["endpoint_id"]
            conversation_id = uuid4()
            await db.q.execute(
                "console/chat_conversation_insert.sql",
                (conversation_id, client_id, contact_id, str(conversation_id)),
                conn=conn,
            )
            row = await db.q.fetch_one(
                "console/chat_thread_insert.sql",
                (
                    conversation_id,
                    client_id,
                    actor.user_id,
                    contact_id,
                    endpoint_id,
                    payload.request_key,
                ),
                conn=conn,
            )
            await audit(conn, actor, client_id, "chat.created", str(conversation_id))
            return ThreadOut.model_validate(row)

    @router.get("/conversations", response_model=ThreadPage)
    async def conversations(
        request: Request,
        actor: Annotated[ConsoleActor, Depends(human)],
        client_id: Annotated[UUID, Depends(tenant)],
        before: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None,
    ) -> ThreadPage:
        async with tx(request.app.state.pool, str(client_id)) as conn:
            rows = await db.q.fetch_all(
                "console/chat_threads.sql",
                (client_id, actor.user_id, before, before, 21),
                conn=conn,
            )
        items = [ThreadOut.model_validate(row) for row in rows[:20]]
        return ThreadPage(items=items, next_cursor=items[-1].sequence if len(rows) > 20 else None)

    @router.post("/conversations/{conversation_id}/archive", status_code=204)
    async def archive(
        conversation_id: UUID,
        request: Request,
        actor: Annotated[ConsoleActor, Depends(human)],
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> None:
        async with tx(request.app.state.pool, str(client_id)) as conn:
            await _thread(conn, client_id, actor, conversation_id)
            changed = await db.q.fetch_one(
                "console/chat_archive.sql", (conversation_id, client_id, actor.user_id), conn=conn
            )
            if changed:
                await audit(conn, actor, client_id, "chat.archived", str(conversation_id))

    @router.post(
        "/conversations/{conversation_id}/turns",
        response_model=QueuedTurn,
        status_code=202,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": SendTurn.model_json_schema()}},
            }
        },
    )
    async def send(
        conversation_id: UUID,
        request: Request,
        actor: Annotated[ConsoleActor, Depends(human)],
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> QueuedTurn:
        payload = await _payload(request, SendTurn)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await enqueue_sandbox_turn(
                conn, client_id, actor.user_id, conversation_id, payload
            )

    @router.get("/conversations/{conversation_id}/turns", response_model=TurnPage)
    async def history(
        conversation_id: UUID,
        request: Request,
        actor: Annotated[ConsoleActor, Depends(human)],
        client_id: Annotated[UUID, Depends(tenant)],
        before: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None,
    ) -> TurnPage:
        async with tx(request.app.state.pool, str(client_id)) as conn:
            await _thread(conn, client_id, actor, conversation_id)
            rows = await db.q.fetch_all(
                "console/chat_turns.sql",
                (client_id, conversation_id, before, before, 51),
                conn=conn,
            )
        items = [TurnOut.model_validate(row) for row in rows[:50]]
        return TurnPage(items=items, next_cursor=items[-1].id if len(rows) > 50 else None)

    return router
