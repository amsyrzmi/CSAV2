"""Durable ingress/outbox worker tests (LG03)."""

from __future__ import annotations

import asyncio
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
from apps.worker.main import MAX_INGRESS_ATTEMPTS, ClaimedIngress, IngressWorker
from csa.channels.telegram import TelegramAdapter
from csa.contracts.envelope import (
    Channel,
    Contact,
    ConversationRef,
    Envelope,
    Message,
    Meta,
)
from csa.core import crypto, db, settings
from psycopg.types.json import Jsonb

from tests.conftest import owner_dsn_for, requires_docker

_KEY = "base64:" + base64.b64encode(b"\x06" * 32).decode("ascii")


def _configure_test_settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    app_dsn: str,
    owner_dsn: str,
) -> None:
    for name, value in {
        "ENV": "ci",
        "DATABASE_URL": app_dsn,
        "LG_DATABASE_URL": owner_dsn,
        "GROQ_API_KEY": "test-key",
        "PUBLIC_BASE_URL": "https://example.test",
        "CREDENTIALS_KEY": _KEY,
    }.items():
        monkeypatch.setenv(name, value)
    settings.get_settings.cache_clear()


def _seed_events(
    owner_dsn: str,
    *,
    count: int = 1,
) -> tuple[str, str, list[uuid.UUID]]:
    client_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    endpoint_id = uuid.uuid4()
    contact_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    trace_ids = [uuid.uuid4() for _ in range(count)]
    event_scope = uuid.uuid4().hex
    token = crypto.encrypt(
        "12345:dev-test-token",
        crypto.credential_aad("channel_credentials", "access_token", str(credential_id)),
    )
    now = datetime.now(UTC)
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clients (id, slug, name, platform_version) VALUES (%s, %s, %s, %s)",
            (client_id, f"worker-{client_id}", "Synthetic Worker Test", "lg03-test"),
        )
        conn.execute("SELECT public.seed_core_capabilities(%s)", (client_id,))
        conn.execute(
            "INSERT INTO channel_credentials (id, client_id, channel, access_token) "
            "VALUES (%s, %s, 'telegram', %s)",
            (credential_id, client_id, token),
        )
        conn.execute(
            "INSERT INTO webhook_endpoints (id, client_id, public_id, credential_id) "
            "VALUES (%s, %s, %s, %s)",
            (endpoint_id, client_id, f"dev-{uuid.uuid4().hex}", credential_id),
        )
        conn.execute(
            "INSERT INTO staff_notification_targets (client_id, channel, address) "
            "VALUES (%s, 'telegram', '999')",
            (client_id,),
        )
        conn.execute(
            "INSERT INTO contacts (id, client_id, telegram_id, full_name) VALUES (%s, %s, %s, %s)",
            (contact_id, client_id, 501, "Synthetic Contact"),
        )
        conn.execute(
            "INSERT INTO conversations "
            "(id, client_id, contact_id, channel, channel_thread) "
            "VALUES (%s, %s, %s, 'telegram', '501')",
            (conversation_id, client_id, contact_id),
        )

        event_ids: list[uuid.UUID] = []
        for index in range(count):
            trace_id = trace_ids[index]
            idempotency_key = f"telegram:dev-worker-{event_scope}:{index + 1}"
            text = f"message-{index + 1}"
            envelope = Envelope(
                trace_id=trace_id,
                received_at=now + timedelta(microseconds=index),
                channel=Channel.TELEGRAM,
                idempotency_key=idempotency_key,
                client_id=client_id,
                contact=Contact(
                    id=contact_id,
                    external_id="501",
                    channel=Channel.TELEGRAM,
                    display_name="Synthetic Contact",
                ),
                conversation=ConversationRef(
                    id=conversation_id,
                    channel_thread="501",
                    turn_count=0,
                    status="open",
                ),
                message=Message(
                    body_raw=text,
                    body_redacted=text,
                    language="en",
                ),
                meta=Meta(is_first_contact=index == 0),
            )
            event_id = uuid.uuid4()
            event_ids.append(event_id)
            conn.execute(
                "INSERT INTO ingress_events "
                "(id, client_id, endpoint_id, contact_id, idempotency_key, "
                "payload_hash, trace_id, raw, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    event_id,
                    client_id,
                    endpoint_id,
                    contact_id,
                    idempotency_key,
                    hashlib.sha256(text.encode()).hexdigest(),
                    trace_id,
                    Jsonb(
                        {
                            "envelope": envelope.model_dump(mode="json"),
                            "source": {"channel": "telegram", "kind": "message"},
                        }
                    ),
                    now + timedelta(microseconds=index),
                ),
            )
    return owner_dsn, str(client_id), event_ids


