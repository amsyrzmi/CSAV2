"""The invoice ledger: issue, mark paid (which starts or resumes a subscription), void and claim.

Every function runs inside the caller's tenant transaction (`tx(pool, client_id)`) and names the
company in every statement. Refusals are `HTTPException`s with a short code: 404
`invoice_not_found` (also for another company's invoice); 409 `invoice_already_paid`,
`invoice_not_payable`, `request_key_reused`, `invoice_paid` and `invoice_void`; 422
`received_in_future`. `invalid_note` and `invalid_reason` (422) are a direct caller's guard only:
the console routes validate the note and the reason in their bodies first (a field-list 422).

The ledger is the truth. An invoice becomes `paid` only in the transaction that inserts its
`payments` row (`invoice_mark_paid.sql` matches only when that row exists), and `payments` is
append-only for `csa_app`. Amounts are `Decimal` from `numeric(12,2)` and are copied from the
invoice into the payment by SQL, so a payment always equals what was invoiced, and an issued
invoice keeps its amount whatever happens to the plan or the subscription's price later.

LOCK ORDER, used by every function here (and expected of the invoice job and the subscriptions
executor): the subscription row first (`subscription_for_invoice.sql` or
`subscriptions/subscription_lock.sql`, FOR UPDATE), then the invoice row (`invoice_lock.sql`, or an
UPDATE of invoices). One order everywhere means mark paid, void, claim, `void_for_subscription` and
the job cannot deadlock; under READ COMMITTED the second of two racing callers reads the state the
first committed, so two staff pressing Mark paid at once record one payment.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import structlog
from fastapi import HTTPException
from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from csa.core import db
from csa.payments.contracts import OPEN_STATUSES, InvoiceStatus, MethodKind
from csa.payments.reference import new_invoice_id, new_reference, normalise_reference
from csa.payments.settings import get_settings
from csa.subscriptions.service import record_event

logger = structlog.get_logger(__name__)

Conn = AsyncConnection[Any]

ClaimOutcome = Literal["noted", "already_noted", "already_paid", "not_open"]

# The random source for references; a test replaces it to force collisions.
randbelow = secrets.randbelow
# One attempt and up to five retries when a fresh reference (or id) is already taken.
ISSUE_ATTEMPTS = 6
_RETRIED_CONSTRAINTS = frozenset({"invoices_reference_uq", "invoices_pkey"})
# A received time this far ahead of the server clock is still accepted (a browser clock ahead).
CLOCK_SKEW = timedelta(minutes=5)
NOTE_MAX = 300
SYSTEM_ACTOR = "system:payment"
_NO_PLAN_CHANGE = {
    "resume_at": None,
    "renews_at": None,
    "immediate_plan": False,
    "scheduled_plan": False,
    "plan_code": None,
    "price": None,
    "currency": None,
    "target_interval": None,
    "effective_at": None,
}


@dataclass(frozen=True)
class InvoiceRecord:
    """One invoice as the service returns it; staff-only fields (void reason, voider) stay out."""

    id: str
    client_id: UUID
    contact_id: UUID | None
    subscription_id: UUID | None
    reference: str | None
    status: InvoiceStatus
    amount: Decimal
    currency: str
    issued_at: datetime
    due_at: datetime | None
    paid_at: datetime | None
    period_start: datetime | None
    period_end: datetime | None
    plan_name: str | None
    claim_note: str | None
    claimed_at: datetime | None
    method_kind: MethodKind | None


@dataclass(frozen=True)
class MarkPaidOutcome:
    """What Mark paid did; a replay of the same request key returns the first answer."""

    invoice: InvoiceRecord
    payment_id: UUID
    replayed: bool  # this request key had already paid the invoice; nothing was written now
    activated: bool  # the payment started a pending subscription
    resumed: bool  # the payment lifted an unpaid pause
    # The invoice belongs to a cancelled subscription: the payment is on the ledger and the invoice
    # is paid, but the subscription was left exactly as it was (it never resurrects).
    recorded_only: bool


def _record(row: dict[str, Any]) -> InvoiceRecord:
    return InvoiceRecord(
        id=row["id"],
        client_id=row["client_id"],
        contact_id=row["contact_id"],
        subscription_id=row["subscription_id"],
        reference=row["reference"],
        status=row["status"],
        amount=row["amount"],
        currency=row["currency"],
        issued_at=row["issued_at"],
        due_at=row["due_at"],
        paid_at=row["paid_at"],
        period_start=row["period_start"],
        period_end=row["period_end"],
        plan_name=row["plan_name"],
        claim_note=row["claim_note"],
        claimed_at=row["claimed_at"],
        method_kind=row["method_kind"],
    )


def _clean_text(text: str | None, *, limit: int = NOTE_MAX) -> str | None:
    """`text` without surrounding whitespace, None when nothing is left."""
    if text is None:
        return None
    cleaned = text.strip()
    if len(cleaned) > limit:
        raise ValueError("too long")
    return cleaned or None


async def _lock_subscription(
    conn: Conn, client_id: UUID, subscription_id: UUID
) -> dict[str, Any] | None:
    """Lock the subscription (first in the lock order) and read its current and next terms."""
    row: dict[str, Any] | None = await db.q.fetch_one(
        "payments/subscription_for_invoice.sql",
        {"client_id": client_id, "subscription_id": subscription_id},
        conn=conn,
    )
    return row


async def _lock_invoice(
    conn: Conn, client_id: UUID, invoice_id: str
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Lock the invoice's subscription, then the invoice (404 when the company has no such one)."""
    key = {"client_id": client_id, "invoice_id": invoice_id}
    found = await db.q.fetch_one("payments/invoice_get.sql", key, conn=conn)
    if found is None:
        raise HTTPException(404, "invoice_not_found")
    subscription = None
    if found["subscription_id"] is not None:  # never changes once set, so safe to read unlocked
        subscription = await _lock_subscription(conn, client_id, found["subscription_id"])
    row = await db.q.fetch_one("payments/invoice_lock.sql", key, conn=conn)
    if row is None:
        raise HTTPException(404, "invoice_not_found")
    return subscription, row


