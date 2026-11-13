"""The customer bot's sign-up (`request_signup`): collect, read back, file on a typed yes.

One thing is asked per turn: the plan, then the full name, then the phone. A phone the channel
verified is used and never asked; otherwise Telegram offers the "share my number" button once and
any channel takes a typed number, which stays on the request only (never on the contact, ruling
D). With everything collected the sign-up is read back and nothing is filed; it is filed only when
the planner read this message as a yes (`confirmed`) and the chat's latest assistant message was
that very read-back of these same details.

The planner sees a Malaysian number only as a placeholder, which resolves only in the message that
holds it, so what each turn collected is kept in a short-lived draft for the next: a
`pending_actions` row of this chat (`signup_draft`), never shown as a button. Every read is of the
chat's own contact; a typed number is never looked up against other contacts.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID

import structlog
from fastapi import HTTPException
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

from csa.agents.base import AgentContext
from csa.agents.staged.models import SubscriptionInput
from csa.agents.staged.subscription_facts import (
    SUBSCRIPTION_SOURCE,
    VERIFIED_PHONE,
    SignupKind,
    plan_facts,
    signup_fact,
)
from csa.contacts.linking import live_contact
from csa.contracts.facts import Fact
from csa.core import db
from csa.core.phone import normalise_my_phone
from csa.payments.settings import get_settings
from csa.subscriptions.signup import file_signup

logger = structlog.get_logger(__name__)

Conn = AsyncConnection[Any]

DRAFT_ACTION = "signup_draft"
# Long enough to find a name or a number; a draft older than this starts the sign-up again.
DRAFT_LIFE = timedelta(hours=1)
# In the read-back key, a phone the channel verified (its digits are never read back).
_VERIFIED_KEY = "verified"


class Draft(BaseModel):
    """What this chat's sign-up has collected so far (the payload of its draft row)."""

    model_config = ConfigDict(extra="ignore")

    plan_code: str | None = None
    full_name: str | None = None
    phone: str | None = None  # typed, normalised; never the contact's own
    noted: bool = False  # the data note was told
    asked_share: bool = False  # Telegram's share button was offered
    read_back: list[str] | None = None  # [plan code, name, phone key] as read back
    read_back_trace: str | None = None  # the turn that read it back


@dataclass(frozen=True)
class SignupTurn:
    """The facts of a sign-up turn, and whether Telegram should offer the share button."""

    facts: list[Fact]
    request_phone: bool = False


@dataclass
class _Reply:
    kinds: list[tuple[SignupKind, dict[str, Any]]] = field(default_factory=list)

    def add(self, kind: SignupKind, **fields: Any) -> _Reply:
        self.kinds.append((kind, fields))
        return self

    def facts(self, extra: Sequence[Fact] = ()) -> list[Fact]:
        numbered = [
            signup_fact(kind, n, **fields) for n, (kind, fields) in enumerate(self.kinds, start=1)
        ]
        return [*numbered, *extra]


