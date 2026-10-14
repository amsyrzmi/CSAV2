"""Tests for LG09 cutover: strangler proxy, runtime rollback, and operational runbooks."""

from __future__ import annotations

import base64
import json
import uuid

import httpx
import psycopg
import pytest
from csa.channels.ingress import (
    TELEGRAM_WEBHOOK_SECRET_META_KEY,
    IngressError,
    TelegramIngress,
)
from csa.core import crypto, db, settings
from csa.core.tenancy import tx
from csa.ops.runbooks import (
    expire_stuck_approvals,
    force_decide_mode,
    get_circuit_states,
    inspect_dlq,
    redrive_dlq,
    requeue_turn,
    reset_circuit,
    set_deployment_mode,
    set_runtime,
)
from psycopg.types.json import Jsonb

from tests.conftest import owner_dsn_for, requires_docker

_KEY = "base64:" + base64.b64encode(b"\x05" * 32).decode("ascii")
pytestmark = [requires_docker, pytest.mark.asyncio, pytest.mark.usefixtures("lg_setup_dsn")]


def _configure_settings(monkeypatch: pytest.MonkeyPatch, app_dsn: str, owner_dsn: str) -> None:
    monkeypatch.setenv("ENV", "ci")
    monkeypatch.setenv("DATABASE_URL", app_dsn)
    monkeypatch.setenv("LG_DATABASE_URL", owner_dsn)
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.test")
    monkeypatch.setenv("CREDENTIALS_KEY", _KEY)
    settings.get_settings.cache_clear()


def _raw_update(update_id: int = 1001, text: str = "Hello from customer") -> bytes:
    return json.dumps(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "date": 1791676800,
                "from": {"id": 888111, "first_name": "StranglerCustomer"},
                "chat": {"id": 888111, "type": "private"},
                "text": text,
            },
        }
    ).encode("utf-8")


