"""The invoice ledger (service level): issue, mark paid with activation and resume, void, claim.

Money-critical. An invoice becomes `paid` only in the transaction that inserts its `payments` row;
two staff pressing Mark paid at once pay it once (real two-connection tests); issuance is
idempotent under concurrency; another company's invoice is a 404; a `paid` row without a payment
(old seeds) is never shown to a customer as paid.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
import pytest
from csa.core import db
from csa.core.tenancy import tx
from csa.payments import invoices
from csa.payments.invoices import InvoiceRecord, MarkPaidOutcome
from csa.payments.reference import (
    ALPHABET,
    REFERENCE_PATTERN,
    new_invoice_id,
    new_reference,
    normalise_reference,
)
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from tests.conftest import requires_docker
from tests.test_console_sessions import SessionWorld

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

HOLD = 0.4  # seconds a racing transaction keeps its locks before it commits


class Ledger:
    """One company of the test world: seeds as the owner, calls the service in its tenant tx."""

    def __init__(self, w: SessionWorld, tenant: str, staff_id: UUID) -> None:
        self.w, self.tenant, self.staff_id = w, UUID(tenant), staff_id

    async def q(self, statement: Any, *params: Any) -> list[dict[str, Any]]:
        return await self.w.q(statement, *params)

    async def payments_on(self, *, lead_days: int = 7, enabled: bool = True) -> None:
        await self.q(
            "INSERT INTO payment_settings (client_id, enabled, lead_days) VALUES (%s,%s,%s) "
            "ON CONFLICT (client_id) DO UPDATE SET enabled = EXCLUDED.enabled, "
            "lead_days = EXCLUDED.lead_days",
            self.tenant,
            enabled,
            lead_days,
        )

    async def plan(
        self, code: str = "basic", price: str = "29.90", interval: str = "month"
    ) -> None:
        await self.q(
            "INSERT INTO plans (client_id, code, name, price, billing_interval, is_active) "
            "VALUES (%s,%s,%s,%s,%s,true) ON CONFLICT DO NOTHING",
            self.tenant,
            code,
            code.title(),
            price,
            interval,
        )

    async def contact(self, name: str = "Aisha Rahman") -> UUID:
        rows = await self.q(
            "INSERT INTO contacts (client_id, full_name, email) VALUES (%s,%s,%s) RETURNING id",
            self.tenant,
            name,
            f"synthetic-{uuid.uuid4()}@example.test",
        )
        return UUID(str(rows[0]["id"]))

    async def subscription(
        self,
        contact: UUID | None = None,
        *,
        plan: str = "basic",
        status: str = "pending",
        price: str = "29.90",
        renews_at: datetime | None = None,
        **cols: Any,
    ) -> UUID:
        await self.plan(plan, price)
        contact = contact or await self.contact()
        names = ["client_id", "contact_id", "plan", "status", "price", "started_at", "renews_at"]
        values: list[Any] = [
            self.tenant,
            contact,
            plan,
            status,
            price,
            datetime.now(UTC) - timedelta(days=20),
            renews_at,
        ]
        for name, value in cols.items():
            names.append(name)
            values.append(value)
        marks = ", ".join(["%s"] * len(names))
        statement = f"INSERT INTO subscriptions ({', '.join(names)}) VALUES ({marks}) RETURNING id"
        rows = await self.q(statement, *values)
        return UUID(str(rows[0]["id"]))

    async def invoice(
        self,
        *,
        status: str = "issued",
        contact: UUID | None = None,
        subscription: UUID | None = None,
        period_start: datetime | None = None,
        amount: str = "29.90",
        due_at: datetime | None = None,
        paid_at: datetime | None = None,
    ) -> str:
        """An invoice written as an old seed or a later state would be (no payment row)."""
        now = datetime.now(UTC)
        invoice_id = new_invoice_id(now)
        await self.q(
            "INSERT INTO invoices (id, client_id, contact_id, subscription_id, reference, status, "
            "amount, currency, issued_at, due_at, paid_at, period_start, period_end, line_items) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,'MYR',%s,%s,%s,%s,%s,%s)",
            invoice_id,
            self.tenant,
            contact,
            subscription,
            new_reference(),
            status,
            amount,
            now,
            due_at or now + timedelta(days=7),
            paid_at,
            period_start,
            period_start + timedelta(days=30) if period_start else None,
            Jsonb([{"description": "Basic", "quantity": 1, "unit_price": amount}]),
        )
        return invoice_id

    async def ledger_payment(self, invoice_id: str, *, received_at: datetime) -> None:
        """A payment row written as the owner (as a seed would), for read tests."""
        await self.q(
            "INSERT INTO payments (client_id, invoice_id, amount, currency, received_at, source, "
            "matched_by, idempotency_key) SELECT client_id, id, amount, currency, %s, 'staff', "
            "'staff', %s FROM invoices WHERE id = %s",
            received_at,
            f"seed:{uuid.uuid4()}",
            invoice_id,
        )

    async def run(self, fn: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
        async with tx(self.w.pool, str(self.tenant)) as conn:
            return await fn(conn, self.tenant, *args, **kwargs)

    async def pay(
        self,
        invoice_id: str,
        key: UUID | None = None,
        *,
        actor: bool = True,
        received_at: datetime | None = None,
        hold: float = 0.0,
        note: str | None = "Seen in the bank app",
    ) -> MarkPaidOutcome:
        async with tx(self.w.pool, str(self.tenant)) as conn:
            outcome = await invoices.mark_paid(
                conn,
                self.tenant,
                invoice_id,
                actor_user_id=self.staff_id if actor else None,
                request_key=key or uuid.uuid4(),
                method_kind="transfer",
                received_at=received_at or datetime.now(UTC) - timedelta(hours=1),
                note=note,
            )
            if hold:
                await asyncio.sleep(hold)
            return outcome

    async def row(self, table: Any, row_id: Any) -> dict[str, Any]:
        rows = await self.q(f"SELECT * FROM {table} WHERE id = %s", row_id)
        return rows[0]

    async def payments(self, invoice_id: str) -> list[dict[str, Any]]:
        return await self.q("SELECT * FROM payments WHERE invoice_id = %s", invoice_id)

    async def events(self, sub: UUID, action: str) -> list[dict[str, Any]]:
        return await self.q(
            "SELECT * FROM subscription_events WHERE subscription_id = %s AND action = %s",
            sub,
            action,
        )

    async def invoices_of(self, sub: UUID) -> list[dict[str, Any]]:
        return await self.q(
            "SELECT * FROM invoices WHERE subscription_id = %s ORDER BY issued_at", sub
        )

    async def next_renewal(self, anchor: datetime, after: datetime, interval: str) -> datetime:
        rows = await self.q(
            "SELECT public.subscription_next_renewal(%s, %s, %s, timezone) AS at "
            "FROM clients WHERE id = %s",
            anchor,
            after,
            interval,
            self.tenant,
        )
        at: datetime = rows[0]["at"]
        return at


async def _ledger(w: SessionWorld, tenant: str) -> Ledger:
    _, me = await w.member(role="tenant_staff", tenant=tenant)
    return Ledger(w, tenant, UUID(me["user"]["id"]))


@pytest.fixture
async def a(sessions: SessionWorld) -> Ledger:
    return await _ledger(sessions, sessions.tenants[0])


@pytest.fixture
async def b(sessions: SessionWorld) -> Ledger:
    return await _ledger(sessions, sessions.tenants[1])


async def refused(call: Awaitable[Any], status: int, detail: str) -> None:
    with pytest.raises(HTTPException) as caught:
        await call
    assert (caught.value.status_code, caught.value.detail) == (status, detail)


def stub_draws(*bodies: str) -> tuple[Callable[[int], int], Callable[[], int]]:
    """A `randbelow` that draws the given reference bodies in order, and a draw counter."""
    codes: Iterator[int] = iter([ALPHABET.index(c) for body in bodies for c in body])
    used = [0]

    def draw(n: int) -> int:
        assert n == len(ALPHABET)
        used[0] += 1
        return next(codes)

    return draw, lambda: used[0]


# --- the opening invoice -------------------------------------------------------------------------


async def test_opening_invoice_is_issued_once_for_a_pending_subscription(a: Ledger) -> None:
    await a.payments_on()
    sub = await a.subscription(status="pending", price="59.90")
    now = datetime.now(UTC)

    issued = await a.run(invoices.issue_opening_invoice, sub, now=now)

    assert isinstance(issued, InvoiceRecord)
    assert issued.status == "issued"
    assert issued.amount == Decimal("59.90") and isinstance(issued.amount, Decimal)
    assert issued.currency == "MYR"
    assert issued.issued_at == now and issued.due_at == now + timedelta(days=7)
    assert issued.subscription_id == sub and issued.period_start is None
    assert issued.plan_name == "Basic" and issued.paid_at is None
    assert issued.reference is not None and re.fullmatch(REFERENCE_PATTERN, issued.reference)
    assert normalise_reference(issued.reference) == issued.reference
    assert re.fullmatch(r"inv_[0-9a-f]{25}", issued.id)
    stored = await a.row("invoices", issued.id)
    assert stored["line_items"] == [{"description": "Basic", "quantity": 1, "unit_price": "59.90"}]
    assert stored["client_id"] == a.tenant

    assert await a.run(invoices.issue_opening_invoice, sub, now=now) is None
    assert len(await a.invoices_of(sub)) == 1


async def test_opening_invoice_needs_payments_on_and_a_pending_subscription(a: Ledger) -> None:
    pending = await a.subscription(status="pending")
    now = datetime.now(UTC)
    assert await a.run(invoices.issue_opening_invoice, pending, now=now) is None  # no settings row
    await a.payments_on(enabled=False)
    assert await a.run(invoices.issue_opening_invoice, pending, now=now) is None
    assert await a.invoices_of(pending) == []

    await a.payments_on()
    for status in ("active", "paused", "cancelled"):
        sub = await a.subscription(status=status, renews_at=now + timedelta(days=3))
        assert await a.run(invoices.issue_opening_invoice, sub, now=now) is None
        assert await a.invoices_of(sub) == []
    assert await a.run(invoices.issue_opening_invoice, uuid.uuid4(), now=now) is None


async def test_a_void_opening_invoice_blocks_a_new_one(a: Ledger) -> None:
    await a.payments_on()
    sub = await a.subscription(status="pending")
    await a.invoice(status="void", subscription=sub)
    assert await a.run(invoices.issue_opening_invoice, sub, now=datetime.now(UTC)) is None
    assert len(await a.invoices_of(sub)) == 1


async def test_two_issuers_at_once_issue_one_opening_invoice(a: Ledger) -> None:
    await a.payments_on()
    sub = await a.subscription(status="pending")
    now = datetime.now(UTC)

    async def issue() -> InvoiceRecord | None:
        async with tx(a.w.pool, str(a.tenant)) as conn:
            issued = await invoices.issue_opening_invoice(conn, a.tenant, sub, now=now)
            await asyncio.sleep(HOLD)
            return issued

    results = await asyncio.gather(issue(), issue())
    assert sorted(r is None for r in results) == [False, True]
    assert len(await a.invoices_of(sub)) == 1


# --- period invoices -----------------------------------------------------------------------------


async def test_period_invoice_covers_the_next_period_once(a: Ledger) -> None:
    await a.payments_on()
    now = datetime.now(UTC)
    renews = now + timedelta(days=5)
    sub = await a.subscription(status="active", price="25.00", renews_at=renews)

    issued = await a.run(invoices.issue_period_invoice, sub, now=now)

    assert isinstance(issued, InvoiceRecord)
    assert issued.amount == Decimal("25.00")  # the subscription's price, not the plan's 29.90
    assert issued.period_start == renews and issued.due_at == renews
    assert issued.period_end == await a.next_renewal(renews, renews, "month")
    assert issued.status == "issued" and issued.issued_at == now
    assert await a.run(invoices.issue_period_invoice, sub, now=now) is None
    assert len(await a.invoices_of(sub)) == 1


async def test_period_invoice_only_for_an_active_subscription_due_within_lead_days(
    a: Ledger,
) -> None:
    now = datetime.now(UTC)
    soon = now + timedelta(days=5)
    late = await a.subscription(status="active", renews_at=now + timedelta(days=9))
    ending = await a.subscription(status="active", renews_at=soon, cancel_at_period_end=True)
    paused = await a.subscription(status="paused", renews_at=soon, paused_at=now)
    pending = await a.subscription(status="pending")
    due = await a.subscription(status="active", renews_at=soon)

    assert await a.run(invoices.issue_period_invoice, due, now=now) is None  # payments off
    await a.payments_on()
    for sub in (late, ending, paused, pending):
        assert await a.run(invoices.issue_period_invoice, sub, now=now) is None
        assert await a.invoices_of(sub) == []
    assert await a.run(invoices.issue_period_invoice, due, now=now) is not None


async def test_period_invoice_uses_the_pending_plan_in_effect_at_its_current_price(
    a: Ledger,
) -> None:
    await a.payments_on()
    await a.plan("pro", "299.00", "year")
    now = datetime.now(UTC)
    renews = now + timedelta(days=4)
    switching = await a.subscription(
        status="active", renews_at=renews, pending_plan="pro", pending_effective_at=renews
    )
    later = await a.subscription(
        status="active",
        renews_at=renews,
        pending_plan="pro",
        pending_effective_at=renews + timedelta(days=30),
    )
    await a.q("UPDATE plans SET price = 319.00 WHERE client_id = %s AND code = 'pro'", a.tenant)

    issued = await a.run(invoices.issue_period_invoice, switching, now=now)
    assert issued is not None
    assert (issued.amount, issued.plan_name) == (Decimal("319.00"), "Pro")
    assert issued.period_end == await a.next_renewal(renews, renews, "year")

    kept = await a.run(invoices.issue_period_invoice, later, now=now)
    assert kept is not None
    assert (kept.amount, kept.plan_name) == (Decimal("29.90"), "Basic")
    assert kept.period_end == await a.next_renewal(renews, renews, "month")

    # An archived pending plan does not apply (rule 2 skips it): the current terms stay.
    archived = await a.subscription(
        status="active", renews_at=renews, pending_plan="pro", pending_effective_at=renews
    )
    await a.q("UPDATE plans SET is_active = false WHERE client_id = %s AND code = 'pro'", a.tenant)
    old_terms = await a.run(invoices.issue_period_invoice, archived, now=now)
    assert old_terms is not None and old_terms.amount == Decimal("29.90")


async def test_issued_amount_never_follows_a_later_price_change(a: Ledger) -> None:
    await a.payments_on()
    now = datetime.now(UTC)
    sub = await a.subscription(status="active", renews_at=now + timedelta(days=2))
    issued = await a.run(invoices.issue_period_invoice, sub, now=now)
    assert issued is not None
    await a.q("UPDATE plans SET price = 99.00 WHERE client_id = %s", a.tenant)
    await a.q("UPDATE subscriptions SET price = 99.00 WHERE id = %s", sub)
    assert (await a.row("invoices", issued.id))["amount"] == Decimal("29.90")
    paid = await a.pay(issued.id)
    assert paid.invoice.amount == Decimal("29.90")
    assert (await a.payments(issued.id))[0]["amount"] == Decimal("29.90")


async def test_a_void_invoice_for_the_period_blocks_a_new_one(a: Ledger) -> None:
    await a.payments_on()
    now = datetime.now(UTC)
    renews = now + timedelta(days=3)
    sub = await a.subscription(status="active", renews_at=renews)
    await a.invoice(status="void", subscription=sub, period_start=renews)
    assert await a.run(invoices.issue_period_invoice, sub, now=now) is None
    assert len(await a.invoices_of(sub)) == 1


async def test_two_issuers_at_once_issue_one_period_invoice(a: Ledger) -> None:
    await a.payments_on()
    now = datetime.now(UTC)
    sub = await a.subscription(status="active", renews_at=now + timedelta(days=3))

    async def issue() -> InvoiceRecord | None:
        async with tx(a.w.pool, str(a.tenant)) as conn:
            issued = await invoices.issue_period_invoice(conn, a.tenant, sub, now=now)
            await asyncio.sleep(HOLD)
            return issued

    results = await asyncio.gather(issue(), issue())
    assert sorted(r is None for r in results) == [False, True]
    assert len(await a.invoices_of(sub)) == 1


# --- reference collisions ------------------------------------------------------------------------


async def test_a_taken_reference_is_drawn_again(a: Ledger, monkeypatch: pytest.MonkeyPatch) -> None:
    await a.payments_on()
    now = datetime.now(UTC)
    first = await a.run(invoices.issue_opening_invoice, await a.subscription(), now=now)
    assert first is not None and first.reference is not None
    taken = first.reference.replace("-", "")[:6]
    free = "222222" if taken != "222222" else "333333"

    draw, used = stub_draws(taken, taken, free)
    monkeypatch.setattr(invoices, "randbelow", draw)
    third_try = await a.run(invoices.issue_opening_invoice, await a.subscription(), now=now)
    assert third_try is not None and third_try.reference is not None
    assert third_try.reference.replace("-", "")[:6] == free and used() == 18

    # One attempt and five retries: the sixth draw may still succeed...
    other = "333333" if taken != "333333" else "444444"
    draw, used = stub_draws(*([taken] * 5), other)
    monkeypatch.setattr(invoices, "randbelow", draw)
    sixth = await a.run(invoices.issue_opening_invoice, await a.subscription(), now=now)
    assert sixth is not None and sixth.reference is not None
    assert sixth.reference.replace("-", "")[:6] == other and used() == 36

    # ...but six collisions raise and leave nothing behind.
    sub = await a.subscription()
    draw, used = stub_draws(*([taken] * 6))
    monkeypatch.setattr(invoices, "randbelow", draw)
    with pytest.raises(RuntimeError, match="reference"):
        await a.run(invoices.issue_opening_invoice, sub, now=now)
    assert used() == 36
    assert await a.invoices_of(sub) == []


# --- mark paid -----------------------------------------------------------------------------------


async def test_mark_paid_records_one_payment_and_starts_the_pending_subscription(
    a: Ledger,
) -> None:
    await a.payments_on()
    sub = await a.subscription(status="pending", price="59.90")
    opening = await a.run(invoices.issue_opening_invoice, sub, now=datetime.now(UTC))
    assert opening is not None
    key = uuid.uuid4()
    received = datetime.now(UTC) - timedelta(hours=2)
    before = datetime.now(UTC)

    paid = await a.pay(opening.id, key, received_at=received)

    after = datetime.now(UTC)
    assert (paid.replayed, paid.activated, paid.resumed) == (False, True, False)
    assert paid.invoice.status == "paid" and paid.invoice.paid_at == received
    assert paid.invoice.method_kind == "transfer"
    payments = await a.payments(opening.id)
    assert len(payments) == 1
    payment = payments[0]
    assert payment["id"] == paid.payment_id
    assert (payment["amount"], payment["currency"]) == (Decimal("59.90"), "MYR")
    assert (payment["source"], payment["matched_by"]) == ("staff", "staff")
    assert payment["idempotency_key"] == f"staff:{key}"
    assert payment["created_by"] == a.staff_id and payment["received_at"] == received
    assert payment["note"] == "Seen in the bank app" and payment["method_kind"] == "transfer"

    started = await a.row("subscriptions", sub)
    assert started["status"] == "active"
    assert before - timedelta(seconds=5) <= started["started_at"] <= after
    assert started["activated_at"] == started["started_at"]
    assert started["renewal_anchor"] == started["started_at"]
    month = await a.next_renewal(started["started_at"], started["started_at"], "month")
    assert started["renews_at"] == month
    assert paid.invoice.period_start == started["started_at"]
    assert paid.invoice.period_end == started["renews_at"]
    activated = await a.events(sub, "activated")
    assert len(activated) == 1
    assert activated[0]["actor"] == str(a.staff_id)
    assert (activated[0]["from_status"], activated[0]["to_status"]) == ("pending", "active")
    assert activated[0]["payload"] == {
        "invoice_id": opening.id,
        "payment_id": str(paid.payment_id),
        "via": "payment",
    }
    audit = await a.q(
        "SELECT actor_id, client_id FROM console_audit "
        "WHERE action = 'payments.invoice.paid' AND target_id = %s",
        opening.id,
    )
    assert audit == [{"actor_id": a.staff_id, "client_id": a.tenant}]

    replay = await a.pay(opening.id, key, received_at=received)
    assert replay.replayed is True
    assert (replay.payment_id, replay.activated, replay.resumed) == (paid.payment_id, True, False)
    assert replay.invoice == paid.invoice
    assert len(await a.payments(opening.id)) == 1
    assert len(await a.events(sub, "activated")) == 1
    assert (await a.row("subscriptions", sub))["version"] == started["version"]

    await refused(a.pay(opening.id, uuid.uuid4()), 409, "invoice_already_paid")
    assert len(await a.payments(opening.id)) == 1


async def test_a_unique_violation_maps_by_its_constraint_name(
    a: Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only payments_one_per_invoice reads as "already paid"; any other unique key is a bug and
    is raised as it is (the locks make both unreachable in practice)."""
    from psycopg.errors import UniqueViolation

    contact = await a.contact()
    target = await a.invoice(contact=contact)
    other = await a.invoice(contact=contact)
    taken = (await a.row("invoices", other))["reference"]
    real = db.q.fetch_one
    mode = {"clash": "reference"}

    async def clashing(path: str, params: Any = None, *, conn: Any) -> Any:
        if path == "payments/payment_insert.sql":
            if mode["clash"] == "reference":
                await conn.execute(
                    "UPDATE invoices SET reference = %s WHERE id = %s", (taken, target)
                )
            else:  # a payment for the invoice slipped in under another key
                await conn.execute(
                    "INSERT INTO payments (client_id, invoice_id, amount, currency, received_at,"
                    " source, matched_by, idempotency_key) SELECT client_id, id, amount,"
                    " currency, now(), 'staff', 'staff', 'staff:slipped' FROM invoices"
                    " WHERE id = %s",
                    (target,),
                )
        return await real(path, params, conn=conn)

    monkeypatch.setattr(db.q, "fetch_one", clashing)
    with pytest.raises(UniqueViolation) as raised:
        await a.pay(target)
    assert raised.value.diag.constraint_name == "invoices_reference_uq"
    mode["clash"] = "payment"
    await refused(a.pay(target), 409, "invoice_already_paid")
    monkeypatch.setattr(db.q, "fetch_one", real)
    assert await a.payments(target) == []


