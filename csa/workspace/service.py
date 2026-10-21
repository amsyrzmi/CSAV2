"""Tenant-scoped workspace thread persistence and deterministic A1 actions."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from csa.agents.base import utc_now
from csa.console.business import BusinessOrder, get_local_order, require_business_read
from csa.console.contracts import ConsoleActor
from csa.core import db
from csa.core.pii import redact
from csa.workspace.contracts import (
    MAX_WORK_TTL,
    GuidanceProposal,
    GuidanceResult,
    LocalOrderSummary,
    OrderPageHint,
    OrderReadProposal,
    OrderReadResult,
    ResolvedWorkspaceAuthority,
    VerifiedWorkspacePrincipal,
    WorkspaceActionResult,
    WorkspaceAllowed,
    WorkspaceDeniedResult,
    WorkspaceThreadOut,
    WorkspaceTurnInput,
    WorkspaceTurnOut,
    WorkspaceWorkBinding,
    validate_workspace_execution,
)

_RESULT = TypeAdapter(WorkspaceActionResult)
_GUIDANCE_KEYS: dict[
    str,
    Literal[
        "workspace_home_help",
        "business_orders_help",
        "business_order_help",
    ],
] = {
    "workspace_home": "workspace_home_help",
    "business_orders": "business_orders_help",
    "business_order": "business_order_help",
}


def _stored_result(value: Any) -> WorkspaceActionResult:
    return _RESULT.validate_json(json.dumps(value, allow_nan=False))


class WorkspaceConflict(ValueError):
    """A same-key payload or resource version no longer matches."""


class WorkspaceNotFound(LookupError):
    """Thread or resource is missing in the current tenant/owner scope."""


def _fingerprint(turn: WorkspaceTurnInput) -> str:
    payload = json.dumps(
        turn.model_dump(mode="json", exclude_none=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _page_and_proposal(
    turn: WorkspaceTurnInput,
) -> tuple[str, GuidanceProposal | OrderReadProposal]:
    hint = turn.page_hint
    if isinstance(hint, OrderPageHint):
        return hint.page, OrderReadProposal(action="local_order_read", resource=hint.entity)
    page = hint.page if hint is not None else "workspace_home"
    return page, GuidanceProposal(action="page_guidance", page=page)


async def lock_request(
    conn: AsyncConnection[Any], client_id: UUID, owner_user_id: UUID, key: UUID
) -> None:
    await db.q.fetch_one(
        "workspace/lock_request.sql",
        (f"workspace:{client_id}:{owner_user_id}:{key}",),
        conn=conn,
    )


async def create_thread(
    conn: AsyncConnection[Any], client_id: UUID, owner_user_id: UUID, request_key: UUID
) -> WorkspaceThreadOut:
    await lock_request(conn, client_id, owner_user_id, request_key)
    row = await db.q.fetch_one(
        "workspace/thread_by_key.sql", (client_id, owner_user_id, request_key), conn=conn
    )
    duplicate = row is not None
    if row is None:
        row = await db.q.fetch_one(
            "workspace/thread_insert.sql", (client_id, owner_user_id, request_key), conn=conn
        )
        if row is None:
            raise RuntimeError("workspace_thread_insert_returned_no_row")
        await db.q.execute(
            "console/audit.sql",
            (
                owner_user_id,
                client_id,
                "workspace.thread.created",
                str(row["id"]),
                Jsonb({"thread_id": str(row["id"])}),
            ),
            conn=conn,
        )
    return WorkspaceThreadOut.model_validate({**row, "duplicate": duplicate})


async def list_threads(
    conn: AsyncConnection[Any], client_id: UUID, owner_user_id: UUID, before: int | None
) -> tuple[list[WorkspaceThreadOut], int | None]:
    rows = await db.q.fetch_all(
        "workspace/threads.sql",
        (client_id, owner_user_id, before, before, 21),
        conn=conn,
    )
    items = [WorkspaceThreadOut.model_validate(row) for row in rows[:20]]
    return items, items[-1].sequence if len(rows) > 20 and items else None


async def get_thread(
    conn: AsyncConnection[Any], client_id: UUID, owner_user_id: UUID, thread_id: UUID
) -> WorkspaceThreadOut:
    row = await db.q.fetch_one(
        "workspace/thread_get.sql", (client_id, owner_user_id, thread_id), conn=conn
    )
    if row is None:
        raise WorkspaceNotFound("workspace conversation not found")
    return WorkspaceThreadOut.model_validate(row)


async def _fresh_result(
    conn: AsyncConnection[Any],
    actor: ConsoleActor,
    client_id: UUID,
    thread: WorkspaceThreadOut,
    turn: WorkspaceTurnInput,
    *,
    now: datetime,
) -> tuple[str, GuidanceProposal | OrderReadProposal, WorkspaceActionResult]:
    page, proposal = _page_and_proposal(turn)
    require_business_read(actor, client_id)
    resource_updated_at = None
    order: BusinessOrder | None = None
    if isinstance(proposal, OrderReadProposal):
        order = await get_local_order(conn, client_id, proposal.resource.order_id)
        resource_updated_at = order.updated_at if order is not None else None
        if order is None:
            return page, proposal, WorkspaceDeniedResult(code="resource_unavailable")
    binding = WorkspaceWorkBinding(
        work_id=uuid4(),
        thread_id=thread.id,
        owner_user_id=actor.user_id,
        session_id=actor.session_id,
        tenant_id=client_id,
        request_key=turn.request_key,
        created_at=now,
        expires_at=now + MAX_WORK_TTL,
        proposal=proposal,
        expected_resource_updated_at=resource_updated_at,
    )
    principal = VerifiedWorkspacePrincipal(
        user_id=actor.user_id,
        session_id=actor.session_id,
        tenant_id=client_id,
        role=actor.role.value if actor.role is not None else "read_only",
        verified_at=now,
    )
    authority = ResolvedWorkspaceAuthority(
        work_id=binding.work_id,
        thread_id=thread.id,
        tenant_id=client_id,
        user_id=actor.user_id,
        session_id=actor.session_id,
        proposal=proposal,
        checked_at=now,
        capability_enabled=True,
        action_allowed=True,
        resource_allowed=not isinstance(proposal, OrderReadProposal) or order is not None,
        resource_updated_at=resource_updated_at,
    )
    decision = validate_workspace_execution(binding, principal, authority, now=now)
    if not isinstance(decision, WorkspaceAllowed):
        return page, proposal, decision
    if isinstance(proposal, GuidanceProposal):
        return (
            page,
            proposal,
            GuidanceResult(
                page=proposal.page,
                instruction_key=_GUIDANCE_KEYS[proposal.page],
                as_of=now,
            ),
        )
    if order is None:
        return page, proposal, WorkspaceDeniedResult(code="resource_unavailable")
    return (
        page,
        proposal,
        OrderReadResult(
            order=LocalOrderSummary(
                resource=proposal.resource,
                status=order.status,
                payment_status=order.payment_status,
                total=order.total,
                currency=order.currency,
                channel=order.channel,
                external_order_id=order.external_order_id,
                external_shop_id=order.external_shop_id,
                tracking_no=order.tracking_no,
                carrier=order.carrier,
                placed_at=order.placed_at,
                shipped_at=order.shipped_at,
                created_at=order.created_at,
                updated_at=order.updated_at,
            ),
            as_of=now,
        ),
    )


async def create_turn(
    conn: AsyncConnection[Any],
    actor: ConsoleActor,
    client_id: UUID,
    thread_id: UUID,
    turn: WorkspaceTurnInput,
    *,
    now: datetime | None = None,
) -> WorkspaceTurnOut:
    timestamp = now or utc_now()
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("workspace clock must be timezone-aware")
    timestamp = timestamp.astimezone(UTC)
    await lock_request(conn, client_id, actor.user_id, turn.request_key)
    thread = await get_thread(conn, client_id, actor.user_id, thread_id)
    fingerprint = _fingerprint(turn)
    existing = await db.q.fetch_one(
        "workspace/turn_by_key.sql",
        (client_id, thread_id, actor.user_id, turn.request_key),
        conn=conn,
    )
    if existing is not None and existing["fingerprint"] != fingerprint:
        raise WorkspaceConflict("request key was already used for different content")
    page, proposal, result = await _fresh_result(
        conn, actor, client_id, thread, turn, now=timestamp
    )
    if existing is not None:
        previous = _stored_result(existing["result"])
        if isinstance(previous, OrderReadResult):
            if not isinstance(result, OrderReadResult) or (
                previous.order.updated_at != result.order.updated_at
            ):
                raise WorkspaceConflict("resource changed; submit a new request")
        return WorkspaceTurnOut.model_validate(
            {
                "id": existing["id"],
                "request_key": existing["request_key"],
                "body": existing["body"],
                "result": previous,
                "created_at": existing["created_at"],
                "duplicate": True,
            }
        )
    body = redact(turn.body)
    if not body.strip() or len(body) > 8000:
        raise ValueError("workspace text could not be safely persisted")
    resource_id = proposal.resource.order_id if isinstance(proposal, OrderReadProposal) else None
    row = await db.q.fetch_one(
        "workspace/turn_insert.sql",
        (
            client_id,
            thread_id,
            actor.user_id,
            actor.session_id,
            turn.request_key,
            fingerprint,
            body,
            proposal.action,
            page,
            resource_id,
            Jsonb(result.model_dump(mode="json")),
        ),
        conn=conn,
    )
    if row is None:
        raise RuntimeError("workspace_turn_insert_returned_no_row")
    await db.q.execute(
        "console/audit.sql",
        (
            actor.user_id,
            client_id,
            "workspace.turn.completed",
            str(thread_id),
            Jsonb({"thread_id": str(thread_id), "action": proposal.action}),
        ),
        conn=conn,
    )
    row["result"] = result
    return WorkspaceTurnOut.model_validate({**row, "duplicate": False})


async def list_turns(
    conn: AsyncConnection[Any],
    client_id: UUID,
    owner_user_id: UUID,
    thread_id: UUID,
    before: int | None,
) -> tuple[list[WorkspaceTurnOut], int | None]:
    await get_thread(conn, client_id, owner_user_id, thread_id)
    rows = await db.q.fetch_all(
        "workspace/turns.sql",
        (client_id, thread_id, owner_user_id, before, before, 51),
        conn=conn,
    )
    items = []
    for row in rows[:50]:
        row["result"] = _stored_result(row["result"])
        items.append(WorkspaceTurnOut.model_validate(row))
    return items, items[-1].id if len(rows) > 50 and items else None
