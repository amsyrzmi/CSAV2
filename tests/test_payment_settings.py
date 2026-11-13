"""Payment settings and payment methods: defaults, optimistic versions, the enable rule, the
last-active-method rule, per-kind details, company isolation and audit rows (service level).
"""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

import pytest
from csa.console.contracts import ConsoleActor, MembershipOut, Role
from csa.core.tenancy import tx
from csa.payments import settings as payments
from csa.payments.contracts import (
    MethodIn,
    MethodOut,
    MethodPatch,
    PaymentSettingsIn,
)
from fastapi import HTTPException
from pydantic import ValidationError

from tests.conftest import requires_docker
from tests.test_console_sessions import SessionWorld

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

TRANSFER = {
    "bank_name": "Bank Contoh",
    "account_name": "Kedai Mawar",
    "account_number": "5120000000",
}
QR = {"duitnow_id": "kedai-mawar-01", "account_name": "Kedai Mawar"}
SAVE = {
    "enabled": False,
    "lead_days": 7,
    "reminder_days": [-7, 0, 3, 7],
    "suspend_after_days": None,
}


def actor_of(me: dict[str, Any]) -> ConsoleActor:
    user = me["user"]
    return ConsoleActor(
        user_id=UUID(user["id"]),
        email=user["email"],
        display_name=user["display_name"],
        platform_admin=user["is_platform_admin"],
        must_change_password=user["must_change_password"],
        session_id=uuid.uuid4(),
        client_id=UUID(me["active_client_id"]),
        role=Role(me["role"]),
        memberships=[MembershipOut.model_validate(m) for m in me["memberships"]],
        csrf_hash="synthetic",
    )


class Company:
    """One company of the test world with its own admin; every call runs in its own tenant tx."""

    def __init__(self, w: SessionWorld, tenant: str, actor: ConsoleActor) -> None:
        self.w, self.tenant, self.actor = w, UUID(tenant), actor

    async def settings(self) -> Any:
        async with tx(self.w.pool, str(self.tenant)) as conn:
            return await payments.get_settings(conn, self.tenant)

    async def save(self, version: int, **fields: Any) -> Any:
        data = PaymentSettingsIn.model_validate({**SAVE, **fields, "version": version})
        async with tx(self.w.pool, str(self.tenant)) as conn:
            return await payments.put_settings(conn, self.tenant, self.actor, data)

    async def methods(self, *, active_only: bool = False) -> list[MethodOut]:
        async with tx(self.w.pool, str(self.tenant)) as conn:
            return await payments.list_methods(conn, self.tenant, active_only=active_only)

    async def add(self, kind: str = "transfer", label: str = "Bank", **extra: Any) -> MethodOut:
        details = extra.pop("details", {"transfer": TRANSFER, "qr": QR, "cash": {}}[kind])
        data = MethodIn.model_validate({"kind": kind, "label": label, "details": details, **extra})
        async with tx(self.w.pool, str(self.tenant)) as conn:
            return await payments.create_method(conn, self.tenant, self.actor, data)

    async def change(self, method_id: UUID, version: int, **fields: Any) -> MethodOut:
        data = MethodPatch.model_validate({**fields, "version": version})
        async with tx(self.w.pool, str(self.tenant)) as conn:
            return await payments.update_method(conn, self.tenant, self.actor, method_id, data)


@pytest.fixture
async def a(sessions: SessionWorld) -> Company:
    _, me = await sessions.member(role="tenant_admin")
    return Company(sessions, sessions.tenants[0], actor_of(me))


@pytest.fixture
async def b(sessions: SessionWorld) -> Company:
    _, me = await sessions.member(role="tenant_admin", tenant=sessions.tenants[1])
    return Company(sessions, sessions.tenants[1], actor_of(me))


async def refused(call: Any, status: int, detail: str) -> None:
    with pytest.raises(HTTPException) as caught:
        await call
    assert (caught.value.status_code, caught.value.detail) == (status, detail)


async def test_a_company_with_no_row_reads_the_defaults(a: Company) -> None:
    shown = await a.settings()
    assert shown.model_dump() == {
        "enabled": False,
        "lead_days": 7,
        "reminder_days": [-7, 0, 3, 7],
        "suspend_after_days": None,
        "version": 0,
    }
    assert payments.DEFAULT_SETTINGS.version == 0
    assert await a.w.q("SELECT 1 FROM payment_settings WHERE client_id = %s", a.tenant) == []