async def _insert(
    conn: Conn,
    client_id: UUID,
    subscription: dict[str, Any],
    *,
    amount: Decimal,
    currency: str,
    plan_name: str,
    now: datetime,
    due_at: datetime,
    period_start: datetime | None,
    period_end: datetime | None,
) -> InvoiceRecord | None:
    """Insert the invoice in a savepoint, drawing a new reference while one collides.

    None when the subscription already has an invoice for this period (any status).
    """
    line_items = [{"description": plan_name, "quantity": 1, "unit_price": f"{amount:.2f}"}]
    last: UniqueViolation | None = None
    for _ in range(ISSUE_ATTEMPTS):
        params = {
            "id": new_invoice_id(now),
            "client_id": client_id,
            "contact_id": subscription["contact_id"],
            "subscription_id": subscription["id"],
            "reference": new_reference(randbelow),
            "amount": amount,
            "currency": currency,
            "issued_at": now,
            "due_at": due_at,
            "period_start": period_start,
            "period_end": period_end,
            "line_items": Jsonb(line_items),
        }
        try:
            async with conn.transaction():
                row = await db.q.fetch_one("payments/invoice_insert.sql", params, conn=conn)
        except UniqueViolation as exc:
            if exc.diag.constraint_name not in _RETRIED_CONSTRAINTS:
                raise
            last = exc
            continue
        return _record(row) if row is not None else None
    raise RuntimeError(
        f"invoice reference collided {ISSUE_ATTEMPTS} times for subscription {subscription['id']}"
    ) from last


async def issue_opening_invoice(
    conn: Conn, client_id: UUID, subscription_id: UUID, *, now: datetime
) -> InvoiceRecord | None:
    """The first invoice of a pending subscription, due `lead_days` after `now`.

    None when payments are off, the subscription is not pending (or not this company's), it already
    has an opening invoice (any status, void included) or its price is zero. The amount and currency
    are the subscription's; the invoice has no period until it is paid.
    """
    settings = await get_settings(conn, client_id)
    if not settings.enabled:
        return None
    subscription = await _lock_subscription(conn, client_id, subscription_id)
    if subscription is None or subscription["status"] != "pending":
        return None
    amount: Decimal = subscription["price"]
    if amount <= 0:  # a payment must be more than zero, so such an invoice could never be paid
        return None
    return await _insert(
        conn,
        client_id,
        subscription,
        amount=amount,
        currency=subscription["currency"],
        plan_name=subscription["plan_name"],
        now=now,
        due_at=now + timedelta(days=settings.lead_days),
        period_start=None,
        period_end=None,
    )


