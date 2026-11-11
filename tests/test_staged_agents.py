"""Wave 2/3 preparation against synthetic data in a disposable database."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import psycopg
import pytest
from csa.agents.base import AgentContext
from csa.agents.runner import SpokeRunner
from csa.agents.staged.models import (
    BookingInput,
    EmailDraftInput,
    InvoiceDraftInput,
    InvoiceLine,
    ResearchInput,
    SubscriptionInput,
)
from csa.agents.staged.service import (
    BookingAgent,
    StaffActor,
    StaffPreparationService,
    SubscriptionAgent,
    resolve_subscription,
)
from csa.contracts.callbacks import PendingActionStore
from csa.contracts.plan import Task
from csa.core import db
from csa.core.tenancy import tx
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from pydantic import ValidationError

from tests.conftest import requires_docker


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "book"},
        {"action": "book", "type_id": str(uuid4())},
        {"action": "book", "starts_at": "2030-01-01T09:00"},
        {"action": "reschedule", "booking_id": str(uuid4())},
        {"action": "reschedule", "starts_at": "2030-01-01T09:00"},
        {"action": "cancel"},
        {"action": "status"},
        {"action": "save_details"},
        {"action": "save_details", "customer_name": " "},
        {"action": "request_book"},
        {"action": "request_cancel", "booking_id": str(uuid4())},
        {"action": "list", "client_id": str(uuid4())},
        {"action": "list", "contact_id": str(uuid4())},
        {"action": "book", "type_id": "haircut", "starts_at": "2030-01-01T09:00"},
        {"action": "availability", "customer_email": "not an address"},
        {"action": "availability", "customer_name": "Aina\nBcc: other@example.test"},
        {"action": "availability", "day": "next tuesday"},
    ],
)
def test_invalid_booking(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        BookingInput.model_validate(payload)


def test_the_booking_input_names_only_the_new_actions() -> None:
    schema = BookingInput.model_json_schema()
    assert schema["properties"]["action"]["enum"] == [
        "availability",
        "book",
        "reschedule",
        "cancel",
        "list",
        "status",
        "save_details",
    ]
    assert set(schema["properties"]) == {
        "action",
        "type_id",
        "day",
        "part_of_day",
        "starts_at",
        "booking_id",
        "confirmed",
        "customer_name",
        "customer_email",
    }
    text = json.dumps(schema)
    for old in ("request_book", "request_reschedule", "request_cancel", "ends_at", "timezone"):
        assert old not in text
    # The planner is shown the schema as it is, so every field explains itself.
    assert all(p.get("description") for p in schema["properties"].values())


def test_a_booking_time_with_no_offset_is_the_business_local_time() -> None:
    naive = BookingInput.model_validate(
        {"action": "book", "type_id": str(uuid4()), "starts_at": "2030-01-01T09:00"}
    )
    assert naive.starts_at == datetime(2030, 1, 1, 9, 0)
    assert naive.starts_at is not None and naive.starts_at.tzinfo is None
    aware = BookingInput.model_validate(
        {"action": "book", "type_id": str(uuid4()), "starts_at": "2030-01-01T09:00+08:00"}
    )
    assert aware.starts_at is not None and aware.starts_at.utcoffset() is not None


def test_a_model_that_fills_unused_fields_with_blanks_is_understood() -> None:
    inp = BookingInput.model_validate(
        {
            "action": "list",
            "type_id": "",
            "day": " ",
            "starts_at": "",
            "booking_id": None,
            "customer_name": "",
            "customer_email": "",
        }
    )
    assert inp.model_dump(exclude_none=True) == {"action": "list"}


def test_saving_details_is_a_commit_that_has_something_to_save() -> None:
    only_email = BookingInput(action="save_details", customer_email="aina@example.test")
    only_name = BookingInput(action="save_details", customer_name="Aina Binti Test")
    agent = BookingAgent()
    assert agent.risk(only_email) == "commit" and agent.risk(only_name) == "commit"
    assert agent.ACTION_RISK["save_details"] == "commit"
    assert (agent.side_effects, agent.max_retries, agent.timeout_ms) == ("irreversible", 0, 10000)


def test_a_customer_email_and_name_are_checked_and_trimmed() -> None:
    inp = BookingInput.model_validate(
        {
            "action": "availability",
            "customer_name": "  Aina Binti Test  ",
            "customer_email": " aina@example.test ",
        }
    )
    assert (inp.customer_name, inp.customer_email) == ("Aina Binti Test", "aina@example.test")


@pytest.mark.parametrize("given", ["[EMAIL_0]", " [EMAIL_12] "])
def test_a_planned_email_may_be_the_placeholder_the_planner_saw(given: str) -> None:
    """The planner only sees redacted text; the server swaps the placeholder for the address."""
    inp = BookingInput.model_validate({"action": "save_details", "customer_email": given})
    assert inp.customer_email == given.strip()


def booking_task(**fields: object) -> Task:
    return Task(task_id="t0-booking_agent", agent="booking_agent", input=dict(fields))


def test_the_server_swaps_a_placeholder_for_the_address_in_the_raw_message() -> None:
    from csa.turn.deps import with_real_email

    raw = "it is b@test.example, or a@test.example"
    swapped = with_real_email(booking_task(action="save_details", customer_email="[EMAIL_1]"), raw)
    assert swapped is not None and swapped.input["customer_email"] == "a@test.example"
    typed = with_real_email(
        booking_task(action="save_details", customer_email="B@test.example"), raw
    )
    assert typed is not None and typed.input["customer_email"] == "b@test.example"
    other = booking_task(action="list")
    assert with_real_email(other, raw) is other


def test_an_address_not_in_the_message_is_dropped_and_an_empty_save_is_no_task() -> None:
    from csa.turn.deps import with_real_email

    invented = booking_task(action="save_details", customer_email="guess@test.example")
    assert with_real_email(invented, "no address") is None  # nothing left to save
    named = booking_task(
        action="save_details", customer_name="Aina Binti Test", customer_email="[EMAIL_0]"
    )
    kept = with_real_email(named, "no address")
    assert kept is not None and kept.input == {
        "action": "save_details",
        "customer_name": "Aina Binti Test",
    }
    book = booking_task(action="book", type_id=str(uuid4()), customer_email="[EMAIL_3]")
    booked = with_real_email(book, "x@test.example")
    assert booked is not None and "customer_email" not in booked.input


@pytest.mark.parametrize("given", ["[EMAIL_]", "[PHONE_0]", "[EMAIL_0] x", "[email_0]"])
def test_a_placeholder_must_be_exactly_an_email_placeholder(given: str) -> None:
    with pytest.raises(ValidationError):
        BookingInput.model_validate({"action": "save_details", "customer_email": given})


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://127.0.0.1",
        "https://[::1]",
        "https://user:pw@example.com",
        "https://metadata.internal",
        "https://localhost",
        "https://example.com:8443",
        "https://example.com/#fragment",
        "https://foo.local",
    ],
)
def test_research_rejects_obviously_unsafe_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        ResearchInput(urls=[url], purpose="Synthetic research")


@pytest.mark.parametrize(
    "values",
    [
        {"subject": "Header\r\nBcc: hidden@example.test"},
        {"recipient": "a@example.test,b@example.test"},
        {"recipient": "Name <a@example.test>"},
        {"body": " "},
        {"body": "x" * 20001},
        {"body": "NUL\x00content"},
    ],
)
def test_email_input_bounds(values: dict[str, str]) -> None:
    payload = {
        "mailbox_connection_id": uuid4(),
        "thread_id": "thread",
        "recipient": "a@example.test",
        "subject": "Test",
        "body": "Test",
    }
    with pytest.raises(ValidationError):
        EmailDraftInput.model_validate({**payload, **values})


def test_invoice_decimal_calculation() -> None:
    inp = InvoiceDraftInput(
        action="draft_create",
        lines=[
            InvoiceLine(
                description="Synthetic",
                quantity=Decimal("3"),
                unit_price=Decimal("0.05"),
                tax_rate=Decimal("0.1"),
            )
        ],
    )
    assert inp.total == Decimal("0.17")
    assert inp.payload()["total"] == "0.17"


def test_invoice_persisted_amount_boundary() -> None:
    large = InvoiceLine(
        description="Large",
        quantity=Decimal("999.999"),
        unit_price=Decimal("10000000"),
        tax_rate=Decimal("0"),
    )
    remainder = InvoiceLine(
        description="Remainder",
        quantity=Decimal("1"),
        unit_price=Decimal("9999.99"),
        tax_rate=Decimal("0"),
    )
    assert InvoiceDraftInput(action="draft_create", lines=[large, remainder]).total == Decimal(
        "9999999999.99"
    )
    with pytest.raises(ValidationError, match="amount bound"):
        InvoiceDraftInput(
            action="draft_create",
            lines=[
                large,
                remainder,
                InvoiceLine(
                    description="Over",
                    quantity=Decimal("1"),
                    unit_price=Decimal("0.01"),
                    tax_rate=Decimal("0"),
                ),
            ],
        )


@pytest.mark.parametrize(
    "values",
    [
        {"quantity": "-1"},
        {"unit_price": "NaN"},
        {"unit_price": "1.001"},
        {"tax_rate": "1.01"},
        {"description": " "},
    ],
)
def test_invoice_rejects_invalid_lines(values: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        InvoiceLine.model_validate(
            {
                "description": "Synthetic",
                "quantity": "1",
                "unit_price": "1.00",
                "tax_rate": "0",
                **values,
            }
        )


class World:
    def __init__(self, ctx: AgentContext, owner_dsn: str) -> None:
        self.ctx = ctx
        self.owner_dsn = owner_dsn
        self.other_contact = uuid4()
        self.other_client = uuid4()
        self.subscription_id = uuid4()
        self.mailbox_id = uuid4()

    def actor(self, role: str = "tenant_staff") -> StaffActor:
        return StaffActor.model_validate(
            {
                "client_id": self.ctx.client_id,
                "actor_id": "synthetic-staff",
                "role": role,
            }
        )

    async def rows(self) -> list[dict[str, object]]:
        async with tx(self.ctx.pool, str(self.ctx.client_id)) as conn:
            cursor = await conn.execute(
                "SELECT * FROM agent_preparation_requests WHERE client_id = %s",
                (self.ctx.client_id,),
            )
            return await cursor.fetchall()


@pytest.fixture
async def world(migrated_dsn: str, app_dsn: str) -> AsyncIterator[World]:
    pool = db.build_pool(app_dsn, max_size=4)
    await pool.open(wait=True)
    ctx = AgentContext(
        client_id=uuid4(),
        contact_id=uuid4(),
        trace_id=uuid4(),
        conversation_id=None,
        idempotency_key="synthetic-request",
        channel="test",
        pool=pool,
        pending_actions=PendingActionStore(pool),
        now=lambda: datetime(2029, 1, 1, tzinfo=UTC),
    )
    w = World(ctx, migrated_dsn)
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        for client in (ctx.client_id, w.other_client):
            await conn.execute(
                "INSERT INTO clients (id,slug,name,platform_version) "
                "VALUES (%s,%s,'Synthetic waves','lg13')",
                (client, f"wave-{client}"),
            )
            await conn.execute("SELECT seed_preparation_capabilities(%s)", (client,))
        for contact in (ctx.contact_id, w.other_contact):
            await conn.execute(
                "INSERT INTO contacts (id,client_id) VALUES (%s,%s)", (contact, ctx.client_id)
            )
        await conn.execute(
            "UPDATE capability_registry SET is_enabled=true,client_enabled=true WHERE client_id=%s",
            (ctx.client_id,),
        )
        await conn.execute(
            "INSERT INTO subscriptions (id,client_id,contact_id,plan,price,started_at) "
            "VALUES (%s,%s,%s,'basic',10,now())",
            (w.subscription_id, ctx.client_id, ctx.contact_id),
        )
        await conn.execute(
            "INSERT INTO plans (client_id,code,name,price) VALUES (%s,'pro','Synthetic',20)",
            (ctx.client_id,),
        )
        await conn.execute(
            "INSERT INTO channel_credentials (id,client_id,channel) VALUES (%s,%s,'gmail')",
            (w.mailbox_id, ctx.client_id),
        )
    try:
        yield w
    finally:
        await pool.close()


@requires_docker
async def test_runner_dispatch_and_risk_boundaries(world: World) -> None:
    runner = SpokeRunner(
        {"booking_agent": BookingAgent(), "subscription_agent": SubscriptionAgent()}
    )
    looked = Task(task_id="look", agent="booking_agent", input={"action": "availability"})
    assert runner.risk(looked) == "read"
    booked = Task(
        task_id="make",
        agent="booking_agent",
        input={"action": "book", "type_id": str(uuid4()), "starts_at": "2030-01-01T09:00"},
    )
    assert runner.risk(booked) == "commit"
    moved = Task(
        task_id="move",
        agent="booking_agent",
        input={"action": "reschedule", "booking_id": str(uuid4()), "starts_at": "2030-01-01T09:00"},
    )
    assert runner.risk(moved) == "commit"
    assert runner.risk(Task(task_id="x", agent="booking_agent", input={"action": "cancel"})) is None
    read = Task(task_id="read", agent="subscription_agent", input={"action": "plans"})
    assert runner.risk(read) == "read"
    assert (await runner.execute(read, world.ctx)).ok
    old = Task(task_id="old", agent="booking_agent", input={"action": "request_book"})
    rejected = await runner.execute(old, world.ctx)
    assert rejected.error and rejected.error.code == "invalid_input"
    staff = Task(task_id="mail", agent="email_agent", input={"action": "send"})
    assert runner.risk(staff) is None
    assert not (await runner.execute(staff, world.ctx)).ok


@requires_docker
async def test_contact_and_tenant_isolation(world: World) -> None:
    agent = SubscriptionAgent()
    inp = SubscriptionInput(action="status", subscription_id=world.subscription_id)
    assert (await agent.run(world.ctx, inp)).ok
    stranger = replace(world.ctx, contact_id=world.other_contact)
    denied = await agent.run(stranger, inp)
    assert denied.ok and [f.id for f in denied.facts] == ["subscription_none"]
    assert "basic" not in denied.facts[0].text
    outsider = replace(world.ctx, client_id=world.other_client)
    disabled = await agent.run(outsider, inp)
    assert disabled.error and disabled.error.code == "disabled"
    assert await world.rows() == []


@requires_docker
async def test_subscription_change_validates_plan_without_mutation(world: World) -> None:
    agent = SubscriptionAgent()
    inp = SubscriptionInput(
        action="request_change", subscription_id=world.subscription_id, plan_code="missing"
    )
    rejected = await agent.run(world.ctx, inp)
    assert rejected.error and rejected.error.code == "plan_not_found"
    valid = inp.model_copy(update={"plan_code": "pro"})
    assert (await agent.run(world.ctx, valid)).ok
    assert (await agent.run(world.ctx, valid)).meta.was_duplicate
    status = await agent.run(
        world.ctx,
        SubscriptionInput(
            action="status",
            subscription_id=world.subscription_id,
        ),
    )
    assert status.facts[0].text.startswith("Your plan: basic, MYR 10.00")
    assert len(await world.rows()) == 1


@requires_docker
async def test_a_switched_off_booking_agent_does_nothing(world: World) -> None:
    async with tx(world.ctx.pool, str(world.ctx.client_id)) as conn:
        await conn.execute(
            "UPDATE capability_registry SET client_enabled=false WHERE client_id=%s",
            (world.ctx.client_id,),
        )
    for inp in (
        BookingInput(action="availability"),
        BookingInput(action="list"),
        BookingInput(action="book", type_id=uuid4(), starts_at=datetime(2030, 1, 1, 9, 0)),
    ):
        disabled = await BookingAgent().run(world.ctx, inp)
        assert disabled.error and disabled.error.code == "disabled"
        assert disabled.facts == []
    assert await world.rows() == []


@requires_docker
@pytest.mark.parametrize("kind", ["email", "research", "invoice"])
async def test_staff_preparation_authorization_and_idempotency(world: World, kind: str) -> None:
    inp = (
        EmailDraftInput(
            mailbox_connection_id=world.mailbox_id,
            thread_id="thread",
            recipient="synthetic@example.test",
            subject="Test",
            body="Untrusted input",
        )
        if kind == "email"
        else ResearchInput(urls=["https://example.com/public"], purpose="Synthetic")
        if kind == "research"
        else InvoiceDraftInput(
            action="draft_create",
            lines=[
                InvoiceLine(
                    description="Test",
                    quantity=Decimal("1"),
                    unit_price=Decimal("10"),
                    tax_rate=Decimal("0.06"),
                ),
            ],
        )
    )
    service = StaffPreparationService()
    forbidden = await service.prepare(world.actor("read_only"), world.ctx, inp)
    assert forbidden.error and forbidden.error.code == "forbidden"
    wrong_tenant = world.actor().model_copy(update={"client_id": world.other_client})
    assert not (await service.prepare(wrong_tenant, world.ctx, inp)).ok
    assert await world.rows() == []
    assert (await service.prepare(world.actor(), world.ctx, inp)).ok
    assert (await service.prepare(world.actor(), world.ctx, inp)).meta.was_duplicate
    rows = await world.rows()
    assert len(rows) == 1 and rows[0]["requested_by"] == "synthetic-staff"
    assert rows[0]["status"] == "prepared"


@requires_docker
async def test_no_cross_tenant_mailbox_or_invoice(world: World) -> None:
    service = StaffPreparationService()
    email = EmailDraftInput(
        mailbox_connection_id=uuid4(),
        thread_id="thread",
        recipient="a@example.test",
        subject="Test",
        body="Test",
    )
    denied = await service.prepare(world.actor(), world.ctx, email)
    assert denied.error and denied.error.code == "mailbox_not_found"
    invoice = InvoiceDraftInput(action="request_void", invoice_id="other-tenant", reason="Test")
    denied = await service.prepare(world.actor(), world.ctx, invoice)
    assert denied.error and denied.error.code == "invoice_not_found"
    assert await world.rows() == []


@requires_docker
async def test_rls_blocks_unscoped_reads_and_request_mutation(world: World) -> None:
    inp = SubscriptionInput(action="request_pause", subscription_id=world.subscription_id)
    assert (await SubscriptionAgent().run(world.ctx, inp)).ok
    async with world.ctx.pool.connection() as conn:
        cursor = await conn.execute("SELECT id FROM agent_preparation_requests")
        assert await cursor.fetchall() == []
    async with tx(world.ctx.pool, str(world.other_client)) as conn:
        cursor = await conn.execute("SELECT id FROM agent_preparation_requests")
        assert await cursor.fetchall() == []
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with tx(world.ctx.pool, str(world.ctx.client_id)) as conn:
            await conn.execute("DELETE FROM agent_preparation_requests")


@requires_docker
async def test_seed_is_disabled_and_repeatable(world: World) -> None:
    async with await psycopg.AsyncConnection.connect(world.owner_dsn) as conn:
        await conn.execute("SELECT seed_preparation_capabilities(%s)", (world.other_client,))
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT is_enabled, client_enabled FROM capability_registry WHERE client_id=%s",
                (world.other_client,),
            )
            rows = await cur.fetchall()
    assert len(rows) == 2
    assert all(not r["is_enabled"] and not r["client_enabled"] for r in rows)


@requires_docker
async def test_request_contact_must_belong_to_tenant(world: World) -> None:
    other = uuid4()
    async with await psycopg.AsyncConnection.connect(world.owner_dsn) as conn:
        await conn.execute(
            "INSERT INTO contacts (id,client_id) VALUES (%s,%s)", (other, world.other_client)
        )
    with pytest.raises(psycopg.errors.RaiseException, match="does not belong"):
        async with tx(world.ctx.pool, str(world.ctx.client_id)) as conn:
            await conn.execute(
                "INSERT INTO agent_preparation_requests "
                "(client_id,contact_id,agent,action,idempotency_key,payload,trace_id,requested_by) "
                "VALUES (%s,%s,'subscription_agent','request_pause','cross-contact','{}',%s,"
                "'test')",
                (world.ctx.client_id, other, world.ctx.trace_id),
            )


@requires_docker
@pytest.mark.parametrize("action", ["request_pause", "request_cancel"])
async def test_subscription_pause_cancel_do_not_mutate(world: World, action: str) -> None:
    inp = SubscriptionInput.model_validate(
        {
            "action": action,
            "subscription_id": world.subscription_id,
        }
    )
    assert (await SubscriptionAgent().run(world.ctx, inp)).ok
    current = await SubscriptionAgent().run(
        world.ctx, SubscriptionInput(action="status", subscription_id=world.subscription_id)
    )
    assert current.facts[0].text.startswith("Your plan: basic")
    assert "paused" not in current.facts[0].text and "cancelled" not in current.facts[0].text


@requires_docker
async def test_missing_context_contact_cannot_prepare(world: World) -> None:
    missing = replace(world.ctx, contact_id=uuid4())
    response = await BookingAgent().run(missing, BookingInput(action="list"))
    assert response.error and response.error.code == "contact_not_found"
    request = await SubscriptionAgent().run(
        missing, SubscriptionInput(action="request_pause", subscription_id=world.subscription_id)
    )
    assert request.error and request.error.code == "contact_not_found"
    assert await world.rows() == []


@requires_docker
async def test_all_local_lists_work(world: World) -> None:
    subscriptions = await SubscriptionAgent().run(world.ctx, SubscriptionInput(action="list"))
    plans = await SubscriptionAgent().run(world.ctx, SubscriptionInput(action="plans"))
    assert subscriptions.facts[0].text.startswith("Your plan: basic, MYR 10.00")
    assert str(world.subscription_id) not in subscriptions.facts[0].text
    assert plans.facts[0].text == "Synthetic: MYR 20.00 a month."


def test_the_subscription_input_takes_the_customers_words_and_no_id() -> None:
    assert SubscriptionAgent.ACTION_RISK["mine"] == "read"
    assert SubscriptionInput(action="mine").subscription_id is None
    assert SubscriptionInput(action="request_cancel", reason="moving abroad").plan_code is None
    change = SubscriptionInput(action="request_change", plan_code="Pro Plus", current_plan="Basic")
    assert (change.plan_code, change.current_plan) == ("Pro Plus", "Basic")
    # A model that fills an unused field with an empty string is understood.
    blank = SubscriptionInput.model_validate(
        {"action": "request_pause", "current_plan": "", "plan_code": " ", "reason": ""}
    )
    assert blank.current_plan is None and blank.plan_code is None and blank.reason is None
    with pytest.raises(ValidationError):
        SubscriptionInput(action="request_change")
    with pytest.raises(ValidationError):
        SubscriptionInput(action="request_pause", current_plan="x" * 121)
    with pytest.raises(ValidationError):
        SubscriptionInput(action="request_change", plan_code="x" * 121)
    descriptions = {n: f.description for n, f in SubscriptionInput.model_fields.items()}
    assert descriptions["subscription_id"] == (
        "Leave it out: the server finds the customer's own subscription from this chat."
    )
    assert descriptions["reason"] == "The customer's reason, only if they gave one."
    assert "mine: the customer's own plans" in str(descriptions["action"])


def _sub(
    plan: str, name: str | None, status: str = "active", **fields: object
) -> dict[str, object]:
    return {
        "id": uuid4(),
        "plan": plan,
        "plan_name": name,
        "status": status,
        "cancel_at_period_end": False,
        **fields,
    }


def test_the_resolution_table() -> None:
    pro, basic = _sub("pro", "Pro"), _sub("basic", "Basic")
    waiting = _sub("lite", "Lite", status="pending")
    paused = _sub("max", "Max", status="paused")
    # No id and no plan named: the only requestable row, else which (names in the order given).
    assert resolve_subscription([basic], None, None) is basic
    assert resolve_subscription([pro, basic], None, None) == ("which", ["Pro", "Basic"])
    assert resolve_subscription([], None, None) == ("none", [])
    assert resolve_subscription([waiting], None, None) == ("waiting", ["Lite"])
    assert resolve_subscription([waiting, basic], None, None) is basic
    # A plan named by the customer picks one row, by code or by name, whatever the case.
    assert resolve_subscription([pro, basic], None, "BASIC") is basic
    assert resolve_subscription([pro, basic], None, "pro") is pro
    assert resolve_subscription([pro, basic], None, "no such plan") == ("which", ["Pro", "Basic"])
    assert resolve_subscription([basic], None, "no such plan") is basic
    assert resolve_subscription([pro, paused], None, "max") is paused
    # A named plan that is waiting for payment is not requestable.
    assert resolve_subscription([waiting], None, "lite") == ("waiting", ["Lite"])
    # An id only picks among these rows; one that is not here finds nothing.
    assert resolve_subscription([pro, basic], pro["id"], None) is pro  # type: ignore[arg-type]
    assert resolve_subscription([pro, basic], uuid4(), None) == ("none", [])
    assert resolve_subscription([pro, waiting], waiting["id"], None) == ("waiting", ["Lite"])  # type: ignore[arg-type]
    # The id wins over a plan named.
    assert resolve_subscription([pro, basic], basic["id"], "pro") is basic  # type: ignore[arg-type]


async def add_subscription(
    world: World,
    *,
    plan: str,
    contact: UUID | None = None,
    status: str = "active",
    started_days: int = 0,
    renews_at: datetime | None = None,
) -> UUID:
    """A synthetic subscription of the world's company; `started_days` orders them."""
    sub = uuid4()
    async with await psycopg.AsyncConnection.connect(world.owner_dsn) as conn:
        await conn.execute(
            "INSERT INTO subscriptions (id,client_id,contact_id,plan,price,status,started_at,"
            "renews_at) VALUES (%s,%s,%s,%s,15,%s,%s,%s)",
            (
                sub,
                world.ctx.client_id,
                contact or world.ctx.contact_id,
                plan,
                status,
                datetime(2028, 1, 1, tzinfo=UTC) + timedelta(days=started_days),
                renews_at,
            ),
        )
    return sub