async def test_strangler_proxy_n8n_and_instant_rollback(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies gateway proxies to n8n when runtime='n8n' and routes to LangGraph on rollback."""
    owner_dsn = owner_dsn_for(pg_container)
    _configure_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)

    client_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    endpoint_id = uuid.uuid4()
    public_id = f"telegram-strangler-{uuid.uuid4().hex[:12]}"
    webhook_secret = "secret-strangler"
    bot_token = "123:strangler-token"
    n8n_url = "https://n8n.internal.test/webhook/csa-telegram"

    enc_secret = crypto.encrypt(
        webhook_secret,
        crypto.credential_aad(
            "channel_credentials",
            "meta.telegram_webhook_secret_enc",
            str(credential_id),
        ),
    )
    enc_token = crypto.encrypt(
        bot_token,
        crypto.credential_aad("channel_credentials", "access_token", str(credential_id)),
    )

    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clients "
            "(id, slug, name, status, platform_version, deployment_mode, config) "
            "VALUES (%s, %s, %s, 'active', 'lg09-test', 'auto', %s)",
            (
                client_id,
                f"client-{client_id.hex[:8]}",
                "Client Strangler",
                Jsonb({"runtime": "n8n", "n8n_webhook_url": n8n_url}),
            ),
        )
        conn.execute(
            "INSERT INTO channel_credentials (id, client_id, channel, access_token, meta) "
            "VALUES (%s, %s, 'telegram', %s, %s)",
            (
                credential_id,
                client_id,
                enc_token,
                Jsonb({TELEGRAM_WEBHOOK_SECRET_META_KEY: enc_secret}),
            ),
        )
        conn.execute(
            "INSERT INTO webhook_endpoints (id, client_id, public_id, credential_id) "
            "VALUES (%s, %s, %s, %s)",
            (endpoint_id, client_id, public_id, credential_id),
        )

    proxied_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        proxied_requests.append(request)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(mock_handler)
    http_client = httpx.AsyncClient(transport=transport)

    pool = db.build_pool(app_dsn, max_size=4)
    await pool.open(wait=True)
    try:
        ingress = TelegramIngress(pool, http_client=http_client)

        # 1. Update received when runtime="n8n"
        receipt1 = await ingress.receive(public_id, webhook_secret, _raw_update(101, "Hello n8n"))
        assert receipt1.accepted is True
        assert receipt1.proxied is True
        assert len(proxied_requests) == 1
        assert str(proxied_requests[0].url) == n8n_url
        assert proxied_requests[0].headers.get("x-telegram-bot-api-secret-token") == webhook_secret

        # Confirm nothing was inserted into ingress_events
        with psycopg.connect(owner_dsn) as conn:
            cnt_row = conn.execute(
                "SELECT count(*) FROM ingress_events WHERE client_id=%s", (client_id,)
            ).fetchone()
            assert cnt_row is not None
            assert cnt_row[0] == 0

        # 2. Instant rollback / cutover drill: flip runtime to 'langgraph'
        async with tx(pool, str(client_id)) as conn:
            await set_runtime(conn, client_id, "langgraph")

        # 3. Update received after rollback to LangGraph
        receipt2 = await ingress.receive(
            public_id, webhook_secret, _raw_update(102, "Hello LangGraph")
        )
        assert receipt2.accepted is True
        assert receipt2.proxied is False
        assert len(proxied_requests) == 1  # No new proxy call

        # Confirm update was durably enqueued into ingress_events
        with psycopg.connect(owner_dsn) as conn:
            row = conn.execute(
                "SELECT id, status FROM ingress_events WHERE client_id=%s", (client_id,)
            ).fetchone()
        assert row is not None
        assert row[1] == "received"
    finally:
        await http_client.aclose()
        await pool.close()


async def test_strangler_proxy_shadow_mode(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies runtime='n8n' with shadow mode proxies to n8n AND enqueues to LangGraph."""
    owner_dsn = owner_dsn_for(pg_container)
    _configure_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)

    client_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    endpoint_id = uuid.uuid4()
    public_id = f"telegram-shadow-{uuid.uuid4().hex[:12]}"
    webhook_secret = "secret-shadow"
    bot_token = "123:shadow-token"
    n8n_url = "https://n8n.internal.test/webhook/shadow"

    enc_secret = crypto.encrypt(
        webhook_secret,
        crypto.credential_aad(
            "channel_credentials",
            "meta.telegram_webhook_secret_enc",
            str(credential_id),
        ),
    )
    enc_token = crypto.encrypt(
        bot_token,
        crypto.credential_aad("channel_credentials", "access_token", str(credential_id)),
    )

    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clients "
            "(id, slug, name, status, platform_version, deployment_mode, config) "
            "VALUES (%s, %s, %s, 'active', 'lg09-test', 'shadow', %s)",
            (
                client_id,
                f"client-{client_id.hex[:8]}",
                "Client Shadow",
                Jsonb(
                    {
                        "runtime": "n8n",
                        "n8n_webhook_url": n8n_url,
                        "policy": {"deployment_mode": "shadow"},
                    }
                ),
            ),
        )
        conn.execute(
            "INSERT INTO channel_credentials (id, client_id, channel, access_token, meta) "
            "VALUES (%s, %s, 'telegram', %s, %s)",
            (
                credential_id,
                client_id,
                enc_token,
                Jsonb({TELEGRAM_WEBHOOK_SECRET_META_KEY: enc_secret}),
            ),
        )
        conn.execute(
            "INSERT INTO webhook_endpoints (id, client_id, public_id, credential_id) "
            "VALUES (%s, %s, %s, %s)",
            (endpoint_id, client_id, public_id, credential_id),
        )

    proxied_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        proxied_requests.append(request)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(mock_handler)
    http_client = httpx.AsyncClient(transport=transport)

    pool = db.build_pool(app_dsn, max_size=4)
    await pool.open(wait=True)
    try:
        ingress = TelegramIngress(pool, http_client=http_client)
        receipt = await ingress.receive(public_id, webhook_secret, _raw_update(201, "Shadow test"))
        assert receipt.accepted is True
        assert receipt.proxied is True
        assert len(proxied_requests) == 1

        # In shadow mode, row is ALSO enqueued into ingress_events
        # for coordinator background evaluation
        with psycopg.connect(owner_dsn) as conn:
            row = conn.execute(
                "SELECT id, status, trace_id FROM ingress_events WHERE client_id=%s",
                (client_id,),
            ).fetchone()
        assert row is not None
        assert row[1] == "received"
        assert row[2] == receipt.trace_id
    finally:
        await http_client.aclose()
        await pool.close()


