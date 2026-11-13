"""Staff reads for the Payments tab: the invoice list, one invoice in detail, the attention list.

Every function runs inside the caller's tenant transaction (`tx(pool, client_id)`). Reminder
addresses never leave the database here, and a contact shows as its id, name and channels only.
The name of the user who recorded a payment comes from the identity pool (`csa_app` cannot read
`console_users`); it is decoration, so a failure there shows no name rather than an error.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from fastapi import HTTPException
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from csa.core import db
from csa.core.tenancy import tx
from csa.payments.contracts import (
    FILTER_STATUSES,
    ContactBrief,
    InvoiceDetail,
    InvoiceFilter,
    InvoiceOut,
    InvoicePage,
    PaymentAttentionItem,
    PaymentAttentionPage,
    PaymentOut,
    ReminderOut,
)
from csa.payments.reference import normalise_reference
from csa.subscriptions.service import like_pattern, phone_digits

Conn = AsyncConnection[Any]
logger = structlog.get_logger(__name__)

# At most this many invoices of each attention kind.
ATTENTION_LIMIT = 100


def invoice_out(row: dict[str, Any]) -> InvoiceOut:
    """One invoice row of invoices_list.sql (or a statement with the same columns)."""
    return InvoiceOut.model_validate(_invoice_fields(row))


def _invoice_fields(row: dict[str, Any]) -> dict[str, Any]:
    contact = None
    if row["contact_id"] is not None:
        contact = ContactBrief(
            id=row["contact_id"],
            full_name=row["contact_full_name"],
            channels=list(row["contact_channels"] or []),
        )
    return {name: row[name] for name in _INVOICE_COLUMNS} | {"contact": contact}


_INVOICE_COLUMNS = tuple(name for name in InvoiceOut.model_fields if name != "contact")


async def list_invoices(
    conn: Conn,
    client_id: UUID,
    *,
    status: InvoiceFilter,
    q: str | None,
    before: str | None,
    limit: int,
) -> InvoicePage:
    """The company's invoices, newest issued first; `next_before` is the last item's id."""
    term = q.strip() if q else ""
    rows = await db.q.fetch_all(
        "payments/invoices_list.sql",
        {
            "client_id": client_id,
            "statuses": list(FILTER_STATUSES[status]),
            "has_q": bool(term),
            "reference": normalise_reference(term) if term else None,
            "like": like_pattern(term) if term else None,
            "digits": phone_digits(term),
            "before": before,
            "limit": limit + 1,
        },
        conn=conn,
    )
    items = [invoice_out(row) for row in rows[:limit]]
    return InvoicePage(items=items, next_before=items[-1].id if len(rows) > limit else None)


async def invoice_detail(
    conn: Conn, client_id: UUID, invoice_id: str
) -> tuple[InvoiceDetail, UUID | None]:
    """One invoice with its payment and reminders, and who recorded the payment (a user id);
    404 `invoice_not_found` (others' too). The payment's `created_by_name` is left empty: the
    caller fills it with `with_recorder_name` once its transaction is over, so the identity pool
    is never waited on while the invoice or subscription is locked."""
    key = {"client_id": client_id, "invoice_id": invoice_id}
    row = await db.q.fetch_one("payments/invoice_detail.sql", key, conn=conn)
    if row is None:
        raise HTTPException(404, "invoice_not_found")
    payment = None
    creator: UUID | None = None
    if row["payment_id"] is not None:
        creator = row["payment_created_by"]
        payment = PaymentOut(
            id=row["payment_id"],
            amount=row["payment_amount"],
            currency=row["payment_currency"],
            received_at=row["payment_received_at"],
            method_kind=row["payment_method_kind"],
            source=row["payment_source"],
            matched_by=row["payment_matched_by"],
            created_by_name=None,
            note=row["payment_note"],
            created_at=row["payment_created_at"],
        )
    reminders = await db.q.fetch_all("payments/invoice_reminders.sql", key, conn=conn)
    detail = InvoiceDetail.model_validate(
        _invoice_fields(row)
        | {
            "payment": payment,
            "reminders": [ReminderOut.model_validate(r) for r in reminders],
        }
    )
    return detail, creator


async def with_recorder_name(
    detail: InvoiceDetail,
    creator: UUID | None,
    client_id: UUID,
    *,
    identity_pool: AsyncConnectionPool | None,
) -> InvoiceDetail:
    """The detail with the name of the member who recorded its payment, outside any tenant
    transaction; no name when there is none or the identity pool fails (`member_names`)."""
    if detail.payment is None or creator is None:
        return detail
    names = await member_names(identity_pool, client_id, {creator})
    payment = detail.payment.model_copy(update={"created_by_name": names.get(creator)})
    return detail.model_copy(update={"payment": payment})


async def attention(conn: Conn, client_id: UUID) -> PaymentAttentionPage:
    """Claimed invoices, invoices no reminder could reach, and recent failed reminders."""
    rows = await db.q.fetch_all(
        "payments/attention_list.sql",
        {"client_id": client_id, "limit": ATTENTION_LIMIT},
        conn=conn,
    )
    return PaymentAttentionPage(
        items=[
            PaymentAttentionItem(kind=row["kind"], invoice=invoice_out(row), since=row["since"])
            for row in rows
        ]
    )


async def member_names(
    identity_pool: AsyncConnectionPool | None, client_id: UUID, user_ids: set[UUID]
) -> dict[UUID, str]:
    """Display names of the company's members (and platform admins) among `user_ids`."""
    if not user_ids or identity_pool is None:
        return {}
    try:
        async with tx(identity_pool, str(client_id)) as conn:
            rows = await db.q.fetch_all(
                "payments/member_names.sql",
                {"client_id": client_id, "user_ids": sorted(user_ids, key=str)},
                conn=conn,
            )
    except Exception as failure:
        logger.warning(
            "payments_names_unavailable",
            client_id=str(client_id),
            outcome=type(failure).__name__,
        )
        return {}
    return {row["user_id"]: row["display_name"] for row in rows}