async def name_plan(world: World, code: str, name: str, features: str = "[]") -> None:
    async with await psycopg.AsyncConnection.connect(world.owner_dsn) as conn:
        await conn.execute(
            "INSERT INTO plans (client_id,code,name,price,features) "
            "VALUES (%s,%s,%s,20,%s::jsonb) "
            "ON CONFLICT (client_id,code) DO UPDATE SET name = excluded.name, "
            "features = excluded.features",
            (world.ctx.client_id, code, name, features),
        )


async def mine(
    world: World,
    ctx: AgentContext | None = None,
    action: Literal["mine", "list", "status"] = "mine",
) -> list[str]:
    result = await SubscriptionAgent().run(ctx or world.ctx, SubscriptionInput(action=action))
    assert result.ok, result.error
    return [f.text for f in result.facts]


@requires_docker
async def test_mine_with_zero_one_and_several_subscriptions(world: World) -> None:
    nobody = replace(world.ctx, contact_id=world.other_contact)
    assert await mine(world, nobody) == ["I can't find a subscription linked to this chat."]
    assert await mine(world) == ["Your plan: basic, MYR 10.00."]
    await name_plan(world, "pro", "Pro")
    await add_subscription(
        world, plan="pro", started_days=10, renews_at=datetime(2029, 3, 1, tzinfo=UTC)
    )
    await add_subscription(world, plan="old", status="cancelled", started_days=20)
    result = await SubscriptionAgent().run(world.ctx, SubscriptionInput(action="mine"))
    assert [f.id for f in result.facts] == ["subscription_1", "subscription_2"]
    assert [f.text for f in result.facts] == [
        "Your plan: Pro, MYR 15.00 a month. It renews on 01 Mar 2029.",
        "Your plan: basic, MYR 10.00.",
    ]
    # `list` also shows the cancelled one; `status` with no id answers as `mine`.
    assert len(await mine(world, action="list")) == 3
    assert "Your plan: old was cancelled." in await mine(world, action="list")
    assert await mine(world, action="status") == await mine(world)


