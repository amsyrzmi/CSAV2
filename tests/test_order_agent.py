"""LG05 order and escalation agents against real Postgres."""

from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any, LiteralString
from zoneinfo import ZoneInfo

import psycopg
import pytest
from csa.agents.base import AgentContext
from csa.agents.escalation.agent import EscalationAgent
from csa.agents.order_approval.agent import OrderApprovalAgent
from csa.agents.orders.agent import OrderAgent
from csa.agents.recorder import PostgresRunRecorder
from csa.agents.runner import SpokeRunner
from csa.agents.tickets import agent as tickets
from csa.agents.tickets.agent import TicketAgent
from csa.contracts.callbacks import CallbackCodec, PendingActionStore
from csa.contracts.envelope import Callback
from csa.contracts.plan import Task
from csa.contracts.state import Context
from csa.core import db
from csa.payments.copy import local_date
from csa.turn.deps import PostgresTurnDependencies

from tests.conftest import owner_dsn_for, requires_docker
from tests.test_coordinator import make_envelope

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


@pytest.mark.parametrize("switch", ["is_enabled", "client_enabled"])
async def test_runtime_denies_disabled_order_drafts_and_existing_callback_actions(
    world: World, switch: str
) -> None:
    envelope = make_envelope()
    envelope = envelope.model_copy(
        update={
            "client_id": world.client_id,
            "trace_id": uuid.uuid4(),
            "contact": envelope.contact.model_copy(update={"id": world.contact_id}),
            "conversation": envelope.conversation.model_copy(update={"id": world.conversation_id}),
        }
    )
    first_worker = PostgresTurnDependencies(world.pool)
    second_worker = PostgresTurnDependencies(world.pool)
    draft_task = Task(
        task_id="enabled-draft",
        agent="order_agent",
        input={"action": "create_draft", "items": [{"sku": "A1", "qty": 1}]},
    )
    result = await first_worker.execute_spoke(draft_task, envelope, Context())
    assert result.ok and result.data.ui.confirmation is not None
    # Sent as it stands on a payment turn: it reads to the customer.
    assert result.data.facts[0].text.endswith(". It is not placed until you confirm it.")
    callback = result.data.ui.confirmation.accept.action_id
    assert callback is not None
    action_id = CallbackCodec.decode(callback)
    assert action_id is not None
    callback_envelope = envelope.model_copy(update={"callback": Callback(action_id=action_id)})
    callback_task = await first_worker.resolve_callback(callback_envelope, Context())
    assert callback_task is not None
    if switch == "is_enabled":
        world.q(
            "UPDATE capability_registry SET is_enabled=false "
            "WHERE client_id=%s AND name='order_agent' RETURNING id",
            world.client_id,
        )
    else:
        world.q(
            "UPDATE capability_registry SET client_enabled=false WHERE client_id=%s "
            "AND name='order_agent' RETURNING id",
            world.client_id,
        )
    for worker in (first_worker, second_worker):
        denied = await worker.execute_spoke(draft_task, envelope, Context())
        assert denied.error is not None and denied.error.code == "capability_unavailable"
        denied_callback = await worker.execute_spoke(callback_task, callback_envelope, Context())
        assert denied_callback.error is not None
        assert denied_callback.error.code == "capability_unavailable"
    assert world.reserved("A1") == 1
    assert (
        world.q(
            "SELECT status FROM orders WHERE client_id=%s AND id=%s",
            world.client_id,
            result.data.ui.confirmation.order_id,
        )[0][0]
        == "draft"
    )
    assert (
        world.q(
            "SELECT count(*) FROM agent_runs "
            "WHERE client_id=%s AND error_code='capability_unavailable'",
            world.client_id,
        )[0][0]
        == 4
    )


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
    # Without a marketplace reader the availability read answers from our shop alone.
    availability = await world.runner.execute(
        Task(
            task_id="av",
            agent="order_agent",
            input={"action": "product_availability", "query": "Product"},
        ),
        world.ctx("av"),
    )
    assert availability.ok
    assert [f.id for f in availability.data.facts] == ["shop_product_0", "shop_product_1"]
    assert all(f.text.endswith(" in our shop.") for f in availability.data.facts)
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
    assert ask.data.facts[0].text == (
        f'Cancel ticket #{ticket_id}? Tap "Yes, cancel it" to confirm. '
        "Nothing has been cancelled yet."
    )
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


