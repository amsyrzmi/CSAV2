"""The customer's bookings, and local reads and durable preparation for the other agents.

Preparation never executes against a provider or approves anything. The booking agent is
different: it really books, through `csa.bookings.bot`.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from csa.agents.base import AgentContext, SideEffects
from csa.agents.staged.booking_facts import (
    NEEDS_NAME,
    NOT_FOUND,
    Scene,
    booking_line,
    facts_for,
    handoff_of,
    saved_line,
    times,
    type_line,
)
from csa.agents.staged.models import (
    BookingInput,
    DraftModel,
    EmailDraftInput,
    InvoiceDraftInput,
    PreparedRequest,
    ResearchInput,
    SubscriptionInput,
)
from csa.bookings import bot, notify, setup
from csa.bookings import service as core
from csa.bookings.bot import BotOutcome
from csa.bookings.service import BookingRecord
from csa.console.mailer import Mailer
from csa.contracts.facts import AgentError, AgentResult, AgentResultData, Fact, ResultMeta
from csa.contracts.plan import RiskClass
from csa.core import db
from csa.core.tenancy import tx

logger = structlog.get_logger(__name__)


def result(
    ctx: AgentContext,
    agent: str,
    text: str,
    *,
    code: str | None = None,
    duplicate: bool = False,
) -> AgentResult:
    return AgentResult(
        ok=code is None,
        status="ok" if code is None else "error",
        task_id=ctx.idempotency_key,
        agent=agent,
        data=AgentResultData(
            facts=[] if code else [Fact(id=agent, text=text, source=agent, as_of=ctx.now())]
        ),
        error=AgentError(code=code, category="validation", message=text) if code else None,
        meta=ResultMeta(trace_id=ctx.trace_id, latency_ms=0, was_duplicate=duplicate),
    )


async def prepare(
    conn: AsyncConnection[Any],
    ctx: AgentContext,
    agent: str,
    inp: SubscriptionInput | EmailDraftInput | ResearchInput | InvoiceDraftInput,
    *,
    requested_by: str | None = None,
) -> AgentResult:
    if not 1 <= len(ctx.idempotency_key.strip()) <= 256 or "\x00" in ctx.idempotency_key:
        return result(ctx, agent, "A bounded idempotency key is required.", code="invalid_key")
    payload = inp.payload() if isinstance(inp, InvoiceDraftInput) else inp.model_dump(mode="json")
    row = await db.q.fetch_one(
        "agents/prepare_request.sql",
        (
            ctx.client_id,
            ctx.contact_id,
            agent,
            inp.action,
            ctx.idempotency_key,
            Jsonb(payload),
            ctx.trace_id,
            requested_by or str(ctx.contact_id),
        ),
        conn=conn,
        model=PreparedRequest,
    )
    duplicate = row is None
    if row is None:
        row = await db.q.fetch_one(
            "agents/get_prepared_request.sql",
            (ctx.client_id, ctx.contact_id, agent, ctx.idempotency_key),
            conn=conn,
            model=PreparedRequest,
        )
    if row is None:
        raise RuntimeError("prepared_request_missing")
    if row.action != inp.action or row.payload != payload:
        return result(
            ctx,
            agent,
            "Idempotency key already used for different input.",
            code="idempotency_conflict",
        )
    return result(
        ctx,
        agent,
        f"Request {row.id} prepared for staff review only. "
        "NOT executed, approved, booked, sent, fetched, billed or submitted. "
        "Provider execution is not configured.",
        duplicate=duplicate,
    )


async def contact_exists(conn: AsyncConnection[Any], ctx: AgentContext) -> bool:
    return (
        await db.q.fetch_one(
            "agents/preparation_contact.sql", (ctx.client_id, ctx.contact_id), conn=conn
        )
        is not None
    )


WRITES = ("book", "reschedule", "cancel", "save_details")
BOOKING = "booking_agent"


def reply(ctx: AgentContext, texts: list[str], *, handoff: str | None = None) -> AgentResult:
    """An answer made of facts. `handoff` passes the customer to a person, with a staff reason."""
    return AgentResult(
        ok=True,
        status="ok",
        task_id=ctx.idempotency_key,
        agent=BOOKING,
        data=AgentResultData(
            facts=[
                Fact(id=f"booking-{n}", text=text, source=BOOKING, as_of=ctx.now())
                for n, text in enumerate(texts)
            ]
        ),
        requires_human=handoff is not None,
        handoff_reason=handoff,
        meta=ResultMeta(trace_id=ctx.trace_id, latency_ms=0),
    )


@dataclass(frozen=True)
class Acted:
    """What a call did inside its transaction. With an outcome, the facts are built after it
    commits, because they may report the mail that went out.
    """

    scene: Scene
    outcome: BotOutcome | None = None
    lines: list[str] = field(default_factory=list)


def local(value: datetime, zone: ZoneInfo) -> datetime:
    """A time with no offset is the company's own local time, never the host's."""
    return value.replace(tzinfo=zone) if value.tzinfo is None else value


def mail_for(outcome: BotOutcome) -> notify.Change | None:
    """The mail a call owes, if it changed a booking (a replay changed nothing)."""
    booking = outcome.booking
    if booking is None:
        return None
    if outcome.code == "confirmed" and booking.status == "confirmed":
        return "created"
    if outcome.code == "moved":
        return "moved"
    if outcome.code == "cancelled":
        return "cancelled"
    return None


class BookingAgent:
    """The customer's own bookings: free times, book, move, cancel, list and status.

    Agents run before the reply gate, so this one obeys the company's reply mode itself (through
    `bot`). It writes at most once per task, because the task's key is the booking's turn key.
    """

    name = BOOKING
    Input = BookingInput
    side_effects: SideEffects = "irreversible"
    timeout_ms = 10000
    max_retries = 0
    ACTION_RISK: dict[str, RiskClass] = {
        "availability": "read",
        "list": "read",
        "status": "read",
        "book": "commit",
        "reschedule": "commit",
        "cancel": "commit",
        "save_details": "commit",
    }

    def __init__(self, mailer: Mailer | None = None) -> None:
        self._mailer = mailer
        # Mail still on its way. Held here so a task is not forgotten before it finishes.
        self._mail_tasks: set[asyncio.Task[None]] = set()

    def risk(self, inp: BaseModel) -> RiskClass:
        assert isinstance(inp, BookingInput)
        return self.ACTION_RISK[inp.action]

    async def run(self, ctx: AgentContext, inp: BaseModel) -> AgentResult:
        assert isinstance(inp, BookingInput)
        key = ctx.idempotency_key
        if inp.action in WRITES and (not 1 <= len(key.strip()) <= 256 or "\x00" in key):
            return result(ctx, self.name, "A bounded key is required.", code="invalid_key")
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            if not await db.q.fetch_one(
                "agents/preparation_enabled.sql", (ctx.client_id, self.name), conn=conn
            ):
                return result(ctx, self.name, "Booking is not enabled.", code="disabled")
            if not await contact_exists(conn, ctx):
                return result(ctx, self.name, "Contact not found.", code="contact_not_found")
            acted = await self._act(conn, ctx, inp)
        # The transaction has committed. Mail goes in the background: the reply never waits for
        # the relay, and it cannot claim mail it has not seen arrive.
        outcome = acted.outcome
        if outcome is None:
            return reply(ctx, acted.lines)
        change = mail_for(outcome)
        if change is not None and outcome.booking is not None:
            self._mail_later(ctx, outcome.booking, change)
        return reply(
            ctx, [*facts_for(outcome, acted.scene), *acted.lines], handoff=handoff_of(outcome)
        )

    def _mail_later(self, ctx: AgentContext, booking: BookingRecord, change: notify.Change) -> None:
        task = asyncio.create_task(self._mail(ctx.pool, booking, change))
        self._mail_tasks.add(task)
        task.add_done_callback(self._mail_tasks.discard)

    async def _mail(self, pool: Any, booking: BookingRecord, change: notify.Change) -> None:
        """Never raises: the booking has committed, and a lost mail is only a lost mail."""
        try:
            await notify.notify_after_commit(pool, self._mailer, booking, change)
        except Exception:
            logger.warning("bookings_mail_failed", category="unexpected")

    async def drain(self) -> None:
        """Wait for the mail still on its way (a worker shutting down, or a test)."""
        while self._mail_tasks:
            await asyncio.gather(*list(self._mail_tasks), return_exceptions=True)

    async def _act(self, conn: AsyncConnection[Any], ctx: AgentContext, inp: BookingInput) -> Acted:
        zone = await core.company_zone(conn, ctx.client_id)
        names = {t.id: t.name for t in await setup.list_types(conn, ctx.client_id)}
        scene = Scene(zone, names)
        blocked, _ = await bot.gate(conn, ctx.client_id)
        if blocked is not None:
            return Acted(scene, BotOutcome(blocked))
        if inp.action == "availability":
            return Acted(scene, lines=await self._availability(conn, ctx, inp, scene))
        if inp.action == "list":
            return Acted(scene, lines=await self._list(conn, ctx, scene))
        if inp.action == "status":
            return await self._status(conn, ctx, inp, scene)
        if inp.action == "save_details":
            return await self._save_details(conn, ctx, inp, scene)
        details = await self._details(conn, ctx, inp.customer_name, inp.customer_email)
        if inp.action == "book":
            return await self._book(conn, ctx, inp, scene, details)
        return await self._change(conn, ctx, inp, scene)

    async def _details(
        self, conn: AsyncConnection[Any], ctx: AgentContext, name: str | None, email: str | None
    ) -> dict[str, Any]:
        """The customer's name and email, after saving what they just said into empty fields."""
        params = {"client_id": ctx.client_id, "id": ctx.contact_id, "name": name}
        try:
            async with conn.transaction():
                row = await db.q.fetch_one(
                    "bookings/contact_details_fill.sql", {**params, "email": email}, conn=conn
                )
        except UniqueViolation:  # another contact took the address a moment ago
            row = await db.q.fetch_one(
                "bookings/contact_details_fill.sql", {**params, "email": None}, conn=conn
            )
        return dict(row or {})

    async def _save_details(
        self, conn: AsyncConnection[Any], ctx: AgentContext, inp: BookingInput, scene: Scene
    ) -> Acted:
        """Save the name or email the customer just gave into the fields that are empty."""
        before = await self._details(conn, ctx, None, None)
        after = await self._details(conn, ctx, inp.customer_name, inp.customer_email)
        line = saved_line(before, after, name=inp.customer_name, email=inp.customer_email)
        return Acted(scene, lines=[line])

    async def _availability(
        self, conn: AsyncConnection[Any], ctx: AgentContext, inp: BookingInput, scene: Scene
    ) -> list[str]:
        types = await bot.customer_types(conn, ctx.client_id)
        if inp.type_id is None:
            if not types:
                return ["Nothing can be booked by message at the moment."]
            return [type_line(t) for t in types]
        if inp.type_id not in {t.id for t in types}:
            return [NOT_FOUND, *(type_line(t) for t in types)]
        now = ctx.now()
        offers = await bot.offer(
            conn,
            ctx.client_id,
            type_id=inp.type_id,
            day_from=inp.day or now.astimezone(scene.zone).date(),
            now=now,
        )
        name = scene.type_names.get(inp.type_id, "this service")
        if not offers:
            return [f"No free times for {name} were found soon."]
        return [f"Free times for {name}: {times(offers)}."]

    async def _list(self, conn: AsyncConnection[Any], ctx: AgentContext, scene: Scene) -> list[str]:
        now = ctx.now()
        rows = await db.q.fetch_all(
            "agents/booking_list.sql",
            {"client_id": ctx.client_id, "contact_id": ctx.contact_id, "now": now},
            conn=conn,
        )
        if not rows:
            return ["The customer has no bookings."]
        lines = []
        for row in rows:
            line = await self._line(conn, ctx, scene, row["id"])
            past = "This booking is in the past. " if row["ends_at"] < now else ""
            lines.append(f"{past}{line} (booking id {row['id']}, for lookups only; never show it)")
        return lines

    async def _status(
        self, conn: AsyncConnection[Any], ctx: AgentContext, inp: BookingInput, scene: Scene
    ) -> Acted:
        assert inp.booking_id is not None
        if await self._own(conn, ctx, inp.booking_id) is None:
            return Acted(scene, lines=[NOT_FOUND])
        return Acted(scene, lines=[await self._line(conn, ctx, scene, inp.booking_id)])

    async def _own(
        self, conn: AsyncConnection[Any], ctx: AgentContext, booking_id: UUID
    ) -> dict[str, Any] | None:
        """The booking when the customer may see it: theirs, not private, not staff-made pending."""
        return await db.q.fetch_one(
            "agents/booking_get.sql",
            {"client_id": ctx.client_id, "contact_id": ctx.contact_id, "id": booking_id},
            conn=conn,
        )

    async def _line(
        self, conn: AsyncConnection[Any], ctx: AgentContext, scene: Scene, booking_id: UUID
    ) -> str:
        """One of the customer's own bookings, in words (the caller has checked it is theirs)."""
        record = await self._record(conn, ctx, booking_id)
        return booking_line("replay", record, await self._scene_for(conn, ctx, scene, record))

    async def _record(
        self, conn: AsyncConnection[Any], ctx: AgentContext, booking_id: UUID
    ) -> BookingRecord:
        return (await core.read_records(conn, ctx.client_id, booking_id=booking_id))[0]

    async def _scene_for(
        self, conn: AsyncConnection[Any], ctx: AgentContext, scene: Scene, record: BookingRecord
    ) -> Scene:
        """The scene for a booking, which may be a request to move another one."""
        if record.replaces_booking_id is None:
            return replace(scene, type_id=record.type_id)
        original = await self._record(conn, ctx, record.replaces_booking_id)
        return replace(scene, original=original, type_id=record.type_id)

    async def _book(
        self,
        conn: AsyncConnection[Any],
        ctx: AgentContext,
        inp: BookingInput,
        scene: Scene,
        details: dict[str, Any],
    ) -> Acted:
        assert inp.type_id is not None and inp.starts_at is not None
        if not (details.get("full_name") or "").strip():
            return Acted(scene, lines=[NEEDS_NAME])
        outcome = await bot.book(
            conn,
            ctx.client_id,
            ctx.contact_id,
            type_id=inp.type_id,
            starts_at=local(inp.starts_at, scene.zone),
            turn_key=ctx.idempotency_key,
            conversation_id=ctx.conversation_id,
            now=ctx.now(),
        )
        scene = replace(
            scene,
            type_id=inp.type_id,
            ask_email=ctx.channel == "whatsapp" and not (details.get("email") or "").strip(),
        )
        if outcome.booking is not None:
            scene = await self._scene_for(conn, ctx, scene, outcome.booking)
        lines: list[str] = []
        if outcome.code == "not_found":  # tell the model what can be booked, as availability does
            lines = [type_line(t) for t in await bot.customer_types(conn, ctx.client_id)]
        return Acted(scene, outcome, lines)

    async def _change(
        self, conn: AsyncConnection[Any], ctx: AgentContext, inp: BookingInput, scene: Scene
    ) -> Acted:
        booking_id = inp.booking_id
        assert booking_id is not None
        own = await self._own(conn, ctx, booking_id)
        type_id = (await self._record(conn, ctx, booking_id)).type_id if own else None
        if inp.action == "cancel":
            outcome = await bot.cancel(
                conn,
                ctx.client_id,
                ctx.contact_id,
                booking_id,
                turn_key=ctx.idempotency_key,
                now=ctx.now(),
            )
        else:
            assert inp.starts_at is not None
            outcome = await bot.change(
                conn,
                ctx.client_id,
                ctx.contact_id,
                booking_id,
                starts_at=local(inp.starts_at, scene.zone),
                turn_key=ctx.idempotency_key,
                now=ctx.now(),
            )
        scene = replace(scene, type_id=type_id)
        if outcome.booking is not None:
            scene = await self._scene_for(conn, ctx, scene, outcome.booking)
        return Acted(scene, outcome)