async def test_paying_an_invoice_that_is_not_the_opening_one_logs_the_unset_period(
    a: Ledger,
) -> None:
    from structlog.testing import capture_logs

    await a.payments_on()
    sub = await a.subscription(status="pending")
    stamped = datetime.now(UTC) - timedelta(days=3)
    invoice_id = await a.invoice(subscription=sub, period_start=stamped)

    with capture_logs() as logs:
        paid = await a.pay(invoice_id)

    assert paid.activated is True and paid.invoice.status == "paid"
    assert len(await a.payments(invoice_id)) == 1  # the payment stands
    (entry,) = [e for e in logs if e["event"] == "payment_activation_period_unset"]
    assert entry["invoice_id"] == invoice_id and entry["stamped"] == 0
    assert entry["subscription_id"] == str(sub)


async def test_the_opening_invoice_paid_gets_the_period_and_nothing_is_logged(a: Ledger) -> None:
    from structlog.testing import capture_logs

    await a.payments_on()
    sub = await a.subscription(status="pending")
    opening = await a.run(invoices.issue_opening_invoice, sub, now=datetime.now(UTC))
    assert opening is not None
    with capture_logs() as logs:
        paid = await a.pay(opening.id)
    assert paid.invoice.period_start is not None
    assert not [e for e in logs if e["event"] == "payment_activation_period_unset"]


