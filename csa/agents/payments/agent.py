"""The payment agent: answers about the customer's own invoices with facts, never model text.

Every read is of the chat's own contact (`ctx.contact_id`, from the turn envelope) through
`customer_invoices`, which shows only statuses a customer may hear about and a `paid` invoice only
with its ledger row. A typed reference narrows within those; one that matches nothing (another
customer's, a typo) is answered "can't find" with the customer's own text, and nothing else.
`claim_paid` writes through `record_claim`, which keeps the ledger's lock order and moves only the
customer's own issued or overdue invoice to `proof_submitted`.

Facts are worded by `csa.payments.copy` in the customer's language (`ctx.locale`) with every
reference, amount and account number inserted from the database; the turn sends them as they
stand (`csa.turn.deps.payment_facts`). Ids are constants with a 1-based index, never an invoice id.
Staff-only fields (void reason, voider, payment note, who recorded it) are never read here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import structlog
from psycopg import AsyncConnection
from pydantic import BaseModel

from csa.agents.base import AgentContext, SideEffects
from csa.agents.payments.models import PaymentInput
from csa.contracts.facts import AgentError, AgentResult, AgentResultData, Fact, ResultMeta
from csa.contracts.plan import RiskClass
from csa.core import db
from csa.core.tenancy import tx
from csa.payments import copy
from csa.payments.contracts import MethodOut
from csa.payments.invoices import InvoiceRecord, customer_invoices, record_claim
from csa.payments.reference import normalise_reference
from csa.payments.settings import get_settings

logger = structlog.get_logger(__name__)

SOURCE = "payment_agent"
# How many of the customer's invoices one answer names at most (open first, then recently paid).
INVOICE_LIMIT = 5
_CLAIMABLE = frozenset({"issued", "overdue"})
_OPEN = frozenset({"issued", "overdue", "proof_submitted", "cash_pending"})

Conn = AsyncConnection[Any]


def _fact(fact_id: str, text: str, ctx: AgentContext) -> Fact:
    return Fact(id=fact_id, text=text, source=SOURCE, as_of=ctx.now())


def _answer(ctx: AgentContext, lines: Sequence[tuple[str, str]]) -> AgentResult:
    return AgentResult(
        ok=True,
        status="ok",
        task_id=ctx.idempotency_key,
        agent=SOURCE,
        data=AgentResultData(facts=[_fact(fact_id, text, ctx) for fact_id, text in lines]),
        meta=ResultMeta(trace_id=ctx.trace_id, latency_ms=0),
    )


def _disabled(ctx: AgentContext) -> AgentResult:
    message = "Payment answers are disabled."
    return AgentResult(
        ok=False,
        status="error",
        task_id=ctx.idempotency_key,
        agent=SOURCE,
        data=AgentResultData(),
        error=AgentError(code="disabled", category="validation", message=message),
        meta=ResultMeta(trace_id=ctx.trace_id, latency_ms=0),
    )


def _echo(text: str) -> str:
    """The customer's text as it may be repeated in a reply: one line of printable characters
    (control characters go first, then whitespace runs collapse), cut to 12 characters."""
    kept = "".join(char for char in text if char.isprintable() or char.isspace())
    return " ".join(kept.split())[:12]


class PaymentAgent:
    name = SOURCE
    Input = PaymentInput
    side_effects: SideEffects = "reversible"
    timeout_ms = 10000
    max_retries = 0
    ACTION_RISK: dict[str, RiskClass] = {
        "instructions": "read",
        "status": "read",
        "claim_paid": "draft",
    }

    def risk(self, inp: BaseModel) -> RiskClass:
        assert isinstance(inp, PaymentInput)
        return self.ACTION_RISK[inp.action]

    async def run(self, ctx: AgentContext, inp: BaseModel) -> AgentResult:
        assert isinstance(inp, PaymentInput)
        locale = copy.locale_of(ctx.locale)
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            if not await db.q.fetch_one(
                "agents/preparation_enabled.sql", (ctx.client_id, self.name), conn=conn
            ):
                return _disabled(ctx)
            if not (await get_settings(conn, ctx.client_id)).enabled:
                return _answer(ctx, [("payment_not_set_up_1", copy.not_set_up_line(locale))])
            invoices = await customer_invoices(
                conn,
                ctx.client_id,
                ctx.contact_id,
                reference=inp.reference,
                limit=INVOICE_LIMIT,
            )
            if inp.reference is not None and not invoices:
                typed = normalise_reference(inp.reference) or _echo(inp.reference)
                line = copy.reference_unknown_line(typed, locale)
                return _answer(ctx, [("payment_reference_unknown_1", line)])
            if inp.action == "instructions":
                return _answer(ctx, await self._instructions(conn, ctx, invoices, locale))
            if inp.action == "status":
                return _answer(ctx, self._status(ctx, invoices, locale))
            return _answer(ctx, await self._claim(conn, ctx, inp, invoices, locale))

    async def _instructions(
        self,
        conn: Conn,
        ctx: AgentContext,
        invoices: list[InvoiceRecord],
        locale: copy.Locale,
    ) -> list[tuple[str, str]]:
        """Each open invoice, then how to pay the ones not yet claimed, and what to do after."""
        open_ = [inv for inv in invoices if inv.status in _OPEN]
        if not open_:
            if invoices:  # a referenced invoice that is already paid
                return [
                    (
                        "payment_already_paid_1",
                        copy.already_paid_line(copy.ref_of(invoices[0]), locale),
                    )
                ]
            return [("payment_nothing_due_1", copy.nothing_due_line(locale))]
        to_pay = [inv for inv in open_ if inv.status != "proof_submitted"]
        methods: list[MethodOut] = []
        if to_pay:
            rows = await db.q.fetch_all(
                "payments/agent_methods.sql",
                {"client_id": ctx.client_id, "limit": copy.CUSTOMER_METHODS},
                conn=conn,
            )
            methods = [MethodOut.model_validate(row) for row in rows]
            if not methods:  # settings refuse this; never tell a customer to pay nowhere
                return [("payment_not_set_up_1", copy.not_set_up_line(locale))]
        lines = [
            (
                f"payment_invoice_{n}",
                copy.claimed_line(inv, locale)
                if inv.status == "proof_submitted"
                else copy.invoice_line(inv, locale, ctx.timezone),
            )
            for n, inv in enumerate(open_, start=1)
        ]
        if not to_pay:
            return lines
        lines += [
            (f"payment_method_{n}", copy.method_line(method, locale))
            for n, method in enumerate(methods, start=1)
        ]
        refs = [copy.ref_of(inv) for inv in to_pay]
        lines.append(("payment_reference_1", copy.reference_line(refs, locale)))
        lines.append(("payment_after_1", copy.after_paying_line(locale)))
        return lines

    def _status(
        self, ctx: AgentContext, invoices: list[InvoiceRecord], locale: copy.Locale
    ) -> list[tuple[str, str]]:
        """Each invoice: not paid, claimed and waiting for staff, or paid (with its ledger row)."""
        lines: list[tuple[str, str]] = []
        for inv in invoices:
            if inv.status == "proof_submitted":
                text = copy.claimed_line(inv, locale)
            elif inv.status == "paid":
                if inv.paid_at is None:  # customer_invoices never returns one; never say "paid"
                    continue
                text = copy.paid_line(inv, inv.paid_at, locale, ctx.timezone)
            else:
                text = copy.unpaid_line(inv, locale)
            lines.append((f"payment_status_{len(lines) + 1}", text))
        return lines or [("payment_nothing_due_1", copy.nothing_due_line(locale))]

    async def _claim(
        self,
        conn: Conn,
        ctx: AgentContext,
        inp: PaymentInput,
        invoices: list[InvoiceRecord],
        locale: copy.Locale,
    ) -> list[tuple[str, str]]:
        """Note the customer's payment on the invoice they mean, or ask which one."""
        if inp.reference is not None:
            target = invoices[0]
        else:
            waiting = [inv for inv in invoices if inv.status in _CLAIMABLE]
            if len(waiting) > 1:
                refs = [copy.ref_of(inv) for inv in waiting]
                return [("payment_which_1", copy.which_line(refs, locale))]
            if not waiting:
                claimed = [inv for inv in invoices if inv.status == "proof_submitted"]
                if not claimed:
                    return [("payment_nothing_due_1", copy.nothing_due_line(locale))]
                return [
                    (f"payment_claim_already_{n}", copy.claimed_line(inv, locale))
                    for n, inv in enumerate(claimed, start=1)
                ]
            target = waiting[0]
        outcome = await record_claim(conn, ctx.client_id, ctx.contact_id, target.id, inp.note)
        logger.info(
            "payment_claim", outcome=outcome, invoice_id=target.id, trace_id=str(ctx.trace_id)
        )
        ref = copy.ref_of(target)
        if outcome == "noted":
            return [("payment_claim_noted_1", copy.claim_noted_line(ref, locale))]
        if outcome == "already_noted":
            return [("payment_claim_already_1", copy.claimed_line(target, locale))]
        if outcome == "already_paid":
            return [("payment_already_paid_1", copy.already_paid_line(ref, locale))]
        # not_open: an invoice the customer may see but cannot claim (waiting for cash).
        return [("payment_status_1", copy.unpaid_line(target, locale))]
