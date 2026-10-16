"""M4: private synthetic web turns through real disposable PostgreSQL queues."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
from apps.delivery.main import DeliveryWorker
from apps.worker.main import ClaimedIngress, IngressWorker
from csa.agents.kb import DefaultKbEmbedder, KbAgent
from csa.agents.kb.extract import ExtractedChunk
from csa.agents.kb.store import store_document
from csa.contracts.envelope import Envelope
from csa.contracts.facts import Fact
from csa.contracts.plan import Intent, Plan, Task
from csa.contracts.state import Context, Draft, GateVerdict, Signals, TurnState, Verification
from csa.core import db
from csa.core.tenancy import tx
from csa.graph.coordinator import build_runtime
from csa.graph.saver import build_checkpoint_pool, build_saver
from csa.turn.deps import SANDBOX_SCOPE_TEXT, PostgresTurnDependencies

from tests.conftest import requires_docker
from tests.test_console_sessions import MACHINE_TOKEN, SessionWorld

pytestmark = [requires_docker, pytest.mark.asyncio, pytest.mark.usefixtures("lg_setup_dsn")]
pytest_plugins = ["tests.test_console_sessions"]


@pytest.fixture(autouse=True)
async def isolate_due_queues(sessions: SessionWorld) -> AsyncIterator[None]:
    # The module shares a throwaway DB; previously tested tenants must not steal claims.
    await sessions.q("UPDATE ingress_events SET next_attempt_at=now()+interval '1 day'")
    await sessions.q("UPDATE outbound_deliveries SET next_attempt_at=now()+interval '1 day'")
    yield


def path(world: SessionWorld, suffix: str = "", *, tenant: int = 0) -> str:
    return f"/console/v1/chat/conversations{suffix}?client_id={world.tenants[tenant]}"


async def create(world: SessionWorld, **kwargs: Any) -> str:
    response = await world.mutate(world.admin, path(world), {"request_key": str(uuid4()), **kwargs})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def send(world: SessionWorld, conversation: str, key: str | None = None) -> dict[str, Any]:
    response = await world.mutate(
        world.admin,
        path(world, f"/{conversation}/turns"),
        {"request_key": key or str(uuid4()), "body": "Synthetic opening hours?"},
    )
    assert response.status_code == 202, response.text
    return response.json()


async def history(world: SessionWorld, conversation: str) -> list[dict[str, Any]]:
    response = await world.admin.get(path(world, f"/{conversation}/turns"))
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def test_private_binding_dedupe_auth_and_archive(sessions: SessionWorld) -> None:
    world = sessions
    key = str(uuid4())
    results = await asyncio.gather(
        *(world.mutate(world.admin, path(world), {"request_key": key}) for _ in range(3))
    )
    assert [response.status_code for response in results] == [201] * 3
    assert len({response.json()["id"] for response in results}) == 1
    conversation = results[0].json()["id"]
    another = await create(world)
    bindings = await world.q(
        "SELECT contact_id,endpoint_id FROM console_chat_threads WHERE id IN (%s,%s)",
        conversation,
        another,
    )
    assert bindings[0] == bindings[1]
    assert str(bindings[0]["contact_id"]) != world.admin_id
    other_member, _ = await world.member()
    foreign_member, _ = await world.member(tenant=world.tenants[1])
    reader, _ = await world.member(role="read_only")
    for browser in (other_member, foreign_member, reader):
        listed = await browser.get("/console/v1/chat/conversations")
        assert listed.status_code == 200 and listed.json()["items"] == []
        for suffix in ("/turns", "/archive"):
            target = f"/console/v1/chat/conversations/{conversation}{suffix}"
            response = (
                (await browser.get(target))
                if suffix == "/turns"
                else (await world.mutate(browser, target))
            )
            assert response.status_code == (
                403 if browser is reader and suffix == "/archive" else 404
            )
    assert (
        await world.mutate(reader, "/console/v1/chat/conversations", {"request_key": str(uuid4())})
    ).status_code == 403
    assert (await world.admin.post(path(world), json={"request_key": key})).status_code == 403
    machine = await world.browser()
    machine.cookies.clear()
    machine.headers.update(
        {"Authorization": f"Bearer {MACHINE_TOKEN}", "X-Client-Id": world.tenants[0]}
    )
    assert (await machine.get("/console/v1/chat/conversations")).status_code == 403
    anonymous = await world.browser()
    assert (await anonymous.get(path(world))).status_code == 401
    assert (
        await world.admin.get(path(world, f"/{conversation}/turns", tenant=1))
    ).status_code == 404
    request_key = str(uuid4())
    queued = await send(world, conversation, request_key)
    repeated = await send(world, conversation, request_key)
    assert repeated == {**queued, "duplicate": True}
    changed = await world.mutate(
        world.admin,
        path(world, f"/{conversation}/turns"),
        {
            "request_key": request_key,
            "body": "Different synthetic text",
        },
    )
    assert changed.status_code == 409
    assert (await history(world, conversation))[0]["status"] == "queued"
    for _ in range(2):
        assert (
            await world.mutate(world.admin, path(world, f"/{conversation}/archive"))
        ).status_code == 204
    assert (await world.admin.get(path(world))).json()["items"] == [
        item
        for item in (await world.admin.get(path(world))).json()["items"]
        if item["id"] != conversation
    ]
    assert len(await history(world, conversation)) == 1
    assert (await send(world, conversation, request_key))["duplicate"]
    new = await world.mutate(
        world.admin,
        path(world, f"/{conversation}/turns"),
        {"request_key": str(uuid4()), "body": "Not accepted"},
    )
    assert new.status_code == 409
    audit = await world.q(
        "SELECT action FROM console_audit WHERE client_id=%s AND action LIKE 'chat.%%'",
        world.tenants[0],
    )
    assert [row["action"] for row in audit].count("chat.archived") == 1
    assert [row["action"] for row in audit].count("chat.queued") == 1


async def test_text_authority_bounds_redaction_and_stable_history(sessions: SessionWorld) -> None:
    world = sessions
    conversation = await create(world)
    target = path(world, f"/{conversation}/turns")
    for extra in (
        {"contact_id": str(uuid4())},
        {"callback": {}},
        {"attachments": []},
        {"voice": {}},
        {"channel": "telegram"},
        {"client_id": world.tenants[1]},
    ):
        response = await world.mutate(
            world.admin,
            target,
            {
                "request_key": str(uuid4()),
                "body": "synthetic",
                **extra,
            },
        )
        assert response.status_code == 422
    for body in ("", " \n ", "a" * 4001):
        assert (
            await world.mutate(world.admin, target, {"request_key": str(uuid4()), "body": body})
        ).status_code == 422
    oversized = await world.admin.post(
        target, content=b"x" * 32769, headers=world.headers(world.admin)
    )
    assert oversized.status_code == 413
    await world.mutate(
        world.admin,
        target,
        {
            "request_key": str(uuid4()),
            "body": "Synthetic email person@example.test",
        },
    )
    first = (await history(world, conversation))[0]
    assert "person@example.test" not in first["body"]
    [raw] = await world.q("SELECT raw FROM ingress_events WHERE trace_id=%s", first["trace_id"])
    assert "person@example.test" not in str(raw)
    expanded = await world.mutate(
        world.admin,
        target,
        {
            "request_key": str(uuid4()),
            "body": ("a@b.cc " * 572)[:4000],
        },
    )
    assert expanded.status_code == 202, expanded.text
    redacted = (await history(world, conversation))[0]["body"]
    assert 4000 < len(redacted) <= 8000 and "a@b.cc" not in redacted
    for before in ("0", str(2**63)):
        assert (await world.admin.get(target + f"&before={before}")).status_code == 422
    for _ in range(51):
        await send(world, conversation)
    page = (await world.admin.get(target)).json()
    assert len(page["items"]) == 50 and page["next_cursor"] is not None
    await send(world, conversation)
    older = (await world.admin.get(target + f"&before={page['next_cursor']}")).json()
    assert len(older["items"]) == 3
    assert not ({item["id"] for item in page["items"]} & {item["id"] for item in older["items"]})
    assert first["id"] in {item["id"] for item in older["items"]}


class SyntheticDeps(PostgresTurnDependencies):
    def __init__(self, pool: Any, mode: str = "send") -> None:
        super().__init__(pool)
        self.mode = mode

    async def signals(self, envelope: Envelope, context: Context) -> Signals:
        return Signals(
            intent=Intent.PRODUCT_INFO,
            provider="policy",
            version="synthetic-m4",
            capability_scores={"kb_agent": 1} if self.mode == "kb" else {},
        )

    async def plan_args(self, envelope: Envelope, context: Context, signals: Signals) -> Plan:
        return Plan(
            tasks=[
                Task(
                    task_id="kb-query",
                    agent="kb_agent",
                    input={
                        "action": "query",
                        "query": envelope.message.body_redacted,
                    },
                )
            ]
            if self.mode == "kb"
            else [],
            reply_strategy="auto_reply",
            intent=Intent.PRODUCT_INFO,
            confidence=1,
            source="policy",
        )

    async def compose(self, state: TurnState) -> Draft:
        if self.mode == "fail":
            raise RuntimeError("synthetic_composer_failure")
        if self.mode == "kb":
            assert state.facts
            return Draft(
                text=state.facts[0].text,
                language="en",
                composer="template",
                fact_ids=[state.facts[0].id],
            )
        return Draft(
            text="Synthetic opening hours: nine to five.", language="en", composer="template"
        )

    async def gate(self, state: TurnState) -> GateVerdict:
        return GateVerdict(
            decision="approve" if self.mode == "hold" else "send",
            risk_score=0,
            reason="synthetic",
            policy="synthetic",
        )


async def process(world: SessionWorld, queued: dict[str, Any], *, mode: str = "send") -> None:
    [row] = await world.q(
        "UPDATE ingress_events SET status='dispatched',attempts=attempts+1 "
        "WHERE trace_id=%s RETURNING id,client_id,endpoint_id,trace_id,raw,attempts",
        queued["trace_id"],
    )
    item = ClaimedIngress.model_validate(row)
    checkpoints = build_checkpoint_pool(world.owner_dsn)
    await checkpoints.open(wait=True)
    try:
        worker = IngressWorker(
            world.pool,
            world.pool,
            lock_conninfo=world.owner_dsn,
            coordinator=build_runtime(SyntheticDeps(world.pool, mode), build_saver(checkpoints)),
        )
        await worker._complete_turn(item, Envelope.model_validate(item.raw["envelope"]))
    finally:
        await checkpoints.close()


@pytest.mark.parametrize(
    "mode,expected", [("send", "awaiting_delivery"), ("hold", "held"), ("fail", "failed")]
)
async def test_graph_and_private_delivery_states(
    sessions: SessionWorld, mode: str, expected: str
) -> None:
    world = sessions
    conversation = await create(world)
    queued = await send(world, conversation)
    await process(world, queued, mode=mode)
    [turn] = await history(world, conversation)
    assert turn["status"] == expected and turn["replies"] == []
    assert not await world.q("SELECT id FROM escalations WHERE client_id=%s", world.tenants[0])
    assert not await world.q(
        "SELECT id FROM staff_notifications WHERE client_id=%s", world.tenants[0]
    )
    if mode == "hold":
        [held] = await world.q(
            "SELECT held_draft FROM console_chat_turns WHERE trace_id=%s", queued["trace_id"]
        )
        assert held["held_draft"] and not await world.q(
            "SELECT id FROM approvals WHERE trace_id=%s", queued["trace_id"]
        )
        return
    # Replaying an already completed durable turn cannot duplicate messages or deliveries.
    await process(world, queued, mode=mode)
    owner = db.build_pool(world.owner_dsn, max_size=2)
    await owner.open(wait=True)

    def no_network(request: httpx.Request) -> httpx.Response:
        raise AssertionError("web delivery must never make an external request")

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(no_network)) as client:
            delivery = DeliveryWorker(owner, world.pool, client)
            assert await delivery.process_once()
            assert not await delivery.process_once()
    finally:
        await owner.close()
    [turn] = await history(world, conversation)
    assert turn["status"] == ("available" if mode == "send" else "failed")
    assert len(turn["replies"]) == 1
    [count] = await world.q(
        "SELECT count(*) AS n FROM outbound_deliveries WHERE trace_id=%s", queued["trace_id"]
    )
    assert count["n"] == 1


async def test_execution_rejects_mutations_and_private_reads(sessions: SessionWorld) -> None:
    world = sessions
    conversation = await create(world)
    queued = await send(world, conversation)
    [row] = await world.q("SELECT raw FROM ingress_events WHERE trace_id=%s", queued["trace_id"])
    envelope = Envelope.model_validate(row["raw"]["envelope"])
    deps = PostgresTurnDependencies(world.pool)
    for agent, action in [
        ("kb_agent", "ingest"),
        ("kb_agent", "delete"),
        ("kb_agent", "list_gaps"),
        ("kb_agent", "resolve_gap"),
        ("order_agent", "create_draft"),
        ("order_agent", "confirm"),
        ("order_agent", "cancel"),
        ("order_agent", "status"),
        ("ticket_agent", "list"),
        ("invoice_agent", "list"),
        ("booking_agent", "list"),
        ("subscription_agent", "status"),
        ("escalation_agent", "create"),
        ("ecommerce_agent", "search"),
    ]:
        result = await deps.execute_spoke(
            Task(task_id=f"{agent}-{action}", agent=agent, input={"action": action}),
            envelope,
            Context(),
        )
        assert not result.ok and result.error and result.error.code == "sandbox_action_forbidden"
    assert not await world.q("SELECT id FROM orders WHERE client_id=%s", world.tenants[0])
    assert not await world.q("SELECT id FROM pending_actions WHERE client_id=%s", world.tenants[0])
    with pytest.raises(ValueError, match="sandbox_callbacks_forbidden"):
        await deps.resolve_callback(
            envelope.model_copy(update={"callback": {"action_id": uuid4()}}), Context()
        )


class WorkerCrash(BaseException):
    pass


async def test_worker_crash_after_outbox_commit_and_delivery_rollback(
    sessions: SessionWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = sessions
    conversation = await create(world)
    queued = await send(world, conversation)
    owner = db.build_pool(world.owner_dsn, max_size=2)
    checkpoints = build_checkpoint_pool(world.owner_dsn)
    await owner.open(wait=True)
    await checkpoints.open(wait=True)
    worker = IngressWorker(
        owner,
        world.pool,
        lock_conninfo=world.owner_dsn,
        coordinator=build_runtime(SyntheticDeps(world.pool), build_saver(checkpoints)),
    )
    original_fetch = db.q.fetch_one

    async def crash_before_ack(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "worker/complete_ingress.sql":
            raise WorkerCrash
        return await original_fetch(name, *args, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(db.q, "fetch_one", crash_before_ack)
            with pytest.raises(WorkerCrash):
                await worker.process_once()
        [before] = await world.q(
            "SELECT status,attempts FROM ingress_events WHERE trace_id=%s", queued["trace_id"]
        )
        assert before == {"status": "dispatched", "attempts": 1}
        assert (await history(world, conversation))[0]["replies"] == []
        await world.q(
            "UPDATE ingress_events SET dispatched_at=now()-interval '1 hour' WHERE trace_id=%s",
            queued["trace_id"],
        )
        assert await worker.reap() == 1
        assert await worker.process_once()
        [completed] = await world.q(
            "SELECT status,attempts FROM ingress_events WHERE trace_id=%s", queued["trace_id"]
        )
        assert completed == {"status": "completed", "attempts": 2}

        original_execute = db.q.execute
        fail_once = True

        async def fail_delivery_commit(name: str, *args: Any, **kwargs: Any) -> Any:
            nonlocal fail_once
            if name == "delivery/insert_attempt.sql" and fail_once:
                fail_once = False
                raise OSError("synthetic local delivery transaction failure")
            return await original_execute(name, *args, **kwargs)

        calls = 0

        def no_network(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise AssertionError("web send attempted an external request")

        async with httpx.AsyncClient(transport=httpx.MockTransport(no_network)) as client:
            delivery = DeliveryWorker(owner, world.pool, client)
            with monkeypatch.context() as patch:
                patch.setattr(db.q, "execute", fail_delivery_commit)
                assert await delivery.process_once()
            [turn] = await history(world, conversation)
            assert turn["status"] == "awaiting_delivery" and turn["replies"] == []
            [pending] = await world.q(
                "SELECT status,attempts FROM outbound_deliveries WHERE trace_id=%s",
                queued["trace_id"],
            )
            assert pending == {"status": "pending", "attempts": 1}
            await world.q(
                "UPDATE outbound_deliveries SET next_attempt_at=now() WHERE trace_id=%s",
                queued["trace_id"],
            )
            assert await delivery.process_once()
            assert not await delivery.process_once()
        assert calls == 0
        [turn] = await history(world, conversation)
        assert turn["status"] == "available" and len(turn["replies"]) == 1
        counts = await world.q(
            "SELECT direction,count(*) AS n FROM messages WHERE trace_id=%s GROUP BY direction",
            queued["trace_id"],
        )
        assert {row["direction"]: row["n"] for row in counts} == {"inbound": 1, "outbound": 1}
        attempts = await world.q(
            "SELECT a.attempt_no,a.error FROM outbound_delivery_attempts a "
            "JOIN outbound_deliveries d "
            "ON d.id=a.delivery_id WHERE d.trace_id=%s ORDER BY a.attempt_no",
            queued["trace_id"],
        )
        assert (
            len(attempts) == 2
            and attempts[0]["error"] == "OSError"
            and attempts[1]["error"] is None
        )
    finally:
        await checkpoints.close()
        await owner.close()


async def test_web_worker_and_delivery_refuse_wrong_participant(sessions: SessionWorld) -> None:
    world = sessions
    conversation, other = await create(world), await create(world)
    queued = await send(world, conversation)
    [row] = await world.q(
        "SELECT id,client_id,endpoint_id,trace_id,raw,attempts FROM ingress_events "
        "WHERE trace_id=%s",
        queued["trace_id"],
    )
    item = ClaimedIngress.model_validate(row)
    envelope = Envelope.model_validate(item.raw["envelope"])
    worker = IngressWorker(world.pool, world.pool, lock_conninfo=world.owner_dsn)
    for forged in (
        envelope.model_copy(
            update={
                "conversation": envelope.conversation.model_copy(
                    update={"id": UUID(other), "channel_thread": other}
                )
            }
        ),
        envelope.model_copy(
            update={"contact": envelope.contact.model_copy(update={"id": uuid4()})}
        ),
    ):
        with pytest.raises(ValueError, match="sandbox_ingress_participant_invalid"):
            await worker._complete_turn(item, forged)
    await process(world, queued)
    await world.q(
        "UPDATE outbound_deliveries SET conversation_id=%s WHERE trace_id=%s",
        other,
        queued["trace_id"],
    )
    owner = db.build_pool(world.owner_dsn, max_size=2)
    await owner.open(wait=True)
    try:
        async with httpx.AsyncClient() as client:
            delivery = DeliveryWorker(owner, world.pool, client)
            assert await delivery.process_once()
        [turn] = await history(world, conversation)
        assert turn["replies"] == []
        [failed] = await world.q(
            "SELECT status,last_error FROM outbound_deliveries WHERE trace_id=%s",
            queued["trace_id"],
        )
        assert (
            failed["status"] == "pending" and failed["last_error"] == "web_participant_unavailable"
        )
    finally:
        await owner.close()


async def test_web_verification_exempts_only_fixed_system_refusal(sessions: SessionWorld) -> None:
    conversation = await create(sessions)
    queued = await send(sessions, conversation)
    [row] = await sessions.q("SELECT raw FROM ingress_events WHERE trace_id=%s", queued["trace_id"])
    envelope = Envelope.model_validate(row["raw"]["envelope"])
    provider = MagicMock()
    provider.verify.return_value = Verification(ok=False, issues=["ungrounded"])
    deps = PostgresTurnDependencies(sessions.pool, llm=provider)
    ungrounded = TurnState(
        envelope=envelope,
        draft=Draft(
            text="Unsupported product claim",
            language="en",
            composer="template",
        ),
    )
    assert not (await deps.verify(ungrounded)).ok
    provider.verify.assert_called_once()
    provider.reset_mock()
    refusal = ungrounded.model_copy(
        update={
            "draft": Draft(
                text=SANDBOX_SCOPE_TEXT,
                language="en",
                composer="template",
            )
        }
    )
    assert (await deps.verify(refusal)).ok
    provider.verify.assert_not_called()


async def test_web_empty_account_plan_cannot_solicit_customer_records(
    sessions: SessionWorld,
) -> None:
    conversation = await create(sessions)
    queued = await send(sessions, conversation)
    [row] = await sessions.q("SELECT raw FROM ingress_events WHERE trace_id=%s", queued["trace_id"])
    envelope = Envelope.model_validate(row["raw"]["envelope"])
    provider = MagicMock()
    provider.compose = AsyncMock(
        return_value=Draft(
            text="Synthetic returns are seventeen days.", fact_ids=["kb-policy"], language="en"
        )
    )
    deps = PostgresTurnDependencies(sessions.pool, llm=provider)
    for intent in (
        Intent.ORDER,
        Intent.REFUND_RETURNS,
        Intent.BILLING_INVOICE,
        Intent.BOOKING,
        Intent.SUBSCRIPTION,
        Intent.WARRANTY,
        Intent.HUMAN_REQUEST,
    ):
        for strategy in ("ask_clarifying", "confirm"):
            plan = Plan(
                intent=intent, tasks=[], reply_strategy=strategy, confidence=1, source="llm"
            )
            draft = await deps.compose(TurnState(envelope=envelope, plan=plan))
            assert draft.text == SANDBOX_SCOPE_TEXT
    provider.compose.assert_not_awaited()
    # General policy facts retrieved by the allowed KB path remain usable.
    plan = Plan(
        intent=Intent.REFUND_RETURNS,
        tasks=[],
        reply_strategy="auto_reply",
        confidence=1,
        source="llm",
    )
    general = TurnState(
        envelope=envelope,
        plan=plan,
        facts=[
            Fact(id="kb-policy", text="Synthetic returns are seventeen days.", source="kb"),
        ],
    )
    assert (await deps.compose(general)).text == "Synthetic returns are seventeen days."
    provider.compose.assert_awaited_once()


async def test_synthetic_kb_retrieval_reaches_private_web_reply(
    sessions: SessionWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = sessions
    embedder = DefaultKbEmbedder()
    monkeypatch.setattr("csa.turn.deps.KbAgent", lambda: KbAgent(embedder=embedder))
    await world.q(
        "UPDATE capability_registry SET is_enabled=true,client_enabled=true "
        "WHERE client_id=%s AND name='kb_agent'",
        world.tenants[0],
    )
    content = "Synthetic opening hours? Synthetic opening hours are nine to five."
    async with tx(world.pool, world.tenants[0]) as conn:
        await store_document(
            conn,
            client_id=UUID(world.tenants[0]),
            title="Synthetic sandbox hours",
            chunks=[ExtractedChunk(content=content, heading="Opening hours")],
            embeddings=await embedder.embed_batch([content]),
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            embed_model=embedder.model_name,
        )
    conversation = await create(world)
    queued = await send(world, conversation)
    await process(world, queued, mode="kb")
    runs = await world.q(
        "SELECT status,output FROM agent_runs WHERE trace_id=%s AND agent='kb_agent'",
        queued["trace_id"],
    )
    assert len(runs) == 1 and runs[0]["status"] == "ok"
    assert any(fact.startswith("kb_chunk_") for fact in runs[0]["output"]["fact_ids"])
    owner = db.build_pool(world.owner_dsn, max_size=2)
    await owner.open(wait=True)
    calls = 0

    def no_network(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("external request from web sandbox")

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(no_network)) as client:
            assert await DeliveryWorker(owner, world.pool, client).process_once()
    finally:
        await owner.close()
    [turn] = await history(world, conversation)
    assert turn["status"] == "available" and "nine to five" in turn["replies"][0]["body"]
    assert calls == 0