async def test_a_void_opening_invoice_is_never_given_a_period(a: Ledger) -> None:
    sub = await a.subscription(status="active", renews_at=datetime.now(UTC) + timedelta(days=10))
    void = await a.invoice(subscription=sub, status="void")

    async def stamp(conn: Any, client_id: UUID) -> list[dict[str, Any]]:
        return await db.q.fetch_all(
            "payments/invoice_set_period.sql",
            {"client_id": client_id, "subscription_id": sub},
            conn=conn,
        )

    assert await a.run(stamp) == []
    row = await a.row("invoices", void)
    assert row["period_start"] is None and row["period_end"] is None


async def test_mark_paid_refuses_void_draft_and_future(a: Ledger) -> None:
    contact = await a.contact()
    void = await a.invoice(status="void", contact=contact)
    draft = await a.invoice(status="draft", contact=contact)
    await refused(a.pay(void), 409, "invoice_not_payable")
    await refused(a.pay(draft), 409, "invoice_not_payable")

    issued = await a.invoice(contact=contact)
    tomorrow = datetime.now(UTC) + timedelta(days=1)
    await refused(a.pay(issued, received_at=tomorrow), 422, "received_in_future")
    await refused(a.pay(uuid.uuid4().hex), 404, "invoice_not_found")
    for invoice_id in (void, draft, issued):
        assert await a.payments(invoice_id) == []
    assert (await a.row("invoices", issued))["status"] == "issued"
    assert (await a.row("invoices", void))["status"] == "void"


