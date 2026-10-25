"""Service package records: migration backfill from existing capability switches."""

from __future__ import annotations

from pathlib import Path
from typing import LiteralString, cast

import pytest

from tests.conftest import requires_docker
from tests.test_console_sessions import SessionWorld

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]
MIGRATION = Path(__file__).parents[1] / "db/migrations/0450_console_service_packages.sql"


async def test_backfill_marks_fully_enabled_packages_active(sessions: SessionWorld) -> None:
    tenant = sessions.tenants[0]
    for name in ("kb_agent", "ticket_agent", "escalation_agent", "order_approval"):
        await sessions.q(
            "UPDATE capability_registry SET is_enabled=true WHERE client_id=%s AND name=%s",
            tenant,
            name,
        )
    await sessions.q(
        "UPDATE capability_registry SET is_enabled=true WHERE client_id=%s AND name='order_agent'",
        tenant,
    )
    await sessions.q("DELETE FROM console_service_packages WHERE client_id=%s", tenant)
    backfill = MIGRATION.read_text().split("-- BACKFILL")[1]
    await sessions.q(cast(LiteralString, backfill))
    rows = await sessions.q(
        "SELECT package, status, reference FROM console_service_packages"
        " WHERE client_id=%s ORDER BY package",
        tenant,
    )
    assert rows == [
        {"package": "customer_support", "status": "active", "reference": "backfill-0450"}
    ]