async def issue_period_invoice(
    conn: Conn, client_id: UUID, subscription_id: UUID, *, now: datetime
) -> InvoiceRecord | None:
    """The invoice for the period starting at `renews_at`, once that is within `lead_days`.

    Only an active subscription that is not cancelling at period end, with payments on. The terms
    are those of the plan in effect at `renews_at` (`subscription_for_invoice.sql`). Due when the
    period starts. None when the period already has an invoice (any status) or the amount is zero.
    """
    settings = await get_settings(conn, client_id)
    if not settings.enabled:
        return None
    subscription = await _lock_subscription(conn, client_id, subscription_id)
    if (
        subscription is None
        or subscription["status"] != "active"
        or subscription["cancel_at_period_end"]
        or subscription["renews_at"] is None
        or subscription["renews_at"] > now + timedelta(days=settings.lead_days)
    ):
        return None
    amount: Decimal = subscription["next_price"]
    if amount <= 0:
        return None
    return await _insert(
        conn,
        client_id,
        subscription,
        amount=amount,
        currency=subscription["next_currency"],
        plan_name=subscription["next_plan_name"],
        now=now,
        due_at=subscription["renews_at"],
        period_start=subscription["renews_at"],
        period_end=subscription["next_period_end"],
    )


async def _apply(
    conn: Conn, client_id: UUID, subscription: dict[str, Any], action: Literal["activate", "resume"]
) -> dict[str, Any]:
    """Apply `activate` or `resume` to the locked subscription through subscription_apply.sql."""
    applied: dict[str, Any] | None = await db.q.fetch_one(
        "subscriptions/subscription_apply.sql",
        {
            "client_id": client_id,
            "subscription_id": subscription["id"],
            "version": subscription["version"],
            "action": action,
            **_NO_PLAN_CHANGE,
        },
        conn=conn,
    )
    if applied is None:
        raise RuntimeError(f"subscription {subscription['id']} {action} matched no row")
    return applied