_FORMAL_MALAY = re.compile(r"\b(?:anda|sila)\b|pihak kami", re.IGNORECASE)


def _in_malay(fact: Any) -> str:
    """The fact's Malay text: set, holding none of the fact's English sentences and none of the
    formal words the customer copy avoids."""
    assert fact.text_ms, fact.id
    for sentence in re.split(r"(?<=[.?!])\s+", fact.text):
        assert sentence.strip() not in fact.text_ms, (fact.id, sentence)
    assert _FORMAL_MALAY.search(fact.text_ms) is None, fact.text_ms
    return str(fact.text_ms)


async def test_ticket_write_facts_carry_a_malay_text(world: World) -> None:
    # Every fact a ticket write gives keeps today's English text and carries a Malay one, for a
    # Malay turn where the facts go out word for word (a ticket written beside a payment answer).
    created = await _ticket(
        world, "create", "m1", subject="Kad ditolak", category="billing", priority="urgent"
    )
    fact = created.data.facts[0]
    n = int(fact.id.split("_")[1])
    due = world.q("SELECT sla_due_at FROM tickets WHERE id=%s", n)[0][0]
    zone = ZoneInfo("Asia/Kuala_Lumpur")
    assert fact.text == (
        f"Ticket #{n} created (billing, high priority), "
        f"target response by {due.astimezone(zone):%d %b %Y}."
    )
    assert _in_malay(fact) == (
        f"Tiket #{n} dah dibuat (bil, keutamaan tinggi), sasaran balasan paling lewat "
        f"{local_date(due, 'ms', zone)}."
    )

    dup = (await _ticket(world, "create", "m2", subject="kad ditolak", category="billing")).data
    assert dup.facts[0].text == f"Ticket #{n} is open."
    assert _in_malay(dup.facts[0]) == f"Tiket #{n} masih terbuka."

    note = (await _ticket(world, "add_note", "m3", ticket_id=n, note="dua kali")).data.facts[0]
    assert note.text == "Your note was added."
    assert _in_malay(note) == "Nota awak dah ditambah."

    ask = await _ticket(world, "cancel", "m4", ticket_id=n)
    assert ask.data.facts[0].text == (
        f'Cancel ticket #{n}? Tap "Yes, cancel it" to confirm. Nothing has been cancelled yet.'
    )
    # The button keeps its label, so the Malay text names it as the customer sees it.
    assert _in_malay(ask.data.facts[0]) == (
        f'Nak batalkan tiket #{n}? Tekan "Yes, cancel it" untuk sahkan. '
        "Belum ada apa-apa yang dibatalkan lagi."
    )
    assert [o.label for o in ask.data.ui.options] == ["Yes, cancel it"]
    yes = ask.data.ui.options[0].action_id
    assert await world.store.consume(yes, client_id=world.client_id, contact_id=world.contact_id)
    done = await world.runner.execute(
        Task(
            task_id="t",
            agent="ticket_agent",
            input={
                "action": "cancel",
                "ticket_id": n,
                "callback_id": str(CallbackCodec.decode(yes)),
            },
        ),
        world.ctx("m5", callback=yes),
    )
    assert done.data.facts[0].text == f"Ticket #{n} was cancelled."
    assert _in_malay(done.data.facts[0]) == f"Tiket #{n} dah dibatalkan."

    again = (await _ticket(world, "cancel", "m6", ticket_id=n)).data.facts[0]
    assert again.text == f"Ticket #{n} is already cancelled."
    assert _in_malay(again) == f"Tiket #{n} dah pun dibatalkan."

    reopened = (await _ticket(world, "reopen", "m7", ticket_id=n)).data.facts[0]
    assert reopened.text == f"Ticket #{n} reopened."
    assert _in_malay(reopened) == f"Tiket #{n} dah dibuka semula."

    still = (await _ticket(world, "reopen", "m8", ticket_id=n)).data.facts[0]
    assert still.text == f"Ticket #{n} is already open."
    assert _in_malay(still) == f"Tiket #{n} memang masih terbuka."

    # Reads never go out word for word (a payment reply leaves them out): English only.
    looked = (await _ticket(world, "get", "m9", ticket_id=n)).data.facts[0]
    assert looked.text_ms is None and looked.text.startswith(f"Ticket #{n}: Kad ditolak")