async def cancelled_with_open_invoice(a: Ledger) -> tuple[UUID, str]:
    """A cancelled subscription whose running-period invoice was left open (cancel keeps it)."""
    now = datetime.now(UTC)
    sub = await a.subscription(
        status="cancelled",
        renews_at=now + timedelta(days=2),
        renewal_anchor=now - timedelta(days=28),
        cancelled_at=now - timedelta(days=1),
    )
    open_invoice = await a.invoice(
        status="overdue", subscription=sub, period_start=now - timedelta(days=28)
    )
    return sub, open_invoice


async def test_paying_an_open_invoice_of_a_cancelled_subscription_records_only(
    a: Ledger,
) -> None:
    sub, invoice_id = await cancelled_with_open_invoice(a)
    before = await a.row("subscriptions", sub)
    key = uuid.uuid4()
    received = datetime.now(UTC) - timedelta(hours=3)

    paid = await a.pay(invoice_id, key, received_at=received)

    assert (paid.replayed, paid.activated, paid.resumed, paid.recorded_only) == (
        False,
        False,
        False,
        True,
    )
    assert paid.invoice.status == "paid" and paid.invoice.paid_at == received
    payments = await a.payments(invoice_id)
    assert len(payments) == 1 and payments[0]["id"] == paid.payment_id
    assert (payments[0]["amount"], payments[0]["idempotency_key"]) == (
        Decimal("29.90"),
        f"staff:{key}",
    )
    assert (await a.row("invoices", invoice_id))["status"] == "paid"
    # The subscription row is exactly as it was: still cancelled, no dates or version moved.
    assert await a.row("subscriptions", sub) == before
    events = await a.events(sub, "payment_recorded")
    assert len(events) == 1
    assert (events[0]["from_status"], events[0]["to_status"]) == ("cancelled", "cancelled")
    assert events[0]["payload"] == {
        "invoice_id": invoice_id,
        "payment_id": str(paid.payment_id),
        "via": "payment",
        "recorded_only": True,
    }
    for action in ("activated", "resumed_paid", "resumed"):
        assert await a.events(sub, action) == []

    replay = await a.pay(invoice_id, key, received_at=received)
    assert replay.replayed is True and replay.payment_id == paid.payment_id
    assert (replay.activated, replay.resumed, replay.recorded_only) == (False, False, True)
    assert len(await a.payments(invoice_id)) == 1
    assert len(await a.events(sub, "payment_recorded")) == 1
    assert await a.row("subscriptions", sub) == before

    await refused(a.pay(invoice_id, uuid.uuid4()), 409, "invoice_already_paid")
    assert len(await a.payments(invoice_id)) == 1


