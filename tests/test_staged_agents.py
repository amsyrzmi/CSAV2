"""Wave 2/3 preparation against synthetic data in a disposable database."""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

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
        {"action": "request_book"},
        {"action": "request_cancel"},
        {
            "action": "request_book",
            "service": "Consultation",
            "starts_at": "2030-01-01T09:00:00",
            "ends_at": "2030-01-01T10:00:00",
        },
        {
            "action": "request_book",
            "service": "Consultation",
            "starts_at": "2030-01-01T10:00:00+08:00",
            "ends_at": "2030-01-01T09:00:00+08:00",
        },
        {
            "action": "request_book",
            "service": "Consultation",
            "starts_at": "2030-01-01T09:00:00+08:00",
            "ends_at": "2030-01-03T09:00:00+08:00",
        },
        {"action": "list", "timezone": "not/a/timezone"},
        {"action": "list", "client_id": str(uuid4())},
    ],
)
def test_invalid_booking(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        BookingInput.model_validate(payload)


def test_dst_offsets_and_nonexistent_wall_time() -> None:
    good = BookingInput(
        action="request_book",
        service="Test",
        timezone="America/New_York",
        starts_at=datetime.fromisoformat("2030-11-03T01:30:00-04:00"),
        ends_at=datetime.fromisoformat("2030-11-03T01:30:00-05:00"),
    )
    assert good.ends_at is not None and good.starts_at is not None
    assert (good.ends_at - good.starts_at).total_seconds() == 3600
    with pytest.raises(ValidationError, match="offset"):
        BookingInput(
            action="request_book",
            service="Test",
            timezone="America/New_York",
            starts_at=datetime.fromisoformat("2030-03-10T02:30:00-05:00"),
            ends_at=datetime.fromisoformat("2030-03-10T04:00:00-04:00"),
        )


def test_dst_aware_python_datetimes_use_elapsed_time() -> None:
    zone = ZoneInfo("America/New_York")
    inp = BookingInput(
        action="request_book",
        service="Test",
        timezone=zone.key,
        starts_at=datetime(2030, 11, 3, 1, 30, tzinfo=zone, fold=0),
        ends_at=datetime(2030, 11, 3, 1, 30, tzinfo=zone, fold=1),
    )
    assert inp.starts_at is not None and inp.ends_at is not None
    assert (inp.ends_at.astimezone(UTC) - inp.starts_at.astimezone(UTC)).total_seconds() == 3600
    with pytest.raises(ValidationError, match="offset"):
        BookingInput(
            action="request_book",
            service="Test",
            timezone=zone.key,
            starts_at=datetime(2030, 3, 10, 2, 30, tzinfo=zone),
            ends_at=datetime(2030, 3, 10, 4, 0, tzinfo=zone),
        )


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
        self.booking_id = uuid4()
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
            "INSERT INTO bookings (id,client_id,contact_id,service,starts_at,ends_at) "
            "VALUES (%s,%s,%s,'Synthetic','2030-01-01T01:00Z','2030-01-01T02:00Z')",
            (w.booking_id, ctx.client_id, ctx.contact_id),
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


def book() -> BookingInput:
    return BookingInput(
        action="request_book",
        service="Consultation",
        starts_at=datetime.fromisoformat("2030-01-01T09:00:00+08:00"),
        ends_at=datetime.fromisoformat("2030-01-01T10:00:00+08:00"),
    )


@requires_docker
async def test_book_prepares_once_and_never_creates_event(world: World) -> None:
    agent = BookingAgent()
    results = await asyncio.gather(*(agent.run(world.ctx, book()) for _ in range(4)))
    assert all(r.ok and "NOT executed" in r.facts[0].text for r in results)
    assert sum(r.meta.was_duplicate is False for r in results) == 1
    rows = await world.rows()
    assert len(rows) == 1 and rows[0]["status"] == "prepared"
    assert rows[0]["requested_by"] == str(world.ctx.contact_id)
    changed = book().model_copy(update={"service": "Different"})
    conflict = await agent.run(world.ctx, changed)
    assert conflict.error and conflict.error.code == "idempotency_conflict"
    async with tx(world.ctx.pool, str(world.ctx.client_id)) as conn:
        row = await (
            await conn.execute(
                "SELECT count(*) AS n FROM bookings WHERE client_id=%s", (world.ctx.client_id,)
            )
        ).fetchone()
        assert row and row["n"] == 1


@requires_docker
async def test_runner_dispatch_and_risk_boundaries(world: World) -> None:
    runner = SpokeRunner(
        {"booking_agent": BookingAgent(), "subscription_agent": SubscriptionAgent()}
    )
    request = Task(task_id="prepare", agent="booking_agent", input=book().model_dump(mode="json"))
    assert runner.risk(request) == "draft"
    assert (await runner.execute(request, world.ctx)).ok
    read = Task(task_id="read", agent="subscription_agent", input={"action": "plans"})
    assert runner.risk(read) == "read"
    assert (await runner.execute(read, world.ctx)).ok
    unsupported = Task(task_id="commit", agent="booking_agent", input={"action": "book"})
    rejected = await runner.execute(unsupported, world.ctx)
    assert rejected.error and rejected.error.code == "invalid_input"
    staff = Task(task_id="mail", agent="email_agent", input={"action": "send"})
    assert runner.risk(staff) is None
    assert not (await runner.execute(staff, world.ctx)).ok


@requires_docker
@pytest.mark.parametrize("kind", ["booking", "subscription"])
async def test_contact_and_tenant_isolation(world: World, kind: str) -> None:
    agent = BookingAgent() if kind == "booking" else SubscriptionAgent()
    inp = (
        BookingInput(action="status", booking_id=world.booking_id)
        if kind == "booking"
        else SubscriptionInput(action="status", subscription_id=world.subscription_id)
    )
    assert (await agent.run(world.ctx, inp)).ok
    stranger = replace(world.ctx, contact_id=world.other_contact)
    denied = await agent.run(stranger, inp)
    assert denied.error and denied.error.code.endswith("_not_found")
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
    assert '"plan": "basic"' in status.facts[0].text
    assert len(await world.rows()) == 1


@requires_docker
async def test_disabled_and_past_booking(world: World) -> None:
    agent = BookingAgent()
    past = replace(world.ctx, now=lambda: datetime(2031, 1, 1, tzinfo=UTC))
    response = await agent.run(past, book())
    assert response.error and response.error.code == "booking_in_past"
    async with tx(world.ctx.pool, str(world.ctx.client_id)) as conn:
        await conn.execute(
            "UPDATE capability_registry SET client_enabled=false WHERE client_id=%s",
            (world.ctx.client_id,),
        )
    disabled = await agent.run(world.ctx, book())
    assert disabled.error and disabled.error.code == "disabled"
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
    assert (await BookingAgent().run(world.ctx, book())).ok
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
                "VALUES (%s,%s,'booking_agent','request_book','cross-contact','{}',%s,'test')",
                (world.ctx.client_id, other, world.ctx.trace_id),
            )