async def test_every_ticket_category_and_priority_has_a_malay_word() -> None:
    assert set(tickets._CATEGORY_MS) == set(tickets.CATEGORIES)
    assert set(tickets._PRIORITY_MS) == set(tickets.DEFAULT_SLA_HOURS)
    words = {*tickets._CATEGORY_MS.values(), *tickets._PRIORITY_MS.values()}
    assert "" not in words and not words & {*tickets.CATEGORIES, *tickets.DEFAULT_SLA_HOURS}


async def _again(world: World, action: str, order_id: str, callback: str) -> Any:
    """The same button's task run a second time (a double tap), its action already used."""
    return await world.runner.execute(
        Task(
            task_id=f"cb-{action}",
            agent="order_agent",
            input={
                "action": action,
                "order_id": order_id,
                "callback_id": str(CallbackCodec.decode(callback)),
            },
        ),
        world.ctx(f"again-{callback}", callback=callback),
    )


async def test_order_write_facts_carry_a_malay_text(world: World) -> None:
    # Every fact an order write gives keeps today's English text and carries a Malay one, for a
    # Malay turn where the facts go out word for word (an order drafted beside a payment answer).
    draft = await world.draft("om1", [{"sku": "B2", "qty": 2}, {"sku": "A1", "qty": 1}])
    conf = draft.data.ui.confirmation
    n = conf.order_id
    fact = draft.data.facts[0]
    assert fact.text == (
        f"Draft order {n}: 1 x Product A1, 2 x Product B2, total MYR 19.00. "
        "It is not placed until you confirm it."
    )
    # The item names, quantities and the total stay exactly as the English writes them.
    assert _in_malay(fact) == (
        f"Draf pesanan {n}: 1 x Product A1, 2 x Product B2, jumlah MYR 19.00. "
        "Pesanan ni belum dibuat lagi selagi awak tak sahkan."
    )
    # The buttons keep their English labels; the Malay text does not name them.
    assert (conf.accept.label, conf.reject.label) == ("Confirm order", "Cancel")

    confirmed = (await world.tap(conf.accept.action_id, "confirm", n)).data.facts[0]
    assert confirmed.text == (
        f"Order {n} confirmed, total MYR 19.00, payment on delivery. Awaiting fulfilment."
    )
    assert _in_malay(confirmed) == (
        f"Pesanan {n} dah disahkan, jumlah MYR 19.00, bayar bila barang sampai. "
        "Sekarang tunggu kami proses ya."
    )
    already = (await _again(world, "confirm", n, conf.accept.action_id)).data.facts[0]
    assert already.text == f"Order {n} is already confirmed."
    assert _in_malay(already) == f"Pesanan {n} dah pun disahkan."

    other = (await world.draft("om2", [{"sku": "B2", "qty": 1}])).data.ui.confirmation
    m = other.order_id
    cancelled = (await world.tap(other.reject.action_id, "cancel", m)).data.facts[0]
    assert cancelled.text == f"Order {m} has been cancelled."
    assert _in_malay(cancelled) == f"Pesanan {m} dah dibatalkan."
    again = (await _again(world, "cancel", m, other.reject.action_id)).data.facts[0]
    assert again.text == f"Order {m} is already cancelled."
    assert _in_malay(again) == f"Pesanan {m} dah pun dibatalkan."

    # Reads never go out word for word (a payment reply leaves them out): English only.
    status = await world.runner.execute(
        Task(task_id="s", agent="order_agent", input={"action": "status", "order_id": n}),
        world.ctx("om3"),
    )
    assert status.data.facts[0].text.startswith(f"Order {n}: pending_confirmation")
    search = await world.runner.execute(
        Task(
            task_id="q",
            agent="order_agent",
            input={"action": "search_products", "query": "Product"},
        ),
        world.ctx("om4"),
    )
    assert [f.text_ms for f in (*status.data.facts, *search.data.facts)] == [None, None, None]


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
