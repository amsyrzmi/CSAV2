"""Tenant-scoped Telegram ingress persistence tests (LG03)."""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import psycopg
import pytest
from csa.channels.ingress import (
    TELEGRAM_WEBHOOK_SECRET_META_KEY,
    IngressError,
    TelegramIngress,
)
from csa.contracts.callbacks import CallbackCodec
from csa.core import crypto, db, settings
from csa.core.tenancy import tx
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from tests.conftest import owner_dsn_for, requires_docker

_KEY = "base64:" + base64.b64encode(b"\x04" * 32).decode("ascii")


@pytest.fixture()
async def ingress_context(
    pg_container: object,
    app_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[TelegramIngress, db.AsyncConnectionPool, str, str, str]]:
    for name, value in {
        "ENV": "ci",
        "DATABASE_URL": "postgresql://csa_app:test_app_pw@localhost:5433/csa",
        "LG_DATABASE_URL": "postgresql://csa_app:test_app_pw@localhost:5433/csa",
        "GROQ_API_KEY": "test-key",
        "PUBLIC_BASE_URL": "https://example.test",
        "CREDENTIALS_KEY": _KEY,
    }.items():
        monkeypatch.setenv(name, value)
    settings.get_settings.cache_clear()

    client_id = str(uuid.uuid4())
    credential_id = str(uuid.uuid4())
    endpoint_id = str(uuid.uuid4())
    public_id = f"telegram-{uuid.uuid4().hex[:20]}"
    webhook_secret = "dev-webhook-secret"
    bot_token = "12345:dev-test-token"
    encrypted_secret = crypto.encrypt(
        webhook_secret,
        crypto.credential_aad(
            "channel_credentials",
            "meta.telegram_webhook_secret_enc",
            credential_id,
        ),
    )
    encrypted_token = crypto.encrypt(
        bot_token,
        crypto.credential_aad("channel_credentials", "access_token", credential_id),
    )
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clients (id, slug, name, platform_version) VALUES (%s, %s, %s, %s)",
            (client_id, f"ingress-{client_id}", f"ingress-{client_id}", "lg03-test"),
        )
        conn.execute(
            "INSERT INTO channel_credentials (id, client_id, channel, access_token, meta) "
            "VALUES (%s, %s, 'telegram', %s, %s)",
            (
                credential_id,
                client_id,
                encrypted_token,
                Jsonb({TELEGRAM_WEBHOOK_SECRET_META_KEY: encrypted_secret}),
            ),
        )
        conn.execute(
            "INSERT INTO webhook_endpoints (id, client_id, public_id, credential_id) "
            "VALUES (%s, %s, %s, %s)",
            (endpoint_id, client_id, public_id, credential_id),
        )

    pool = db.build_pool(app_dsn, max_size=4)
    await pool.open(wait=True)
    try:
        yield TelegramIngress(pool), pool, client_id, public_id, webhook_secret
    finally:
        await pool.close()
        settings.get_settings.cache_clear()


def _message_update(
    *,
    update_id: int = 123,
    sender_id: int = 501,
    chat_id: int = 501,
    text: str = "Hello",
) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 1791676800,
            "from": {"id": sender_id, "first_name": "Synthetic"},
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


@requires_docker
async def test_ingress_persists_normalized_event_and_deduplicates(
    ingress_context: tuple[TelegramIngress, db.AsyncConnectionPool, str, str, str],
) -> None:
    ingress, pool, client_id, public_id, secret = ingress_context
    update = _message_update(text="Email me@example.test or call +60123456789")
    body = json.dumps(update, separators=(",", ":")).encode()

    first = await ingress.receive(public_id, secret, body)
    duplicate = await ingress.receive(public_id, secret, json.dumps(update, indent=2).encode())

    assert first.accepted is True
    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert duplicate.trace_id == first.trace_id

    async with tx(pool, client_id) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT raw, payload_hash, status FROM ingress_events "
                "WHERE client_id = %s AND idempotency_key = %s",
                (client_id, f"telegram:{public_id}:123"),
            )
            event = await cur.fetchone()
            assert event is not None
            assert event["status"] == "received"
            envelope = event["raw"]["envelope"]
            assert envelope["message"]["body_raw"] == "Email me@example.test or call +60123456789"
            assert envelope["message"]["body_redacted"] == "Email [EMAIL_0] or call [PHONE_0]"
            assert envelope["meta"]["is_first_contact"] is True
            await cur.execute(
                "SELECT count(*) AS count FROM ingress_events "
                "WHERE client_id = %s AND idempotency_key = %s",
                (client_id, f"telegram:{public_id}:123"),
            )
            count_row = await cur.fetchone()
            assert count_row is not None
            assert count_row["count"] == 1


