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
from pydantic import BaseModel, ConfigDict

from csa.core import db
from csa.core.pii import redact
from csa.core.tenancy import tx
from csa.decide.grounding import ungrounded
from csa.notify.notifier import notify
from csa.staff.ownership import automation_allowed, lock_conversation

logger = structlog.get_logger(__name__)

FALLBACK_TEXT = "Thanks for your message. A team member will follow up with you shortly."
_PREVIEW_CHARS = 800

MAX_EDIT_CHARS = 2000
EDIT_PROMPT_TEXT = (
    "Send the replacement reply as your next message (in a group, reply to the alert)."
)

Decision = Literal["approved", "edited", "rejected", "expired"]


class EditTextError(ValueError):
    """The staff replacement text cannot be sent to a customer."""


def validate_edit_text(raw: str) -> str:
    """Return the trimmed replacement text or raise ``EditTextError`` with a staff-safe reason."""
    text = raw.strip()
    if not text:
        raise EditTextError("The replacement text is empty. Send the reply text.")
    if text.startswith("/"):
        raise EditTextError("Commands cannot be sent to the customer. Send the reply text.")
    if len(text) > MAX_EDIT_CHARS:
        raise EditTextError(
            f"The replacement text is too long ({len(text)} of {MAX_EDIT_CHARS} characters)."
        )
    if any((ord(ch) < 32 and ch not in "\n\t") or ord(ch) == 127 for ch in text):
        raise EditTextError("The replacement text contains control characters.")
    return text


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


class ReplyDecisionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["applied", "already_handled", "expired", "not_found"]
    status: str | None = None
    queued: bool = False
    warnings: list[str] = []


async def _locked_approval(
    conn: psycopg.AsyncConnection[Any], client_id: UUID, approval_id: int
) -> dict[str, Any] | None:
    reference = await db.q.fetch_one(
        "approval/get_reference.sql", (client_id, approval_id), conn=conn
    )
    if reference is None:
        return None
    await lock_conversation(conn, client_id, reference["conversation_id"])
    return await db.q.fetch_one("approval/get_approval.sql", (client_id, approval_id), conn=conn)


async def transition_reply(
    conn: psycopg.AsyncConnection[Any],
    *,
    client_id: UUID,
    approval_id: int,
    decision: Decision,
    actor: str,
    text: str | None = None,
) -> ReplyDecisionResult:
    """Shared transition for authenticated adapters; audit/outbox commit with the caller."""
    final_text = validate_edit_text(text or "") if decision == "edited" else None
    approval = await _locked_approval(conn, client_id, approval_id)
    if approval is None:
        return ReplyDecisionResult(outcome="not_found")
    if approval["status"] != "pending":
        return ReplyDecisionResult(outcome="already_handled", status=approval["status"])
    if approval["expires_at"] <= datetime.now(UTC):
        queued = await _settle(conn, approval, "expired", "system")
        return ReplyDecisionResult(outcome="expired", status="expired", queued=queued)
    queued = await _settle(conn, approval, decision, actor, final_text)
    warnings: list[str] = []
    if final_text is not None:
        sources = [approval["draft"]] + [
            row["redacted_body"] or ""
            for row in await db.q.fetch_all(
                "approval/recent_customer_text.sql",
                (client_id, approval["conversation_id"]),
                conn=conn,
            )
        ]
        warnings = ungrounded(final_text, sources)[:5]
    return ReplyDecisionResult(outcome="applied", status=decision, queued=queued, warnings=warnings)


def _staff_result(result: ReplyDecisionResult) -> str:
    if result.outcome == "not_found":
        return "This action is no longer valid."
    if result.outcome == "already_handled":
        return "Already handled."
    if result.outcome == "expired":
        return "This approval has expired."
    if result.status == "edited":
        message = "Edited reply queued." if result.queued else "Edited. Reply suppressed."
        if result.warnings:
            message += " Warning: not found in the draft or customer messages: " + ", ".join(
                result.warnings
            )
        return message
    if result.status == "approved":
        return "Approved. Reply queued." if result.queued else "Approved. Reply suppressed."
    return "Rejected. Follow-up reply queued." if result.queued else "Rejected. Reply suppressed."


