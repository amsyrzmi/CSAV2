"""Tests for Console API: agent capabilities, subscription toggling, and specialized tabs (LG13)."""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from csa.console.capabilities import CapabilityOut
from csa.console.last_change import LastChange, last_change_from_row
from csa.console.router import build_router
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError


@pytest.fixture
def client_app() -> tuple[TestClient, uuid.UUID, str]:
    token = "a" * 32
    client_id = uuid.uuid4()
    app = FastAPI()
    app.state.pool = AsyncMock()
    app.include_router(build_router(token, machine_client_ids=frozenset({client_id})))
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
        assert data[0]["last_change"] is None  # Filled only on the services read

        # 2. Specialized tabs overview
        tabs_resp = client.get("/console/v1/tabs/specialized", headers=headers)
        assert tabs_resp.status_code == 200
        tabs = tabs_resp.json()
        tab_ids = [t["tab_id"] for t in tabs]
        assert "lhdn_myinvois" in tab_ids
        assert "ecommerce" in tab_ids
        assert "knowledge_base" in tab_ids
        assert "invoices" in tab_ids

        # Switches do not imply a live MyInvois integration.
        lhdn_tab = next(t for t in tabs if t["tab_id"] == "lhdn_myinvois")
        assert lhdn_tab["is_subscribed"] is True
        assert lhdn_tab["is_active"] is True
        assert lhdn_tab["metrics"] == {}
        assert lhdn_tab["implementation"] == "not_implemented"
        assert lhdn_tab["readiness"] == "setup_required"
        assert lhdn_tab["can_view"] is False

        # E-Commerce tab is subscribed but not active (client toggle off)
        ecom_tab = next(t for t in tabs if t["tab_id"] == "ecommerce")
        assert ecom_tab["is_subscribed"] is True
        assert ecom_tab["is_active"] is False
        assert ecom_tab["readiness"] == "setup_required"
        assert ecom_tab["metrics"] == {}


def test_machine_cannot_toggle_capability_subscription(
    client_app: tuple[TestClient, uuid.UUID, str],
) -> None:
    client, client_id, token = client_app
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Client-Id": str(client_id),
    }

    with patch("csa.console.capabilities.toggle_capability") as mutate:
        response = client.patch(
            "/console/v1/capabilities/lhdn_agent",
            json={"is_enabled": False},
            headers=headers,
        )
        assert response.status_code == 403
        mutate.assert_not_called()

    # A correctly signed machine token cannot select an unconfigured tenant.
    response = client.get(
        "/console/v1/capabilities",
        headers={**headers, "X-Client-Id": str(uuid.uuid4())},
    )
    assert response.status_code == 403


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
        # The pool is a mock; the package gate itself is covered against Postgres.
        patch("csa.console.services.require_package", new_callable=AsyncMock) as gate,
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
        assert dl_resp.status_code == 501
        assert "M6" in dl_resp.json()["detail"]
        assert [c.args[1:] for c in gate.await_args_list] == [
            (client_id, frozenset({"orders_products"}), frozenset({"active"}))
        ] * 2


# --- last_change (0502): action derivation and the actor rule, without a database.

AT = datetime(2026, 10, 29, 9, 30)


def _row(
    action: str,
    details: dict[str, object],
    name: str | None = "Synthetic Admin",
    platform: bool = True,
) -> dict:
    return {
        "action": action,
        "at": AT,
        "actor_name": name,
        "actor_is_platform_admin": platform,
        "details": details,
    }


def _flags(before: tuple[bool, bool], after: tuple[bool, bool]) -> dict[str, object]:
    return {
        "before": {"is_enabled": before[0], "client_enabled": before[1]},
        "after": {"is_enabled": after[0], "client_enabled": after[1]},
    }


@pytest.mark.parametrize(
    ("action", "details", "expected"),
    [
        ("service.requested", {"before": None, "after": "requested"}, "requested"),
        ("service.activated", {"before": "requested", "after": "active"}, "activated"),
        ("service.suspended", {"before": "active", "after": "suspended"}, "suspended"),
        ("capability.updated", _flags((False, False), (True, False)), "subscribed"),
        ("capability.updated", _flags((True, True), (False, True)), "unsubscribed"),
        ("capability.updated", _flags((True, False), (True, True)), "tenant_switch_on"),
        ("capability.updated", _flags((True, True), (True, False)), "tenant_switch_off"),
        # Both switches changed in one row: the subscription wins.
        ("capability.updated", _flags((False, False), (True, True)), "subscribed"),
        ("capability.updated", _flags((True, True), (False, False)), "unsubscribed"),
        # Nothing recognisable changed.
        ("capability.updated", _flags((True, True), (True, True)), None),
        ("capability.updated", {}, None),
        ("capability.updated", {"before": None, "after": "x"}, None),
        ("service.unknown", {}, None),
    ],
)
def test_last_change_action_mapping(
    action: str, details: dict[str, object], expected: str | None
) -> None:
    change = last_change_from_row(_row(action, details), platform_admin=True)
    if expected is None:
        assert change is None
    else:
        assert change is not None and change.action == expected and change.at == AT


def test_last_change_actor_is_a_name_only_for_platform_admins() -> None:
    row = _row("service.activated", {"before": None, "after": "active"})
    shown = last_change_from_row(row, platform_admin=True)
    assert shown is not None and shown.actor == "Synthetic Admin"
    hidden = last_change_from_row(row, platform_admin=False)
    assert hidden is not None and hidden.actor == "a platform administrator"
    assert "Synthetic Admin" not in hidden.model_dump_json()
    # A missing or empty actor name falls back to the role label even for platform admins.
    for name in (None, ""):
        anonymous = last_change_from_row({**row, "actor_name": name}, platform_admin=True)
        assert anonymous is not None and anonymous.actor == "a platform administrator"
    assert last_change_from_row(None, platform_admin=True) is None


def test_last_change_labels_a_tenant_actor_as_a_team_member_for_tenant_viewers() -> None:
    row = _row(
        "service.requested", {"before": None, "after": "requested"}, "Synthetic Member", False
    )
    team = last_change_from_row(row, platform_admin=False)
    assert team is not None and team.actor == "a member of your team"
    assert "Synthetic Member" not in team.model_dump_json()
    named = last_change_from_row(row, platform_admin=True)
    assert named is not None and named.actor == "Synthetic Member"
    # No actor (system or legacy rows): the function reports no name and false. Tenant viewers
    # get neutral wording, not "a member of your team"; platform viewers keep the role label.
    gone = {**row, "actor_name": None, "actor_is_platform_admin": False}
    tenant_view = last_change_from_row(gone, platform_admin=False)
    assert tenant_view is not None and tenant_view.actor == "an administrator"
    platform_view = last_change_from_row(gone, platform_admin=True)
    assert platform_view is not None and platform_view.actor == "a platform administrator"


def test_last_change_model_is_closed_and_capability_field_is_optional() -> None:
    with pytest.raises(ValidationError):
        LastChange.model_validate(
            {"action": "activated", "at": AT, "actor": "x", "actor_id": str(uuid.uuid4())}
        )
    with pytest.raises(ValidationError):
        LastChange.model_validate({"action": "deleted", "at": AT, "actor": "x"})
    cap = CapabilityOut(
        id=uuid.uuid4(),
        name="kb_agent",
        is_enabled=True,
        client_enabled=True,
        active=True,
        circuit_state="closed",
        fail_count=0,
        capability_kind="dispatched",
        updated_at=AT,
    )
    assert cap.last_change is None and cap.model_dump()["last_change"] is None