@requires_docker
async def test_mine_never_returns_another_contacts_subscription(world: World) -> None:
    await add_subscription(world, plan="secret-plan", contact=world.other_contact, started_days=5)
    for action in ("mine", "list", "status"):
        shown = await mine(world, action=action)
        assert shown == ["Your plan: basic, MYR 10.00."], action
    theirs = replace(world.ctx, contact_id=world.other_contact)
    assert await mine(world, theirs) == ["Your plan: secret-plan, MYR 15.00."]


@requires_docker
async def test_an_injected_subscription_id_of_another_contact_finds_nothing_and_prepares_nothing(
    world: World,
) -> None:
    theirs = await add_subscription(world, plan="secret-plan", contact=world.other_contact)
    none = "I can't find a subscription linked to this chat."
    for action in ("status", "request_pause", "request_cancel"):
        result = await SubscriptionAgent().run(
            world.ctx,
            SubscriptionInput(action=action, subscription_id=theirs),  # type: ignore[arg-type]
        )
        assert result.ok, action
        assert [(f.id, f.text) for f in result.facts] == [("subscription_none", none)], action
    change = SubscriptionInput(action="request_change", subscription_id=theirs, plan_code="pro")  # type: ignore[arg-type]
    result = await SubscriptionAgent().run(world.ctx, change)
    assert [f.text for f in result.facts] == [none]
    assert await world.rows() == []


