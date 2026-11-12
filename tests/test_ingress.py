"""Tenant-scoped Telegram ingress persistence tests (LG03)."""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any, LiteralString, cast

import httpx
import psycopg
import pytest
from csa.channels import ingress as ingress_module
from csa.channels.ingress import (
    TELEGRAM_WEBHOOK_SECRET_META_KEY,
    IngressError,
    TelegramIngress,
)
from csa.contracts.callbacks import CallbackCodec
from csa.core import crypto, db, settings
from csa.core.tenancy import tx
from fastapi import HTTPException
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from structlog.testing import capture_logs

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
async def test_ingress_resolves_credentials_and_runtime_in_one_tenant_lookup(
    ingress_context: tuple[TelegramIngress, db.AsyncConnectionPool, str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ingress, pool, client_id, public_id, secret = ingress_context
    original = db.q.fetch_one
    paths: list[str] = []

    async def tracked(path: str, *args: Any, **kwargs: Any) -> Any:
        paths.append(path)
        return await original(path, *args, **kwargs)

    monkeypatch.setattr(db.q, "fetch_one", tracked)
    receipt = await ingress.receive(public_id, secret, json.dumps(_message_update()).encode())
    assert receipt.accepted
    assert paths.count("ingress/get_channel_credentials.sql") == 1
    assert len(paths) == 6
    async with tx(pool, client_id) as conn:
        endpoint = await original("ingress/resolve_endpoint.sql", (public_id,), conn=conn)
        route = await original(
            "ingress/get_channel_credentials.sql", (endpoint["endpoint_id"], client_id), conn=conn
        )
        assert "runtime" not in route and "n8n_webhook_url" not in route
    async with tx(pool, str(uuid.uuid4())) as conn:
        assert (
            await original(
                "ingress/get_channel_credentials.sql",
                (endpoint["endpoint_id"], client_id),
                conn=conn,
            )
            is None
        )


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


async def _counts_and_username(
    pool: db.AsyncConnectionPool, client_id: str
) -> tuple[dict[str, int], str | None]:
    async with tx(pool, client_id) as conn:
        counts = {}
        for table in ("contacts", "conversations", "ingress_events"):
            result = await conn.execute(
                sql.SQL("SELECT count(*) AS count FROM {}").format(sql.Identifier(table))
            )
            row = await result.fetchone()
            assert row is not None
            counts[table] = row["count"]
        result = await conn.execute("SELECT telegram_username FROM contacts")
        row = await result.fetchone()
        assert row is not None
        return counts, row["telegram_username"]


async def _receive_with_username(
    ingress_context: tuple[TelegramIngress, db.AsyncConnectionPool, str, str, str],
    update_id: int,
    username: object,
    *,
    include: bool = True,
) -> Any:
    ingress, _, _, public_id, secret = ingress_context
    update = _message_update(update_id=update_id)
    if include:
        update["message"]["from"]["username"] = username
    return await ingress.receive(public_id, secret, json.dumps(update).encode())


@requires_docker
async def test_telegram_username_is_stored_updated_and_never_blocks_a_message(
    ingress_context: tuple[TelegramIngress, db.AsyncConnectionPool, str, str, str],
) -> None:
    _, pool, client_id, _, _ = ingress_context

    first = await _receive_with_username(ingress_context, 301, "aisyah_r")
    assert first.accepted and not first.duplicate
    counts, username = await _counts_and_username(pool, client_id)
    assert username == "aisyah_r"
    assert counts["contacts"] == 1 and counts["conversations"] == 1

    await _receive_with_username(ingress_context, 302, "new_name_1")
    _, username = await _counts_and_username(pool, client_id)
    assert username == "new_name_1"

    # An invalid username is accepted exactly like a normal message and clears the value.
    ok = await _receive_with_username(ingress_context, 303, "ab")
    assert ok.accepted and not ok.duplicate
    counts_after, username = await _counts_and_username(pool, client_id)
    assert username is None
    assert counts_after["contacts"] == 1 and counts_after["conversations"] == 1
    assert counts_after["ingress_events"] == counts["ingress_events"] + 2

    for update_id, bad in ((304, "has space"), (305, 123), (306, "x" * 40)):
        await _receive_with_username(ingress_context, update_id, bad)
        _, username = await _counts_and_username(pool, client_id)
        assert username is None

    await _receive_with_username(ingress_context, 307, "back_again")
    _, username = await _counts_and_username(pool, client_id)
    assert username == "back_again"
    # A sender without a username clears it.
    await _receive_with_username(ingress_context, 308, None, include=False)
    final, username = await _counts_and_username(pool, client_id)
    assert username is None
    assert final["contacts"] == 1 and final["conversations"] == 1
    assert final["ingress_events"] == 8


@requires_docker
async def test_telegram_username_with_a_new_contact_that_has_none_or_a_bad_one(
    ingress_context: tuple[TelegramIngress, db.AsyncConnectionPool, str, str, str],
) -> None:
    _, pool, client_id, _, _ = ingress_context
    receipt = await _receive_with_username(ingress_context, 310, "bad name!")
    assert receipt.accepted
    counts, username = await _counts_and_username(pool, client_id)
    assert username is None and counts["contacts"] == 1 and counts["conversations"] == 1


@requires_docker
async def test_telegram_username_survives_the_concurrent_insert_fallback_path(
    ingress_context: tuple[TelegramIngress, db.AsyncConnectionPool, str, str, str],
) -> None:
    _, pool, client_id, _, _ = ingress_context
    # A contact that already exists without an identity makes the first statement find
    # nothing and resolves through the conflict statements instead.
    async with tx(pool, client_id) as conn:
        await conn.execute(
            "INSERT INTO contacts (client_id,telegram_id,full_name,telegram_username) "
            "VALUES (%s,501,'Legacy','old_name')",
            (client_id,),
        )
    receipt = await _receive_with_username(ingress_context, 320, "aisyah_r")
    assert receipt.accepted
    counts, username = await _counts_and_username(pool, client_id)
    assert username == "aisyah_r" and counts["contacts"] == 1 and counts["conversations"] == 1
    # The identity now exists, so the next message takes the known-contact path.
    await _receive_with_username(ingress_context, 321, "ab")
    _, username = await _counts_and_username(pool, client_id)
    assert username is None


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


def _staff_tap_update(update_id: int, chat_id: int, callback_data: str) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cq-{update_id}",
            "from": {"id": chat_id, "first_name": "Staff"},
            "data": callback_data,
            "message": {
                "message_id": 40,
                "date": 1791676800,
                "chat": {"id": chat_id, "type": "private"},
            },
        },
    }