async def test_two_staff_paying_a_cancelled_subscriptions_invoice_at_once_pay_once(
    a: Ledger,
) -> None:
    sub, invoice_id = await cancelled_with_open_invoice(a)
    before = await a.row("subscriptions", sub)

    results = await asyncio.gather(
        a.pay(invoice_id, uuid.uuid4(), hold=HOLD),
        a.pay(invoice_id, uuid.uuid4(), hold=HOLD),
        return_exceptions=True,
    )

    wins = [r for r in results if isinstance(r, MarkPaidOutcome)]
    losses: list[HTTPException] = [r for r in results if isinstance(r, HTTPException)]
    assert len(wins) == 1 and len(losses) == 1, results
    assert (losses[0].status_code, losses[0].detail) == (409, "invoice_already_paid")
    assert wins[0].recorded_only is True and wins[0].replayed is False
    assert len(await a.payments(invoice_id)) == 1
    assert len(await a.events(sub, "payment_recorded")) == 1
    assert await a.row("subscriptions", sub) == before


async def test_two_staff_marking_paid_at_once_pay_once(a: Ledger) -> None:
    await a.payments_on()
    sub = await a.subscription(status="pending")
    opening = await a.run(invoices.issue_opening_invoice, sub, now=datetime.now(UTC))
    assert opening is not None
    version = (await a.row("subscriptions", sub))["version"]

    results = await asyncio.gather(
        a.pay(opening.id, uuid.uuid4(), hold=HOLD),
        a.pay(opening.id, uuid.uuid4(), hold=HOLD),
        return_exceptions=True,
    )

    wins = [r for r in results if isinstance(r, MarkPaidOutcome)]
    losses: list[HTTPException] = [r for r in results if isinstance(r, HTTPException)]
    assert len(wins) == 1 and len(losses) == 1, results
    assert (losses[0].status_code, losses[0].detail) == (409, "invoice_already_paid")
    assert wins[0].activated is True and wins[0].replayed is False
    assert len(await a.payments(opening.id)) == 1
    assert len(await a.events(sub, "activated")) == 1
    assert (await a.row("subscriptions", sub))["version"] == version + 1


