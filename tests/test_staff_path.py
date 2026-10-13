"""LG05 staff notification sender and staff callback path against real Postgres."""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any

import httpx
import psycopg
import pytest
from csa.core import crypto, settings
from csa.notify.sender import StaffNotificationSender
from csa.staff.callbacks import handle_staff_action

from tests.conftest import owner_dsn_for, requires_docker
from tests.test_order_agent import World, _pending_order, _seed_target, world  # noqa: F401

pytestmark = [requires_docker, pytest.mark.asyncio]

_KEY = "base64:" + base64.b64encode(b"\x07" * 32).decode("ascii")


@pytest.fixture(autouse=True)
def _crypto_key(
    monkeypatch: pytest.MonkeyPatch, migrated_dsn: str, app_dsn: str, pg_container: object
) -> None:
    for name, value in {
        "ENV": "ci",
        "DATABASE_URL": app_dsn,
        "LG_DATABASE_URL": owner_dsn_for(pg_container),
        "GROQ_API_KEY": "test-key",
        "PUBLIC_BASE_URL": "https://example.test",
        "CREDENTIALS_KEY": _KEY,
    }.items():
        monkeypatch.setenv(name, value)
    settings.get_settings.cache_clear()


def _quiesce_other_tenants(world: World) -> None:  # noqa: F811
    """The shared test DB may hold other tests' queued rows; the sender claims globally."""
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE staff_notifications SET status='dead' WHERE client_id <> %s",
            (world.client_id,),
        )


def _seed_endpoint(world: World) -> uuid.UUID:  # noqa: F811
    _quiesce_other_tenants(world)
    credential_id = uuid.uuid4()
    endpoint_id = uuid.uuid4()
    token = crypto.encrypt(
        "12345:dev-test-token",
        crypto.credential_aad("channel_credentials", "access_token", str(credential_id)),
    )
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO channel_credentials (id, client_id, channel, access_token) "
            "VALUES (%s,%s,'telegram',%s)",
            (credential_id, world.client_id, token),
        )
        conn.execute(
            "INSERT INTO webhook_endpoints (id, client_id, public_id, credential_id) "
            "VALUES (%s,%s,%s,%s)",
            (endpoint_id, world.client_id, uuid.uuid4().hex, credential_id),
        )
    return endpoint_id


def _sender(
    world: World,  # noqa: F811
    owner_pool: Any,
    sent: list[dict[str, Any]],
    status: int = 200,
) -> StaffNotificationSender:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        if status != 200:
            return httpx.Response(status, json={"ok": False})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return StaffNotificationSender(owner_pool, world.pool, client)


@pytest.fixture
async def owner_pool(world: World) -> Any:  # noqa: F811
    from csa.core import db

    pool = db.build_pool(world.owner_dsn, max_size=2)
    await pool.open(wait=True)
    yield pool
    await pool.close()


def _tap_ids(sent: list[dict[str, Any]]) -> dict[str, str]:
    keyboard = sent[0]["reply_markup"]["inline_keyboard"][0]
    return {b["text"]: b["callback_data"] for b in keyboard}


async def _staff_flow(world: World, owner_pool: Any) -> tuple[uuid.UUID, str, dict[str, str], list]:  # noqa: F811
    endpoint_id = _seed_endpoint(world)
    _seed_target(world)
    order_id = await _pending_order(world, "sp1", "A1", 2)
    sent: list[dict[str, Any]] = []
    sender = _sender(world, owner_pool, sent)
    assert await sender.process_once()
    return endpoint_id, order_id, _tap_ids(sent), sent


async def test_sender_delivers_approval_with_buttons(world: World, owner_pool: Any) -> None:  # noqa: F811
    _, order_id, buttons, sent = await _staff_flow(world, owner_pool)
    assert sent[0]["chat_id"] == "999"
    assert order_id in sent[0]["text"]
    assert set(buttons) == {"Approve", "Reject"}
    assert all(v.startswith("s:") and len(v) <= 64 for v in buttons.values())
    assert world.q(
        "SELECT status FROM staff_notifications WHERE client_id=%s", world.client_id
    ) == [("delivered",)]


async def test_staff_approve_tap_confirms_and_queues_customer_message(
    world: World,  # noqa: F811
    owner_pool: Any,
) -> None:
    endpoint_id, order_id, buttons, _ = await _staff_flow(world, owner_pool)
    action_id = uuid.UUID(buttons["Approve"][2:])
    answer = await handle_staff_action(
        world.pool,
        client_id=world.client_id,
        endpoint_id=endpoint_id,
        chat_id="999",
        staff_action_id=action_id,
        trace_id=uuid.uuid4(),
    )
    assert answer == "Approved."
    assert world.q("SELECT status FROM orders WHERE id=%s", order_id)[0][0] == "confirmed"
    again = await handle_staff_action(
        world.pool,
        client_id=world.client_id,
        endpoint_id=endpoint_id,
        chat_id="999",
        staff_action_id=uuid.UUID(buttons["Reject"][2:]),
        trace_id=uuid.uuid4(),
    )
    assert again == "Already handled."
    assert world.q("SELECT status FROM orders WHERE id=%s", order_id)[0][0] == "confirmed"
    rows = world.q(
        "SELECT count(*) FROM messages m JOIN outbound_deliveries d ON d.message_id = m.id "
        "WHERE m.client_id=%s AND m.idempotency_key LIKE 'staff:%%'",
        world.client_id,
    )
    assert rows[0][0] == 1