@requires_docker
async def test_staff_edit_text_via_webhook_reaches_customer_outbox_once(
    ingress_context: tuple[TelegramIngress, db.AsyncConnectionPool, str, str, str],
    pg_container: object,
) -> None:
    _, pool, client_id, public_id, secret = ingress_context
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append({"url": request.url.path.rsplit("/", 1)[-1], **json.loads(request.content)})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})

    ingress = TelegramIngress(
        pool, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )

    def post(update: dict[str, Any]) -> bytes:
        return json.dumps(update).encode()

    customer = await ingress.receive(
        public_id, secret, post(_message_update(update_id=1, sender_id=777, chat_id=777))
    )
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        conversation_id = conn.execute(
            "SELECT id FROM conversations WHERE client_id=%s AND channel_thread='777'", (client_id,)
        ).fetchall()[0][0]
        target_id = conn.execute(
            "INSERT INTO staff_notification_targets (client_id, channel, address) "
            "VALUES (%s,'telegram','501') RETURNING id",
            (client_id,),
        ).fetchall()[0][0]
        notification_id = conn.execute(
            "INSERT INTO staff_notifications "
            "(client_id, target_id, event, payload, idempotency_key) "
            "VALUES (%s,%s,'approval_needed','{}'::jsonb,'k') RETURNING id",
            (client_id, target_id),
        ).fetchall()[0][0]
        approval_id = conn.execute(
            "INSERT INTO approvals "
            "(client_id, conversation_id, trace_id, draft, reason, expires_at) "
            "VALUES (%s,%s,%s,'Original draft','test', now() + interval '30 minutes') "
            "RETURNING id",
            (client_id, conversation_id, customer.trace_id),
        ).fetchall()[0][0]
        action_id = conn.execute(
            "INSERT INTO staff_actions "
            "(client_id, notification_id, target_id, approval_id, action, expires_at) "
            "VALUES (%s,%s,%s,%s,'edit_reply', now() + interval '1 hour') RETURNING id",
            (client_id, notification_id, target_id, approval_id),
        ).fetchall()[0][0]

    # Text before the Edit tap is an ordinary private message, not a replacement.
    await ingress.receive(
        public_id,
        secret,
        post(_message_update(update_id=2, sender_id=501, chat_id=501, text="hello")),
    )
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        assert conn.execute(
            "SELECT status FROM approvals WHERE id=%s", (approval_id,)
        ).fetchone() == ("pending",)

    await ingress.receive(public_id, secret, post(_staff_tap_update(3, 501, f"s:{action_id}")))
    assert any(
        call["url"] == "answerCallbackQuery" and "replacement" in call["text"] for call in sent
    )
    assert not any(call["url"] == "editMessageReplyMarkup" for call in sent)

    sent.clear()
    text_update = post(
        _message_update(update_id=4, sender_id=501, chat_id=501, text="Edited by staff")
    )
    await ingress.receive(public_id, secret, text_update)
    await ingress.receive(public_id, secret, text_update)
    assert [c["text"] for c in sent if c["url"] == "sendMessage"] == ["Edited reply queued."]

    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        assert conn.execute(
            "SELECT m.body, count(d.id) FROM messages m "
            "JOIN outbound_deliveries d ON d.message_id=m.id "
            "WHERE m.client_id=%s AND m.direction='outbound' GROUP BY m.body",
            (client_id,),
        ).fetchall() == [("Edited by staff", 1)]
        assert conn.execute(
            "SELECT status, final_text FROM approvals WHERE id=%s", (approval_id,)
        ).fetchone() == ("edited", "Edited by staff")
        assert conn.execute(
            "SELECT action FROM approval_audit WHERE approval_id=%s", (approval_id,)
        ).fetchall() == [("edited",)]
        assert conn.execute(
            "SELECT status FROM ingress_events WHERE client_id=%s AND idempotency_key=%s",
            (client_id, f"telegram:{public_id}:4"),
        ).fetchall() == [("completed",)]