async def test_one_staff_pressing_twice_at_once_gets_the_same_answer(a: Ledger) -> None:
    await a.payments_on()
    sub = await a.subscription(status="pending")
    opening = await a.run(invoices.issue_opening_invoice, sub, now=datetime.now(UTC))
    assert opening is not None
    key = uuid.uuid4()
    received = datetime.now(UTC) - timedelta(minutes=5)

    results = await asyncio.gather(
        a.pay(opening.id, key, received_at=received, hold=HOLD),
        a.pay(opening.id, key, received_at=received, hold=HOLD),
    )

    assert sorted(r.replayed for r in results) == [False, True]
    assert results[0].payment_id == results[1].payment_id
    assert all(r.activated for r in results)
    assert len(await a.payments(opening.id)) == 1
    assert len(await a.events(sub, "activated")) == 1


async def test_mark_paid_and_void_at_once_never_deadlock(a: Ledger) -> None:
    for first in ("pay", "void"):
        await a.payments_on()
        sub = await a.subscription(status="pending")
        opening = await a.run(invoices.issue_opening_invoice, sub, now=datetime.now(UTC))
        assert opening is not None

        async def void(invoice_id: str = opening.id) -> InvoiceRecord:
            async with tx(a.w.pool, str(a.tenant)) as conn:
                done = await invoices.void_invoice(
                    conn, a.tenant, invoice_id, actor_user_id=a.staff_id, reason="Duplicate"
                )
                await asyncio.sleep(HOLD)
                return done

        async def cancel(subscription: UUID = sub) -> int:
            async with tx(a.w.pool, str(a.tenant)) as conn:
                done = await invoices.void_for_subscription(
                    conn,
                    a.tenant,
                    subscription,
                    reason="subscription_cancelled",
                    from_period=datetime.now(UTC),
                )
                await asyncio.sleep(HOLD)
                return done

        racers = [a.pay(opening.id, hold=HOLD), void(), cancel()]
        if first == "void":
            racers.reverse()
        results = await asyncio.gather(*racers, return_exceptions=True)
        assert not any(isinstance(r, psycopg.errors.DeadlockDetected) for r in results), results
        assert not any(
            isinstance(r, Exception) and not isinstance(r, HTTPException) for r in results
        )
        final = await a.row("invoices", opening.id)
        assert final["status"] in {"paid", "void"}
        assert len(await a.payments(opening.id)) == (1 if final["status"] == "paid" else 0)


async def test_paying_the_overdue_invoice_resumes_an_unpaid_pause(a: Ledger) -> None:
    now = datetime.now(UTC)
    sub = await a.subscription(
        status="paused",
        renews_at=now + timedelta(days=10),
        paused_at=now - timedelta(days=1),
        pause_reason="unpaid",
    )
    overdue = await a.invoice(
        status="overdue", subscription=sub, period_start=now - timedelta(days=20)
    )

    paid = await a.pay(overdue)

    assert (paid.resumed, paid.activated) == (True, False)
    resumed = await a.row("subscriptions", sub)
    assert resumed["status"] == "active"
    assert resumed["pause_reason"] is None and resumed["paused_at"] is None
    events = await a.events(sub, "resumed_paid")
    assert len(events) == 1
    assert (events[0]["from_status"], events[0]["to_status"]) == ("paused", "active")
    assert events[0]["payload"]["payment_id"] == str(paid.payment_id)


