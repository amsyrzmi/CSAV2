"""WhatsApp channel schema and SQL tests (migration 0470)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import LiteralString, cast

import psycopg
import pytest

from tests.conftest import owner_dsn_for, requires_docker

PHONE_NUMBER_ID = "109876543210"


def _seed_tenant(conn: psycopg.Connection, phone_number_id: str) -> tuple[str, str, str]:
    client_id, credential_id, endpoint_id = (str(uuid.uuid4()) for _ in range(3))
    conn.execute(
        "INSERT INTO clients (id, slug, name, platform_version) VALUES (%s, %s, %s, %s)",
        (client_id, f"wa-{client_id}", f"wa-{client_id}", "wa-test"),
    )
    conn.execute(
        "INSERT INTO channel_credentials (id, client_id, channel, shop_id, access_token) "
        "VALUES (%s, %s, 'whatsapp', %s, 'enc')",
        (credential_id, client_id, phone_number_id),
    )
    conn.execute(
        "INSERT INTO webhook_endpoints (id, client_id, public_id, credential_id) "
        "VALUES (%s, %s, %s, %s)",
        (endpoint_id, client_id, f"whatsapp-{uuid.uuid4().hex[:20]}", credential_id),
    )
    return client_id, credential_id, endpoint_id


@requires_docker
async def test_0470_columns_indexes_and_function_exist(app_dsn: str, pg_container: object) -> None:
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        cols = {
            (r[0], r[1])
            for r in conn.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public'"
            )
        }
        assert ("conversations", "last_inbound_at") in cols
        assert ("outbound_deliveries", "provider_message_id") in cols
        assert ("outbound_deliveries", "provider_status") in cols
        indexes = {r[0] for r in conn.execute("SELECT indexname FROM pg_indexes")}
        assert "channel_credentials_whatsapp_number_key" in indexes
        assert "outbound_deliveries_provider_message_idx" in indexes
        checks = " ".join(
            r[0]
            for r in conn.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'public.outbound_deliveries'::regclass AND contype = 'c'"
            )
        )
        assert "provider_status" in checks and "delivered" in checks
        client_id, credential_id, endpoint_id = _seed_tenant(conn, PHONE_NUMBER_ID)
        assert conn.execute(
            "SELECT has_function_privilege("
            "'csa_app', 'public.resolve_whatsapp_route(text)', 'EXECUTE')"
        ).fetchone() == (True,)
    with psycopg.connect(app_dsn, autocommit=True) as app:
        row = app.execute(
            "SELECT endpoint_id, client_id, credential_id, is_enabled "
            "FROM public.resolve_whatsapp_route(%s)",
            (PHONE_NUMBER_ID,),
        ).fetchone()
    assert row is not None
    assert [str(v) for v in row[:3]] == [endpoint_id, client_id, credential_id]


@requires_docker
async def test_whatsapp_number_is_unique_across_tenants(app_dsn: str, pg_container: object) -> None:
    number = "209876543210"
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        _seed_tenant(conn, number)
        other = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO clients (id, slug, name, platform_version) VALUES (%s, %s, %s, %s)",
            (other, f"wa-{other}", f"wa-{other}", "wa-test"),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO channel_credentials (client_id, channel, shop_id) "
                "VALUES (%s, 'whatsapp', %s)",
                (other, number),
            )


@requires_docker
async def test_resolve_whatsapp_contact_links_exact_digits_or_creates(
    app_dsn: str, pg_container: object
) -> None:
    """Identity wins, a single exact digit match links, anything else creates."""
    stmt = Path("db/sql/ingress/resolve_whatsapp_contact.sql").read_text()
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        client_id, _, _ = _seed_tenant(conn, "309876543210")
        conn.execute(
            "INSERT INTO contacts (client_id, phone, full_name) "
            "VALUES (%s, '+60 12-345 6789', 'Aina')",
            (client_id,),
        )

        def run(wa_id: str, name: str) -> tuple[object, ...]:
            row = conn.execute(
                cast(LiteralString, stmt),
                {"client_id": client_id, "wa_id": wa_id, "display_name": name},
            ).fetchone()
            assert row is not None
            return row

        linked = run("60123456789", "Other")
        assert linked[1] == "Aina" and linked[2] is False
        again = run("60123456789", "Other")
        assert again[0] == linked[0] and again[2] is False
        created = run("60199999999", "New Person")
        assert created[1] == "New Person" and created[2] is True
        assert conn.execute(
            "SELECT phone FROM contacts WHERE id = %s", (created[0],)
        ).fetchone() == ("+60199999999",)
        # a near-miss number must not link to the existing contact
        assert run("6012345678", "Short")[2] is True