@requires_docker
async def test_group_staff_edit_binds_to_the_alert_reply(
    ingress_context: tuple[TelegramIngress, db.AsyncConnectionPool, str, str, str],
    pg_container: object,
) -> None:
    _, pool, client_id, public_id, secret = ingress_context
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append({"url": request.url.path.rsplit("/", 1)[-1], **json.loads(request.content)})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})

    ingress = TelegramIngress(
        pool, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )

    def post(update: dict[str, Any]) -> bytes:
        return json.dumps(update).encode()

    def group_text(update_id: int, text: str, reply_to: int | None) -> dict[str, Any]:
        message: dict[str, Any] = {
            "message_id": update_id,
            "date": 1791676800,
            "from": {"id": 501, "first_name": "Staff"},
            "chat": {"id": -100500, "type": "supergroup"},
            "text": text,
        }
        if reply_to is not None:
            message["reply_to_message"] = {"message_id": reply_to}
        return {"update_id": update_id, "message": message}

    customer = await ingress.receive(
        public_id, secret, post(_message_update(update_id=1, sender_id=777, chat_id=777))
    )
    owner_dsn = owner_dsn_for(pg_container)
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conversation_id = conn.execute(
            "SELECT id FROM conversations WHERE client_id=%s AND channel_thread='777'", (client_id,)
        ).fetchall()[0][0]
        target_id = conn.execute(
            "INSERT INTO staff_notification_targets (client_id, channel, address) "
            "VALUES (%s,'telegram','-100500') RETURNING id",
            (client_id,),
        ).fetchall()[0][0]
        notification_id = conn.execute(
            "INSERT INTO staff_notifications "
            "(client_id, target_id, event, payload, idempotency_key) "
            "VALUES (%s,%s,'approval_needed','{}'::jsonb,'kg') RETURNING id",
            (client_id, target_id),
        ).fetchall()[0][0]
        approval_id = conn.execute(
            "INSERT INTO approvals "
            "(client_id, conversation_id, trace_id, draft, reason, expires_at) "
            "VALUES (%s,%s,%s,'Original draft','test', now() + interval '30 minutes') "
            "RETURNING id",
            (client_id, conversation_id, customer.trace_id),
        ).fetchall()[0][0]
        action_id = conn.execute(
            "INSERT INTO staff_actions "
            "(client_id, notification_id, target_id, approval_id, action, expires_at) "
            "VALUES (%s,%s,%s,%s,'edit_reply', now() + interval '1 hour') RETURNING id",
            (client_id, notification_id, target_id, approval_id),
        ).fetchall()[0][0]

    def counts() -> tuple[int, str]:
        with psycopg.connect(owner_dsn) as conn:
            customers = conn.execute(
                "SELECT count(*) FROM contacts WHERE client_id=%s", (client_id,)
            ).fetchone()
            status = conn.execute(
                "SELECT status FROM approvals WHERE id=%s", (approval_id,)
            ).fetchone()
        assert customers is not None and status is not None
        return customers[0], status[0]

    before = counts()
    with pytest.raises(IngressError) as unarmed:
        await ingress.receive(public_id, secret, post(group_text(2, "hi", 40)))
    assert unarmed.value.status_code == 422

    tap = _staff_tap_update(3, -100500, f"s:{action_id}")
    tap["callback_query"]["from"]["id"] = 501
    await ingress.receive(public_id, secret, post(tap))

    with pytest.raises(IngressError) as unbound:
        await ingress.receive(public_id, secret, post(group_text(4, "wrong reply", 41)))
    assert unbound.value.status_code == 422
    with pytest.raises(IngressError):
        await ingress.receive(public_id, secret, post(group_text(5, "no reply", None)))
    assert counts() == before

    sent.clear()
    await ingress.receive(public_id, secret, post(group_text(6, "Edited in group", 40)))
    assert [c["text"] for c in sent if c["url"] == "sendMessage"] == ["Edited reply queued."]
    with psycopg.connect(owner_dsn) as conn:
        assert conn.execute(
            "SELECT status, final_text FROM approvals WHERE id=%s", (approval_id,)
        ).fetchone() == ("edited", "Edited in group")
        assert conn.execute(
            "SELECT count(*) FROM outbound_deliveries WHERE client_id=%s", (client_id,)
        ).fetchone() == (1,)
    assert counts()[0] == before[0]