@requires_docker
async def test_a_request_without_an_id_prepares_on_the_only_subscription_with_the_id_stored(
    world: World,
) -> None:
    result = await SubscriptionAgent().run(
        world.ctx, SubscriptionInput(action="request_pause", reason="holiday")
    )
    assert result.ok and [f.id for f in result.facts] == ["subscription_passed"]
    rows = await world.rows()
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert isinstance(payload, dict)
    assert payload["subscription_id"] == str(world.subscription_id)
    assert payload["action"] == "request_pause" and payload["reason"] == "holiday"
    assert payload.get("plan_code") is None and payload.get("current_plan") is None
    # What staff read back is the resolved subscription.
    async with tx(world.ctx.pool, str(world.ctx.client_id)) as conn:
        read = await db.q.fetch_one(
            "subscriptions/request_get.sql",
            {"client_id": world.ctx.client_id, "request_id": rows[0]["id"]},
            conn=conn,
        )
    assert read["subscription_id"] == world.subscription_id and read["reason"] == "holiday"


@requires_docker
async def test_two_subscriptions_ask_which_and_current_plan_picks_one(world: World) -> None:
    await name_plan(world, "pro", "Pro")
    second = await add_subscription(world, plan="pro", started_days=10)
    agent = SubscriptionAgent()
    asked = await agent.run(world.ctx, SubscriptionInput(action="request_cancel"))
    assert [f.id for f in asked.facts] == ["subscription_which"]
    assert asked.facts[0].text == (
        "This chat has more than one plan: Pro, basic. Which one do you mean?"
    )
    unknown = await agent.run(
        world.ctx, SubscriptionInput(action="request_cancel", current_plan="platinum")
    )
    assert [f.id for f in unknown.facts] == ["subscription_which"]
    assert await world.rows() == []
    picked = await agent.run(
        world.ctx, SubscriptionInput(action="request_cancel", current_plan="PRO")
    )
    assert [f.id for f in picked.facts] == ["subscription_passed"]
    assert picked.facts[0].text.startswith("Your request to cancel Pro was passed")
    rows = await world.rows()
    assert len(rows) == 1 and rows[0]["payload"]["subscription_id"] == str(second)  # type: ignore[index]


