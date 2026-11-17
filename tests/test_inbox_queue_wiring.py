"""The queue transitions wired into the code that knows the actor (design 2026-11-16, 5.2).

Held replies (``hold_reply``, the approval settle), the Inbox takeover, reply and release routes
and the staff-chat "Resume AI" button drive ``conversations.queue_status`` through
``csa.inbox_queue``; the three escalation writers do it through the 0622 trigger with no code of
their own. Disposable database, synthetic data only.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from csa.agents.base import AgentContext
from csa.agents.escalation.agent import EscalationAgent, EscalationInput
from csa.approval.service import decide_reply, edit_reply, expire_due
from csa.contracts.callbacks import PendingActionStore
from csa.contracts.envelope import Envelope
from csa.contracts.state import OutboundMessage
from csa.core import db
from csa.core.tenancy import tx
from csa.payments import notices
from csa.staff.callbacks import handle_staff_action
from csa.turn.deps import PostgresTurnDependencies, _insert_outbound
from psycopg.types.json import Jsonb

from tests.conftest import requires_docker
from tests.test_console_inbox import held, path, seed, takeover
from tests.test_console_services import activate
from tests.test_console_sessions import SessionWorld

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

_STATE = (
    "SELECT queue_status, queue_reason, priority, assignee_user_id, claimed_at, needs_staff_since, "
    "due_at, snoozed_until, resolved_at, last_staff_reply_at, inbox_version, owned_by, status "
    "FROM conversations WHERE id=%s"
)


@pytest.fixture(autouse=True)
async def packages(sessions: SessionWorld) -> None:
    for tenant in sessions.tenants:
        await activate(sessions, tenant, "customer_support")


async def state(w: SessionWorld, conversation: str | UUID) -> dict[str, Any]:
    [row] = await w.q(_STATE, UUID(str(conversation)))
    return row


async def events(w: SessionWorld, conversation: str | UUID) -> list[dict[str, Any]]:
    return await w.q(
        "SELECT action, from_status, to_status, actor_user_id, details FROM inbox_queue_events "
        "WHERE conversation_id=%s ORDER BY id",
        UUID(str(conversation)),
    )


async def escalation_statuses(w: SessionWorld, conversation: str) -> list[str]:
    rows = await w.q(
        "SELECT status FROM escalations WHERE conversation_id=%s ORDER BY created_at",
        UUID(conversation),
    )
    return [r["status"] for r in rows]


async def replies_on(w: SessionWorld) -> None:
    """The company sends replies (the default 'shadow' mode sends nothing on expiry or mute)."""
    await w.q(
        "UPDATE clients SET config=jsonb_set(config,'{policy,deployment_mode}','\"suggest\"')"
        " WHERE id=%s",
        w.tenants[0],
    )


async def decide(w: SessionWorld, approval: int, decision: str, text: str | None = None) -> Any:
    payload: dict[str, Any] = {"request_key": str(uuid4()), "decision": decision}
    if text is not None:
        payload["text"] = text
    response = await w.mutate(w.admin, path(w, f"/approvals/{approval}/decide"), payload)
    assert response.status_code == 200, response.text
    return response.json()


async def act(w: SessionWorld, conversation: str, action: str, **extra: Any) -> Any:
    version = (await state(w, conversation))["inbox_version"]
    return await w.mutate(
        w.admin,
        path(w, f"/conversations/{conversation}/{action}"),
        {"request_key": str(uuid4()), "expected_version": version, **extra},
    )


def context(envelope: Envelope, w: SessionWorld, key: str) -> AgentContext:
    assert envelope.contact.id is not None and envelope.conversation.id is not None
    return AgentContext(
        client_id=envelope.client_id,
        contact_id=envelope.contact.id,
        trace_id=envelope.trace_id,
        conversation_id=envelope.conversation.id,
        idempotency_key=key,
        channel="telegram",
        pool=w.pool,
        pending_actions=PendingActionStore(w.pool),
    )


async def escalated(w: SessionWorld, envelope: Envelope) -> None:
    """An escalation through the real agent: ticket, escalation row and the staff mute."""
    result = await EscalationAgent().run(
        context(envelope, w, f"esc:{envelope.trace_id}"), EscalationInput(reason="agent_failure")
    )
    assert result.ok and result.facts


# --- held replies ---------------------------------------------------------------------------------


async def test_a_held_reply_needs_staff(sessions: SessionWorld) -> None:
    w = sessions
    env, conversation = await seed(w)
    assert (await state(w, conversation))["queue_status"] == "bot"
    await held(w, env)
    current = await state(w, conversation)
    assert (current["queue_status"], current["queue_reason"], current["priority"]) == (
        "needs_staff",
        "held_reply",
        "normal",
    )
    assert current["needs_staff_since"] is not None and current["due_at"] is not None
    assert current["assignee_user_id"] is None
    assert [
        (e["action"], e["to_status"], e["actor_user_id"]) for e in await events(w, conversation)
    ] == [("entered", "needs_staff", None)]


async def test_a_replayed_hold_after_the_decision_changes_nothing(sessions: SessionWorld) -> None:
    w = sessions
    env, conversation = await seed(w)
    approval = await held(w, env)
    assert (await decide(w, approval, "approve"))["outcome"] == "applied"
    before, history = await state(w, conversation), await events(w, conversation)
    assert await held(w, env) == approval
    assert await state(w, conversation) == before
    assert await events(w, conversation) == history


@pytest.mark.parametrize("decision", ["approve", "edit"])
async def test_a_console_user_approving_waits_on_the_customer_and_claims(
    sessions: SessionWorld, decision: str
) -> None:
    w = sessions
    env, conversation = await seed(w)
    approval = await held(w, env)
    result = await decide(
        w, approval, decision, "Edited synthetic answer" if decision == "edit" else None
    )
    assert result["outcome"] == "applied" and result["queued"]
    current = await state(w, conversation)
    assert current["queue_status"] == "waiting_customer"
    assert current["queue_reason"] is None and current["due_at"] is None
    assert str(current["assignee_user_id"]) == w.admin_id and current["claimed_at"] is not None
    assert current["last_staff_reply_at"] is not None
    last = (await events(w, conversation))[-1]
    assert (last["action"], last["from_status"], str(last["actor_user_id"])) == (
        "replied",
        "needs_staff",
        w.admin_id,
    )
    [message] = await w.q(
        "SELECT send_authority FROM messages WHERE conversation_id=%s AND direction='outbound'",
        UUID(conversation),
    )
    assert message["send_authority"] == "reviewed"


@pytest.mark.parametrize("decision", ["approve", "edit"])
async def test_a_staff_chat_decision_waits_on_the_customer_unclaimed(
    sessions: SessionWorld, decision: str
) -> None:
    w = sessions
    env, conversation = await seed(w)
    approval = await held(w, env)
    if decision == "approve":
        reply = await decide_reply(
            w.pool,
            client_id=env.client_id,
            approval_id=approval,
            approve=True,
            actor="staff_chat:synthetic",
        )
        assert reply == "Approved. Reply queued."
    else:
        reply = await edit_reply(
            w.pool,
            client_id=env.client_id,
            approval_id=approval,
            text="Edited synthetic answer",
            actor="staff_chat:synthetic",
        )
        assert reply.startswith("Edited reply queued.")
    current = await state(w, conversation)
    assert current["queue_status"] == "waiting_customer"
    assert current["assignee_user_id"] is None and current["claimed_at"] is None
    assert current["last_staff_reply_at"] is not None
    last = (await events(w, conversation))[-1]
    assert (last["action"], last["actor_user_id"]) == ("replied", None)


async def test_a_rejected_held_reply_stays_needs_staff_as_a_customer_message(
    sessions: SessionWorld,
) -> None:
    w = sessions
    env, conversation = await seed(w)
    approval = await held(w, env)
    entered = await state(w, conversation)
    result = await decide(w, approval, "reject")
    assert result["outcome"] == "applied" and result["queued"]
    current = await state(w, conversation)
    assert (current["queue_status"], current["queue_reason"]) == ("needs_staff", "customer_message")
    assert current["needs_staff_since"] == entered["needs_staff_since"]
    assert current["due_at"] == entered["due_at"]
    assert current["last_staff_reply_at"] is None and current["assignee_user_id"] is None
    assert [e["action"] for e in await events(w, conversation)] == ["entered", "reason_changed"]
    # The follow-up went out, as an automated message.
    assert await w.q(
        "SELECT id FROM messages WHERE conversation_id=%s AND idempotency_key=%s",
        UUID(conversation),
        f"appr:{approval}:rejected",
    )


async def test_a_rejection_the_bot_may_not_follow_up_still_closes_the_held_reply(
    sessions: SessionWorld,
) -> None:
    """After a takeover no follow-up is sent; the held reply is closed all the same."""
    w = sessions
    env, conversation = await seed(w)
    approval = await held(w, env)
    assert (await takeover(w, conversation)).json()["outcome"] == "applied"
    claimed = await state(w, conversation)
    assert (claimed["queue_reason"], str(claimed["assignee_user_id"])) == ("held_reply", w.admin_id)
    result = await decide(w, approval, "reject")
    assert result["outcome"] == "applied" and not result["queued"]
    current = await state(w, conversation)
    assert (current["queue_status"], current["queue_reason"]) == ("needs_staff", "customer_message")
    assert str(current["assignee_user_id"]) == w.admin_id


@pytest.mark.parametrize("replies", [True, False])
async def test_an_expired_held_reply_becomes_a_high_escalation(
    sessions: SessionWorld, replies: bool
) -> None:
    """The expiry worker closes the held reply; its escalation row lifts the reason (0622)."""
    w = sessions
    if replies:
        await replies_on(w)
    env, conversation = await seed(w)
    approval = await held(w, env)
    await w.q("UPDATE approvals SET expires_at=now()-interval '1 second' WHERE id=%s", approval)
    owner_pool = db.build_pool(w.owner_dsn, max_size=1)
    await owner_pool.open(wait=True)
    try:
        assert await expire_due(owner_pool, w.pool) >= 1
    finally:
        await owner_pool.close()
    current = await state(w, conversation)
    assert (current["queue_status"], current["queue_reason"], current["priority"]) == (
        "needs_staff",
        "escalation",
        "high",
    )
    assert current["last_staff_reply_at"] is None
    assert [
        (e["action"], e["details"].get("reason"), e["details"].get("priority"))
        for e in await events(w, conversation)
    ] == [
        ("entered", "held_reply", "normal"),
        ("reason_changed", "customer_message", "normal"),
        ("reason_changed", "escalation", "high"),
    ]
    [escalation] = await w.q(
        "SELECT reason FROM escalations WHERE conversation_id=%s", UUID(conversation)
    )
    assert escalation["reason"] == "approval_expired"
    sent = await w.q(
        "SELECT id FROM messages WHERE conversation_id=%s AND idempotency_key=%s",
        UUID(conversation),
        f"appr:{approval}:expired",
    )
    assert bool(sent) is replies


async def test_an_approval_that_writes_no_message_leaves_the_queue_alone(
    sessions: SessionWorld,
) -> None:
    w = sessions
    env, conversation = await seed(w)
    approval = await held(w, env)
    # A message already holds the settle's idempotency key, so the settle inserts none.
    await w.q(
        "INSERT INTO messages(client_id,conversation_id,direction,author,body,redacted_body,"
        "idempotency_key,trace_id) VALUES (%s,%s,'outbound','assistant','x','x',%s,%s)",
        env.client_id,
        UUID(conversation),
        f"appr:{approval}:approved",
        env.trace_id,
    )
    before, history = await state(w, conversation), await events(w, conversation)
    result = await decide(w, approval, "approve")
    assert result["outcome"] == "applied" and not result["queued"]
    assert await state(w, conversation) == before
    assert await events(w, conversation) == history


# --- takeover, reply, release ---------------------------------------------------------------------


async def test_takeover_claims_and_a_repeat_changes_nothing(sessions: SessionWorld) -> None:
    w = sessions
    _, conversation = await seed(w)
    response = await takeover(w, conversation)
    assert response.json()["outcome"] == "applied"
    current = await state(w, conversation)
    assert (current["queue_status"], current["queue_reason"], current["priority"]) == (
        "needs_staff",
        "manual",
        "normal",
    )
    assert str(current["assignee_user_id"]) == w.admin_id
    assert current["inbox_version"] == 1 == response.json()["conversation"]["inbox_version"]
    [entered] = await events(w, conversation)
    assert (entered["action"], str(entered["actor_user_id"])) == ("entered", w.admin_id)
    again = await act(w, conversation, "takeover")
    assert again.json()["outcome"] == "already_handled"
    assert await state(w, conversation) == current
    assert len(await events(w, conversation)) == 1


async def test_a_stale_takeover_is_409_and_writes_no_queue_event(sessions: SessionWorld) -> None:
    w = sessions
    _, conversation = await seed(w)
    await w.q("UPDATE conversations SET inbox_version=3 WHERE id=%s", UUID(conversation))
    response = await takeover(w, conversation)
    assert response.status_code == 409
    assert (await state(w, conversation))["queue_status"] == "bot"
    assert await events(w, conversation) == []


async def test_a_failed_takeover_rolls_the_queue_back(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = sessions
    _, conversation = await seed(w)
    import csa.console.inbox as inbox

    monkeypatch.setattr(inbox, "audit", AsyncMock(side_effect=RuntimeError("synthetic-failure")))
    with pytest.raises(RuntimeError, match="synthetic-failure"):
        await takeover(w, conversation)
    current = await state(w, conversation)
    assert (current["queue_status"], current["owned_by"]) == ("bot", None)
    assert await events(w, conversation) == []


async def test_a_reply_waits_on_the_customer(sessions: SessionWorld) -> None:
    w = sessions
    _, conversation = await seed(w)
    assert (await takeover(w, conversation)).status_code == 200
    version = (await state(w, conversation))["inbox_version"]
    response = await act(w, conversation, "reply", body="Human synthetic reply")
    assert response.status_code == 202, response.text
    current = await state(w, conversation)
    # A person's answer moves the queue state, so it bumps the version once.
    assert current["inbox_version"] == version + 1
    assert response.json()["conversation"]["inbox_version"] == version + 1
    assert current["queue_status"] == "waiting_customer"
    assert current["last_staff_reply_at"] is not None and current["due_at"] is None
    assert str(current["assignee_user_id"]) == w.admin_id
    assert [(e["action"], str(e["actor_user_id"])) for e in await events(w, conversation)] == [
        ("entered", w.admin_id),
        ("replied", w.admin_id),
    ]


async def test_a_resolve_read_before_a_reply_is_409(sessions: SessionWorld) -> None:
    """Two people read the item at version 7; one replies, the other's resolve is stale."""
    w = sessions
    _, conversation = await seed(w)
    assert (await takeover(w, conversation)).status_code == 200
    await w.q("UPDATE conversations SET inbox_version = 7 WHERE id = %s", UUID(conversation))
    replied = await w.mutate(
        w.admin,
        path(w, f"/conversations/{conversation}/reply"),
        {"request_key": str(uuid4()), "expected_version": 7, "body": "Human synthetic reply"},
    )
    assert replied.status_code == 202, replied.text
    assert replied.json()["conversation"]["inbox_version"] == 8
    before = (await state(w, conversation), await events(w, conversation))
    resolve = await w.mutate(
        w.admin,
        path(w, f"/conversations/{conversation}/queue/resolve"),
        {"request_key": str(uuid4()), "expected_version": 7},
    )
    assert resolve.status_code == 409, resolve.text
    assert resolve.json()["detail"] == "conversation changed; refresh before acting"
    assert (await state(w, conversation), await events(w, conversation)) == before
    assert before[0]["queue_status"] == "waiting_customer"


