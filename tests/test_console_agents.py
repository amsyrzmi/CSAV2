"""Catalogue status never asserts live health from a subscription or configuration."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from csa.console.agents import list_agents
from csa.console.capabilities import CapabilityOut
from csa.console.contracts import Role


def _cap(name: str) -> CapabilityOut:
    return CapabilityOut(
        id=uuid4(),
        name=name,
        is_enabled=True,
        client_enabled=True,
        active=True,
        circuit_state="closed",
        fail_count=0,
        capability_kind="dispatched",
        updated_at=datetime.now(UTC),
    )


@pytest.mark.parametrize(
    ("role", "subscribe", "toggle"),
    [
        (Role.PLATFORM_ADMIN, True, True),
        (Role.TENANT_ADMIN, False, True),
        (Role.TENANT_STAFF, False, False),
        (Role.READ_ONLY, False, False),
        (None, False, False),
    ],
)
async def test_catalogue_permissions_and_implementation_states(
    role: Role | None, subscribe: bool, toggle: bool
) -> None:
    caps = [_cap(name) for name in ("invoice_agent", "booking_agent", "ecommerce_agent", "unknown")]
    with patch("csa.console.agents.list_capabilities", return_value=caps):
        rows = {row.name: row for row in await list_agents(AsyncMock(), uuid4(), role=role)}
    assert rows["invoice_agent"].readiness == "available"
    assert "PDF" in rows["invoice_agent"].detail
    assert rows["booking_agent"].implementation == "prepared_only"
    assert rows["ecommerce_agent"].readiness == "setup_required"
    assert rows["lhdn_agent"].implementation == "not_implemented"
    assert not rows["lhdn_agent"].can_view_specialized
    assert rows["unknown"].implementation == "not_implemented"
    assert rows["invoice_agent"].can_subscribe is subscribe
    assert rows["invoice_agent"].can_toggle is toggle


@pytest.mark.parametrize(
    ("tei", "compatible", "incompatible", "expected"),
    [
        (True, 0, 0, "configured_unverified"),
        (True, 5, 0, "configured_unverified"),
        (True, 0, 5, "reindex_required"),
        (True, 3, 5, "configured_unverified"),
        (False, 0, 0, "setup_required"),
        (False, 0, 5, "reindex_required"),
    ],
)
async def test_kb_status_separates_configuration_model_compatibility_and_live_health(
    tei: bool, compatible: int, incompatible: int, expected: str
) -> None:
    with (
        patch("csa.console.agents.list_capabilities", return_value=[_cap("kb_agent")]),
        patch("csa.console.agents.tx") as transaction,
        patch(
            "csa.core.db.q.fetch_one",
            return_value={"compatible_chunks": compatible, "incompatible_chunks": incompatible},
        ),
    ):
        transaction.return_value.__aenter__.return_value = AsyncMock()
        rows = await list_agents(
            AsyncMock(),
            uuid4(),
            role=Role.READ_ONLY,
            kb_model="synthetic-e5" if tei else "hash-384",
            kb_tei_configured=tei,
        )
    kb = next(row for row in rows if row.name == "kb_agent")
    assert kb.readiness == expected
    assert kb.compatible_chunks == compatible
    assert kb.incompatible_chunks == incompatible
    assert "http" not in kb.model_dump_json()


async def test_query_failure_is_not_a_healthy_or_zero_fallback() -> None:
    with (
        patch("csa.console.agents.list_capabilities", return_value=[_cap("kb_agent")]),
        patch("csa.console.agents.tx") as transaction,
        patch("csa.core.db.q.fetch_one", side_effect=RuntimeError("query failed")),
    ):
        transaction.return_value.__aenter__.return_value = AsyncMock()
        with pytest.raises(RuntimeError, match="query failed"):
            await list_agents(AsyncMock(), uuid4(), role=Role.READ_ONLY, kb_model="synthetic")