class SubscriptionAgent:
    name = "subscription_agent"
    Input = SubscriptionInput
    side_effects: SideEffects = "reversible"
    timeout_ms = 10000
    max_retries = 0
    ACTION_RISK: dict[str, RiskClass] = {
        "plans": "read",
        "list": "read",
        "status": "read",
        "request_pause": "draft",
        "request_cancel": "draft",
        "request_change": "draft",
    }

    def risk(self, inp: BaseModel) -> RiskClass:
        assert isinstance(inp, SubscriptionInput)
        return self.ACTION_RISK[inp.action]

    async def run(self, ctx: AgentContext, inp: BaseModel) -> AgentResult:
        assert isinstance(inp, SubscriptionInput)
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            if not await db.q.fetch_one(
                "agents/preparation_enabled.sql", (ctx.client_id, self.name), conn=conn
            ):
                return result(
                    ctx, self.name, "Subscription preparation is disabled.", code="disabled"
                )
            if not await contact_exists(conn, ctx):
                return result(ctx, self.name, "Contact not found.", code="contact_not_found")
            if inp.action in {"plans", "list"}:
                path = "subscription_plans" if inp.action == "plans" else "subscription_list"
                params = (
                    (ctx.client_id,) if inp.action == "plans" else (ctx.client_id, ctx.contact_id)
                )
                rows = await db.q.fetch_all(f"agents/{path}.sql", params, conn=conn)
                return result(ctx, self.name, json.dumps(rows, default=str))
            row = await db.q.fetch_one(
                "agents/subscription_get.sql",
                (ctx.client_id, ctx.contact_id, inp.subscription_id),
                conn=conn,
            )
            if not row:
                return result(
                    ctx, self.name, "Subscription not found.", code="subscription_not_found"
                )
            if inp.action == "status":
                return result(ctx, self.name, json.dumps(row, default=str))
            if row["status"] not in {"active", "paused"}:
                return result(
                    ctx, self.name, "Subscription is not active.", code="subscription_inactive"
                )
            if inp.action == "request_change" and not await db.q.fetch_one(
                "agents/subscription_plan.sql", (ctx.client_id, inp.plan_code), conn=conn
            ):
                return result(ctx, self.name, "Active plan not found.", code="plan_not_found")
            return await prepare(conn, ctx, self.name, inp)