async def test_an_unpaid_pause_stays_while_another_invoice_is_overdue(a: Ledger) -> None:
    now = datetime.now(UTC)
    sub = await a.subscription(
        status="paused", renews_at=now + timedelta(days=10), paused_at=now, pause_reason="unpaid"
    )
    one = await a.invoice(status="overdue", subscription=sub, period_start=now - timedelta(days=50))
    await a.invoice(status="overdue", subscription=sub, period_start=now - timedelta(days=20))
    paid = await a.pay(one)
    assert paid.resumed is False
    assert (await a.row("subscriptions", sub))["status"] == "paused"
    assert await a.events(sub, "resumed_paid") == []


async def test_a_staff_pause_stays_paused_and_a_system_payment_has_no_audit(a: Ledger) -> None:
    now = datetime.now(UTC)
    sub = await a.subscription(status="paused", renews_at=now + timedelta(days=10), paused_at=now)
    overdue = await a.invoice(
        status="overdue", subscription=sub, period_start=now - timedelta(days=20)
    )
    paid = await a.pay(overdue, actor=False)
    assert paid.resumed is False and paid.invoice.status == "paid"
    stays = await a.row("subscriptions", sub)
    assert (stays["status"], stays["pause_reason"]) == ("paused", None)
    assert (await a.payments(overdue))[0]["created_by"] is None
    assert (await a.q("SELECT 1 FROM console_audit WHERE target_id = %s", overdue)) == []

    pending = await a.subscription(status="pending")
    await a.payments_on()
    opening = await a.run(invoices.issue_opening_invoice, pending, now=now)
    assert opening is not None
    await a.pay(opening.id, actor=False)
    assert (await a.events(pending, "activated"))[0]["actor"] == "system:payment"


async def test_resume_and_cancel_now_clear_the_unpaid_pause_reason(a: Ledger) -> None:
    now = datetime.now(UTC)
    for action, status in (("resume", "active"), ("cancel_now", "cancelled")):
        sub = await a.subscription(
            status="paused", renews_at=now + timedelta(days=9), paused_at=now, pause_reason="unpaid"
        )
        version = (await a.row("subscriptions", sub))["version"]
        async with tx(a.w.pool, str(a.tenant)) as conn:
            applied = await db.q.fetch_one(
                "subscriptions/subscription_apply.sql",
                {
                    "client_id": a.tenant,
                    "subscription_id": sub,
                    "version": version,
                    "action": action,
                    "resume_at": None,
                    "renews_at": None,
                    "immediate_plan": False,
                    "scheduled_plan": False,
                    "plan_code": None,
                    "price": None,
                    "currency": None,
                    "target_interval": None,
                    "effective_at": None,
                },
                conn=conn,
            )
        assert applied is not None and applied["status"] == status
        assert (await a.row("subscriptions", sub))["pause_reason"] is None


# --- void ----------------------------------------------------------------------------------------


async def test_void_from_every_open_status_and_never_a_paid_one(a: Ledger) -> None:
    for status in ("draft", "issued", "overdue", "proof_submitted", "cash_pending"):
        invoice_id = await a.invoice(status=status, contact=await a.contact())
        voided = await a.run(
            invoices.void_invoice,
            invoice_id,
            actor_user_id=a.staff_id,
            reason="  Duplicate of an earlier invoice  ",
        )
        assert voided.status == "void", status
        stored = await a.row("invoices", invoice_id)
        assert stored["void_reason"] == "Duplicate of an earlier invoice"
        assert stored["voided_by"] == a.staff_id and stored["voided_at"] is not None
        await refused(
            a.run(invoices.void_invoice, invoice_id, actor_user_id=a.staff_id, reason="Again"),
            409,
            "invoice_void",
        )
    audits = await a.q(
        "SELECT count(*) AS n FROM console_audit "
        "WHERE action = 'payments.invoice.voided' AND actor_id = %s",
        a.staff_id,
    )
    assert audits[0]["n"] == 5

    paid = await a.invoice(contact=await a.contact())
    await a.pay(paid)
    await refused(
        a.run(invoices.void_invoice, paid, actor_user_id=a.staff_id, reason="Oops"),
        409,
        "invoice_paid",
    )
    assert (await a.row("invoices", paid))["status"] == "paid"


async def test_void_for_subscription_keeps_the_running_period(a: Ledger) -> None:
    now = datetime.now(UTC)
    sub = await a.subscription(status="active", renews_at=now + timedelta(days=3))
    running = await a.invoice(subscription=sub, period_start=now - timedelta(days=27))
    following = await a.invoice(subscription=sub, period_start=now + timedelta(days=3))
    opening = await a.invoice(status="proof_submitted", subscription=sub)
    paid_later = await a.invoice(subscription=sub, period_start=now + timedelta(days=33))
    await a.pay(paid_later)
    other = await a.subscription(status="active", renews_at=now + timedelta(days=3))
    untouched = await a.invoice(subscription=other, period_start=now + timedelta(days=3))

    voided = await a.run(
        invoices.void_for_subscription, sub, reason="subscription_cancelled", from_period=now
    )

    assert voided == 2
    assert (await a.row("invoices", running))["status"] == "issued"
    assert (await a.row("invoices", paid_later))["status"] == "paid"
    assert (await a.row("invoices", untouched))["status"] == "issued"
    for invoice_id in (following, opening):
        stored = await a.row("invoices", invoice_id)
        assert (stored["status"], stored["void_reason"]) == ("void", "subscription_cancelled")
        assert stored["voided_by"] is None and stored["voided_at"] is not None


# --- claims --------------------------------------------------------------------------------------


