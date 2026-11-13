"""Customer sign-up: file, staff approve and decline, auto-approve (customer onboarding)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, LiteralString
from uuid import UUID

import pytest
from csa.core.tenancy import tx
from csa.payments.reference import new_invoice_id, new_reference
from csa.subscriptions import service, signup
from csa.subscriptions.contracts import SubscriptionCreateIn
from csa.subscriptions.signup import SignupFiled
from fastapi import HTTPException

from tests.conftest import requires_docker
from tests.test_console_sessions import SessionWorld
from tests.test_console_subscriptions import seed_customer, seed_plan
from tests.test_subscriptions_executor import actor_from

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

TYPED = "+60199990000"
HOLD = 0.4  # seconds a racing transaction keeps its locks before it commits


async def file(
    w: SessionWorld,
    contact: str,
    plan: str = "basic",
    *,
    tenant: str | None = None,
    name: str = "Aisha Rahman",
    phone: str = TYPED,
    email: str | None = None,
) -> SignupFiled:
    tenant = tenant or w.tenants[0]
    async with tx(w.pool, tenant) as conn:
        return await signup.file_signup(
            conn, UUID(tenant), UUID(contact), plan, name, phone, email=email
        )


async def approve(w: SessionWorld, request_id: UUID, *, tenant: str | None = None) -> Any:
    tenant = tenant or w.tenants[0]
    async with tx(w.pool, tenant) as conn:
        return await signup.approve_signup(conn, UUID(tenant), request_id, UUID(w.admin_id))


async def decline(
    w: SessionWorld, request_id: UUID, reason: str | None = None, *, tenant: str | None = None
) -> Any:
    tenant = tenant or w.tenants[0]
    async with tx(w.pool, tenant) as conn:
        return await signup.decline_signup(conn, UUID(tenant), request_id, UUID(w.admin_id), reason)


async def refusal(coroutine: Any) -> tuple[int, str]:
    with pytest.raises(HTTPException) as caught:
        await coroutine
    return caught.value.status_code, str(caught.value.detail)


COUNTS: dict[str, LiteralString] = {
    "signup_requests": "SELECT count(*) AS n FROM signup_requests WHERE client_id=%s",
    "subscriptions": "SELECT count(*) AS n FROM subscriptions WHERE client_id=%s",
    "subscription_events": "SELECT count(*) AS n FROM subscription_events WHERE client_id=%s",
}


async def count(w: SessionWorld, table: str, tenant: str | None = None) -> int:
    rows = await w.q(COUNTS[table], tenant or w.tenants[0])
    return int(rows[0]["n"])


async def world(w: SessionWorld) -> tuple[str, str]:
    """One customer (with a different phone on file than the one typed) and the plan `basic`."""
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    contact = await seed_customer(w, w.tenants[0], phone="+60 12-345 6789")
    return contact, "basic"


async def test_file_creates_one_pending_request_and_a_repeat_returns_it(
    sessions: SessionWorld,
) -> None:
    w = sessions
    contact, _ = await world(w)
    first = await file(w, contact, phone="019-999 0000")
    assert first.outcome == "filed" and first.created is True and first.request is not None
    request = first.request
    assert request.status == "pending" and request.plan_code == "basic"
    assert request.plan_name == "Basic" and request.full_name == "Aisha Rahman"
    assert request.phone == TYPED  # stored as normalised
    assert request.subscription_id is None and request.decided_at is None
    assert request.decided_via is None and request.decline_reason is None
    assert request.contact.id == UUID(contact) and request.contact.phone == "+60 12-345 6789"
    assert request.source_channel is None

    again = await file(w, contact, name="Someone Else")
    assert again.outcome == "exists" and again.created is False
    assert again.request is not None and again.request.id == request.id
    assert again.request.full_name == "Aisha Rahman"  # the first stands
    assert await count(w, "signup_requests") == 1
    assert await count(w, "subscriptions") == 0


async def test_file_refuses_a_phone_that_is_not_a_number(sessions: SessionWorld) -> None:
    w = sessions
    contact, _ = await world(w)
    assert await refusal(file(w, contact, phone="call me")) == (422, "invalid_phone")
    assert await refusal(file(w, contact, name="  ")) == (422, "invalid_name")
    assert await refusal(file(w, contact, name="x" * 121)) == (422, "invalid_name")
    assert await count(w, "signup_requests") == 0


async def test_unknown_archived_and_closed_plans_write_nothing(sessions: SessionWorld) -> None:
    w = sessions
    contact, _ = await world(w)
    await seed_plan(w, w.tenants[0], "old", "9.90", is_active=False)
    await seed_plan(w, w.tenants[0], "shut", "19.90")
    await w.q(
        "UPDATE plans SET is_signup_open=false WHERE client_id=%s AND code='shut'", w.tenants[0]
    )

    for code, outcome in (("nope", "unknown_plan"), ("old", "unknown_plan"), ("shut", "closed")):
        filed = await file(w, contact, code)
        assert (filed.outcome, filed.created, filed.request) == (outcome, False, None)
    assert await count(w, "signup_requests") == 0
    assert await count(w, "subscriptions") == 0


async def test_a_live_subscription_on_the_plan_returns_has_plan(sessions: SessionWorld) -> None:
    w = sessions
    contact, _ = await world(w)
    for status in ("active", "paused", "pending"):
        sub = await w.q(
            "INSERT INTO subscriptions (client_id, contact_id, plan, status, price, started_at) "
            "VALUES (%s,%s,'basic',%s,29.90,now()) RETURNING id",
            w.tenants[0],
            contact,
            status,
        )
        filed = await file(w, contact)
        assert (filed.outcome, filed.created, filed.request) == ("has_plan", False, None)
        await w.q("UPDATE subscriptions SET status='cancelled' WHERE id=%s", sub[0]["id"])
    assert await count(w, "signup_requests") == 0
    # Once every subscription on the plan is cancelled, a sign-up is welcome again.
    assert (await file(w, contact)).outcome == "filed"


async def test_a_declined_request_allows_a_new_one(sessions: SessionWorld) -> None:
    w = sessions
    contact, _ = await world(w)
    first = (await file(w, contact)).request
    assert first is not None
    await decline(w, first.id, "not now")
    second = await file(w, contact)
    assert second.outcome == "filed" and second.created is True
    assert second.request is not None and second.request.id != first.id
    assert await count(w, "signup_requests") == 2


async def test_approve_creates_one_pending_subscription_at_the_price_now(
    sessions: SessionWorld,
) -> None:
    w = sessions
    contact, _ = await world(w)
    request = (await file(w, contact)).request
    assert request is not None
    await w.q(
        "UPDATE plans SET price=39.90 WHERE client_id=%s AND code='basic'", w.tenants[0]
    )  # repriced after the sign-up

    decided = await approve(w, request.id)
    assert decided.status == "approved" and decided.decided_via == "staff"
    assert decided.decided_at is not None and decided.subscription_id is not None

    subs = await w.q("SELECT * FROM subscriptions WHERE client_id=%s", w.tenants[0])
    assert len(subs) == 1
    sub = subs[0]
    assert sub["id"] == decided.subscription_id and str(sub["contact_id"]) == contact
    assert (sub["plan"], sub["status"], sub["currency"], sub["version"]) == (
        "basic",
        "pending",
        "MYR",
        1,
    )
    assert str(sub["price"]) == "39.90"
    assert sub["started_at"] is not None and sub["activated_at"] is None
    assert sub["renews_at"] is None and sub["renewal_anchor"] is None

    events = await w.q(
        "SELECT actor, action, from_status, to_status FROM subscription_events "
        "WHERE subscription_id=%s",
        sub["id"],
    )
    assert len(events) == 1
    assert events[0]["actor"] == w.admin_id and events[0]["action"] == "signup_approved"
    assert (events[0]["from_status"], events[0]["to_status"]) == (None, "pending")
    stored = await w.q("SELECT * FROM signup_requests WHERE id=%s", request.id)
    assert stored[0]["decided_by"] == UUID(w.admin_id) and stored[0]["status"] == "approved"
    assert stored[0]["subscription_id"] == sub["id"]
    audited = await w.q(
        "SELECT actor_id, target_id FROM console_audit WHERE client_id=%s AND action=%s",
        w.tenants[0],
        "subscriptions.signup.approved",
    )
    assert [(str(r["actor_id"]), r["target_id"]) for r in audited] == [
        (w.admin_id, str(request.id))
    ]
    # Ruling D: a typed number never reaches the contact, only the request keeps it.
    after = await w.q("SELECT phone FROM contacts WHERE id=%s", contact)
    assert after[0]["phone"] == "+60 12-345 6789"
    assert stored[0]["phone"] == TYPED


async def test_two_approvals_at_once_give_one_subscription_and_one_409(
    sessions: SessionWorld,
) -> None:
    w = sessions
    contact, _ = await world(w)
    request = (await file(w, contact)).request
    assert request is not None

    results = await asyncio.gather(
        approve(w, request.id), approve(w, request.id), return_exceptions=True
    )
    refused = [r for r in results if isinstance(r, HTTPException)]
    done = [r for r in results if not isinstance(r, BaseException)]
    assert len(done) == 1 and len(refused) == 1
    assert (refused[0].status_code, refused[0].detail) == (409, "signup_not_pending")
    assert await count(w, "subscriptions") == 1
    assert await count(w, "subscription_events") == 1


async def test_a_staff_approve_and_an_auto_approve_racing_give_one_subscription(
    sessions: SessionWorld,
) -> None:
    w = sessions
    contact, _ = await world(w)
    request = (await file(w, contact)).request
    assert request is not None

    async def auto() -> Any:
        async with tx(w.pool, w.tenants[0]) as conn:
            return await signup.auto_approve(conn, UUID(w.tenants[0]), request.id)

    results = await asyncio.gather(approve(w, request.id), auto(), return_exceptions=True)
    assert sum(isinstance(r, HTTPException) for r in results) == 1
    assert await count(w, "subscriptions") == 1


async def test_approve_after_staff_added_the_customer_by_hand_is_refused(
    sessions: SessionWorld,
) -> None:
    w = sessions
    contact, _ = await world(w)
    request = (await file(w, contact)).request
    assert request is not None
    # Staff add the customer to the plan by hand before they get to the request.
    await w.q(
        "INSERT INTO subscriptions (client_id, contact_id, plan, status, price, started_at) "
        "VALUES (%s,%s,'basic','active',29.90,now())",
        w.tenants[0],
        contact,
    )

    assert await refusal(approve(w, request.id)) == (409, "signup_has_plan")
    assert await count(w, "subscriptions") == 1
    assert await count(w, "subscription_events") == 0
    stored = await w.q("SELECT status FROM signup_requests WHERE id=%s", request.id)
    assert stored == [{"status": "pending"}]  # untouched: staff can decline it


async def test_two_filings_at_once_on_an_auto_plan_give_one_subscription(
    sessions: SessionWorld,
) -> None:
    w = sessions
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    await w.q(
        "UPDATE plans SET signup_mode='auto' WHERE client_id=%s AND code='basic'", w.tenants[0]
    )
    contacts = [
        await seed_customer(w, w.tenants[0], phone=f"+6012345{n:04d}", email=f"c{n}@example.test")
        for n in range(2)
    ]

    # Two customers, each filing twice at once (say, on Telegram and on WhatsApp). The test pool
    # has four connections, so this is as many transactions as can overlap.
    results = await asyncio.gather(*(file(w, c) for c in contacts for _ in range(2)))

    for index, contact in enumerate(contacts):
        pair = results[2 * index : 2 * index + 2]
        assert sorted(r.outcome for r in pair) == ["filed", "has_plan"], contact
    assert await count(w, "subscriptions") == len(contacts)
    assert await count(w, "signup_requests") == len(contacts)
    per_contact = await w.q(
        "SELECT contact_id, count(*) AS n FROM subscriptions WHERE client_id=%s "
        "GROUP BY contact_id HAVING count(*) > 1",
        w.tenants[0],
    )
    assert per_contact == []


async def test_two_filings_at_once_in_staff_mode_give_one_request(sessions: SessionWorld) -> None:
    w = sessions
    contact, _ = await world(w)

    first, second = await asyncio.gather(file(w, contact), file(w, contact))

    assert sorted((first.outcome, second.outcome)) == ["exists", "filed"]
    assert first.request is not None and second.request is not None
    assert first.request.id == second.request.id
    assert await count(w, "signup_requests") == 1
    assert await count(w, "subscriptions") == 0


async def test_a_filing_racing_a_staff_approval_never_leaves_a_second_live_row(
    sessions: SessionWorld,
) -> None:
    """Approve and file both take the per-(contact, plan) lock first, then the request row.

    Filing a request beside a staff approval of the same one, for several customers at once:
    every approval succeeds, no pair deadlocks, and no pending request is left beside a live
    subscription (the state a later approve would turn into a duplicate).
    """
    w = sessions
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    contacts = [
        await seed_customer(w, w.tenants[0], phone=f"+6012345{n:04d}", email=f"c{n}@example.test")
        for n in range(2)
    ]
    requests = []
    for contact in contacts:
        filed = (await file(w, contact)).request
        assert filed is not None
        requests.append(filed)

    results = await asyncio.wait_for(
        asyncio.gather(
            *(approve(w, r.id) for r in requests),
            *(file(w, c) for c in contacts),
            return_exceptions=True,
        ),
        timeout=30,
    )

    assert not [r for r in results if isinstance(r, BaseException)], results
    assert await count(w, "subscriptions") == len(contacts)
    beside_live = await w.q(
        "SELECT r.id FROM signup_requests r JOIN subscriptions s "
        "ON s.client_id=r.client_id AND s.contact_id=r.contact_id AND s.plan=r.plan_code "
        "WHERE r.client_id=%s AND r.status='pending' AND s.status IN ('active','paused','pending')",
        w.tenants[0],
    )
    assert beside_live == []


async def test_approve_after_the_plan_closed_or_was_archived_is_refused(
    sessions: SessionWorld,
) -> None:
    w = sessions
    contact, _ = await world(w)
    request = (await file(w, contact)).request
    assert request is not None

    closers: tuple[LiteralString, ...] = (
        "UPDATE plans SET is_signup_open=false WHERE client_id=%s AND code='basic'",
        "UPDATE plans SET is_active=false WHERE client_id=%s AND code='basic'",
    )
    for closer in closers:
        await w.q(closer, w.tenants[0])
        assert await refusal(approve(w, request.id)) == (409, "plan_not_open")
        stored = await w.q(
            "SELECT status, subscription_id FROM signup_requests WHERE id=%s", request.id
        )
        assert stored == [{"status": "pending", "subscription_id": None}]
        assert await count(w, "subscriptions") == 0
        await w.q(
            "UPDATE plans SET is_signup_open=true, is_active=true "
            "WHERE client_id=%s AND code='basic'",
            w.tenants[0],
        )
    assert (await approve(w, request.id)).status == "approved"


async def test_decline_stores_the_reason_and_a_second_decision_is_refused(
    sessions: SessionWorld,
) -> None:
    w = sessions
    contact, _ = await world(w)
    request = (await file(w, contact)).request
    assert request is not None

    declined = await decline(w, request.id, "  Out of area  ")
    assert declined.status == "declined" and declined.decline_reason == "Out of area"
    assert declined.decided_via == "staff" and declined.decided_at is not None
    stored = await w.q(
        "SELECT decided_by, subscription_id FROM signup_requests WHERE id=%s", request.id
    )
    assert stored[0]["decided_by"] == UUID(w.admin_id) and stored[0]["subscription_id"] is None

    assert await refusal(decline(w, request.id, "again")) == (409, "signup_not_pending")
    assert await refusal(approve(w, request.id)) == (409, "signup_not_pending")
    assert await count(w, "subscriptions") == 0
    assert await refusal(decline(w, request.id, "x" * 301)) == (422, "decline_reason_too_long")


async def test_a_decline_without_a_reason_stores_none(sessions: SessionWorld) -> None:
    w = sessions
    contact, _ = await world(w)
    request = (await file(w, contact)).request
    assert request is not None
    declined = await decline(w, request.id, "   ")
    assert declined.decline_reason is None


async def test_an_auto_plan_creates_the_subscription_at_filing(sessions: SessionWorld) -> None:
    w = sessions
    contact, _ = await world(w)
    await w.q(
        "UPDATE plans SET signup_mode='auto' WHERE client_id=%s AND code='basic'", w.tenants[0]
    )

    filed = await file(w, contact)
    assert filed.outcome == "filed" and filed.created is True and filed.request is not None
    assert filed.request.status == "approved" and filed.request.decided_via == "auto"
    assert filed.request.subscription_id is not None and filed.request.decided_at is not None

    stored = await w.q(
        "SELECT decided_by, decided_via, status FROM signup_requests WHERE id=%s", filed.request.id
    )
    assert stored == [{"decided_by": None, "decided_via": "auto", "status": "approved"}]
    subs = await w.q("SELECT id, status, price FROM subscriptions WHERE client_id=%s", w.tenants[0])
    assert len(subs) == 1 and subs[0]["status"] == "pending" and str(subs[0]["price"]) == "29.90"
    events = await w.q(
        "SELECT actor, action, to_status FROM subscription_events WHERE client_id=%s",
        w.tenants[0],
    )
    assert events == [
        {"actor": "system:signup_auto", "action": "signup_approved", "to_status": "pending"}
    ]

    # A repeat now meets the live pending subscription, not a second request.
    again = await file(w, contact)
    assert (again.outcome, again.request) == ("has_plan", None)
    assert await count(w, "subscriptions") == 1


async def test_a_staff_mode_plan_files_and_waits(sessions: SessionWorld) -> None:
    w = sessions
    contact, _ = await world(w)
    filed = await file(w, contact)
    assert filed.request is not None and filed.request.status == "pending"
    assert await count(w, "subscriptions") == 0


async def test_company_b_cannot_list_read_approve_or_decline_company_a_requests(
    sessions: SessionWorld,
) -> None:
    w = sessions
    contact, _ = await world(w)
    request = (await file(w, contact)).request
    assert request is not None
    other = w.tenants[1]
    await seed_plan(w, other, "basic", "5.00")

    async with tx(w.pool, other) as conn:
        page = await signup.list_signups(conn, UUID(other), status="pending", before=None, limit=50)
        assert page.items == [] and page.next_cursor is None
        page = await signup.list_signups(conn, UUID(other), status="decided", before=None, limit=50)
        assert page.items == []
    assert await refusal(approve(w, request.id, tenant=other)) == (404, "signup_not_found")
    assert await refusal(decline(w, request.id, "no", tenant=other)) == (404, "signup_not_found")
    stored = await w.q("SELECT status FROM signup_requests WHERE id=%s", request.id)
    assert stored == [{"status": "pending"}]
    assert await count(w, "subscriptions", other) == 0


async def test_list_filters_pending_and_decided_with_a_cursor(sessions: SessionWorld) -> None:
    w = sessions
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    ids: list[UUID] = []
    for n in range(3):
        contact = await seed_customer(w, w.tenants[0], name=f"Cust {n}", email=f"c{n}@example.test")
        request = (await file(w, contact)).request
        assert request is not None
        ids.append(request.id)
    await decline(w, ids[0], "no")
    await approve(w, ids[1])

    tenant = UUID(w.tenants[0])
    async with tx(w.pool, w.tenants[0]) as conn:
        pending = await signup.list_signups(conn, tenant, status="pending", before=None, limit=50)
        assert [item.id for item in pending.items] == [ids[2]]
        first = await signup.list_signups(conn, tenant, status="decided", before=None, limit=1)
        assert [item.id for item in first.items] == [ids[1]] and first.next_cursor == ids[1]
        second = await signup.list_signups(
            conn, tenant, status="decided", before=first.next_cursor, limit=1
        )
        assert [item.id for item in second.items] == [ids[0]] and second.next_cursor is None


async def test_phone_verified_and_source_channel_come_from_the_contact(
    sessions: SessionWorld,
) -> None:
    w = sessions
    contact, _ = await world(w)
    await w.q(
        "INSERT INTO contact_identities (client_id, channel, external_id, contact_id, created_at) "
        "VALUES (%s,'telegram','tg-1',%s, now() - interval '2 days'), "
        "(%s,'whatsapp','601199990000',%s, now() - interval '1 day')",
        w.tenants[0],
        contact,
        w.tenants[0],
        contact,
    )

    unverified = (await file(w, contact)).request
    assert unverified is not None
    assert unverified.phone_verified is False and unverified.source_channel == "whatsapp"
    assert unverified.contact.phone == "+60 12-345 6789"
    await decline(w, unverified.id)

    # The contact's phone is verified: a typed number that differs still reads unverified.
    await w.q("UPDATE contacts SET phone_verified_at=now() WHERE id=%s", contact)
    typed_other = (await file(w, contact)).request
    assert typed_other is not None and typed_other.phone_verified is False
    await decline(w, typed_other.id)
    same = (await file(w, contact, phone="012 345 6789")).request
    assert same is not None and same.phone == "+60123456789" and same.phone_verified is True


# --- the opening invoice (payments) --------------------------------------------------------------


async def payments_on(w: SessionWorld, *, enabled: bool = True, tenant: str | None = None) -> None:
    await w.q(
        "INSERT INTO payment_settings (client_id, enabled) VALUES (%s,%s) "
        "ON CONFLICT (client_id) DO UPDATE SET enabled = EXCLUDED.enabled",
        tenant or w.tenants[0],
        enabled,
    )


async def invoices_of(w: SessionWorld, sub: UUID | None) -> list[dict[str, Any]]:
    return await w.q("SELECT * FROM invoices WHERE subscription_id=%s ORDER BY issued_at", sub)


async def test_approve_issues_the_opening_invoice_in_the_same_transaction(
    sessions: SessionWorld,
) -> None:
    w = sessions
    contact, _ = await world(w)
    await payments_on(w)
    request = (await file(w, contact)).request
    assert request is not None and request.invoice_reference is None

    decided = await approve(w, request.id)

    [invoice] = await invoices_of(w, decided.subscription_id)
    assert invoice["status"] == "issued" and invoice["period_start"] is None
    assert str(invoice["amount"]) == "29.90" and invoice["currency"] == "MYR"
    assert invoice["contact_id"] == UUID(contact) and invoice["reference"] is not None
    assert decided.invoice_reference == invoice["reference"]
    async with tx(w.pool, w.tenants[0]) as conn:
        page = await signup.list_signups(
            conn, UUID(w.tenants[0]), status="decided", before=None, limit=50
        )
    assert [item.invoice_reference for item in page.items] == [invoice["reference"]]

    # Payments off: the approval issues nothing and says so.
    await payments_on(w, enabled=False)
    other = await seed_customer(w, w.tenants[0], name="Budi Contoh", email="budi@example.test")
    second = (await file(w, other)).request
    assert second is not None
    off = await approve(w, second.id)
    assert off.status == "approved" and off.invoice_reference is None
    assert await invoices_of(w, off.subscription_id) == []


async def test_the_sign_up_shows_its_opening_invoice_never_a_later_one(
    sessions: SessionWorld,
) -> None:
    w = sessions
    contact, _ = await world(w)
    await payments_on(w)
    request = (await file(w, contact)).request
    assert request is not None
    decided = await approve(w, request.id)
    [opening] = await invoices_of(w, decided.subscription_id)
    # A later period's invoice, issued after the opening one.
    later = new_reference()
    await w.q(
        "INSERT INTO invoices (id, client_id, contact_id, subscription_id, reference, status, "
        "amount, currency, issued_at, due_at, period_start, period_end) "
        "VALUES (%s, %s, %s, %s, %s, 'issued', 29.90, 'MYR', now() + interval '1 minute', "
        "now() + interval '30 days', now() + interval '30 days', now() + interval '60 days')",
        new_invoice_id(datetime.now(UTC)),
        w.tenants[0],
        contact,
        decided.subscription_id,
        later,
    )

    async def shown() -> list[str | None]:
        async with tx(w.pool, w.tenants[0]) as conn:
            one = await signup.get_signup(conn, UUID(w.tenants[0]), request.id)
            page = await signup.list_signups(
                conn, UUID(w.tenants[0]), status="decided", before=None, limit=50
            )
        return [one.invoice_reference, *[i.invoice_reference for i in page.items]]

    assert await shown() == [opening["reference"], opening["reference"]]
    # Paid, the opening invoice gets its period and is still the one shown.
    await w.q(
        "UPDATE invoices SET period_start = now(), period_end = now() + interval '30 days' "
        "WHERE id = %s",
        opening["id"],
    )
    assert await shown() == [opening["reference"], opening["reference"]]
    # Voided, it shows none: never the later period's reference in its place.
    await w.q(
        "UPDATE invoices SET status = 'void', period_start = NULL, period_end = NULL WHERE id = %s",
        opening["id"],
    )
    assert await shown() == [None, None]


async def test_approve_rolled_back_leaves_no_invoice(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = sessions
    contact, _ = await world(w)
    await payments_on(w)
    request = (await file(w, contact)).request
    assert request is not None
    issued: list[Any] = []
    original = signup.issue_opening_invoice

    async def issue_and_note(*args: Any, **kwargs: Any) -> Any:
        record = await original(*args, **kwargs)
        issued.append(record)
        return record

    async def fail(*_: Any, **__: Any) -> None:
        raise RuntimeError("forced failure after the invoice insert")

    monkeypatch.setattr(signup, "issue_opening_invoice", issue_and_note)
    monkeypatch.setattr(signup, "_audit", fail)

    with pytest.raises(RuntimeError, match="forced failure"):
        await approve(w, request.id)

    assert len(issued) == 1 and issued[0] is not None  # the invoice was inserted, then undone
    assert await w.q("SELECT id FROM invoices WHERE client_id=%s", w.tenants[0]) == []
    assert await count(w, "subscriptions") == 0
    stored = await w.q("SELECT status FROM signup_requests WHERE id=%s", request.id)
    assert stored == [{"status": "pending"}]


async def test_an_auto_approval_issues_the_opening_invoice_too(sessions: SessionWorld) -> None:
    w = sessions
    contact, _ = await world(w)
    await payments_on(w)
    await w.q(
        "UPDATE plans SET signup_mode='auto' WHERE client_id=%s AND code='basic'", w.tenants[0]
    )

    filed = await file(w, contact)

    assert filed.request is not None and filed.request.status == "approved"
    [invoice] = await invoices_of(w, filed.request.subscription_id)
    assert invoice["status"] == "issued" and str(invoice["amount"]) == "29.90"
    assert filed.request.invoice_reference == invoice["reference"]


async def test_a_manual_create_racing_an_approve_gives_one_live_subscription(
    sessions: SessionWorld,
) -> None:
    """Staff add the customer by hand while another approves their sign-up (two connections)."""
    w = sessions
    contact, _ = await world(w)
    request = (await file(w, contact)).request
    assert request is not None
    _, me = await w.member(role="tenant_staff")
    tenant = UUID(w.tenants[0])

    async def create() -> Any:
        async with tx(w.pool, w.tenants[0]) as conn:
            made = await service.create_subscription(
                conn,
                tenant,
                actor_from(me),
                SubscriptionCreateIn(contact_id=UUID(contact), plan_code="basic"),
            )
            await asyncio.sleep(HOLD)
            return made

    async def approving() -> Any:
        async with tx(w.pool, w.tenants[0]) as conn:
            decided = await signup.approve_signup(conn, tenant, request.id, UUID(w.admin_id))
            await asyncio.sleep(HOLD)
            return decided

    results = await asyncio.gather(create(), approving(), return_exceptions=True)

    refused = [r for r in results if isinstance(r, HTTPException)]
    assert len(refused) == 1, results
    assert refused[0].status_code == 409
    assert refused[0].detail in {"subscription_exists", "signup_has_plan"}
    live = await w.q(
        "SELECT count(*) AS n FROM subscriptions WHERE client_id=%s AND contact_id=%s "
        "AND plan='basic' AND status IN ('active','paused','pending')",
        w.tenants[0],
        contact,
    )
    assert live == [{"n": 1}]


# --- the email given at sign-up (WhatsApp, for payment reminders) ------------------------------


async def contact_emails(w: SessionWorld, contact: str) -> dict[str, Any]:
    [row] = await w.q("SELECT email, customer_given_email FROM contacts WHERE id=%s", contact)
    return row


async def test_a_given_email_is_stored_lower_case_and_fills_an_empty_given_email(
    sessions: SessionWorld,
) -> None:
    w = sessions
    contact, _ = await world(w)
    await w.q("UPDATE contacts SET email=NULL WHERE id=%s", contact)

    filed = await file(w, contact, email="  Aisha.Rahman@Example.COM ")

    assert filed.outcome == "filed" and filed.request is not None
    assert filed.request.email == "aisha.rahman@example.com"
    stored = await w.q("SELECT email FROM signup_requests WHERE id=%s", filed.request.id)
    assert stored == [{"email": "aisha.rahman@example.com"}]
    # A claim nobody checked: the given address only, never the contact's own email.
    assert await contact_emails(w, contact) == {
        "email": None,
        "customer_given_email": "aisha.rahman@example.com",
    }
    # The staff list shows it.
    async with tx(w.pool, w.tenants[0]) as conn:
        page = await signup.list_signups(
            conn, UUID(w.tenants[0]), status="pending", before=None, limit=50
        )
    assert [item.email for item in page.items] == ["aisha.rahman@example.com"]


async def test_a_given_email_never_overwrites_an_address_on_file(sessions: SessionWorld) -> None:
    w = sessions
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    given = await seed_customer(w, w.tenants[0], name="Given Contoh", email="unused@example.test")
    await w.q(
        "UPDATE contacts SET email=NULL, customer_given_email='first@example.test' WHERE id=%s",
        given,
    )
    own = await seed_customer(w, w.tenants[0], name="Own Contoh", email="own@example.test")

    for contact in (given, own):
        filed = await file(w, contact, email="second@example.test")
        assert filed.request is not None and filed.request.email == "second@example.test"
    assert await contact_emails(w, given) == {
        "email": None,
        "customer_given_email": "first@example.test",
    }
    assert await contact_emails(w, own) == {
        "email": "own@example.test",
        "customer_given_email": None,
    }


async def test_no_email_files_none_and_a_bad_one_files_nothing(sessions: SessionWorld) -> None:
    w = sessions
    contact, _ = await world(w)
    await w.q("UPDATE contacts SET email=NULL WHERE id=%s", contact)
    assert await refusal(file(w, contact, email="not-an-address")) == (422, "invalid_email")
    assert await count(w, "signup_requests") == 0
    filed = await file(w, contact)
    assert filed.request is not None and filed.request.email is None
    assert await contact_emails(w, contact) == {"email": None, "customer_given_email": None}
    # A repeat with an address returns the open request as it was, and fills nothing.
    again = await file(w, contact, email="late@example.test")
    assert again.outcome == "exists" and again.request is not None and again.request.email is None
    assert await contact_emails(w, contact) == {"email": None, "customer_given_email": None}