class StaffActor(DraftModel):
    """Trusted backend identity, never accepted from agent/model input."""

    client_id: UUID
    actor_id: str = Field(min_length=1, max_length=128)
    role: Literal["platform_admin", "tenant_admin", "tenant_staff", "read_only"]


class StaffPreparationService:
    """Not registered in the customer planner; future RBAC adapters supply the actor."""

    async def prepare(
        self,
        actor: StaffActor,
        ctx: AgentContext,
        inp: EmailDraftInput | ResearchInput | InvoiceDraftInput,
    ) -> AgentResult:
        name = (
            "email_agent"
            if isinstance(inp, EmailDraftInput)
            else "web_research_agent"
            if isinstance(inp, ResearchInput)
            else "invoice_write_agent"
        )
        if (
            actor.client_id != ctx.client_id
            or actor.role == "read_only"
            or not actor.actor_id.strip()
        ):
            return result(ctx, name, "Staff authorization required.", code="forbidden")
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            if not await contact_exists(conn, ctx):
                return result(ctx, name, "Contact not found.", code="contact_not_found")
            if isinstance(inp, EmailDraftInput) and not await db.q.fetch_one(
                "agents/preparation_mailbox.sql",
                (ctx.client_id, inp.mailbox_connection_id),
                conn=conn,
            ):
                return result(ctx, name, "Mailbox not found.", code="mailbox_not_found")
            if (
                isinstance(inp, InvoiceDraftInput)
                and inp.invoice_id
                and not await db.q.fetch_one(
                    "agents/preparation_invoice.sql",
                    (ctx.client_id, ctx.contact_id, inp.invoice_id),
                    conn=conn,
                )
            ):
                return result(ctx, name, "Invoice not found.", code="invoice_not_found")
            return await prepare(conn, ctx, name, inp, requested_by=actor.actor_id)
