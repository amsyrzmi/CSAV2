"""WhatsApp outbox delivery: service window, error mapping, takeover suppression."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import psycopg
import pytest
from apps.delivery.main import DeliveryWorker
from csa.core import crypto, db, settings
from psycopg.types.json import Jsonb

from tests.conftest import owner_dsn_for, requires_docker

_KEY = "base64:" + base64.b64encode(b"\x07" * 32).decode("ascii")
PHONE_NUMBER_ID = "109876543210"
WA_ID = "60123456789"


def _configure(
    monkeypatch: pytest.MonkeyPatch, *, app_dsn: str, owner_dsn: str, whatsapp: bool = True
) -> None:
    values = {
        "ENV": "ci",
        "DATABASE_URL": app_dsn,
        "LG_DATABASE_URL": owner_dsn,
        "GROQ_API_KEY": "test-key",
        "PUBLIC_BASE_URL": "https://example.test",
        "CREDENTIALS_KEY": _KEY,
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    for name in ("WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN"):
        if whatsapp:
            monkeypatch.setenv(name, f"synthetic-{name.lower()}")
        else:
            monkeypatch.delenv(name, raising=False)
    settings.get_settings.cache_clear()


def _seed(
    owner_dsn: str,
    *,
    channel: str = "whatsapp",
    last_inbound_at: datetime | None,
    send_authority: str = "automation",
    human_owner: bool = False,
) -> str:
    client_id, credential_id, endpoint_id, contact_id, conversation_id, message_id, trace_id = (
        uuid.uuid4() for _ in range(7)
    )
    token = crypto.encrypt(
        "synthetic-wa-token",
        crypto.credential_aad("channel_credentials", "access_token", str(credential_id)),
    )
    thread = WA_ID if channel == "whatsapp" else "501"
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clients (id, slug, name, platform_version) VALUES (%s, %s, %s, %s)",
            (client_id, f"wad-{client_id}", "Synthetic WA Delivery", "wa-test"),
        )
        conn.execute("SELECT public.seed_core_capabilities(%s)", (client_id,))
        conn.execute(
            "INSERT INTO channel_credentials (id, client_id, channel, shop_id, access_token) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                credential_id,
                client_id,
                channel,
                PHONE_NUMBER_ID + client_id.hex[:6] if channel == "whatsapp" else None,
                token,
            ),
        )
        conn.execute(
            "INSERT INTO webhook_endpoints (id, client_id, public_id, credential_id) "
            "VALUES (%s, %s, %s, %s)",
            (endpoint_id, client_id, f"wad-{uuid.uuid4().hex}", credential_id),
        )
        conn.execute(
            "INSERT INTO contacts (id, client_id, full_name) VALUES (%s, %s, 'Synthetic')",
            (contact_id, client_id),
        )
        conn.execute(
            "INSERT INTO conversations "
            "(id, client_id, contact_id, channel, channel_thread, last_inbound_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (conversation_id, client_id, contact_id, channel, thread, last_inbound_at),
        )
        if human_owner:
            conn.execute(
                "UPDATE conversations SET owned_by = 'human_support' WHERE id = %s",
                (conversation_id,),
            )
        conn.execute(
            "INSERT INTO ingress_events (id, client_id, endpoint_id, contact_id, "
            "idempotency_key, payload_hash, trace_id, raw) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                uuid.uuid4(),
                client_id,
                endpoint_id,
                contact_id,
                f"wad:{trace_id}",
                hashlib.sha256(b"x").hexdigest(),
                trace_id,
                Jsonb({}),
            ),
        )
        conn.execute(
            "INSERT INTO messages (id, client_id, conversation_id, direction, author, body, "
            "redacted_body, idempotency_key, trace_id, send_authority) "
            "VALUES (%s, %s, %s, 'outbound', 'ai', 'Hello there', 'Hello there', %s, %s, %s)",
            (
                message_id,
                client_id,
                conversation_id,
                f"wad:{trace_id}:out",
                trace_id,
                send_authority,
            ),
        )
        conn.execute(
            "INSERT INTO outbound_deliveries "
            "(client_id, endpoint_id, trace_id, conversation_id, message_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            (client_id, endpoint_id, trace_id, conversation_id, message_id),
        )
    return str(client_id)


def _delivery_row(owner_dsn: str, client_id: str) -> tuple[Any, ...]:
    with psycopg.connect(owner_dsn) as conn:
        row = conn.execute(
            "SELECT status, attempts, last_status_code, last_error, provider_message_id, "
            "extract(epoch FROM next_attempt_at - now()) "
            "FROM outbound_deliveries WHERE client_id = %s",
            (client_id,),
        ).fetchone()
        assert row is not None
        return row


def _attempt_count(owner_dsn: str, client_id: str) -> int:
    with psycopg.connect(owner_dsn) as conn:
        row = conn.execute(
            "SELECT count(*) FROM outbound_delivery_attempts a "
            "JOIN outbound_deliveries d ON d.id = a.delivery_id WHERE d.client_id = %s",
            (client_id,),
        ).fetchone()
    assert row is not None
    return int(row[0])


async def _run(
    owner_dsn: str,
    app_dsn: str,
    client_id: str,
    handler: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_pool = db.build_pool(owner_dsn, max_size=4)
    app_pool = db.build_pool(app_dsn, max_size=4)
    await owner_pool.open(wait=True)
    await app_pool.open(wait=True)
    try:
        # isolate from rows other tests left pending in the shared container
        with psycopg.connect(owner_dsn, autocommit=True) as conn:
            conn.execute(
                "UPDATE outbound_deliveries SET next_attempt_at = now() + interval '1 day' "
                "WHERE client_id <> %s AND status = 'pending'",
                (client_id,),
            )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            worker = DeliveryWorker(owner_pool, app_pool, http)

            async def no_throttle(_chat_id: str) -> None:
                return None

            monkeypatch.setattr(worker, "_throttle", no_throttle)
            assert await worker.process_once() is True
    finally:
        await app_pool.close()
        await owner_pool.close()
        settings.get_settings.cache_clear()


@requires_docker
async def test_whatsapp_reply_inside_window_is_sent_and_provider_id_stored(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    client_id = _seed(owner_dsn, last_inbound_at=datetime.now(UTC) - timedelta(hours=1))
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.OUT"}]})

    await _run(owner_dsn, app_dsn, client_id, handler, monkeypatch)
    assert len(requests) == 1
    assert requests[0].headers["Authorization"] == "Bearer synthetic-wa-token"
    assert requests[0].url.path.endswith("/messages")
    assert PHONE_NUMBER_ID in requests[0].url.path
    body = json.loads(requests[0].content)
    assert body["to"] == WA_ID and body["text"]["body"] == "Hello there"
    row = _delivery_row(owner_dsn, client_id)
    assert row[:5] == ("delivered", 1, 200, None, "wamid.OUT")
    assert _attempt_count(owner_dsn, client_id) == 1


@requires_docker
async def test_reply_outside_window_is_dead_without_retry_or_provider_call(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    last = datetime.now(UTC) - timedelta(hours=24, seconds=1)
    client_id = _seed(owner_dsn, last_inbound_at=last)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.X"}]})

    await _run(owner_dsn, app_dsn, client_id, handler, monkeypatch)
    assert calls == []
    row = _delivery_row(owner_dsn, client_id)
    assert row[:4] == ("dead", 1, None, "outside_service_window")
    assert _attempt_count(owner_dsn, client_id) == 1


@requires_docker
async def test_reply_at_exact_boundary_is_sent(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    frozen = datetime.now(UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> FrozenDatetime:  # type: ignore[override]
            return cls.fromtimestamp(frozen.timestamp(), tz or UTC)

    monkeypatch.setattr("apps.delivery.main.datetime", FrozenDatetime)
    client_id = _seed(owner_dsn, last_inbound_at=frozen - timedelta(hours=24))
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.EDGE"}]})

    await _run(owner_dsn, app_dsn, client_id, handler, monkeypatch)
    assert len(calls) == 1
    assert _delivery_row(owner_dsn, client_id)[0] == "delivered"


@requires_docker
async def test_rate_limited_retries_with_retry_after(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    client_id = _seed(owner_dsn, last_inbound_at=datetime.now(UTC))

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json={"error": {"code": 130429, "message": "x"}}, headers={"Retry-After": "7"}
        )

    await _run(owner_dsn, app_dsn, client_id, handler, monkeypatch)
    row = _delivery_row(owner_dsn, client_id)
    assert row[:4] == ("pending", 1, 429, "rate_limited")
    assert 4 <= float(row[5]) <= 8


@requires_docker
async def test_token_invalid_is_terminal(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    client_id = _seed(owner_dsn, last_inbound_at=datetime.now(UTC))

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": 190, "message": "x"}})

    await _run(owner_dsn, app_dsn, client_id, handler, monkeypatch)
    row = _delivery_row(owner_dsn, client_id)
    assert row[:4] == ("dead", 1, 401, "token_invalid")


@requires_docker
async def test_unconfigured_deployment_fails_whatsapp_rows_closed(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn, whatsapp=False)
    client_id = _seed(owner_dsn, last_inbound_at=datetime.now(UTC))
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.X"}]})

    await _run(owner_dsn, app_dsn, client_id, handler, monkeypatch)
    assert calls == []
    row = _delivery_row(owner_dsn, client_id)
    assert row[:4] == ("pending", 1, None, "channel_not_configured")

    _configure(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn, whatsapp=False)
    tg_client = _seed(owner_dsn, channel="telegram", last_inbound_at=None)

    def tg_handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})

    await _run(owner_dsn, app_dsn, tg_client, tg_handler, monkeypatch)
    assert len(calls) == 1
    assert _delivery_row(owner_dsn, tg_client)[:3] == ("delivered", 1, 200)


@requires_docker
async def test_takeover_suppression_still_applies_on_whatsapp(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    client_id = _seed(
        owner_dsn, last_inbound_at=datetime.now(UTC), human_owner=True, send_authority="automation"
    )
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.X"}]})

    await _run(owner_dsn, app_dsn, client_id, handler, monkeypatch)
    assert calls == []
    assert _delivery_row(owner_dsn, client_id)[0] == "skipped"