async def request_signup(conn: Conn, ctx: AgentContext, inp: SubscriptionInput) -> SignupTurn:
    """One turn of a sign-up; see the module docstring."""
    # A contact merged by a link since this message arrived files for the contact it lives on now.
    contact_id = await live_contact(conn, ctx.client_id, ctx.contact_id)
    draft = await _load(conn, ctx, contact_id)
    named = inp.plan_code or draft.plan_code
    name = inp.full_name or draft.full_name
    plan = await _plan(conn, ctx.client_id, named) if named else None
    if plan is None:
        offered = await db.q.fetch_all(
            "agents/subscription_plans.sql", {"client_id": ctx.client_id}, conn=conn
        )
        draft = draft.model_copy(update={"plan_code": None, "full_name": name})
        return await _ask(conn, ctx, contact_id, draft, _Reply().add("which"), plan_facts(offered))
    code, plan_name = str(plan["code"]), str(plan["name"])
    ended = _ended(plan, await _held(conn, ctx.client_id, contact_id, code))
    if ended is not None:
        await _close(conn, ctx, contact_id, "used")
        return SignupTurn(_Reply().add(ended, plan=plan_name).facts())
    verified = await _verified_phone(conn, ctx.client_id, contact_id)
    typed = draft.phone
    draft = draft.model_copy(update={"plan_code": code, "full_name": name})
    if verified is None and inp.phone is not None:
        typed = normalise_my_phone(inp.phone)
        if typed is None:
            return await _ask(conn, ctx, contact_id, draft, _Reply().add("phone_invalid"))
        draft = draft.model_copy(update={"phone": typed})
    if name is None:
        missing = _Reply().add("missing", plan=plan_name, field="full name")
        return await _ask(conn, ctx, contact_id, draft, missing)
    if verified is None and typed is None:
        # The button once on Telegram; after that (a share that verified nothing, or a customer
        # who would rather type) the number is asked in words, one question per turn.
        share = ctx.channel == "telegram" and not draft.asked_share
        draft = draft.model_copy(update={"asked_share": draft.asked_share or share})
        missing = _Reply().add("missing", plan=plan_name, field="phone number")
        return await _ask(conn, ctx, contact_id, draft, missing, request_phone=share)
    phone = verified or typed
    assert phone is not None
    key = [code, name, _VERIFIED_KEY if verified else phone]
    if inp.confirmed and await _answers_read_back(conn, ctx, draft, key):
        return await _file(conn, ctx, contact_id, code, plan_name, name, phone)
    draft = draft.model_copy(update={"read_back": key, "read_back_trace": str(ctx.trace_id)})
    shown = VERIFIED_PHONE if verified else phone
    read = _Reply().add("readback", plan=plan_name, name=name, phone=shown)
    return await _ask(conn, ctx, contact_id, draft, read)


async def _plan(conn: Conn, client_id: UUID, named: str) -> dict[str, Any] | None:
    return await db.q.fetch_one(
        "agents/subscription_plan.sql", {"client_id": client_id, "plan": named}, conn=conn
    )


async def _held(conn: Conn, client_id: UUID, contact_id: UUID, code: str) -> SignupKind | None:
    """Why this contact cannot sign up for the plan: they hold it, or a request waits."""
    live = await db.q.fetch_one(
        "subscriptions/signup_live_subscription.sql",
        {"client_id": client_id, "contact_id": contact_id, "plan_code": code},
        conn=conn,
    )
    if live is not None and live["live"]:
        return "has_plan"
    waiting = await db.q.fetch_one(
        "subscriptions/signup_open.sql",
        {"client_id": client_id, "contact_id": contact_id, "plan_code": code},
        conn=conn,
    )
    return "exists" if waiting is not None else None


def _ended(plan: dict[str, Any], held: SignupKind | None) -> SignupKind | None:
    if not plan["is_signup_open"]:
        return "closed"
    return held


async def _verified_phone(conn: Conn, client_id: UUID, contact_id: UUID) -> str | None:
    """The contact's own phone when its channel verified it, normalised; otherwise None."""
    own = await db.q.fetch_one(
        "agents/contact_phone.sql", {"client_id": client_id, "contact_id": contact_id}, conn=conn
    )
    if own is None or not own["verified"] or not own["phone"]:
        return None
    return normalise_my_phone(str(own["phone"]))


async def _answers_read_back(conn: Conn, ctx: AgentContext, draft: Draft, key: list[str]) -> bool:
    """A yes counts only for these same details, read back in the chat's latest reply."""
    if draft.read_back != key or draft.read_back_trace is None or ctx.conversation_id is None:
        return False
    last = await db.q.fetch_one(
        "agents/last_reply_trace.sql",
        {
            "client_id": ctx.client_id,
            "conversation_id": ctx.conversation_id,
            "trace_id": ctx.trace_id,
        },
        conn=conn,
    )
    return last is not None and str(last["trace_id"]) == draft.read_back_trace


