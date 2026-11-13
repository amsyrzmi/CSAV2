"""Tests for the invoice agent: contact security, lookups, and only invoices a customer may see.

The download link is gone: its route never existed (the console answered 501), so the action, the
signing helpers and the link fact went with it (Payments 1).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from csa.agents import invoices as invoices_package
from csa.agents.base import AgentContext
from csa.agents.invoices import InvoiceAgent, InvoiceInput
from csa.agents.invoices import agent as invoice_agent_module
from csa.contracts.callbacks import PendingActionStore
from pydantic import ValidationError

from tests.conftest import requires_docker
from tests.test_console_sessions import SessionWorld
from tests.test_payment_invoices import _ledger

pytest_plugins = ["tests.test_console_sessions"]


def test_the_download_link_is_gone() -> None:
    assert InvoiceAgent.ACTION_RISK == {"status": "read", "list": "read"}
    for name in ("generate_invoice_signature", "verify_invoice_signature"):
        assert not hasattr(invoice_agent_module, name)
        assert not hasattr(invoices_package, name)
    for payload in (
        {"action": "download_link"},
        {"action": "download_link", "invoice_id": "INV-2026-888"},
    ):
        with pytest.raises(ValidationError):
            InvoiceInput.model_validate(payload)
    assert InvoiceInput.model_json_schema()["properties"]["action"]["enum"] == ["status", "list"]


def test_invoice_input_validation() -> None:
    # Status requires either invoice_id or order_id
    with pytest.raises(ValueError, match="either invoice_id or order_id"):
        InvoiceInput(action="status")

    # Valid inputs
    inv_inp = InvoiceInput(action="status", invoice_id="INV-101")
    assert inv_inp.action == "status"
    assert inv_inp.invoice_id == "INV-101"

    list_inp = InvoiceInput(action="list", limit=5)
    assert list_inp.limit == 5


@pytest.mark.asyncio
async def test_invoice_agent_requires_authenticated_contact() -> None:
    agent = InvoiceAgent()
    ctx = MagicMock(spec=AgentContext)
    ctx.client_id = uuid4()
    ctx.contact_id = None  # Unauthenticated
    ctx.pool = MagicMock()
    ctx.idempotency_key = "inv_task"
    ctx.trace_id = uuid4()

    inp = InvoiceInput(action="status", invoice_id="INV-101")
    res = await agent.run(ctx, inp)
    assert res.ok is False
    assert res.error is not None
    assert res.error.code == "unauthorized"


@pytest.mark.asyncio
async def test_invoice_agent_status_found() -> None:
    agent = InvoiceAgent()
    ctx = MagicMock(spec=AgentContext)
    ctx.client_id = uuid4()
    ctx.contact_id = uuid4()
    ctx.pool = MagicMock()
    ctx.idempotency_key = "inv_task_status"
    ctx.trace_id = uuid4()

    mock_row = {
        "id": "INV-2026-101",
        "client_id": ctx.client_id,
        "contact_id": ctx.contact_id,
        "order_id": "ORD-555",
        "status": "paid",
        "amount": 250.00,
        "currency": "MYR",
        "issued_at": datetime(2026, 4, 1),
        "due_at": datetime(2026, 4, 15),
        "paid_at": datetime(2026, 4, 5),
        "payment_ref": "FPX-889900",
        "email": "customer@example.com",
        "full_name": "Test Customer",
    }

    with (
        patch("csa.agents.invoices.agent.tx") as mock_tx,
        patch("csa.core.db.q.fetch_one") as mock_fetch,
    ):
        mock_conn = AsyncMock()
        mock_tx.return_value.__aenter__.return_value = mock_conn
        mock_fetch.return_value = mock_row

        inp = InvoiceInput(action="status", invoice_id="INV-2026-101")
        res = await agent.run(ctx, inp)

        assert res.ok is True
        assert len(res.data.facts) == 1
        fact_text = res.data.facts[0].text
        assert "Invoice #INV-2026-101" in fact_text
        assert "PAID" in fact_text
        assert "MYR 250.00" in fact_text
        assert "Ref: FPX-889900" in fact_text


@pytest.fixture
async def seeded(sessions: SessionWorld) -> tuple[AgentContext, dict[str, str]]:
    """A customer with an invoice in every status, and a paid one with no payment (an old seed)."""
    ledger = await _ledger(sessions, sessions.tenants[0])
    contact = await ledger.contact()
    now = datetime.now(UTC)
    ids: dict[str, str] = {}
    for status in ("draft", "issued", "overdue", "proof_submitted", "void", "paid"):
        ids[status] = await ledger.invoice(
            status=status, contact=contact, paid_at=now if status == "paid" else None
        )
    await ledger.ledger_payment(ids["paid"], received_at=now - timedelta(hours=1))
    ids["seed"] = await ledger.invoice(status="paid", contact=contact, paid_at=now)
    ctx = AgentContext(
        client_id=ledger.tenant,
        contact_id=contact,
        trace_id=uuid4(),
        conversation_id=None,
        idempotency_key="synthetic-invoice-task",
        channel="telegram",
        pool=sessions.pool,
        pending_actions=PendingActionStore(sessions.pool),
    )
    return ctx, ids


@requires_docker
@pytest.mark.asyncio
async def test_only_invoices_a_customer_may_see_are_listed_or_looked_up(
    seeded: tuple[AgentContext, dict[str, str]],
) -> None:
    ctx, ids = seeded
    agent = InvoiceAgent()
    listed = await agent.run(ctx, InvoiceInput(action="list"))
    assert listed.ok
    shown = {fact.id.removeprefix("invoice_") for fact in listed.facts}
    assert shown == {ids[s] for s in ("issued", "overdue", "proof_submitted", "paid")}
    for hidden in ("draft", "void", "seed"):
        looked = await agent.run(ctx, InvoiceInput(action="status", invoice_id=ids[hidden]))
        assert looked.ok is False and looked.error is not None
        assert looked.error.code == "invoice_not_found", hidden
    paid = await agent.run(ctx, InvoiceInput(action="status", invoice_id=ids["paid"]))
    assert paid.ok and "PAID" in paid.facts[0].text
