"""Staff button taps: authenticate the sender, run order approval, tell the customer (LG05)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import structlog
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from csa.agents.base import AgentContext
from csa.agents.order_approval.agent import ApprovalInput, OrderApprovalAgent
from csa.approval.service import (
    EDIT_PROMPT_TEXT,
    EditTextError,
    begin_edit,
    decide_reply,
    edit_reply,
)
from csa.contracts.callbacks import PendingActionStore
from csa.core import db
from csa.core.pii import redact
from csa.core.tenancy import tx

logger = structlog.get_logger(__name__)

_CUSTOMER_TEXT = {
    "approve": "Good news! Your order {order_id} has been approved and is being prepared.",
    "reject": "Sorry, we could not accept your order {order_id}. Our team will be in touch.",
}


NOT_AUTHORISED_TEXT = "You are not authorised for this action."


async def handle_staff_action(
    pool: AsyncConnectionPool,
    *,
    client_id: UUID,
    endpoint_id: UUID,
    chat_id: str,
    staff_action_id: UUID,
    trace_id: UUID,
    source_message_id: int | None = None,
) -> str:
    """Process one staff tap and return the short text shown to the tapping staff member."""
    async with tx(pool, str(client_id)) as conn:
        action = await db.q.fetch_one(
            "staff/get_action.sql", (client_id, staff_action_id), conn=conn
        )
    if action is None:
        logger.warning("staff_callback_unknown_action", client_id=str(client_id))
        return "This action is no longer valid."
    if not action["is_enabled"] or action["address"] != chat_id:
        logger.warning(
            "staff_callback_unauthorised",
            client_id=str(client_id),
            staff_action_id=str(staff_action_id),
        )
        return NOT_AUTHORISED_TEXT
    if action["action"] == "edit_reply" and action["status"] == "awaiting":
        return EDIT_PROMPT_TEXT
    if action["status"] != "open":
        return "Already handled."
    if action["expires_at"] <= datetime.now(UTC):
        return "This action has expired."

    if action["action"] == "resume":
        return await _resume_conversation(pool, client_id, action)

    if action["action"] == "edit_reply":
        return await begin_edit(
            pool,
            client_id=client_id,
            approval_id=action["approval_id"],
            staff_action_id=staff_action_id,
            source_message_id=source_message_id,
        )

    if action["action"] in {"approve_reply", "reject_reply"}:
        return await decide_reply(
            pool,
            client_id=client_id,
            approval_id=action["approval_id"],
            approve=action["action"] == "approve_reply",
            actor=f"staff_chat:{chat_id}",
        )

    order_id = str(action["order_id"])
    async with tx(pool, str(client_id)) as conn:
        target = await db.q.fetch_one(
            "staff/get_order_conversation.sql", (client_id, order_id), conn=conn
        )
        order = await db.q.fetch_one("orders/get_order_staff.sql", (client_id, order_id), conn=conn)
    if order is None or order["contact_id"] is None:
        return "Order not found."

    ctx = AgentContext(
        client_id=client_id,
        contact_id=order["contact_id"],
        trace_id=trace_id,
        conversation_id=target["conversation_id"] if target else None,
        idempotency_key=f"staff:{staff_action_id}",
        channel="telegram",
        pool=pool,
        pending_actions=PendingActionStore(pool),
    )
    result = await OrderApprovalAgent().run(
        ctx,
        ApprovalInput(
            action=action["action"], order_id=order_id, staff_target_id=action["target_id"]
        ),
    )
    if not result.ok:
        code = result.error.code if result.error else "unknown"
        logger.warning("staff_callback_failed", order_id=order_id, code=code)
        return f"Could not apply: {code}."

    async with tx(pool, str(client_id)) as conn:
        await db.q.fetch_one("staff/use_action.sql", (client_id, order_id), conn=conn)
        if target is not None:
            text = _CUSTOMER_TEXT[action["action"]].format(order_id=order_id)
            key = f"staff:{order_id}:{action['action']}"
            message = await db.q.fetch_one(
                "worker/insert_message.sql",
                (
                    client_id,
                    target["conversation_id"],
                    "outbound",
                    "assistant",
                    text,
                    redact(text),
                    Jsonb([]),
                    False,
                    key,
                    trace_id,
                ),
                conn=conn,
            )
            if message is not None:
                await db.q.fetch_one(
                    "worker/insert_delivery.sql",
                    (client_id, endpoint_id, trace_id, target["conversation_id"], message["id"]),
                    conn=conn,
                )
    return "Approved." if action["action"] == "approve" else "Rejected."


async def submit_staff_edit(
    pool: AsyncConnectionPool,
    *,
    client_id: UUID,
    chat_id: str,
    text: str,
    reply_to_message_id: int | None = None,
) -> str:
    """Apply the staff replacement text; invalid text keeps the Edit armed for a retry."""
    async with tx(pool, str(client_id)) as conn:
        action = await db.q.fetch_one(
            "staff/get_awaiting_edit.sql",
            (client_id, reply_to_message_id, reply_to_message_id, chat_id),
            conn=conn,
        )
    if action is None:
        return "No edit is waiting for a reply."
    try:
        return await edit_reply(
            pool,
            client_id=client_id,
            approval_id=action["approval_id"],
            text=text,
            actor=f"staff_chat:{chat_id}",
        )
    except EditTextError as exc:
        return str(exc)


async def _resume_conversation(
    pool: AsyncConnectionPool, client_id: UUID, action: dict[str, object]
) -> str:
    async with tx(pool, str(client_id)) as conn:
        released = await db.q.fetch_one(
            "escalation/release_conversation.sql",
            (action["conversation_id"], client_id),
            conn=conn,
        )
        await db.q.fetch_one("staff/use_action_by_id.sql", (client_id, action["id"]), conn=conn)
    return "AI resumed." if released else "Conversation was already released."
