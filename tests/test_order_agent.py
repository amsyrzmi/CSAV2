"""LG05 order and escalation agents against real Postgres."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, LiteralString

import psycopg
import pytest
from csa.agents.base import AgentContext
from csa.agents.escalation.agent import EscalationAgent
from csa.agents.order_approval.agent import OrderApprovalAgent
from csa.agents.orders.agent import OrderAgent
from csa.agents.recorder import PostgresRunRecorder
from csa.agents.runner import SpokeRunner
from csa.agents.tickets.agent import TicketAgent
from csa.contracts.callbacks import CallbackCodec, PendingActionStore
from csa.contracts.plan import Task
from csa.core import db

from tests.conftest import owner_dsn_for, requires_docker

pytestmark = [requires_docker, pytest.mark.asyncio]


class World:
    def __init__(self, owner_dsn: str, app_pool: Any) -> None:
        self.owner_dsn = owner_dsn
        self.pool = app_pool
        self.store = PendingActionStore(app_pool)
        self.client_id = uuid.uuid4()
        self.contact_id = uuid.uuid4()
        self.other_contact_id = uuid.uuid4()
        self.conversation_id = uuid.uuid4()
        self.runner = SpokeRunner(
            {
                "order_agent": OrderAgent(),
                "escalation_agent": EscalationAgent(),
                "ticket_agent": TicketAgent(),
                "order_approval": OrderApprovalAgent(),
            },
            recorder=PostgresRunRecorder(app_pool),
        )
        with psycopg.connect(owner_dsn, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO clients (id, slug, name, platform_version) VALUES (%s,%s,%s,%s)",
                (self.client_id, f"o-{self.client_id}", "Synthetic Orders", "lg05"),
            )
            for cid, tg in ((self.contact_id, 601), (self.other_contact_id, 602)):
                conn.execute(
                    "INSERT INTO contacts (id, client_id, telegram_id, full_name) "
                    "VALUES (%s,%s,%s,'Synthetic')",
                    (cid, self.client_id, tg),
                )
            conn.execute(
                "INSERT INTO conversations (id, client_id, contact_id, channel, channel_thread) "
                "VALUES (%s,%s,%s,'telegram','601')",
                (self.conversation_id, self.client_id, self.contact_id),
            )
            conn.execute("SELECT public.seed_core_capabilities(%s)", (self.client_id,))
            for sku, price, stock in (("A1", "10.00", 5), ("B2", "4.50", 100)):
                conn.execute(
                    "INSERT INTO products (client_id, sku, name, price, stock_qty) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (self.client_id, sku, f"Product {sku}", price, stock),
                )

    def ctx(
        self,
        key: str,
        *,
        contact: uuid.UUID | None = None,
        callback: str | None = None,
    ) -> AgentContext:
        return AgentContext(
            client_id=self.client_id,
            contact_id=contact or self.contact_id,
            trace_id=uuid.uuid4(),
            conversation_id=self.conversation_id,
            idempotency_key=key,
            channel="telegram",
            pool=self.pool,
            pending_actions=self.store,
            callback_action_id=CallbackCodec.decode(callback) if callback else None,
        )

    def q(self, sql: LiteralString, *args: Any) -> list[tuple[Any, ...]]:
        with psycopg.connect(self.owner_dsn) as conn:
            return conn.execute(sql, args).fetchall()

    async def draft(self, key: str, items: list[dict[str, Any]]) -> Any:
        return await self.runner.execute(
            Task(
                task_id="draft",
                agent="order_agent",
                input={"action": "create_draft", "items": items},
            ),
            self.ctx(key),
        )

    async def tap(
        self, callback: str, action: str, order_id: str, *, contact: uuid.UUID | None = None
    ) -> Any:
        who = contact or self.contact_id
        pa = await self.store.consume(callback, client_id=self.client_id, contact_id=who)
        assert pa is not None or contact is not None
        cb_id = CallbackCodec.decode(callback)
        return await self.runner.execute(
            Task(
                task_id=f"cb-{action}",
                agent="order_agent",
                input={"action": action, "order_id": order_id, "callback_id": str(cb_id)},
            ),
            self.ctx(f"tap-{callback}", contact=who, callback=callback),
        )

    def reserved(self, sku: str) -> int:
        return self.q(
            "SELECT reserved_qty FROM products WHERE client_id=%s AND sku=%s",
            self.client_id,
            sku,
        )[0][0]


@pytest.fixture
async def world(migrated_dsn: str, pg_container: object, app_dsn: str) -> Any:
    pool = db.build_pool(app_dsn, max_size=8)
    await pool.open(wait=True)
    yield World(owner_dsn_for(pg_container), pool)
    await pool.close()


def _order_id(result: Any) -> str:
    return result.data.ui.confirmation.order_id


async def test_draft_reserves_stock_and_offers_buttons(world: World) -> None:
    result = await world.draft("m1", [{"sku": "A1", "qty": 2}])
    assert result.ok
    assert result.data.ui.confirmation is not None
    assert world.reserved("A1") == 2
    assert world.q("SELECT count(*) FROM agent_runs WHERE client_id=%s", world.client_id)[0][0] >= 1


async def test_duplicate_sku_lines_reserve_aggregate_and_check_total(world: World) -> None:
    ok = await world.draft("dup1", [{"sku": "A1", "qty": 1}, {"sku": "A1", "qty": 1}])
    assert ok.ok
    assert world.reserved("A1") == 2
    too_many = await world.draft("dup2", [{"sku": "A1", "qty": 2}, {"sku": "A1", "qty": 2}])
    assert too_many.error is not None and too_many.error.code == "insufficient_stock"
    assert world.reserved("A1") == 2


async def test_concurrent_identical_requests_create_one_draft(world: World) -> None:
    results = await asyncio.gather(
        *(world.draft("same", [{"sku": "B2", "qty": 3}]) for _ in range(4))
    )
    assert all(r.ok for r in results)
    assert len({_order_id(r) for r in results}) == 1
    assert world.q("SELECT count(*) FROM orders WHERE client_id=%s", world.client_id)[0][0] == 1
    assert world.reserved("B2") == 3


async def test_confirm_button_and_double_tap(world: World) -> None:
    draft = await world.draft("c1", [{"sku": "B2", "qty": 1}])
    conf = draft.data.ui.confirmation
    first = await world.tap(conf.accept.action_id, "confirm", conf.order_id)
    assert first.ok
    row = world.q("SELECT status, payment_status FROM orders WHERE id=%s", conf.order_id)[0]
    assert row == ("pending_confirmation", "cod_pending")
    replay = await world.runner.execute(
        Task(
            task_id="cb-confirm",
            agent="order_agent",
            input={
                "action": "confirm",
                "order_id": conf.order_id,
                "callback_id": str(CallbackCodec.decode(conf.accept.action_id)),
            },
        ),
        world.ctx("tap-again", callback=conf.accept.action_id),
    )
    assert replay.ok and replay.meta.was_duplicate
    assert world.reserved("B2") == 1


async def test_cancel_releases_reservation_once(world: World) -> None:
    draft = await world.draft("x1", [{"sku": "A1", "qty": 3}])
    conf = draft.data.ui.confirmation
    assert (await world.tap(conf.reject.action_id, "cancel", conf.order_id)).ok
    assert world.reserved("A1") == 0
    assert world.q("SELECT status FROM orders WHERE id=%s", conf.order_id)[0][0] == "cancelled"


async def test_wrong_contact_cannot_use_button(world: World) -> None:
    draft = await world.draft("w1", [{"sku": "B2", "qty": 1}])
    conf = draft.data.ui.confirmation
    pa = await world.store.consume(
        conf.accept.action_id, client_id=world.client_id, contact_id=world.other_contact_id
    )
    assert pa is None
    forged = await world.runner.execute(
        Task(
            task_id="forged",
            agent="order_agent",
            input={
                "action": "confirm",
                "order_id": conf.order_id,
                "callback_id": str(CallbackCodec.decode(conf.accept.action_id)),
            },
        ),
        world.ctx("forge", contact=world.other_contact_id, callback=conf.accept.action_id),
    )
    assert forged.error is not None and forged.error.code == "not_authorised"
    assert world.q("SELECT status FROM orders WHERE id=%s", conf.order_id)[0][0] == "draft"


async def test_confirm_without_callback_is_rejected(world: World) -> None:
    draft = await world.draft("n1", [{"sku": "B2", "qty": 1}])
    result = await world.runner.execute(
        Task(
            task_id="t",
            agent="order_agent",
            input={
                "action": "confirm",
                "order_id": _order_id(draft),
                "callback_id": str(uuid.uuid4()),
            },
        ),
        world.ctx("nocb"),
    )
    assert result.error is not None and result.error.code == "not_authorised"


async def test_status_search_detail_and_unknown(world: World) -> None:
    draft = await world.draft("s1", [{"sku": "B2", "qty": 1}])
    status = await world.runner.execute(
        Task(task_id="s", agent="order_agent", input={"action": "status"}), world.ctx("st")
    )
    assert status.ok and status.data.facts[0].id == "no_orders"
    explicit = await world.runner.execute(
        Task(
            task_id="explicit",
            agent="order_agent",
            input={"action": "status", "order_id": draft.data.ui.confirmation.order_id},
        ),
        world.ctx("explicit"),
    )
    assert explicit.ok and "draft" in explicit.data.facts[0].text
    search = await world.runner.execute(
        Task(
            task_id="q",
            agent="order_agent",
            input={"action": "search_products", "query": "Product"},
        ),
        world.ctx("sr"),
    )
    assert len(search.data.facts) == 2
    detail = await world.runner.execute(
        Task(task_id="d", agent="order_agent", input={"action": "product_detail", "sku": "ZZ"}),
        world.ctx("pd"),
    )
    assert detail.error is not None and detail.error.category == "not_found"
    bad = await world.runner.execute(
        Task(task_id="b", agent="order_agent", input={"action": "refund_everything"}),
        world.ctx("bad"),
    )
    assert bad.error is not None and bad.error.category == "validation"


async def test_escalation_mutes_conversation_idempotently(world: World) -> None:
    for _ in range(2):
        result = await world.runner.execute(
            Task(task_id="esc", agent="escalation_agent", input={"reason": "angry"}),
            world.ctx("esc-1"),
        )
        assert result.ok
    muted = world.q(
        "SELECT ai_muted_until > now(), status FROM conversations WHERE id=%s",
        world.conversation_id,
    )[0]
    assert muted == (True, "escalated")


def _seed_target(world: World, events: str = "{escalation,approval_needed}") -> uuid.UUID:
    target_id = uuid.uuid4()
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO staff_notification_targets (id, client_id, channel, address, events) "
            "VALUES (%s,%s,'telegram','999',%s::text[])",
            (target_id, world.client_id, events),
        )
    return target_id


async def test_seed_function_creates_capability_rows(world: World) -> None:
    names = {
        r[0]
        for r in world.q("SELECT name FROM capability_registry WHERE client_id=%s", world.client_id)
    }
    assert {"order_agent", "escalation_agent", "ticket_agent", "order_approval"} <= names


async def test_escalation_creates_ticket_notifies_once_and_reuses_ticket(world: World) -> None:
    _seed_target(world)
    first = await world.runner.execute(
        Task(task_id="e", agent="escalation_agent", input={"reason": "angry"}), world.ctx("e1")
    )
    assert first.ok and "ticket #" in first.data.facts[0].text
    again = await world.runner.execute(
        Task(task_id="e", agent="escalation_agent", input={"reason": "angry"}), world.ctx("e1")
    )
    assert again.ok
    assert world.q("SELECT count(*) FROM tickets WHERE client_id=%s", world.client_id)[0][0] == 1
    assert (
        world.q("SELECT count(*) FROM staff_notifications WHERE client_id=%s", world.client_id)[0][
            0
        ]
        <= 2
    )


async def test_notifier_respects_event_filter_and_dedupes(world: World) -> None:
    from csa.core.tenancy import tx
    from csa.notify.notifier import notify

    _seed_target(world, "{dlq}")
    async with tx(world.pool, str(world.client_id)) as conn:
        assert (
            await notify(
                conn,
                client_id=world.client_id,
                event="escalation",
                priority="normal",
                payload={},
                dedupe_key="k",
            )
            == 0
        )
        args = {"client_id": world.client_id, "event": "dlq", "priority": "high", "payload": {}}
        assert await notify(conn, dedupe_key="k", **args) == 1
        assert await notify(conn, dedupe_key="k", **args) == 0


async def _ticket(world: World, action: str, key: str, **inp: Any) -> Any:
    return await world.runner.execute(
        Task(task_id="t", agent="ticket_agent", input={"action": action, **inp}), world.ctx(key)
    )


async def test_ticket_lifecycle_with_confirmed_cancel(world: World) -> None:
    created = await _ticket(
        world, "create", "t1", subject="Parcel late", category="shipping", priority="urgent"
    )
    assert created.ok
    ticket_id = int(created.data.facts[0].id.split("_")[1])
    assert world.q("SELECT priority FROM tickets WHERE id=%s", ticket_id)[0][0] == "high"
    dup = await _ticket(world, "create", "t2", subject="parcel late", category="shipping")
    assert dup.meta.was_duplicate
    assert (await _ticket(world, "add_note", "t3", ticket_id=f"#{ticket_id}", note="hello")).ok
    ask = await _ticket(world, "delete", "t4", ticket_id=ticket_id)
    assert "NOT cancelled" in ask.data.facts[0].text
    assert world.q("SELECT status FROM tickets WHERE id=%s", ticket_id)[0][0] == "open"
    yes = ask.data.ui.options[0].action_id
    pa = await world.store.consume(yes, client_id=world.client_id, contact_id=world.contact_id)
    assert pa is not None
    done = await world.runner.execute(
        Task(
            task_id="t",
            agent="ticket_agent",
            input={
                "action": "cancel",
                "ticket_id": ticket_id,
                "callback_id": str(CallbackCodec.decode(yes)),
            },
        ),
        world.ctx("t5", callback=yes),
    )
    assert done.ok
    assert world.q("SELECT status FROM tickets WHERE id=%s", ticket_id)[0][0] == "cancelled"
    assert (await _ticket(world, "reopen", "t6", ticket_id=ticket_id)).ok
    other = await world.runner.execute(
        Task(task_id="t", agent="ticket_agent", input={"action": "get", "ticket_id": ticket_id}),
        world.ctx("t7", contact=world.other_contact_id),
    )
    assert other.error is not None and other.error.code == "ticket_not_found"


async def _approve(world: World, action: str, order_id: str, target: uuid.UUID, key: str) -> Any:
    return await world.runner.execute(
        Task(
            task_id="a",
            agent="order_approval",
            input={"action": action, "order_id": order_id, "staff_target_id": str(target)},
        ),
        world.ctx(key),
    )


async def _pending_order(world: World, key: str, sku: str, qty: int) -> str:
    draft = await world.draft(key, [{"sku": sku, "qty": qty}])
    conf = draft.data.ui.confirmation
    assert (await world.tap(conf.accept.action_id, "confirm", conf.order_id)).ok
    return conf.order_id


async def test_staff_approve_commits_stock_and_reject_releases(world: World) -> None:
    target = _seed_target(world)
    approved = await _pending_order(world, "ap1", "A1", 2)
    assert (await _approve(world, "approve", approved, target, "s1")).ok
    assert world.q(
        "SELECT stock_qty, reserved_qty FROM products WHERE client_id=%s AND sku='A1'",
        world.client_id,
    )[0] == (3, 0)
    assert world.q("SELECT status FROM orders WHERE id=%s", approved)[0][0] == "confirmed"
    replay = await _approve(world, "approve", approved, target, "s2")
    assert replay.ok and replay.meta.was_duplicate
    assert (
        world.q("SELECT stock_qty FROM products WHERE client_id=%s AND sku='A1'", world.client_id)[
            0
        ][0]
        == 3
    )
    rejected = await _pending_order(world, "ap2", "A1", 1)
    assert world.reserved("A1") == 1
    assert (await _approve(world, "reject", rejected, target, "s3")).ok
    assert world.reserved("A1") == 0


async def test_staff_approval_requires_known_target(world: World) -> None:
    order_id = await _pending_order(world, "ap3", "B2", 1)
    result = await _approve(world, "approve", order_id, uuid.uuid4(), "s4")
    assert result.error is not None and result.error.code == "staff_not_authorised"
    assert (
        world.q("SELECT status FROM orders WHERE id=%s", order_id)[0][0] == "pending_confirmation"
    )