async def _file(
    conn: Conn,
    ctx: AgentContext,
    contact_id: UUID,
    code: str,
    plan_name: str,
    name: str,
    phone: str,
) -> SignupTurn:
    """File the request.

    Any refusal the service raises rolls back to the savepoint, so nothing is filed, and the
    customer is told only to try again later (no error text).
    """
    try:
        async with conn.transaction():
            filed = await file_signup(conn, ctx.client_id, contact_id, code, name, phone)
    except HTTPException as exc:
        logger.warning(
            "signup_not_filed",
            status=exc.status_code,
            code=str(exc.detail),
            trace_id=str(ctx.trace_id),
        )
        return SignupTurn(_Reply().add("try_later").facts())
    if filed.outcome == "unknown_plan":
        offered = await db.q.fetch_all(
            "agents/subscription_plans.sql", {"client_id": ctx.client_id}, conn=conn
        )
        return SignupTurn(_Reply().add("which").facts(plan_facts(offered)))
    await _close(conn, ctx, contact_id, "used")
    if filed.outcome == "filed":
        request = filed.request
        if request is not None and request.status == "approved":
            # An auto plan: approved at once, with its opening invoice while payments are on.
            if request.invoice_reference:
                reply = _Reply().add(
                    "filed_auto_invoice", plan=plan_name, ref=request.invoice_reference
                )
            else:
                reply = _Reply().add("filed_auto", plan=plan_name)
            return SignupTurn(reply.facts())
        payments = (await get_settings(conn, ctx.client_id)).enabled
        return SignupTurn(_Reply().add("filed", plan=plan_name, payments=payments).facts())
    told: dict[str, SignupKind] = {"exists": "exists", "has_plan": "has_plan", "closed": "closed"}
    return SignupTurn(_Reply().add(told[filed.outcome], plan=plan_name).facts())


async def _ask(
    conn: Conn,
    ctx: AgentContext,
    contact_id: UUID,
    draft: Draft,
    reply: _Reply,
    extra: Sequence[Fact] = (),
    *,
    request_phone: bool = False,
) -> SignupTurn:
    """Ask for the next thing (or read back), telling the data note in the first asking turn."""
    if not draft.noted:
        reply.add("data_note")
        draft = draft.model_copy(update={"noted": True})
    await _save(conn, ctx, contact_id, draft)
    return SignupTurn(reply.facts(extra), request_phone=request_phone)


async def _load(conn: Conn, ctx: AgentContext, contact_id: UUID) -> Draft:
    if ctx.conversation_id is None:
        return Draft()
    row = await db.q.fetch_one(
        "agents/signup_draft.sql",
        {
            "client_id": ctx.client_id,
            "contact_id": contact_id,
            "conversation_id": ctx.conversation_id,
        },
        conn=conn,
    )
    return Draft.model_validate(row["payload"]) if row is not None else Draft()


async def _save(conn: Conn, ctx: AgentContext, contact_id: UUID, draft: Draft) -> None:
    """Update this chat's one open draft in place with `draft`, or open it if the chat has none.

    A chat with no conversation keeps none. An open draft past its hour that the expiry job has
    not reached yet is not readable: it is closed (and cleared) before the new one is opened.
    """
    if ctx.conversation_id is None:
        return
    updated = await db.q.fetch_one(
        "agents/signup_draft_update.sql",
        {
            "client_id": ctx.client_id,
            "contact_id": contact_id,
            "conversation_id": ctx.conversation_id,
            "payload": Jsonb(draft.model_dump(mode="json")),
            "life": DRAFT_LIFE,
        },
        conn=conn,
    )
    if updated is not None:
        return
    await _close(conn, ctx, contact_id, "cancelled")
    saved = await db.q.fetch_one(
        "pending_actions/insert.sql",
        (
            ctx.client_id,
            SUBSCRIPTION_SOURCE,
            DRAFT_ACTION,
            Jsonb(draft.model_dump(mode="json")),
            DRAFT_LIFE,
            ctx.conversation_id,
            ctx.client_id,
            contact_id,
        ),
        conn=conn,
    )
    if saved is None:
        raise RuntimeError("signup draft: the conversation or contact is not this company's")


async def _close(conn: Conn, ctx: AgentContext, contact_id: UUID, status: str) -> None:
    """Close this chat's open draft; its typed data is cleared with it (see the SQL)."""
    if ctx.conversation_id is None:
        return
    await db.q.execute(
        "agents/signup_draft_close.sql",
        {
            "client_id": ctx.client_id,
            "contact_id": contact_id,
            "conversation_id": ctx.conversation_id,
            "status": status,
        },
        conn=conn,
    )
