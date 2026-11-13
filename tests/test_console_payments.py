"""Payments tab staff routes: invoices, mark paid, void, attention, settings and methods."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from csa.payments.api import build_payments_router
from csa.payments.reference import new_invoice_id, new_reference
from psycopg.types.json import Jsonb

from tests.conftest import requires_docker
from tests.test_console_services import activate
from tests.test_console_sessions import SessionWorld

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

BASE = "/console/v1/payments"
TRANSFER = {
    "bank_name": "Synthetic Bank",
    "account_name": "Kedai Contoh Sdn Bhd",
    "account_number": "512000000001",
}


@pytest.fixture(autouse=True)
async def packages(sessions: SessionWorld) -> None:
    sessions.app.include_router(build_payments_router())
    for tenant in sessions.tenants:
        await activate(sessions, tenant, "subscriptions")


def when(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def seed_contact(
    w: SessionWorld, tenant: str, *, name: str = "Aisha Rahman", phone: str = "+60 12-345 6789"
) -> str:
    rows = await w.q(
        "INSERT INTO contacts (client_id, full_name, phone) VALUES (%s,%s,%s) RETURNING id",
        tenant,
        name,
        phone,
    )
    return str(rows[0]["id"])


async def seed_invoice(
    w: SessionWorld,
    tenant: str,
    *,
    status: str = "issued",
    contact: str | None = None,
    subscription: str | None = None,
    amount: str = "59.90",
    issued_at: datetime | None = None,
    claimed_at: datetime | None = None,
) -> tuple[str, str]:
    """An invoice written as the invoice job would (no payment row); returns (id, reference)."""
    issued = issued_at or datetime.now(UTC)
    invoice_id, reference = new_invoice_id(issued), new_reference()
    await w.q(
        "INSERT INTO invoices (id, client_id, contact_id, subscription_id, reference, status, "
        "amount, currency, issued_at, due_at, claimed_at, claim_note, line_items) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,'MYR',%s,%s,%s,%s,%s)",
        invoice_id,
        tenant,
        contact,
        subscription,
        reference,
        status,
        amount,
        issued,
        issued + timedelta(days=7),
        claimed_at,
        "Sent from Synthetic Bank" if claimed_at else None,
        Jsonb([{"description": "Gold", "quantity": 1, "unit_price": amount}]),
    )
    return invoice_id, reference


async def seed_reminder(
    w: SessionWorld,
    tenant: str,
    invoice_id: str,
    step: int,
    status: str,
    *,
    ago: timedelta,
    channel: str = "none",
    address: str | None = None,
) -> None:
    await w.q(
        "INSERT INTO invoice_reminders (client_id, invoice_id, step, channel, address, status, "
        "created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        tenant,
        invoice_id,
        step,
        channel,
        address,
        status,
        datetime.now(UTC) - ago,
    )


async def audit_actions(w: SessionWorld, tenant: str) -> list[str]:
    rows = await w.q(
        "SELECT action FROM console_audit WHERE client_id=%s AND action LIKE 'payments.%%' "
        "ORDER BY created_at, action",
        tenant,
    )
    return [row["action"] for row in rows]


async def test_settings_and_methods_admin_writes_staff_reads(sessions: SessionWorld) -> None:
    w = sessions
    admin, _ = await w.member(role="tenant_admin")
    staff, _ = await w.member(role="tenant_staff")

    shown = await staff.get(f"{BASE}/settings")
    assert shown.status_code == 200, shown.text
    assert shown.json() == {
        "enabled": False,
        "lead_days": 7,
        "reminder_days": [-7, 0, 3, 7],
        "suspend_after_days": None,
        "version": 0,
    }
    switch_on = {
        "enabled": True,
        "lead_days": 5,
        "reminder_days": [3, -3, 0],
        "suspend_after_days": 14,
        "version": 0,
    }
    bare = await w.mutate(admin, f"{BASE}/settings", switch_on, method="PUT")
    assert bare.status_code == 409 and bare.json() == {"detail": "no_active_method"}

    cash = await w.mutate(
        admin, f"{BASE}/methods", {"kind": "cash", "label": "Counter", "details": {}}
    )
    assert cash.status_code == 201, cash.text
    still_bare = await w.mutate(admin, f"{BASE}/settings", switch_on, method="PUT")
    assert still_bare.status_code == 409 and still_bare.json()["detail"] == "no_active_method"

    bank = await w.mutate(
        admin, f"{BASE}/methods", {"kind": "transfer", "label": " Bank ", "details": TRANSFER}
    )
    assert bank.status_code == 201, bank.text
    assert bank.json()["label"] == "Bank" and bank.json()["details"] == TRANSFER
    assert bank.json()["version"] == 1 and bank.json()["is_active"] is True

    saved = await w.mutate(admin, f"{BASE}/settings", switch_on, method="PUT")
    assert saved.status_code == 200, saved.text
    assert saved.json()["enabled"] is True and saved.json()["reminder_days"] == [-3, 0, 3]
    assert saved.json()["version"] == 1
    stale = await w.mutate(admin, f"{BASE}/settings", switch_on, method="PUT")
    assert stale.status_code == 409 and stale.json() == {"detail": "version_conflict"}

    changed = await w.mutate(
        admin,
        f"{BASE}/methods/{bank.json()['id']}",
        {"label": "Maybank", "version": 1},
        method="PATCH",
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["label"] == "Maybank" and changed.json()["version"] == 2
    last = await w.mutate(
        admin,
        f"{BASE}/methods/{bank.json()['id']}",
        {"is_active": False, "version": 2},
        method="PATCH",
    )
    assert last.status_code == 409 and last.json() == {"detail": "last_active_method"}
    missing = await w.mutate(
        admin, f"{BASE}/methods/{uuid.uuid4()}", {"label": "x", "version": 1}, method="PATCH"
    )
    assert missing.status_code == 404 and missing.json() == {"detail": "method_not_found"}

    listed = await staff.get(f"{BASE}/methods")
    assert listed.status_code == 200
    assert {m["label"] for m in listed.json()} == {"Counter", "Maybank"}

    # Staff read, but settings and methods are an administrator's to change.
    for refused in (
        await w.mutate(staff, f"{BASE}/settings", {**switch_on, "version": 1}, method="PUT"),
        await w.mutate(staff, f"{BASE}/methods", {"kind": "cash", "label": "Till", "details": {}}),
        await w.mutate(
            staff,
            f"{BASE}/methods/{bank.json()['id']}",
            {"label": "x", "version": 2},
            method="PATCH",
        ),
    ):
        assert refused.status_code == 403, refused.text

    # A platform admin names the company.
    platform = await w.admin.get(f"{BASE}/settings?client_id={w.tenants[0]}")
    assert platform.status_code == 200 and platform.json()["version"] == 1

    assert await audit_actions(w, w.tenants[0]) == [
        "payments.method.created",
        "payments.method.created",
        "payments.settings.updated",
        "payments.method.updated",
    ]


async def test_bad_method_details_are_the_same_422_on_create_and_change(
    sessions: SessionWorld,
) -> None:
    w = sessions
    admin, _ = await w.member(role="tenant_admin")
    bad = {**TRANSFER, "account_number": "12-34"}

    created = await w.mutate(
        admin, f"{BASE}/methods", {"kind": "transfer", "label": "Bank", "details": bad}
    )
    assert created.status_code == 422, created.text
    assert created.json() == {"detail": "invalid_details"}
    wrong_kind = await w.mutate(
        admin, f"{BASE}/methods", {"kind": "qr", "label": "QR", "details": TRANSFER}
    )
    assert wrong_kind.status_code == 422 and wrong_kind.json() == {"detail": "invalid_details"}

    good = await w.mutate(
        admin, f"{BASE}/methods", {"kind": "transfer", "label": "Bank", "details": TRANSFER}
    )
    assert good.status_code == 201, good.text
    changed = await w.mutate(
        admin,
        f"{BASE}/methods/{good.json()['id']}",
        {"details": bad, "version": 1},
        method="PATCH",
    )
    assert changed.status_code == 422 and changed.json() == created.json()

    # Other validation errors keep the usual field list.
    unknown = await w.mutate(
        admin, f"{BASE}/methods", {"kind": "cheque", "label": "x", "details": {}}
    )
    assert unknown.status_code == 422 and isinstance(unknown.json()["detail"], list)
    assert await audit_actions(w, w.tenants[0]) == ["payments.method.created"]


async def test_read_only_and_the_package_gate_refuse_everything(sessions: SessionWorld) -> None:
    w = sessions
    reader, _ = await w.member(role="read_only")
    invoice_id, _ = await seed_invoice(w, w.tenants[0])
    method = str(uuid.uuid4())
    mark = {"request_key": str(uuid.uuid4()), "method_kind": "transfer"}

    gets = ("/settings", "/methods", "/invoices", f"/invoices/{invoice_id}", "/attention")
    for path in gets:
        response = await reader.get(f"{BASE}{path}")
        assert response.status_code == 403, (path, response.text)
    for path, body, verb in (
        (
            "/settings",
            {
                "enabled": False,
                "lead_days": 7,
                "reminder_days": [],
                "suspend_after_days": None,
                "version": 0,
            },
            "PUT",
        ),
        ("/methods", {"kind": "cash", "label": "Counter", "details": {}}, "POST"),
        (f"/methods/{method}", {"label": "x", "version": 1}, "PATCH"),
        (f"/invoices/{invoice_id}/mark-paid", mark, "POST"),
        (f"/invoices/{invoice_id}/void", {"reason": "Typo"}, "POST"),
    ):
        response = await w.mutate(reader, f"{BASE}{path}", body, method=verb)
        assert response.status_code == 403, (path, response.text)
    assert (await w.q("SELECT status FROM invoices WHERE id=%s", invoice_id)) == [
        {"status": "issued"}
    ]

    created = await w.mutate(
        w.admin,
        "/console/v1/admin/clients",
        {"slug": f"synthetic-{uuid.uuid4()}", "name": "No Package Tenant"},
    )
    assert created.status_code == 201, created.text
    bare, _ = await w.member(role="tenant_admin", tenant=created.json()["id"])
    for response in (
        await bare.get(f"{BASE}/invoices"),
        await bare.get(f"{BASE}/settings"),
        await bare.get(f"{BASE}/attention"),
        await w.mutate(bare, f"{BASE}/invoices/{invoice_id}/mark-paid", mark),
    ):
        assert response.status_code == 403 and response.json()["detail"] == "package_inactive"


async def test_another_company_never_reaches_an_invoice(sessions: SessionWorld) -> None:
    w = sessions
    other, _ = await w.member(role="tenant_admin", tenant=w.tenants[1])
    mine, _ = await w.member(role="tenant_admin")
    invoice_id, reference = await seed_invoice(w, w.tenants[0])

    assert (await other.get(f"{BASE}/invoices/{invoice_id}")).json() == {
        "detail": "invoice_not_found"
    }
    paid = await w.mutate(
        other,
        f"{BASE}/invoices/{invoice_id}/mark-paid",
        {"request_key": str(uuid.uuid4()), "method_kind": "cash"},
    )
    assert paid.status_code == 404 and paid.json() == {"detail": "invoice_not_found"}
    voided = await w.mutate(other, f"{BASE}/invoices/{invoice_id}/void", {"reason": "x"})
    assert voided.status_code == 404 and voided.json() == {"detail": "invoice_not_found"}
    listed = await other.get(f"{BASE}/invoices?status=all&q={reference}")
    assert listed.status_code == 200 and listed.json() == {"items": [], "next_before": None}
    assert (await other.get(f"{BASE}/attention")).json() == {"items": []}

    crossing = await other.get(f"{BASE}/invoices?client_id={w.tenants[0]}")
    assert crossing.status_code == 403
    assert (await w.q("SELECT count(*) AS n FROM payments")) == [{"n": 0}]
    assert (await mine.get(f"{BASE}/invoices/{invoice_id}")).status_code == 200


async def test_list_filters_search_and_pages_newest_first(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    aisha = await seed_contact(w, w.tenants[0])
    budi = await seed_contact(w, w.tenants[0], name="Budi Santoso", phone="+60 19 888 0000")
    await w.q(
        "INSERT INTO contact_identities (client_id, channel, external_id, contact_id) VALUES "
        "(%s,'whatsapp','60123456789',%s), (%s,'telegram','tg-synthetic-1',%s)",
        w.tenants[0],
        aisha,
        w.tenants[0],
        aisha,
    )
    start = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    made: dict[str, tuple[str, str]] = {}
    for offset, (name, status, contact) in enumerate(
        (
            ("issued", "issued", aisha),
            ("overdue", "overdue", budi),
            ("claimed", "proof_submitted", aisha),
            ("cash", "cash_pending", None),
            ("paid", "paid", budi),
            ("void", "void", aisha),
            ("draft", "draft", aisha),
        )
    ):
        made[name] = await seed_invoice(
            w,
            w.tenants[0],
            status=status,
            contact=contact,
            issued_at=start + timedelta(days=offset),
            claimed_at=start if status == "proof_submitted" else None,
        )

    async def ids(query: str) -> list[str]:
        response = await staff.get(f"{BASE}/invoices?{query}")
        assert response.status_code == 200, response.text
        return [item["id"] for item in response.json()["items"]]

    def named(*names: str) -> list[str]:
        return [made[name][0] for name in names]

    assert await ids("") == named("cash", "claimed", "overdue", "issued")  # open by default
    assert await ids("status=open") == named("cash", "claimed", "overdue", "issued")
    assert await ids("status=overdue") == named("overdue")
    assert await ids("status=proof_submitted") == named("claimed")
    assert await ids("status=paid") == named("paid")
    assert await ids("status=void") == named("void")
    assert await ids("status=all") == named(
        "draft", "void", "paid", "cash", "claimed", "overdue", "issued"
    )
    assert (await staff.get(f"{BASE}/invoices?status=draft")).status_code == 422

    reference = made["overdue"][1]
    typed = reference.replace("-", " ").lower()  # 'abcd ef3' finds 'ABCD-EF3'
    assert await ids(f"status=all&q={typed}") == named("overdue")
    assert await ids(f"status=all&q={reference}") == named("overdue")
    assert await ids("status=all&q=SANTOSO") == named("paid", "overdue")
    assert await ids("status=all&q=0123") == named("draft", "void", "claimed", "issued")
    assert await ids("status=open&q=%2B60%2019-888") == named("overdue")
    assert await ids("status=all&q=nobody") == []

    first = (await staff.get(f"{BASE}/invoices?status=all&limit=3")).json()
    assert [item["id"] for item in first["items"]] == named("draft", "void", "paid")
    assert first["next_before"] is not None
    second = (
        await staff.get(f"{BASE}/invoices?status=all&limit=3&before={first['next_before']}")
    ).json()
    assert [item["id"] for item in second["items"]] == named("cash", "claimed", "overdue")
    third = (
        await staff.get(f"{BASE}/invoices?status=all&limit=3&before={second['next_before']}")
    ).json()
    assert [item["id"] for item in third["items"]] == named("issued")
    assert third["next_before"] is None
    assert (await staff.get(f"{BASE}/invoices?limit=101")).status_code == 422

    item = next(i for i in first["items"] if i["id"] == made["void"][0])
    assert item["amount"] == "59.90" and item["currency"] == "MYR"
    assert item["contact"] == {
        "id": aisha,
        "full_name": "Aisha Rahman",
        "channels": ["telegram", "whatsapp"],
    }
    assert item["plan_name"] == "Gold" and item["reference"] == made["void"][1]
    assert set(item) == {
        "id",
        "reference",
        "status",
        "amount",
        "currency",
        "issued_at",
        "due_at",
        "paid_at",
        "period_start",
        "period_end",
        "subscription_id",
        "plan_name",
        "contact",
        "claim_note",
        "claimed_at",
        "method_kind",
        "void_reason",
        "voided_at",
        "reminded_at",
    }
    cash = next(i for i in second["items"] if i["id"] == made["cash"][0])
    assert cash["contact"] is None

    long_id = await staff.get(f"{BASE}/invoices/{'i' * 65}")
    assert long_id.status_code == 422
    unknown = await staff.get(f"{BASE}/invoices/inv_{'0' * 25}")
    assert unknown.status_code == 404 and unknown.json() == {"detail": "invoice_not_found"}


async def test_mark_paid_replays_refuses_twice_and_records_a_cancelled_one(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, me = await w.member(role="tenant_staff")
    contact = await seed_contact(w, w.tenants[0])
    invoice_id, _ = await seed_invoice(w, w.tenants[0], contact=contact)
    other_id, _ = await seed_invoice(w, w.tenants[0], contact=contact)
    void_id, _ = await seed_invoice(w, w.tenants[0], status="void")
    path = f"{BASE}/invoices/{invoice_id}/mark-paid"
    key = str(uuid.uuid4())

    paid = await w.mutate(
        staff, path, {"request_key": key, "method_kind": "transfer", "note": " Maybank slip "}
    )
    assert paid.status_code == 200, paid.text
    detail = paid.json()
    assert detail["id"] == invoice_id and detail["status"] == "paid"
    assert detail["method_kind"] == "transfer" and detail["reminders"] == []
    payment = detail["payment"]
    assert payment["amount"] == "59.90" and payment["currency"] == "MYR"
    assert payment["source"] == "staff" and payment["matched_by"] == "staff"
    assert payment["note"] == "Maybank slip" and payment["method_kind"] == "transfer"
    assert payment["created_by_name"] == me["user"]["display_name"] == "Synthetic Member"
    assert abs((datetime.now(UTC) - when(payment["received_at"])).total_seconds()) < 120

    replay = await w.mutate(staff, path, {"request_key": key, "method_kind": "transfer"})
    assert replay.status_code == 200 and replay.json() == detail
    assert (await w.q("SELECT count(*) AS n FROM payments WHERE invoice_id=%s", invoice_id)) == [
        {"n": 1}
    ]
    twice = await w.mutate(staff, path, {"request_key": str(uuid.uuid4()), "method_kind": "cash"})
    assert twice.status_code == 409 and twice.json() == {"detail": "invoice_already_paid"}
    reused = await w.mutate(
        staff,
        f"{BASE}/invoices/{other_id}/mark-paid",
        {"request_key": key, "method_kind": "cash"},
    )
    assert reused.status_code == 409 and reused.json() == {"detail": "request_key_reused"}
    future = await w.mutate(
        staff,
        f"{BASE}/invoices/{other_id}/mark-paid",
        {
            "request_key": str(uuid.uuid4()),
            "method_kind": "cash",
            "received_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
        },
    )
    assert future.status_code == 422 and future.json() == {"detail": "received_in_future"}
    naive = await w.mutate(
        staff,
        f"{BASE}/invoices/{other_id}/mark-paid",
        {
            "request_key": str(uuid.uuid4()),
            "method_kind": "cash",
            "received_at": "2026-11-01T10:00:00",
        },
    )
    assert naive.status_code == 422
    not_payable = await w.mutate(
        staff,
        f"{BASE}/invoices/{void_id}/mark-paid",
        {"request_key": str(uuid.uuid4()), "method_kind": "cash"},
    )
    assert not_payable.status_code == 409 and not_payable.json() == {
        "detail": "invoice_not_payable"
    }
    missing = await w.mutate(
        staff,
        f"{BASE}/invoices/inv_{'0' * 25}/mark-paid",
        {"request_key": str(uuid.uuid4()), "method_kind": "cash"},
    )
    assert missing.status_code == 404 and missing.json() == {"detail": "invoice_not_found"}

    # A cancelled subscription's open invoice is paid and recorded; the subscription stays as it is.
    await w.q(
        "INSERT INTO plans (client_id, code, name, price, billing_interval, is_active) "
        "VALUES (%s,'gold','Gold',59.90,'month',true)",
        w.tenants[0],
    )
    rows = await w.q(
        "INSERT INTO subscriptions (client_id, contact_id, plan, status, price, started_at, "
        "cancelled_at) VALUES (%s,%s,'gold','cancelled',59.90,now() - interval '40 days', now()) "
        "RETURNING id, version",
        w.tenants[0],
        contact,
    )
    subscription = str(rows[0]["id"])
    cancelled_id, _ = await seed_invoice(
        w, w.tenants[0], contact=contact, subscription=subscription, status="overdue"
    )
    received = "2026-11-10T09:30:00+00:00"
    recorded = await w.mutate(
        staff,
        f"{BASE}/invoices/{cancelled_id}/mark-paid",
        {"request_key": str(uuid.uuid4()), "method_kind": "qr", "received_at": received},
    )
    assert recorded.status_code == 200, recorded.text
    assert (
        recorded.json()["status"] == "paid" and recorded.json()["subscription_id"] == subscription
    )
    assert when(recorded.json()["payment"]["received_at"]) == when(received)
    assert (await w.q("SELECT status, version FROM subscriptions WHERE id=%s", subscription)) == [
        {"status": "cancelled", "version": rows[0]["version"]}
    ]

    assert await audit_actions(w, w.tenants[0]) == [
        "payments.invoice.paid",
        "payments.invoice.paid",
    ]


async def test_void_refuses_paid_and_void_and_keeps_the_reason(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    invoice_id, _ = await seed_invoice(w, w.tenants[0])
    paid_id, _ = await seed_invoice(w, w.tenants[0])
    path = f"{BASE}/invoices/{invoice_id}/void"

    empty = await w.mutate(staff, path, {"reason": "   "})
    assert empty.status_code == 422
    voided = await w.mutate(staff, path, {"reason": " Issued twice by mistake "})
    assert voided.status_code == 200, voided.text
    body = voided.json()
    assert body["status"] == "void" and body["void_reason"] == "Issued twice by mistake"
    assert abs((datetime.now(UTC) - when(body["voided_at"])).total_seconds()) < 120
    assert body["payment"] is None and body["reminders"] == []
    again = await w.mutate(staff, path, {"reason": "Again"})
    assert again.status_code == 409 and again.json() == {"detail": "invoice_void"}

    marked = await w.mutate(
        staff,
        f"{BASE}/invoices/{paid_id}/mark-paid",
        {"request_key": str(uuid.uuid4()), "method_kind": "cash"},
    )
    assert marked.status_code == 200, marked.text
    refused = await w.mutate(staff, f"{BASE}/invoices/{paid_id}/void", {"reason": "Oops"})
    assert refused.status_code == 409 and refused.json() == {"detail": "invoice_paid"}

    assert await audit_actions(w, w.tenants[0]) == [
        "payments.invoice.voided",
        "payments.invoice.paid",
    ]


async def test_detail_lists_reminders_without_any_address(sessions: SessionWorld) -> None:
    w = sessions
    admin, _ = await w.member(role="tenant_admin")
    contact = await seed_contact(w, w.tenants[0])
    invoice_id, _ = await seed_invoice(w, w.tenants[0], contact=contact, status="overdue")
    address = "aisha.synthetic@example.test"
    await seed_reminder(w, w.tenants[0], invoice_id, -7, "skipped", ago=timedelta(days=3))
    await seed_reminder(
        w,
        w.tenants[0],
        invoice_id,
        0,
        "sent",
        ago=timedelta(days=2),
        channel="email",
        address=address,
    )
    await seed_reminder(w, w.tenants[0], invoice_id, 3, "no_channel", ago=timedelta(days=1))

    response = await admin.get(f"{BASE}/invoices/{invoice_id}")
    assert response.status_code == 200, response.text
    detail = response.json()
    assert [(r["step"], r["channel"], r["status"]) for r in detail["reminders"]] == [
        (-7, "none", "skipped"),
        (0, "email", "sent"),
        (3, "none", "no_channel"),
    ]
    assert set(detail["reminders"][0]) == {"step", "channel", "status", "created_at", "sent_at"}
    assert address not in response.text and '"address"' not in response.text
    assert detail["payment"] is None and detail["amount"] == "59.90"


async def test_attention_lists_each_invoice_once_per_kind(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    tenant = w.tenants[0]
    now = datetime.now(UTC)

    older_claim, _ = await seed_invoice(
        w, tenant, status="proof_submitted", claimed_at=now - timedelta(days=2)
    )
    newer_claim, _ = await seed_invoice(
        w, tenant, status="proof_submitted", claimed_at=now - timedelta(hours=1)
    )
    # Four no_channel rows, the newest of them last: the invoice appears once.
    nudge, _ = await seed_invoice(w, tenant, status="overdue")
    for step, days in ((-7, 10), (0, 3), (3, 2), (7, 1)):
        await seed_reminder(w, tenant, nudge, step, "no_channel", ago=timedelta(days=days))
    # A no_channel row followed by a sent one no longer needs a nudge.
    reached, _ = await seed_invoice(w, tenant, status="issued")
    await seed_reminder(w, tenant, reached, -7, "no_channel", ago=timedelta(days=2))
    await seed_reminder(w, tenant, reached, 0, "sent", ago=timedelta(days=1), channel="telegram")
    # Two recent failures: once, since the newest.
    failing, _ = await seed_invoice(w, tenant, status="issued")
    await seed_reminder(w, tenant, failing, -7, "failed", ago=timedelta(days=5), channel="telegram")
    await seed_reminder(w, tenant, failing, 0, "failed", ago=timedelta(days=4), channel="telegram")
    old_failure, _ = await seed_invoice(w, tenant, status="issued")
    await seed_reminder(
        w, tenant, old_failure, -7, "failed", ago=timedelta(days=20), channel="telegram"
    )
    # Paid and void invoices never appear, whatever their reminders say.
    for status in ("paid", "void"):
        closed, _ = await seed_invoice(w, tenant, status=status)
        await seed_reminder(
            w,
            tenant,
            closed,
            -7,
            "failed",
            ago=timedelta(days=1),
            channel="email",
            address="closed@example.test",
        )
        await seed_reminder(w, tenant, closed, 0, "no_channel", ago=timedelta(hours=2))

    response = await staff.get(f"{BASE}/attention")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [(item["kind"], item["invoice"]["id"]) for item in items] == [
        ("proof_submitted", older_claim),
        ("proof_submitted", newer_claim),
        ("needs_nudge", nudge),
        ("reminder_failed", failing),
    ]
    since = {item["invoice"]["id"]: when(item["since"]) for item in items}
    assert abs((since[older_claim] - (now - timedelta(days=2))).total_seconds()) < 1
    assert abs((since[nudge] - (now - timedelta(days=1))).total_seconds()) < 5
    assert abs((since[failing] - (now - timedelta(days=4))).total_seconds()) < 5
    assert items[0]["invoice"]["claim_note"] == "Sent from Synthetic Bank"
    assert "closed@example.test" not in response.text