async def test_release_hands_back_and_closes_the_escalation_ticket(
    sessions: SessionWorld,
) -> None:
    w = sessions
    await replies_on(w)
    env, conversation = await seed(w)
    await escalated(w, env)
    current = await state(w, conversation)
    assert (current["queue_status"], current["queue_reason"]) == ("needs_staff", "escalation")
    assert current["owned_by"] == "human_support"
    response = await act(w, conversation, "release")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "applied" and not body["conversation"]["human_muted"]
    current = await state(w, conversation)
    assert (current["queue_status"], current["owned_by"], current["status"]) == (
        "bot",
        "ai",
        "open",
    )
    assert current["assignee_user_id"] is None and current["queue_reason"] is None
    assert body["conversation"]["inbox_version"] == current["inbox_version"]
    assert [
        r["status"]
        for r in await w.q(
            "SELECT status FROM tickets WHERE conversation_id=%s", UUID(conversation)
        )
    ] == ["resolved"]
    assert await escalation_statuses(w, conversation) == ["resolved"]
    last = (await events(w, conversation))[-1]
    assert (last["action"], last["to_status"], str(last["actor_user_id"])) == (
        "handed_back",
        "bot",
        w.admin_id,
    )
    again = await act(w, conversation, "release")
    assert again.json()["outcome"] == "already_handled"
    assert await state(w, conversation) == current


