"""WhatsApp channel schema and SQL tests (migration 0470)."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, LiteralString, cast

import httpx
import psycopg
import pytest
from csa.channels.ingress import IngressError
from csa.channels.whatsapp_ingress import WhatsAppIngress
from csa.core.db import build_pool
from psycopg_pool import AsyncConnectionPool

from tests.conftest import owner_dsn_for, requires_docker
from tests.test_console_identity_gateway import application

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
        assert ("outbound_deliveries", "provider_error") in cols
        route_columns = {
            r[0]
            for r in conn.execute(
                "SELECT unnest(proargnames) FROM pg_proc WHERE proname = 'resolve_whatsapp_route'"
            )
        }
        # Ciphertext is never fetched before the tenant is known.
        assert "access_token" not in route_columns and "endpoint_id" in route_columns
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

        def record(provider_message_id: str, status: str, error_code: str | None = None) -> None:
            conn.execute(
                stmt,
                {
                    "client_id": client_id,
                    "provider_message_id": provider_message_id,
                    "status": status,
                    "error_code": error_code,
                },
            )

        def current(provider_message_id: str) -> object:
            row = conn.execute(
                "SELECT provider_status FROM outbound_deliveries WHERE provider_message_id = %s",
                (provider_message_id,),
            ).fetchone()
            assert row is not None
            return row[0]

        def error(provider_message_id: str) -> object:
            row = conn.execute(
                "SELECT provider_error FROM outbound_deliveries WHERE provider_message_id = %s",
                (provider_message_id,),
            ).fetchone()
            assert row is not None
            return row[0]

        delivery("wamid.A")
        record("wamid.A", "sent", "999")
        assert error("wamid.A") is None
        record("wamid.A", "failed", "131047")
        record("wamid.A", "sent")
        record("wamid.A", "delivered")
        assert current("wamid.A") == "failed"
        assert error("wamid.A") == "131047"

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
        touch = {"client_id": client_id, "conversation_id": created[0]}
        late = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
        touched = conn.execute(_sql("ingress/touch_last_inbound.sql"), {**touch, "at": late})
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


# --- ingress pipeline -------------------------------------------------------


APP_SECRET = "wa-app-secret"
VERIFY_TOKEN = "wa-verify-token"
WA_ID = "60123456789"


def signed(body: dict[str, Any], secret: str = APP_SECRET) -> tuple[str, bytes]:
    raw = json.dumps(body).encode()
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest(), raw


def _delivery(
    phone_number_id: str,
    *,
    messages: list[dict[str, Any]] | None = None,
    statuses: list[dict[str, Any]] | None = None,
    wa_id: str = WA_ID,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "15550001111", "phone_number_id": phone_number_id},
        "contacts": [{"profile": {"name": "Aina"}, "wa_id": wa_id}],
    }
    if messages is not None:
        value["messages"] = messages
    if statuses is not None:
        value["statuses"] = statuses
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "1", "changes": [{"field": "messages", "value": value}]}],
    }


def _text(wamid: str, wa_id: str = WA_ID, body: str = "Hello") -> dict[str, Any]:
    return {
        "from": wa_id,
        "id": wamid,
        "timestamp": "1791676800",
        "type": "text",
        "text": {"body": body},
    }


WhatsAppContext = tuple[WhatsAppIngress, AsyncConnectionPool, str, str, str]


@pytest.fixture()
async def whatsapp_context(app_dsn: str, pg_container: object) -> AsyncIterator[WhatsAppContext]:
    phone_number_id = str(uuid.uuid4().int)[:15]
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        client_id, _, _ = _seed_tenant(conn, phone_number_id)
    pool = build_pool(app_dsn, max_size=4)
    await pool.open(wait=True)
    try:
        ingress = WhatsAppIngress(pool, app_secret=APP_SECRET, verify_token=VERIFY_TOKEN)
        yield ingress, pool, client_id, phone_number_id, APP_SECRET
    finally:
        await pool.close()


def _count(pg_container: object, table: str, client_id: str) -> int:
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        row = conn.execute(
            cast(LiteralString, f"SELECT count(*) FROM {table} WHERE client_id = %s"),
            (client_id,),
        ).fetchone()
    assert row is not None
    return int(row[0])


@requires_docker
async def test_text_message_creates_contact_conversation_event_and_last_inbound(
    whatsapp_context: WhatsAppContext, pg_container: object
) -> None:
    ingress, _, client_id, number, _ = whatsapp_context
    signature, raw = signed(_delivery(number, messages=[_text("wamid.T1")]))
    receipts = await ingress.receive(signature, raw)
    assert len(receipts) == 1 and receipts[0].accepted and not receipts[0].duplicate
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        contacts = conn.execute(
            "SELECT phone FROM contacts WHERE client_id = %s", (client_id,)
        ).fetchall()
        assert contacts == [("+60123456789",)]
        identity = conn.execute(
            "SELECT external_id FROM contact_identities "
            "WHERE client_id = %s AND channel = 'whatsapp'",
            (client_id,),
        ).fetchall()
        assert identity == [(WA_ID,)]
        conversation = conn.execute(
            "SELECT channel, channel_thread, last_inbound_at FROM conversations "
            "WHERE client_id = %s",
            (client_id,),
        ).fetchall()
        assert len(conversation) == 1
        assert conversation[0][:2] == ("whatsapp", WA_ID) and conversation[0][2] is not None
        events = conn.execute(
            "SELECT idempotency_key, raw FROM ingress_events WHERE client_id = %s", (client_id,)
        ).fetchall()
        assert len(events) == 1 and events[0][0] == "wamid.T1"
        assert events[0][1]["source"] == {
            "channel": "whatsapp",
            "kind": "message",
            "phone_number_id": number,
        }
        assert events[0][1]["envelope"]["channel"] == "whatsapp"


@requires_docker
async def test_duplicate_webhook_is_acknowledged_without_second_event(
    whatsapp_context: WhatsAppContext, pg_container: object
) -> None:
    ingress, _, client_id, number, _ = whatsapp_context
    signature, raw = signed(_delivery(number, messages=[_text("wamid.D1")]))
    first = await ingress.receive(signature, raw)
    second = await ingress.receive(signature, raw)
    assert first[0].duplicate is False and second[0].duplicate is True
    assert second[0].trace_id == first[0].trace_id
    assert _count(pg_container, "ingress_events", client_id) == 1


@requires_docker
async def test_bad_signature_has_no_side_effects(
    whatsapp_context: WhatsAppContext, pg_container: object
) -> None:
    ingress, _, client_id, number, _ = whatsapp_context
    _, raw = signed(_delivery(number, messages=[_text("wamid.B1")]))
    bad, _ = signed({"x": 1}, secret="other")
    for header in (bad, None):
        with pytest.raises(IngressError) as excinfo:
            await ingress.receive(header, raw)
        assert excinfo.value.status_code == 403
    for table in ("contacts", "conversations", "ingress_events"):
        assert _count(pg_container, table, client_id) == 0
    garbage_sig, garbage = signed({"entry": "nope"})
    with pytest.raises(IngressError) as parse_error:
        await ingress.receive(garbage_sig, garbage)
    assert parse_error.value.status_code == 422
    no_number = _delivery(number, messages=[_text("wamid.B2")])
    del no_number["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"]
    with pytest.raises(IngressError) as envelope_error:
        await ingress.receive(*signed(no_number))
    assert envelope_error.value.status_code == 422
    assert _count(pg_container, "ingress_events", client_id) == 0


@requires_docker
async def test_signed_batch_skips_items_it_does_not_understand(
    whatsapp_context: WhatsAppContext, pg_container: object
) -> None:
    """One odd item never poisons the batch: the rest is stored and Meta gets a 200."""
    ingress, _, client_id, number, _ = whatsapp_context
    odd_message = {**_text("wamid.ODD"), "timestamp": "not-a-time"}
    odd_status = {"id": "wamid.X", "status": "deleted", "timestamp": "1", "recipient_id": WA_ID}
    body = _delivery(number, messages=[odd_message, _text("wamid.GOOD")], statuses=[odd_status])
    receipts = await ingress.receive(*signed(body))
    assert len(receipts) == 1 and not receipts[0].duplicate
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        keys = conn.execute(
            "SELECT idempotency_key FROM ingress_events WHERE client_id = %s", (client_id,)
        ).fetchall()
    assert keys == [("wamid.GOOD",)]


@requires_docker
async def test_sender_links_to_single_phone_match_only(
    whatsapp_context: WhatsAppContext, pg_container: object
) -> None:
    ingress, _, client_id, number, _ = whatsapp_context
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        conn.execute(
            "INSERT INTO contacts (client_id, phone, full_name) "
            "VALUES (%s, '+60 12-345 6789', 'Existing')",
            (client_id,),
        )
    await ingress.receive(*signed(_delivery(number, messages=[_text("wamid.L1")])))
    assert _count(pg_container, "contacts", client_id) == 1
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        first_contact = conn.execute(
            "SELECT raw -> 'envelope' -> 'meta' ->> 'is_first_contact' "
            "FROM ingress_events WHERE client_id = %s",
            (client_id,),
        ).fetchone()
        assert first_contact == ("false",)
        # Two contacts share one phone: ambiguity creates a new contact instead of guessing.
        conn.execute(
            "INSERT INTO contacts (client_id, phone, full_name) VALUES (%s, '60 19 888 7777', 'A')",
            (client_id,),
        )
        conn.execute(
            "INSERT INTO contacts (client_id, phone, full_name) VALUES (%s, '+6019-8887777', 'B')",
            (client_id,),
        )
    other = "60198887777"
    delivery = _delivery(number, messages=[_text("wamid.L2", other)], wa_id=other)
    await ingress.receive(*signed(delivery))
    assert _count(pg_container, "contacts", client_id) == 4


@requires_docker
async def test_two_tenants_two_numbers_stay_isolated(
    whatsapp_context: WhatsAppContext, pg_container: object
) -> None:
    ingress, _, client_a, _, _ = whatsapp_context
    number_b = str(uuid.uuid4().int)[:15]
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        client_b, _, _ = _seed_tenant(conn, number_b)
        # Tenant A already knows this person: same phone, same wa_id conversation.
        contact_a = conn.execute(
            "INSERT INTO contacts (client_id, phone, full_name) "
            "VALUES (%s, '+60 12-345 6789', 'Tenant A customer') RETURNING id",
            (client_a,),
        ).fetchone()
        assert contact_a is not None
        conn.execute(
            "INSERT INTO conversations (client_id, contact_id, channel, channel_thread) "
            "VALUES (%s, %s, 'whatsapp', %s)",
            (client_a, contact_a[0], WA_ID),
        )
    await ingress.receive(*signed(_delivery(number_b, messages=[_text("wamid.I1")])))
    for table in ("contacts", "conversations", "ingress_events"):
        assert _count(pg_container, table, client_b) == 1
    assert _count(pg_container, "contacts", client_a) == 1
    assert _count(pg_container, "conversations", client_a) == 1
    assert _count(pg_container, "ingress_events", client_a) == 0
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        contact_b = conn.execute(
            "SELECT id, client_id, phone FROM contacts WHERE client_id = %s", (client_b,)
        ).fetchall()
        envelope_contact = conn.execute(
            "SELECT raw -> 'envelope' -> 'contact' ->> 'id' FROM ingress_events "
            "WHERE client_id = %s",
            (client_b,),
        ).fetchone()
    assert len(contact_b) == 1 and contact_b[0][0] != contact_a[0]
    assert str(contact_b[0][1]) == client_b and contact_b[0][2] == "+60123456789"
    assert envelope_contact == (str(contact_b[0][0]),)


@requires_docker
async def test_unknown_number_is_skipped_without_failing_the_batch(
    whatsapp_context: WhatsAppContext, pg_container: object
) -> None:
    ingress, _, client_id, number, _ = whatsapp_context
    body = _delivery("999000111222", messages=[_text("wamid.U1")])
    body["entry"].append(_delivery(number, messages=[_text("wamid.U2")])["entry"][0])
    receipts = await ingress.receive(*signed(body))
    assert len(receipts) == 1
    assert _count(pg_container, "ingress_events", client_id) == 1


@requires_docker
async def test_status_updates_delivery_by_provider_message_id(
    whatsapp_context: WhatsAppContext, pg_container: object
) -> None:
    ingress, _, client_id, number, _ = whatsapp_context
    await ingress.receive(*signed(_delivery(number, messages=[_text("wamid.S0")])))
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        endpoint = conn.execute(
            "SELECT id FROM webhook_endpoints WHERE client_id = %s", (client_id,)
        ).fetchone()
        conversation = conn.execute(
            "SELECT id FROM conversations WHERE client_id = %s", (client_id,)
        ).fetchone()
        assert endpoint is not None and conversation is not None
        message = conn.execute(
            "INSERT INTO messages (client_id, conversation_id, direction, author, body, "
            "idempotency_key) VALUES (%s, %s, 'outbound', 'ai', 'hi', 'out-1') RETURNING id",
            (client_id, conversation[0]),
        ).fetchone()
        assert message is not None
        conn.execute(
            "INSERT INTO outbound_deliveries (client_id, endpoint_id, conversation_id, "
            "message_id, provider_message_id) VALUES (%s, %s, %s, %s, 'wamid.OUT')",
            (client_id, endpoint[0], conversation[0], message[0]),
        )

    def status(wamid: str, value: str) -> dict[str, Any]:
        return {"id": wamid, "status": value, "timestamp": "1791676900", "recipient_id": WA_ID}

    def current() -> object:
        with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
            row = conn.execute(
                "SELECT provider_status FROM outbound_deliveries "
                "WHERE provider_message_id = 'wamid.OUT'"
            ).fetchone()
        assert row is not None
        return row[0]

    delivered = _delivery(number, statuses=[status("wamid.OUT", "delivered")])
    assert await ingress.receive(*signed(delivered)) == []
    assert current() == "delivered"
    await ingress.receive(*signed(_delivery(number, statuses=[status("wamid.OUT", "read")])))
    assert current() == "read"
    unknown = _delivery(number, statuses=[status("wamid.NONE", "read")])
    assert await ingress.receive(*signed(unknown)) == []
    failed = {**status("wamid.OUT", "failed"), "errors": [{"code": 131047, "title": "x"}]}
    await ingress.receive(*signed(_delivery(number, statuses=[failed])))
    assert current() == "failed"
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        stored = conn.execute(
            "SELECT provider_error FROM outbound_deliveries WHERE provider_message_id = 'wamid.OUT'"
        ).fetchone()
    assert stored == ("131047",)


@requires_docker
async def test_image_message_is_enqueued_with_attachment_stub(
    whatsapp_context: WhatsAppContext, pg_container: object
) -> None:
    ingress, _, client_id, number, _ = whatsapp_context
    image = {
        "from": WA_ID,
        "id": "wamid.IMG",
        "timestamp": "1791676800",
        "type": "image",
        "image": {"id": "media-1", "mime_type": "image/jpeg"},
    }
    receipts = await ingress.receive(*signed(_delivery(number, messages=[image])))
    assert len(receipts) == 1
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        row = conn.execute(
            "SELECT raw -> 'envelope' FROM ingress_events WHERE client_id = %s", (client_id,)
        ).fetchone()
    assert row is not None
    assert row[0]["message"]["attachments"][0]["kind"] == "image"
    assert row[0]["message"]["body_raw"] == ""


@requires_docker
async def test_location_message_is_enqueued_as_a_live_turn(
    whatsapp_context: WhatsAppContext, pg_container: object
) -> None:
    ingress, _, client_id, number, _ = whatsapp_context
    location = {
        "from": WA_ID,
        "id": "wamid.LOC",
        "timestamp": "1791676800",
        "type": "location",
        "location": {"latitude": 3.1, "longitude": 101.6},
    }
    receipts = await ingress.receive(*signed(_delivery(number, messages=[location])))
    assert len(receipts) == 1 and not receipts[0].duplicate
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        row = conn.execute(
            "SELECT status, raw -> 'envelope' -> 'message', raw -> 'source' ->> 'kind' "
            "FROM ingress_events WHERE client_id = %s",
            (client_id,),
        ).fetchone()
        stamped = conn.execute(
            "SELECT last_inbound_at FROM conversations WHERE client_id = %s", (client_id,)
        ).fetchone()
    assert row is not None
    status, message, kind = row
    assert status == "received" and kind == "message"
    assert message["body_raw"] == ""
    assert message["attachments"] == [
        {
            "kind": "other",
            "mime_type": "application/vnd.whatsapp.location",
            "file_id": None,
            "filename": None,
            "size_bytes": None,
        }
    ]
    assert stamped is not None and stamped[0] is not None


@requires_docker
async def test_unsupported_type_is_recorded_once(
    whatsapp_context: WhatsAppContext, pg_container: object
) -> None:
    ingress, _, client_id, number, _ = whatsapp_context
    reaction = {
        "from": WA_ID,
        "id": "wamid.REACT",
        "timestamp": "1791676800",
        "type": "reaction",
        "reaction": {"message_id": "wamid.X", "emoji": "x"},
    }
    body = signed(_delivery(number, messages=[reaction]))
    first = await ingress.receive(*body)
    second = await ingress.receive(*body)
    assert first[0].duplicate is False and second[0].duplicate is True
    assert _count(pg_container, "ingress_events", client_id) == 1
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        claimed = conn.execute(_sql("worker/claim_ingress.sql")).fetchall()
        row = conn.execute(
            "SELECT status, attempts FROM ingress_events WHERE client_id = %s", (client_id,)
        ).fetchone()
    assert row == ("completed", 0)
    assert all(str(r[1]) != client_id for r in claimed)


def _last_inbound(pg_container: object, client_id: str) -> datetime:
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        row = conn.execute(
            "SELECT last_inbound_at FROM conversations WHERE client_id = %s", (client_id,)
        ).fetchone()
    assert row is not None and row[0] is not None
    return row[0]


@requires_docker
async def test_replay_and_older_events_do_not_move_the_service_window(
    whatsapp_context: WhatsAppContext, pg_container: object
) -> None:
    ingress, _, client_id, number, _ = whatsapp_context
    newer = {**_text("wamid.W2"), "timestamp": "1791676800"}
    older = {**_text("wamid.W1"), "timestamp": "1791670000"}
    await ingress.receive(*signed(_delivery(number, messages=[newer])))
    stamped = _last_inbound(pg_container, client_id)
    assert stamped == datetime.fromtimestamp(1791676800, UTC)
    replay = await ingress.receive(*signed(_delivery(number, messages=[newer])))
    assert replay[0].duplicate is True
    assert _last_inbound(pg_container, client_id) == stamped
    await ingress.receive(*signed(_delivery(number, messages=[older])))
    assert _last_inbound(pg_container, client_id) == stamped


@requires_docker
async def test_over_limit_tenant_does_not_block_other_tenants_in_a_batch(
    whatsapp_context: WhatsAppContext, pg_container: object
) -> None:
    ingress, _, client_a, number_a, _ = whatsapp_context
    number_b = str(uuid.uuid4().int)[:15]
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        client_b, _, _ = _seed_tenant(conn, number_b)
        conn.execute(
            "UPDATE webhook_endpoints SET rate_limit_rpm = 1 WHERE client_id = %s", (client_a,)
        )
    body = _delivery(number_a, messages=[_text("wamid.R1"), _text("wamid.R2")])
    body["entry"].append(_delivery(number_b, messages=[_text("wamid.R3")])["entry"][0])
    with pytest.raises(IngressError) as excinfo:
        await ingress.receive(*signed(body))
    assert excinfo.value.status_code == 429
    assert _count(pg_container, "ingress_events", client_a) == 1
    assert _count(pg_container, "ingress_events", client_b) == 1


@requires_docker
async def test_n8n_runtime_tenant_is_skipped(
    whatsapp_context: WhatsAppContext, pg_container: object
) -> None:
    ingress, _, client_id, number, _ = whatsapp_context
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        conn.execute(
            'UPDATE clients SET config = config || \'{"runtime": "n8n"}\'::jsonb WHERE id = %s',
            (client_id,),
        )
    receipts = await ingress.receive(*signed(_delivery(number, messages=[_text("wamid.N1")])))
    assert receipts == []
    assert _count(pg_container, "ingress_events", client_id) == 0


async def test_gateway_routes_hidden_when_unconfigured_and_verify_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hidden = application(monkeypatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=hidden), base_url="https://gateway.test"
    ) as client:
        assert (await client.get("/channels/whatsapp")).status_code == 404
        assert (await client.post("/channels/whatsapp", json={})).status_code == 404

    enabled = application(monkeypatch, whatsapp=(APP_SECRET, VERIFY_TOKEN))
    # The lifespan needs a database; the routes only need the ingress object.
    enabled.state.whatsapp_ingress = WhatsAppIngress(
        cast(Any, None), app_secret=APP_SECRET, verify_token=VERIFY_TOKEN
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=enabled), base_url="https://gateway.test"
    ) as client:
        query = {"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "42"}
        good = await client.get("/channels/whatsapp", params=query)
        assert good.status_code == 200 and good.text == "42"
        wrong = await client.get("/channels/whatsapp", params={**query, "hub.verify_token": "x"})
        assert wrong.status_code == 403
        unsigned = await client.post("/channels/whatsapp", json={"entry": []})
        assert unsigned.status_code == 403