async def test_settings_save_and_read_back_with_sorted_reminder_days(a: Company) -> None:
    saved = await a.save(0, lead_days=10, reminder_days=[7, 0, -7, 3], suspend_after_days=30)
    assert saved.version == 1
    assert saved.reminder_days == [-7, 0, 3, 7]
    assert await a.settings() == saved
    again = await a.save(1, lead_days=14, reminder_days=[], suspend_after_days=None)
    assert (again.version, again.lead_days, again.reminder_days) == (2, 14, [])
    assert again.suspend_after_days is None


async def test_a_stale_settings_save_is_a_version_conflict(a: Company) -> None:
    await a.save(0, lead_days=10)
    await refused(a.save(0, lead_days=11), 409, "version_conflict")
    await refused(a.save(5, lead_days=11), 409, "version_conflict")
    assert (await a.settings()).lead_days == 10


@pytest.mark.parametrize(
    "fields",
    [
        {"lead_days": 0},
        {"lead_days": 31},
        {"reminder_days": [0, 0]},
        {"reminder_days": [-31]},
        {"reminder_days": [61]},
        {"reminder_days": [-3, -2, -1, 0, 1, 2, 3]},
        {"suspend_after_days": 0},
        {"suspend_after_days": 91},
        {"surprise": True},
    ],
)
async def test_settings_input_is_bounded(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PaymentSettingsIn.model_validate({**SAVE, **fields, "version": 0})


async def test_enabling_needs_an_active_qr_or_transfer_method(a: Company) -> None:
    await refused(a.save(0, enabled=True), 409, "no_active_method")
    cash = await a.add("cash", "At the counter")
    await refused(a.save(0, enabled=True), 409, "no_active_method")
    parked = await a.add("transfer", "Old account", is_active=False)
    await refused(a.save(0, enabled=True), 409, "no_active_method")
    assert (await a.settings()).version == 0  # nothing was written by a refused save
    await a.change(parked.id, parked.version, is_active=True)
    on = await a.save(0, enabled=True)
    assert on.enabled and on.version == 1
    assert cash.kind == "cash"
    # Switching off never needs a method.
    assert not (await a.save(1, enabled=False)).enabled


async def test_a_qr_method_alone_is_enough_to_enable(a: Company) -> None:
    await a.add("qr", "DuitNow")
    assert (await a.save(0, enabled=True)).enabled


async def test_method_details_are_validated_per_kind() -> None:
    bad_number = {**TRANSFER, "account_number": "12ab"}
    for details in (
        bad_number,
        {**TRANSFER, "account_number": "12345"},  # too short
        {**TRANSFER, "account_number": "1" * 21},
        {**TRANSFER, "bank_name": ""},
        {**TRANSFER, "extra": "x"},
        QR,  # the wrong kind's fields
        {},
    ):
        with pytest.raises(ValidationError):
            MethodIn.model_validate({"kind": "transfer", "label": "Bank", "details": details})
    for details in ({**QR, "duitnow_id": "a b"}, {**QR, "duitnow_id": "abc"}, TRANSFER, {}):
        with pytest.raises(ValidationError):
            MethodIn.model_validate({"kind": "qr", "label": "DuitNow", "details": details})
    with pytest.raises(ValidationError):
        MethodIn.model_validate({"kind": "cash", "label": "Counter", "details": {"x": 1}})
    with pytest.raises(ValidationError):
        MethodIn.model_validate({"kind": "cheque", "label": "x", "details": {}})
    with pytest.raises(ValidationError):
        MethodIn.model_validate({"kind": "cash", "label": "x" * 61, "details": {}})
    with pytest.raises(ValidationError):
        MethodIn.model_validate(
            {"kind": "cash", "label": "x", "details": {}, "instructions": "y" * 501}
        )
    ok = MethodIn.model_validate({"kind": "transfer", "label": " Bank ", "details": TRANSFER})
    assert ok.label == "Bank" and ok.is_active and ok.sort_order == 0 and ok.instructions == ""


async def test_a_patch_validates_details_against_the_stored_kind(a: Company) -> None:
    method = await a.add("transfer", "Bank")
    await refused(
        a.change(method.id, 1, details={**TRANSFER, "account_number": "12ab"}),
        422,
        "invalid_details",
    )
    await refused(a.change(method.id, 1, details=QR), 422, "invalid_details")
    changed = await a.change(method.id, 1, details={**TRANSFER, "account_number": "5120000001"})
    assert changed.details["account_number"] == "5120000001"
    assert changed.version == 2
    assert changed.label == "Bank"  # a field left out is unchanged


async def test_methods_list_by_sort_order_then_label_and_can_be_filtered(a: Company) -> None:
    last = await a.add("transfer", "Zebra bank", sort_order=5)
    await a.add("qr", "DuitNow", sort_order=1)
    await a.add("transfer", "Alpha bank", sort_order=5)
    off = await a.add("cash", "Counter", sort_order=0, is_active=False)
    assert [m.label for m in await a.methods()] == [
        "Counter",
        "DuitNow",
        "Alpha bank",
        "Zebra bank",
    ]
    assert [m.label for m in await a.methods(active_only=True)] == [
        "DuitNow",
        "Alpha bank",
        "Zebra bank",
    ]
    assert off.is_active is False and last.version == 1


async def test_a_stale_method_change_is_a_version_conflict(a: Company) -> None:
    method = await a.add()
    await a.change(method.id, 1, label="Main account")
    await refused(a.change(method.id, 1, label="Other"), 409, "version_conflict")
    assert [m.label for m in await a.methods()] == ["Main account"]


async def test_the_last_active_method_of_an_enabled_company_stays_on(a: Company) -> None:
    transfer = await a.add("transfer", "Bank")
    cash = await a.add("cash", "Counter")
    await a.save(0, enabled=True)
    # A cash method does not count, so the only transfer cannot be deactivated.
    await refused(a.change(transfer.id, 1, is_active=False), 409, "last_active_method")
    assert (await a.methods(active_only=True))[0].id == transfer.id
    # With a second QR or transfer method it can; then the second is the last one.
    qr = await a.add("qr", "DuitNow")
    off = await a.change(transfer.id, 1, is_active=False)
    assert off.is_active is False and off.version == 2
    await refused(a.change(qr.id, 1, is_active=False), 409, "last_active_method")
    # Deactivating cash is always allowed, and a refusal wrote nothing.
    assert (await a.change(cash.id, 1, is_active=False)).is_active is False
    # Switch payments off and the last method may go.
    await a.save(1, enabled=False)
    assert (await a.change(qr.id, 1, is_active=False)).is_active is False


async def test_an_enabled_company_may_edit_its_last_method_without_deactivating(a: Company) -> None:
    transfer = await a.add("transfer", "Bank")
    await a.save(0, enabled=True)
    edited = await a.change(transfer.id, 1, label="Main bank", instructions="Use the reference.")
    assert (edited.label, edited.instructions, edited.is_active) == (
        "Main bank",
        "Use the reference.",
        True,
    )


async def test_a_missing_method_is_404(a: Company) -> None:
    await refused(a.change(uuid.uuid4(), 1, label="x"), 404, "method_not_found")


async def test_company_b_cannot_read_or_change_company_as_methods(a: Company, b: Company) -> None:
    method = await a.add("transfer", "Bank")
    assert await b.methods() == []
    assert [m.id for m in await a.methods()] == [method.id]
    await refused(b.change(method.id, 1, label="Hijacked"), 404, "method_not_found")
    assert [m.label for m in await a.methods()] == ["Bank"]
    # B's settings are its own: A's save does not show in B.
    await a.save(0, lead_days=20, enabled=True)
    assert (await b.settings()).version == 0
    await refused(b.save(0, enabled=True), 409, "no_active_method")


async def test_every_write_leaves_an_audit_row_with_the_actor(a: Company) -> None:
    method = await a.add("transfer", "Bank")
    await a.change(method.id, 1, label="Main")
    await a.save(0, enabled=True)
    await refused(a.change(method.id, 2, is_active=False), 409, "last_active_method")
    rows = await a.w.q(
        "SELECT action, actor_id, target_id FROM console_audit"
        " WHERE client_id = %s AND action LIKE 'payments.%%' ORDER BY action",
        a.tenant,
    )
    assert [(r["action"], r["actor_id"], r["target_id"]) for r in rows] == [
        ("payments.method.created", a.actor.user_id, str(method.id)),
        ("payments.method.updated", a.actor.user_id, str(method.id)),
        ("payments.settings.updated", a.actor.user_id, str(a.tenant)),
    ]
