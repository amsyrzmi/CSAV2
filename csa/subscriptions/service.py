"""Subscriptions service: tenant-tx reads and writes; decide_request opens its own tx."""

from __future__ import annotations

import re
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException, Request
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from csa.console.contracts import ConsoleActor
from csa.console.kb_uploads import audit
from csa.core import db
from csa.core.tenancy import tx
from csa.subscriptions.contracts import (
    ActionIn,
    CustomerRef,
    DecideIn,
    PlanIn,
    PlanOut,
    PlanPatch,
    RequestOut,
    RequestPage,
    Status,
    SubscriptionCreateIn,
    SubscriptionDetail,
    SubscriptionEventOut,
    SubscriptionOut,
    SubscriptionPage,
)
from csa.workspace.registry import ProposalRegistry

Conn = AsyncConnection[Any]

PHONE_TERM = re.compile(r"[\d\s+().-]+")


def subscription_out(row: dict[str, Any]) -> SubscriptionOut:
    return SubscriptionOut.model_validate(
        {
            **row,
            "customer": CustomerRef(
                id=row["contact_id"],
                full_name=row["full_name"],
                phone=row["phone"],
                email=row["email"],
            ),
        },
        extra="ignore",
    )


def like_pattern(q: str) -> str:
    """Contains-match pattern for ILIKE with the user's own wildcards made literal."""
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def list_plans(conn: Conn, client_id: UUID, *, include_archived: bool) -> list[PlanOut]:
    rows = await db.q.fetch_all(
        "subscriptions/plans_list.sql",
        {"client_id": client_id, "include_archived": include_archived},
        conn=conn,
    )
    return [PlanOut.model_validate(row) for row in rows]


async def create_plan(conn: Conn, client_id: UUID, actor: ConsoleActor, data: PlanIn) -> PlanOut:
    row = await db.q.fetch_one(
        "subscriptions/plan_insert.sql",
        {"client_id": client_id, **data.model_dump(), "features": Jsonb(data.features)},
        conn=conn,
    )
    if row is None:
        raise HTTPException(409, "plan_exists")
    await audit(conn, actor, client_id, "subscriptions.plan.created", data.code)
    return PlanOut.model_validate(row)


async def update_plan(
    conn: Conn, client_id: UUID, actor: ConsoleActor, code: str, data: PlanPatch
) -> PlanOut:
    changes = data.model_dump()
    if changes["features"] is not None:
        changes["features"] = Jsonb(changes["features"])
    row = await db.q.fetch_one(
        "subscriptions/plan_update.sql",
        {"client_id": client_id, "code": code, **changes},
        conn=conn,
    )
    if row is None:
        raise HTTPException(404, "plan_not_found")
    await audit(conn, actor, client_id, "subscriptions.plan.updated", code)
    return PlanOut.model_validate(row)


async def search_subscriptions(
    conn: Conn,
    client_id: UUID,
    *,
    q: str | None,
    status: Status | None,
    plan: str | None,
    before: UUID | None,
    limit: int,
) -> SubscriptionPage:
    term = q.strip() if q else ""
    # Digits only count as a phone search when the whole term reads like a phone number, so an
    # email or "unit 5" never pulls in customers whose phone happens to contain those digits.
    digits = re.sub(r"\D", "", term)
    if not PHONE_TERM.fullmatch(term) or len(digits) < 3:
        digits = ""
    rows = await db.q.fetch_all(
        "subscriptions/subscriptions_search.sql",
        {
            "client_id": client_id,
            "status": status,
            "plan": plan,
            "like": like_pattern(term) if term else None,
            "digits": digits,
            "before": before,
            "limit": limit + 1,
        },
        conn=conn,
    )
    items = [subscription_out(row) for row in rows[:limit]]
    return SubscriptionPage(items=items, next_cursor=items[-1].id if len(rows) > limit else None)


async def get_subscription_out(
    conn: Conn, client_id: UUID, subscription_id: UUID
) -> SubscriptionOut:
    row = await db.q.fetch_one(
        "subscriptions/subscription_get.sql",
        {"client_id": client_id, "subscription_id": subscription_id},
        conn=conn,
    )
    if row is None:
        raise HTTPException(404, "subscription_not_found")
    return subscription_out(row)