async def test_release_of_an_ai_owned_item_still_leaves_the_queue(sessions: SessionWorld) -> None:
    """The AI has the chat but a held reply put it in the queue: the staff UI sees a change."""
    w = sessions
    env, conversation = await seed(w)
    await held(w, env)
    before = await state(w, conversation)
    assert before["owned_by"] is None and before["queue_status"] == "needs_staff"
    response = await act(w, conversation, "release")
    assert response.json()["outcome"] == "applied"
    current = await state(w, conversation)
    assert current["queue_status"] == "bot" and current["owned_by"] is None
    assert current["inbox_version"] == before["inbox_version"] + 1
    assert response.json()["conversation"]["inbox_version"] == current["inbox_version"]


async def test_release_of_a_bot_item_is_already_handled(sessions: SessionWorld) -> None:
    w = sessions
    _, conversation = await seed(w)
    before = await state(w, conversation)
    response = await act(w, conversation, "release")
    assert response.json()["outcome"] == "already_handled"
    assert await state(w, conversation) == before
    assert await events(w, conversation) == []


async def resume_button(w: SessionWorld, env: Envelope, conversation: str) -> Any:
    """Escalate through the real agent, then return a tap on the alert's Resume AI button."""
    target = uuid4()
    await w.q(
        "INSERT INTO staff_notification_targets (id, client_id, channel, address, events) "
        "VALUES (%s,%s,'telegram','999','{escalation}'::text[])",
        target,
        env.client_id,
    )
    await escalated(w, env)
    [notification] = await w.q(
        "SELECT id FROM staff_notifications WHERE client_id=%s AND event='escalation'",
        env.client_id,
    )
    async with tx(w.pool, str(env.client_id)) as conn:
        action = await db.q.fetch_one(
            "staff/upsert_resume_action.sql",
            (env.client_id, notification["id"], target, UUID(conversation), "2999-01-01T00:00Z"),
            conn=conn,
        )
    assert action is not None

    async def tap() -> str:
        return await handle_staff_action(
            w.pool,
            client_id=env.client_id,
            endpoint_id=uuid4(),
            chat_id="999",
            staff_action_id=action["id"],
            trace_id=uuid4(),
        )

    return tap