@requires_docker
async def test_request_change_takes_the_plan_name_and_stores_its_code(world: World) -> None:
    await name_plan(world, "pro-plus", "Pro Plus")
    result = await SubscriptionAgent().run(
        world.ctx, SubscriptionInput(action="request_change", plan_code="pro plus")
    )
    assert result.ok, result.error
    assert result.facts[0].text == (
        "Your request to change basic to Pro Plus was passed to our staff to decide. "
        "Nothing has changed yet."
    )
    rows = await world.rows()
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["plan_code"] == "pro-plus"  # type: ignore[index]
    assert payload["subscription_id"] == str(world.subscription_id)  # type: ignore[index]
    # Another tenant's plan, or none at all, is the existing error and prepares nothing.
    missing = await SubscriptionAgent().run(
        world.ctx, SubscriptionInput(action="request_change", plan_code="Platinum")
    )
    assert missing.error and missing.error.code == "plan_not_found"
    assert len(await world.rows()) == 1


@requires_docker
async def test_cancelling_a_plan_already_ending_prepares_nothing(world: World) -> None:
    async with await psycopg.AsyncConnection.connect(world.owner_dsn) as conn:
        await conn.execute(
            "UPDATE subscriptions SET cancel_at_period_end = true, renews_at = %s WHERE id = %s",
            (datetime(2029, 2, 1, 4, 0, tzinfo=UTC), world.subscription_id),
        )
    agent = SubscriptionAgent()
    result = await agent.run(world.ctx, SubscriptionInput(action="request_cancel"))
    assert [f.id for f in result.facts] == ["subscription_already_ending"]
    assert result.facts[0].text == "basic is already set to end on 01 Feb 2029 and will not renew."
    assert await mine(world) == [
        "Your plan: basic, MYR 10.00. It ends on 01 Feb 2029 and will not renew."
    ]
    # A pause is still fine; and a paused plan is not paused twice.
    assert (await agent.run(world.ctx, SubscriptionInput(action="request_pause"))).ok
    async with await psycopg.AsyncConnection.connect(world.owner_dsn) as conn:
        await conn.execute(
            "UPDATE subscriptions SET status = 'paused' WHERE id = %s", (world.subscription_id,)
        )
    again = await agent.run(world.ctx, SubscriptionInput(action="request_pause"))
    assert [f.id for f in again.facts] == ["subscription_already_paused"]
    assert again.facts[0].text == "basic is already paused."
    assert len(await world.rows()) == 1


