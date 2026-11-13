"""Customer sign-up: file a request, staff approve or decline it, or the plan approves it at once.

An approval creates one `pending` subscription (price and currency are the plan's at that moment)
and, while the company's payments are on, its opening invoice in the same transaction; staff start
it later, a payment of that invoice starts it, or it expires. Nothing here charges, and the typed
sign-up phone stays on the request: it is never written to the contact, so a typed number can never
link its owner's contact to the person who typed it. An email given at sign-up (for payment
reminders) is stored on the request in lower case and fills only the contact's
`customer_given_email`, and only while the contact has no address at all: never its `email`, which
identity and linking trust. Every function takes a connection inside a tenant transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from csa.bookings.notify import clean_address
from csa.core import db
from csa.core.phone import normalise_my_phone
from csa.payments.invoices import issue_opening_invoice
from csa.subscriptions.contracts import CustomerRef, SignupOut, SignupPage
from csa.subscriptions.service import holds_plan, lock_contact_plan, record_event

Conn = AsyncConnection[Any]

AUTO_ACTOR = "system:signup_auto"
MAX_NAME = 120
MAX_REASON = 300

Outcome = Literal["filed", "exists", "has_plan", "closed", "unknown_plan"]


@dataclass(frozen=True)
class SignupFiled:
    """What filing did: the request (None when nothing was filed) and why."""

    request: SignupOut | None
    created: bool
    outcome: Outcome


def signup_out(row: dict[str, Any]) -> SignupOut:
    return SignupOut.model_validate(
        {
            **row,
            "contact": CustomerRef(
                id=row["contact_id"],
                full_name=row["contact_full_name"],
                phone=row["contact_phone"],
                email=row["contact_email"],
            ),
        },
        extra="ignore",
    )


async def get_signup(conn: Conn, client_id: UUID, request_id: UUID) -> SignupOut:
    """One request; another company's id is invisible under RLS."""
    row = await db.q.fetch_one(
        "subscriptions/signup_get.sql",
        {"client_id": client_id, "request_id": request_id},
        conn=conn,
    )
    if row is None:
        raise HTTPException(404, "signup_not_found")
    return signup_out(row)


async def list_signups(
    conn: Conn,
    client_id: UUID,
    *,
    status: Literal["pending", "decided"],
    before: UUID | None,
    limit: int,
) -> SignupPage:
    rows = await db.q.fetch_all(
        "subscriptions/signups_list.sql",
        {"client_id": client_id, "status": status, "before": before, "limit": limit + 1},
        conn=conn,
    )
    items = [signup_out(row) for row in rows[:limit]]
    return SignupPage(items=items, next_cursor=items[-1].id if len(rows) > limit else None)


async def _audit(
    conn: Conn, actor_user_id: UUID | None, client_id: UUID, action: str, request_id: UUID
) -> None:
    """The audit row; an automatic decision has no console user."""
    await db.q.execute(
        "console/audit.sql",
        (actor_user_id, client_id, action, str(request_id), Jsonb({})),
        conn=conn,
    )


async def file_signup(
    conn: Conn,
    client_id: UUID,
    contact_id: UUID,
    plan_code: str,
    full_name: str,
    phone: str,
    *,
    email: str | None = None,
) -> SignupFiled:
    """File one pending request for the contact, or say why none was filed.

    A repeat returns the open request (`exists`). An archived or unknown plan is `unknown_plan`,
    one closed to sign-ups is `closed`, and a live subscription on the plan is `has_plan`; none of
    them writes anything. A plan that approves by itself is approved in this same transaction.
    `email` (checked as a booking's address is, 422 `invalid_email` otherwise) is stored on a new
    request and fills the contact's empty `customer_given_email`; a repeat changes neither.
    """
    typed = normalise_my_phone(phone)  # cheap, and a no-op on a number already normalised
    if typed is None:
        raise HTTPException(422, "invalid_phone")
    given: str | None = None
    if email is not None:
        address = clean_address(email)
        if address is None:
            raise HTTPException(422, "invalid_email")
        given = address.lower()
    name = full_name.strip()
    if not 1 <= len(name) <= MAX_NAME:
        raise HTTPException(422, "invalid_name")
    plan = await db.q.fetch_one(
        "subscriptions/plan_get.sql", {"client_id": client_id, "code": plan_code}, conn=conn
    )
    if plan is None or not plan["is_active"]:
        return SignupFiled(None, False, "unknown_plan")
    if not plan["is_signup_open"]:
        return SignupFiled(None, False, "closed")
    await lock_contact_plan(conn, client_id, contact_id, plan_code)
    if await holds_plan(conn, client_id, contact_id, plan_code):
        return SignupFiled(None, False, "has_plan")
    inserted = await db.q.fetch_one(
        "subscriptions/signup_insert.sql",
        {
            "client_id": client_id,
            "contact_id": contact_id,
            "plan_code": plan_code,
            "full_name": name,
            "phone": typed,
            "email": given,
        },
        conn=conn,
    )
    if inserted is None:
        existing = await db.q.fetch_one(
            "subscriptions/signup_open.sql",
            {"client_id": client_id, "contact_id": contact_id, "plan_code": plan_code},
            conn=conn,
        )
        if existing is None:  # decided between the conflict and this read
            raise HTTPException(409, "signup_changed")
        return SignupFiled(await get_signup(conn, client_id, existing["id"]), False, "exists")
    request_id: UUID = inserted["id"]
    if given is not None:
        await db.q.execute(
            "subscriptions/contact_given_email_fill.sql",
            {"client_id": client_id, "contact_id": contact_id, "email": given},
            conn=conn,
        )
    if plan["signup_mode"] == "auto":
        return SignupFiled(await auto_approve(conn, client_id, request_id), True, "filed")
    return SignupFiled(await get_signup(conn, client_id, request_id), True, "filed")


