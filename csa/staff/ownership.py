"""Shared conversation lock and deterministic automation authority for M5."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from csa.core import db


async def lock_conversation(
    conn: AsyncConnection[Any], client_id: UUID, conversation_id: UUID
) -> dict[str, Any]:
    row = await db.q.fetch_one(
        "staff/lock_conversation.sql", (client_id, conversation_id), conn=conn
    )
    if row is None:
        raise RuntimeError("conversation_not_found")
    return row


async def automation_allowed(
    conn: AsyncConnection[Any],
    client_id: UUID,
    conversation_id: UUID,
    trace_id: UUID | None,
    *,
    handoff: bool = False,
) -> bool:
    """Lock the conversation and check automation authority through transaction completion."""
    row = await db.q.fetch_one(
        "staff/automation_allowed.sql",
        (handoff, trace_id, trace_id, client_id, conversation_id),
        conn=conn,
    )
    if row is None:
        raise RuntimeError("conversation_not_found")
    return row["allowed"] is True
