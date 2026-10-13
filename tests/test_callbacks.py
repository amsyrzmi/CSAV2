"""Tenant-scoped callback codec integration tests (LG02 §4)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import psycopg
import pytest
from csa.contracts.callbacks import (
    CallbackCodec,
    PendingActionContextError,
    PendingActionStore,
)
from csa.core import db

from tests.conftest import owner_dsn_for, requires_docker


@pytest.fixture()
async def callback_store(migrated_dsn: str, pg_container: object, app_dsn: str):
    client_id = str(uuid.uuid4())
    contact_id = str(uuid.uuid4())
    conversation_id = str(uuid.uuid4())
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clients (id, slug, name, platform_version) VALUES (%s, %s, %s, %s)",
            (client_id, f"callback-{client_id}", "callback-test", "lg02-test"),
        )
        conn.execute(
            "INSERT INTO contacts (id, client_id) VALUES (%s, %s)", (contact_id, client_id)
        )
        conn.execute(
            "INSERT INTO conversations (id, client_id, contact_id, channel, channel_thread) "
            "VALUES (%s, %s, %s, 'telegram', %s)",
            (conversation_id, client_id, contact_id, f"thread-{conversation_id}"),
        )
    pool = db.build_pool(app_dsn, max_size=4)
    await pool.open(wait=True)
    try:
        yield PendingActionStore(pool), client_id, contact_id, conversation_id
    finally:
        await pool.close()


def test_callback_codec_uses_compact_uuid_format() -> None:
    action_id = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    callback_data = CallbackCodec.encode(action_id)

    assert callback_data == "a:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert len(callback_data.encode("utf-8")) == 38
    assert CallbackCodec.decode(callback_data) == action_id
    assert CallbackCodec.decode("ord:confirm:too-long") is None


@requires_docker
async def test_issue_resolve_and_consume_are_single_use(callback_store) -> None:
    store, client_id, contact_id, conversation_id = callback_store
    callback_data = await store.issue(
        client_id=client_id,
        conversation_id=conversation_id,
        contact_id=contact_id,
        agent="order_agent",
        action="confirm",
        payload={"order_id": "ORD-A-1001"},
        ttl=timedelta(minutes=5),
    )
    assert len(callback_data.encode("utf-8")) == 38

    pending = await store.resolve(callback_data, client_id=client_id, contact_id=contact_id)
    assert pending is not None
    assert pending.action == "confirm"
    assert pending.payload == {"order_id": "ORD-A-1001"}

    consumed = await store.consume(callback_data, client_id=client_id, contact_id=contact_id)
    assert consumed is not None
    assert consumed.status == "used"
    assert await store.resolve(callback_data, client_id=client_id, contact_id=contact_id) is None
    assert await store.consume(callback_data, client_id=client_id, contact_id=contact_id) is None


@requires_docker
async def test_wrong_contact_cannot_resolve_or_consume(callback_store) -> None:
    store, client_id, contact_id, conversation_id = callback_store
    callback_data = await store.issue(
        client_id=client_id,
        conversation_id=conversation_id,
        contact_id=contact_id,
        agent="order_agent",
        action="confirm",
        payload={},
        ttl=timedelta(minutes=5),
    )

    assert (
        await store.resolve(callback_data, client_id=client_id, contact_id=str(uuid.uuid4()))
        is None
    )
    assert (
        await store.consume(callback_data, client_id=client_id, contact_id=str(uuid.uuid4()))
        is None
    )
    assert await store.resolve(callback_data, client_id=client_id, contact_id=contact_id)


@requires_docker
async def test_issue_rejects_mismatched_tenant_context(callback_store) -> None:
    store, client_id, _, conversation_id = callback_store
    with pytest.raises(PendingActionContextError):
        await store.issue(
            client_id=client_id,
            conversation_id=conversation_id,
            contact_id=str(uuid.uuid4()),
            agent="order_agent",
            action="confirm",
            payload={},
            ttl=timedelta(minutes=5),
        )


@requires_docker
async def test_expired_action_resolves_to_none_and_is_marked_expired(
    callback_store, migrated_dsn: str
) -> None:
    store, client_id, contact_id, conversation_id = callback_store
    callback_data = await store.issue(
        client_id=client_id,
        conversation_id=conversation_id,
        contact_id=contact_id,
        agent="order_agent",
        action="confirm",
        payload={},
        ttl=timedelta(seconds=1),
    )
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        action_id = CallbackCodec.decode(callback_data)
        assert action_id is not None
        conn.execute(
            "UPDATE pending_actions SET expires_at = now() - interval '1 second' WHERE id = %s",
            (action_id,),
        )

    assert await store.resolve(callback_data, client_id=client_id, contact_id=contact_id) is None
    assert await store.consume(callback_data, client_id=client_id, contact_id=contact_id) is None
    action_id = CallbackCodec.decode(callback_data)
    assert action_id is not None
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        status = conn.execute(
            "SELECT status FROM pending_actions WHERE id = %s", (action_id,)
        ).fetchone()
    assert status == ("expired",)


@requires_docker
async def test_concurrent_consumers_only_consume_once(callback_store) -> None:
    store, client_id, contact_id, conversation_id = callback_store
    callback_data = await store.issue(
        client_id=client_id,
        conversation_id=conversation_id,
        contact_id=contact_id,
        agent="order_agent",
        action="confirm",
        payload={},
        ttl=timedelta(minutes=5),
    )

    results = await asyncio.gather(
        store.consume(callback_data, client_id=client_id, contact_id=contact_id),
        store.consume(callback_data, client_id=client_id, contact_id=contact_id),
    )
    assert sum(result is not None for result in results) == 1