@requires_docker
async def test_voice_note_is_durably_queued_without_provider_calls(ingress_context: Any) -> None:
    ingress, pool, client_id, public_id, secret = ingress_context
    update = _message_update()
    update["message"].pop("text")
    update["message"]["voice"] = {
        "file_id": "synthetic-voice-id",
        "duration": 3,
        "mime_type": "audio/ogg",
    }
    receipt = await ingress.receive(public_id, secret, json.dumps(update).encode())
    assert receipt.accepted and not receipt.duplicate
    duplicate = await ingress.receive(public_id, secret, json.dumps(update).encode())
    assert duplicate.duplicate
    async with tx(pool, client_id) as conn:
        row = await db.q.fetch_one(
            "ingress/get_event_hash.sql", (client_id, f"telegram:{public_id}:123"), conn=conn
        )
    assert row is not None


# --- Telegram share-my-number ------------------------------------------------------------------

_PHONE = "0123456789"
_ASK = "Please use the button to share your own number."


def _share_update(
    *,
    update_id: int,
    sender_id: int = 501,
    user_id: int | None = 501,
    phone: str = _PHONE,
    forwarded: bool = False,
    chat: dict[str, Any] | None = None,
) -> dict[str, Any]:
    update = _message_update(update_id=update_id, sender_id=sender_id, chat_id=sender_id)
    message = update["message"]
    message.pop("text")
    contact: dict[str, Any] = {"phone_number": phone, "first_name": "Synthetic"}
    if user_id is not None:
        contact["user_id"] = user_id
    message["contact"] = contact
    if forwarded:
        message["forward_origin"] = {"type": "user", "date": 1791676000}
    if chat is not None:
        message["chat"] = chat
    return update