async def test_staff_tap_from_wrong_chat_is_refused(world: World, owner_pool: Any) -> None:  # noqa: F811
    endpoint_id, order_id, buttons, _ = await _staff_flow(world, owner_pool)
    answer = await handle_staff_action(
        world.pool,
        client_id=world.client_id,
        endpoint_id=endpoint_id,
        chat_id="12345",
        staff_action_id=uuid.UUID(buttons["Approve"][2:]),
        trace_id=uuid.uuid4(),
    )
    assert answer == "You are not authorised for this action."
    assert (
        world.q("SELECT status FROM orders WHERE id=%s", order_id)[0][0] == "pending_confirmation"
    )


async def test_sender_retries_then_dead_letters(world: World, owner_pool: Any) -> None:  # noqa: F811
    _seed_endpoint(world)
    _seed_target(world)
    await _pending_order(world, "sp2", "B2", 1)
    sent: list[dict[str, Any]] = []
    sender = _sender(world, owner_pool, sent, status=500)
    for attempt in range(1, 6):
        assert await sender.process_once()
        status = world.q(
            "SELECT status, attempts FROM staff_notifications WHERE client_id=%s", world.client_id
        )[0]
        if attempt < 5:
            assert status == ("pending", attempt)
            with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
                conn.execute(
                    "UPDATE staff_notifications SET next_attempt_at = now() WHERE client_id=%s",
                    (world.client_id,),
                )
    assert world.q(
        "SELECT status, attempts, last_error FROM staff_notifications WHERE client_id=%s",
        world.client_id,
    )[0] == (
        "dead",
        5,
        "telegram_http_500",
    )


async def test_escalation_alert_has_resume_button_that_releases_the_ai(
    world: World,  # noqa: F811
    owner_pool: Any,
) -> None:
    from csa.agents.base import AgentContext
    from csa.agents.escalation.agent import EscalationAgent, EscalationInput
    from csa.contracts.callbacks import PendingActionStore

    endpoint_id = _seed_endpoint(world)
    _seed_target(world)
    trace_id = uuid.uuid4()
    ctx = AgentContext(
        client_id=world.client_id,
        contact_id=world.contact_id,
        trace_id=trace_id,
        conversation_id=world.conversation_id,
        idempotency_key="esc-1",
        channel="telegram",
        pool=world.pool,
        pending_actions=PendingActionStore(world.pool),
    )
    result = await EscalationAgent().run(ctx, EscalationInput(reason="agent_failure"))
    assert result.ok
    assert world.q(
        "SELECT status, ai_muted_until IS NOT NULL FROM conversations WHERE id=%s",
        world.conversation_id,
    ) == [("escalated", True)]

    sent: list[dict[str, Any]] = []
    assert await _sender(world, owner_pool, sent).process_once()
    keyboard = sent[0]["reply_markup"]["inline_keyboard"][0]
    assert [b["text"] for b in keyboard] == ["Resume AI"]
    action_id = uuid.UUID(keyboard[0]["callback_data"][2:])

    async def tap(chat: str) -> str:
        return await handle_staff_action(
            world.pool,
            client_id=world.client_id,
            endpoint_id=endpoint_id,
            chat_id=chat,
            staff_action_id=action_id,
            trace_id=uuid.uuid4(),
        )

    assert await tap("12345") == "You are not authorised for this action."
    assert await tap("999") == "AI resumed."
    assert world.q(
        "SELECT status, owned_by, ai_muted_until FROM conversations WHERE id=%s",
        world.conversation_id,
    ) == [("open", "ai", None)]
    assert await tap("999") == "Already handled."


async def _hold(world: World, endpoint_id: uuid.UUID, ttl_minutes: int = 30) -> int:  # noqa: F811
    from datetime import timedelta

    from csa.approval.service import hold_reply
    from csa.core.tenancy import tx

    trace_id = uuid.uuid4()
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO ingress_events (client_id, endpoint_id, idempotency_key, payload_hash, "
            "trace_id) VALUES (%s,%s,%s,'h',%s)",
            (world.client_id, endpoint_id, f"k-{trace_id}", trace_id),
        )
    async with tx(world.pool, str(world.client_id)) as conn:
        return await hold_reply(
            conn,
            client_id=world.client_id,
            conversation_id=world.conversation_id,
            trace_id=trace_id,
            draft="Your refund is on the way.",
            reason="never_auto:refund_returns",
            risk=0.1,
            ui={},
            ttl=timedelta(minutes=ttl_minutes),
        )


