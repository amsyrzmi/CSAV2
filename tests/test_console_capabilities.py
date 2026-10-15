"""Tests for Console API: agent capabilities, subscription toggling, and specialized tabs (LG13)."""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from csa.console.router import build_router
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client_app() -> tuple[TestClient, uuid.UUID, str]:
    token = "a" * 32
    client_id = uuid.uuid4()
    app = FastAPI()
    app.state.pool = AsyncMock()
    app.include_router(build_router(token))
    client = TestClient(app)
    return client, client_id, token


def test_capabilities_and_specialized_tabs(
    client_app: tuple[TestClient, uuid.UUID, str],
) -> None:
    client, client_id, token = client_app
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Client-Id": str(client_id),
    }

    mock_caps_rows = [
        {
            "id": uuid.uuid4(),
            "name": "lhdn_agent",
            "is_enabled": True,
            "client_enabled": True,
            "circuit_state": "closed",
            "fail_count": 0,
            "capability_kind": "dispatched",
            "updated_at": datetime(2026, 4, 1),
        },
        {
            "id": uuid.uuid4(),
            "name": "ecommerce_agent",
            "is_enabled": True,
            "client_enabled": False,  # Client turned off
            "circuit_state": "closed",
            "fail_count": 0,
            "capability_kind": "dispatched",
            "updated_at": datetime(2026, 4, 1),
        },
    ]

    with (
        patch("csa.console.capabilities.tx") as mock_tx,
        patch("csa.core.db.q.fetch_all") as mock_fetch,
    ):
        mock_conn = AsyncMock()
        mock_tx.return_value.__aenter__.return_value = mock_conn
        mock_fetch.return_value = mock_caps_rows

        # 1. List capabilities
        resp = client.get("/console/v1/capabilities", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["name"] == "lhdn_agent"
        assert data[0]["active"] is True
        assert data[1]["name"] == "ecommerce_agent"
        assert data[1]["active"] is False  # Because client_enabled is False

        # 2. Specialized tabs overview
        tabs_resp = client.get("/console/v1/tabs/specialized", headers=headers)
        assert tabs_resp.status_code == 200
        tabs = tabs_resp.json()
        tab_ids = [t["tab_id"] for t in tabs]
        assert "lhdn_myinvois" in tab_ids
        assert "ecommerce" in tab_ids
        assert "knowledge_base" in tab_ids
        assert "invoices" in tab_ids

        # LHDN tab has subscription and active true
        lhdn_tab = next(t for t in tabs if t["tab_id"] == "lhdn_myinvois")
        assert lhdn_tab["is_subscribed"] is True
        assert lhdn_tab["is_active"] is True
        assert lhdn_tab["metrics"]["compliance_version"] == "1.1"

        # E-Commerce tab is subscribed but not active (client toggle off)
        ecom_tab = next(t for t in tabs if t["tab_id"] == "ecommerce")
        assert ecom_tab["is_subscribed"] is True
        assert ecom_tab["is_active"] is False


def test_toggle_capability_subscription(
    client_app: tuple[TestClient, uuid.UUID, str],
) -> None:
    client, client_id, token = client_app
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Client-Id": str(client_id),
    }

    mock_row = {
        "id": uuid.uuid4(),
        "name": "lhdn_agent",
        "is_enabled": False,  # Turned off by admin
        "client_enabled": True,
        "circuit_state": "closed",
        "fail_count": 0,
        "capability_kind": "dispatched",
        "updated_at": datetime(2026, 4, 1),
    }

    with (
        patch("csa.console.capabilities.tx") as mock_tx,
        patch("csa.core.db.q.fetch_one") as mock_fetch_one,
        patch("csa.core.db.q.fetch_all") as mock_fetch_all,
    ):
        mock_conn = AsyncMock()
        mock_tx.return_value.__aenter__.return_value = mock_conn
        mock_fetch_one.return_value = {"id": mock_row["id"], "name": "lhdn_agent"}
        mock_fetch_all.return_value = [mock_row]

        patch_resp = client.patch(
            "/console/v1/capabilities/lhdn_agent",
            json={"is_enabled": False, "changed_by": "super_admin"},
            headers=headers,
        )
        assert patch_resp.status_code == 200
        result = patch_resp.json()
        assert result["name"] == "lhdn_agent"
        assert result["is_enabled"] is False
        assert result["active"] is False


def test_invoices_console_endpoints(
    client_app: tuple[TestClient, uuid.UUID, str],
) -> None:
    client, client_id, token = client_app
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Client-Id": str(client_id),
    }

    mock_invoices = [
        {
            "id": "INV-1001",
            "client_id": client_id,
            "contact_id": uuid.uuid4(),
            "order_id": "ORD-123",
            "status": "unpaid",
            "amount": 199.50,
            "currency": "MYR",
            "issued_at": datetime(2026, 4, 10),
            "due_at": datetime(2026, 4, 25),
            "paid_at": None,
            "payment_ref": None,
            "email": "user@domain.com",
            "full_name": "John Doe",
        }
    ]

    with (
        patch("csa.console.router.tx") as mock_tx,
        patch("csa.core.db.q.fetch_all") as mock_fetch,
    ):
        mock_conn = AsyncMock()
        mock_tx.return_value.__aenter__.return_value = mock_conn
        mock_fetch.return_value = mock_invoices

        # 1. List invoices
        resp = client.get("/console/v1/invoices", headers=headers)
        assert resp.status_code == 200
        invoices = resp.json()
        assert len(invoices) == 1
        assert invoices[0]["id"] == "INV-1001"
        assert invoices[0]["contact_name"] == "John Doe"

        # 2. Download link
        dl_resp = client.get("/console/v1/invoices/INV-1001/download-link", headers=headers)
        assert dl_resp.status_code == 200
        dl_data = dl_resp.json()
        assert dl_data["invoice_id"] == "INV-1001"
        assert "sig=" in dl_data["download_url"]
        assert dl_data["expires_at"] > 0