class _Share:
    """An ingress that records what it sends to Telegram, and the tenant's owner connection."""

    def __init__(
        self, pool: db.AsyncConnectionPool, pg_container: object, client_id: str, public_id: str
    ) -> None:
        self.sent: list[dict[str, Any]] = []
        self.client_id = client_id
        self.public_id = public_id
        self.pg_container = pg_container

        def handler(request: httpx.Request) -> httpx.Response:
            self.sent.append(
                {"method": request.url.path.rsplit("/", 1)[-1], **json.loads(request.read())}
            )
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})

        self.ingress = TelegramIngress(
            pool, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )

    async def post(self, update: dict[str, Any]) -> Any:
        return await self.ingress.receive(
            self.public_id, "dev-webhook-secret", json.dumps(update).encode()
        )

    def rows(self, query: str, *params: object) -> list[tuple[Any, ...]]:
        with psycopg.connect(owner_dsn_for(self.pg_container), autocommit=True) as conn:
            return conn.execute(cast(LiteralString, query), params).fetchall()

    def add_contact(self, *, days_old: float = 0, **cols: object) -> uuid.UUID:
        row = {"id": uuid.uuid4(), "client_id": self.client_id, **cols}
        names, slots = ", ".join(row), ", ".join(["%s"] * len(row))
        self.rows(
            f"INSERT INTO contacts ({names}, created_at)"
            f" VALUES ({slots}, now() - %s * interval '1 day') RETURNING id",
            *row.values(),
            days_old,
        )
        return cast(uuid.UUID, row["id"])

    def messages_sent(self) -> list[dict[str, Any]]:
        return [call for call in self.sent if call["method"] == "sendMessage"]

    def envelope_bodies(self) -> list[str]:
        return [
            row[0]
            for row in self.rows(
                "SELECT raw -> 'envelope' -> 'message' ->> 'body_raw' FROM ingress_events"
                " WHERE client_id = %s AND raw IS NOT NULL ORDER BY created_at",
                self.client_id,
            )
        ]


@pytest.fixture()
def share(
    ingress_context: tuple[TelegramIngress, db.AsyncConnectionPool, str, str, str],
    pg_container: object,
) -> _Share:
    _, pool, client_id, public_id, _ = ingress_context
    return _Share(pool, pg_container, client_id, public_id)


@requires_docker
async def test_sharing_your_own_number_verifies_it_and_the_graph_never_sees_it(
    share: _Share,
) -> None:
    with capture_logs() as logs:
        receipt = await share.post(_share_update(update_id=10))

    assert receipt.accepted and not receipt.duplicate
    assert share.rows(
        "SELECT phone, phone_digits, phone_verified_at IS NOT NULL FROM contacts"
        " WHERE client_id = %s",
        share.client_id,
    ) == [("+60123456789", "60123456789", True)]
    [ack] = share.messages_sent()
    assert ack["text"] == "Thanks, I have your number."
    assert ack["reply_markup"] == {"remove_keyboard": True}
    # The pipeline continues from a synthetic inbound; the number is nowhere in what it reads.
    assert share.envelope_bodies() == ["[shared phone number]"]
    stored = share.rows(
        "SELECT raw::text, payload_hash FROM ingress_events WHERE client_id = %s", share.client_id
    )
    assert all("0123456789" not in raw and "123456789" not in raw for raw, _ in stored)
    assert "123456789" not in str(logs)
    assert share.rows(
        "SELECT status FROM ingress_events WHERE client_id = %s", share.client_id
    ) == [("received",)]


@requires_docker
@pytest.mark.parametrize("outcome", ["invalid", "review"])
async def test_a_share_that_stores_no_number_gets_a_neutral_thanks(
    share: _Share, outcome: str
) -> None:
    if outcome == "review":
        # Two contacts already hold the digits: a review opens and nothing is stored.
        share.add_contact(days_old=5, phone=_PHONE)
        share.add_contact(days_old=4, phone=_PHONE)
    phone = "12345" if outcome == "invalid" else _PHONE

    receipt = await share.post(_share_update(update_id=14, phone=phone))

    assert receipt.accepted and not receipt.duplicate
    [ack] = share.messages_sent()
    assert ack["text"] == "Thanks."
    assert ack["reply_markup"] == {"remove_keyboard": True}
    assert (
        share.rows(
            "SELECT id FROM contacts WHERE client_id = %s AND phone_verified_at IS NOT NULL",
            share.client_id,
        )
        == []
    )
    reviews = share.rows(
        "SELECT reason FROM contact_link_reviews WHERE client_id = %s", share.client_id
    )
    assert reviews == ([("several_matches",)] if outcome == "review" else [])