async def test_staff_chat_resume_ai_hands_back(sessions: SessionWorld) -> None:
    w = sessions
    await replies_on(w)
    env, conversation = await seed(w)
    tap = await resume_button(w, env, conversation)

    assert await tap() == "AI resumed."
    current = await state(w, conversation)
    assert (current["queue_status"], current["owned_by"], current["status"]) == (
        "bot",
        "ai",
        "open",
    )
    assert current["queue_reason"] is None and current["assignee_user_id"] is None
    assert [
        r["status"]
        for r in await w.q(
            "SELECT status FROM tickets WHERE conversation_id=%s", UUID(conversation)
        )
    ] == ["resolved"]
    assert await escalation_statuses(w, conversation) == ["resolved"]
    last = (await events(w, conversation))[-1]
    assert (last["action"], last["to_status"], last["actor_user_id"]) == (
        "handed_back",
        "bot",
        None,
    )
    assert await tap() == "Already handled."


async def test_resume_ai_after_the_mute_expired_still_hands_back(sessions: SessionWorld) -> None:
    """The mute job gave the chat back to the AI but left the item: the button still ends it."""
    w = sessions
    await replies_on(w)
    env, conversation = await seed(w)
    tap = await resume_button(w, env, conversation)
    await w.q(
        "UPDATE conversations SET ai_muted_until = now() - interval '1 minute' WHERE id = %s",
        UUID(conversation),
    )
    async with tx(w.pool, str(env.client_id)) as conn:
        expired = await db.q.fetch_one(
            "jobs/mute_expiry.sql", (env.client_id, 100, env.client_id), conn=conn
        )
    assert expired is not None and expired["count"] == 1
    current = await state(w, conversation)
    assert (current["queue_status"], current["owned_by"], current["status"]) == (
        "needs_staff",
        "ai",
        "open",
    )
    assert await tap() == "AI resumed."
    current = await state(w, conversation)
    assert (current["queue_status"], current["queue_reason"]) == ("bot", None)
    assert await escalation_statuses(w, conversation) == ["resolved"]
    last = (await events(w, conversation))[-1]
    assert (last["action"], last["to_status"], last["actor_user_id"]) == (
        "handed_back",
        "bot",
        None,
    )