async def _open_worker(
    owner_dsn: str,
    app_dsn: str,
) -> tuple[db.AsyncConnectionPool, db.AsyncConnectionPool, IngressWorker]:
    owner_pool = db.build_pool(owner_dsn, max_size=4)
    app_pool = db.build_pool(app_dsn, max_size=4)
    await owner_pool.open(wait=True)
    await app_pool.open(wait=True)
    return (
        owner_pool,
        app_pool,
        IngressWorker(
            owner_pool,
            app_pool,
            lock_conninfo=owner_dsn,
        ),
    )


async def _close_pools(
    owner_pool: db.AsyncConnectionPool,
    app_pool: db.AsyncConnectionPool,
) -> None:
    await app_pool.close()
    await owner_pool.close()


def _skip_pending_deliveries(owner_dsn: str, client_id: str) -> None:
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE outbound_deliveries SET status='skipped' "
            "WHERE client_id = %s AND status='pending'",
            (client_id,),
        )


@requires_docker
async def test_ingress_worker_echoes_once_through_messages_and_outbox(
    pg_container: object,
    app_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    owner_dsn, client_id, event_ids = _seed_events(owner_dsn)
    owner_pool, app_pool, worker = await _open_worker(owner_dsn, app_dsn)
    try:
        assert await worker.process_once() is True
        assert await worker.process_once() is False
        with psycopg.connect(owner_dsn, autocommit=True) as conn:
            conn.execute(
                "UPDATE ingress_events SET status='received', completed_at=NULL, "
                "next_attempt_at=now() WHERE id=%s",
                (event_ids[0],),
            )
        assert await worker.process_once() is True

        with psycopg.connect(owner_dsn) as conn:
            messages = conn.execute(
                "SELECT direction, body FROM messages "
                "WHERE client_id = %s ORDER BY created_at, direction",
                (client_id,),
            ).fetchall()
            delivery = conn.execute(
                "SELECT status, attempts FROM outbound_deliveries WHERE client_id = %s",
                (client_id,),
            ).fetchone()
            event = conn.execute(
                "SELECT status FROM ingress_events WHERE id = %s",
                (event_ids[0],),
            ).fetchone()
            conversation = conn.execute(
                "SELECT turn_count FROM conversations WHERE client_id = %s",
                (client_id,),
            ).fetchone()

        assert messages == [("inbound", "message-1"), ("outbound", "Echo: message-1")]
        assert delivery == ("pending", 0)
        assert event == ("completed",)
        assert conversation == (1,)
    finally:
        await _close_pools(owner_pool, app_pool)
        _skip_pending_deliveries(owner_dsn, client_id)
        settings.get_settings.cache_clear()


@requires_docker
async def test_ingress_worker_serializes_same_conversation_in_order(
    pg_container: object,
    app_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    owner_dsn, client_id, _ = _seed_events(owner_dsn, count=2)
    owner_pool, app_pool, _ = await _open_worker(owner_dsn, app_dsn)
    processing_started = asyncio.Event()

    class SlowWorker(IngressWorker):
        async def _complete_turn(self, item: ClaimedIngress, envelope: Envelope) -> None:
            processing_started.set()
            await asyncio.sleep(0.05)
            await super()._complete_turn(item, envelope)

    worker = SlowWorker(owner_pool, app_pool, lock_conninfo=owner_dsn)
    try:
        first = asyncio.create_task(worker.process_once())
        await asyncio.wait_for(processing_started.wait(), timeout=2)
        assert await worker.process_once() is False
        assert await first is True
        assert await worker.process_once() is True

        with psycopg.connect(owner_dsn) as conn:
            inbound = conn.execute(
                "SELECT body FROM messages "
                "WHERE client_id = %s AND direction = 'inbound' ORDER BY created_at",
                (client_id,),
            ).fetchall()
        assert inbound == [("message-1",), ("message-2",)]
    finally:
        await _close_pools(owner_pool, app_pool)
        _skip_pending_deliveries(owner_dsn, client_id)
        settings.get_settings.cache_clear()


@requires_docker
async def test_ingress_reaper_requeues_and_dead_letters_exhausted_turns(
    pg_container: object,
    app_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    owner_dsn, client_id, event_ids = _seed_events(owner_dsn, count=2)
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE ingress_events SET status='dispatched', attempts=%s, "
            "dispatched_at=now()-interval '10 minutes' WHERE id=%s",
            (MAX_INGRESS_ATTEMPTS, event_ids[0]),
        )
        conn.execute(
            "UPDATE ingress_events SET status='dispatched', attempts=2, "
            "dispatched_at=now()-interval '10 minutes' WHERE id=%s",
            (event_ids[1],),
        )
    owner_pool, app_pool, worker = await _open_worker(owner_dsn, app_dsn)
    try:
        assert await worker.reap() == 2
        with psycopg.connect(owner_dsn) as conn:
            statuses = conn.execute(
                "SELECT id, status FROM ingress_events WHERE id = ANY(%s)",
                (event_ids,),
            ).fetchall()
            dlq = conn.execute(
                "SELECT stage, envelope FROM dlq WHERE client_id = %s",
                (client_id,),
            ).fetchone()
        assert sorted(status for _, status in statuses) == ["dead", "received"]
        assert dlq is not None
        assert dlq[0] == "turn"
        assert dlq[1]["envelope"]["message"]["body_redacted"] == "message-1"
        notifications = _notification_rows(owner_dsn, client_id)
        assert len(notifications) == 1
        assert notifications[0][0] == "dlq"
        assert notifications[0][1]["stage"] == "turn"
        assert notifications[0][1]["reason"] == "maximum ingress attempts exceeded"
    finally:
        await _close_pools(owner_pool, app_pool)
        with psycopg.connect(owner_dsn, autocommit=True) as conn:
            conn.execute(
                "UPDATE ingress_events SET status='dead' "
                "WHERE client_id = %s AND status='received'",
                (client_id,),
            )
        settings.get_settings.cache_clear()


@requires_docker
async def test_delivery_worker_sends_telegram_and_records_attempt(
    pg_container: object,
    app_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    owner_dsn, client_id, _ = _seed_events(owner_dsn)
    owner_pool, app_pool, ingress_worker = await _open_worker(owner_dsn, app_dsn)
    try:
        assert await ingress_worker.process_once() is True
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 82}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            delivery_worker = DeliveryWorker(owner_pool, app_pool, client)
            assert await delivery_worker.process_once() is True
            restarted_worker = DeliveryWorker(owner_pool, app_pool, client)
            assert await restarted_worker.reap() == 0
            assert await restarted_worker.process_once() is False

        assert len(requests) == 1
        assert requests[0].url.path.endswith("/sendMessage")
        assert json.loads(requests[0].content) == {
            "chat_id": "501",
            "text": "Echo: message-1",
        }
        with psycopg.connect(owner_dsn) as conn:
            delivery = conn.execute(
                "SELECT status, attempts, last_status_code FROM outbound_deliveries "
                "WHERE client_id = %s",
                (client_id,),
            ).fetchone()
            attempt_count = conn.execute(
                "SELECT count(*) FROM outbound_delivery_attempts attempt "
                "JOIN outbound_deliveries delivery ON delivery.id=attempt.delivery_id "
                "WHERE delivery.client_id = %s",
                (client_id,),
            ).fetchone()
            all_deliveries = conn.execute(
                "SELECT client_id, status, attempts, last_status_code, last_error "
                "FROM outbound_deliveries"
            ).fetchall()
        assert delivery == ("delivered", 1, 200), all_deliveries
        assert attempt_count == (1,)
    finally:
        await _close_pools(owner_pool, app_pool)
        settings.get_settings.cache_clear()


@requires_docker
async def test_delivery_worker_honors_telegram_429_retry_after(
    pg_container: object,
    app_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    owner_dsn, client_id, _ = _seed_events(owner_dsn)
    owner_pool, app_pool, ingress_worker = await _open_worker(owner_dsn, app_dsn)
    try:
        assert await ingress_worker.process_once() is True

        def respond(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                json={
                    "ok": False,
                    "error_code": 429,
                    "description": "Too Many Requests",
                    "parameters": {"retry_after": 120},
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            delivery_worker = DeliveryWorker(owner_pool, app_pool, client)
            assert await delivery_worker.process_once() is True
            assert await delivery_worker.process_once() is False

        with psycopg.connect(owner_dsn) as conn:
            row = conn.execute(
                "SELECT status, attempts, last_status_code, "
                "extract(epoch FROM next_attempt_at - now()) "
                "FROM outbound_deliveries WHERE client_id=%s",
                (client_id,),
            ).fetchone()
        assert row is not None
        assert row[:3] == ("pending", 1, 429)
        assert 100 <= float(row[3]) <= 121
    finally:
        await _close_pools(owner_pool, app_pool)
        settings.get_settings.cache_clear()


@requires_docker
async def test_delivery_worker_retries_and_dead_letters_after_max_attempts(
    pg_container: object,
    app_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    owner_dsn, client_id, _ = _seed_events(owner_dsn)
    owner_pool, app_pool, ingress_worker = await _open_worker(owner_dsn, app_dsn)
    try:
        assert await ingress_worker.process_once() is True

        def respond(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"ok": False, "error_code": 503})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            delivery_worker = DeliveryWorker(owner_pool, app_pool, client)

            async def no_throttle(_chat_id: str) -> None:
                return None

            monkeypatch.setattr(delivery_worker, "_throttle", no_throttle)
            for attempt in range(5):
                if attempt:
                    with psycopg.connect(owner_dsn, autocommit=True) as conn:
                        conn.execute(
                            "UPDATE outbound_deliveries SET next_attempt_at=now() "
                            "WHERE client_id=%s AND status='pending'",
                            (client_id,),
                        )
                assert await delivery_worker.process_once() is True

        with psycopg.connect(owner_dsn) as conn:
            delivery = conn.execute(
                "SELECT status, attempts FROM outbound_deliveries WHERE client_id=%s",
                (client_id,),
            ).fetchone()
            attempt_count = conn.execute(
                "SELECT count(*) FROM outbound_delivery_attempts attempt "
                "JOIN outbound_deliveries delivery ON delivery.id=attempt.delivery_id "
                "WHERE delivery.client_id=%s",
                (client_id,),
            ).fetchone()
            dlq = conn.execute(
                "SELECT stage, envelope FROM dlq WHERE client_id=%s",
                (client_id,),
            ).fetchone()
        assert delivery == ("dead", 5)
        assert attempt_count == (5,)
        assert dlq is not None
        assert dlq[0] == "delivery"
        assert dlq[1]["body"] == "Echo: message-1"
        notifications = _notification_rows(owner_dsn, client_id)
        assert len(notifications) == 1
        assert notifications[0][0] == "dlq"
        assert notifications[0][1]["stage"] == "delivery"
        assert notifications[0][1]["reason"] == "telegram_http_503"
    finally:
        await _close_pools(owner_pool, app_pool)
        settings.get_settings.cache_clear()


@requires_docker
async def test_delivery_worker_crash_mid_send_recovers_and_delivers(
    pg_container: object,
    app_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    owner_dsn, client_id, _ = _seed_events(owner_dsn)
    owner_pool, app_pool, ingress_worker = await _open_worker(owner_dsn, app_dsn)
    try:
        assert await ingress_worker.process_once() is True
        sent_requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            sent_requests.append(request)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})

        class SimulatedCrash(BaseException):
            pass

        original_send_message = TelegramAdapter.send_message

        async def crash_after_send(*args: Any, **kwargs: Any) -> Any:
            await original_send_message(*args, **kwargs)
            raise SimulatedCrash("worker process killed mid-send")

        monkeypatch.setattr(TelegramAdapter, "send_message", crash_after_send)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            delivery_worker = DeliveryWorker(owner_pool, app_pool, client)
            with pytest.raises(SimulatedCrash):
                await delivery_worker.process_once()

        assert len(sent_requests) == 1
        with psycopg.connect(owner_dsn) as conn:
            row = conn.execute(
                "SELECT status, attempts, locked_at FROM outbound_deliveries WHERE client_id = %s",
                (client_id,),
            ).fetchone()
            assert row is not None
            assert row[0] == "delivering"
            assert row[1] == 1
            assert row[2] is not None

            # Fast-forward stale lease time so the reaper can reclaim it
            conn.execute(
                "UPDATE outbound_deliveries SET locked_at = now() - interval '200 seconds' "
                "WHERE client_id = %s",
                (client_id,),
            )

        # Restore normal send_message for restarted worker
        monkeypatch.setattr(TelegramAdapter, "send_message", original_send_message)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            restarted_worker = DeliveryWorker(owner_pool, app_pool, client)
            reaped = await restarted_worker.reap()
            assert reaped == 1

            # Make next attempt immediately due
            with psycopg.connect(owner_dsn) as conn:
                conn.execute(
                    "UPDATE outbound_deliveries SET next_attempt_at = now() - interval '1 second' "
                    "WHERE client_id = %s",
                    (client_id,),
                )

            # Process again with restarted worker
            assert await restarted_worker.process_once() is True

        assert len(sent_requests) == 2
        assert sent_requests[0].content == sent_requests[1].content
        with psycopg.connect(owner_dsn) as conn:
            delivery = conn.execute(
                "SELECT status, attempts, last_status_code, last_error FROM outbound_deliveries "
                "WHERE client_id = %s",
                (client_id,),
            ).fetchone()
            assert delivery == ("delivered", 2, 200, None)

            attempts = conn.execute(
                "SELECT attempt_no, status_code, error FROM outbound_delivery_attempts oda "
                "JOIN outbound_deliveries od ON od.id = oda.delivery_id "
                "WHERE od.client_id = %s ORDER BY attempt_no",
                (client_id,),
            ).fetchall()
            assert attempts == [
                (1, None, "delivery lease expired"),
                (2, 200, None),
            ]
    finally:
        await _close_pools(owner_pool, app_pool)
        settings.get_settings.cache_clear()


def _notification_rows(owner_dsn: str, client_id: str) -> list[tuple[str, dict[str, Any]]]:
    with psycopg.connect(owner_dsn) as conn:
        return conn.execute(
            "SELECT event, payload FROM staff_notifications WHERE client_id=%s",
            (client_id,),
        ).fetchall()
