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


async def test_backfill_edge_cases_and_grants(sessions: SessionWorld) -> None:
    tenant = sessions.tenants[0]
    await sessions.q(
        "UPDATE capability_registry SET is_enabled=true WHERE client_id=%s"
        " AND name IN ('kb_agent','ticket_agent','escalation_agent',"
        "'order_approval','order_agent')",
        tenant,
    )
    # A package whose member row is missing must not count as fully enabled.
    await sessions.q(
        "DELETE FROM capability_registry WHERE client_id=%s AND name='invoice_agent'", tenant
    )
    # A package with no member rows at all (zero-member rule).
    await sessions.q(
        "DELETE FROM capability_registry WHERE client_id=%s AND name='subscription_agent'", tenant
    )
    await sessions.q(
        "INSERT INTO hr_agent_settings(client_id,name,enabled) VALUES (%s,'hr_directory',true)"
        " ON CONFLICT (client_id,name) DO UPDATE SET enabled=true",
        tenant,
    )
    await sessions.q("UPDATE email_settings SET enabled=true WHERE client_id=%s", tenant)
    await sessions.q("DELETE FROM console_service_packages WHERE client_id=%s", tenant)
    await sessions.q(
        "INSERT INTO console_service_packages(client_id,package,status,reference)"
        " VALUES (%s,'customer_support','suspended','keep-me')",
        tenant,
    )
    backfill = MIGRATION.read_text().split("-- BACKFILL")[1]
    await sessions.q(cast(LiteralString, backfill))
    rows = await sessions.q(
        "SELECT package, status, reference FROM console_service_packages"
        " WHERE client_id=%s ORDER BY package",
        tenant,
    )
    assert rows == [
        {"package": "customer_support", "status": "suspended", "reference": "keep-me"},
        {"package": "hr", "status": "active", "reference": "backfill-0450"},
        {"package": "staff_email", "status": "active", "reference": "backfill-0450"},
    ]
    grants = await sessions.q(
        "SELECT has_table_privilege('csa_ro','public.console_service_packages','SELECT') AS ro,"
        " has_table_privilege('csa_identity','public.console_service_packages','SELECT') AS ident,"
        " has_table_privilege('csa_app','public.console_service_packages','UPDATE') AS app"
    )
    assert grants == [{"ro": False, "ident": False, "app": True}]
