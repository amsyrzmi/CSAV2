"""Subscriptions service: every function runs on a connection inside the caller's tenant tx."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from csa.console.contracts import ConsoleActor
from csa.console.kb_uploads import audit
from csa.core import db
from csa.subscriptions.contracts import (
    CustomerRef,
    PlanIn,
    PlanOut,
    PlanPatch,
    Status,
    SubscriptionCreateIn,
    SubscriptionDetail,
    SubscriptionEventOut,
    SubscriptionOut,
    SubscriptionPage,
)

Conn = AsyncConnection[Any]


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
    rows = await db.q.fetch_all(
        "subscriptions/subscriptions_search.sql",
        {
            "client_id": client_id,
            "status": status,
            "plan": plan,
            "like": like_pattern(term) if term else None,
            "digits": re.sub(r"\D", "", term),
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
) -> None:
    await db.q.execute(
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