async def test_a_customer_claim_is_noted_once_and_only_on_their_own_invoice(a: Ledger) -> None:
    mine, theirs = await a.contact(), await a.contact("Farid Hakim")
    issued = await a.invoice(contact=mine)
    overdue = await a.invoice(status="overdue", contact=mine)
    other = await a.invoice(contact=theirs)

    noted = await a.run(invoices.record_claim, mine, issued, "  Paid from Bank Contoh at 3pm  ")
    assert noted == "noted"
    stored = await a.row("invoices", issued)
    assert stored["status"] == "proof_submitted"
    assert stored["claim_note"] == "Paid from Bank Contoh at 3pm"
    assert stored["claimed_at"] is not None
    assert await a.run(invoices.record_claim, mine, issued, "again") == "already_noted"
    assert (await a.row("invoices", issued))["claim_note"] == "Paid from Bank Contoh at 3pm"

    assert await a.run(invoices.record_claim, mine, overdue, "x" * 400) == "noted"
    assert (await a.row("invoices", overdue))["claim_note"] == "x" * 300

    assert await a.run(invoices.record_claim, mine, other, "mine") == "not_open"
    assert (await a.row("invoices", other))["status"] == "issued"
    assert (await a.row("invoices", other))["claim_note"] is None

    paid = await a.invoice(contact=mine)
    await a.pay(paid)
    assert await a.run(invoices.record_claim, mine, paid, None) == "already_paid"
    legacy = await a.invoice(status="paid", contact=mine, paid_at=datetime.now(UTC))
    assert await a.run(invoices.record_claim, mine, legacy, None) == "not_open"
    for status in ("void", "draft", "cash_pending"):
        invoice_id = await a.invoice(status=status, contact=mine)
        assert await a.run(invoices.record_claim, mine, invoice_id, None) == "not_open"
        assert (await a.row("invoices", invoice_id))["status"] == status


# --- what a customer sees ------------------------------------------------------------------------


async def test_customer_invoices_show_only_their_open_and_truly_paid_ones(a: Ledger) -> None:
    mine, theirs = await a.contact(), await a.contact("Farid Hakim")
    now = datetime.now(UTC)
    later = await a.invoice(contact=mine, due_at=now + timedelta(days=9))
    sooner = await a.invoice(status="overdue", contact=mine, due_at=now - timedelta(days=2))
    claimed = await a.invoice(
        status="proof_submitted", contact=mine, due_at=now + timedelta(days=1)
    )
    paid = await a.invoice(contact=mine)
    await a.pay(paid)
    old_paid = await a.invoice(status="paid", contact=mine, paid_at=now - timedelta(days=90))
    await a.ledger_payment(old_paid, received_at=now - timedelta(days=90))
    for status in ("draft", "void"):
        await a.invoice(status=status, contact=mine)
    await a.invoice(status="paid", contact=mine, paid_at=now)  # an old seed: paid, no payment
    foreign = await a.invoice(contact=theirs)

    shown = await a.run(invoices.customer_invoices, mine, limit=10)
    assert [i.id for i in shown] == [sooner, claimed, later, paid]
    assert all(i.contact_id == mine for i in shown)

    assert [i.id for i in await a.run(invoices.customer_invoices, mine, limit=2)] == [
        sooner,
        claimed,
    ]
    reference = (await a.row("invoices", later))["reference"]
    typed = reference.lower().replace("-", " ")
    found = await a.run(invoices.customer_invoices, mine, reference=typed)
    assert [i.id for i in found] == [later]
    foreign_ref = (await a.row("invoices", foreign))["reference"]
    assert await a.run(invoices.customer_invoices, mine, reference=foreign_ref) == []
    assert await a.run(invoices.customer_invoices, mine, reference="nonsense") == []


# --- tenancy and the ledger's grants -------------------------------------------------------------


async def test_another_company_cannot_read_pay_void_or_claim(a: Ledger, b: Ledger) -> None:
    await a.payments_on()
    await b.payments_on()
    contact = await a.contact()
    sub = await a.subscription(contact, status="pending")
    opening = await a.run(invoices.issue_opening_invoice, sub, now=datetime.now(UTC))
    assert opening is not None

    assert await b.run(invoices.customer_invoices, contact) == []
    await refused(b.pay(opening.id), 404, "invoice_not_found")
    await refused(
        b.run(invoices.void_invoice, opening.id, actor_user_id=b.staff_id, reason="No"),
        404,
        "invoice_not_found",
    )
    await refused(b.run(invoices.record_claim, contact, opening.id, None), 404, "invoice_not_found")
    assert await b.run(invoices.issue_opening_invoice, sub, now=datetime.now(UTC)) is None
    assert (
        await b.run(
            invoices.void_for_subscription,
            sub,
            reason="subscription_cancelled",
            from_period=datetime.now(UTC),
        )
        == 0
    )
    stored = await a.row("invoices", opening.id)
    assert (stored["status"], stored["claim_note"]) == ("issued", None)
    assert await a.payments(opening.id) == []
    assert (await a.row("subscriptions", sub))["status"] == "pending"


async def test_the_app_role_cannot_change_or_delete_a_payment(a: Ledger) -> None:
    invoice_id = await a.invoice(contact=await a.contact())
    await a.pay(invoice_id)
    for statement in (
        "UPDATE payments SET amount = 1.00 WHERE invoice_id = %s",
        "DELETE FROM payments WHERE invoice_id = %s",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with tx(a.w.pool, str(a.tenant)) as conn:
                await conn.execute(statement, (invoice_id,))  # type: ignore[arg-type]
    assert (await a.payments(invoice_id))[0]["amount"] == Decimal("29.90")