async def mark_paid(
    conn: Conn,
    client_id: UUID,
    invoice_id: str,
    *,
    actor_user_id: UUID | None,
    request_key: UUID,
    method_kind: MethodKind | None,
    received_at: datetime,
    note: str | None,
) -> MarkPaidOutcome:
    """Record that the invoice was paid: one ledger row, the invoice `paid`, in this transaction.

    The same `request_key` again returns the first answer (`replayed=True`) and writes nothing. A
    pending subscription starts (event `activated`) and its opening invoice gets its first period; a
    subscription paused for being unpaid resumes (event `resumed_paid`) when none of its other
    invoices is overdue. A void, draft or already paid invoice is refused. An open invoice of a
    cancelled subscription is still paid and recorded (money that arrived must be recordable), but
    the subscription is not touched: no activation, no resume, no status or date change. The result
    says `recorded_only` and a `payment_recorded` event with `recorded_only: true` goes on its
    timeline. Nothing is ever un-voided or un-cancelled.
    """
    if received_at.tzinfo is None:
        raise ValueError("received_at must be timezone-aware")
    subscription, row = await _lock_invoice(conn, client_id, invoice_id)
    key = f"staff:{request_key}"
    prior = await db.q.fetch_one(
        "payments/payment_by_key.sql", {"client_id": client_id, "idempotency_key": key}, conn=conn
    )
    if prior is not None:
        if prior["invoice_id"] != invoice_id:
            raise HTTPException(409, "request_key_reused")
        return MarkPaidOutcome(
            invoice=_record(row),
            payment_id=prior["id"],
            replayed=True,
            activated=prior["activated"],
            resumed=prior["resumed"],
            recorded_only=prior["recorded_only"],
        )
    if row["status"] == "paid":
        raise HTTPException(409, "invoice_already_paid")
    if row["status"] not in OPEN_STATUSES or row["amount"] <= 0:
        raise HTTPException(409, "invoice_not_payable")
    if received_at > datetime.now(UTC) + CLOCK_SKEW:
        raise HTTPException(422, "received_in_future")
    try:
        cleaned_note = _clean_text(note)
    except ValueError as exc:
        raise HTTPException(422, "invalid_note") from exc

    try:
        async with conn.transaction():
            payment = await db.q.fetch_one(
                "payments/payment_insert.sql",
                {
                    "client_id": client_id,
                    "invoice_id": invoice_id,
                    "received_at": received_at,
                    "method_kind": method_kind,
                    "source": "staff",
                    "matched_by": "staff",
                    "created_by": actor_user_id,
                    "note": cleaned_note,
                    "idempotency_key": key,
                },
                conn=conn,
            )
    except UniqueViolation as exc:  # the locks above make these unreachable; never a 500 if not
        if exc.diag.constraint_name == "payments_idempotency_uq":
            raise HTTPException(409, "request_key_reused") from exc
        if exc.diag.constraint_name == "payments_one_per_invoice":
            raise HTTPException(409, "invoice_already_paid") from exc
        raise  # any other unique key is a bug, never an "already paid"
    if payment is None:
        raise RuntimeError(f"payment insert for invoice {invoice_id} returned no row")
    payment_id: UUID = payment["id"]
    paid = await db.q.fetch_one(
        "payments/invoice_mark_paid.sql",
        {
            "client_id": client_id,
            "invoice_id": invoice_id,
            "payment_id": payment_id,
            "received_at": received_at,
            "method_kind": method_kind,
        },
        conn=conn,
    )
    if paid is None:
        raise RuntimeError(f"invoice {invoice_id} was not marked paid under its lock")

    activated = resumed = recorded_only = False
    if subscription is not None:
        actor = str(actor_user_id) if actor_user_id is not None else SYSTEM_ACTOR
        payload = {"invoice_id": invoice_id, "payment_id": str(payment_id), "via": "payment"}
        if subscription["status"] == "pending":
            applied = await _apply(conn, client_id, subscription, "activate")
            await record_event(
                conn,
                client_id,
                subscription["id"],
                actor=actor,
                action="activated",
                from_status="pending",
                to_status=applied["status"],
                payload=payload,
            )
            stamped = await db.q.fetch_all(
                "payments/invoice_set_period.sql",
                {"client_id": client_id, "subscription_id": subscription["id"]},
                conn=conn,
            )
            if [r["id"] for r in stamped] != [invoice_id]:
                # The paid invoice should be the opening one and get the first period. Logged,
                # never raised: the payment stands (money received is always recordable), and
                # staff see an invoice without its period.
                logger.error(
                    "payment_activation_period_unset",
                    client_id=str(client_id),
                    invoice_id=invoice_id,
                    subscription_id=str(subscription["id"]),
                    stamped=len(stamped),
                )
            activated = True
        elif (
            subscription["status"] == "paused"
            and subscription["pause_reason"] == "unpaid"
            and paid["other_overdue"] == 0
        ):
            applied = await _apply(conn, client_id, subscription, "resume")
            await record_event(
                conn,
                client_id,
                subscription["id"],
                actor=actor,
                action="resumed_paid",
                from_status="paused",
                to_status=applied["status"],
                payload=payload,
            )
            resumed = True
        elif subscription["status"] == "cancelled":
            await record_event(
                conn,
                client_id,
                subscription["id"],
                actor=actor,
                action="payment_recorded",
                from_status="cancelled",
                to_status="cancelled",
                payload={**payload, "recorded_only": True},
            )
            recorded_only = True
    if actor_user_id is not None:
        await db.q.execute(
            "console/audit.sql",
            (
                actor_user_id,
                client_id,
                "payments.invoice.paid",
                invoice_id,
                Jsonb({"payment_id": str(payment_id)}),
            ),
            conn=conn,
        )
    final = await db.q.fetch_one(
        "payments/invoice_get.sql", {"client_id": client_id, "invoice_id": invoice_id}, conn=conn
    )
    if final is None:
        raise RuntimeError(f"invoice {invoice_id} vanished inside its transaction")
    return MarkPaidOutcome(
        invoice=_record(final),
        payment_id=payment_id,
        replayed=False,
        activated=activated,
        resumed=resumed,
        recorded_only=recorded_only,
    )