@requires_docker
@pytest.mark.parametrize("variant", ["other_user", "no_user_id", "forwarded"])
async def test_a_card_that_is_not_your_own_is_refused_and_nothing_is_stored(
    share: _Share, variant: str
) -> None:
    update = {
        "other_user": _share_update(update_id=11, user_id=999),
        "no_user_id": _share_update(update_id=11, user_id=None),
        "forwarded": _share_update(update_id=11, user_id=501, forwarded=True),
    }[variant]
    receipt = await share.post(update)

    assert receipt.accepted and not receipt.duplicate
    assert [m["text"] for m in share.messages_sent()] == [_ASK]
    assert "reply_markup" not in share.messages_sent()[0]
    assert share.rows("SELECT id FROM contacts WHERE client_id = %s", share.client_id) == []
    assert share.rows("SELECT id FROM conversations WHERE client_id = %s", share.client_id) == []
    assert share.rows("SELECT id FROM contact_links WHERE client_id = %s", share.client_id) == []
    # The update is recorded as handled (a retry is a duplicate) and holds no body.
    assert share.rows(
        "SELECT status, raw IS NULL FROM ingress_events WHERE client_id = %s",
        share.client_id,
    ) == [("completed", True)]
    again = await share.post(update)
    assert again.duplicate is True
    assert [m["text"] for m in share.messages_sent()] == [_ASK]


@requires_docker
async def test_a_typed_phone_number_never_verifies(share: _Share) -> None:
    await share.post(_message_update(update_id=12, text=f"my number is {_PHONE}"))
    await share.post(_message_update(update_id=13, text="+60123456789"))

    assert share.rows(
        "SELECT phone, phone_verified_at FROM contacts WHERE client_id = %s", share.client_id
    ) == [(None, None)]
    assert share.messages_sent() == []


@requires_docker
async def test_a_share_that_matches_one_other_contact_links_and_the_next_message_follows(
    share: _Share,
) -> None:
    staff = share.add_contact(days_old=5, phone=_PHONE, full_name="Staff entered")
    await share.post(_message_update(update_id=20, text="hello"))
    [telegram] = [
        row[0]
        for row in share.rows(
            "SELECT id FROM contacts WHERE client_id = %s AND telegram_id = 501", share.client_id
        )
    ]
    assert telegram != staff

    await share.post(_share_update(update_id=21))
    assert share.rows(
        "SELECT count(*) FROM contact_links WHERE client_id = %s", share.client_id
    ) == [(1,)]
    assert share.rows("SELECT merged_into FROM contacts WHERE id = %s", telegram) == [(staff,)]
    assert share.rows(
        "SELECT telegram_id, phone_verified_at IS NOT NULL FROM contacts WHERE id = %s", staff
    ) == [(501, True)]
    assert share.rows(
        "SELECT contact_id FROM conversations WHERE client_id = %s", share.client_id
    ) == [(staff,)]
    assert [m["text"] for m in share.messages_sent()] == ["Thanks, I have your number."]

    # The next message resolves to the survivor: no 409, the same conversation.
    follow = await share.post(_message_update(update_id=22, text="and one more thing"))
    assert follow.accepted and not follow.duplicate
    assert share.rows(
        "SELECT DISTINCT raw -> 'envelope' -> 'contact' ->> 'id' FROM ingress_events"
        " WHERE client_id = %s AND idempotency_key = %s",
        share.client_id,
        f"telegram:{share.public_id}:22",
    ) == [(str(staff),)]
    assert share.rows(
        "SELECT count(*) FROM conversations WHERE client_id = %s", share.client_id
    ) == [(1,)]


