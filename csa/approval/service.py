"""Human review of held AI replies: hold, decide and expire (LG07 §4).

A held reply lives in ``approvals`` (draft, buttons, expiry). The turn finishes without sending;
a staff tap or the expiry sweep later writes the customer message and its delivery row in one
transaction, so a reply is released at most once.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import psycopg
import structlog
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from csa.core import db
from csa.core.tenancy import tx
from csa.notify.notifier import notify

logger = structlog.get_logger(__name__)

FALLBACK_TEXT = "Thanks for your message. A team member will follow up with you shortly."
_PREVIEW_CHARS = 800

Decision = Literal["approved", "rejected", "expired"]


async def hold_reply(
    conn: psycopg.AsyncConnection[Any],
    *,
    client_id: UUID,
    conversation_id: UUID,
    trace_id: UUID,
    draft: str,
    reason: str,
    risk: float,
    ui: dict[str, Any],
    ttl: timedelta,
) -> int:
    """Persist the draft and queue a staff alert; replaying the same trace changes nothing."""
    row = await db.q.fetch_one(
        "approval/upsert_approval.sql",
        (
            client_id,
            conversation_id,
            trace_id,
            draft,
            reason[:500],
            risk,
            Jsonb(ui),
            datetime.now(UTC) + ttl,
        ),
        conn=conn,
    )
    if row is None:
        raise RuntimeError("approval_not_created")
    await notify(
        conn,
        client_id=client_id,
        event="approval_needed",
        priority="normal",
        payload={
            "approval_id": row["id"],
            "text": draft[:_PREVIEW_CHARS],
            "reason": reason,
        },
        dedupe_key=f"reply:{trace_id}",
    )
    return int(row["id"])


async def decide_reply(
    pool: AsyncConnectionPool,
    *,
    client_id: UUID,
    approval_id: int,
    approve: bool,
    actor: str,
) -> str:
    """Apply a staff decision and return the text shown to the tapping staff member."""
    async with tx(pool, str(client_id)) as conn:
        approval = await db.q.fetch_one(
            "approval/get_approval.sql", (client_id, approval_id), conn=conn
        )
        if approval is None:
            return "This action is no longer valid."
        if approval["status"] != "pending":
            return "Already handled."
        if approval["expires_at"] <= datetime.now(UTC):
            await _settle(conn, approval, "expired", "system")
            return "This approval has expired."
        outcome: Decision = "approved" if approve else "rejected"
        await _settle(conn, approval, outcome, actor)
    return (
        "Approved. Reply sent."
        if approve
        else "Rejected. Customer told a team member will follow up."
    )


async def expire_due(owner_pool: AsyncConnectionPool, app_pool: AsyncConnectionPool) -> int:
    """Settle every pending approval past its expiry with the safe fallback reply."""
    async with owner_pool.connection() as conn:
        due = await db.q.fetch_all("approval/list_expired.sql", conn=conn)
    settled = 0
    for item in due:
        async with tx(app_pool, str(item["client_id"])) as conn:
            approval = await db.q.fetch_one(
                "approval/get_approval.sql", (item["client_id"], item["id"]), conn=conn
            )
            if approval is None or approval["status"] != "pending":
                continue
            await _settle(conn, approval, "expired", "system")
            settled += 1
    return settled


async def _settle(
    conn: psycopg.AsyncConnection[Any],
    approval: dict[str, Any],
    outcome: Decision,
    actor: str,
) -> None:
    client_id = approval["client_id"]
    send_draft = outcome == "approved"
    text = approval["draft"] if send_draft else FALLBACK_TEXT
    decided = await db.q.fetch_one(
        "approval/decide_approval.sql",
        (outcome, actor, text, client_id, approval["id"]),
        conn=conn,
    )
    if decided is None:
        return
    await db.q.execute(
        "approval/insert_audit.sql",
        (client_id, approval["id"], outcome, actor, Jsonb({"final_text": text})),
        conn=conn,
    )
    await db.q.execute("staff/close_approval_actions.sql", (client_id, approval["id"]), conn=conn)
    message = await db.q.fetch_one(
        "worker/insert_message.sql",
        (
            client_id,
            approval["conversation_id"],
            "outbound",
            "assistant",
            text,
            text,
            Jsonb([]),
            False,
            f"appr:{approval['id']}:{outcome}",
            approval["trace_id"],
        ),
        conn=conn,
    )
    if message is None:
        return
    ui = approval["ui"] if send_draft else {}
    if ui.get("confirmation") or ui.get("options"):
        await db.q.fetch_one(
            "worker/set_message_ui.sql", (Jsonb(ui), message["id"], client_id), conn=conn
        )
    endpoint = await db.q.fetch_one(
        "turn/get_ingress_endpoint.sql", (approval["trace_id"], client_id), conn=conn
    )
    if endpoint is None:
        raise RuntimeError("ingress_endpoint_not_found")
    await db.q.fetch_one(
        "worker/insert_delivery.sql",
        (
            client_id,
            endpoint["endpoint_id"],
            approval["trace_id"],
            approval["conversation_id"],
            message["id"],
        ),
        conn=conn,
    )
    logger.info("approval_settled", approval_id=approval["id"], outcome=outcome)