async def get_subscription(
    conn: Conn, client_id: UUID, subscription_id: UUID
) -> SubscriptionDetail:
    subscription = await get_subscription_out(conn, client_id, subscription_id)
    rows = await db.q.fetch_all(
        "subscriptions/subscription_events.sql",
        {"client_id": client_id, "subscription_id": subscription_id},
        conn=conn,
    )
    return SubscriptionDetail(
        subscription=subscription, events=[SubscriptionEventOut.model_validate(row) for row in rows]
    )


async def record_event(
    conn: Conn,
    client_id: UUID,
    subscription_id: UUID,
    *,
    actor: str,
    action: str,
    from_status: str | None,
    to_status: str | None,
    payload: dict[str, Any] | None = None,
    proposal_id: UUID | None = None,
) -> int:
    """Insert one timeline event and return its id."""
    row = await db.q.fetch_one(
        "subscriptions/event_insert.sql",
        {
            "client_id": client_id,
            "subscription_id": subscription_id,
            "actor": actor,
            "action": action,
            "from_status": from_status,
            "to_status": to_status,
            "payload": Jsonb(payload or {}),
            "proposal_id": proposal_id,
        },
        conn=conn,
    )
    if row is None:
        raise RuntimeError("subscription event insert returned no row")
    event_id: int = row["id"]
    return event_id


async def create_subscription(
    conn: Conn, client_id: UUID, actor: ConsoleActor, data: SubscriptionCreateIn
) -> SubscriptionOut:
    plan = await db.q.fetch_one(
        "subscriptions/plan_get.sql", {"client_id": client_id, "code": data.plan_code}, conn=conn
    )
    if plan is None or not plan["is_active"]:
        raise HTTPException(422, "plan_unavailable")
    contact = await db.q.fetch_one(
        "subscriptions/contact_get.sql",
        {"client_id": client_id, "contact_id": data.contact_id},
        conn=conn,
    )
    if contact is None:
        raise HTTPException(404, "contact_not_found")
    created = await db.q.fetch_one(
        "subscriptions/subscription_insert.sql",
        {
            "client_id": client_id,
            "contact_id": data.contact_id,
            "plan_code": data.plan_code,
            "started_at": data.started_at,
            "renews_at": data.renews_at,
        },
        conn=conn,
    )
    if created is None:  # the plan was archived or removed after the check above
        raise HTTPException(422, "plan_unavailable")
    if not created["renews_at_ok"]:
        raise HTTPException(422, "renews_at_before_start")
    subscription_id: UUID = created["id"]
    await record_event(
        conn,
        client_id,
        subscription_id,
        actor=str(actor.user_id),
        action="created",
        from_status=None,
        to_status="active",
        payload={"plan": data.plan_code},
    )
    await audit(conn, actor, client_id, "subscriptions.created", str(subscription_id))
    return await get_subscription_out(conn, client_id, subscription_id)


def request_out(row: dict[str, Any]) -> RequestOut:
    return RequestOut.model_validate(
        {
            **row,
            "customer": CustomerRef(
                id=row["contact_id"],
                full_name=row["full_name"],
                phone=row["phone"],
                email=row["email"],
            ),
        },
        extra="ignore",
    )


async def list_requests(
    conn: Conn,
    client_id: UUID,
    *,
    status: Literal["open", "decided"],
    before: UUID | None,
    limit: int,
) -> RequestPage:
    rows = await db.q.fetch_all(
        "subscriptions/requests_list.sql",
        {"client_id": client_id, "status": status, "before": before, "limit": limit + 1},
        conn=conn,
    )
    items = [request_out(row) for row in rows[:limit]]
    return RequestPage(items=items, next_cursor=items[-1].id if len(rows) > limit else None)


async def get_request_row(conn: Conn, client_id: UUID, request_id: UUID) -> dict[str, Any]:
    """The request with its decision; another tenant's id is invisible under RLS."""
    row: dict[str, Any] | None = await db.q.fetch_one(
        "subscriptions/request_get.sql",
        {"client_id": client_id, "request_id": request_id},
        conn=conn,
    )
    if row is None:
        raise HTTPException(404, "request_not_found")
    return row