async def _held_buttons(world: World, owner_pool: Any) -> tuple[int, uuid.UUID, dict[str, str]]:  # noqa: F811
    endpoint_id = _seed_endpoint(world)
    _seed_target(world)
    approval_id = await _hold(world, endpoint_id)
    sent: list[dict[str, Any]] = []
    assert await _sender(world, owner_pool, sent).process_once()
    assert "AI reply needs approval" in sent[0]["text"]
    assert "Your refund is on the way." in sent[0]["text"]
    return approval_id, endpoint_id, _tap_ids(sent)


async def _tap_reply(world: World, endpoint_id: uuid.UUID, callback: str, chat: str = "999") -> str:  # noqa: F811
    return await handle_staff_action(
        world.pool,
        client_id=world.client_id,
        endpoint_id=endpoint_id,
        chat_id=chat,
        staff_action_id=uuid.UUID(callback[2:]),
        trace_id=uuid.uuid4(),
    )


async def test_held_reply_is_sent_once_when_staff_approves(world: World, owner_pool: Any) -> None:  # noqa: F811
    approval_id, endpoint_id, buttons = await _held_buttons(world, owner_pool)
    assert set(buttons) == {"Send reply", "Reject"}
    assert world.q("SELECT count(*) FROM messages WHERE client_id=%s", world.client_id)[0][0] == 0

    assert await _tap_reply(world, endpoint_id, buttons["Reject"], "12345") == (
        "You are not authorised for this action."
    )
    assert await _tap_reply(world, endpoint_id, buttons["Send reply"]) == "Approved. Reply sent."
    assert world.q("SELECT body, direction FROM messages WHERE client_id=%s", world.client_id) == [
        ("Your refund is on the way.", "outbound")
    ]
    assert world.q(
        "SELECT count(*) FROM outbound_deliveries WHERE client_id=%s", world.client_id
    ) == [(1,)]
    assert world.q(
        "SELECT status, decided_by, final_text FROM approvals WHERE id=%s", approval_id
    ) == [("approved", "staff_chat:999", "Your refund is on the way.")]
    assert world.q("SELECT action FROM approval_audit WHERE approval_id=%s", approval_id) == [
        ("approved",)
    ]

    assert await _tap_reply(world, endpoint_id, buttons["Send reply"]) == "Already handled."
    assert await _tap_reply(world, endpoint_id, buttons["Reject"]) == "Already handled."
    assert world.q("SELECT count(*) FROM messages WHERE client_id=%s", world.client_id) == [(1,)]


async def test_rejected_reply_sends_only_the_safe_fallback(world: World, owner_pool: Any) -> None:  # noqa: F811
    approval_id, endpoint_id, buttons = await _held_buttons(world, owner_pool)
    assert (await _tap_reply(world, endpoint_id, buttons["Reject"])).startswith("Rejected.")
    assert world.q("SELECT body FROM messages WHERE client_id=%s", world.client_id) == [
        ("Thanks for your message. A team member will follow up with you shortly.",)
    ]
    assert world.q("SELECT status FROM approvals WHERE id=%s", approval_id) == [("rejected",)]


async def test_expired_approvals_are_settled_by_the_sweeper(world: World, owner_pool: Any) -> None:  # noqa: F811
    from csa.approval.service import expire_due

    approval_id, endpoint_id, buttons = await _held_buttons(world, owner_pool)
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE approvals SET expires_at = now() - interval '1 minute' WHERE id=%s",
            (approval_id,),
        )
    assert await expire_due(owner_pool, world.pool) == 1
    assert await expire_due(owner_pool, world.pool) == 0
    assert world.q("SELECT status, decided_by FROM approvals WHERE id=%s", approval_id) == [
        ("expired", "system")
    ]
    assert world.q("SELECT count(*) FROM messages WHERE client_id=%s", world.client_id) == [(1,)]
    assert await _tap_reply(world, endpoint_id, buttons["Send reply"]) == "Already handled."


async def test_open_circuit_queues_one_staff_alert(world: World) -> None:  # noqa: F811
    import time

    from csa.agents.recorder import PostgresBreakerStore

    _seed_target(world, events="{circuit_opened}")
    store = PostgresBreakerStore(world.pool)
    opened_at = time.monotonic()
    await store.save(world.client_id, "order_agent", "open", 5, opened_at)
    await store.save(world.client_id, "order_agent", "open", 5, opened_at)

    assert world.q(
        "SELECT event, priority, payload FROM staff_notifications WHERE client_id=%s",
        world.client_id,
    ) == [("circuit_opened", "high", {"agent": "order_agent"})]