async def test_strangler_proxy_errors(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies that missing n8n URL or n8n network failure raises appropriate IngressError."""
    owner_dsn = owner_dsn_for(pg_container)
    _configure_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)

    client_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    endpoint_id = uuid.uuid4()
    public_id = f"telegram-err-{uuid.uuid4().hex[:12]}"
    webhook_secret = "secret-err"
    bot_token = "123:err-token"

    enc_secret = crypto.encrypt(
        webhook_secret,
        crypto.credential_aad(
            "channel_credentials",
            "meta.telegram_webhook_secret_enc",
            str(credential_id),
        ),
    )
    enc_token = crypto.encrypt(
        bot_token,
        crypto.credential_aad("channel_credentials", "access_token", str(credential_id)),
    )

    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clients (id, slug, name, status, platform_version, config) "
            "VALUES (%s, %s, %s, 'active', 'lg09-test', %s)",
            (
                client_id,
                f"client-{client_id.hex[:8]}",
                "Client Err",
                Jsonb({"runtime": "n8n"}),  # No n8n_webhook_url!
            ),
        )
        conn.execute(
            "INSERT INTO channel_credentials (id, client_id, channel, access_token, meta) "
            "VALUES (%s, %s, 'telegram', %s, %s)",
            (
                credential_id,
                client_id,
                enc_token,
                Jsonb({TELEGRAM_WEBHOOK_SECRET_META_KEY: enc_secret}),
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
        ingress = TelegramIngress(pool)
        # 1. Missing n8n URL raises 503
        with pytest.raises(IngressError) as exc_info:
            await ingress.receive(public_id, webhook_secret, _raw_update(301))
        assert exc_info.value.status_code == 503

        # 2. n8n upstream error raises 502
        with psycopg.connect(owner_dsn, autocommit=True) as conn:
            conn.execute(
                "UPDATE clients "
                "SET config = jsonb_set(config, '{n8n_webhook_url}', "
                "'\"https://failing.n8n/webhook\"') "
                "WHERE id = %s",
                (client_id,),
            )

        failing_client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(500, text="Internal Error"))
        )
        ingress_failing = TelegramIngress(pool, http_client=failing_client)
        with pytest.raises(IngressError) as exc_502:
            await ingress_failing.receive(public_id, webhook_secret, _raw_update(302))
        assert exc_502.value.status_code == 502
        await failing_client.aclose()
    finally:
        await pool.close()


async def test_ops_runbooks_procedures(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies operational runbook functions: requeue turn, DLQ, breaker, decide, approvals."""
    owner_dsn = owner_dsn_for(pg_container)
    _configure_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)

    client_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    endpoint_id = uuid.uuid4()
    contact_id = uuid.uuid4()
    conv_id = uuid.uuid4()

    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clients (id, slug, name, status, platform_version, deployment_mode) "
            "VALUES (%s, %s, %s, 'active', 'lg09-test', 'auto')",
            (client_id, f"client-{client_id.hex[:8]}", "Client Runbooks"),
        )
        conn.execute(
            "INSERT INTO channel_credentials (id, client_id, channel, access_token) "
            "VALUES (%s, %s, 'telegram', 'token')",
            (credential_id, client_id),
        )
        conn.execute(
            "INSERT INTO webhook_endpoints (id, client_id, public_id, credential_id) "
            "VALUES (%s, %s, %s, %s)",
            (endpoint_id, client_id, f"endpoint-runbook-{endpoint_id.hex[:16]}", credential_id),
        )
        conn.execute(
            "INSERT INTO contacts (id, client_id, full_name) VALUES (%s, %s, 'Ops Contact')",
            (contact_id, client_id),
        )
        conn.execute(
            "INSERT INTO conversations (id, client_id, contact_id, channel, channel_thread) "
            "VALUES (%s, %s, %s, 'telegram', '12345')",
            (conv_id, client_id, contact_id),
        )

    pool = db.build_pool(app_dsn, max_size=4)
    await pool.open(wait=True)
    try:
        # 1. Runbook 1: Re-queue failed turn
        evt_id = uuid.uuid4()
        trace_id = uuid.uuid4()
        async with tx(pool, str(client_id)) as conn:
            await conn.execute(
                "INSERT INTO ingress_events "
                "(id, client_id, endpoint_id, contact_id, idempotency_key, "
                "payload_hash, trace_id, status, attempts, last_error) "
                "VALUES (%s, %s, %s, %s, 'key-fail', 'hash', %s, 'failed', 3, 'TimeoutError')",
                (evt_id, client_id, endpoint_id, contact_id, trace_id),
            )
            requeued = await requeue_turn(conn, client_id, evt_id)
            assert requeued is not None
            assert requeued["status"] == "received"
            assert requeued["attempts"] == 0

        # 2. Runbook 2: DLQ inspection and redrive
        dlq_trace_id = uuid.uuid4()
        async with tx(pool, str(client_id)) as conn:
            await conn.execute(
                "INSERT INTO dlq "
                "(id, client_id, trace_id, stage, envelope, error, replay_attempts, max_replays) "
                "VALUES (99001, %s, %s, 'turn', %s, %s, 0, 2)",
                (
                    client_id,
                    dlq_trace_id,
                    Jsonb(
                        {
                            "meta": {"endpoint_id": str(endpoint_id)},
                            "contact": {"id": str(contact_id)},
                        }
                    ),
                    Jsonb({"error": "unhandled"}),
                ),
            )
            items = await inspect_dlq(conn, client_id)
            assert len(items) >= 1
            assert items[0]["id"] == 99001

            redriven = await redrive_dlq(conn, client_id, 99001)
            assert redriven is not None
            assert redriven["id"] == 99001

        # 3. Runbook 3: Circuit breaker reset
        async with tx(pool, str(client_id)) as conn:
            await conn.execute(
                "INSERT INTO capability_registry "
                "(id, client_id, name, display_name, description, "
                "pattern, workflow_id, input_schema, circuit_state, fail_count, opened_at) "
                "VALUES (gen_random_uuid(), %s, 'test_agent', 'Test Agent', "
                "'Desc', 'pattern', 'wf1', '{}', 'open', 5, now())",
                (client_id,),
            )
            states = await get_circuit_states(conn, client_id)
            assert any(s["name"] == "test_agent" and s["circuit_state"] == "open" for s in states)

            reset_res = await reset_circuit(conn, client_id, "test_agent")
            assert reset_res is not None
            assert reset_res["circuit_state"] == "closed"

        # 4. Runbook 4 & 6: Force decide mode and deployment mode
        async with tx(pool, str(client_id)) as conn:
            decide_res = await force_decide_mode(conn, client_id, "llm")
            assert decide_res is not None
            assert decide_res["decide_mode"] == "llm"

            mode_res = await set_deployment_mode(conn, client_id, "suggest")
            assert mode_res is not None
            assert mode_res["deployment_mode"] == "suggest"

        # 5. Runbook 8: Expire stuck approvals
        async with tx(pool, str(client_id)) as conn:
            appr_id = 88001
            await conn.execute(
                "INSERT INTO approvals "
                "(id, client_id, conversation_id, trace_id, draft, reason, status, expires_at) "
                "VALUES (%s, %s, %s, %s, 'Draft text', 'High risk', 'pending', "
                "now() - interval '10 minutes')",
                (appr_id, client_id, conv_id, uuid.uuid4()),
            )
            expired_count = await expire_stuck_approvals(conn, client_id)
            assert expired_count == 1

            # Check approval status and audit entry
            row_appr = await conn.execute(
                "SELECT status, decided_by FROM approvals WHERE id = %s", (appr_id,)
            )
            appr = await row_appr.fetchone()
            assert appr is not None
            assert appr["status"] == "expired"
            assert appr["decided_by"] == "system_runbook"

            row_audit = await conn.execute(
                "SELECT action, actor FROM approval_audit WHERE approval_id = %s", (appr_id,)
            )
            audit = await row_audit.fetchone()
            assert audit is not None
            assert audit["action"] == "expired"
            assert audit["actor"] == "system_runbook"
    finally:
        await pool.close()
