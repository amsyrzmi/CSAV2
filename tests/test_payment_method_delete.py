"""Deleting a payment method: admins only, the last-active rule, its QR picture, money untouched."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from csa.payments.api import build_payments_router

from tests.conftest import requires_docker
from tests.media_images import png
from tests.test_console_payments import seed_contact, seed_invoice
from tests.test_console_services import activate
from tests.test_console_sessions import SessionWorld

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

BASE = "/console/v1/payments"
QR = {"duitnow_id": "SYNTH-0001", "account_name": "Kedai Contoh Sdn Bhd"}
TRANSFER = {
    "bank_name": "Synthetic Bank",
    "account_name": "Kedai Contoh Sdn Bhd",
    "account_number": "512000000001",
}
DETAILS = {"qr": QR, "transfer": TRANSFER, "cash": {}}


@pytest.fixture(autouse=True)
async def packages(sessions: SessionWorld) -> None:
    sessions.app.include_router(build_payments_router())
    for tenant in sessions.tenants:
        await activate(sessions, tenant, "subscriptions")


async def add(
    w: SessionWorld,
    admin: httpx.AsyncClient,
    kind: str = "transfer",
    label: str = "Bank",
    *,
    active: bool = True,
) -> dict[str, Any]:
    made = await w.mutate(
        admin,
        f"{BASE}/methods",
        {"kind": kind, "label": label, "details": DETAILS[kind], "is_active": active},
    )
    assert made.status_code == 201, made.text
    return made.json()


async def delete(w: SessionWorld, client: httpx.AsyncClient, method_id: str) -> httpx.Response:
    return await w.mutate(client, f"{BASE}/methods/{method_id}", method="DELETE")


async def switch_on(w: SessionWorld, admin: httpx.AsyncClient) -> None:
    saved = await w.mutate(
        admin,
        f"{BASE}/settings",
        {
            "enabled": True,
            "lead_days": 7,
            "reminder_days": [0],
            "suspend_after_days": None,
            "version": 0,
        },
        method="PUT",
    )
    assert saved.status_code == 200, saved.text


async def upload_qr(w: SessionWorld, admin: httpx.AsyncClient, method_id: str) -> None:
    stored = await admin.put(
        f"{BASE}/methods/{method_id}/picture",
        files={"file": ("qr.png", png(800, 800), "image/png")},
        headers=w.headers(admin),
    )
    assert stored.status_code == 200, stored.text


async def pictures(w: SessionWorld, method_id: str) -> list[dict[str, Any]]:
    return await w.q(
        "SELECT id FROM media_assets WHERE kind = 'payment_qr' AND subject_key = %s", method_id
    )


async def audit_rows(w: SessionWorld, tenant: str, action: str) -> list[dict[str, Any]]:
    return await w.q(
        "SELECT action, actor_id, target_id FROM console_audit"
        " WHERE client_id = %s AND action = %s ORDER BY created_at",
        tenant,
        action,
    )


async def test_admin_deletes_a_method(sessions: SessionWorld) -> None:
    w = sessions
    admin, me = await w.member(role="tenant_admin")
    kept = await add(w, admin, "cash", "Counter")
    method = await add(w, admin, "transfer", "Bank")

    gone = await delete(w, admin, method["id"])
    assert gone.status_code == 204, gone.text
    assert gone.content == b""
    listed = await admin.get(f"{BASE}/methods")
    assert listed.status_code == 200
    assert [m["id"] for m in listed.json()] == [kept["id"]]
    assert await w.q("SELECT id FROM payment_methods WHERE id = %s", method["id"]) == []
    assert await audit_rows(w, w.tenants[0], "payments.method.deleted") == [
        {
            "action": "payments.method.deleted",
            "actor_id": uuid.UUID(me["user"]["id"]),
            "target_id": method["id"],
        }
    ]

    # A platform admin names the company and may delete too.
    other = await add(w, admin, "qr", "DuitNow")
    platform = await delete(w, w.admin, f"{other['id']}?client_id={w.tenants[0]}")
    assert platform.status_code == 204, platform.text


async def test_delete_unknown_or_other_company_method_is_404(sessions: SessionWorld) -> None:
    w = sessions
    admin_a, _ = await w.member(role="tenant_admin")
    admin_b, _ = await w.member(role="tenant_admin", tenant=w.tenants[1])
    method = await add(w, admin_a)

    unknown = await delete(w, admin_a, str(uuid.uuid4()))
    assert unknown.status_code == 404 and unknown.json() == {"detail": "method_not_found"}
    crossing = await delete(w, admin_b, method["id"])
    assert crossing.status_code == 404 and crossing.json() == {"detail": "method_not_found"}
    assert len(await w.q("SELECT id FROM payment_methods WHERE id = %s", method["id"])) == 1

    first = await delete(w, admin_a, method["id"])
    assert first.status_code == 204, first.text
    second = await delete(w, admin_a, method["id"])
    assert second.status_code == 404 and second.json() == {"detail": "method_not_found"}
    assert len(await audit_rows(w, w.tenants[0], "payments.method.deleted")) == 1
    assert await audit_rows(w, w.tenants[1], "payments.method.deleted") == []


async def test_delete_last_active_payable_method_with_payments_on_is_409(
    sessions: SessionWorld,
) -> None:
    w = sessions
    admin, _ = await w.member(role="tenant_admin")
    await add(w, admin, "cash", "Counter")  # cash does not count as a way to pay
    await add(w, admin, "transfer", "Old account", active=False)  # nor does an inactive one
    qr = await add(w, admin, "qr", "DuitNow")
    await upload_qr(w, admin, qr["id"])
    await switch_on(w, admin)

    refused = await delete(w, admin, qr["id"])
    assert refused.status_code == 409 and refused.json() == {"detail": "last_active_method"}
    assert len(await w.q("SELECT id FROM payment_methods WHERE id = %s", qr["id"])) == 1
    assert len(await pictures(w, qr["id"])) == 1
    assert await audit_rows(w, w.tenants[0], "payments.method.deleted") == []
    assert await audit_rows(w, w.tenants[0], "media.picture.removed") == []

    # With a second active transfer method the QR may go; then the transfer is the last one.
    bank = await add(w, admin, "transfer", "Bank")
    assert (await delete(w, admin, qr["id"])).status_code == 204
    last = await delete(w, admin, bank["id"])
    assert last.status_code == 409 and last.json() == {"detail": "last_active_method"}


async def test_delete_inactive_or_cash_method_or_payments_off_succeeds(
    sessions: SessionWorld,
) -> None:
    w = sessions
    admin, _ = await w.member(role="tenant_admin")
    bank = await add(w, admin, "transfer", "Bank")
    parked = await add(w, admin, "qr", "Old DuitNow", active=False)
    cash = await add(w, admin, "cash", "Counter")
    await switch_on(w, admin)

    # Payments on, `bank` the last active payable method: an inactive one and cash still go.
    assert (await delete(w, admin, parked["id"])).status_code == 204
    assert (await delete(w, admin, cash["id"])).status_code == 204
    assert (await delete(w, admin, bank["id"])).status_code == 409

    # Payments off: the last method may go too.
    off = await w.mutate(
        admin,
        f"{BASE}/settings",
        {
            "enabled": False,
            "lead_days": 7,
            "reminder_days": [0],
            "suspend_after_days": None,
            "version": 1,
        },
        method="PUT",
    )
    assert off.status_code == 200, off.text
    assert (await delete(w, admin, bank["id"])).status_code == 204
    assert (await admin.get(f"{BASE}/methods")).json() == []
    assert len(await audit_rows(w, w.tenants[0], "payments.method.deleted")) == 3


async def test_delete_removes_the_qr_picture_in_the_same_transaction(
    sessions: SessionWorld,
) -> None:
    w = sessions
    admin, me = await w.member(role="tenant_admin")
    qr = await add(w, admin, "qr", "DuitNow")
    await upload_qr(w, admin, qr["id"])
    [asset] = await pictures(w, qr["id"])

    assert (await delete(w, admin, qr["id"])).status_code == 204
    assert await pictures(w, qr["id"]) == []
    assert await w.q("SELECT asset_id FROM media_asset_data WHERE asset_id = %s", asset["id"]) == []
    assert await audit_rows(w, w.tenants[0], "media.picture.removed") == [
        {
            "action": "media.picture.removed",
            "actor_id": uuid.UUID(me["user"]["id"]),
            "target_id": f"payment_qr:{qr['id']}",
        }
    ]
    assert len(await audit_rows(w, w.tenants[0], "payments.method.deleted")) == 1


async def test_delete_without_picture_writes_no_picture_audit(sessions: SessionWorld) -> None:
    w = sessions
    admin, _ = await w.member(role="tenant_admin")
    never = await add(w, admin, "qr", "DuitNow")
    removed_first = await add(w, admin, "qr", "Second DuitNow")
    await upload_qr(w, admin, removed_first["id"])
    dropped = await w.mutate(
        admin, f"{BASE}/methods/{removed_first['id']}/picture", method="DELETE"
    )
    assert dropped.status_code == 204, dropped.text

    assert (await delete(w, admin, never["id"])).status_code == 204
    assert (await delete(w, admin, removed_first["id"])).status_code == 204
    # Only the picture route's own removal is audited, not a second one from the delete.
    assert await audit_rows(w, w.tenants[0], "media.picture.removed") == [
        {
            "action": "media.picture.removed",
            "actor_id": (await audit_rows(w, w.tenants[0], "media.picture.set"))[0]["actor_id"],
            "target_id": f"payment_qr:{removed_first['id']}",
        }
    ]
    assert len(await audit_rows(w, w.tenants[0], "payments.method.deleted")) == 2


async def test_delete_does_not_touch_invoices_or_payments(sessions: SessionWorld) -> None:
    w = sessions
    admin, _ = await w.member(role="tenant_admin")
    bank = await add(w, admin, "transfer", "Bank")
    contact = await seed_contact(w, w.tenants[0])
    paid_id, _ = await seed_invoice(w, w.tenants[0], contact=contact)
    open_id, _ = await seed_invoice(w, w.tenants[0], contact=contact)
    paid = await w.mutate(
        admin,
        f"{BASE}/invoices/{paid_id}/mark-paid",
        {"request_key": str(uuid.uuid4()), "method_kind": "transfer"},
    )
    assert paid.status_code == 200, paid.text
    invoices_before = await w.q(
        "SELECT * FROM invoices WHERE client_id = %s ORDER BY id", w.tenants[0]
    )
    payments_before = await w.q(
        "SELECT * FROM payments WHERE client_id = %s ORDER BY id", w.tenants[0]
    )
    assert [p["method_kind"] for p in payments_before] == ["transfer"]
    assert {i["id"] for i in invoices_before} == {paid_id, open_id}

    assert (await delete(w, admin, bank["id"])).status_code == 204
    assert (
        await w.q("SELECT * FROM invoices WHERE client_id = %s ORDER BY id", w.tenants[0])
        == invoices_before
    )
    assert (
        await w.q("SELECT * FROM payments WHERE client_id = %s ORDER BY id", w.tenants[0])
        == payments_before
    )
    detail = await admin.get(f"{BASE}/invoices/{paid_id}")
    assert detail.status_code == 200 and detail.json()["method_kind"] == "transfer"


async def test_read_only_and_staff_get_403(sessions: SessionWorld) -> None:
    w = sessions
    admin, _ = await w.member(role="tenant_admin")
    qr = await add(w, admin, "qr", "DuitNow")
    await upload_qr(w, admin, qr["id"])
    other_admin, _ = await w.member(role="tenant_admin", tenant=w.tenants[1])
    for role in ("tenant_staff", "read_only"):
        member, _ = await w.member(role=role)
        refused = await delete(w, member, qr["id"])
        assert refused.status_code == 403, (role, refused.text)
        # Refused before anything is read: an unknown id is 403 as well, never 404.
        unknown = await delete(w, member, str(uuid.uuid4()))
        assert unknown.status_code == 403, (role, unknown.text)
    # Another company's admin cannot name company A.
    crossing = await delete(w, other_admin, f"{qr['id']}?client_id={w.tenants[0]}")
    assert crossing.status_code == 403, crossing.text
    assert len(await w.q("SELECT id FROM payment_methods WHERE id = %s", qr["id"])) == 1
    assert len(await pictures(w, qr["id"])) == 1
    assert await audit_rows(w, w.tenants[0], "payments.method.deleted") == []
