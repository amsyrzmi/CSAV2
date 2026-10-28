"""M5 authority, atomic transitions, and takeover races on disposable synthetic data."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import urlencode
from uuid import UUID, uuid4

import httpx
import pytest
from apps.delivery.main import DeliveryWorker
from csa.agents.base import AgentContext
from csa.agents.escalation.agent import EscalationAgent, EscalationInput
from csa.approval.service import decide_reply, hold_reply
from csa.contracts.callbacks import PendingActionStore
from csa.contracts.envelope import Channel, Contact, ConversationRef, Envelope, Message, Meta
from csa.contracts.state import OutboundMessage
from csa.core import db
from csa.core.tenancy import tx
from csa.turn.deps import PostgresTurnDependencies, _insert_outbound

from tests.conftest import requires_docker
from tests.test_console_services import activate
from tests.test_console_sessions import MACHINE_TOKEN, SessionWorld

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]


@pytest.fixture(autouse=True)
async def packages(sessions: SessionWorld) -> None:
    """Both tenants hold customer_support so isolation checks get past the package gate."""
    for tenant in sessions.tenants:
        await activate(sessions, tenant, "customer_support")


async def test_pagination_validation_redaction_and_mutation_role_denials(
    sessions: SessionWorld,
) -> None:
    w = sessions
    env, conversation = await seed(w)
    await seed(w)
    first = (await w.admin.get(path(w, "/conversations") + "&limit=1")).json()
    second = (
        await w.admin.get(path(w, "/conversations") + f"&limit=1&before={first['next_cursor']}")
    ).json()
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert second["next_cursor"] is None
    await w.q(
        "UPDATE messages SET body='Email synthetic@example.test',redacted_body=NULL "
        "WHERE trace_id=%s",
        env.trace_id,
    )
    messages = await w.admin.get(path(w, f"/conversations/{conversation}/messages"))
    assert "synthetic@example.test" not in messages.text and "[EMAIL_0]" in messages.text
    approval = await held(w, env)
    reader, _ = await w.member(role="read_only")
    foreign, _ = await w.member(tenant=w.tenants[1])
    for suffix in ("takeover", "release", "reply"):
        payload = {"request_key": str(uuid4()), "expected_version": 0}
        if suffix == "reply":
            payload["body"] = "Synthetic"
        url = f"/console/v1/inbox/conversations/{conversation}/{suffix}"
        assert (await w.mutate(reader, url, payload)).status_code == 403
        assert (await w.mutate(foreign, url, payload)).status_code == 404
    url = f"/console/v1/inbox/approvals/{approval}/decide"
    assert (
        await w.mutate(reader, url, {"request_key": str(uuid4()), "decision": "approve"})
    ).status_code == 403
    assert (
        await w.mutate(foreign, url, {"request_key": str(uuid4()), "decision": "approve"})
    ).status_code == 404
    url = path(w, f"/conversations/{conversation}/takeover")
    oversized = await w.admin.post(url, content=b" " * 32769, headers=w.headers(w.admin))
    assert oversized.status_code == 413
    assert (
        await w.mutate(
            w.admin, url, {"request_key": str(uuid4()), "expected_version": 0, "actor": "invented"}
        )
    ).status_code == 422
    assert (await takeover(w, conversation)).status_code == 200
    for text in ("  ", "/command", "bad" + chr(0) + "text", "x" * 2001):
        assert (
            await w.mutate(
                w.admin,
                path(w, f"/conversations/{conversation}/reply"),
                {"request_key": str(uuid4()), "expected_version": 1, "body": text},
            )
        ).status_code == 422
    [receipt] = await w.q(
        "SELECT request_key FROM console_inbox_actions WHERE client_id=%s AND actor_id=%s",
        w.tenants[0],
        w.admin_id,
    )
    async with tx(w.pool, w.tenants[1]) as conn:
        assert not await db.q.fetch_all(
            "inbox/get_action.sql", (w.tenants[0], w.admin_id, receipt["request_key"]), conn=conn
        )


async def test_commit_ack_loss_replays_the_committed_human_reply(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = sessions
    _, conversation = await seed(w)
    assert (await takeover(w, conversation)).status_code == 200
    import csa.console.inbox as inbox

    original_tx = inbox.tx

    @asynccontextmanager
    async def uncertain(*args: Any, **kwargs: Any):
        async with original_tx(*args, **kwargs) as conn:
            yield conn
        raise RuntimeError("synthetic-commit-response-loss")

    url = path(w, f"/conversations/{conversation}/reply")
    payload = {"request_key": str(uuid4()), "expected_version": 1, "body": "Durable human reply"}
    with monkeypatch.context() as patch:
        patch.setattr(inbox, "tx", uncertain)
        with pytest.raises(RuntimeError, match="synthetic-commit-response-loss"):
            await w.mutate(w.admin, url, payload)
    replayed = await w.mutate(w.admin, url, payload)
    assert replayed.status_code == 202 and replayed.json()["queued"]
    rows = await w.q(
        "SELECT m.id FROM messages m JOIN outbound_deliveries d ON d.message_id=m.id "
        "WHERE m.conversation_id=%s AND m.send_authority='human'",
        conversation,
    )
    assert len(rows) == 1 and str(rows[0]["id"]) == replayed.json()["message_id"]
    assert (
        len(
            await w.q(
                "SELECT id FROM console_audit WHERE client_id=%s AND action='inbox.reply'",
                w.tenants[0],
            )
        )
        == 1
    )


async def test_expiry_after_takeover_does_not_resume_a_stale_reply(sessions: SessionWorld) -> None:
    w = sessions
    env, conversation = await seed(w)
    approval = await held(w, env)
    assert (await takeover(w, conversation)).status_code == 200
    await w.q("UPDATE approvals SET expires_at=now()-interval '1 second' WHERE id=%s", approval)
    result = await w.mutate(
        w.admin,
        path(w, f"/approvals/{approval}/decide"),
        {"request_key": str(uuid4()), "decision": "approve"},
    )
    assert result.json()["outcome"] == "expired" and not result.json()["queued"]
    assert not await w.q("SELECT id FROM outbound_deliveries WHERE trace_id=%s", env.trace_id)


async def test_takeover_replaces_temporary_escalation_with_indefinite_ownership(
    sessions: SessionWorld,
) -> None:
    w = sessions
    _, conversation = await seed(w)
    await w.q(
        "UPDATE conversations SET owned_by='human_support', status='escalated', "
        "ai_muted_until=now()+interval '1 hour' WHERE id=%s",
        conversation,
    )
    response = await takeover(w, conversation)
    assert response.status_code == 200, response.text
    current = response.json()["conversation"]
    assert response.json()["outcome"] == "applied"
    assert current["human_muted"] and current["ai_muted_until"] is None
    assert current["inbox_version"] == 1


@pytest.mark.parametrize("release", [False, True])
async def test_late_escalation_cannot_overwrite_staff_ownership(
    sessions: SessionWorld,
    release: bool,
) -> None:
    w = sessions
    envelope, conversation = await seed(w)
    assert envelope.contact.id is not None
    assert (await takeover(w, conversation)).status_code == 200
    if release:
        response = await w.mutate(
            w.admin,
            path(w, f"/conversations/{conversation}/release"),
            {"request_key": str(uuid4()), "expected_version": 1},
        )
        assert response.status_code == 200
    ctx = AgentContext(
        client_id=envelope.client_id,
        contact_id=envelope.contact.id,
        trace_id=envelope.trace_id,
        conversation_id=UUID(conversation),
        idempotency_key=f"late-escalation:{envelope.trace_id}",
        channel="telegram",
        pool=w.pool,
        pending_actions=PendingActionStore(w.pool),
    )
    result = await EscalationAgent().run(ctx, EscalationInput(reason="late synthetic turn"))
    assert result.ok and result.facts == []
    [current] = await w.q(
        "SELECT owned_by,ai_muted_until,inbox_version FROM conversations WHERE id=%s",
        conversation,
    )
    assert current == {
        "owned_by": "ai" if release else "human_support",
        "ai_muted_until": None,
        "inbox_version": 2 if release else 1,
    }
    for table in ("escalations", "tickets", "staff_notifications"):
        assert not await w.q(f"SELECT id FROM {table} WHERE client_id=%s", envelope.client_id)


def path(w: SessionWorld, suffix: str = "") -> str:
    return f"/console/v1/inbox{suffix}?client_id={w.tenants[0]}"


async def seed(
    w: SessionWorld,
    *,
    tenant: int = 0,
    name: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    address: str | None = None,
    body: str = "Synthetic question",
    opened_at: datetime | None = None,
    message_at: datetime | None = None,
    messages: bool = True,
) -> tuple[Envelope, str]:
    client, contact, conversation, credential, endpoint, trace = (
        UUID(w.tenants[tenant]),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    await w.q(
        "INSERT INTO contacts(id,client_id,full_name,phone,email,address) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        contact,
        client,
        name,
        phone,
        email,
        address,
    )
    await w.q(
        "INSERT INTO conversations(id,client_id,contact_id,channel,channel_thread,opened_at) "
        "VALUES (%s,%s,%s,'telegram',%s,COALESCE(%s::timestamptz, now()))",
        conversation,
        client,
        contact,
        str(contact.int),
        opened_at,
    )
    await w.q(
        "INSERT INTO channel_credentials(id,client_id,channel,access_token) "
        "VALUES (%s,%s,'telegram','synthetic-ciphertext')",
        credential,
        client,
    )
    await w.q(
        "INSERT INTO webhook_endpoints(id,client_id,public_id,credential_id) VALUES (%s,%s,%s,%s)",
        endpoint,
        client,
        f"synthetic-{uuid4().hex}",
        credential,
    )
    envelope = Envelope(
        trace_id=trace,
        received_at=datetime.now(UTC),
        channel=Channel.TELEGRAM,
        idempotency_key=f"synthetic:{trace}",
        client_id=client,
        contact=Contact(
            id=contact,
            external_id=str(contact.int),
            channel=Channel.TELEGRAM,
            display_name="Synthetic",
        ),
        conversation=ConversationRef(
            id=conversation, channel_thread=str(contact.int), turn_count=1, status="open"
        ),
        message=Message(
            body_raw="Synthetic question", body_redacted="Synthetic question", language="en"
        ),
        meta=Meta(),
    )
    async with tx(w.pool, str(client)) as conn:
        from psycopg.types.json import Jsonb

        await db.q.fetch_one(
            "ingress/insert_event.sql",
            (
                client,
                endpoint,
                contact,
                envelope.idempotency_key,
                "synthetic-hash",
                trace,
                Jsonb({"envelope": envelope.model_dump(mode="json")}),
            ),
            conn=conn,
        )
        if messages:
            await db.q.fetch_one(
                "worker/insert_message.sql",
                (
                    client,
                    conversation,
                    "inbound",
                    "customer",
                    body,
                    body,
                    Jsonb([]),
                    False,
                    f"{trace}:in",
                    trace,
                ),
                conn=conn,
            )
    if message_at is not None:
        await w.q(
            "UPDATE messages SET created_at=%s WHERE conversation_id=%s", message_at, conversation
        )
    return envelope, str(conversation)


async def held(w: SessionWorld, envelope: Envelope) -> int:
    from datetime import timedelta

    assert envelope.conversation.id is not None
    async with tx(w.pool, str(envelope.client_id)) as conn:
        return await hold_reply(
            conn,
            client_id=envelope.client_id,
            conversation_id=envelope.conversation.id,
            trace_id=envelope.trace_id,
            draft="Synthetic policy answer",
            reason="review",
            risk=0.5,
            ui={},
            ttl=timedelta(minutes=30),
        )


async def takeover(w: SessionWorld, conversation: str) -> httpx.Response:
    return await w.mutate(
        w.admin,
        path(w, f"/conversations/{conversation}/takeover"),
        {"request_key": str(uuid4()), "expected_version": 0},
    )


async def test_inbox_roles_tenants_and_explicit_private_exclusion(sessions: SessionWorld) -> None:
    w = sessions
    _, conversation = await seed(w)
    staff, _ = await w.member()
    tenant_admin, _ = await w.member(role="tenant_admin")
    reader, _ = await w.member(role="read_only")
    foreign, _ = await w.member(tenant=w.tenants[1])
    for browser in (staff, tenant_admin):
        assert (await browser.get("/console/v1/inbox/conversations")).status_code == 200
    for suffix in ("/conversations", "/approvals", f"/conversations/{conversation}/messages"):
        assert (await reader.get("/console/v1/inbox" + suffix)).status_code == 403
    assert (
        await foreign.get(f"/console/v1/inbox/conversations/{conversation}/messages")
    ).status_code == 404
    assert (
        await staff.get(path(w, "/conversations").replace(w.tenants[0], w.tenants[1]))
    ).status_code == 403
    assert (await w.admin.get("/console/v1/inbox/conversations")).status_code == 400
    anonymous = await w.browser()
    assert (await anonymous.get(path(w, "/conversations"))).status_code == 401
    anonymous.cookies.clear()
    anonymous.headers.update(
        {"Authorization": f"Bearer {MACHINE_TOKEN}", "X-Client-Id": w.tenants[0]}
    )
    assert (await anonymous.get("/console/v1/inbox/conversations")).status_code == 403
    created = await w.mutate(
        w.admin,
        f"/console/v1/chat/conversations?client_id={w.tenants[0]}",
        {"request_key": str(uuid4())},
    )
    sandbox = created.json()["id"]
    # Even corrupting a channel label must not turn an archived/private thread into customer data.
    await w.q("UPDATE conversations SET channel='telegram' WHERE id=%s", sandbox)
    await w.q("UPDATE console_chat_threads SET archived_at=now() WHERE id=%s", sandbox)
    listed = (await w.admin.get(path(w, "/conversations"))).json()["items"]
    assert [item["id"] for item in listed] == [conversation]
    for suffix in ("", "/messages"):
        assert (await w.admin.get(path(w, f"/conversations/{sandbox}{suffix}"))).status_code == 404
    assert (await takeover(w, sandbox)).status_code == 404
    assert (
        await w.admin.post(
            path(w, f"/conversations/{conversation}/takeover"),
            json={"request_key": str(uuid4()), "expected_version": 0},
        )
    ).status_code == 403


@pytest.mark.parametrize("decision", ["approve", "reject", "edit", "expire"])
async def test_structured_reply_decisions_and_replay(sessions: SessionWorld, decision: str) -> None:
    w = sessions
    env, _ = await seed(w)
    approval = await held(w, env)
    if decision == "expire":
        await w.q("UPDATE approvals SET expires_at=now()-interval '1 second' WHERE id=%s", approval)
    payload: dict[str, Any] = {
        "request_key": str(uuid4()),
        "decision": "approve" if decision == "expire" else decision,
    }
    if decision == "edit":
        payload["text"] = "Edited synthetic answer"
    url = path(w, f"/approvals/{approval}/decide")
    response = await w.mutate(w.admin, url, payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["outcome"] == ("expired" if decision == "expire" else "applied")
    assert result["queued"] is True
    assert (await w.mutate(w.admin, url, payload)).json() == result
    payload["request_key"] = str(uuid4())
    assert (await w.mutate(w.admin, url, payload)).json()["outcome"] == "already_handled"
    assert len(await w.q("SELECT id FROM approval_audit WHERE approval_id=%s", approval)) == 1
    assert len(await w.q("SELECT id FROM outbound_deliveries WHERE trace_id=%s", env.trace_id)) == 1


async def test_console_telegram_race_and_atomic_audit_rollback(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = sessions
    env, _ = await seed(w)
    approval = await held(w, env)
    response, telegram = await asyncio.gather(
        w.mutate(
            w.admin,
            path(w, f"/approvals/{approval}/decide"),
            {"request_key": str(uuid4()), "decision": "edit", "text": "Edited answer"},
        ),
        decide_reply(
            w.pool,
            client_id=env.client_id,
            approval_id=approval,
            approve=True,
            actor="staff_chat:synthetic",
        ),
    )
    assert response.json()["outcome"] in {"applied", "already_handled"}
    assert telegram in {"Approved. Reply queued.", "Already handled."}
    assert len(await w.q("SELECT id FROM approval_audit WHERE approval_id=%s", approval)) == 1
    assert len(await w.q("SELECT id FROM outbound_deliveries WHERE trace_id=%s", env.trace_id)) == 1
    env2, _ = await seed(w)
    approval2 = await held(w, env2)
    import csa.console.inbox as inbox

    with monkeypatch.context() as patch:
        patch.setattr(
            inbox, "audit", AsyncMock(side_effect=RuntimeError("synthetic-audit-failure"))
        )
        with pytest.raises(RuntimeError, match="synthetic-audit-failure"):
            await w.mutate(
                w.admin,
                path(w, f"/approvals/{approval2}/decide"),
                {"request_key": str(uuid4()), "decision": "approve"},
            )
    assert (await w.q("SELECT status FROM approvals WHERE id=%s", approval2))[0][
        "status"
    ] == "pending"
    assert not await w.q("SELECT id FROM outbound_deliveries WHERE trace_id=%s", env2.trace_id)


async def test_takeover_replay_stale_turns_release_and_human_reply(sessions: SessionWorld) -> None:
    w = sessions
    env, conversation = await seed(w)
    assert env.contact.id is not None
    message = OutboundMessage(
        trace_id=env.trace_id,
        contact_id=env.contact.id,
        channel=env.channel,
        body="Old AI answer",
        idempotency_key=f"{env.trace_id}:out",
    )
    async with tx(w.pool, str(env.client_id)) as conn:
        await _insert_outbound(conn, env, message)
    payload = {"request_key": str(uuid4()), "expected_version": 0}
    url = path(w, f"/conversations/{conversation}/takeover")
    results = await asyncio.gather(*(w.mutate(w.admin, url, payload) for _ in range(3)))
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    assert all(r.json() == results[0].json() for r in results)
    assert results[0].json()["conversation"]["human_muted"]
    context = await PostgresTurnDependencies(w.pool).load_context(env)
    assert context.flags["human_muted"]
    assert (await w.q("SELECT status FROM outbound_deliveries WHERE trace_id=%s", env.trace_id))[0][
        "status"
    ] == "skipped"
    reply = {"request_key": str(uuid4()), "expected_version": 1, "body": "Human synthetic reply"}
    reply_url = path(w, f"/conversations/{conversation}/reply")
    first = await w.mutate(w.admin, reply_url, reply)
    assert first.status_code == 202 and first.json()["queued"], first.text
    assert (await w.mutate(w.admin, reply_url, reply)).json() == first.json()
    reply["body"] = "Changed"
    assert (await w.mutate(w.admin, reply_url, reply)).status_code == 409
    assert (
        await w.mutate(
            w.admin,
            path(w, f"/conversations/{conversation}/release"),
            {"request_key": str(uuid4()), "expected_version": 0},
        )
    ).status_code == 409
    released = await w.mutate(
        w.admin,
        path(w, f"/conversations/{conversation}/release"),
        {"request_key": str(uuid4()), "expected_version": 1},
    )
    assert released.status_code == 200 and not released.json()["conversation"]["human_muted"]
    await PostgresTurnDependencies(w.pool).fail_safe(env, context, "synthetic-error")
    async with tx(w.pool, str(env.client_id)) as conn:
        await _insert_outbound(
            conn, env, message.model_copy(update={"idempotency_key": f"{env.trace_id}:late"})
        )
    assert (
        len(
            await w.q(
                "SELECT id FROM messages WHERE conversation_id=%s AND direction='outbound'",
                conversation,
            )
        )
        == 2
    )
    assert (
        len(
            await w.q(
                "SELECT id FROM console_audit WHERE client_id=%s AND action='inbox.takeover'",
                env.client_id,
            )
        )
        == 1
    )


async def test_delivery_lock_waits_before_takeover_and_preserves_human_reply(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = sessions
    env, conversation = await seed(w)
    assert env.contact.id is not None
    async with tx(w.pool, str(env.client_id)) as conn:
        await _insert_outbound(
            conn,
            env,
            OutboundMessage(
                trace_id=env.trace_id,
                contact_id=env.contact.id,
                channel=env.channel,
                body="AI answer",
                idempotency_key=f"{env.trace_id}:out",
            ),
        )
    async with tx(w.pool, str(env.client_id)) as conn:
        await _insert_outbound(
            conn,
            env,
            OutboundMessage(
                trace_id=env.trace_id,
                contact_id=env.contact.id,
                channel=env.channel,
                body="Queued later",
                idempotency_key=f"{env.trace_id}:second",
            ),
        )
    await w.q(
        "UPDATE outbound_deliveries SET created_at=created_at-interval '1 second' "
        "WHERE message_id IN (SELECT id FROM messages WHERE idempotency_key=%s)",
        f"{env.trace_id}:out",
    )
    await w.q(
        "UPDATE outbound_deliveries SET next_attempt_at=now()+interval '1 day' WHERE client_id<>%s",
        env.client_id,
    )
    started, finish = asyncio.Event(), asyncio.Event()

    async def send(request: httpx.Request) -> httpx.Response:
        started.set()
        await finish.wait()
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 55}})

    monkeypatch.setattr("apps.delivery.main.crypto.decrypt", lambda *args: "synthetic-token")
    owner = db.build_pool(w.owner_dsn, min_size=1, max_size=2)
    await owner.open(wait=True)
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as http:
            worker = DeliveryWorker(owner, w.pool, http)
            delivery = asyncio.create_task(worker.process_once())
            await asyncio.wait_for(started.wait(), 5)
            assert not await DeliveryWorker(owner, w.pool, http).process_once()
            taking = asyncio.create_task(takeover(w, conversation))
            await asyncio.sleep(0.1)
            assert not taking.done()
            finish.set()
            assert await delivery
            assert (await taking).status_code == 200
    finally:
        await owner.close()


async def contact_of(w: SessionWorld, conversation: str) -> UUID:
    [row] = await w.q("SELECT contact_id FROM conversations WHERE id=%s", conversation)
    return row["contact_id"]


async def listing(w: SessionWorld, query: str = "", *, tenant: int = 0) -> dict[str, Any]:
    url = f"/console/v1/inbox/conversations?client_id={w.tenants[tenant]}{query}"
    response = await w.admin.get(url)
    assert response.status_code == 200, response.text
    return response.json()


async def listing_all(w: SessionWorld, query: str = "", *, limit: int = 2) -> list[dict[str, Any]]:
    """Walk every page of the list with the given filters and return all items in order."""
    items: list[dict[str, Any]] = []
    cursor = ""
    for _ in range(50):
        page = await listing(w, f"&limit={limit}{query}{cursor}")
        items.extend(page["items"])
        if page["next_cursor"] is None:
            return items
        cursor = f"&before={page['next_cursor']}"
    raise AssertionError("pagination did not end")


async def test_list_returns_contact_last_message_and_pending_count(
    sessions: SessionWorld,
) -> None:
    w = sessions
    env, conversation = await seed(w, name="Nur Aisyah", phone="+60125550148")
    await w.q(
        "UPDATE messages SET body='Mail synthetic@example.test please',redacted_body=NULL "
        "WHERE conversation_id=%s",
        conversation,
    )
    await held(w, env)
    [item] = (await listing(w))["items"]
    assert item["contact"]["display_name"] == "Nur Aisyah"
    assert item["contact"]["phone_masked"] == "••••0148"
    assert item["contact"]["phone"] is None
    assert item["contact"]["telegram_username"] is None
    assert item["contact"]["kind"] == "customer" and item["contact"]["known"] is False
    assert "synthetic@example.test" not in str(item["last_message"])
    assert item["last_message"]["preview"] == "Mail [EMAIL_0] please"
    assert item["last_message"]["at"]
    assert item["pending_approvals"] == 1
    single = await w.admin.get(path(w, f"/conversations/{conversation}"))
    assert single.status_code == 200
    assert single.json()["contact"]["phone"] == "+60125550148"
    assert single.json()["contact"]["phone_masked"] == "••••0148"
    assert single.json()["pending_approvals"] == 1
    # Mutation results carry no contact data, preview or count, and neither does the receipt.
    taken = await takeover(w, conversation)
    assert taken.status_code == 200
    result = taken.json()["conversation"]
    assert result["contact"] is None and result["last_message"] is None
    assert result["pending_approvals"] == 0
    [receipt] = await w.q(
        "SELECT result::text AS result FROM console_inbox_actions WHERE client_id=%s",
        w.tenants[0],
    )
    for personal in ("Nur Aisyah", "0148", "60125550148", "EMAIL", "synthetic@example.test"):
        assert personal not in receipt["result"]


async def test_known_versus_new_sender(sessions: SessionWorld) -> None:
    w = sessions
    base = datetime.now(UTC)
    _, with_order = await seed(w, opened_at=base)
    _, with_subscription = await seed(w, opened_at=base)
    _, with_email = await seed(w, email="buyer@example.test", opened_at=base)
    _, with_address = await seed(w, address="1 Synthetic Road", opened_at=base)
    _, name_and_phone = await seed(w, name="Nur Aisyah", phone="+60125550148", opened_at=base)
    _, blank_fields = await seed(w, email="  ", address="", opened_at=base)
    client = UUID(w.tenants[0])
    await w.q(
        "INSERT INTO orders(id,client_id,contact_id,status,total) VALUES (%s,%s,%s,'confirmed',10)",
        f"ORD-{uuid4().hex[:8]}",
        client,
        await contact_of(w, with_order),
    )
    await w.q(
        "INSERT INTO subscriptions(client_id,contact_id,plan,price,started_at) "
        "VALUES (%s,%s,'basic',10,now())",
        client,
        await contact_of(w, with_subscription),
    )
    known = {with_order, with_subscription, with_email, with_address}
    new = {name_and_phone, blank_fields}
    everything = await listing_all(w)
    assert {i["id"]: i["contact"]["known"] for i in everything} == {
        **dict.fromkeys(known, True),
        **dict.fromkeys(new, False),
    }
    assert {i["id"] for i in await listing_all(w, "&sender=known")} == known
    assert {i["id"] for i in await listing_all(w, "&sender=new")} == new
    assert len(await listing_all(w, "&sender=known")) == len(known)
    response = await w.admin.get(path(w, "/conversations") + "&sender=other")
    assert response.status_code == 422


async def test_needs_review_filter_and_count_ignore_expired(sessions: SessionWorld) -> None:
    w = sessions
    live_env, live = await seed(w, name="Live Review")
    expired_env, expired = await seed(w, name="Expired Review")
    handled_env, handled = await seed(w, name="Handled Review")
    _, quiet = await seed(w, name="No Review")
    await held(w, live_env)
    expired_approval = await held(w, expired_env)
    handled_approval = await held(w, handled_env)
    await w.q(
        "UPDATE approvals SET expires_at=now()-interval '1 second' WHERE id=%s", expired_approval
    )
    await w.q("UPDATE approvals SET status='rejected' WHERE id=%s", handled_approval)
    page = await listing(w)
    assert page["needs_review_count"] == 1
    by_id = {i["id"]: i for i in page["items"]}
    assert by_id[live]["pending_approvals"] == 1
    assert by_id[expired]["pending_approvals"] == 0
    assert by_id[handled]["pending_approvals"] == 0
    assert by_id[quiet]["pending_approvals"] == 0
    flagged = await listing_all(w, "&needs_review=true")
    assert [i["id"] for i in flagged] == [live]
    assert (await listing(w, "&needs_review=true&limit=1"))["next_cursor"] is None
    # The count is tenant-wide: no filter changes it, even one that hides every row.
    for query in ("&channel=whatsapp", "&q=zzzz", "&sender=known", "&needs_review=true&q=Live"):
        filtered = await listing(w, query)
        assert filtered["needs_review_count"] == 1, query
    assert (await listing(w, "&channel=whatsapp"))["items"] == []
    assert (await listing(w, "&needs_review=true&q=Expired"))["items"] == []


async def test_list_orders_by_last_message_with_stable_cursor(sessions: SessionWorld) -> None:
    w = sessions
    now = datetime.now(UTC)
    day = timedelta(days=1)
    _, oldest = await seed(w, opened_at=now - 3 * day, message_at=now - 3 * day)
    _, middle = await seed(w, opened_at=now - 2 * day, message_at=now - 2 * day)
    _, tie_a = await seed(w, opened_at=now - day, message_at=now - day)
    _, tie_b = await seed(w, opened_at=now - day, message_at=now - day)
    _, silent = await seed(w, opened_at=now - timedelta(hours=12), messages=False)
    ties = sorted([tie_a, tie_b], key=UUID, reverse=True)
    assert [i["id"] for i in await listing_all(w, limit=50)] == [silent, *ties, middle, oldest]
    by_id = {i["id"]: i for i in (await listing(w))["items"]}
    assert by_id[silent]["last_message"] is None
    assert by_id[oldest]["last_message"]["preview"] == "Synthetic question"
    # A new message on the oldest conversation moves it first.
    await w.q(
        "UPDATE messages SET created_at=%s WHERE conversation_id=%s",
        now - timedelta(minutes=1),
        oldest,
    )
    expected = [oldest, silent, *ties, middle]
    assert [i["id"] for i in await listing_all(w, limit=50)] == expected
    # One at a time, the cursor visits every conversation exactly once, in the same order.
    assert [i["id"] for i in await listing_all(w, limit=1)] == expected
    assert [i["id"] for i in await listing_all(w, limit=2)] == expected


async def test_search_matches_name_phone_formats_and_escapes(sessions: SessionWorld) -> None:
    w = sessions
    _, aisyah = await seed(w, name="Nur Aisyah", phone="+60125550148")
    _, short = await seed(w, name="Li")
    _, percent = await seed(w, name="Fifty%Percent")
    _, underscore = await seed(w, name="Under_score")
    _, quoted = await seed(w, name="Dan O'Neil")
    _, plain = await seed(w, name="Plain Name", phone="+60179990000")

    async def found(q: str) -> set[str]:
        response = await w.admin.get(path(w, "/conversations") + "&" + urlencode({"q": q}))
        assert response.status_code == 200, (q, response.text)
        return {i["id"] for i in response.json()["items"]}

    for q in ("aisyah", "AISYAH", "  @Aisyah ", "0125550148", "012 555", "+60125550148"):
        assert await found(q) == {aisyah}, q
    assert await found("li") == {short}
    assert await found("y%P") == {percent}
    assert await found("r_s") == {underscore}
    assert await found("r%s") == set()
    assert await found("%%") == set()
    assert await found("a\\b") == set()
    assert await found("o'neil") == {quoted}
    assert await found("zzzz") == set()
    assert await found("Name") == {plain}
    for bad in ("a", " @ ", "x" * 65):
        response = await w.admin.get(path(w, "/conversations") + "&" + urlencode({"q": bad}))
        assert response.status_code == 422, bad
    short_response = await w.admin.get(path(w, "/conversations") + "&q=a")
    assert short_response.json()["detail"] == "search needs at least two characters"
    # Search combines with the other filters and with paging.
    combined = await w.admin.get(
        path(w, "/conversations") + "&q=aisyah&sender=new&channel=telegram"
    )
    assert {i["id"] for i in combined.json()["items"]} == {aisyah}


async def test_search_never_leaks_other_tenants(sessions: SessionWorld) -> None:
    w = sessions
    _, mine = await seed(w, name="Aisyah Mine", phone="+60125550148")
    _, theirs = await seed(w, tenant=1, name="Aisyah Theirs", phone="+60125550149")
    own = await listing(w, "&q=aisyah")
    assert [i["id"] for i in own["items"]] == [mine]
    foreign = await listing(w, "&q=0125550149", tenant=1)
    assert [i["id"] for i in foreign["items"]] == [theirs]
    assert (await listing(w, "&q=0125550149"))["items"] == []
    assert (await listing(w, "&q=Aisyah", tenant=1))["items"][0]["id"] == theirs


async def test_new_params_keep_role_rules(sessions: SessionWorld) -> None:
    w = sessions
    _, conversation = await seed(w, name="Nur Aisyah", phone="+60125550148")
    reader, _ = await w.member(role="read_only")
    foreign, _ = await w.member(tenant=w.tenants[1])
    staff, _ = await w.member()
    for query in ("needs_review=true", "sender=known", "q=aisyah"):
        assert (await reader.get(f"/console/v1/inbox/conversations?{query}")).status_code == 403
        listed = await staff.get(f"/console/v1/inbox/conversations?{query}")
        assert listed.status_code == 200, listed.text
        wrong_tenant = f"/console/v1/inbox/conversations?client_id={w.tenants[0]}&{query}"
        assert (await foreign.get(wrong_tenant)).status_code == 403
    assert (await reader.get(f"/console/v1/inbox/conversations/{conversation}")).status_code == 403
    assert (await foreign.get(f"/console/v1/inbox/conversations/{conversation}")).status_code == 404
    staff_read = await staff.get(f"/console/v1/inbox/conversations/{conversation}")
    assert staff_read.status_code == 200
    assert staff_read.json()["contact"]["phone"] == "+60125550148"


async def test_unnamed_whatsapp_sender_shows_masked_number(sessions: SessionWorld) -> None:
    w = sessions
    _, conversation = await seed(w, name="+60125550148", phone="+60125550148")
    [item] = (await listing(w))["items"]
    assert item["contact"]["display_name"] is None
    assert item["contact"]["phone_masked"] == "••••0148"
    assert item["contact"]["phone"] is None
    single = (await w.admin.get(path(w, f"/conversations/{conversation}"))).json()
    assert single["contact"]["display_name"] is None
    assert single["contact"]["phone"] == "+60125550148"