async def _approve(
    conn: Conn,
    client_id: UUID,
    request_id: UUID,
    *,
    via: Literal["staff", "auto"],
    actor_user_id: UUID | None,
) -> SignupOut:
    """Approve under a row lock on the request.

    A staff approval racing an auto-approval (or a second staff approval) gives exactly one
    subscription and one 409. The contact and plan of a request never change, so they are read
    first (no lock), the per-(contact, plan) advisory lock is taken, and only then the request row
    is locked: the same order `file_signup` and a subscription added by hand use. A contact who
    already holds the plan by then (added by hand, or approved through another request) is 409
    `signup_has_plan`. While the company's payments are on, the subscription's opening invoice is
    issued in this same transaction (a failure later rolls both back); the returned request then
    carries its `invoice_reference`.
    """
    seen = await get_signup(conn, client_id, request_id)
    await lock_contact_plan(conn, client_id, seen.contact.id, seen.plan_code)
    locked = await db.q.fetch_one(
        "subscriptions/signup_lock.sql",
        {"client_id": client_id, "request_id": request_id},
        conn=conn,
    )
    if locked is None:
        raise HTTPException(404, "signup_not_found")
    if locked["status"] != "pending":
        raise HTTPException(409, "signup_not_pending")
    if await holds_plan(conn, client_id, locked["contact_id"], locked["plan_code"]):
        raise HTTPException(409, "signup_has_plan")
    # The plan as it is now, shared-locked so an archive or a close cannot land after this read.
    plan = await db.q.fetch_one(
        "subscriptions/plan_get_share.sql",
        {"client_id": client_id, "code": locked["plan_code"]},
        conn=conn,
    )
    if plan is None or not plan["is_active"] or not plan["is_signup_open"]:
        raise HTTPException(409, "plan_not_open")
    created = await db.q.fetch_one(
        "subscriptions/pending_subscription_insert.sql",
        {
            "client_id": client_id,
            "contact_id": locked["contact_id"],
            "plan_code": locked["plan_code"],
        },
        conn=conn,
    )
    if created is None:
        raise HTTPException(409, "plan_not_open")
    subscription_id: UUID = created["id"]
    decided = await db.q.fetch_one(
        "subscriptions/signup_decide.sql",
        {
            "client_id": client_id,
            "request_id": request_id,
            "status": "approved",
            "decided_via": via,
            "decided_by": actor_user_id,
            "decline_reason": None,
            "subscription_id": subscription_id,
        },
        conn=conn,
    )
    if decided is None:
        raise RuntimeError(f"signup request {request_id} was decided while locked")
    await record_event(
        conn,
        client_id,
        subscription_id,
        actor=str(actor_user_id) if actor_user_id is not None else AUTO_ACTOR,
        action="signup_approved",
        from_status=None,
        to_status="pending",
        payload={"plan": locked["plan_code"], "request_id": str(request_id)},
    )
    await issue_opening_invoice(conn, client_id, subscription_id, now=datetime.now(UTC))
    await _audit(conn, actor_user_id, client_id, "subscriptions.signup.approved", request_id)
    return await get_signup(conn, client_id, request_id)


async def approve_signup(
    conn: Conn, client_id: UUID, request_id: UUID, actor_user_id: UUID
) -> SignupOut:
    """Staff approve a request.

    409 `signup_not_pending` unless it is pending, 409 `plan_not_open` unless the plan is active
    and open, 409 `signup_has_plan` when the contact already holds a live subscription on the plan;
    otherwise one pending subscription at the plan's price now (and its opening invoice while
    payments are on).
    """
    return await _approve(conn, client_id, request_id, via="staff", actor_user_id=actor_user_id)


async def auto_approve(conn: Conn, client_id: UUID, request_id: UUID) -> SignupOut:
    """The plan approves by itself (event actor `system:signup_auto`, no deciding user)."""
    return await _approve(conn, client_id, request_id, via="auto", actor_user_id=None)


async def decline_signup(
    conn: Conn, client_id: UUID, request_id: UUID, actor_user_id: UUID, reason: str | None
) -> SignupOut:
    """Staff decline with an optional reason (staff only: it is never sent to the customer)."""
    note = (reason or "").strip() or None
    if note is not None and len(note) > MAX_REASON:
        raise HTTPException(422, "decline_reason_too_long")
    locked = await db.q.fetch_one(
        "subscriptions/signup_lock.sql",
        {"client_id": client_id, "request_id": request_id},
        conn=conn,
    )
    if locked is None:
        raise HTTPException(404, "signup_not_found")
    if locked["status"] != "pending":
        raise HTTPException(409, "signup_not_pending")
    decided = await db.q.fetch_one(
        "subscriptions/signup_decide.sql",
        {
            "client_id": client_id,
            "request_id": request_id,
            "status": "declined",
            "decided_via": "staff",
            "decided_by": actor_user_id,
            "decline_reason": note,
            "subscription_id": None,
        },
        conn=conn,
    )
    if decided is None:
        raise RuntimeError(f"signup request {request_id} was decided while locked")
    await _audit(conn, actor_user_id, client_id, "subscriptions.signup.declined", request_id)
    return await get_signup(conn, client_id, request_id)