@requires_docker
async def test_the_request_fact_says_passed_and_nothing_changed(world: World) -> None:
    agent = SubscriptionAgent()
    first = await agent.run(world.ctx, SubscriptionInput(action="request_cancel"))
    text = first.facts[0].text
    assert text == (
        "Your request to cancel basic was passed to our staff to decide. Nothing has changed yet."
    )
    assert "prepared" not in text and "Request " not in text
    assert first.facts[0].source == "subscription_agent" and first.facts[0].as_of is not None
    # A retried message is a duplicate, not a second request, and says the same.
    again = await agent.run(world.ctx, SubscriptionInput(action="request_cancel"))
    assert again.meta.was_duplicate and again.facts[0].text == text
    assert len(await world.rows()) == 1
    assert (await mine(world)) == ["Your plan: basic, MYR 10.00."]
    # The same key for different input is still refused.
    clash = await agent.run(world.ctx, SubscriptionInput(action="request_pause"))
    assert clash.error and clash.error.code == "idempotency_conflict"


@requires_docker
async def test_plans_carry_features(world: World) -> None:
    await name_plan(world, "pro", "Pro", features='["5 GB data", "Free calls"]')
    result = await SubscriptionAgent().run(world.ctx, SubscriptionInput(action="plans"))
    assert [f.id for f in result.facts] == ["subscription_plan_1"]
    assert result.facts[0].text == "Pro: MYR 20.00 a month. Includes: 5 GB data; Free calls."