@requires_docker
@pytest.mark.parametrize("action", ["request_reschedule", "request_cancel"])
async def test_booking_changes_are_preparation_only(world: World, action: str) -> None:
    inp = BookingInput.model_validate(
        {
            **book().model_dump(),
            "action": action,
            "booking_id": world.booking_id,
        }
    )
    assert (await BookingAgent().run(world.ctx, inp)).ok
    current = await BookingAgent().run(
        world.ctx, BookingInput(action="status", booking_id=world.booking_id)
    )
    assert '"status": "confirmed"' in current.facts[0].text
    assert '"starts_at": "2030-01-01 01:00:00+00:00"' in current.facts[0].text


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
    assert '"status": "active"' in current.facts[0].text


@requires_docker
async def test_missing_context_contact_cannot_prepare(world: World) -> None:
    missing = replace(world.ctx, contact_id=uuid4())
    response = await BookingAgent().run(missing, book())
    assert response.error and response.error.code == "contact_not_found"
    assert await world.rows() == []


@requires_docker
async def test_all_local_lists_work(world: World) -> None:
    bookings = await BookingAgent().run(world.ctx, BookingInput(action="list"))
    subscriptions = await SubscriptionAgent().run(world.ctx, SubscriptionInput(action="list"))
    plans = await SubscriptionAgent().run(world.ctx, SubscriptionInput(action="plans"))
    assert str(world.booking_id) in bookings.facts[0].text
    assert str(world.subscription_id) in subscriptions.facts[0].text
    assert '"code": "pro"' in plans.facts[0].text


@requires_docker
async def test_make_db_reset_on_disposable_database(
    migrated_dsn: str, pg_container: object
) -> None:
    async with await psycopg.AsyncConnection.connect(migrated_dsn, autocommit=True) as conn:
        await conn.execute("CREATE DATABASE wave_reset")
    dsn = make_conninfo(migrated_dsn, dbname="wave_reset")
    container_id = pg_container.get_wrapped_container().id  # type: ignore[attr-defined]
    psql = (
        ["psql", dsn]
        if shutil.which("psql")
        else ["docker", "exec", "-i", container_id, "psql", "-U", "csa", "-d", "wave_reset"]
    )
    process = await asyncio.create_subprocess_exec(
        "make",
        "db-reset",
        f"DEV_OWNER_DSN={dsn}",
        f"PSQL={shlex.join(psql)}",
        "DEV_APP_PASSWORD=test_app_pw",
        "DEV_RO_PASSWORD=test_ro_pw",
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "ENV": "ci"},
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
