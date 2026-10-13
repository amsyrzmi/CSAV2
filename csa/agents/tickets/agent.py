"""Ticket agent: create, get, list, add_note, cancel (confirmed), reopen (LG05)."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from csa.agents.base import AgentContext, SideEffects
from csa.agents.facts import day
from csa.contracts.facts import (
    AgentError,
    AgentResult,
    AgentResultData,
    Fact,
    ResultMeta,
    Ui,
    UiOption,
)
from csa.contracts.plan import RiskClass
from csa.core import db
from csa.core.tenancy import tx

CATEGORIES = ("returns", "shipping", "product", "billing", "technical", "other")
DEFAULT_SLA_HOURS = {"urgent": 1, "high": 4, "normal": 24, "low": 72}
REOPEN_WINDOW = timedelta(days=7)
CONFIRM_TTL = timedelta(minutes=10)
_ALIASES = {
    "delete": "cancel",
    "remove": "cancel",
    "close": "cancel",
    "status": "get",
    "view": "get",
    "check": "get",
    "edit": "add_note",
    "modify": "add_note",
    "note": "add_note",
    "comment": "add_note",
    "append": "add_note",
    "add_info": "add_note",
}
_TICKET_ID = re.compile(r"^(?:#|TKT-)?(\d{1,18})$", re.IGNORECASE)


class TicketInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["create", "get", "list", "add_note", "cancel", "reopen"]
    ticket_id: int | None = None
    subject: str | None = Field(default=None, min_length=3, max_length=200)
    category: Literal["returns", "shipping", "product", "billing", "technical", "other"] = "other"
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    description: str | None = Field(default=None, max_length=2000)
    note: str | None = Field(default=None, max_length=2000)
    callback_id: UUID | None = None

    @model_validator(mode="before")
    @classmethod
    def normalise(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        action = str(data.get("action", "")).lower()
        data["action"] = _ALIASES.get(action, action)
        return data

    @field_validator("ticket_id", mode="before")
    @classmethod
    def parse_ticket_id(cls, value: Any) -> Any:
        if isinstance(value, str):
            match = _TICKET_ID.match(value.strip())
            if match is None:
                raise ValueError("invalid ticket id")
            return int(match.group(1))
        return value

    @model_validator(mode="after")
    def check_fields(self) -> TicketInput:
        needs_id = self.action in {"get", "add_note", "cancel", "reopen"}
        if needs_id and self.ticket_id is None:
            raise ValueError("ticket_id required")
        if self.action == "create" and not self.subject:
            raise ValueError("subject required")
        if self.action == "add_note" and not self.note:
            raise ValueError("note required")
        return self


class TicketAgent:
    name = "ticket_agent"
    Input = TicketInput
    side_effects: SideEffects = "reversible"
    timeout_ms = 10000
    max_retries = 1

    ACTION_RISK: dict[str, RiskClass] = {
        "get": "read",
        "list": "read",
        "create": "commit",
        "add_note": "commit",
        "cancel": "commit",
        "reopen": "commit",
    }

    def __init__(self, sla_hours: dict[str, int] | None = None) -> None:
        self._sla = sla_hours or DEFAULT_SLA_HOURS

    def risk(self, inp: BaseModel) -> RiskClass:
        assert isinstance(inp, TicketInput)
        return self.ACTION_RISK[inp.action]

    async def run(self, ctx: AgentContext, inp: BaseModel) -> AgentResult:
        assert isinstance(inp, TicketInput)
        handler = {
            "create": self._create,
            "get": self._get,
            "list": self._list,
            "add_note": self._add_note,
            "cancel": self._cancel,
            "reopen": self._reopen,
        }[inp.action]
        return await handler(ctx, inp)

    def _result(
        self,
        ctx: AgentContext,
        facts: list[Fact],
        ui: Ui | None = None,
        *,
        duplicate: bool | None = None,
    ) -> AgentResult:
        return AgentResult(
            ok=True,
            status="ok",
            task_id=ctx.idempotency_key,
            agent=self.name,
            data=AgentResultData(facts=facts, ui=ui or Ui()),
            meta=ResultMeta(trace_id=ctx.trace_id, latency_ms=0, was_duplicate=duplicate),
        )

    def _error(
        self, ctx: AgentContext, code: str, category: Any, message: str, hint: str | None = None
    ) -> AgentResult:
        return AgentResult(
            ok=False,
            status="error",
            task_id=ctx.idempotency_key,
            agent=self.name,
            data=AgentResultData(),
            error=AgentError(
                code=code, category=category, message=message, customer_safe_hint=hint
            ),
            meta=ResultMeta(trace_id=ctx.trace_id, latency_ms=0),
        )

    def _fact(self, fact_id: str, text: str) -> Fact:
        return Fact(id=fact_id, text=text, source=self.name)

    async def _event(
        self,
        conn: psycopg.AsyncConnection[Any],
        ctx: AgentContext,
        ticket_id: int,
        event: str,
        old: dict[str, object] | None,
        new: dict[str, object] | None,
    ) -> None:
        await db.q.fetch_one(
            "tickets/insert_event.sql",
            (
                ctx.client_id,
                ticket_id,
                "customer",
                event,
                Jsonb(old) if old is not None else None,
                Jsonb(new) if new is not None else None,
            ),
            conn=conn,
        )

    async def _create(self, ctx: AgentContext, inp: TicketInput) -> AgentResult:
        # Customers cannot buy a 1-hour SLA.
        priority = "high" if inp.priority == "urgent" else inp.priority
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            duplicate = await db.q.fetch_one(
                "tickets/find_recent_duplicate.sql",
                (ctx.client_id, ctx.contact_id, ctx.conversation_id, inp.subject),
                conn=conn,
            )
            if duplicate is not None:
                return self._result(
                    ctx,
                    [
                        self._fact(
                            f"ticket_{duplicate['id']}", f"Ticket #{duplicate['id']} is open."
                        )
                    ],
                    duplicate=True,
                )
            row = await db.q.fetch_one(
                "tickets/create.sql",
                (
                    ctx.client_id,
                    ctx.conversation_id,
                    ctx.contact_id,
                    inp.subject,
                    inp.category,
                    priority,
                    self._sla[priority],
                ),
                conn=conn,
            )
            assert row is not None
            await self._event(
                conn,
                ctx,
                row["id"],
                "created",
                None,
                {"category": inp.category, "priority": priority, "description": inp.description},
            )
        return self._result(
            ctx,
            [
                self._fact(
                    f"ticket_{row['id']}",
                    f"Ticket #{row['id']} created ({inp.category}, {priority} priority), "
                    f"target response by {day(row['sla_due_at'], ctx.timezone)}.",
                )
            ],
            duplicate=False,
        )

    async def _get(self, ctx: AgentContext, inp: TicketInput) -> AgentResult:
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            row = await db.q.fetch_one(
                "tickets/get.sql", (ctx.client_id, ctx.contact_id, inp.ticket_id), conn=conn
            )
        if row is None:
            return self._error(ctx, "ticket_not_found", "not_found", "ticket not found")
        return self._result(ctx, [self._describe(row)])

    def _describe(self, row: dict[str, Any]) -> Fact:
        return self._fact(
            f"ticket_{row['id']}",
            f"Ticket #{row['id']}: {row['subject']} ({row['category']}), "
            f"{row['status']}, {row['priority']} priority.",
        )

    async def _list(self, ctx: AgentContext, inp: TicketInput) -> AgentResult:
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            rows = await db.q.fetch_all(
                "tickets/list.sql", (ctx.client_id, ctx.contact_id), conn=conn
            )
        if not rows:
            return self._result(ctx, [self._fact("no_tickets", "No tickets were found.")])
        return self._result(ctx, [self._describe(row) for row in rows])

    async def _add_note(self, ctx: AgentContext, inp: TicketInput) -> AgentResult:
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            row = await db.q.fetch_one(
                "tickets/get_for_update.sql",
                (ctx.client_id, ctx.contact_id, inp.ticket_id),
                conn=conn,
            )
            if row is None:
                return self._error(ctx, "ticket_not_found", "not_found", "ticket not found")
            if row["status"] in {"cancelled", "closed"}:
                return self._error(
                    ctx, "ticket_closed", "conflict", "ticket is closed", "That ticket is closed."
                )
            await self._event(conn, ctx, row["id"], "note", None, {"note": inp.note})
        return self._result(ctx, [self._fact(f"ticket_{row['id']}", "Your note was added.")])

    async def _cancel(self, ctx: AgentContext, inp: TicketInput) -> AgentResult:
        ticket_id = 0
        confirmed = ctx.callback_action_id is not None and ctx.callback_action_id == inp.callback_id
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            row = await db.q.fetch_one(
                "tickets/get_for_update.sql",
                (ctx.client_id, ctx.contact_id, inp.ticket_id),
                conn=conn,
            )
            if row is None:
                return self._error(ctx, "ticket_not_found", "not_found", "ticket not found")
            if row["status"] == "cancelled":
                return self._result(
                    ctx,
                    [
                        self._fact(
                            f"ticket_{row['id']}", f"Ticket #{row['id']} is already cancelled."
                        )
                    ],
                    duplicate=True,
                )
            if not confirmed:
                ticket_id = row["id"]
        if not confirmed:
            if ctx.conversation_id is None:
                raise RuntimeError("conversation_id_missing")
            yes = await ctx.pending_actions.issue(
                client_id=ctx.client_id,
                conversation_id=ctx.conversation_id,
                contact_id=ctx.contact_id,
                agent=self.name,
                action="cancel",
                payload={"ticket_id": ticket_id},
                ttl=CONFIRM_TTL,
            )
            return self._result(
                ctx,
                [
                    self._fact(
                        f"ticket_{ticket_id}",
                        f"Ticket #{ticket_id} is NOT cancelled yet; the customer must confirm.",
                    )
                ],
                Ui(options=[UiOption(label="Yes, cancel it", action_id=yes)]),
            )
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            used = await db.q.fetch_one(
                "orders/get_used_action.sql",
                (ctx.callback_action_id, ctx.client_id, ctx.contact_id),
                conn=conn,
            )
            if (
                used is None
                or used["agent"] != self.name
                or used["action"] != "cancel"
                or used["payload"].get("ticket_id") != inp.ticket_id
            ):
                return self._error(ctx, "not_authorised", "validation", "callback mismatch")
            await db.q.fetch_one(
                "tickets/set_status.sql",
                ("cancelled", True, ctx.client_id, inp.ticket_id),
                conn=conn,
            )
            await self._event(
                conn, ctx, inp.ticket_id or 0, "cancelled", {"status": row["status"]}, None
            )
        return self._result(
            ctx, [self._fact(f"ticket_{inp.ticket_id}", f"Ticket #{inp.ticket_id} was cancelled.")]
        )

    async def _reopen(self, ctx: AgentContext, inp: TicketInput) -> AgentResult:
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            row = await db.q.fetch_one(
                "tickets/get_for_update.sql",
                (ctx.client_id, ctx.contact_id, inp.ticket_id),
                conn=conn,
            )
            if row is None:
                return self._error(ctx, "ticket_not_found", "not_found", "ticket not found")
            if row["status"] not in {"cancelled", "closed", "resolved"}:
                return self._result(
                    ctx,
                    [self._fact(f"ticket_{row['id']}", f"Ticket #{row['id']} is already open.")],
                    duplicate=True,
                )
            if row["closed_at"] is None or ctx.now() - row["closed_at"] > REOPEN_WINDOW:
                return self._error(
                    ctx,
                    "reopen_window_passed",
                    "conflict",
                    "closed more than 7 days ago",
                    "That ticket was closed too long ago to reopen; a new one can be created.",
                )
            await db.q.fetch_one(
                "tickets/set_status.sql", ("open", False, ctx.client_id, row["id"]), conn=conn
            )
            await self._event(conn, ctx, row["id"], "reopened", {"status": row["status"]}, None)
        return self._result(
            ctx, [self._fact(f"ticket_{row['id']}", f"Ticket #{row['id']} reopened.")]
        )
