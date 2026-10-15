"""Invoice agent: contact-authorized status, listing, and signed download links (LG13)."""

from __future__ import annotations

import hmac
import time
from hashlib import sha256
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from csa.agents.base import AgentContext, SideEffects
from csa.agents.invoices.models import InvoiceInput
from csa.contracts.facts import (
    AgentError,
    AgentResult,
    AgentResultData,
    Fact,
    ResultMeta,
    Ui,
)
from csa.contracts.plan import RiskClass
from csa.core import db
from csa.core.settings import get_settings
from csa.core.tenancy import tx

DEFAULT_LINK_TTL_SECONDS = 900  # 15 minutes


def generate_invoice_signature(
    client_id: UUID, invoice_id: str, expires_at: int, secret: str
) -> str:
    """Generate HMAC-SHA256 signature for secure expiring invoice download URLs."""
    payload = f"{client_id}:{invoice_id}:{expires_at}".encode()
    return hmac.new(secret.encode(), payload, sha256).hexdigest()


def verify_invoice_signature(
    client_id: UUID, invoice_id: str, expires_at: int, sig: str, secret: str
) -> bool:
    """Verify an expiring invoice download signature."""
    if int(time.time()) > expires_at:
        return False
    expected = generate_invoice_signature(client_id, invoice_id, expires_at, secret)
    return hmac.compare_digest(expected, sig)


class InvoiceAgent:
    name = "invoice_agent"
    Input = InvoiceInput
    side_effects: SideEffects = "none"
    timeout_ms = 10000
    max_retries = 1
    ACTION_RISK: dict[str, RiskClass] = {
        "status": "read",
        "list": "read",
        "download_link": "read",
    }

    def risk(self, inp: BaseModel) -> RiskClass:
        assert isinstance(inp, InvoiceInput)
        return self.ACTION_RISK[inp.action]

    async def run(self, ctx: AgentContext, inp: BaseModel) -> AgentResult:
        assert isinstance(inp, InvoiceInput)
        handler = {
            "status": self._status,
            "list": self._list,
            "download_link": self._download_link,
        }[inp.action]
        return await handler(ctx, inp)

    async def _status(self, ctx: AgentContext, inp: InvoiceInput) -> AgentResult:
        if not ctx.contact_id:
            return self._error(
                ctx,
                "unauthorized",
                "validation",
                "invoice lookup requires authenticated contact context",
            )

        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            row = await db.q.fetch_one(
                "invoices/get_by_contact.sql",
                (ctx.client_id, inp.invoice_id, inp.order_id, ctx.contact_id),
                conn=conn,
            )

        if not row:
            return self._error(
                ctx,
                "invoice_not_found",
                "not_found",
                f"no invoice matching #{inp.invoice_id or inp.order_id} found for contact",
                hint="Please verify the invoice number or order reference.",
            )

        due_text = row["due_at"].strftime("%Y-%m-%d") if row.get("due_at") else "N/A"
        amount_text = f"{row['currency']} {row['amount']:.2f}"
        ref_text = f" (Ref: {row['payment_ref']})" if row.get("payment_ref") else ""
        desc = (
            f"Invoice #{row['id']}: Status is {row['status'].upper()}, "
            f"Total: {amount_text}, Due: {due_text}{ref_text}."
        )

        facts = [
            Fact(
                id=f"invoice_{row['id']}",
                text=desc,
                kind="verbatim",
                source="invoice_agent",
            ),
        ]
        return self._ok(ctx, facts)

    async def _list(self, ctx: AgentContext, inp: InvoiceInput) -> AgentResult:
        if not ctx.contact_id:
            return self._error(
                ctx,
                "unauthorized",
                "validation",
                "invoice list requires authenticated contact context",
            )

        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            rows = await db.q.fetch_all(
                "invoices/list_by_contact.sql",
                (ctx.client_id, ctx.contact_id, inp.limit),
                conn=conn,
            )

        if not rows:
            return self._ok(
                ctx,
                [
                    Fact(
                        id="invoice_empty_list",
                        text="No invoices found for this account.",
                        kind="verbatim",
                        source="invoice_agent",
                    )
                ],
            )

        facts: list[Fact] = []
        for r in rows:
            amount_str = f"{r['currency']} {r['amount']:.2f}"
            issued_str = r["issued_at"].strftime("%Y-%m-%d") if r.get("issued_at") else "N/A"
            line = f"#{r['id']} | Status: {r['status']} | Amount: {amount_str} | Date: {issued_str}"
            facts.append(
                Fact(
                    id=f"invoice_{r['id']}",
                    text=line,
                    kind="verbatim",
                    source="invoice_agent",
                )
            )
        return self._ok(ctx, facts)

    async def _download_link(self, ctx: AgentContext, inp: InvoiceInput) -> AgentResult:
        if not ctx.contact_id:
            return self._error(
                ctx,
                "unauthorized",
                "validation",
                "download link requires authenticated contact context",
            )

        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            row = await db.q.fetch_one(
                "invoices/get_by_contact.sql",
                (ctx.client_id, inp.invoice_id, inp.order_id, ctx.contact_id),
                conn=conn,
            )

        if not row:
            return self._error(
                ctx,
                "invoice_not_found",
                "not_found",
                f"no invoice matching #{inp.invoice_id or inp.order_id} found for contact",
            )

        try:
            settings = get_settings()
            secret = (
                settings.INVOICE_SIGNING_SECRET.get_secret_value()
                if settings.INVOICE_SIGNING_SECRET
                else settings.CREDENTIALS_KEY.get_secret_value()
            )
            base_url = str(settings.PUBLIC_BASE_URL).rstrip("/")
        except Exception:
            secret = "fallback_dev_signing_key_secret_123"
            base_url = "http://localhost:18080"
        expires_at = int(time.time()) + DEFAULT_LINK_TTL_SECONDS
        sig = generate_invoice_signature(ctx.client_id, row["id"], expires_at, secret)

        download_url = (
            f"{base_url}/api/v1/invoices/{row['id']}/download"
            f"?client_id={ctx.client_id}&expires={expires_at}&sig={sig}"
        )

        facts = [
            Fact(
                id=f"invoice_dl_{row['id']}",
                text=f"Secure download link for invoice #{row['id']}: {download_url} (valid 15m).",
                kind="verbatim",
                source="invoice_agent",
            )
        ]
        return self._ok(ctx, facts)

    def _ok(
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
        self,
        ctx: AgentContext,
        code: str,
        category: Any,
        message: str,
        *,
        hint: str | None = None,
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