async def test_resume_ai_on_a_sandbox_escalation_releases_it_and_leaves_the_queue(
    sessions: SessionWorld,
) -> None:
    w = sessions
    await replies_on(w)
    env, conversation = await seed(w)
    await w.q("UPDATE conversations SET channel = 'web' WHERE id = %s", UUID(conversation))
    tap = await resume_button(w, env, conversation)
    current = await state(w, conversation)
    assert (current["queue_status"], current["status"]) == ("bot", "escalated")
    assert await tap() == "AI resumed."
    current = await state(w, conversation)
    assert (current["queue_status"], current["owned_by"], current["status"]) == (
        "bot",
        "ai",
        "open",
    )
    assert await events(w, conversation) == []


# --- writers that need no code: the escalation trigger --------------------------------------------


async def test_the_escalation_agent_and_the_fail_safe_need_staff(sessions: SessionWorld) -> None:
    w = sessions
    env, conversation = await seed(w)
    await escalated(w, env)
    current = await state(w, conversation)
    assert (current["queue_status"], current["queue_reason"], current["priority"]) == (
        "needs_staff",
        "escalation",
        "normal",
    )
    env2, conversation2 = await seed(w)
    deps = PostgresTurnDependencies(w.pool)
    await deps.fail_safe(env2, await deps.load_context(env2), "synthetic-error")
    current = await state(w, conversation2)
    assert (current["queue_status"], current["queue_reason"], current["priority"]) == (
        "needs_staff",
        "escalation",
        "high",
    )


