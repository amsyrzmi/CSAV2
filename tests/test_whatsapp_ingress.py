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


def _sql(name: str) -> LiteralString:
    return cast(LiteralString, Path("db/sql", name).read_text())


@requires_docker
async def test_provider_status_never_regresses(app_dsn: str, pg_container: object) -> None:
    """Failed outranks everything; delivered cannot overwrite read."""
    stmt = _sql("delivery/record_provider_status.sql")
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        client_id, _, endpoint_id = _seed_tenant(conn, "409876543210")
        contact = conn.execute(
            "INSERT INTO contacts (client_id, phone, full_name) "
            "VALUES (%s, '+1', 'C') "
            "RETURNING id",
            (client_id,),
        ).fetchone()
        assert contact is not None
        conversation = conn.execute(
            "INSERT INTO conversations (client_id, contact_id, channel, channel_thread) "
            "VALUES (%s, %s, 'whatsapp', '1') RETURNING id",
            (client_id, contact[0]),
        ).fetchone()
        assert conversation is not None

        def delivery(provider_message_id: str) -> None:
            message = conn.execute(
                "INSERT INTO messages (client_id, conversation_id, direction, author, body, "
                "idempotency_key) VALUES (%s, %s, 'outbound', 'ai', 'hi', %s) RETURNING id",
                (client_id, conversation[0], provider_message_id),
            ).fetchone()
            assert message is not None
            conn.execute(
                "INSERT INTO outbound_deliveries (client_id, endpoint_id, conversation_id, "
                "message_id, provider_message_id) VALUES (%s, %s, %s, %s, %s)",
                (client_id, endpoint_id, conversation[0], message[0], provider_message_id),
            )

        def record(provider_message_id: str, status: str) -> None:
            conn.execute(
                stmt,
                {
                    "client_id": client_id,
                    "provider_message_id": provider_message_id,
                    "status": status,
                },
            )

        def current(provider_message_id: str) -> object:
            row = conn.execute(
                "SELECT provider_status FROM outbound_deliveries WHERE provider_message_id = %s",
                (provider_message_id,),
            ).fetchone()
            assert row is not None
            return row[0]

        delivery("wamid.A")
        record("wamid.A", "sent")
        record("wamid.A", "failed")
        record("wamid.A", "sent")
        record("wamid.A", "delivered")
        assert current("wamid.A") == "failed"

        delivery("wamid.B")
        record("wamid.B", "sent")
        record("wamid.B", "read")
        record("wamid.B", "delivered")
        assert current("wamid.B") == "read"


@requires_docker
async def test_whatsapp_conversation_sql_and_last_inbound(
    app_dsn: str, pg_container: object
) -> None:
    """Conversation SQL is channel-parameterised; touch_last_inbound stamps the row."""
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        client_id, _, _ = _seed_tenant(conn, "509876543210")
        contact = conn.execute(
            "INSERT INTO contacts (client_id, phone, full_name) "
            "VALUES (%s, '+2', 'C') "
            "RETURNING id",
            (client_id,),
        ).fetchone()
        assert contact is not None
        created = conn.execute(
            _sql("ingress/insert_conversation.sql"), (client_id, contact[0], "whatsapp", "6012")
        ).fetchone()
        assert created is not None
        fetched = conn.execute(
            _sql("ingress/get_conversation.sql"), (client_id, "whatsapp", "6012")
        ).fetchone()
        assert fetched == created
        assert (
            conn.execute(
                _sql("ingress/get_conversation.sql"), (client_id, "telegram", "6012")
            ).fetchone()
            is None
        )
        before = conn.execute(
            "SELECT last_inbound_at FROM conversations WHERE id = %s", (created[0],)
        ).fetchone()
        assert before == (None,)
        touched = conn.execute(_sql("ingress/touch_last_inbound.sql"), (client_id, created[0]))
        assert touched.rowcount == 1
        after = conn.execute(
            "SELECT last_inbound_at FROM conversations WHERE id = %s", (created[0],)
        ).fetchone()
        assert after is not None and after[0] is not None


@requires_docker
async def test_concurrent_first_whatsapp_messages_resolve_one_contact(
    app_dsn: str, pg_container: object
) -> None:
    """The advisory lock serialises first-contact resolution per number."""
    import threading

    lock_stmt = _sql("ingress/lock_whatsapp_contact.sql")
    resolve_stmt = _sql("ingress/resolve_whatsapp_contact.sql")
    dsn = owner_dsn_for(pg_container)
    with psycopg.connect(dsn, autocommit=True) as setup:
        client_id, _, _ = _seed_tenant(setup, "609876543210")
    params = {"client_id": client_id, "wa_id": "60188887777", "display_name": "Racer"}
    barrier = threading.Barrier(2)
    results: list[object] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            with psycopg.connect(dsn) as conn:
                barrier.wait(timeout=10)
                conn.execute(lock_stmt, params)
                rows = conn.execute(resolve_stmt, params).fetchall()
                results.append(rows)
                conn.commit()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors
    assert len(results) == 2
    assert all(isinstance(rows, list) and len(rows) == 1 for rows in results)
    with psycopg.connect(dsn, autocommit=True) as check:
        contacts = check.execute(
            "SELECT count(*) FROM contacts WHERE client_id = %s", (client_id,)
        ).fetchone()
        identities = check.execute(
            "SELECT count(*) FROM contact_identities WHERE client_id = %s AND channel = 'whatsapp'",
            (client_id,),
        ).fetchone()
    assert contacts == (1,)
    assert identities == (1,)