@requires_docker
async def test_invalid_secret_is_rejected_before_contact_or_event_creation(
    ingress_context: tuple[TelegramIngress, db.AsyncConnectionPool, str, str, str],
) -> None:
    ingress, pool, client_id, public_id, _ = ingress_context

    with pytest.raises(IngressError) as error:
        await ingress.receive(public_id, "wrong-secret", json.dumps(_message_update()).encode())

    assert error.value.status_code == 401
    async with tx(pool, client_id) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT count(*) AS count FROM ingress_events WHERE client_id = %s", (client_id,)
            )
            count_row = await cur.fetchone()
            assert count_row is not None
            assert count_row["count"] == 0
            await cur.execute(
                "SELECT count(*) AS count FROM contacts WHERE client_id = %s", (client_id,)
            )
            count_row = await cur.fetchone()
            assert count_row is not None
            assert count_row["count"] == 0


@requires_docker
async def test_callback_is_persisted_and_telegram_spinner_is_answered(
    ingress_context: tuple[TelegramIngress, db.AsyncConnectionPool, str, str, str],
) -> None:
    _, pool, client_id, public_id, secret = ingress_context
    answered: list[httpx.Request] = []

    def answer_callback(request: httpx.Request) -> httpx.Response:
        answered.append(request)
        return httpx.Response(200, json={"ok": True, "result": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer_callback)) as client:
        ingress = TelegramIngress(pool, http_client=client)
        action_id = uuid.uuid4()
        update = {
            "update_id": 124,
            "callback_query": {
                "id": "callback-query-test",
                "from": {"id": 501, "first_name": "Synthetic"},
                "message": {
                    "message_id": 77,
                    "date": 1791676800,
                    "chat": {"id": 501, "type": "private"},
                },
                "data": CallbackCodec.encode(action_id),
            },
        }
        receipt = await ingress.receive(public_id, secret, json.dumps(update).encode())

    assert receipt.accepted
    assert len(answered) == 2
    assert answered[0].url.path.endswith("/answerCallbackQuery")
    assert json.loads(answered[0].content) == {"callback_query_id": "callback-query-test"}
    assert answered[1].url.path.endswith("/editMessageReplyMarkup")
    assert json.loads(answered[1].content) == {
        "chat_id": "501",
        "message_id": 77,
        "reply_markup": {"inline_keyboard": []},
    }
    async with tx(pool, client_id) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT raw FROM ingress_events WHERE client_id = %s AND idempotency_key = %s",
                (client_id, f"telegram:{public_id}:124"),
            )
            event = await cur.fetchone()
            assert event is not None
            assert event["raw"]["envelope"]["callback"]["action_id"] == str(action_id)


@requires_docker
async def test_conversation_cannot_be_rebound_to_a_different_sender(
    ingress_context: tuple[TelegramIngress, db.AsyncConnectionPool, str, str, str],
) -> None:
    ingress, _, _, public_id, secret = ingress_context
    await ingress.receive(public_id, secret, json.dumps(_message_update()).encode())

    with pytest.raises(IngressError) as error:
        await ingress.receive(
            public_id,
            secret,
            json.dumps(_message_update(update_id=125, sender_id=502, chat_id=501)).encode(),
        )

    assert error.value.status_code == 409