# --- not a staff answer (Review Focus 2) ----------------------------------------------------------


async def test_automated_messages_leave_a_needs_staff_item_alone(sessions: SessionWorld) -> None:
    """A payment notice, a booking message, a staff-chat order decision and a bot reply."""
    w = sessions
    env, conversation = await seed(w)
    await held(w, env)
    before, history = await state(w, conversation), await events(w, conversation)
    assert env.contact.id is not None
    async with tx(w.pool, str(env.client_id)) as conn:
        endpoint = await db.q.fetch_one(
            "turn/get_ingress_endpoint.sql", (env.trace_id, env.client_id), conn=conn
        )
        assert endpoint is not None
        route = {
            "conversation_id": UUID(conversation),
            "endpoint_id": endpoint["endpoint_id"],
            "trace_id": env.trace_id,
        }
        # The payment notice writer itself (reviewed authority and a delivery row).
        await notices._write(  # pyright: ignore[reportPrivateUsage]
            conn, env.client_id, route, key=f"pay:{uuid4()}", body="Synthetic notice",
            attachments=[],
        )  # fmt: skip
        # A booking message and a staff-chat order decision: the same statements as
        # csa.bookings.customer_messages.telegram_message and csa.staff.callbacks.
        for key, authority_sql in (
            (f"booking:{uuid4()}", "bookings/message_authority_set.sql"),
            (f"staff:{uuid4()}:approve", "staff/set_reviewed.sql"),
        ):
            message = await db.q.fetch_one(
                "worker/insert_message.sql",
                (
                    env.client_id,
                    UUID(conversation),
                    "outbound",
                    "assistant",
                    "Synthetic automated text",
                    "Synthetic automated text",
                    Jsonb([]),
                    False,
                    key,
                    env.trace_id,
                ),
                conn=conn,
            )
            assert message is not None
            if authority_sql.startswith("bookings/"):
                await db.q.execute(
                    authority_sql,
                    {"client_id": env.client_id, "id": message["id"], "authority": "reviewed"},
                    conn=conn,
                )
            else:
                await db.q.execute(authority_sql, (env.client_id, message["id"]), conn=conn)
            await db.q.fetch_one(
                "worker/insert_delivery.sql",
                (
                    env.client_id,
                    endpoint["endpoint_id"],
                    env.trace_id,
                    UUID(conversation),
                    message["id"],
                ),
                conn=conn,
            )
        await _insert_outbound(
            conn,
            env,
            OutboundMessage(
                trace_id=env.trace_id,
                contact_id=env.contact.id,
                channel=env.channel,
                body="Synthetic bot reply",
                idempotency_key=f"{env.trace_id}:bot",
            ),
        )
    reviewed = await w.q(
        "SELECT count(*) AS n FROM messages WHERE conversation_id=%s AND direction='outbound' "
        "AND send_authority='reviewed'",
        UUID(conversation),
    )
    assert reviewed[0]["n"] == 3
    assert await state(w, conversation) == before
    assert await events(w, conversation) == history