@requires_docker
async def test_ambiguous_matches_open_one_review_and_the_conversation_continues(
    share: _Share,
) -> None:
    share.add_contact(phone=_PHONE, full_name="A")
    share.add_contact(phone="+60 12-345 6789", full_name="B")

    receipt = await share.post(_share_update(update_id=30))
    await share.post(_share_update(update_id=31))

    assert receipt.accepted
    assert share.rows(
        "SELECT reason FROM contact_link_reviews WHERE client_id = %s AND status = 'open'",
        share.client_id,
    ) == [("several_matches",)]
    assert share.rows(
        "SELECT count(*) FROM contact_links WHERE client_id = %s", share.client_id
    ) == [(0,)]
    assert share.envelope_bodies() == ["[shared phone number]", "[shared phone number]"]
    assert share.rows(
        "SELECT phone_verified_at FROM contacts WHERE client_id = %s AND telegram_id = 501",
        share.client_id,
    ) == [(None,)]


@requires_docker
async def test_a_repeated_share_update_is_processed_once(
    share: _Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real = ingress_module.verify_phone

    async def counting(*args: Any, **kwargs: Any) -> Any:
        calls.append(args[3])
        return await real(*args, **kwargs)

    monkeypatch.setattr(ingress_module, "verify_phone", counting)
    update = _share_update(update_id=40)
    first = await share.post(update)
    stamp = share.rows(
        "SELECT phone_verified_at FROM contacts WHERE client_id = %s", share.client_id
    )
    second = await share.post(update)

    assert not first.duplicate and second.duplicate
    assert second.trace_id == first.trace_id
    assert len(calls) == 1
    assert len(share.messages_sent()) == 1
    assert (
        share.rows("SELECT phone_verified_at FROM contacts WHERE client_id = %s", share.client_id)
        == stamp
    )
    assert share.rows(
        "SELECT count(*) FROM ingress_events WHERE client_id = %s", share.client_id
    ) == [(1,)]


@requires_docker
async def test_a_contact_merged_during_the_share_is_resolved_again(
    share: _Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = ingress_module.verify_phone
    calls: list[int] = []

    async def merged_once(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise HTTPException(409, "contact_merged")
        return await real(*args, **kwargs)

    monkeypatch.setattr(ingress_module, "verify_phone", merged_once)
    with capture_logs() as logs:
        receipt = await share.post(_share_update(update_id=50))

    assert receipt.accepted and len(calls) == 2
    assert [(e["event"], e["log_level"]) for e in logs] == [
        ("telegram_contact_merged_retry", "warning")
    ]
    assert share.rows(
        "SELECT phone_verified_at IS NOT NULL FROM contacts WHERE client_id = %s", share.client_id
    ) == [(True,)]


@requires_docker
async def test_a_contact_merged_twice_is_a_retryable_503_and_stores_nothing(
    share: _Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def always_merged(*args: Any, **kwargs: Any) -> Any:
        raise HTTPException(409, "contact_merged")

    monkeypatch.setattr(ingress_module, "verify_phone", always_merged)
    with capture_logs() as logs, pytest.raises(IngressError) as raised:
        await share.post(_share_update(update_id=51))

    assert raised.value.status_code == 503
    assert [e["log_level"] for e in logs] == ["warning", "warning", "error"]
    assert share.rows("SELECT id FROM contacts WHERE client_id = %s", share.client_id) == []
    assert share.rows("SELECT id FROM ingress_events WHERE client_id = %s", share.client_id) == []
    assert share.messages_sent() == []


@requires_docker
async def test_any_other_http_error_from_verify_propagates(
    share: _Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def missing(*args: Any, **kwargs: Any) -> Any:
        raise HTTPException(404, "contact_not_found")

    monkeypatch.setattr(ingress_module, "verify_phone", missing)
    with pytest.raises(HTTPException) as raised:
        await share.post(_share_update(update_id=52))

    assert raised.value.detail == "contact_not_found"


@requires_docker
async def test_a_contact_card_in_a_group_reply_is_not_handled_as_a_share(share: _Share) -> None:
    update = _share_update(update_id=60, chat={"id": -100777, "type": "supergroup"})
    update["message"]["reply_to_message"] = {"message_id": 40}
    with pytest.raises(IngressError) as raised:
        await share.post(update)

    assert raised.value.status_code == 422
    assert share.rows("SELECT id FROM contacts WHERE client_id = %s", share.client_id) == []
    assert share.messages_sent() == []