@requires_docker
async def test_known_contact_preserves_name_and_is_not_first_contact(
    ingress_context: tuple[TelegramIngress, db.AsyncConnectionPool, str, str, str],
) -> None:
    ingress, pool, client_id, public_id, secret = ingress_context
    await ingress.receive(public_id, secret, json.dumps(_message_update()).encode())
    update = _message_update(update_id=126)
    update["message"]["from"]["first_name"] = "Changed"
    await ingress.receive(public_id, secret, json.dumps(update).encode())
    async with tx(pool, client_id) as conn:
        row = await conn.execute(
            "SELECT raw->'envelope' AS envelope FROM ingress_events WHERE idempotency_key=%s",
            (f"telegram:{public_id}:126",),
        )
        result = await row.fetchone()
    assert result is not None
    assert result["envelope"]["contact"]["display_name"] == "Synthetic"
    assert result["envelope"]["meta"]["is_first_contact"] is False


@requires_docker
async def test_concurrent_first_messages_resolve_one_contact_and_conversation(
    ingress_context: tuple[TelegramIngress, db.AsyncConnectionPool, str, str, str],
) -> None:
    ingress, pool, client_id, public_id, secret = ingress_context
    receipts = await asyncio.gather(
        *(
            ingress.receive(
                public_id, secret, json.dumps(_message_update(update_id=200 + i)).encode()
            )
            for i in range(6)
        )
    )
    assert all(receipt.accepted and not receipt.duplicate for receipt in receipts)
    async with tx(pool, client_id) as conn:
        for table in ("contacts", "contact_identities", "conversations"):
            result = await conn.execute(
                sql.SQL("SELECT count(*) AS count FROM {}").format(sql.Identifier(table))
            )
            assert await result.fetchone() == {"count": 1}
        result = await conn.execute(
            "SELECT count(*) AS count FROM ingress_events "
            "WHERE raw->'envelope'->'meta'->>'is_first_contact'='true'"
        )
        assert await result.fetchone() == {"count": 1}


@requires_docker
async def test_legacy_contact_without_identity_is_repaired(
    ingress_context: tuple[TelegramIngress, db.AsyncConnectionPool, str, str, str],
) -> None:
    ingress, pool, client_id, public_id, secret = ingress_context
    async with tx(pool, client_id) as conn:
        result = await conn.execute(
            "INSERT INTO contacts (client_id,telegram_id,full_name) "
            "VALUES (%s,501,'Legacy') RETURNING id",
            (client_id,),
        )
        contact = await result.fetchone()
    assert contact is not None
    await ingress.receive(public_id, secret, json.dumps(_message_update()).encode())
    async with tx(pool, client_id) as conn:
        result = await conn.execute("SELECT contact_id FROM contact_identities")
        assert await result.fetchone() == {"contact_id": contact["id"]}
        result = await conn.execute(
            "SELECT raw->'envelope'->'contact'->>'display_name' AS name FROM ingress_events"
        )
        assert await result.fetchone() == {"name": "Legacy"}


@requires_docker
@pytest.mark.parametrize("autocommit", [False, True])
async def test_identity_conflict_rolls_back_new_contact_and_event(
    ingress_context: tuple[TelegramIngress, db.AsyncConnectionPool, str, str, str],
    app_dsn: str,
    autocommit: bool,
) -> None:
    _, pool, client_id, public_id, secret = ingress_context
    async with tx(pool, client_id) as conn:
        result = await conn.execute(
            "INSERT INTO contacts (client_id,telegram_id) VALUES (%s,502) RETURNING id",
            (client_id,),
        )
        contact = await result.fetchone()
        assert contact is not None
        await conn.execute(
            "INSERT INTO contact_identities (client_id,channel,external_id,contact_id) "
            "VALUES (%s,'telegram','501',%s)",
            (client_id, contact["id"]),
        )
    ingress_pool = db.build_pool(app_dsn, max_size=1, autocommit=autocommit)
    await ingress_pool.open(wait=True)
    try:
        with pytest.raises(IngressError) as error:
            await TelegramIngress(ingress_pool).receive(
                public_id, secret, json.dumps(_message_update()).encode()
            )
    finally:
        await ingress_pool.close()
    assert error.value.status_code == 409
    async with tx(pool, client_id) as conn:
        result = await conn.execute("SELECT id FROM contacts ORDER BY telegram_id")
        assert await result.fetchall() == [{"id": contact["id"]}]
        result = await conn.execute("SELECT count(*) AS count FROM ingress_events")
        assert await result.fetchone() == {"count": 0}
