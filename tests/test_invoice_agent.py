"""Tests for Invoices agent: contact security, lookups, and expiring signed downloads (LG13)."""

from __future__ import annotations

import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from csa.agents.base import AgentContext
from csa.agents.invoices import (
    InvoiceAgent,
    InvoiceInput,
    generate_invoice_signature,
    verify_invoice_signature,
)


def test_invoice_signature_and_tamper_proofing() -> None:
    client_id = uuid4()
    invoice_id = "INV-2026-001"
    secret = "test_signing_secret_key_1234567890"
    now_ts = int(time.time())
    expires_at = now_ts + 900  # 15 mins

    # 1. Valid signature verifies
    sig = generate_invoice_signature(client_id, invoice_id, expires_at, secret)
    assert isinstance(sig, str)
    assert len(sig) == 64
    assert verify_invoice_signature(client_id, invoice_id, expires_at, sig, secret) is True

    # 2. Tampered invoice_id fails
    assert verify_invoice_signature(client_id, "INV-2026-999", expires_at, sig, secret) is False

    # 3. Tampered client_id fails
    assert verify_invoice_signature(uuid4(), invoice_id, expires_at, sig, secret) is False

    # 4. Wrong secret fails
    assert verify_invoice_signature(client_id, invoice_id, expires_at, sig, "wrong_secret") is False

    # 5. Expired timestamp fails
    expired_ts = now_ts - 60
    expired_sig = generate_invoice_signature(client_id, invoice_id, expired_ts, secret)
    assert verify_invoice_signature(client_id, invoice_id, expired_ts, expired_sig, secret) is False


def test_invoice_input_validation() -> None:
    # Status requires either invoice_id or order_id
    with pytest.raises(ValueError, match="either invoice_id or order_id"):
        InvoiceInput(action="status")

    # Download link requires either invoice_id or order_id
    with pytest.raises(ValueError, match="either invoice_id or order_id"):
        InvoiceInput(action="download_link")

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


@pytest.mark.asyncio
async def test_invoice_agent_download_link() -> None:
    agent = InvoiceAgent()
    ctx = MagicMock(spec=AgentContext)
    ctx.client_id = uuid4()
    ctx.contact_id = uuid4()
    ctx.pool = MagicMock()
    ctx.idempotency_key = "inv_task_dl"
    ctx.trace_id = uuid4()

    mock_row = {
        "id": "INV-2026-888",
        "client_id": ctx.client_id,
        "contact_id": ctx.contact_id,
        "order_id": None,
        "status": "unpaid",
    }

    with (
        patch("csa.agents.invoices.agent.tx") as mock_tx,
        patch("csa.core.db.q.fetch_one") as mock_fetch,
    ):
        mock_conn = AsyncMock()
        mock_tx.return_value.__aenter__.return_value = mock_conn
        mock_fetch.return_value = mock_row

        inp = InvoiceInput(action="download_link", invoice_id="INV-2026-888")
        res = await agent.run(ctx, inp)

        assert res.ok is True
        assert len(res.data.facts) == 1
        dl_text = res.data.facts[0].text
        assert "INV-2026-888" in dl_text
        assert "/api/v1/invoices/INV-2026-888/download" in dl_text
        assert "sig=" in dl_text
        assert "expires=" in dl_text