@requires_docker
async def test_make_db_reset_on_disposable_database(combined_pg_image: str) -> None:
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer(
        image=combined_pg_image,
        username="csa",
        password="test_owner_pw",
        dbname="csa",
        driver=None,
    ) as container:
        dsn = make_conninfo(
            host=container.get_container_host_ip(),
            port=container.get_exposed_port(5432),
            user="csa",
            password="test_owner_pw",
            dbname="csa",
        )
        container_id = container.get_wrapped_container().id
        psql = (
            ["psql", dsn]
            if shutil.which("psql")
            else ["docker", "exec", "-i", container_id, "psql", "-U", "csa", "-d", "csa"]
        )
        process = await asyncio.create_subprocess_exec(
            "make",
            "db-reset",
            f"DEV_OWNER_DSN={dsn}",
            f"PSQL={shlex.join(psql)}",
            "DEV_APP_PASSWORD=test_app_pw",
            "DEV_RO_PASSWORD=test_ro_pw",
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "ENV": "ci", "NO_LOCAL_ENV": "1"},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await process.communicate()
        assert process.returncode == 0, output.decode()
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            row = await (
                await conn.execute(
                    "SELECT relrowsecurity FROM pg_class WHERE relname='agent_preparation_requests'"
                )
            ).fetchone()
            assert row and row[0] is True