async def void_invoice(
    conn: Conn, client_id: UUID, invoice_id: str, *, actor_user_id: UUID, reason: str
) -> InvoiceRecord:
    """Staff void an invoice that is not paid: 409 `invoice_paid` or `invoice_void` otherwise."""
    try:
        cleaned = _clean_text(reason)
    except ValueError as exc:
        raise HTTPException(422, "invalid_reason") from exc
    if cleaned is None:
        raise HTTPException(422, "invalid_reason")
    _, row = await _lock_invoice(conn, client_id, invoice_id)
    if row["status"] == "paid":
        raise HTTPException(409, "invoice_paid")
    if row["status"] == "void":
        raise HTTPException(409, "invoice_void")
    done = await db.q.fetch_one(
        "payments/invoice_void.sql",
        {
            "client_id": client_id,
            "invoice_id": invoice_id,
            "reason": cleaned,
            "voided_by": actor_user_id,
        },
        conn=conn,
    )
    if done is None:
        raise RuntimeError(f"invoice {invoice_id} in status {row['status']} could not be voided")
    await db.q.execute(
        "console/audit.sql",
        (actor_user_id, client_id, "payments.invoice.voided", invoice_id, Jsonb({})),
        conn=conn,
    )
    final = await db.q.fetch_one(
        "payments/invoice_get.sql", {"client_id": client_id, "invoice_id": invoice_id}, conn=conn
    )
    if final is None:
        raise RuntimeError(f"invoice {invoice_id} vanished inside its transaction")
    return _record(final)


async def void_for_subscription(
    conn: Conn,
    client_id: UUID,
    subscription_id: UUID,
    *,
    reason: Literal["subscription_cancelled", "subscription_expired", "subscription_changed"],
    from_period: datetime,
    include_opening: bool = True,
) -> int:
    """Void the subscription's open invoices for periods from `from_period` on, and its opening one.

    A running period's invoice (period_start before `from_period`) is left alone; so is the opening
    invoice (no period yet) when `include_opening` is false (a renewal date moved: the
    subscription goes on, the opening invoice stays owed). Returns how many were voided. The
    subscription lock comes first (a no-op when the caller already holds it).
    """
    key = {"client_id": client_id, "subscription_id": subscription_id}
    await db.q.fetch_one("subscriptions/subscription_lock.sql", key, conn=conn)
    rows = await db.q.fetch_all(
        "payments/void_for_subscription.sql",
        {
            **key,
            "reason": reason,
            "from_period": from_period,
            "include_opening": include_opening,
        },
        conn=conn,
    )
    return len(rows)


async def record_claim(
    conn: Conn, client_id: UUID, contact_id: UUID, invoice_id: str, note: str | None
) -> ClaimOutcome:
    """A customer says they paid their own invoice: an issued or overdue one waits for staff.

    Another contact's invoice is `not_open` and is not touched; so is a `paid` row without a payment
    (an old seed), which is never presented as paid. An id the company does not have is 404
    `invoice_not_found`.
    """
    _, row = await _lock_invoice(conn, client_id, invoice_id)
    if row["contact_id"] != contact_id:
        return "not_open"
    status = row["status"]
    if status in ("issued", "overdue"):
        trimmed = note.strip()[:NOTE_MAX].strip() if note is not None else ""
        await db.q.fetch_one(
            "payments/invoice_claim.sql",
            {
                "client_id": client_id,
                "invoice_id": invoice_id,
                "contact_id": contact_id,
                "note": trimmed or None,
            },
            conn=conn,
        )
        return "noted"
    if status == "proof_submitted":
        return "already_noted"
    if status == "paid" and row["has_payment"]:
        return "already_paid"
    return "not_open"


async def customer_invoices(
    conn: Conn,
    client_id: UUID,
    contact_id: UUID,
    *,
    reference: str | None = None,
    limit: int = 5,
) -> list[InvoiceRecord]:
    """The contact's own invoices a customer may hear about: open first, then recently paid.

    A paid invoice appears only with its ledger row. `reference` is normalised first; a text that is
    not a valid reference matches nothing, and a reference of another contact is never returned.
    """
    normalised = None
    if reference is not None:
        normalised = normalise_reference(reference)
        if normalised is None:
            return []
    rows = await db.q.fetch_all(
        "payments/customer_invoices.sql",
        {
            "client_id": client_id,
            "contact_id": contact_id,
            "reference": normalised,
            "open": sorted(OPEN_STATUSES),
            "limit": limit,
        },
        conn=conn,
    )
    return [_record(row) for row in rows]