async def decide_reply(
    pool: AsyncConnectionPool,
    *,
    client_id: UUID,
    approval_id: int,
    approve: bool,
    actor: str,
) -> str:
    async with tx(pool, str(client_id)) as conn:
        result = await transition_reply(
            conn,
            client_id=client_id,
            approval_id=approval_id,
            decision="approved" if approve else "rejected",
            actor=actor,
        )
    return _staff_result(result)


async def begin_edit(
    pool: AsyncConnectionPool,
    *,
    client_id: UUID,
    approval_id: int,
    staff_action_id: UUID,
    source_message_id: int | None = None,
) -> str:
    """Arm the Edit action; the next message (or reply to the alert, in a group) is the reply."""
    async with tx(pool, str(client_id)) as conn:
        approval = await _locked_approval(conn, client_id, approval_id)
        if approval is None:
            return "This action is no longer valid."
        if approval["status"] != "pending":
            return "Already handled."
        if approval["expires_at"] <= datetime.now(UTC):
            await _settle(conn, approval, "expired", "system")
            return "This approval has expired."
        armed = await db.q.fetch_one(
            "staff/await_edit.sql", (source_message_id, client_id, staff_action_id), conn=conn
        )
        if armed is not None:
            await db.q.execute(
                "staff/disarm_other_edits.sql",
                (client_id, client_id, staff_action_id, staff_action_id),
                conn=conn,
            )
    return EDIT_PROMPT_TEXT


async def edit_reply(
    pool: AsyncConnectionPool,
    *,
    client_id: UUID,
    approval_id: int,
    text: str,
    actor: str,
) -> str:
    async with tx(pool, str(client_id)) as conn:
        result = await transition_reply(
            conn,
            client_id=client_id,
            approval_id=approval_id,
            decision="edited",
            actor=actor,
            text=text,
        )
    return _staff_result(result)


async def expire_due(owner_pool: AsyncConnectionPool, app_pool: AsyncConnectionPool) -> int:
    """Settle every pending approval past its expiry with the safe fallback reply."""
    async with owner_pool.connection() as conn:
        due = await db.q.fetch_all("approval/list_expired.sql", conn=conn)
    settled = 0
    for item in due:
        async with tx(app_pool, str(item["client_id"])) as conn:
            approval = await _locked_approval(conn, item["client_id"], item["id"])
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
    edited_text: str | None = None,
) -> bool:
    client_id = approval["client_id"]
    send_draft = outcome == "approved"
    if outcome == "edited":
        if edited_text is None:
            raise ValueError("edited_text_required")
        text = edited_text
    else:
        text = approval["draft"] if send_draft else FALLBACK_TEXT
    decided = await db.q.fetch_one(
        "approval/decide_approval.sql",
        (outcome, actor, text, client_id, approval["id"]),
        conn=conn,
    )
    if decided is None:
        return False
    await db.q.execute(
        "approval/insert_audit.sql",
        (
            client_id,
            approval["id"],
            outcome,
            actor,
            Jsonb({"draft": approval["draft"]}) if outcome == "edited" else None,
            Jsonb({"final_text": text}),
        ),
        conn=conn,
    )
    await db.q.execute("staff/close_approval_actions.sql", (client_id, approval["id"]), conn=conn)
    if outcome in {"rejected", "expired"} and not await automation_allowed(
        conn, client_id, approval["conversation_id"], approval["trace_id"]
    ):
        return False
    message = await db.q.fetch_one(
        "worker/insert_message.sql",
        (
            client_id,
            approval["conversation_id"],
            "outbound",
            "assistant",
            text,
            redact(text),
            Jsonb([]),
            False,
            f"appr:{approval['id']}:{outcome}",
            approval["trace_id"],
        ),
        conn=conn,
    )
    if message is None:
        return False
    if outcome in {"approved", "edited"}:
        await db.q.execute("staff/set_reviewed.sql", (client_id, message["id"]), conn=conn)
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

    return True