def approval_action(row: dict[str, Any], data: DecideIn) -> ActionIn:
    """The subscription action an approval of this request proposes (422 when it names none)."""
    kind = row["action"]
    if data.resume_at is not None and kind != "request_pause":
        raise HTTPException(422, "resume_at_applies_to_pause_only")
    reason = (row["reason"] or "")[:500] or None
    if kind == "request_pause":
        return ActionIn(action="pause", resume_at=data.resume_at, reason=reason)
    if kind == "request_cancel":
        return ActionIn(action="cancel_at_period_end", reason=reason)
    if not row["plan_code"]:
        raise HTTPException(422, "request_has_no_plan")
    return ActionIn(action="change_plan", plan_code=row["plan_code"], reason=reason)


async def _decline(
    conn: Conn, actor: ConsoleActor, client_id: UUID, request_id: UUID, data: DecideIn
) -> RequestOut:
    inserted = await db.q.fetch_one(
        "subscriptions/decision_insert.sql",
        {
            "client_id": client_id,
            "request_id": request_id,
            "decision": "declined",
            "note": data.note,
            "decided_by": actor.user_id,
            "proposal_id": None,
        },
        conn=conn,
    )
    if inserted is None:  # an approval or another decline committed first
        raise HTTPException(409, "request_decided")
    await audit(conn, actor, client_id, "subscriptions.request.declined", str(request_id))
    return request_out(await get_request_row(conn, client_id, request_id))


async def decide_request(
    pool: AsyncConnectionPool,
    request: Request,
    actor: ConsoleActor,
    client_id: UUID,
    request_id: UUID,
    data: DecideIn,
) -> RequestOut:
    """Decline a staged request, or approve it by proposing and confirming its action.

    An approval's proposal carries the request id, and the executor records the approved
    decision in the same transaction as the subscription change (and audits it there). The
    decision's primary key then makes approve, decline and a second approve mutually
    exclusive: the first to commit wins and a loser changes nothing and gets 409
    ``request_decided``. No transaction spans the registry calls, and each approval is its own
    ordinary proposal, so any staff member can approve after another's attempt failed.
    """
    async with tx(pool, str(client_id)) as conn:
        row = await get_request_row(conn, client_id, request_id)
        if row["decision"] is not None:
            raise HTTPException(409, "request_decided")
        if data.decision == "decline":
            return await _decline(conn, actor, client_id, request_id, data)
        action = approval_action(row, data)
        if row["subscription_id"] is None:
            raise HTTPException(422, "request_has_no_subscription")
        subscription = await db.q.fetch_one(
            "subscriptions/subscription_get.sql",
            {"client_id": client_id, "subscription_id": row["subscription_id"]},
            conn=conn,
        )
        if subscription is None or subscription["contact_id"] != row["contact_id"]:
            raise HTTPException(422, "request_subscription_mismatch")
    registry = getattr(request.app.state, "proposal_registry", None)
    if not isinstance(registry, ProposalRegistry):
        raise HTTPException(503, "proposal_registry_unavailable")
    payload = {
        "subscription_id": str(row["subscription_id"]),
        **action.model_dump(mode="json", exclude_none=True),
        "request_id": str(request_id),
        **({"decision_note": data.note} if data.note is not None else {}),
    }
    proposal = await registry.create(
        pool,
        request,
        actor,
        client_id,
        "subscription_action",
        payload,
        thread_id=None,
        origin="tab",
    )
    done = await registry.confirm(pool, request, actor, client_id, proposal.id)
    if done.status != "executed":
        raise HTTPException(409, "proposal_not_executed")
    async with tx(pool, str(client_id)) as conn:
        stored = await get_request_row(conn, client_id, request_id)
    if stored["decision"] != "approved" or stored["proposal_id"] != done.id:
        raise RuntimeError(f"request {request_id} has no approval recorded for proposal {done.id}")
    return request_out(stored)
